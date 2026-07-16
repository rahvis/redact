"""
Shared dialog helpers for WorkOnward Read.

Provides window-centered popup positioning, error/info popups, a reusable
file-open row and the page-range parser used by page-level tools.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import FreeSimpleGUI as sg

from workonward_read.i18n import _


def centered(window, width=370, height=400):
    """
    Return an (x, y) location that centers a popup of the given size over
    the parent window. Falls back to (None, None) (screen center) when the
    parent geometry cannot be determined.
    """
    try:
        win_loc_x, win_loc_y = window.current_location()
        win_w, win_h = window.current_size_accurate()
        return (int(win_loc_x + win_w / 2 - width / 2),
                int(win_loc_y + win_h / 2 - height / 2))
    except Exception:
        return (None, None)


def error_popup(window, message, details=None):
    """Show a modal error popup centered over the parent window."""
    args = (message,) if details is None else (message, str(details))
    sg.popup(*args, keep_on_top=True, location=centered(window))


def info_popup(window, message):
    """Show a frameless informational popup centered over the parent window."""
    sg.popup_no_titlebar(
        message,
        grab_anywhere=False,
        location=centered(window),
        keep_on_top=True,
        background_color='silver',
        button_color='grey'
    )


def require_document_free(window, state):
    """Entry guard for document-mutating/consuming operations.

    Returns True when no background task is currently using the loaded
    document (``state.doc_lock`` busy set is empty). Otherwise shows an
    info popup and returns False. Page ops, OCR-current-doc and save/export
    check this; file->file tools (merge/split/compress on picked files)
    intentionally do not.
    """
    if getattr(state, 'doc_lock', None):
        info_popup(window, _('Another operation is using the document — '
                             'wait for it to finish.'))
        return False
    return True


def not_yet(window, state):
    """Shared placeholder handler for tools that arrive in a later wave."""
    info_popup(window, _('This tool arrives in the next build step.'))


def file_open_row(key='-FILE-', file_types=None, save_as=False, default_path=''):
    """Return a [Input, Browse] row for use inside dialog layouts."""
    browse_cls = sg.FileSaveAs if save_as else sg.FileBrowse
    kwargs = {}
    if file_types:
        kwargs['file_types'] = file_types
    return [
        sg.Input(default_text=default_path, key=key, expand_x=True),
        browse_cls(**kwargs),
    ]


def parse_page_ranges(spec, total):
    """
    Parse a 1-based page-range string like ``'1-3,7,9-'`` into a sorted list
    of unique 0-based page indices.

    Supported token forms: ``N`` (single page), ``N-M`` (inclusive range)
    and ``N-`` (open-ended: from N to the last page). Whitespace around
    tokens is ignored.

    Args:
        spec: Range specification string (1-based page numbers).
        total: Total number of pages in the document.

    Returns:
        list[int]: Sorted, unique, 0-based page indices.

    Raises:
        ValueError: ``'bad token: X'`` for any malformed or out-of-range token.
    """
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError(f'bad token: {spec!r}')

    pages = set()
    for raw_token in spec.split(','):
        token = raw_token.strip()
        if not token:
            raise ValueError(f'bad token: {raw_token!r}')

        if '-' in token:
            left, _sep, right = token.partition('-')
            left = left.strip()
            right = right.strip()
            if not left.isdigit():
                raise ValueError(f'bad token: {token}')
            start = int(left)
            if right:
                if not right.isdigit():
                    raise ValueError(f'bad token: {token}')
                end = int(right)
            else:
                end = total
            if start < 1 or start > total or end < start or end > total:
                raise ValueError(f'bad token: {token}')
            pages.update(range(start - 1, end))
        else:
            if not token.isdigit():
                raise ValueError(f'bad token: {token}')
            number = int(token)
            if number < 1 or number > total:
                raise ValueError(f'bad token: {token}')
            pages.add(number - 1)

    return sorted(pages)
