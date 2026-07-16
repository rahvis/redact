"""
Edit handlers for CoverUP PDF: undo, redo (placeholder) and delete-all.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import FreeSimpleGUI as sg

from coverup.image_container import delete_all_rectangles
from coverup.handlers.view import flip_to_page
from coverup.i18n import _


def undo(window, state):
    """Remove the most recent rectangle on the current page."""
    if not state.images:
        return
    state.images[state.current_page].undo(window)


def redo(window, state):
    """Redo placeholder — becomes functional with the wave-2 UndoStack."""
    return None


def delete_all(window, state):
    """Ask for confirmation, then delete every rectangle on every page."""
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
            delete_workfile = (state.workfile_manager.delete
                               if state.workfile_manager is not None else None)
            delete_all_rectangles(state.images, delete_workfile)
            state.current_page = flip_to_page(window, state.images, state.current_page)
            if state.workfile_manager is not None:
                state.workfile_manager.save(
                    state.images, state.current_page,
                    state.fill_color, state.output_quality
                )
        except Exception:
            pass


HANDLERS = {
    'MENU_UNDO': undo,
    'MENU_REDO': redo,
    'MENU_DELETE_ALL': delete_all,
}
