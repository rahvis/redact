"""Headless integration tests for the Sign & Forms handler group
(handlers/sign.py cores + dialogs/sign.py pure helpers).

Credentials are synthesized in-test with `cryptography` (mirrors
tests/test_signing.py) — no binary fixtures, no real windows.

License: GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import os
from datetime import datetime, timedelta, timezone

import fixtures
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from workonward_read import canvas_tools, forms, tasks
from workonward_read.dialogs import sign as sign_dialogs
from workonward_read.dialogs.common import not_yet
from workonward_read.handlers import sign as sign_handlers
from workonward_read.state import AppState

TEST_CN = 'WorkOnward Read Sign-Group Signer'
P12_PASSWORD = 'test-pass'


def _make_credentials(tmp_path, cn=TEST_CN, password=P12_PASSWORD.encode()):
    """Create a self-signed cert + RSA key, return (p12_path, pem_path)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, cn),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'WorkOnward Read Tests'),
        ]
    )
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None),
                       critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    p12_bytes = pkcs12.serialize_key_and_certificates(
        name=b'workonward_read-sign-test',
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(password),
    )
    p12_path = tmp_path / 'signer.p12'
    p12_path.write_bytes(p12_bytes)

    pem_path = tmp_path / 'signer.pem'
    pem_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(p12_path), str(pem_path)


@pytest.fixture(scope='module')
def credentials(tmp_path_factory):
    return _make_credentials(tmp_path_factory.mktemp('sign-creds'))


class FakeWindow:
    """Collects write_event_value calls like an sg.Window would."""

    def __init__(self):
        self.events = []

    def write_event_value(self, key, value):
        self.events.append((key, value))


def _done_payload(window, key=sign_handlers.TASK_KEY):
    payloads = [value for (event, value) in window.events
                if event == (key, 'DONE')]
    assert payloads, f'no DONE event; events: {window.events}'
    return payloads[0]


# ---------------------------------------------------------------------------
# Handler registry shape
# ---------------------------------------------------------------------------

def test_handlers_dict_shape_and_real_implementations():
    expected = {'MENU_FILL_SIGN', 'MENU_CERT_SIGN', 'MENU_VALIDATE_SIGS',
                'MENU_FILL_FORM'}
    assert set(sign_handlers.HANDLERS) == expected
    for key, handler in sign_handlers.HANDLERS.items():
        assert callable(handler), key
        assert handler is not not_yet, f'{key} is still the placeholder'


# ---------------------------------------------------------------------------
# Fill & Sign core (plain AppState, no window)
# ---------------------------------------------------------------------------

def test_apply_fill_sign_sets_tool_and_props():
    state = AppState()
    sign_handlers.apply_fill_sign(state, 'PNG_B64_DATA')

    assert state.tool == 'signature'
    # The shape the canvas signature tool consumes via tool_defaults().
    assert state.tool_props['signature']['png_b64'] == 'PNG_B64_DATA'
    merged = canvas_tools.tool_defaults(state, 'signature')
    assert merged['png_b64'] == 'PNG_B64_DATA'
    assert merged['scale'] == 1.0
    # Literal contract key kept in sync.
    assert state.tool_props['signature_png_b64'] == 'PNG_B64_DATA'


def test_apply_fill_sign_preserves_existing_scale():
    state = AppState()
    state.tool_props['signature'] = {'scale': 2.0}
    sign_handlers.apply_fill_sign(state, 'X')
    assert canvas_tools.tool_defaults(state, 'signature')['scale'] == 2.0


def test_fill_sign_wrapper_requires_loaded_document(monkeypatch):
    messages = []
    monkeypatch.setattr(sign_handlers, 'info_popup',
                        lambda window, message: messages.append(message))
    opened = []
    monkeypatch.setattr(sign_handlers.annotate_dialogs, 'signature_dialog',
                        lambda *args: opened.append(args) or None)
    state = AppState()  # no images loaded
    sign_handlers.fill_sign(FakeWindow(), state)
    assert not opened
    assert messages and 'Open a document' in messages[0]
    assert state.tool == 'redact'  # unchanged


def test_fill_sign_wrapper_arms_tool_and_informs(monkeypatch):
    messages = []
    monkeypatch.setattr(sign_handlers, 'info_popup',
                        lambda window, message: messages.append(message))
    monkeypatch.setattr(
        sign_handlers.annotate_dialogs, 'signature_dialog',
        lambda window, state, pos: {'pos': [0, 0], 'png_b64': 'SIGPNG',
                                    'scale': 1.0})
    state = AppState()
    state.images = [object()]
    sign_handlers.fill_sign(FakeWindow(), state)
    assert state.tool == 'signature'
    assert state.tool_props['signature']['png_b64'] == 'SIGPNG'
    assert messages == ['Click on the page to place your signature. '
                        'Use Save Redacted PDF to burn it in.']


