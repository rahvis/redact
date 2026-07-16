"""Tests for the menu spec / handler registry / toolbar mapping / tasks.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import time

from workonward_read import menu, tasks
from workonward_read.handlers import HANDLERS, TOOLBAR_HANDLERS


# Full key set from docs/dev-architecture.md (the binding contract).
CONTRACT_MENU_KEYS = {
    # File
    'MENU_OPEN', 'MENU_SAVE_REDACTED', 'MENU_EXPORT_PAGE', 'MENU_SAVE_ORGANIZED',
    'MENU_IMAGES_TO_PDF', 'MENU_CONVERT_IMAGES', 'MENU_CONVERT_TEXT',
    'MENU_CONVERT_WORD', 'MENU_CONVERT_HTML', 'MENU_PRINT', 'MENU_EXIT',
    # Edit
    'MENU_UNDO', 'MENU_REDO', 'MENU_DELETE_ALL', 'MENU_SEARCH',
    # View
    'MENU_ZOOM_IN', 'MENU_ZOOM_OUT', 'MENU_PREV_PAGE', 'MENU_NEXT_PAGE',
    'MENU_THUMBNAILS',
    # Tools
    'MENU_MERGE', 'MENU_SPLIT', 'MENU_INSERT_PAGES', 'MENU_DELETE_PAGES',
    'MENU_REORDER_PAGES', 'MENU_ROTATE_PAGES', 'MENU_EXTRACT_PAGES', 'MENU_CROP',
    'MENU_WATERMARK', 'MENU_HEADER_FOOTER', 'MENU_COMPRESS', 'MENU_OCR',
    'MENU_COMPARE', 'MENU_BATCH', 'MENU_PROPERTIES',
    # Protect
    'MENU_SET_PASSWORDS', 'MENU_REMOVE_PASSWORD', 'MENU_SANITIZE',
    # Sign
    'MENU_FILL_SIGN', 'MENU_CERT_SIGN', 'MENU_VALIDATE_SIGS', 'MENU_FILL_FORM',
    # Help
    'MENU_ABOUT',
}

TOOLBAR_EVENTS = [
    'LOAD_PDF', 'SAVE_PDF', 'EXPORT_PAGE', 'UNDO', 'EDIT_MODE', 'DELETE_ALL',
    'CHANGE_COLOR', 'TOGGLE_QUALITY', 'BACK', 'FORTH', 'ZOOM_IN', 'ZOOM_OUT',
    'ABOUT',
]


def test_menu_spec_contains_exactly_the_contract_keys():
    keys = menu.menu_keys()
    assert set(keys) == CONTRACT_MENU_KEYS


def test_menu_spec_has_no_duplicate_keys():
    keys = menu.menu_keys()
    assert len(keys) == len(set(keys))


def test_every_menu_key_has_a_handler():
    missing = [key for key in menu.menu_keys() if key not in HANDLERS]
    assert not missing, f'menu keys without handler: {missing}'


def test_all_handlers_are_callable():
    for key, handler in HANDLERS.items():
        assert callable(handler), f'{key} handler is not callable'


def test_event_normalization_matches_dispatch():
    """Menu items are '<label>::<KEY>'; rsplit('::', 1)[-1] must yield the key."""
    for item in _iter_items(menu.build_menu()):
        if '::' in item:
            key = item.rsplit('::', 1)[-1]
            assert key in HANDLERS


def _iter_items(entries):
    for entry in entries:
        if isinstance(entry, list):
            yield from _iter_items(entry)
        elif isinstance(entry, str):
            yield entry


def test_every_toolbar_event_maps_to_a_handler():
    for event in TOOLBAR_EVENTS:
        assert event in TOOLBAR_HANDLERS, f'toolbar event {event} not mapped'
        assert callable(TOOLBAR_HANDLERS[event])


def test_toolbar_shares_menu_handler_functions():
    assert TOOLBAR_HANDLERS['LOAD_PDF'] is HANDLERS['MENU_OPEN']
    assert TOOLBAR_HANDLERS['SAVE_PDF'] is HANDLERS['MENU_SAVE_REDACTED']
    assert TOOLBAR_HANDLERS['EXPORT_PAGE'] is HANDLERS['MENU_EXPORT_PAGE']
    assert TOOLBAR_HANDLERS['UNDO'] is HANDLERS['MENU_UNDO']
    assert TOOLBAR_HANDLERS['DELETE_ALL'] is HANDLERS['MENU_DELETE_ALL']
    assert TOOLBAR_HANDLERS['ZOOM_IN'] is HANDLERS['MENU_ZOOM_IN']
    assert TOOLBAR_HANDLERS['ZOOM_OUT'] is HANDLERS['MENU_ZOOM_OUT']
    assert TOOLBAR_HANDLERS['BACK'] is HANDLERS['MENU_PREV_PAGE']
    assert TOOLBAR_HANDLERS['FORTH'] is HANDLERS['MENU_NEXT_PAGE']
    assert TOOLBAR_HANDLERS['ABOUT'] is HANDLERS['MENU_ABOUT']


# --- tasks.run_task ---------------------------------------------------------

class FakeWindow:
    """Collects write_event_value calls like an sg.Window would."""

    def __init__(self):
        self.events = []

    def write_event_value(self, key, value):
        self.events.append((key, value))


def test_run_task_reports_progress_and_done():
    window = FakeWindow()

    def job(value, progress_cb=None):
        progress_cb(50, 'halfway')
        return value * 2

    thread = tasks.run_task(window, job, 21)
    thread.join(timeout=5)
    assert not thread.is_alive()

    key = thread.task_key
    assert tasks.is_task_event((key, 'DONE'))
    assert ((key, 'PROGRESS'), (50, 'halfway')) in window.events
    assert ((key, 'DONE'), 42) in window.events


def test_run_task_mints_unique_keys_per_invocation():
    window = FakeWindow()

    def job():
        return 'ok'

    thread_a = tasks.run_task(window, job)
    thread_b = tasks.run_task(window, job)
    thread_a.join(timeout=5)
    thread_b.join(timeout=5)

    assert thread_a.task_key != thread_b.task_key
    assert thread_a.task_key[0] == tasks.TASK_KEY
    assert thread_b.task_key[0] == tasks.TASK_KEY
    assert ((thread_a.task_key, 'DONE'), 'ok') in window.events
    assert ((thread_b.task_key, 'DONE'), 'ok') in window.events


def test_run_task_reports_error_traceback():
    window = FakeWindow()

    def bad_job():
        raise RuntimeError('kaboom')

    thread = tasks.run_task(window, bad_job)
    thread.join(timeout=5)

    error_events = [e for e in window.events
                    if e[0] == (thread.task_key, 'ERROR')]
    assert len(error_events) == 1
    assert 'kaboom' in error_events[0][1]
    assert 'RuntimeError' in error_events[0][1]


def test_run_task_without_progress_cb_parameter():
    window = FakeWindow()

    def plain_job(a, b):
        return a + b

    thread = tasks.run_task(window, plain_job, 1, b=2)
    thread.join(timeout=5)
    assert ((thread.task_key, 'DONE'), 3) in window.events


def test_run_task_is_daemon_and_nonblocking():
    window = FakeWindow()
    started = time.monotonic()

    def slow_job():
        time.sleep(0.2)
        return 'done'

    thread = tasks.run_task(window, slow_job)
    assert thread.daemon
    assert time.monotonic() - started < 0.2  # returned before job finished
    thread.join(timeout=5)
    assert ((thread.task_key, 'DONE'), 'done') in window.events


def test_run_task_registers_and_pops_callbacks():
    window = FakeWindow()
    on_done = lambda w, s, r: None          # noqa: E731
    on_error = lambda w, s, tb: None        # noqa: E731

    thread = tasks.run_task(window, lambda: 1,
                            on_done=on_done, on_error=on_error)
    thread.join(timeout=5)

    assert tasks.pop_callbacks(thread.task_key) == (on_done, on_error)
    # Second pop: already consumed.
    assert tasks.pop_callbacks(thread.task_key) == (None, None)


def test_is_task_event_shapes():
    key = tasks.next_task_key()
    assert tasks.is_task_event((key, 'DONE'))
    assert tasks.is_task_event((key, 'PROGRESS'))
    assert tasks.is_task_event((key, 'ERROR'))
    assert not tasks.is_task_event((key, 'OTHER'))
    assert not tasks.is_task_event(('-TASK-', 'DONE'))       # legacy shape
    assert not tasks.is_task_event(('-THUMB-', 1, 'CLICK'))  # 3-tuple event
    assert not tasks.is_task_event('MENU_OPEN')
    assert not tasks.is_task_event(None)
