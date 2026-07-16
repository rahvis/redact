"""
Handler registry for CoverUP PDF.

Merges every group module's ``HANDLERS`` dict into one registry that
``main.py`` dispatches menu events through. Duplicate keys are a startup
error. ``TOOLBAR_HANDLERS`` maps the classic toolbar icon events to the
same handler callables (plus the three toolbar-only toggles).

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

from coverup.handlers import (
    annotate,
    convert,
    edit,
    file,
    help,
    organize,
    protect,
    review,
    sign,
    view,
)

_GROUPS = (file, edit, view, help, organize, protect, convert, review, sign, annotate)

HANDLERS = {}
for _group in _GROUPS:
    for _key, _handler in _group.HANDLERS.items():
        if _key in HANDLERS:
            raise RuntimeError(f'duplicate handler key: {_key}')
        HANDLERS[_key] = _handler
del _group, _key, _handler


# Classic toolbar icon events dispatch to the same handler functions.
TOOLBAR_HANDLERS = {
    'LOAD_PDF': HANDLERS['MENU_OPEN'],
    'SAVE_PDF': HANDLERS['MENU_SAVE_REDACTED'],
    'EXPORT_PAGE': HANDLERS['MENU_EXPORT_PAGE'],
    'UNDO': HANDLERS['MENU_UNDO'],
    'DELETE_ALL': HANDLERS['MENU_DELETE_ALL'],
    'ZOOM_IN': HANDLERS['MENU_ZOOM_IN'],
    'ZOOM_OUT': HANDLERS['MENU_ZOOM_OUT'],
    'BACK': HANDLERS['MENU_PREV_PAGE'],
    'FORTH': HANDLERS['MENU_NEXT_PAGE'],
    'ABOUT': HANDLERS['MENU_ABOUT'],
    'CHANGE_COLOR': view.change_color,
    'TOGGLE_QUALITY': view.toggle_quality,
    'EDIT_MODE': view.toggle_eraser,
}
