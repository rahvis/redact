"""Regression tests for the concurrency/orchestration fixes.

1. Unique per-invocation task keys: overlapping background tasks deliver
   DONE payloads to their OWN callbacks (previously a single '-TASK-'
   registry key was overwritten by the second task), plus the doc busy-set
   guard for document-mutating vs document-consuming operations.
2. OCR-current-doc holds the doc lock for its whole task duration; the
   guarded page-op entry (delete_pages_core path) refuses meanwhile.
3. Serialized pdfium access via workonward_read.pdfium_io (canonical
   password error + concurrent text-extract/render without crashing).
4. The compare-results text diff runs as a background task with its button
   disabled while running.
5. Aux-window registry routing used by main.py's read_all_windows loop.

Licensed under GPL-3.0
(c) 2026 WorkOnward Read contributors
"""

import os
import threading

import pytest

import fixtures
from fixtures import runtime_pw
from workonward_read import compare, convert, main, pdfium_io, search, tasks
from workonward_read.dialogs import common as dialogs_common
from workonward_read.handlers import convert as convert_handlers
from workonward_read.handlers import file as file_handlers
from workonward_read.handlers import organize
from workonward_read.handlers import review
from workonward_read.state import AppState


# ---------------------------------------------------------------------------
# Shims
# ---------------------------------------------------------------------------

class FakeElement:
    """Records update() calls like an sg element would."""

    def __init__(self):
        self.updates = []

    def update(self, *args, **kwargs):
        self.updates.append((args, kwargs))


class FakeWindow:
    """Enough of sg.Window for _handle_task_event and aux-window handlers."""

    def __init__(self):
        self.events = []
        self.elements = {}
        self.closed = False
        self.raised = 0

    def __getitem__(self, key):
        return self.elements.setdefault(key, FakeElement())

    def write_event_value(self, key, value):
        self.events.append((key, value))

    def was_closed(self):
        return self.closed

    def close(self):
        self.closed = True

    def bring_to_front(self):
        self.raised += 1


def _feed_task_events(window, state):
    """Feed every collected task event through main.py's task handler."""
    for event, payload in list(window.events):
        if tasks.is_task_event(event):
            main._handle_task_event(window, state, event, {event: payload})


# ---------------------------------------------------------------------------
# Fix 1: unique task keys — no callback collision between overlapping tasks
# ---------------------------------------------------------------------------

def test_overlapping_tasks_deliver_results_to_own_callbacks():
    """Two tasks overlap; the one started FIRST finishes LAST. With the old
    shared '-TASK-' key the second registration overwrote the first and the
    first DONE event delivered the wrong payload to the wrong callback."""
    window = FakeWindow()
    state = AppState()
    got = {}

    release_a = threading.Event()
    release_b = threading.Event()

    def job_a():
        assert release_a.wait(5)
        return 'result-a'

    def job_b():
        assert release_b.wait(5)
        return 'result-b'

    thread_a = tasks.run_task(window, job_a,
                              on_done=lambda w, s, r: got.__setitem__('a', r))
    thread_b = tasks.run_task(window, job_b,
                              on_done=lambda w, s, r: got.__setitem__('b', r))

    release_b.set()
    thread_b.join(timeout=5)
    release_a.set()
    thread_a.join(timeout=5)
    assert not thread_a.is_alive() and not thread_b.is_alive()

    # Drive main.py's task handling with the events in arrival order.
    _feed_task_events(window, state)

    assert got == {'a': 'result-a', 'b': 'result-b'}


def test_overlapping_task_error_reaches_own_error_callback(monkeypatch):
    """DONE of task A and ERROR of task B interleave; each reaches its own
    callback and the standard error popup still fires (after cleanup)."""
    window = FakeWindow()
    state = AppState()
    order = []
    monkeypatch.setattr(main, 'error_popup',
                        lambda w, m, d=None: order.append('popup'))

    ok = tasks.run_task(window, lambda: 'fine',
                        on_done=lambda w, s, r: order.append(f'done:{r}'))
    bad = tasks.run_task(window, lambda: 1 / 0,
                         on_error=lambda w, s, tb: order.append('cleanup'))
    ok.join(timeout=5)
    bad.join(timeout=5)

    _feed_task_events(window, state)

    assert 'done:fine' in order
    # cleanup hook runs BEFORE the blocking error popup
    assert order.index('cleanup') < order.index('popup')


