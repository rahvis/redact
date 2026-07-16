"""
Help handlers for CoverUP PDF: about dialog.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import FreeSimpleGUI as sg

from coverup import __version__
from coverup.i18n import _


def about(window, state):
    """Show the About popup centered over the main window."""
    about_text = _('about_text', version=__version__)
    win_loc_x, win_loc_y = window.current_location()
    win_w, win_h = window.current_size_accurate()
    sg.popup_no_titlebar(
        about_text,
        grab_anywhere=False,
        location=(win_loc_x + win_w/2 - 185, win_loc_y + win_h/2 - 200),
        keep_on_top=True,
        background_color='silver',
        button_color='grey'
    )


HANDLERS = {
    'MENU_ABOUT': about,
}
