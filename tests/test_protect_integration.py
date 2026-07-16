"""Integration tests for the Protect group (headless).

Covers the handler cores in workonward_read/handlers/protect.py — set
passwords (AES round-trip + permission flags verified via pypdf), remove
password (wrong-then-right retry loop) and sanitize (report propagation) —
plus the pure dialog validators in workonward_read/dialogs/protect.py.

No real window is ever opened; run_task integration uses a FakeWindow shim.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
(c) 2026 WorkOnward Read contributors
"""

import fixtures
from fixtures import runtime_pw
from pypdf import PdfReader
from pypdf.constants import UserAccessPermissions as UAP

from workonward_read import tasks
from workonward_read.dialogs import protect as protect_dialogs
from workonward_read.handlers import protect as protect_handlers
from workonward_read.state import AppState


class FakeWindow:
    """Collects write_event_value calls like an sg.Window would."""

    def __init__(self):
        self.events = []

    def write_event_value(self, key, value):
        self.events.append((key, value))


def _set_passwords_request(src, out, **overrides):
    request = {
        'input': str(src),
        'output': str(out),
        'user_pw': runtime_pw('hunter2'),
        'owner_pw': None,
        'allow_print': True,
        'allow_copy': False,
        'allow_modify': False,
    }
    request.update(overrides)
    return request


# ---------------------------------------------------------------------------
# MENU_SET_PASSWORDS core
# ---------------------------------------------------------------------------

def test_set_passwords_task_aes_roundtrip(tmp_path):
    src = fixtures.make_pdf(tmp_path / 'src.pdf', pages=2)
    out = str(tmp_path / 'enc.pdf')
    progress = []

    result = protect_handlers.set_passwords_task(
        _set_passwords_request(src, out), AppState(),
        progress_cb=lambda pct, msg='': progress.append(pct))

    assert result['output'] == out
    assert progress and progress[-1] == 100

    reader = PdfReader(out)
    assert reader.is_encrypted
    # AES-256 (R6) encryption dictionary
    enc_dict = reader.trailer['/Encrypt']
    assert enc_dict['/V'] == 5
    assert enc_dict['/R'] == 6
    assert int(reader.decrypt(runtime_pw('wrong'))) == 0
    assert int(reader.decrypt(runtime_pw('hunter2'))) != 0
    assert 'Page 1' in reader.pages[0].extract_text()
    assert len(reader.pages) == 2


def test_set_passwords_task_permission_flags(tmp_path):
    src = fixtures.make_pdf(tmp_path / 'src.pdf', pages=1)
    out = str(tmp_path / 'enc.pdf')

    protect_handlers.set_passwords_task(
        _set_passwords_request(src, out, owner_pw=runtime_pw('boss'),
                               allow_print=True, allow_copy=False,
                               allow_modify=False),
        AppState())

    reader = PdfReader(out)
    reader.decrypt(runtime_pw('hunter2'))
    perms = reader.user_access_permissions
    assert perms & UAP.PRINT
    assert not perms & UAP.EXTRACT
    assert not perms & UAP.MODIFY
    # Separate owner password opens it too
    reader2 = PdfReader(out)
    assert int(reader2.decrypt(runtime_pw('boss'))) != 0

    out2 = str(tmp_path / 'enc2.pdf')
    protect_handlers.set_passwords_task(
        _set_passwords_request(src, out2, allow_print=False,
                               allow_copy=True, allow_modify=True),
        AppState())
    reader3 = PdfReader(out2)
    reader3.decrypt(runtime_pw('hunter2'))
    perms3 = reader3.user_access_permissions
    assert not perms3 & UAP.PRINT
    assert perms3 & UAP.EXTRACT
    assert perms3 & UAP.MODIFY


def test_set_passwords_task_uses_source_password_of_loaded_file(tmp_path):
    enc = fixtures.make_encrypted_pdf(tmp_path / 'enc.pdf',
                                      user_password=runtime_pw('oldpw'), pages=2)
    out = str(tmp_path / 're-enc.pdf')
    state = AppState()
    state.file_path = str(enc)
    state.source_password = runtime_pw('oldpw')

    protect_handlers.set_passwords_task(
        _set_passwords_request(enc, out, user_pw=runtime_pw('newpw')), state)

    reader = PdfReader(out)
    assert int(reader.decrypt(runtime_pw('newpw'))) != 0
    assert len(reader.pages) == 2


def test_set_passwords_task_ignores_state_password_for_other_files(tmp_path):
    src = fixtures.make_pdf(tmp_path / 'plain.pdf', pages=1)
    out = str(tmp_path / 'enc.pdf')
    state = AppState()
    state.file_path = str(tmp_path / 'someother.pdf')
    state.source_password = runtime_pw('irrelevant')

    assert state.password_for(str(src)) is None
    protect_handlers.set_passwords_task(
        _set_passwords_request(src, out), state)
    assert PdfReader(out).is_encrypted


