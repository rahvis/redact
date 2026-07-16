"""
Annotate handlers for WorkOnward Read (document decorations: watermark,
header & footer).

Wave-2 replaces the shared placeholder with real implementations.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

from workonward_read.dialogs.common import not_yet as _not_yet

HANDLERS = {
    'MENU_WATERMARK': _not_yet,
    'MENU_HEADER_FOOTER': _not_yet,
}
