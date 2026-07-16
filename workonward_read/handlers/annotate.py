"""
Annotate handlers for WorkOnward Read (document decorations: watermark,
header & footer / page numbers / Bates numbering).

Decorations live in ``state.decorations`` (document-level) and are burned in
at export by the annotations engine. After a dialog changes them the current
page is refreshed so the user sees a live preview: the decoration overlay is
rendered into a COPY of the displayed (scaled) image via
``ImageContainer.display_data`` — zoom and page flips never touch the
original image data.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

from workonward_read.dialogs import annotate as annotate_dialogs
from workonward_read.handlers.view import flip_to_page


def _refresh_preview(window, state):
    """Redisplay the current page so decoration changes are visible."""
    if state.images:
        state.current_page = flip_to_page(
            window, state.images, state.current_page, state)


def watermark(window, state):
    """Configure (or remove) the document watermark."""
    if not state.images:
        return
    request = annotate_dialogs.watermark_dialog(window, state)
    if request is None:
        return
    if request.get('remove'):
        state.decorations.pop('watermark', None)
    else:
        state.decorations['watermark'] = request
    _refresh_preview(window, state)


def header_footer(window, state):
    """Configure header/footer text, page numbers and Bates numbering."""
    if not state.images:
        return
    request = annotate_dialogs.header_footer_dialog(window, state)
    if request is None:
        return
    for key in ('header_footer', 'page_numbers', 'bates'):
        value = request.get(key)
        if value:
            state.decorations[key] = value
        else:
            state.decorations.pop(key, None)
    _refresh_preview(window, state)


HANDLERS = {
    'MENU_WATERMARK': watermark,
    'MENU_HEADER_FOOTER': header_footer,
}