def test_set_passwords_task_via_run_task_reports_done(tmp_path):
    """The core plugs into the established background-task plumbing."""
    src = fixtures.make_pdf(tmp_path / 'src.pdf', pages=1)
    out = str(tmp_path / 'enc.pdf')
    window = FakeWindow()
    request = _set_passwords_request(src, out)

    thread = tasks.run_task(window, protect_handlers.set_passwords_task,
                            request, AppState())
    thread.join(timeout=10)
    assert not thread.is_alive()

    done = [value for key, value in window.events
            if key == (thread.task_key, 'DONE')]
    assert done == [request]
    progress = [key for key, _v in window.events
                if key == (thread.task_key, 'PROGRESS')]
    assert progress  # progress_cb was injected and used
    assert PdfReader(out).is_encrypted


# ---------------------------------------------------------------------------
# MENU_REMOVE_PASSWORD core (retry loop)
# ---------------------------------------------------------------------------

def test_remove_password_wrong_then_right(tmp_path):
    enc = fixtures.make_encrypted_pdf(tmp_path / 'enc.pdf',
                                      user_password=runtime_pw('geheim'), pages=2)
    out = str(tmp_path / 'plain.pdf')
    prompts = []

    def prompt(error, defaults):
        prompts.append((error, defaults))
        password = runtime_pw('wrong') if len(prompts) == 1 else runtime_pw('geheim')
        return {'input': str(enc), 'password': password, 'output': out}

    status, request = protect_handlers.remove_password_with_retries(prompt)

    assert status == 'ok'
    assert request['password'] == runtime_pw('geheim')
    assert len(prompts) == 2
    # First prompt: no inline error; re-prompt carries the error message and
    # the previous request (so paths can be prefilled).
    assert prompts[0][0] == ''
    assert prompts[0][1] is None
    assert prompts[1][0] == 'Wrong password. Please try again.'
    assert prompts[1][1]['input'] == str(enc)

    reader = PdfReader(out)
    assert not reader.is_encrypted
    assert len(reader.pages) == 2


def test_remove_password_gives_up_after_three_attempts(tmp_path):
    enc = fixtures.make_encrypted_pdf(tmp_path / 'enc.pdf',
                                      user_password=runtime_pw('geheim'))
    out = str(tmp_path / 'plain.pdf')
    prompts = []

    def prompt(error, defaults):
        prompts.append(error)
        return {'input': str(enc), 'password': runtime_pw('nope'), 'output': out}

    status, request = protect_handlers.remove_password_with_retries(prompt)

    assert status == 'failed'
    assert request['password'] == runtime_pw('nope')
    assert len(prompts) == 3


def test_remove_password_cancel_stops_loop(tmp_path):
    calls = []

    def prompt(error, defaults):
        calls.append(error)
        return None

    status, request = protect_handlers.remove_password_with_retries(prompt)
    assert (status, request) == ('cancelled', None)
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# MENU_SANITIZE core (report propagation)
# ---------------------------------------------------------------------------

def test_sanitize_task_propagates_report(tmp_path):
    src = fixtures.make_pdf(tmp_path / 'src.pdf', pages=1)  # fpdf writes info dict
    out = str(tmp_path / 'clean.pdf')
    request = {
        'input': str(src),
        'output': out,
        'strip_metadata': True,
        'strip_annotations': True,
        'strip_attachments': True,
        'strip_javascript': True,
    }

    result = protect_handlers.sanitize_task(request, AppState())

    assert result['request'] is request
    removed = result['report']['removed']
    assert 'document information dictionary' in removed

    reader = PdfReader(out)
    assert not reader.is_encrypted
    meta = reader.metadata
    assert not meta or '/Producer' not in meta


def test_sanitize_task_respects_flags(tmp_path):
    src = fixtures.make_pdf(tmp_path / 'src.pdf', pages=1)
    out = str(tmp_path / 'clean.pdf')
    request = {
        'input': str(src),
        'output': out,
        'strip_metadata': False,
        'strip_annotations': True,
        'strip_attachments': True,
        'strip_javascript': True,
    }

    result = protect_handlers.sanitize_task(request, AppState())

    removed = result['report']['removed']
    assert 'document information dictionary' not in removed
    assert PdfReader(out).metadata  # info dict survived


def test_sanitize_task_uses_source_password_of_loaded_file(tmp_path):
    enc = fixtures.make_encrypted_pdf(tmp_path / 'enc.pdf',
                                      user_password=runtime_pw('pw'), pages=1)
    out = str(tmp_path / 'clean.pdf')
    state = AppState()
    state.file_path = str(enc)
    state.source_password = runtime_pw('pw')
    request = {'input': str(enc), 'output': out}

    result = protect_handlers.sanitize_task(request, state)

    assert isinstance(result['report']['removed'], list)
    assert not PdfReader(out).is_encrypted


