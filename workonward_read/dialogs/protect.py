"""
Protect dialogs for WorkOnward Read: set passwords, remove password and
sanitize document.

Every dialog is modal, keep-on-top, centered over the parent window and
returns a plain request dict or None when cancelled. The pure validators
(``validate_set_passwords``, ``validate_remove_password``,
``validate_sanitize``) and ``default_output_path`` live at module level so
they are unit-testable without opening a window.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
(c) 2026 WorkOnward Read contributors
"""

import os

import FreeSimpleGUI as sg

from workonward_read.dialogs.common import (centered, file_open_row,
                                            open_modal as _open_modal)
from workonward_read.i18n import _


PDF_FILE_TYPES = (('PDF', '*.pdf *.PDF'),)


# ---------------------------------------------------------------------------
# Pure helpers (GUI-free, unit-testable)
# ---------------------------------------------------------------------------

def default_output_path(input_path, suffix):
    """Suggest ``<dir>/<base><suffix>.pdf`` next to ``input_path``.

    Returns '' when no input path is available.
    """
    input_path = (input_path or '').strip()
    if not input_path:
        return ''
    base, _ext = os.path.splitext(input_path)
    return f'{base}{suffix}.pdf'


def validate_set_passwords(values):
    """Validate Set Passwords dialog values.

    Args:
        values: dict with keys -INPUT-, -USER-, -CONFIRM-, -OWNER-,
            -ALLOW_PRINT-, -ALLOW_COPY-, -ALLOW_MODIFY-, -OUTPUT-.

    Returns:
        (request, error): ``request`` is the plain dict for
        ``pdf_ops.set_passwords`` and ``error`` is None on success;
        on failure ``request`` is None and ``error`` a translated message.
    """
    input_path = (values.get('-INPUT-') or '').strip()
    output_path = (values.get('-OUTPUT-') or '').strip()
    user_pw = values.get('-USER-') or ''
    confirm_pw = values.get('-CONFIRM-') or ''
    owner_pw = values.get('-OWNER-') or ''

    if not input_path:
        return None, _('Please choose a source PDF.')
    if user_pw != confirm_pw:
        return None, _('Passwords do not match.')
    if not user_pw and not owner_pw:
        return None, _('Enter a user password or an owner password.')
    if not output_path:
        return None, _('Please choose an output file.')

    return {
        'input': input_path,
        'output': output_path,
        'user_pw': user_pw or None,
        'owner_pw': owner_pw or None,
        'allow_print': bool(values.get('-ALLOW_PRINT-')),
        'allow_copy': bool(values.get('-ALLOW_COPY-')),
        'allow_modify': bool(values.get('-ALLOW_MODIFY-')),
    }, None


def validate_remove_password(values):
    """Validate Remove Password dialog values.

    Returns:
        (request, error) — same convention as ``validate_set_passwords``.
    """
    input_path = (values.get('-INPUT-') or '').strip()
    output_path = (values.get('-OUTPUT-') or '').strip()
    if not input_path:
        return None, _('Please choose a source PDF.')
    if not output_path:
        return None, _('Please choose an output file.')
    return {
        'input': input_path,
        'output': output_path,
        'password': values.get('-PASSWORD-') or '',
    }, None


def validate_sanitize(values):
    """Validate Sanitize dialog values.

    Returns:
        (request, error) — same convention as ``validate_set_passwords``.
    """
    input_path = (values.get('-INPUT-') or '').strip()
    output_path = (values.get('-OUTPUT-') or '').strip()
    flags = {
        'strip_metadata': bool(values.get('-METADATA-')),
        'strip_annotations': bool(values.get('-ANNOTATIONS-')),
        'strip_attachments': bool(values.get('-ATTACHMENTS-')),
        'strip_javascript': bool(values.get('-JAVASCRIPT-')),
    }
    if not input_path:
        return None, _('Please choose a source PDF.')
    if not any(flags.values()):
        return None, _('Select at least one item to remove.')
    if not output_path:
        return None, _('Please choose an output file.')
    return {'input': input_path, 'output': output_path, **flags}, None


def format_sanitize_report(report):
    """Turn a ``pdf_ops.sanitize`` report into user-readable popup text."""
    removed = (report or {}).get('removed') or []
    if not removed:
        return _('Nothing needed to be removed.')
    lines = [_('Removed:')]
    lines.extend(f'  • {item}' for item in removed)
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Dialog helpers
# ---------------------------------------------------------------------------

def _run_validated(title, layout, window, validator):
    """Open a modal dialog and loop until the validator accepts the values
    (inline error text in '-ERROR-') or the dialog is cancelled/closed."""
    dialog = _open_modal(title, layout, window)
    request = None
    while True:
        event, values = dialog.read()
        if event in (sg.WINDOW_CLOSED, '-CANCEL-'):
            break
        if event == '-OK-':
            request, error = validator(values)
            if request is not None:
                break
            dialog['-ERROR-'].update(error or '')
    dialog.close()
    return request


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------