# ---------------------------------------------------------------------------
# Certificate signing core: round-trip, visible box, error marker
# ---------------------------------------------------------------------------

def test_cert_sign_core_invisible_roundtrip(tmp_path, credentials):
    p12_path, pem_path = credentials
    src = fixtures.make_pdf(tmp_path / 'src.pdf', pages=2)
    out = str(tmp_path / 'signed.pdf')
    progress = []

    result = sign_handlers.cert_sign_core(
        {
            'input': src,
            'output': out,
            'p12_path': p12_path,
            'p12_password': P12_PASSWORD,
            'reason': 'Approval',
            'location': 'Zurich',
            'visible_page': None,
        },
        progress_cb=lambda pct, msg='': progress.append(pct),
    )
    assert result == {'ok': True, 'output': out}
    assert os.path.isfile(out)
    assert progress and progress[-1] == 100

    # Round-trip via validate_core: intact + valid, untrusted without roots.
    reports = sign_handlers.validate_core({'input': out})
    assert len(reports) == 1
    assert reports[0]['intact'] is True
    assert reports[0]['valid'] is True
    assert reports[0]['trusted'] is False
    assert reports[0]['signer_cn'] == TEST_CN

    # With the self-signed cert as trust root: trusted.
    trusted = sign_handlers.validate_core(
        {'input': out, 'trust_root': pem_path})
    assert trusted[0]['trusted'] is True


def test_cert_sign_core_visible_box_bottom_right(tmp_path, credentials):
    p12_path, _pem = credentials
    src = fixtures.make_pdf(tmp_path / 'src.pdf', pages=2)
    out = str(tmp_path / 'signed.pdf')

    result = sign_handlers.cert_sign_core(
        {
            'input': src,
            'output': out,
            'p12_path': p12_path,
            'p12_password': P12_PASSWORD,
            'visible_page': 0,
        }
    )
    assert result['ok'] is True

    reports = sign_handlers.validate_core({'input': out})
    assert reports[0]['intact'] is True

    from pypdf import PdfReader

    page = PdfReader(out).pages[0]
    page_w = float(page.mediabox.width)
    widgets = [
        annot.get_object()
        for annot in (page.get('/Annots') or [])
        if str(annot.get_object().get('/Subtype', '')) == '/Widget'
    ]
    assert widgets, 'visible signature widget missing on page 0'
    rect = [float(v) for v in widgets[0]['/Rect']]
    x0, y0 = min(rect[0], rect[2]), min(rect[1], rect[3])
    x1, y1 = max(rect[0], rect[2]), max(rect[1], rect[3])
    margin = sign_handlers.SIG_BOX_MARGIN_PT
    assert abs(x1 - (page_w - margin)) < 0.5
    assert abs((x1 - x0) - sign_handlers.SIG_BOX_WIDTH_PT) < 0.5
    assert abs(y0 - margin) < 0.5
    assert abs((y1 - y0) - sign_handlers.SIG_BOX_HEIGHT_PT) < 0.5


def test_bottom_right_rect_px_geometry():
    # A4: 595.28 x 841.89 pt; expected box 200x80pt with 36pt margins.
    rect = sign_handlers.bottom_right_rect_px(595.28, 841.89)
    scale = 200.0 / 72.0
    assert rect[0] == pytest.approx((595.28 - 36 - 200) * scale)
    assert rect[2] == pytest.approx((595.28 - 36) * scale)
    # y-down px: top edge (36+80 pt above the bottom) has the smaller y.
    assert rect[1] == pytest.approx((841.89 - 116) * scale)
    assert rect[3] == pytest.approx((841.89 - 36) * scale)
    assert rect[1] < rect[3]


def test_cert_sign_core_wrong_p12_password_returns_marker(tmp_path, credentials):
    p12_path, _pem = credentials
    src = fixtures.make_pdf(tmp_path / 'src.pdf', pages=1)
    out = str(tmp_path / 'never-written.pdf')

    result = sign_handlers.cert_sign_core(
        {
            'input': src,
            'output': out,
            'p12_path': p12_path,
            'p12_password': 'totally-wrong',
            'visible_page': None,
        }
    )
    assert result['ok'] is False
    assert result['error'] == sign_handlers.P12_PASSWORD_ERROR
    assert result['detail']
    assert not os.path.exists(out)