def test_progress_events_update_shared_bar_last_wins():
    window = FakeWindow()
    state = AppState()

    key_a = tasks.next_task_key()
    key_b = tasks.next_task_key()
    for event, payload in (((key_a, 'PROGRESS'), (10, '')),
                           ((key_b, 'PROGRESS'), (80, '')),
                           ((key_a, 'PROGRESS'), (20, ''))):
        main._handle_task_event(window, state, event, {event: payload})

    counts = [kw['current_count']
              for _a, kw in window['-PROGRESS-'].updates]
    assert counts == [10, 80, 20]  # shared bar: last reporter wins


# ---------------------------------------------------------------------------
# Fix 1: doc busy-set guard (state.doc_lock)
# ---------------------------------------------------------------------------

def test_password_for_matches_loaded_document_only(tmp_path):
    doc = tmp_path / 'loaded.pdf'
    doc.write_bytes(b'%PDF-1.4')
    state = AppState()
    state.file_path = str(doc)
    state.source_password = runtime_pw('secret')

    assert state.password_for(str(doc)) == runtime_pw('secret')
    # A different (relative) spelling of the same path still matches.
    rel = os.path.relpath(str(doc))
    assert state.password_for(rel) == runtime_pw('secret')
    assert state.password_for(str(tmp_path / 'other.pdf')) is None
    assert state.password_for('') is None
    assert state.password_for(None) is None

    state.file_path = None
    assert state.password_for(str(doc)) is None


def test_require_document_free_guard(monkeypatch):
    window = FakeWindow()
    state = AppState()
    popups = []
    monkeypatch.setattr(dialogs_common, 'info_popup',
                        lambda w, m: popups.append(m))

    assert dialogs_common.require_document_free(window, state) is True
    assert not popups

    state.acquire_doc('ocr-current-document')
    assert dialogs_common.require_document_free(window, state) is False
    assert popups and 'Another operation is using the document' in popups[0]

    state.release_doc('ocr-current-document')
    state.release_doc('ocr-current-document')  # double release tolerated
    assert dialogs_common.require_document_free(window, state) is True


def test_doc_mutating_page_op_refused_while_doc_consuming_task_active(
        monkeypatch):
    window = FakeWindow()
    state = AppState()
    state.images = [object(), object()]
    popups = []
    monkeypatch.setattr(dialogs_common, 'info_popup',
                        lambda w, m: popups.append(m))

    dialog_calls = []
    core_calls = []

    def dialog_fn():
        dialog_calls.append(1)
        return {'scope': 'current'}

    def core_fn(st, request):
        core_calls.append(request)

    state.acquire_doc('ocr-current-document')
    organize._loaded_doc_op(window, state, dialog_fn, core_fn)
    assert not dialog_calls and not core_calls
    assert popups and 'Another operation is using the document' in popups[0]

    # Released: the op runs normally.
    state.release_doc('ocr-current-document')
    from workonward_read.handlers import view as view_handlers
    monkeypatch.setattr(view_handlers, 'flip_to_page',
                        lambda w, images, page, st=None: 0)
    organize._loaded_doc_op(window, state, dialog_fn, core_fn)
    assert core_calls == [{'scope': 'current'}]


def test_save_and_export_refused_while_doc_consuming_task_active(monkeypatch):
    window = FakeWindow()
    state = AppState()
    state.images = [object()]
    popups = []
    monkeypatch.setattr(dialogs_common, 'info_popup',
                        lambda w, m: popups.append(m))
    asked = []
    monkeypatch.setattr(file_handlers.sg, 'popup_get_file',
                        lambda *a, **k: asked.append(1) or None)

    state.acquire_doc('ocr-current-document')
    file_handlers.save_redacted(window, state)
    file_handlers.export_page(window, state)
    assert len(popups) == 2
    assert not asked, 'save dialog must not open while the doc is in use'


