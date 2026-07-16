"""
Application state for WorkOnward Read.

Central mutable state shared between the event loop, menu/toolbar handlers
and canvas tools. The dataclass fields up to ``thumbnails_visible`` follow
the binding contract in docs/dev-architecture.md; the fields below that
marker are runtime-only helpers (never persisted).

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import os
from dataclasses import dataclass, field


@dataclass
class AppState:
    """Mutable application state (contract: docs/dev-architecture.md)."""

    # --- contract fields -------------------------------------------------
    images: list = field(default_factory=list)        # list[ImageContainer]
    file_path: str | None = None                      # original document path
    source_password: str | None = None                # never persisted
    current_page: int = 0
    fill_color: str = 'black'
    output_quality: str = 'high'
    tool: str = 'redact'                              # current canvas tool key
    tool_props: dict = field(default_factory=dict)    # per-tool props (color, width, alpha…)
    decorations: dict = field(default_factory=dict)   # watermark/header_footer/page_numbers/bates
    journal: object | None = None                     # pdf_ops.PageOpsJournal
    undo: dict = field(default_factory=dict)          # page_idx -> annotations.UndoStack
    first_load: bool = True
    busy: bool = False
    thumbnails_visible: bool = False

    # --- runtime-only helpers (not part of the persisted contract) -------
    workfile_manager: object | None = None            # workfile.WorkfileManager
    icons: dict = field(default_factory=dict)         # toolbar icon bytes by name
    # Busy set of reasons: background tasks currently USING the loaded
    # document (state.images / state.journal). Document-mutating and
    # document-consuming entry points refuse to start while non-empty
    # (dialogs.common.require_document_free).
    doc_lock: set = field(default_factory=set)
    # Registered non-modal secondary windows:
    # sg.Window -> handler(window, state, event, values) -> bool keep_open.
    # main.py's read_all_windows loop routes their events (aux-window
    # contract in docs/dev-architecture.md).
    aux_windows: dict = field(default_factory=dict)

    # --- helpers ----------------------------------------------------------

    def password_for(self, path):
        """Password for ``path`` when it is the loaded (encrypted) document.

        Paths are compared via ``os.path.abspath`` + ``os.path.normcase`` so
        relative spellings and case-insensitive filesystems match the loaded
        ``file_path``. Returns None for any other file.
        """
        if not path or not self.file_path:
            return None
        canonical = os.path.normcase(os.path.abspath(str(path)))
        loaded = os.path.normcase(os.path.abspath(str(self.file_path)))
        return self.source_password if canonical == loaded else None

    def acquire_doc(self, reason):
        """Mark the loaded document as in use by a background task."""
        self.doc_lock.add(reason)

    def release_doc(self, reason):
        """Release a doc-lock reason (tolerates double release)."""
        self.doc_lock.discard(reason)