def test_cert_sign_core_page_out_of_range(tmp_path, credentials):
    p12_path, _pem = credentials
    src = fixtures.make_pdf(tmp_path / 'src.pdf', pages=1)
    with pytest.raises(IndexError):
        sign_handlers.cert_sign_core(
            {
                'input': src,
                'output': str(tmp_path / 'out.pdf'),
                'p12_path': p12_path,
                'p12_password': P12_PASSWORD,
                'visible_page': 99,
            }
        )


def test_cert_sign_wrapper_reprompts_on_bad_password(tmp_path, credentials,
                                                     monkeypatch):
    """Full task plumbing: dialog -> run_task -> DONE payload -> callback
    shows a friendly error and re-opens the dialog."""
    p12_path, _pem = credentials
    src = fixtures.make_pdf(tmp_path / 'src.pdf', pages=1)
    out = str(tmp_path / 'signed.pdf')

    dialog_calls = []

    def fake_dialog(window, state):
        dialog_calls.append(1)
        if len(dialog_calls) == 1:
            return {
                'input': src, 'output': out, 'p12_path': p12_path,
                'p12_password': 'wrong', 'reason': None, 'location': None,
                'visible_page': None,
            }
        return None  # user cancels the re-prompt

    errors = []
    monkeypatch.setattr(sign_handlers.sign_dialogs, 'cert_sign_dialog',
                        fake_dialog)
    monkeypatch.setattr(
        sign_handlers, 'error_popup',
        lambda window, message, details=None: errors.append(message))

    window = FakeWindow()
    state = AppState()
    thread = sign_handlers.cert_sign(window, state)
    assert thread is not None
    thread.join(timeout=30)
    assert not thread.is_alive()

    payload = _done_payload(window)
    assert payload['error'] == sign_handlers.P12_PASSWORD_ERROR

    # Simulate main.py's DONE handling: pop and invoke the callback.
    callback = state.task_callbacks.pop(sign_handlers.TASK_KEY)
    callback(window, state, payload)
    assert errors, 'friendly error popup expected'
    assert len(dialog_calls) == 2, 'dialog should be re-opened'


def test_validate_sigs_wrapper_shows_results(tmp_path, credentials,
                                             monkeypatch):
    p12_path, _pem = credentials
    src = fixtures.make_pdf(tmp_path / 'src.pdf', pages=1)
    signed = str(tmp_path / 'signed.pdf')
    assert sign_handlers.cert_sign_core(
        {'input': src, 'output': signed, 'p12_path': p12_path,
         'p12_password': P12_PASSWORD, 'visible_page': None})['ok']

    monkeypatch.setattr(sign_handlers.sign_dialogs, 'validate_dialog',
                        lambda window, state: {'input': signed,
                                               'trust_root': None})
    shown = []
    monkeypatch.setattr(sign_handlers.sign_dialogs,
                        'validation_results_window',
                        lambda window, results: shown.append(results))

    window = FakeWindow()
    state = AppState()
    thread = sign_handlers.validate_sigs(window, state)
    thread.join(timeout=30)

    payload = _done_payload(window)
    callback = state.task_callbacks.pop(sign_handlers.TASK_KEY)
    callback(window, state, payload)
    assert shown and shown[0][0]['intact'] is True
    assert shown[0][0]['signer_cn'] == TEST_CN


# ---------------------------------------------------------------------------
# Validation results formatting (pure)
# ---------------------------------------------------------------------------

def test_format_validation_results_lists_each_signature():
    results = [
        {'field_name': 'Signature1', 'signer_cn': 'Alice Example',
         'signing_time_iso': '2026-07-16T10:00:00+00:00',
         'intact': True, 'valid': True, 'trusted': False,
         'summary': 'INTACT:UNTRUSTED'},
        {'field_name': 'Signature2', 'signer_cn': None,
         'signing_time_iso': None,
         'intact': False, 'valid': False, 'trusted': False,
         'summary': 'INVALID'},
    ]
    text = sign_dialogs.format_validation_results(results)
    assert 'Signature1' in text
    assert 'Alice Example' in text
    assert '2026-07-16T10:00:00+00:00' in text
    assert '✓' in text and '✗' in text
    assert 'INTACT:UNTRUSTED' in text
    assert '2 signature(s): 1 intact, 0 trusted.' in text


