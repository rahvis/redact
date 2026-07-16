"""
Sign handlers for WorkOnward Read (fill & sign, certificate signing,
signature validation, form filling).

Module-level core functions (``apply_fill_sign``, ``cert_sign_core``,
``validate_core``, ``fill_form_core``, ``bottom_right_rect_px``) hold the
business-facing logic and are headless-testable; the ``sg.Window``-facing
wrappers stay thin and run the slow cores via ``tasks.run_task`` with an
``on_done`` completion callback (main.py's task-event handling calls it
with ``(window, state, payload)``).

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import os

from workonward_read import forms, signing, tasks
from workonward_read.dialogs import annotate as annotate_dialogs
from workonward_read.dialogs import sign as sign_dialogs
from workonward_read.dialogs.common import error_popup, info_popup
from workonward_read.i18n import _

# Error marker returned by cert_sign_core when the PKCS#12 credentials
# could not be loaded (wrong certificate password or unusable file).
P12_PASSWORD_ERROR = 'p12-password'

# Fixed visible-signature box: 200 x 80 pt in the bottom-right corner.
SIG_BOX_WIDTH_PT = 200.0
SIG_BOX_HEIGHT_PT = 80.0
SIG_BOX_MARGIN_PT = 36.0

_PX_PER_PT = signing.IMPORT_PPI / 72.0


# ---------------------------------------------------------------------------
# Headless cores
# ---------------------------------------------------------------------------

def apply_fill_sign(state, png_b64):
    """Arm the canvas signature tool with a prepared signature image.

    Sets ``state.tool`` and stores the PNG under
    ``state.tool_props['signature']['png_b64']`` — the shape
    ``canvas_tools.tool_defaults(state, 'signature')`` merges for the
    signature tool. The flat ``'signature_png_b64'`` key is kept in sync
    for callers reading the literal contract key.
    """
    state.tool = 'signature'
    props = state.tool_props.setdefault('signature', {})
    props['png_b64'] = png_b64
    props.setdefault('scale', 1.0)
    state.tool_props['signature_png_b64'] = png_b64


def bottom_right_rect_px(page_w_pt, page_h_pt,
                         width_pt=SIG_BOX_WIDTH_PT,
                         height_pt=SIG_BOX_HEIGHT_PT,
                         margin_pt=SIG_BOX_MARGIN_PT):
    """Fixed bottom-right signature box as ``[x0, y0, x1, y1]`` in
    200-PPI, y-down image pixels (the coordinate space signing.sign_pdf
    expects for ``visible['rect_px']``)."""
    right_pt = page_w_pt - margin_pt
    left_pt = max(0.0, right_pt - width_pt)
    bottom_pt = margin_pt                      # distance from page bottom
    top_pt = min(page_h_pt, bottom_pt + height_pt)
    return [
        left_pt * _PX_PER_PT,
        (page_h_pt - top_pt) * _PX_PER_PT,     # y-down: top edge first
        right_pt * _PX_PER_PT,
        (page_h_pt - bottom_pt) * _PX_PER_PT,
    ]


def _page_size_pt(input_path, page_index, password=None):
    """Return (width_pt, height_pt) of a page's mediabox.

    Raises:
        IndexError: If page_index is out of range.
        ValueError: If the file is encrypted and the password is wrong.
    """
    from pypdf import PdfReader

    reader = PdfReader(input_path)
    if reader.is_encrypted:
        if not reader.decrypt(password or ''):
            raise ValueError(
                'The PDF is encrypted and the password is missing or wrong.')
    if page_index < 0 or page_index >= len(reader.pages):
        raise IndexError(
            f'Page index {page_index} out of range '
            f'(document has {len(reader.pages)} pages).')
    box = reader.pages[page_index].mediabox
    return float(box.width), float(box.height)


def cert_sign_core(request, progress_cb=None):
    """Sign ``request['input']`` with a PKCS#12 credential.

    Returns ``{'ok': True, 'output': path}`` on success or
    ``{'ok': False, 'error': P12_PASSWORD_ERROR, 'detail': str}`` when the
    certificate file could not be opened (wrong password / unusable file).
    Other errors propagate (run_task turns them into ERROR events).
    """
    def report(pct, msg=''):
        if progress_cb:
            progress_cb(pct, msg)

    report(5, 'preparing')
    visible = None
    page_index = request.get('visible_page')
    if page_index is not None:
        page_w, page_h = _page_size_pt(
            request['input'], int(page_index), request.get('password'))
        visible = {
            'page_index': int(page_index),
            'rect_px': bottom_right_rect_px(page_w, page_h),
        }

    report(25, 'signing')
    try:
        signing.sign_pdf(
            request['input'],
            request['output'],
            request['p12_path'],
            request.get('p12_password') or '',
            reason=request.get('reason'),
            location=request.get('location'),
            visible=visible,
            password=request.get('password'),
        )
    except ValueError as exc:
        if 'PKCS#12' in str(exc):
            return {'ok': False, 'error': P12_PASSWORD_ERROR,
                    'detail': str(exc)}
        raise
    report(100, 'done')
    return {'ok': True, 'output': request['output']}


def validate_core(request, progress_cb=None):
    """Validate all signatures of ``request['input']``. Returns the
    signing.validate_signatures result list."""
    if progress_cb:
        progress_cb(10, 'validating')
    trust_roots = [request['trust_root']] if request.get('trust_root') else None
    results = signing.validate_signatures(
        request['input'],
        extra_trust_roots=trust_roots,
        password=request.get('password'),
    )
    if progress_cb:
        progress_cb(100, 'done')
    return results


def fill_form_core(request, progress_cb=None):
    """Fill AcroForm fields and write the result. Returns the output path."""
    if progress_cb:
        progress_cb(10, 'filling')
    output = forms.fill_fields(
        request['input'],
        request['output'],
        request['values'],
        password=request.get('password'),
        flatten=bool(request.get('flatten')),
    )
    if progress_cb:
        progress_cb(100, 'done')
    return output


# ---------------------------------------------------------------------------
# Window-facing handlers
# ---------------------------------------------------------------------------

def fill_sign(window, state):
    """Fill & Sign: build a signature image (type / draw / image) and arm
    the canvas signature tool for click-to-place."""
    if not state.images:
        info_popup(window, _('Open a document first.'))
        return None
    props = annotate_dialogs.signature_dialog(window, state, (0, 0))
    if not props:
        return None
    apply_fill_sign(state, props['png_b64'])
    try:
        window['-TOOL-'].update(value='signature')
    except Exception:
        pass
    info_popup(window, _('Click on the page to place your signature. '
                         'Use Save Redacted PDF to burn it in.'))
    return None


def cert_sign(window, state):
    """Certificate Sign: sign a PDF file with a .p12/.pfx credential."""
    request = sign_dialogs.cert_sign_dialog(window, state)
    if request is None:
        return None
    password = state.password_for(request['input'])
    if password:
        request['password'] = password

    def _done(done_window, done_state, result):
        if result.get('ok'):
            info_popup(done_window,
                       _('Signed PDF saved: {path}', path=result['output']))
        elif result.get('error') == P12_PASSWORD_ERROR:
            error_popup(done_window,
                        _('The certificate file could not be opened — the '
                          'certificate password is probably wrong. '
                          'Please try again.'),
                        result.get('detail'))
            cert_sign(done_window, done_state)

    return tasks.run_task(window, cert_sign_core, request, on_done=_done)


def validate_sigs(window, state):
    """Validate Signatures: check every signature of a PDF file."""
    request = sign_dialogs.validate_dialog(window, state)
    if request is None:
        return None
    password = state.password_for(request['input'])
    if password:
        request['password'] = password

    def _done(done_window, done_state, results):
        sign_dialogs.validation_results_window(done_window, results)

    return tasks.run_task(window, validate_core, request, on_done=_done)


def fill_form(window, state):
    """Fill Form: fill the AcroForm fields of a PDF file."""
    source = sign_dialogs.fill_form_source_dialog(window, state)
    if source is None:
        return None
    input_path = source['input']
    password = state.password_for(input_path)
    try:
        fields = forms.list_fields(input_path, password=password)
    except Exception as exc:
        error_popup(window, _('error_occurred'), exc)
        return None
    if not any(f.get('type') in sign_dialogs.EDITABLE_FIELD_TYPES
               for f in fields):
        info_popup(window, _('No fillable form fields found — use '
                             'Fill & Sign to type on the page instead.'))
        return None

    root, ext = os.path.splitext(input_path)
    request = sign_dialogs.fill_form_dialog(
        window, fields, default_output=root + '-filled' + (ext or '.pdf'))
    if request is None:
        return None
    request['input'] = input_path
    if password:
        request['password'] = password

    def _done(done_window, done_state, output):
        sign_dialogs.offer_open_result(done_window, output)

    return tasks.run_task(window, fill_form_core, request, on_done=_done)


HANDLERS = {
    'MENU_FILL_SIGN': fill_sign,
    'MENU_CERT_SIGN': cert_sign,
    'MENU_VALIDATE_SIGS': validate_sigs,
    'MENU_FILL_FORM': fill_form,
}
