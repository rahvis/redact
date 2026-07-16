"""
Convert handlers for WorkOnward Read (PDF to images/text/word/html, images to
PDF, OCR).

Wave-2 replaces the shared placeholder with real implementations.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

from workonward_read.dialogs.common import not_yet as _not_yet

HANDLERS = {
    'MENU_CONVERT_IMAGES': _not_yet,
    'MENU_CONVERT_TEXT': _not_yet,
    'MENU_CONVERT_WORD': _not_yet,
    'MENU_CONVERT_HTML': _not_yet,
    'MENU_IMAGES_TO_PDF': _not_yet,
    'MENU_OCR': _not_yet,
}