def test_file_to_file_tools_stay_allowed_while_doc_locked(monkeypatch):
    """merge (a file->file tool) still starts while the doc lock is held."""
    window = FakeWindow()
    state = AppState()
    state.acquire_doc('ocr-current-document')

    monkeypatch.setattr(organize.dialogs, 'merge_dialog',
                        lambda w: {'inputs': ['a.pdf', 'b.pdf'],
                                   'output': 'out.pdf'})
    started = []
    monkeypatch.setattr(organize.tasks, 'run_task',
                        lambda w, fn, *a, **k: started.append(fn))

    organize.merge(window, state)
    assert started == [organize.merge_core]


# ---------------------------------------------------------------------------
# Fix 2: OCR-current-doc holds the doc lock for its whole task duration
# ---------------------------------------------------------------------------

def _wire_ocr_handler(monkeypatch, request):
    """Monkeypatch the OCR handler's collaborators; capture run_task args."""
    captured = {}
    monkeypatch.setattr(convert_handlers.ocr, 'find_tesseract',
                        lambda *a, **k: '/usr/bin/tesseract')
    monkeypatch.setattr(convert_handlers, 'get_saved_tesseract_path',
                        lambda: None)
    monkeypatch.setattr(convert_handlers.ocr, 'available_languages',
                        lambda p: ['eng'])
    monkeypatch.setattr(convert_handlers.convert_dialogs, 'ocr_dialog',
                        lambda w, s, langs: dict(request))
    monkeypatch.setattr(convert_handlers, 'info_popup',
                        lambda w, m: captured.setdefault('popups', []).append(m))

    def fake_run_task(window, fn, *args, on_done=None, on_error=None, **kw):
        captured.update(fn=fn, args=args, on_done=on_done, on_error=on_error)
        return None

    monkeypatch.setattr(convert_handlers, 'run_task', fake_run_task)
    return captured


def test_ocr_current_doc_holds_doc_lock_and_blocks_delete(monkeypatch,
                                                          tmp_path):
    window = FakeWindow()
    state = AppState()
    state.images = [object(), object()]
    captured = _wire_ocr_handler(monkeypatch, {
        'use_loaded': True, 'input_path': None, 'lang': 'eng',
        'output': str(tmp_path / 'ocr.pdf')})

    convert_handlers.ocr_document(window, state)
    assert convert_handlers.OCR_DOC_REASON in state.doc_lock
    assert captured['fn'] is convert_handlers.run_ocr

    # While the OCR task holds the lock, the guarded delete entry refuses:
    # delete_pages_core is never reached.
    popups = []
    monkeypatch.setattr(dialogs_common, 'info_popup',
                        lambda w, m: popups.append(m))
    organize._loaded_doc_op(
        window, state,
        lambda: {'scope': 'current'},
        organize.delete_pages_core)
    assert len(state.images) == 2, 'pages must not be touched mid-OCR'
    assert state.journal is None
    assert popups and 'Another operation is using the document' in popups[0]

    # DONE releases the lock; the op would be allowed again.
    captured['on_done'](window, state, str(tmp_path / 'ocr.pdf'))
    assert not state.doc_lock


def test_ocr_current_doc_releases_lock_on_error(monkeypatch, tmp_path):
    window = FakeWindow()
    state = AppState()
    state.images = [object()]
    captured = _wire_ocr_handler(monkeypatch, {
        'use_loaded': True, 'input_path': None, 'lang': 'eng',
        'output': str(tmp_path / 'ocr.pdf')})

    convert_handlers.ocr_document(window, state)
    assert convert_handlers.OCR_DOC_REASON in state.doc_lock
    captured['on_error'](window, state, 'Traceback: boom')
    assert not state.doc_lock


