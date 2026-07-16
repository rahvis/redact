"""
Sign dialogs for WorkOnward Read: certificate signing, signature-validation
results and AcroForm filling.

Every dialog is modal, keep-on-top, centered over the parent window and
returns a plain request dict or None when cancelled. The pure helper
``format_validation_results`` lives at module level so it is unit-testable
without opening a window.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import os
import subprocess
import sys

import FreeSimpleGUI as sg

from workonward_read.dialogs.common import centered, error_popup, file_open_row
from workonward_read.i18n import _

_PDF_FILE_TYPES = (('PDF', '*.pdf *.PDF'),)
_P12_FILE_TYPES = (('PKCS#12', '*.p12 *.pfx *.P12 *.PFX'), ('All files', '*.* *'))
_PEM_FILE_TYPES = (('Certificates', '*.pem *.crt *.cer *.der'), ('All files', '*.* *'))

# Field types the fill-form dialog can render as editable widgets.
EDITABLE_FIELD_TYPES = ('text', 'checkbox', 'radio', 'choice')

_CHECK = '✓'
_CROSS = '✗'


def _open_modal(title, layout, window):
    return sg.Window(
        title, layout, modal=True, keep_on_top=True, finalize=True,
        location=centered(window))


def _suggest_output(input_path, suffix):
    """Suggest an output filename next to the input ('doc-signed.pdf')."""
    if not input_path:
        return ''
    root, ext = os.path.splitext(input_path)
    return root + suffix + (ext or '.pdf')


# ---------------------------------------------------------------------------
# Pure helpers (GUI-free, unit-testable)
# ---------------------------------------------------------------------------

def format_validation_results(results):
    """Format signing.validate_signatures results as plain text: one block
    per signature (field, signer CN, time, intact/trusted marks, summary)
    plus a totals line."""
    if not results:
        return _('No digital signatures found in this document.')
    lines = []
    for report in results:
        intact = _CHECK if report.get('intact') else _CROSS
        trusted = _CHECK if report.get('trusted') else _CROSS
        lines.append(_('Field: {name}', name=report.get('field_name') or '?'))
        lines.append('    ' + _('Signer: {cn}',
                                cn=report.get('signer_cn') or _('(unknown)')))
        lines.append('    ' + _('Time: {time}',
                                time=report.get('signing_time_iso')
                                or _('(not recorded)')))
        lines.append('    ' + _('Intact: {intact}    Trusted: {trusted}',
                                intact=intact, trusted=trusted))
        summary = report.get('summary')
        if summary:
            lines.append('    ' + str(summary))
        lines.append('')
    intact_count = sum(1 for r in results if r.get('intact'))
    trusted_count = sum(1 for r in results if r.get('trusted'))
    lines.append(_('{total} signature(s): {intact} intact, {trusted} trusted.',
                   total=len(results), intact=intact_count,
                   trusted=trusted_count))
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Certificate signing
# ---------------------------------------------------------------------------

def cert_sign_dialog(window, state):
    """Certificate-sign dialog. Returns a request dict or None:

    {'input', 'p12_path', 'p12_password', 'reason': str|None,
     'location': str|None, 'visible_page': int|None (0-based), 'output'}
    """
    default_src = state.file_path or ''
    default_out = _suggest_output(default_src, '-signed')
    layout = [
        [sg.Text(_('Sign LAST: any edit made after signing invalidates '
                   'the signature.'), text_color='dark red')],
        [sg.Text(_('PDF to sign'))],
        file_open_row('-SRC-', file_types=_PDF_FILE_TYPES,
                      default_path=default_src),
        [sg.Text(_('Signing uses the file as saved on disk — unsaved edits '
                   'in the editor are NOT included.'),
                 text_color='gray', visible=bool(default_src))],
        [sg.Text(_('Certificate file (.p12 / .pfx)'))],
        file_open_row('-P12-', file_types=_P12_FILE_TYPES),
        [sg.Text(_('Certificate password')),
         sg.Input(key='-P12PW-', password_char='*', size=(24, 1))],
        [sg.Text(_('Reason')),
         sg.Input(key='-REASON-', size=(20, 1)),
         sg.Text(_('Location')),
         sg.Input(key='-LOCATION-', size=(16, 1))],
        [sg.Checkbox(_('Visible signature (bottom-right box)'),
                     key='-VISIBLE-'),
         sg.Text(_('Page')),
         sg.Spin(values=list(range(1, 10000)), initial_value=1,
                 key='-PAGE-', size=(5, 1))],
        [sg.Text(_('Output file'))],
        file_open_row('-OUT-', file_types=_PDF_FILE_TYPES, save_as=True,
                      default_path=default_out),
        [sg.Push(), sg.Button(_('Sign'), key='-OK-'),
         sg.Button(_('Cancel'), key='-CANCEL-')],
    ]
    dialog = _open_modal(_('Certificate Sign'), layout, window)
    result = None
    while True:
        event, values = dialog.read()
        if event in (sg.WINDOW_CLOSED, '-CANCEL-'):
            break
        if event != '-OK-':
            continue
        src = (values.get('-SRC-') or '').strip()
        p12 = (values.get('-P12-') or '').strip()
        out = (values.get('-OUT-') or '').strip()
        if not src or not os.path.isfile(src):
            error_popup(window, _('Please choose a PDF file to sign.'))
            continue
        if not p12 or not os.path.isfile(p12):
            error_popup(window, _('Please choose a .p12 / .pfx certificate file.'))
            continue
        if not out:
            error_popup(window, _('Please choose an output file.'))
            continue
        if os.path.abspath(out) == os.path.abspath(src):
            error_popup(window, _('The output file must differ from the input file.'))
            continue
        try:
            page_index = max(0, int(values.get('-PAGE-') or 1) - 1)
        except (TypeError, ValueError):
            page_index = 0
        result = {
            'input': src,
            'p12_path': p12,
            'p12_password': values.get('-P12PW-') or '',
            'reason': (values.get('-REASON-') or '').strip() or None,
            'location': (values.get('-LOCATION-') or '').strip() or None,
            'visible_page': page_index if values.get('-VISIBLE-') else None,
            'output': out,
        }
        break
    dialog.close()
    return result


# ---------------------------------------------------------------------------
# Signature validation
# ---------------------------------------------------------------------------

def validate_dialog(window, state):
    """Validation source dialog. Returns {'input', 'trust_root': str|None}
    or None."""
    layout = [
        [sg.Text(_('PDF to check'))],
        file_open_row('-SRC-', file_types=_PDF_FILE_TYPES,
                      default_path=state.file_path or ''),
        [sg.Text(_('Trust root certificate (optional, PEM/DER)'))],
        file_open_row('-TRUST-', file_types=_PEM_FILE_TYPES),
        [sg.Text(_("Self-signed certificates show as 'untrusted' unless "
                   'their certificate is provided as a trust root.'),
                 text_color='gray')],
        [sg.Push(), sg.Button(_('Validate'), key='-OK-'),
         sg.Button(_('Cancel'), key='-CANCEL-')],
    ]
    dialog = _open_modal(_('Validate Signatures'), layout, window)
    result = None
    while True:
        event, values = dialog.read()
        if event in (sg.WINDOW_CLOSED, '-CANCEL-'):
            break
        if event != '-OK-':
            continue
        src = (values.get('-SRC-') or '').strip()
        if not src or not os.path.isfile(src):
            error_popup(window, _('Please choose a PDF file to check.'))
            continue
        trust = (values.get('-TRUST-') or '').strip() or None
        if trust and not os.path.isfile(trust):
            error_popup(window, _('The trust root certificate file was not found.'))
            continue
        result = {'input': src, 'trust_root': trust}
        break
    dialog.close()
    return result


def validation_results_window(window, results):
    """Show the validation results in a read-only window."""
    text = format_validation_results(results)
    height = min(24, max(8, text.count('\n') + 2))
    layout = [
        [sg.Multiline(text, size=(72, height), disabled=True,
                      key='-RESULTS-')],
        [sg.Text(_("Note: self-signed certificates show as 'untrusted' "
                   'unless you provide their certificate as a trust root.'),
                 text_color='gray')],
        [sg.Push(), sg.Button(_('Close'), key='-CLOSE-')],
    ]
    dialog = _open_modal(_('Signature Validation Results'), layout, window)
    dialog.read()
    dialog.close()


# ---------------------------------------------------------------------------
# Form filling
# ---------------------------------------------------------------------------

def fill_form_source_dialog(window, state):
    """Form source picker. Returns {'input': path} or None."""
    layout = [
        [sg.Text(_('PDF form to fill'))],
        file_open_row('-SRC-', file_types=_PDF_FILE_TYPES,
                      default_path=state.file_path or ''),
        [sg.Push(), sg.Button(_('OK'), key='-OK-'),
         sg.Button(_('Cancel'), key='-CANCEL-')],
    ]
    dialog = _open_modal(_('Fill Form'), layout, window)
    result = None
    while True:
        event, values = dialog.read()
        if event in (sg.WINDOW_CLOSED, '-CANCEL-'):
            break
        if event != '-OK-':
            continue
        src = (values.get('-SRC-') or '').strip()
        if not src or not os.path.isfile(src):
            error_popup(window, _('Please choose a PDF file.'))
            continue
        result = {'input': src}
        break
    dialog.close()
    return result


def fill_form_dialog(window, fields, default_output=''):
    """Dialog generated from forms.list_fields output. Returns
    {'values': {name: value}, 'flatten': bool, 'output': path} or None.

    Text fields become inputs, checkboxes become checkboxes, choice/radio
    fields become combos; read-only fields are shown disabled; signature and
    other field types are shown as informational text only.
    """
    rows = []
    editable = []
    for index, field in enumerate(fields):
        key = f'-FIELD-{index}-'
        field_type = field.get('type')
        disabled = bool(field.get('read_only'))
        label = sg.Text(str(field.get('name') or ''), size=(20, 1))
        if field_type == 'text':
            element = sg.Input(default_text=str(field.get('value') or ''),
                               key=key, size=(34, 1), disabled=disabled)
        elif field_type == 'checkbox':
            element = sg.Checkbox('', default=bool(field.get('value')),
                                  key=key, disabled=disabled)
        elif field_type in ('choice', 'radio'):
            options = list(field.get('options') or [])
            default = field.get('value')
            if isinstance(default, list):
                default = default[0] if default else ''
            element = sg.Combo(options, default_value=default or '',
                               key=key, readonly=True, size=(32, 1),
                               disabled=disabled)
        else:
            note = (_('(signature field — use Certificate Sign)')
                    if field_type == 'signature' else _('(not fillable here)'))
            rows.append([label, sg.Text(note, text_color='gray')])
            continue
        if not disabled:
            editable.append((key, field))
        rows.append([label, element])

    scrollable = len(rows) > 12
    column_kwargs = {'scrollable': scrollable, 'vertical_scroll_only': True}
    if scrollable:
        column_kwargs['size'] = (500, 340)
    layout = [
        [sg.Column(rows, **column_kwargs)],
        [sg.Checkbox(_('Flatten (make fields read-only after filling)'),
                     key='-FLATTEN-')],
        [sg.Text(_('Output file'))],
        file_open_row('-OUT-', file_types=_PDF_FILE_TYPES, save_as=True,
                      default_path=default_output),
        [sg.Push(), sg.Button(_('OK'), key='-OK-'),
         sg.Button(_('Cancel'), key='-CANCEL-')],
    ]
    dialog = _open_modal(_('Fill Form'), layout, window)
    result = None
    while True:
        event, values = dialog.read()
        if event in (sg.WINDOW_CLOSED, '-CANCEL-'):
            break
        if event != '-OK-':
            continue
        out = (values.get('-OUT-') or '').strip()
        if not out:
            error_popup(window, _('Please choose an output file.'))
            continue
        out_values = {}
        for key, field in editable:
            value = values.get(key)
            if field['type'] == 'checkbox':
                out_values[field['name']] = bool(value)
            elif field['type'] in ('choice', 'radio'):
                if value:
                    out_values[field['name']] = str(value)
            else:
                out_values[field['name']] = '' if value is None else str(value)
        result = {
            'values': out_values,
            'flatten': bool(values.get('-FLATTEN-')),
            'output': out,
        }
        break
    dialog.close()
    return result


def offer_open_result(window, path):
    """Success popup offering to open the written file with the system
    default application."""
    answer = sg.popup_yes_no(
        _('Saved: {path}', path=path),
        _('Open the result now?'),
        keep_on_top=True, location=centered(window))
    if answer != 'Yes':
        return
    try:
        if sys.platform == 'darwin':
            subprocess.Popen(['open', path])
        elif os.name == 'nt':
            os.startfile(path)  # noqa: S606 (Windows only)
        else:
            subprocess.Popen(['xdg-open', path])
    except Exception:
        pass