def set_passwords_dialog(window, state):
    """Set Passwords dialog. Returns a request dict for
    ``pdf_ops.set_passwords`` or None on cancel."""
    default_input = state.file_path or ''
    layout = [
        [sg.Text(_('Source PDF'))],
        file_open_row('-INPUT-', file_types=PDF_FILE_TYPES,
                      default_path=default_input),
        [sg.Text(_('User password'), size=(18, 1)),
         sg.Input(key='-USER-', password_char='*', size=(24, 1))],
        [sg.Text(_('Confirm password'), size=(18, 1)),
         sg.Input(key='-CONFIRM-', password_char='*', size=(24, 1))],
        [sg.Text(_('Owner password (optional)'), size=(18, 1)),
         sg.Input(key='-OWNER-', password_char='*', size=(24, 1))],
        [sg.Text(_('Leave the owner password empty to reuse the user password.'),
                 text_color='gray')],
        [sg.Frame(_('Permissions'), [[
            sg.Checkbox(_('Allow printing'), default=True, key='-ALLOW_PRINT-'),
            sg.Checkbox(_('Allow copying'), default=False, key='-ALLOW_COPY-'),
            sg.Checkbox(_('Allow modifying'), default=False, key='-ALLOW_MODIFY-'),
        ]])],
        [sg.Text(_('Save encrypted PDF as'))],
        file_open_row('-OUTPUT-', file_types=PDF_FILE_TYPES, save_as=True,
                      default_path=default_output_path(default_input, '_protected')),
        [sg.Text('', key='-ERROR-', text_color='red', size=(48, 1))],
        [sg.Push(), sg.Button(_('OK'), key='-OK-'),
         sg.Button(_('Cancel'), key='-CANCEL-')],
    ]
    return _run_validated(_('Set Passwords'), layout, window,
                          validate_set_passwords)


def remove_password_dialog(window, state, error='', defaults=None):
    """Remove Password dialog. ``error`` is shown inline (re-prompt after a
    wrong password, with the password field cleared); ``defaults`` carries
    the previous attempt's paths. Returns a request dict or None."""
    defaults = defaults or {}
    default_input = defaults.get('input') or state.file_path or ''
    default_output = (defaults.get('output')
                      or default_output_path(default_input, '_decrypted'))
    layout = [
        [sg.Text(_('Source PDF'))],
        file_open_row('-INPUT-', file_types=PDF_FILE_TYPES,
                      default_path=default_input),
        [sg.Text(_('Password'), size=(10, 1)),
         sg.Input(key='-PASSWORD-', password_char='*', size=(24, 1))],
        [sg.Text(_('Save decrypted PDF as'))],
        file_open_row('-OUTPUT-', file_types=PDF_FILE_TYPES, save_as=True,
                      default_path=default_output),
        [sg.Text(error or '', key='-ERROR-', text_color='red', size=(48, 1))],
        [sg.Push(), sg.Button(_('OK'), key='-OK-'),
         sg.Button(_('Cancel'), key='-CANCEL-')],
    ]
    return _run_validated(_('Remove Password'), layout, window,
                          validate_remove_password)


def sanitize_dialog(window, state):
    """Sanitize Document dialog. Returns a request dict for
    ``pdf_ops.sanitize`` or None on cancel."""
    default_input = state.file_path or ''
    layout = [
        [sg.Text(_('Source PDF'))],
        file_open_row('-INPUT-', file_types=PDF_FILE_TYPES,
                      default_path=default_input),
        [sg.Frame(_('Remove'), [
            [sg.Checkbox(_('Metadata (document info & XMP)'), default=True,
                         key='-METADATA-')],
            [sg.Checkbox(_('Annotations'), default=True, key='-ANNOTATIONS-')],
            [sg.Checkbox(_('File attachments'), default=True,
                         key='-ATTACHMENTS-')],
            [sg.Checkbox(_('JavaScript and automatic actions'), default=True,
                         key='-JAVASCRIPT-')],
        ])],
        [sg.Text(_('For pixel-level content redaction use the canvas Redact tool.'),
                 text_color='gray')],
        [sg.Text(_('Save sanitized PDF as'))],
        file_open_row('-OUTPUT-', file_types=PDF_FILE_TYPES, save_as=True,
                      default_path=default_output_path(default_input, '_sanitized')),
        [sg.Text('', key='-ERROR-', text_color='red', size=(48, 1))],
        [sg.Push(), sg.Button(_('OK'), key='-OK-'),
         sg.Button(_('Cancel'), key='-CANCEL-')],
    ]
    return _run_validated(_('Sanitize Document'), layout, window,
                          validate_sanitize)


def show_sanitize_report(window, report):
    """Show the sanitize removal report in a scrollable result popup."""
    sg.popup_scrolled(
        format_sanitize_report(report),
        title=_('Sanitize Report'),
        size=(56, 12),
        modal=True,
        keep_on_top=True,
        location=centered(window),
    )