def test_ocr_refused_while_doc_already_locked(monkeypatch, tmp_path):
    window = FakeWindow()
    state = AppState()
    state.images = [object()]
    captured = _wire_ocr_handler(monkeypatch, {
        'use_loaded': True, 'input_path': None, 'lang': 'eng',
        'output': str(tmp_path / 'ocr.pdf')})
    popups = []
    monkeypatch.setattr(dialogs_common, 'info_popup',
                        lambda w, m: popups.append(m))

    state.acquire_doc('save-organized')
    convert_handlers.ocr_document(window, state)
    assert 'fn' not in captured, 'no task while another one uses the doc'
    assert popups


def test_ocr_picked_file_does_not_take_doc_lock(monkeypatch, tmp_path):
    window = FakeWindow()
    state = AppState()
    captured = _wire_ocr_handler(monkeypatch, {
        'use_loaded': False, 'input_path': str(tmp_path / 'scan.pdf'),
        'lang': 'eng', 'output': str(tmp_path / 'ocr.pdf')})

    convert_handlers.ocr_document(window, state)
    assert captured['fn'] is convert_handlers.run_ocr
    assert not state.doc_lock


# ---------------------------------------------------------------------------
# Fix 3: pdfium_io — canonical open helper + serialized pdfium access
# ---------------------------------------------------------------------------

def test_pdfium_io_open_pdf_wrong_password(tmp_path):
    enc = fixtures.make_encrypted_pdf(tmp_path / 'enc.pdf',
                                      user_password=runtime_pw('pw'), pages=1)
    with pytest.raises(ValueError, match='password') as err:
        pdfium_io.open_pdf(str(enc))
    assert str(err.value) == pdfium_io.PASSWORD_ERROR

    with pytest.raises(ValueError, match='password'):
        pdfium_io.open_pdf(str(enc), password=runtime_pw('wrong'))

    with pdfium_io.pdfium_session(str(enc), runtime_pw('pw')) as doc:
        assert len(doc) == 1


def test_pdfium_io_open_pdf_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        pdfium_io.open_pdf(str(tmp_path / 'missing.pdf'))
    with pytest.raises(FileNotFoundError):
        pdfium_io.open_pdf('')


def test_canonical_password_message_shared_by_refactored_modules(tmp_path):
    enc = fixtures.make_encrypted_pdf(tmp_path / 'enc.pdf',
                                      user_password=runtime_pw('pw'), pages=1)
    attempts = (
        lambda: search.page_count(str(enc)),
        lambda: search.search_document(str(enc), 'x'),
        lambda: compare.text_diff(str(enc), str(enc)),
        lambda: compare.compare_pdfs(str(enc), str(enc)),
        lambda: convert.pdf_to_text(str(enc), str(tmp_path / 'x.txt')),
    )
    for attempt in attempts:
        with pytest.raises(ValueError) as err:
            attempt()
        assert str(err.value) == pdfium_io.PASSWORD_ERROR


def test_render_page_to_pil_variants(tmp_path):
    pdf_path = fixtures.make_pdf(tmp_path / 'doc.pdf', pages=1)

    # From a path (opened/closed internally), grayscale guaranteed 'L'.
    gray = pdfium_io.render_page_to_pil(str(pdf_path), 0, scale=0.5,
                                        grayscale=True)
    assert gray.mode == 'L'
    gray.close()

    # From an open doc, JPEG round-trip detaches from pdfium buffers.
    doc = pdfium_io.open_pdf(str(pdf_path))
    try:
        image = pdfium_io.render_page_to_pil(doc, 0, scale=0.5,
                                             jpeg_roundtrip=True)
        assert image.size[0] > 0 and image.size[1] > 0
        image.load()  # fully loaded, independent of the (closing) doc
        image.close()
        assert pdfium_io.get_page_size(doc, 0)[0] > 0
        assert pdfium_io.page_count(doc) == 1
    finally:
        pdfium_io.close_pdf(doc)