# ---------------------------------------------------------------------------
# Dialog validators / pure helpers
# ---------------------------------------------------------------------------

def _set_pw_values(**overrides):
    values = {
        '-INPUT-': '/tmp/in.pdf',
        '-USER-': runtime_pw('pw'),
        '-CONFIRM-': runtime_pw('pw'),
        '-OWNER-': '',
        '-ALLOW_PRINT-': True,
        '-ALLOW_COPY-': False,
        '-ALLOW_MODIFY-': True,
        '-OUTPUT-': '/tmp/out.pdf',
    }
    values.update(overrides)
    return values


def test_validate_set_passwords_ok():
    request, error = protect_dialogs.validate_set_passwords(_set_pw_values())
    assert error is None
    assert request == {
        'input': '/tmp/in.pdf',
        'output': '/tmp/out.pdf',
        'user_pw': runtime_pw('pw'),
        'owner_pw': None,
        'allow_print': True,
        'allow_copy': False,
        'allow_modify': True,
    }


def test_validate_set_passwords_mismatch():
    request, error = protect_dialogs.validate_set_passwords(
        _set_pw_values(**{'-CONFIRM-': runtime_pw('different')}))
    assert request is None
    assert error == 'Passwords do not match.'


def test_validate_set_passwords_requires_some_password():
    request, error = protect_dialogs.validate_set_passwords(
        _set_pw_values(**{'-USER-': '', '-CONFIRM-': '', '-OWNER-': ''}))
    assert request is None
    assert error == 'Enter a user password or an owner password.'


def test_validate_set_passwords_owner_only_is_allowed():
    request, error = protect_dialogs.validate_set_passwords(
        _set_pw_values(**{'-USER-': '', '-CONFIRM-': '', '-OWNER-': 'boss'}))
    assert error is None
    assert request['user_pw'] is None
    assert request['owner_pw'] == 'boss'


def test_validate_set_passwords_requires_paths():
    request, error = protect_dialogs.validate_set_passwords(
        _set_pw_values(**{'-INPUT-': '  '}))
    assert request is None and error == 'Please choose a source PDF.'
    request, error = protect_dialogs.validate_set_passwords(
        _set_pw_values(**{'-OUTPUT-': ''}))
    assert request is None and error == 'Please choose an output file.'


def test_validate_remove_password():
    request, error = protect_dialogs.validate_remove_password(
        {'-INPUT-': 'a.pdf', '-PASSWORD-': runtime_pw('pw'), '-OUTPUT-': 'b.pdf'})
    assert error is None
    assert request == {'input': 'a.pdf', 'output': 'b.pdf', 'password': runtime_pw('pw')}

    request, error = protect_dialogs.validate_remove_password(
        {'-INPUT-': '', '-PASSWORD-': runtime_pw('pw'), '-OUTPUT-': 'b.pdf'})
    assert request is None and error == 'Please choose a source PDF.'


def test_validate_sanitize_requires_a_selection():
    values = {'-INPUT-': 'a.pdf', '-OUTPUT-': 'b.pdf',
              '-METADATA-': False, '-ANNOTATIONS-': False,
              '-ATTACHMENTS-': False, '-JAVASCRIPT-': False}
    request, error = protect_dialogs.validate_sanitize(values)
    assert request is None
    assert error == 'Select at least one item to remove.'

    values['-METADATA-'] = True
    request, error = protect_dialogs.validate_sanitize(values)
    assert error is None
    assert request == {
        'input': 'a.pdf', 'output': 'b.pdf',
        'strip_metadata': True, 'strip_annotations': False,
        'strip_attachments': False, 'strip_javascript': False,
    }


def test_default_output_path():
    assert protect_dialogs.default_output_path(
        '/docs/report.pdf', '_protected') == '/docs/report_protected.pdf'
    assert protect_dialogs.default_output_path('', '_x') == ''
    assert protect_dialogs.default_output_path(None, '_x') == ''


def test_format_sanitize_report():
    text = protect_dialogs.format_sanitize_report(
        {'removed': ['XMP metadata stream', 'document JavaScript']})
    assert 'XMP metadata stream' in text
    assert 'document JavaScript' in text
    assert protect_dialogs.format_sanitize_report(
        {'removed': []}) == 'Nothing needed to be removed.'


# ---------------------------------------------------------------------------
# Handler registry shape
# ---------------------------------------------------------------------------

def test_protect_handlers_are_real_implementations():
    assert set(protect_handlers.HANDLERS) == {
        'MENU_SET_PASSWORDS', 'MENU_REMOVE_PASSWORD', 'MENU_SANITIZE'}
    assert protect_handlers.HANDLERS['MENU_SET_PASSWORDS'] is protect_handlers.set_passwords
    assert protect_handlers.HANDLERS['MENU_REMOVE_PASSWORD'] is protect_handlers.remove_password
    assert protect_handlers.HANDLERS['MENU_SANITIZE'] is protect_handlers.sanitize
