"""Work-session restore tests: the restored journal must be REPLAYED on the
freshly rendered original pages before the (post-op) annotations are
attached, the restore guard must compare the workfile's page count against
the POST-journal page count, and restored annotations must be reachable via
Undo/Redo (fixes for handlers/file.py + document_loader.py).

The full ``load_path`` flow is exercised with a FakeWindow/FakeGraph shim —
no real sg.Window is ever created.

License: GPL-3.0
(c) 2026 CoverUP contributors
"""

import pytest
import fixtures

from workonward_read import document_loader, page_render, pdf_ops
from workonward_read.annotations import Annotation, new_id, to_dict
from workonward_read.handlers import edit as edit_handlers
from workonward_read.handlers import file as file_handlers
from workonward_read.pdf_ops import PageOpsJournal
from workonward_read.state import AppState
from workonward_read.workfile import WorkfileManager, serialize_journal

PX_PER_PT = 200.0 / 72.0


def redact_ann(p1, p2):
    return Annotation(id=new_id(), kind='redact',
                      props={'p1': list(p1), 'p2': list(p2), 'fill': 'black'})


# ---------------------------------------------------------------------------
# Restore guard: workfile 'pages' is the POST-journal page count
# ---------------------------------------------------------------------------

def test_restored_session_matches_uses_post_journal_count():
    journal = PageOpsJournal()
    journal.record(('delete', [0]))
    ops = serialize_journal(journal)

    # a delete-page session: 3 original pages, 2 saved -> matches
    assert document_loader.restored_session_matches(
        {'pages': 2, 'journal': ops}, 3) is True
    # the OLD (wrong) guard compared against the original count
    assert document_loader.restored_session_matches(
        {'pages': 3, 'journal': ops}, 3) is False
    # without a journal the counts must simply be equal
    assert document_loader.restored_session_matches(
        {'pages': 3, 'journal': []}, 3) is True
    assert document_loader.restored_session_matches(
        {'pages': 2, 'journal': []}, 3) is False
    # a journal that cannot fit the document never matches (and never raises)
    assert document_loader.restored_session_matches(
        {'pages': 1, 'journal': [['delete', [7]]]}, 2) is False
    assert document_loader.restored_session_matches(None, 2) is False


def test_restored_session_matches_insert_ops(tmp_path):
    journal = PageOpsJournal()
    journal.record(('insert_blank', 1, [200, 300]))
    assert document_loader.restored_session_matches(
        {'pages': 4, 'journal': serialize_journal(journal)}, 3) is True


# ---------------------------------------------------------------------------
# apply_restored_session: journal replay BEFORE annotation attach
# ---------------------------------------------------------------------------

def test_apply_restored_session_replays_rotation_then_attaches(tmp_path):
    path = fixtures.make_pdf(tmp_path / 'rot.pdf', pages=2, size=(300, 400))
    images = page_render.render_pdf_pages(path, [0, 1])

    journal = PageOpsJournal()
    journal.record(('rotate', {0: 90}))
    saved_ann = to_dict(redact_ann((10, 10), (60, 60)))  # post-rotate coords
    work_data = {
        'annotations': [[saved_ann], []],
        'journal': serialize_journal(journal),
        'pages': 2,
    }

    document_loader.apply_restored_session(images, work_data)

    # page 0 is displayed rotated (landscape) …
    assert images[0].image.width > images[0].image.height
    assert images[0].image.width == pytest.approx(400 * PX_PER_PT, abs=2)
    assert images[0].image.height == pytest.approx(300 * PX_PER_PT, abs=2)
    # … and the restored annotation is attached UNTRANSFORMED (its coords
    # are already post-op) — the journal must not touch it a second time.
    assert len(images[0].annotations) == 1
    assert images[0].annotations[0].props == saved_ann['props']
    assert images[1].annotations == []

    # journal not double-applied: saving organized replays the SAME journal
    # on the original file and yields a single 90 degree rotation whose
    # display size matches the restored in-memory page.
    out = str(tmp_path / 'organized.pdf')
    pdf_ops.apply_journal(path, journal, out)
    from pypdf import PdfReader
    page = PdfReader(out).pages[0]
    assert page.rotation % 360 == 90
    eff_w = float(page.mediabox.height)  # rotated: displayed width
    eff_h = float(page.mediabox.width)
    assert images[0].image.width == pytest.approx(eff_w * PX_PER_PT, abs=2)
    assert images[0].image.height == pytest.approx(eff_h * PX_PER_PT, abs=2)

    for container in images:
        container.close()


def test_apply_restored_session_delete_page_session(tmp_path):
    path = fixtures.make_pdf(tmp_path / 'del.pdf', pages=3, size=(300, 400))
    images = page_render.render_pdf_pages(path, [0, 1, 2])

    journal = PageOpsJournal()
    journal.record(('delete', [0]))
    ann = to_dict(redact_ann((5, 5), (25, 25)))
    work_data = {
        'annotations': [[ann], []],   # per POST-op page
        'journal': serialize_journal(journal),
        'pages': 2,
    }

    document_loader.apply_restored_session(images, work_data)

    assert len(images) == 2
    assert len(images[0].annotations) == 1
    assert images[0].annotations[0].props == ann['props']
    assert images[1].annotations == []
    for container in images:
        container.close()


# ---------------------------------------------------------------------------
# FakeWindow/FakeGraph shims for the full load_path flow
# ---------------------------------------------------------------------------

class FakeWidget:
    def config(self, **kwargs):
        pass

    def create_line(self, *args, **kwargs):
        return 1

    def create_rectangle(self, *args, **kwargs):
        return 1