def test_concurrent_text_extract_and_render_same_file(tmp_path):
    """Two threads drive pdfium on the same file at once (text extraction
    vs page rendering) — with PDFIUM_LOCK serialization this completes
    without crashing or raising."""
    pdf_path = str(fixtures.make_pdf(tmp_path / 'doc.pdf', pages=2,
                                     texts=['alpha page', 'beta page']))
    errors = []

    def extract_loop():
        try:
            for _ in range(3):
                diff = compare.text_diff(pdf_path, pdf_path)
                assert diff == []
        except Exception as exc:  # pragma: no cover - the regression itself
            errors.append(exc)

    def render_loop():
        try:
            for run in range(3):
                out_dir = str(tmp_path / f'imgs-{run}')
                written = convert.pdf_to_images(pdf_path, out_dir, dpi=50)
                assert len(written) == 2
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=extract_loop),
               threading.Thread(target=render_loop)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive()
    assert errors == []


# ---------------------------------------------------------------------------
# Fix 4: compare-results text diff runs as a background task
# ---------------------------------------------------------------------------

def _results_fixture(monkeypatch):
    main_window = FakeWindow()
    results_window = FakeWindow()
    state = AppState()
    request = {'path_a': 'a.pdf', 'path_b': 'b.pdf'}
    started = []

    def fake_run_task(window, fn, *args, on_done=None, on_error=None, **kw):
        started.append({'window': window, 'fn': fn, 'args': args,
                        'on_done': on_done, 'on_error': on_error})
        return None

    monkeypatch.setattr(review, 'run_task', fake_run_task)
    handler = review._make_results_handler(
        main_window, state, request, object(), results_window)
    return main_window, results_window, state, request, handler, started


def test_text_diff_runs_via_task_with_button_disabled(monkeypatch):
    (main_window, results_window, state, request,
     handler, started) = _results_fixture(monkeypatch)

    keep = handler(results_window, state, '-TEXTDIFF-', {})
    assert keep is True
    assert results_window['-TEXTDIFF-'].updates[-1] == ((), {'disabled': True})
    task = started[0]
    assert task['window'] is main_window          # events go to the MAIN window
    assert task['fn'] is review.run_text_diff
    assert task['args'] == (request,)

    # A second click while the diff runs is ignored.
    handler(results_window, state, '-TEXTDIFF-', {})
    assert len(started) == 1

    # Result is shown from the DONE callback; button re-enabled first.
    popups = []
    monkeypatch.setattr(review.review_dialogs, 'show_text_diff_popup',
                        lambda w, text: popups.append(text))
    task['on_done'](main_window, state, ['-old line', '+new line'])
    assert popups == ['-old line\n+new line']
    assert results_window['-TEXTDIFF-'].updates[-1] == ((), {'disabled': False})

    # After completion the button works again.
    handler(results_window, state, '-TEXTDIFF-', {})
    assert len(started) == 2


def test_text_diff_error_reenables_button(monkeypatch):
    (main_window, results_window, state, _request,
     handler, started) = _results_fixture(monkeypatch)

    handler(results_window, state, '-TEXTDIFF-', {})
    started[0]['on_error'](main_window, state, 'Traceback: boom')
    assert results_window['-TEXTDIFF-'].updates[-1] == ((), {'disabled': False})

    # Closed window: DONE callback must not try to show the popup.
    handler(results_window, state, '-TEXTDIFF-', {})
    results_window.close()
    shown = []
    monkeypatch.setattr(review.review_dialogs, 'show_text_diff_popup',
                        lambda w, text: shown.append(text))
    started[1]['on_done'](main_window, state, ['+x'])
    assert shown == []


def test_export_runs_via_task_with_button_disabled(monkeypatch):
    (main_window, results_window, state, request,
     handler, started) = _results_fixture(monkeypatch)
    monkeypatch.setattr(review.sg, 'popup_get_file',
                        lambda *a, **k: '/tmp/report.pdf')

    handler(results_window, state, '-EXPORT-', {})
    assert results_window['-EXPORT-'].updates[-1] == ((), {'disabled': True})
    task = started[0]
    assert task['window'] is main_window
    assert task['fn'] is review.export_report
    assert task['args'][-1] == '/tmp/report.pdf'

    popups = []
    monkeypatch.setattr(review, 'info_popup', lambda w, m: popups.append(m))
    task['on_done'](main_window, state, '/tmp/report.pdf')
    assert popups == ['Report saved: report.pdf']
    assert results_window['-EXPORT-'].updates[-1] == ((), {'disabled': False})


