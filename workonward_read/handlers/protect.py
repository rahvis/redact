"""
Protect handlers for WorkOnward Read (passwords, sanitize).

Wave-2 replaces the shared placeholder with real implementations.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

from workonward_read.dialogs.common import not_yet as _not_yet

HANDLERS = {
    'MENU_SET_PASSWORDS': _not_yet,
    'MENU_REMOVE_PASSWORD': _not_yet,
    'MENU_SANITIZE': _not_yet,
}