class FakeGraph:
    def __init__(self):
        self.next_id = 1
        self.Widget = FakeWidget()

    def _new_id(self, *args, **kwargs):
        figure_id = self.next_id
        self.next_id += 1
        return figure_id

    draw_image = draw_rectangle = draw_line = draw_oval = _new_id
    draw_text = draw_point = _new_id

    def erase(self):
        pass

    def delete_figure(self, figure_id):
        pass

    def set_cursor(self, cursor):
        pass


class FakeElement:
    def update(self, *args, **kwargs):
        pass


class FakeWindow:
    def __init__(self):
        self.elements = {'-GRAPH-': FakeGraph()}

    def __getitem__(self, key):
        return self.elements.setdefault(key, FakeElement())

    def refresh(self):
        pass

    def set_cursor(self, cursor):
        pass

    def set_title(self, title):
        pass

    def current_location(self):
        return (0, 0)

    def current_size_accurate(self):
        return (800, 600)


def _saved_session(tmp_path, pdf_path, annotations_per_page, journal):
    """Persist a work session for pdf_path the way _save_worksession does."""
    datadir = tmp_path / 'data'
    datadir.mkdir(exist_ok=True)
    manager = WorkfileManager(str(datadir))
    manager.set_file_path(pdf_path)

    class _Page:
        def __init__(self, annotations):
            self.annotations = annotations

    pages = [_Page(anns) for anns in annotations_per_page]
    manager.save(pages, 0, 'black', 'high',
                 journal=serialize_journal(journal))
    return manager


def test_load_path_replays_journal_and_seeds_undo(tmp_path, monkeypatch):
    pdf_path = fixtures.make_pdf(tmp_path / 'doc.pdf', pages=2,
                                 size=(300, 400))
    journal = PageOpsJournal()
    journal.record(('rotate', {0: 90}))
    restored = redact_ann((10, 10), (60, 60))     # post-rotate coords
    manager = _saved_session(tmp_path, pdf_path, [[restored], []], journal)

    state = AppState()
    state.workfile_manager = manager
    window = FakeWindow()

    # accept the restore prompt; any error popup fails the test loudly
    monkeypatch.setattr(document_loader.sg, 'popup_ok_cancel',
                        lambda *a, **k: 'OK')
    monkeypatch.setattr(
        file_handlers.sg, 'popup',
        lambda *a, **k: pytest.fail(f'error popup shown: {a}'))

    file_handlers.load_path(window, state, pdf_path)

    # the journal was replayed exactly once on the rendered original pages
    assert len(state.images) == 2
    assert state.images[0].image.width > state.images[0].image.height
    assert state.journal is not None
    assert state.journal.ops == [('rotate', {0: 90})]
    # restored annotation attached with its (post-op) coordinates untouched
    assert len(state.images[0].annotations) == 1
    assert state.images[0].annotations[0].props == restored.props
    # saving organized later replays the journal on the ORIGINAL file: a
    # single 90 degree rotation, not a double-applied 180
    out = str(tmp_path / 'org.pdf')
    pdf_ops.apply_journal(pdf_path, state.journal, out)
    from pypdf import PdfReader
    assert PdfReader(out).pages[0].rotation % 360 == 90

    # fix 6: the restored set is inside undo — one Undo empties the page …
    assert set(state.undo) == {0}
    assert state.current_page == 0
    edit_handlers.undo(window, state)
    assert state.images[0].annotations == []
    # … and Redo brings it back
    edit_handlers.redo(window, state)
    assert len(state.images[0].annotations) == 1
    assert state.images[0].annotations[0].props == restored.props


def test_load_path_restores_delete_page_session(tmp_path, monkeypatch):
    pdf_path = fixtures.make_pdf(tmp_path / 'doc3.pdf', pages=3,
                                 size=(300, 400))
    journal = PageOpsJournal()
    journal.record(('delete', [0]))
    ann = redact_ann((5, 5), (25, 25))
    # the saved session has 2 (post-delete) pages
    manager = _saved_session(tmp_path, pdf_path, [[ann], []], journal)

    state = AppState()
    state.workfile_manager = manager
    window = FakeWindow()
    prompts = []

    def fake_prompt(*a, **k):
        prompts.append(True)
        return 'OK'

    monkeypatch.setattr(document_loader.sg, 'popup_ok_cancel', fake_prompt)
    monkeypatch.setattr(
        file_handlers.sg, 'popup',
        lambda *a, **k: pytest.fail(f'error popup shown: {a}'))

    file_handlers.load_path(window, state, pdf_path)

    # the guard accepted the session (2 == post-journal count of 3 pages) …
    assert prompts, 'restore prompt was never offered'
    # … the delete was replayed and the annotations survived on the right page
    assert len(state.images) == 2
    assert state.journal.ops == [('delete', [0])]
    assert len(state.images[0].annotations) == 1
    assert state.images[0].annotations[0].props == ann.props
    assert state.images[1].annotations == []
    assert set(state.undo) == {0}


def test_load_document_skips_restore_when_counts_mismatch(tmp_path, monkeypatch):
    pdf_path = fixtures.make_pdf(tmp_path / 'doc4.pdf', pages=2,
                                 size=(300, 400))
    # stale session: claims 3 pages with no journal -> cannot fit
    manager = _saved_session(tmp_path, pdf_path,
                             [[redact_ann((1, 1), (2, 2))], [], []],
                             PageOpsJournal())

    window = FakeWindow()
    monkeypatch.setattr(
        document_loader.sg, 'popup_ok_cancel',
        lambda *a, **k: pytest.fail('restore prompt offered for a session '
                                    'that does not fit the document'))

    images, *_rest = document_loader.load_document(
        pdf_path, 200, window, manager)
    assert len(images) == 2
    assert all(container.annotations == [] for container in images)
    for container in images:
        container.close()
