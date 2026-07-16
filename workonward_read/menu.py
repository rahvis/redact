"""
Menu bar definition for WorkOnward Read.

Builds the FreeSimpleGUI menu definition (a nested list). Every actionable
item is encoded as ``'<translated label>::<KEY>'`` where the KEY is stable
across languages; ``main.py`` normalizes events with
``event.rsplit('::', 1)[-1]`` before dispatching through the handler
registry in :mod:`workonward_read.handlers`.

This module deliberately does not import FreeSimpleGUI so the menu spec can
be built and inspected without a GUI toolkit present.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

from workonward_read.i18n import _

MENU_SEPARATOR = '---'


def _item(label, key):
    """Return a menu item string '<label>::<KEY>'."""
    return f'{label}::{key}'


def build_menu():
    """
    Build and return the sg.Menu definition (nested list of menus/items).

    The result is suitable for ``sg.Menu(build_menu())``.
    """
    file_menu = [
        _item(_('Open…'), 'MENU_OPEN'),
        MENU_SEPARATOR,
        _item(_('Save Redacted PDF…'), 'MENU_SAVE_REDACTED'),
        _item(_('Export Current Page…'), 'MENU_EXPORT_PAGE'),
        _item(_('Save Organized PDF…'), 'MENU_SAVE_ORGANIZED'),
        MENU_SEPARATOR,
        _item(_('Images to PDF…'), 'MENU_IMAGES_TO_PDF'),
        _('Convert'),
        [
            _item(_('PDF to Images…'), 'MENU_CONVERT_IMAGES'),
            _item(_('PDF to Text…'), 'MENU_CONVERT_TEXT'),
            _item(_('PDF to Word…'), 'MENU_CONVERT_WORD'),
            _item(_('PDF to HTML…'), 'MENU_CONVERT_HTML'),
        ],
        MENU_SEPARATOR,
        _item(_('Print…'), 'MENU_PRINT'),
        MENU_SEPARATOR,
        _item(_('Exit'), 'MENU_EXIT'),
    ]

    edit_menu = [
        _item(_('Undo'), 'MENU_UNDO'),
        _item(_('Redo'), 'MENU_REDO'),
        MENU_SEPARATOR,
        _item(_('Delete All Redactions'), 'MENU_DELETE_ALL'),
        MENU_SEPARATOR,
        _item(_('Search…'), 'MENU_SEARCH'),
    ]

    view_menu = [
        _item(_('Zoom In'), 'MENU_ZOOM_IN'),
        _item(_('Zoom Out'), 'MENU_ZOOM_OUT'),
        MENU_SEPARATOR,
        _item(_('Previous Page'), 'MENU_PREV_PAGE'),
        _item(_('Next Page'), 'MENU_NEXT_PAGE'),
        MENU_SEPARATOR,
        _item(_('Thumbnails'), 'MENU_THUMBNAILS'),
    ]

    tools_menu = [
        _('Organize Pages'),
        [
            _item(_('Merge PDFs…'), 'MENU_MERGE'),
            _item(_('Split PDF…'), 'MENU_SPLIT'),
            _item(_('Insert Pages…'), 'MENU_INSERT_PAGES'),
            _item(_('Delete Pages…'), 'MENU_DELETE_PAGES'),
            _item(_('Reorder Pages…'), 'MENU_REORDER_PAGES'),
            _item(_('Rotate Pages…'), 'MENU_ROTATE_PAGES'),
            _item(_('Extract Pages…'), 'MENU_EXTRACT_PAGES'),
            _item(_('Crop Pages…'), 'MENU_CROP'),
        ],
        MENU_SEPARATOR,
        _item(_('Watermark…'), 'MENU_WATERMARK'),
        _item(_('Header & Footer…'), 'MENU_HEADER_FOOTER'),
        MENU_SEPARATOR,
        _item(_('Compress PDF…'), 'MENU_COMPRESS'),
        _item(_('Recognize Text (OCR)…'), 'MENU_OCR'),
        _item(_('Compare PDFs…'), 'MENU_COMPARE'),
        _item(_('Batch Process…'), 'MENU_BATCH'),
        MENU_SEPARATOR,
        _item(_('Document Properties…'), 'MENU_PROPERTIES'),
    ]

    protect_menu = [
        _item(_('Set Passwords…'), 'MENU_SET_PASSWORDS'),
        _item(_('Remove Password…'), 'MENU_REMOVE_PASSWORD'),
        _item(_('Sanitize PDF…'), 'MENU_SANITIZE'),
    ]

    sign_menu = [
        _item(_('Fill & Sign…'), 'MENU_FILL_SIGN'),
        _item(_('Certificate Sign…'), 'MENU_CERT_SIGN'),
        _item(_('Validate Signatures…'), 'MENU_VALIDATE_SIGS'),
        _item(_('Fill Form…'), 'MENU_FILL_FORM'),
    ]

    help_menu = [
        _item(_('About WorkOnward Read'), 'MENU_ABOUT'),
    ]

    return [
        [_('File'), file_menu],
        [_('Edit'), edit_menu],
        [_('View'), view_menu],
        [_('Tools'), tools_menu],
        [_('Protect'), protect_menu],
        [_('Sign'), sign_menu],
        [_('Help'), help_menu],
    ]


def _walk_keys(entries, keys):
    """Recursively collect '::KEY' suffixes from a menu definition."""
    for entry in entries:
        if isinstance(entry, list):
            _walk_keys(entry, keys)
        elif isinstance(entry, str) and '::' in entry:
            keys.append(entry.rsplit('::', 1)[-1])


def menu_keys(menu_def=None):
    """Return the list of all event keys contained in the menu definition."""
    if menu_def is None:
        menu_def = build_menu()
    keys = []
    _walk_keys(menu_def, keys)
    return keys