# ---------------------------------------------------------------------------
# Fix 5: aux-window registry routing
# ---------------------------------------------------------------------------

def test_route_aux_window_event_keep_and_close():
    state = AppState()
    aux = FakeWindow()
    seen = []

    def handler(win, st, event, values):
        seen.append(event)
        return event != '-CLOSE-'

    state.aux_windows[aux] = handler

    assert main._route_aux_window_event(state, aux, '-FIND-', {}) is True
    assert aux in state.aux_windows and not aux.closed

    assert main._route_aux_window_event(state, aux, '-CLOSE-', {}) is False
    assert aux not in state.aux_windows
    assert aux.closed
    assert seen == ['-FIND-', '-CLOSE-']


def test_route_aux_window_event_unregistered_window_is_closed():
    state = AppState()
    aux = FakeWindow()
    assert main._route_aux_window_event(state, aux, None, None) is False
    assert aux.closed


def test_search_registers_finder_and_routes_via_callbacks(monkeypatch,
                                                          tmp_path):
    state = AppState()
    state.file_path = str(tmp_path / 'doc.pdf')
    state.images = [object()]
    main_window = FakeWindow()
    finder = FakeWindow()

    monkeypatch.setattr(review.review_dialogs, 'open_search_window',
                        lambda w: finder)
    started = []

    def fake_run_task(window, fn, *args, on_done=None, on_error=None, **kw):
        started.append({'window': window, 'fn': fn, 'args': args,
                        'on_done': on_done, 'on_error': on_error})
        return None

    monkeypatch.setattr(review, 'run_task', fake_run_task)

    review.search(main_window, state)
    assert finder in state.aux_windows
    handler = state.aux_windows[finder]
    assert getattr(handler, 'aux_kind', None) == 'search-finder'

    # A second MENU_SEARCH focuses the existing finder, no new window.
    review.search(main_window, state)
    assert list(state.aux_windows) == [finder]
    assert finder.raised == 1

    # -FIND- disables the button and starts the search on the MAIN window.
    keep = handler(finder, state, '-FIND-',
                   {'-TERM-': 'needle', '-CASE-': False})
    assert keep is True
    assert finder['-FIND-'].updates[-1] == ((), {'disabled': True})
    task = started[0]
    assert task['window'] is main_window
    assert task['fn'] is review.perform_search

    # A second -FIND- while searching is ignored.
    handler(finder, state, '-FIND-', {'-TERM-': 'needle'})
    assert len(started) == 1

    # DONE with no hits: counter reset, button re-enabled, popup shown.
    popups = []
    monkeypatch.setattr(review, 'info_popup', lambda w, m: popups.append(m))
    task['on_done'](main_window, state, [])
    assert finder['-FIND-'].updates[-1] == ((), {'disabled': False})
    assert popups == ['No matches found.']

    # Close: handler returns False (main loop then closes + unregisters).
    assert handler(finder, state, '-CLOSE-', {}) is False


def test_search_done_callback_ignores_closed_finder(monkeypatch, tmp_path):
    state = AppState()
    state.file_path = str(tmp_path / 'doc.pdf')
    state.images = [object()]
    main_window = FakeWindow()
    finder = FakeWindow()
    monkeypatch.setattr(review.review_dialogs, 'open_search_window',
                        lambda w: finder)
    started = []
    monkeypatch.setattr(
        review, 'run_task',
        lambda window, fn, *args, on_done=None, on_error=None, **kw:
        started.append((on_done, on_error)))

    review.search(main_window, state)
    handler = state.aux_windows[finder]
    handler(finder, state, '-FIND-', {'-TERM-': 'needle'})

    # The finder is closed before the search finishes; the late DONE and
    # ERROR callbacks must not touch the dead window.
    finder.close()
    updates_before = len(finder.elements.get('-FIND-', FakeElement()).updates)
    on_done, on_error = started[0]
    on_done(main_window, state, [])
    on_error(main_window, state, 'Traceback: boom')
    updates_after = len(finder.elements.get('-FIND-', FakeElement()).updates)
    assert updates_after == updates_before