def test_format_validation_results_empty():
    text = sign_dialogs.format_validation_results([])
    assert 'No digital signatures' in text


# ---------------------------------------------------------------------------
# Form filling core
# ---------------------------------------------------------------------------

def test_fill_form_core_fills_fixture_fields(tmp_path):
    src = fixtures.make_form_pdf(tmp_path / 'form.pdf')
    out = str(tmp_path / 'filled.pdf')
    progress = []

    result = sign_handlers.fill_form_core(
        {'input': src, 'output': out,
         'values': {'name': 'Alice', 'city': 'Zurich'}, 'flatten': False},
        progress_cb=lambda pct, msg='': progress.append(pct),
    )
    assert result == out
    assert progress and progress[-1] == 100

    read_back = {f['name']: f for f in forms.list_fields(out)}
    assert read_back['name']['value'] == 'Alice'
    assert read_back['city']['value'] == 'Zurich'
    assert read_back['name']['read_only'] is False


def test_fill_form_core_flatten_marks_read_only(tmp_path):
    src = fixtures.make_form_pdf(tmp_path / 'form.pdf')
    out = str(tmp_path / 'flat.pdf')
    sign_handlers.fill_form_core(
        {'input': src, 'output': out, 'values': {'name': 'Bob'},
         'flatten': True})
    read_back = {f['name']: f for f in forms.list_fields(out)}
    assert read_back['name']['value'] == 'Bob'
    assert all(f['read_only'] for f in read_back.values())


def test_fill_form_core_via_run_task(tmp_path):
    """The core runs through tasks.run_task exactly like main.py would."""
    src = fixtures.make_form_pdf(tmp_path / 'form.pdf')
    out = str(tmp_path / 'filled.pdf')
    window = FakeWindow()

    thread = tasks.run_task(
        window, sign_handlers.fill_form_core,
        {'input': src, 'output': out, 'values': {'name': 'Carol'},
         'flatten': False})
    thread.join(timeout=30)

    assert _done_payload(window) == out
    progress_events = [e for e in window.events
                       if e[0] == (sign_handlers.TASK_KEY, 'PROGRESS')]
    assert progress_events, 'progress_cb should be injected and used'


def test_fill_form_wrapper_no_fields_popup(tmp_path, monkeypatch):
    plain = fixtures.make_pdf(tmp_path / 'plain.pdf', pages=1)
    monkeypatch.setattr(sign_handlers.sign_dialogs, 'fill_form_source_dialog',
                        lambda window, state: {'input': plain})
    messages = []
    monkeypatch.setattr(sign_handlers, 'info_popup',
                        lambda window, message: messages.append(message))
    opened = []
    monkeypatch.setattr(sign_handlers.sign_dialogs, 'fill_form_dialog',
                        lambda *args, **kwargs: opened.append(1) or None)

    result = sign_handlers.fill_form(FakeWindow(), AppState())
    assert result is None
    assert not opened
    assert messages == ['No fillable form fields found — use Fill & Sign '
                        'to type on the page instead.']


def test_fill_form_wrapper_end_to_end(tmp_path, monkeypatch):
    src = fixtures.make_form_pdf(tmp_path / 'form.pdf')
    out = str(tmp_path / 'filled.pdf')

    monkeypatch.setattr(sign_handlers.sign_dialogs, 'fill_form_source_dialog',
                        lambda window, state: {'input': src})

    seen_fields = []

    def fake_form_dialog(window, fields, default_output=''):
        seen_fields.extend(fields)
        assert default_output.endswith('-filled.pdf')
        return {'values': {'name': 'Dave', 'city': 'Bern'},
                'flatten': False, 'output': out}

    monkeypatch.setattr(sign_handlers.sign_dialogs, 'fill_form_dialog',
                        fake_form_dialog)
    offered = []
    monkeypatch.setattr(sign_handlers.sign_dialogs, 'offer_open_result',
                        lambda window, path: offered.append(path))

    window = FakeWindow()
    state = AppState()
    thread = sign_handlers.fill_form(window, state)
    thread.join(timeout=30)

    assert {f['name'] for f in seen_fields} == {'name', 'city'}
    payload = _done_payload(window)
    assert payload == out

    callback = state.task_callbacks.pop(sign_handlers.TASK_KEY)
    callback(window, state, payload)
    assert offered == [out]

    read_back = {f['name']: f['value'] for f in forms.list_fields(out)}
    assert read_back == {'name': 'Dave', 'city': 'Bern'}
