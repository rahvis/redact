"""
Edit handlers for WorkOnward Read: undo, redo and delete-all.

Undo/redo use the per-page snapshot stacks in ``state.undo``
(:class:`workonward_read.annotations.UndoStack`); canvas tools push a snapshot
before every annotation-adding/removing action. The toolbar UNDO icon maps
to the same handler (see handlers/__init__.py).

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
(c) 2026 WorkOnward Read contributors
"""

import FreeSimpleGUI as sg

from workonward_read.annotations import UndoStack
from workonward_read.image_container import delete_all_annotations
from workonward_read.handlers.view import flip_to_page
from workonward_read.workfile import serialize_journal
from workonward_read.i18n import _


def _apply_snapshot(window, container, snapshot):
    """Replace the container's annotations with a snapshot and redraw."""
    for ann in container.annotations:
        for figure_id in (ann.graph_ids or []):
            try:
                window['-GRAPH-'].delete_figure(figure_id)
            except Exception:
                pass
    container.annotations = snapshot
    try:
        container.draw_annotations_on_graph(window)
    except Exception:
        pass


def undo(window, state):
    """Restore the previous annotation snapshot of the current page."""
    if not state.images:
        return
    stack = state.undo.get(state.current_page)
    if stack is None:
        return
    container = state.images[state.current_page]
    snapshot = stack.undo(container.annotations)
    if snapshot is None:
        return
    _apply_snapshot(window, container, snapshot)


def redo(window, state):
    """Re-apply the most recently undone snapshot of the current page."""
    if not state.images:
        return
    stack = state.undo.get(state.current_page)
    if stack is None:
        return
    container = state.images[state.current_page]
    snapshot = stack.redo(container.annotations)
    if snapshot is None:
        return
    _apply_snapshot(window, container, snapshot)


def delete_all(window, state):
    """Ask for confirmation, then delete every annotation on every page.

    A snapshot is pushed onto each affected page's UndoStack first, so the
    deletion is undoable per page."""
    if not state.images:
        return

    win_loc_x, win_loc_y = window.current_location()
    win_w, win_h = window.current_size_accurate()

    result = sg.popup_ok_cancel(
        _('confirm_delete_all'),
        no_titlebar=True,
        location=(win_loc_x + win_w/2 - 185, win_loc_y + win_h/2 - 200),
        keep_on_top=True,
        background_color='silver',
        button_color='grey'
    )

    if result == 'OK':
        try:
            for page_idx, page in enumerate(state.images):
                if getattr(page, 'annotations', None):
                    state.undo.setdefault(page_idx, UndoStack()).push(page.annotations)
            delete_workfile = (state.workfile_manager.delete
                               if state.workfile_manager is not None else None)
            delete_all_annotations(state.images, delete_workfile)
            state.current_page = flip_to_page(window, state.images, state.current_page, state)
            if state.workfile_manager is not None:
                state.workfile_manager.save(
                    state.images, state.current_page,
                    state.fill_color, state.output_quality,
                    decorations=state.decorations,
                    journal=serialize_journal(state.journal)
                )
        except Exception:
            pass


HANDLERS = {
    'MENU_UNDO': undo,
    'MENU_REDO': redo,
    'MENU_DELETE_ALL': delete_all,
}
