"""
Protect handlers for WorkOnward Read (passwords, sanitize).

The window-facing handlers are thin wrappers: they open the dialogs in
:mod:`workonward_read.dialogs.protect` and delegate to the module-level cores
(``set_passwords_task``, ``remove_password_with_retries``,
``sanitize_task``) which are headless-testable. Slow operations run on a
background thread via :func:`workonward_read.tasks.run_task`; completion
callbacks are stored in ``state.task_callbacks`` and invoked by main.py's
``('-TASK-', 'DONE')`` handling.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import os

from workonward_read import pdf_ops
from workonward_read.dialogs import protect as protect_dialogs
from workonward_read.dialogs.common import error_popup, info_popup
from workonward_read.i18n import _
from workonward_read.tasks import run_task


REMOVE_PASSWORD_MAX_ATTEMPTS = 3


# ---------------------------------------------------------------------------
# Headless cores
# ---------------------------------------------------------------------------

def source_password(request, state):
    """Return the password of the (possibly encrypted) source file.

    The password kept in ``state.source_password`` only applies when the
    request targets the currently loaded document.
    """
    if state.file_path and request.get('input') == state.file_path:
        return state.source_password
    return None


def set_passwords_task(request, state, progress_cb=None):
    """Encrypt ``request['input']`` to ``request['output']`` (AES-256).

    Runs on a background thread via ``run_task``. Returns the request so the
    completion callback can name the output file.
    """
    if progress_cb:
        progress_cb(10, _('Encrypting…'))
    pdf_ops.set_passwords(
        request['input'], request['output'],
        user_pw=request.get('user_pw'),
        owner_pw=request.get('owner_pw'),
        allow_print=request.get('allow_print', True),
        allow_copy=request.get('allow_copy', False),
        allow_modify=request.get('allow_modify', False),
        password=source_password(request, state),
    )
    if progress_cb:
        progress_cb(100, _('Done'))
    return request


def remove_password_with_retries(prompt_fn,
                                 max_attempts=REMOVE_PASSWORD_MAX_ATTEMPTS):
    """Drive the remove-password prompt/attempt loop (headless).

    Args:
        prompt_fn: ``prompt_fn(error_message, defaults) -> request | None``.
            Called with a translated inline error message ('' on the first
            attempt) and the previous request (so paths can be prefilled,
            password cleared).
        max_attempts: Maximum number of decryption attempts.

    Returns:
        (status, request): status is ``'ok'`` (request is the successful
        one), ``'cancelled'`` (request is None) or ``'failed'`` (request is
        the last attempted one; the password was wrong ``max_attempts``
        times).
    """
    error = ''
    request = None
    for _attempt in range(max_attempts):
        request = prompt_fn(error, request)
        if request is None:
            return 'cancelled', None
        try:
            pdf_ops.remove_password(
                request['input'], request['password'], request['output'])
            return 'ok', request
        except ValueError:
            error = _('Wrong password. Please try again.')
    return 'failed', request


def sanitize_task(request, state, progress_cb=None):
    """Sanitize ``request['input']`` to ``request['output']``.

    Runs on a background thread via ``run_task``. Returns
    ``{'request': ..., 'report': {'removed': [...]}}`` for the completion
    callback (report propagation).
    """
    if progress_cb:
        progress_cb(10, _('Sanitizing…'))
    report = pdf_ops.sanitize(
        request['input'], request['output'],
        password=source_password(request, state),
        strip_metadata=request.get('strip_metadata', True),
        strip_annotations=request.get('strip_annotations', True),
        strip_attachments=request.get('strip_attachments', True),
        strip_javascript=request.get('strip_javascript', True),
    )
    if progress_cb:
        progress_cb(100, _('Done'))
    return {'request': request, 'report': report}


# ---------------------------------------------------------------------------
# Completion callbacks (main.py invokes these on ('-TASK-', 'DONE'))
# ---------------------------------------------------------------------------

def _after_set_passwords(window, state, result):
    filename = os.path.basename(result['output'])
    info_popup(window, '\n'.join([
        _('Encrypted PDF saved as {filename}.', filename=filename),
        '',
        _('Keep your password safe — it cannot be recovered.'),
    ]))


def _after_sanitize(window, state, result):
    protect_dialogs.show_sanitize_report(window, result['report'])


# ---------------------------------------------------------------------------
# Window-facing handlers
# ---------------------------------------------------------------------------

def set_passwords(window, state):
    """MENU_SET_PASSWORDS: dialog, then encrypt on a background task."""
    request = protect_dialogs.set_passwords_dialog(window, state)
    if request is None:
        return
    state.busy = True
    state.task_callbacks['-TASK-'] = _after_set_passwords
    run_task(window, set_passwords_task, request, state)


def remove_password(window, state):
    """MENU_REMOVE_PASSWORD: prompt/attempt loop (up to 3 attempts)."""
    def prompt(error, defaults):
        return protect_dialogs.remove_password_dialog(
            window, state, error=error, defaults=defaults)

    try:
        status, request = remove_password_with_retries(prompt)
    except Exception as exc:
        error_popup(window, _('error_occurred'), exc)
        return

    if status == 'ok':
        filename = os.path.basename(request['output'])
        info_popup(window, _('Decrypted PDF saved as {filename}.',
                             filename=filename))
    elif status == 'failed':
        error_popup(window, _('Could not decrypt the file: the password was '
                              'wrong {count} times.',
                              count=REMOVE_PASSWORD_MAX_ATTEMPTS))


def sanitize(window, state):
    """MENU_SANITIZE: dialog, then sanitize on a background task."""
    request = protect_dialogs.sanitize_dialog(window, state)
    if request is None:
        return
    state.busy = True
    state.task_callbacks['-TASK-'] = _after_sanitize
    run_task(window, sanitize_task, request, state)


HANDLERS = {
    'MENU_SET_PASSWORDS': set_passwords,
    'MENU_REMOVE_PASSWORD': remove_password,
    'MENU_SANITIZE': sanitize,
}
