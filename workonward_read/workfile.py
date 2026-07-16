"""
Workfile management for session persistence in WorkOnward Read.

This module provides the WorkfileManager class that handles saving and loading
of work sessions. Work sessions are stored as JSON files in the user's data
directory, keyed by an MD5 hash of the original file path.

Workfile format v2 (see docs/dev-architecture.md):

    {"version": 2,
     "annotations": [[{"id": ..., "kind": ..., "props": {...}}, ...] per page],
     "decorations": {...}, "journal": [...],
     "pages": N, "current_page": n,
     "fill_color": "black", "output_quality": "high"}

Annotation dicts carry the full typed annotation model
(:mod:`workonward_read.annotations`). Legacy v1 files (a top-level
``"rectangles"`` key) and early v2 redact-only files are still loaded and
migrated transparently. ``load()`` returns annotation data as plain dicts
per page (see ``annotations.from_dict`` for rebuilding Annotation objects).
Passwords are never persisted.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import os
import json
import shutil

from appdirs import user_data_dir

from workonward_read import annotations as annotations_engine
from workonward_read.utils import encode_filepath, delete_oldest_files
from workonward_read.image_container import export_annotations


def get_default_datadir():
    """
    Return (and create) the WorkOnward Read user data directory.

    One-time migration: if the new data directory is empty and the old
    CoverUP data directory exists, the JSON workfiles stored there are
    copied over (best effort) so existing sessions survive the rename.
    Note: workfiles are JSON documents saved without a .json extension
    (their names are MD5 hashes of the document path).
    """
    datadir = user_data_dir('WorkOnwardRead', 'WorkOnward')
    try:
        if not os.path.exists(datadir):
            os.makedirs(datadir)
    except Exception:
        pass

    # Best-effort one-time migration of old CoverUP workfiles.
    try:
        if os.path.isdir(datadir) and not os.listdir(datadir):
            old_datadir = user_data_dir('CoverUP', 'digidigital')
            if os.path.isdir(old_datadir):
                for name in os.listdir(old_datadir):
                    src = os.path.join(old_datadir, name)
                    if os.path.isfile(src):
                        shutil.copy2(src, os.path.join(datadir, name))
    except Exception:
        pass

    return datadir


def serialize_journal(journal):
    """
    Return the persistable ops list for a page-ops journal.

    Accepts a :class:`workonward_read.pdf_ops.PageOpsJournal` (duck-typed via
    ``to_dict``), an already-serialized ops list, or None/empty (-> ``[]``).
    """
    if journal is None:
        return []
    to_dict = getattr(journal, 'to_dict', None)
    if callable(to_dict):
        try:
            return to_dict().get('ops', [])
        except Exception:
            return []
    if isinstance(journal, dict):
        return list(journal.get('ops', []))
    return list(journal)


def _normalize_annotation_dict(data):
    """Round-trip an annotation dict through the engine: fills in missing
    ids, deep-copies props and drops any stray keys. Returns None for
    entries that are not dicts."""
    if not isinstance(data, dict):
        return None
    return annotations_engine.to_dict(annotations_engine.from_dict(data))


def _migrate_v1_page(page_rectangles):
    """Convert one page of v1 rectangle entries ``[p1, p2, fill, graph_id]``
    into annotation dicts."""
    migrated = []
    for rectangle in page_rectangles:
        try:
            ann = annotations_engine.migrate_v1_rectangle(
                (tuple(rectangle[0]), tuple(rectangle[1]), rectangle[2], None))
            migrated.append(annotations_engine.to_dict(ann))
        except Exception:
            continue
    return migrated


class WorkfileManager:
    """
    Manages saving and loading of work sessions.

    Work sessions allow users to continue annotating a document where they
    left off. Session files are stored in the application's data directory
    and are automatically cleaned up when the history limit is exceeded.

    Attributes:
        datadir: Directory path for storing workfiles.
        history_length: Maximum number of workfiles to retain.
        file_path: Current document file path (used for workfile naming).
    """

    def __init__(self, datadir, history_length=30):
        """
        Initialize the WorkfileManager.

        Args:
            datadir: Directory path for storing workfiles.
            history_length: Maximum number of workfiles to retain (default: 30).
        """
        self.datadir = datadir
        self.history_length = history_length
        self.file_path = None

    def set_file_path(self, file_path):
        """
        Set the current file path for workfile operations.

        Args:
            file_path: Path to the currently loaded document.
        """
        self.file_path = file_path

    def save(self, images, current_page, fill_color, output_quality,
             decorations=None, journal=None):
        """
        Save the current work session as a v2 workfile.

        The workfile is deleted instead when there is nothing to persist
        (no annotations, no decorations, no journal ops).

        Args:
            images: List of ImageContainer objects with annotation data.
            current_page: Currently displayed page index.
            fill_color: Current fill color ('black' or 'white').
            output_quality: Current quality setting ('high' or 'low').
            decorations: Optional document-level decorations dict.
            journal: Optional serialized page-ops journal (list of ops).
        """
        if not self.file_path or not self.datadir:
            return

        if not images:
            self.delete()
            return

        decorations = decorations if decorations else {}
        journal = list(journal) if journal else []

        annotations = export_annotations(images)
        if annotations is None and not decorations and not journal:
            self.delete()
            return
        if annotations is None:
            annotations = [[] for _ in images]

        workfile_name = encode_filepath(self.file_path)
        work_data = {
            'version': 2,
            'annotations': annotations,
            'decorations': decorations,
            'journal': journal,
            'pages': len(images),
            'current_page': current_page,
            'fill_color': fill_color,
            'output_quality': output_quality
        }
        try:
            with open(os.path.join(self.datadir, workfile_name), 'w', encoding='utf-8') as f:
                json.dump(work_data, f, ensure_ascii=False, indent=4)
            delete_oldest_files(self.datadir, self.history_length)
        except Exception:
            pass

    def delete(self):
        """
        Delete the current workfile.

        Called when the user starts over or when there is nothing to save.
        """
        if not self.file_path:
            return

        try:
            workfile = os.path.join(self.datadir, encode_filepath(self.file_path))
            if os.path.isfile(workfile):
                os.remove(workfile)
        except Exception:
            pass

    def load(self):
        """
        Load work data from the workfile if it exists.

        Accepts v1 workfiles (top-level 'rectangles' -> migrated to redact
        annotations) and v2 workfiles (top-level 'annotations', including
        early redact-only v2 files). The returned dict always carries
        annotation data as plain annotation dicts, one list per page.

        Returns:
            dict: Work session data containing 'annotations', 'pages',
                  'current_page', 'fill_color', 'output_quality',
                  'decorations' and 'journal', or None if no workfile
                  exists or it cannot be parsed.
        """
        if not self.file_path:
            return None

        try:
            workfile_name = encode_filepath(self.file_path)
            workfile = os.path.join(self.datadir, workfile_name)
            if not os.path.isfile(workfile):
                return None
            with open(workfile, 'r', encoding='utf-8') as f:
                work_data = json.load(f)

            if work_data.get('version', 1) >= 2 or 'annotations' in work_data:
                annotations = []
                for page_annotations in work_data.get('annotations', []):
                    page = []
                    for entry in page_annotations:
                        normalized = _normalize_annotation_dict(entry)
                        if normalized is not None:
                            page.append(normalized)
                    annotations.append(page)
            else:
                # v1: rectangle tuples -> redact annotations.
                annotations = [
                    _migrate_v1_page(page_rectangles)
                    for page_rectangles in work_data.get('rectangles', [])
                ]

            return {
                'annotations': annotations,
                'pages': work_data.get('pages'),
                'current_page': work_data.get('current_page', 0),
                'fill_color': work_data.get('fill_color'),
                'output_quality': work_data.get('output_quality'),
                'decorations': work_data.get('decorations', {}) or {},
                'journal': work_data.get('journal', []) or [],
            }
        except Exception:
            return None
