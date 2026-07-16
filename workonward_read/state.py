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
    import_ppi: int = 200                             # render resolution for imports
    workfile_manager: object | None = None            # workfile.WorkfileManager
    icons: dict = field(default_factory=dict)         # toolbar icon bytes by name
    task_callbacks: dict = field(default_factory=dict)  # task key -> completion callable
