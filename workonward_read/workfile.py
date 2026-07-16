"""
Workfile management for session persistence in WorkOnward Read.

This module provides the WorkfileManager class that handles saving and loading
of work sessions. Work sessions are stored as JSON files in the user's data
directory, keyed by an MD5 hash of the original file path.

Workfile format v2 (see docs/dev-architecture.md):

    {"version": 2,
     "annotations": [[{"id": ..., "kind": "redact",
                       "props": {"p1": [x, y], "p2": [x, y], "fill": color}}, ...]
                     per page],
     "decorations": {...}, "journal": [...],
     "pages": N, "current_page": n,
     "fill_color": "black", "output_quality": "high"}

Legacy v1 files (a top-level ``"rectangles"`` key) are still loaded and
migrated transparently. ``load()`` always returns rectangle data in the
current in-app tuple form ``(p1, p2, fill, None)``. Passwords are never
persisted.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import os
import json
import shutil
import uuid

from appdirs import user_data_dir

from workonward_read.utils import encode_filepath, delete_oldest_files
from workonward_read.image_container import export_rectangles


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


def _rectangle_to_annotation(rectangle):
    """
    Convert a current-form rectangle tuple ``(p1, p2, fill, graph_id)`` into
    a v2 annotation dict. Constructed inline on purpose — no dependency on
    workonward_read.annotations, which may not exist yet.
    """
    return {
        'id': uuid.uuid4().hex,
        'kind': 'redact',
        'props': {
            'p1': [int(rectangle[0][0]), int(rectangle[0][1])],
            'p2': [int(rectangle[1][0]), int(rectangle[1][1])],
            'fill': rectangle[2],
        },
    }


def _annotation_to_rectangle(annotation):
    """
    Convert a v2 annotation dict back into the current in-app tuple form
    ``[p1, p2, fill, None]``. Returns None for kinds the redaction view
    does not render (future annotation kinds).
    """
    if annotation.get('kind') != 'redact':
        return None
    props = annotation.get('props', {})
    p1 = props.get('p1')
    p2 = props.get('p2')
    if p1 is None or p2 is None:
        return None
    return [tuple(p1), tuple(p2), props.get('fill', 'black'), None]


class WorkfileManager:
    """
    Manages saving and loading of work sessions.

    Work sessions allow users to continue redacting a document where they
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

        Args:
            images: List of ImageContainer objects with rectangle data.
            current_page: Currently displayed page index.
            fill_color: Current fill color ('black' or 'white').
            output_quality: Current quality setting ('high' or 'low').
            decorations: Optional document-level decorations dict.
            journal: Optional serialized page-ops journal (list).
        """
        if not self.file_path or not self.datadir:
            return

        if not images:
            self.delete()
            return

        rectangles = export_rectangles(images)
        if rectangles is not None:
            workfile_name = encode_filepath(self.file_path)
            annotations = [
                [_rectangle_to_annotation(rectangle) for rectangle in page_rectangles]
                for page_rectangles in rectangles
            ]
            work_data = {
                'version': 2,
                'annotations': annotations,
                'decorations': decorations if decorations is not None else {},
                'journal': journal if journal is not None else [],
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
        else:
            self.delete()

    def delete(self):
        """
        Delete the current workfile.

        Called when the user starts over or when there are no rectangles to save.
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

        Accepts both v1 workfiles (top-level 'rectangles') and v2 workfiles
        (top-level 'annotations'). In both cases the returned dict carries
        rectangle data in the current in-app tuple form (p1, p2, fill, None).

        Returns:
            dict: Work session data containing 'rectangles', 'pages',
                  'current_page', 'fill_color' and 'output_quality' (plus
                  'decorations' and 'journal' for v2 files), or None if no
                  workfile exists or it cannot be parsed.
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
                rectangles = []
                for page_annotations in work_data.get('annotations', []):
                    page_rectangles = []
                    for annotation in page_annotations:
                        rectangle = _annotation_to_rectangle(annotation)
                        if rectangle is not None:
                            page_rectangles.append(rectangle)
                    rectangles.append(page_rectangles)
            else:
                # v1: rectangles stored directly; drop stale graph ids.
                rectangles = [
                    [[tuple(rectangle[0]), tuple(rectangle[1]), rectangle[2], None]
                     for rectangle in page_rectangles]
                    for page_rectangles in work_data.get('rectangles', [])
                ]

            result = {
                'rectangles': rectangles,
                'pages': work_data.get('pages'),
                'current_page': work_data.get('current_page', 0),
                'fill_color': work_data.get('fill_color'),
                'output_quality': work_data.get('output_quality'),
            }
            if work_data.get('version', 1) >= 2:
                result['decorations'] = work_data.get('decorations', {})
                result['journal'] = work_data.get('journal', [])
            return result
        except Exception:
            return None
