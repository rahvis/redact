"""
Background task runner for WorkOnward Read.

Runs long operations on a daemon thread. Every invocation of
:func:`run_task` mints a UNIQUE tuple key ``('-TASK-', seq)`` (a monotonic
sequence counter, deterministic for tests) so concurrent tasks can never
collide on a shared key. Workers communicate with the GUI exclusively
through ``window.write_event_value`` tuple events:

    (key, 'PROGRESS') -> (pct, msg)
    (key, 'DONE')     -> result
    (key, 'ERROR')    -> traceback string

Completion callables (``on_done`` / ``on_error``) are registered here, in a
registry owned by this module, keyed by the minted task key. ``main.py``'s
task-event handling pops them via :func:`pop_callbacks` and invokes them as
``callback(window, state, payload)`` on the GUI thread.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import inspect
import itertools
import threading
import traceback

# Prefix of every minted task key; full keys are ('-TASK-', seq).
TASK_KEY = '-TASK-'

_TASK_EVENT_KINDS = ('PROGRESS', 'DONE', 'ERROR')

_key_counter = itertools.count(1)

# Task key -> (on_done, on_error). Owned by this module; main.py pops
# entries via pop_callbacks() when the DONE/ERROR event arrives.
_callbacks = {}
_callbacks_lock = threading.Lock()


def _accepts_progress_cb(fn):
    """Return True if fn has an explicit 'progress_cb' parameter."""
    try:
        return 'progress_cb' in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def next_task_key():
    """Mint the next unique task key ``('-TASK-', seq)``."""
    return (TASK_KEY, next(_key_counter))


def is_task_event(event):
    """True for the ``(key, kind)`` tuple events emitted by run_task workers.

    Task events are 2-tuples whose first element is a minted
    ``('-TASK-', seq)`` key. Other tuple events (e.g. the 3-tuple thumbnail
    click events) never match.
    """
    return (isinstance(event, tuple) and len(event) == 2
            and isinstance(event[0], tuple) and len(event[0]) == 2
            and event[0][0] == TASK_KEY
            and event[1] in _TASK_EVENT_KINDS)


def pop_callbacks(key):
    """Remove and return ``(on_done, on_error)`` registered for ``key``.

    Returns ``(None, None)`` when nothing was registered (fire-and-forget
    tasks, or the callbacks were already consumed).
    """
    with _callbacks_lock:
        return _callbacks.pop(key, (None, None))


def run_task(window, fn, *args, on_done=None, on_error=None, **kwargs):
    """
    Run ``fn(*args, **kwargs)`` on a daemon thread under a unique task key.

    If ``fn`` declares a ``progress_cb`` parameter (and none was supplied),
    a callback emitting ``(key, 'PROGRESS')`` events is injected. On success
    a ``(key, 'DONE')`` event carries the return value; on failure a
    ``(key, 'ERROR')`` event carries the formatted traceback.

    Args:
        window: The sg.Window used for ``write_event_value`` — pass the MAIN
            window so task events always reach the central task handler.
        fn: Callable to execute in the background.
        *args: Positional arguments for ``fn``.
        on_done: Optional ``on_done(window, state, result)`` invoked by
            main.py's task handling when the DONE event arrives.
        on_error: Optional ``on_error(window, state, traceback_str)`` cleanup
            hook invoked BEFORE the standard error popup on ERROR.
        **kwargs: Keyword arguments for ``fn``.

    Returns:
        threading.Thread: The started daemon thread. Its ``task_key``
        attribute carries the minted ``('-TASK-', seq)`` key.
    """
    key = next_task_key()
    if on_done is not None or on_error is not None:
        with _callbacks_lock:
            _callbacks[key] = (on_done, on_error)

    def progress_cb(pct, msg=''):
        window.write_event_value((key, 'PROGRESS'), (pct, msg))

    inject_cb = _accepts_progress_cb(fn) and 'progress_cb' not in kwargs

    def worker():
        try:
            if inject_cb:
                result = fn(*args, progress_cb=progress_cb, **kwargs)
            else:
                result = fn(*args, **kwargs)
            window.write_event_value((key, 'DONE'), result)
        except Exception:
            window.write_event_value((key, 'ERROR'), traceback.format_exc())

    thread = threading.Thread(target=worker, daemon=True)
    thread.task_key = key
    thread.start()
    return thread
