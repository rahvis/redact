"""
Sign handlers for WorkOnward Read (fill & sign, certificate signing, validation,
forms).

Wave-2 replaces the shared placeholder with real implementations.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

from workonward_read.dialogs.common import not_yet as _not_yet

HANDLERS = {
    'MENU_FILL_SIGN': _not_yet,
    'MENU_CERT_SIGN': _not_yet,
    'MENU_VALIDATE_SIGS': _not_yet,
    'MENU_FILL_FORM': _not_yet,
}
