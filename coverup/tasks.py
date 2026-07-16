"""
Background task runner for CoverUP PDF.

Runs long operations on a daemon thread. Workers communicate with the GUI
exclusively through ``window.write_event_value`` tuple events:

    (key, 'PROGRESS') -> (pct, msg)
    (key, 'DONE')     -> result
    (key, 'ERROR')    -> traceback string

``main.py`` handles these events (progress bar update, error popup and
completion callbacks stored in ``state.task_callbacks``).

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import inspect
import threading
import traceback


def _accepts_progress_cb(fn):
    """Return True if fn has an explicit 'progress_cb' parameter."""
    try:
        return 'progress_cb' in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def run_task(window, fn, *args, key='-TASK-', **kwargs):
    """
    Run ``fn(*args, **kwargs)`` on a daemon thread.

    If ``fn`` declares a ``progress_cb`` parameter (and none was supplied),
    a callback emitting ``(key, 'PROGRESS')`` events is injected. On success
    a ``(key, 'DONE')`` event carries the return value; on failure a
    ``(key, 'ERROR')`` event carries the formatted traceback.

    Args:
        window: The sg.Window used for ``write_event_value``.
        fn: Callable to execute in the background.
        *args: Positional arguments for ``fn``.
        key: Event key prefix (default ``'-TASK-'``).
        **kwargs: Keyword arguments for ``fn``.

    Returns:
        threading.Thread: The started daemon thread.
    """
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
    thread.start()
    return thread
