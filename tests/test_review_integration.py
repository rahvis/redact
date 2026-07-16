"""Headless integration tests for the review handler group
(in-document search + compare) — workonward_read/handlers/review.py.

No real sg.Window is ever opened: the window-facing wrappers stay untested
here; the module-level core functions are exercised directly, with tiny
FakeWindow/FakeGraph shims where a graph surface is needed.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import pypdfium2 as pdfium
import pytest

import fixtures
from workonward_read.handlers import review
from workonward_read.pdf_ops import PageOpsJournal
from workonward_read.search import Hit, page_count
from workonward_read.state import AppState

PT_TO_PX = 200 / 72.0
# fixtures.make_pdf default page size (A4 portrait, pt)
PAGE_W_PT, PAGE_H_PT = 595.28, 841.89


# ---------------------------------------------------------------------------
# Shims
# ---------------------------------------------------------------------------

class FakeGraph:
    """Records draw_rectangle/delete_figure calls like an sg.Graph would."""

    def __init__(self):
        self.next_id = 1
        self.figures = {}
        self.deleted = []

    def draw_rectangle(self, top_left, bottom_right, fill_color=None,
                       line_color=None, line_width=None):
        figure_id = self.next_id
        self.next_id += 1
        self.figures[figure_id] = {
            'top_left': top_left,
            'bottom_right': bottom_right,
            'line_color': line_color,
            'line_width': line_width,
        }
        return figure_id

    def delete_figure(self, figure_id):
        self.deleted.append(figure_id)
        self.figures.pop(figure_id, None)


class FakeWindow:
    def __init__(self):
        self.graph = FakeGraph()

    def __getitem__(self, key):
        assert key == '-GRAPH-'
        return self.graph


def _pdf_state(pdf_path, pages=3):
    state = AppState()
    state.file_path = str(pdf_path)
    state.images = [object()] * pages  # loaded-document marker only
    return state


# ---------------------------------------------------------------------------
# Handler registry shape
# ---------------------------------------------------------------------------

def test_handlers_dict_shape():
    assert set(review.HANDLERS) == {'MENU_SEARCH', 'MENU_COMPARE'}
    assert review.HANDLERS['MENU_SEARCH'] is review.search
    assert review.HANDLERS['MENU_COMPARE'] is review.compare


# ---------------------------------------------------------------------------
# Search core
# ---------------------------------------------------------------------------

def _search_pdf(tmp_path):
    return fixtures.make_pdf(
        tmp_path / 'search.pdf',
        pages=3,
        texts=[
            'alpha needle in the haystack',
            'nothing relevant on this page',
            'Needle up high\nfiller line\nlow needle down here',
        ],
    )


def test_perform_search_pages_and_px_rects(tmp_path):
    state = _pdf_state(_search_pdf(tmp_path))
    hits = review.perform_search(state, 'needle')

    assert [hit.page_index for hit in hits] == [0, 2, 2]

    page_w_px = PAGE_W_PT * PT_TO_PX
    page_h_px = PAGE_H_PT * PT_TO_PX
    for hit in hits:
        assert hit.rects_px, 'every hit must carry at least one rectangle'
        for x0, y0, x1, y1 in hit.rects_px:
            assert 0 <= x0 < x1 <= page_w_px
            assert 0 <= y0 < y1 <= page_h_px
        # hits carry the page size so rects can be remapped through page ops
        assert hit.page_size_px == pytest.approx([page_w_px, page_h_px])


def test_perform_search_match_case(tmp_path):
    state = _pdf_state(_search_pdf(tmp_path))
    sensitive = review.perform_search(state, 'needle', match_case=True)
    # 'Needle' on page 2 is excluded when matching case.
    assert [hit.page_index for hit in sensitive] == [0, 2]


def test_perform_search_empty_term_raises(tmp_path):
    state = _pdf_state(_search_pdf(tmp_path))
    with pytest.raises(ValueError):
        review.perform_search(state, '')
    with pytest.raises(ValueError):
        review.perform_search(state, '   ')
    with pytest.raises(ValueError):
        review.perform_search(state, None)


def test_perform_search_uses_source_password(tmp_path):
    pdf = fixtures.make_encrypted_pdf(tmp_path / 'enc.pdf',
                                      user_password='secret', pages=2)
    state = _pdf_state(pdf, pages=2)

    with pytest.raises(ValueError):
        review.perform_search(state, 'Encrypted')

    state.source_password = 'secret'
    hits = review.perform_search(state, 'Encrypted')
    assert [hit.page_index for hit in hits] == [0, 1]


def test_format_hit_is_one_based():
    from workonward_read.search import Hit
    assert review.format_hit(
        Hit(page_index=2, context='low needle down')) == 'p.3: low needle down'


def test_is_pdf_loaded_guard(tmp_path):
    state = AppState()
    assert review.is_pdf_loaded(state) is False          # nothing loaded

    state.file_path = str(tmp_path / 'doc.pdf')
    assert review.is_pdf_loaded(state) is False          # no images

    state.images = [object()]
    assert review.is_pdf_loaded(state) is True           # loaded PDF

    state.file_path = str(tmp_path / 'photo.PNG')
    assert review.is_pdf_loaded(state) is False          # image import


# ---------------------------------------------------------------------------
# Hit remapping through the page-ops journal (search runs on the ORIGINAL
# file, the displayed document may have been reorganized)
# ---------------------------------------------------------------------------

def _hit(page_index, rect, size=(800, 1000)):
    return Hit(page_index=page_index, context='x', rects_px=[list(rect)],
               page_size_px=list(size))


def test_remap_hit_location_without_journal_is_identity():
    hit = _hit(2, [10, 20, 30, 40])
    assert review.remap_hit_location(hit, None, 3) == (2, [[10, 20, 30, 40]])
    assert review.remap_hit_location(hit, PageOpsJournal(), 3) == \
        (2, [[10, 20, 30, 40]])


def test_remap_hit_location_delete_shifts_page_index():
    journal = PageOpsJournal()
    journal.record(('delete', [0]))
    current, rects = review.remap_hit_location(
        _hit(2, [10, 20, 30, 40]), journal, 3)
    assert current == 1
    assert rects == [[10, 20, 30, 40]]


def test_remap_hit_location_deleted_page_returns_none():
    journal = PageOpsJournal()
    journal.record(('delete', [1]))
    assert review.remap_hit_location(_hit(1, [10, 20, 30, 40]), journal, 3) \
        == (None, [])


def test_remap_hit_location_rotate_transforms_rect():
    journal = PageOpsJournal()
    journal.record(('rotate', {0: 90}))
    current, rects = review.remap_hit_location(
        _hit(0, [10, 20, 30, 60], size=(100, 200)), journal, 1)
    assert current == 0
    # 90 CW on a 100x200 page: corners (x, y) -> (199 - y, x), re-normalized
    assert rects == [[139, 10, 179, 30]]


def test_remap_hit_location_cropped_away_rect_keeps_page():
    journal = PageOpsJournal()
    journal.record(('crop', 0, [100, 100, 300, 400]))
    current, rects = review.remap_hit_location(
        _hit(0, [0, 0, 40, 40], size=(800, 1000)), journal, 1)
    # the page survives (shown without outlines), the rect was cropped away
    assert current == 0
    assert rects == []


def test_remap_hit_location_without_page_size_drops_rects_only():
    journal = PageOpsJournal()
    journal.record(('rotate', {0: 90}))
    hit = Hit(page_index=0, context='x', rects_px=[[10, 20, 30, 40]])
    assert review.remap_hit_location(hit, journal, 1) == (0, [])


def test_page_count_helper(tmp_path):
    pdf = fixtures.make_pdf(tmp_path / 'count.pdf', pages=3)
    assert page_count(pdf) == 3
    enc = fixtures.make_encrypted_pdf(tmp_path / 'enc.pdf',
                                      user_password='pw', pages=2)
    assert page_count(enc, password='pw') == 2
    with pytest.raises(ValueError):
        page_count(enc)


# ---------------------------------------------------------------------------
# Hit rectangle scaling + temporary graph figures
# ---------------------------------------------------------------------------

def test_hit_rect_to_graph_zoom_100():
    top_left, bottom_right = review.hit_rect_to_graph([10, 20, 30, 40], 100)
    assert top_left == pytest.approx((10.0, -20.0))
    assert bottom_right == pytest.approx((30.0, -40.0))


def test_hit_rect_to_graph_zoom_140():
    top_left, bottom_right = review.hit_rect_to_graph([10, 20, 30, 40], 140)
    assert top_left == pytest.approx((14.0, -28.0))
    assert bottom_right == pytest.approx((42.0, -56.0))


def test_draw_and_clear_hit_outlines():
    window = FakeWindow()
    rects = [[10, 20, 30, 40], [50, 60, 70, 80]]

    ids = review.draw_hit_outlines(window, rects, 140)
    assert len(ids) == 2
    figure = window.graph.figures[ids[0]]
    assert figure['line_color'] == 'red'
    assert figure['top_left'] == pytest.approx((14.0, -28.0))
    assert figure['bottom_right'] == pytest.approx((42.0, -56.0))

    remaining = review.clear_temp_figures(window, ids)
    assert remaining == []
    assert window.graph.figures == {}
    assert window.graph.deleted == ids


def test_clear_temp_figures_tolerates_empty():
    window = FakeWindow()
    assert review.clear_temp_figures(window, None) == []
    assert review.clear_temp_figures(window, []) == []


# ---------------------------------------------------------------------------
# Compare core
# ---------------------------------------------------------------------------

def test_run_compare_identical_pair(tmp_path):
    pdf = fixtures.make_pdf(tmp_path / 'same.pdf', pages=3)
    request = {'path_a': pdf, 'path_b': pdf, 'dpi': 75, 'threshold': 24}

    result = review.run_compare(request)
    assert result.identical is True

    lines = review.build_result_lines(result)
    assert len(lines) == 3
    assert lines[0].startswith('p.1: 0.00%')
    assert all('identical' in line for line in lines)
    assert 'identical' in review.verdict_text(result)


def test_run_compare_different_pair(tmp_path):
    pdf_a = fixtures.make_pdf(tmp_path / 'a.pdf', pages=2,
                              texts=['shared first page', 'original text'])
    pdf_b = fixtures.make_pdf(tmp_path / 'b.pdf', pages=2,
                              texts=['shared first page', 'REVISED CONTENT'])
    request = {'path_a': pdf_a, 'path_b': pdf_b, 'dpi': 75, 'threshold': 24}

    result = review.run_compare(request)
    assert result.identical is False
    assert result.pages[0].changed_ratio == 0
    assert result.pages[1].changed_ratio > 0

    lines = review.build_result_lines(result)
    assert 'identical' in lines[0]
    assert 'different' in lines[1]

    verdict = review.verdict_text(result)
    assert 'different' in verdict
    assert '1' in verdict and '2' in verdict


def test_resolve_passwords_only_for_loaded_source(tmp_path):
    pdf_a = fixtures.make_pdf(tmp_path / 'a.pdf', pages=1)
    pdf_b = fixtures.make_pdf(tmp_path / 'b.pdf', pages=1)

    state = AppState()
    state.file_path = pdf_a
    state.source_password = 'secret'

    request = {'path_a': pdf_a, 'path_b': pdf_b, 'dpi': 100, 'threshold': 24}
    resolved = review.resolve_passwords(request, state)
    assert resolved.get('password_a') == 'secret'
    assert resolved.get('password_b') is None
    assert 'password_a' not in request  # original untouched

    state.source_password = None
    resolved = review.resolve_passwords(request, state)
    assert resolved.get('password_a') is None


def test_export_report_has_max_page_count(tmp_path):
    pdf_a = fixtures.make_pdf(tmp_path / 'a.pdf', pages=3)
    pdf_b = fixtures.make_pdf(tmp_path / 'b.pdf', pages=2)
    request = {'path_a': pdf_a, 'path_b': pdf_b, 'dpi': 75, 'threshold': 24}

    result = review.run_compare(request)
    output = tmp_path / 'report.pdf'
    returned = review.export_report(result, request, str(output))

    assert returned == str(output)
    report = pdfium.PdfDocument(str(output))
    try:
        assert len(report) == max(result.page_count_a, result.page_count_b) == 3
    finally:
        report.close()


def test_run_text_diff_and_truncation(tmp_path):
    pdf_a = fixtures.make_pdf(tmp_path / 'a.pdf', pages=1, texts=['old line'])
    pdf_b = fixtures.make_pdf(tmp_path / 'b.pdf', pages=1, texts=['new line'])
    request = {'path_a': pdf_a, 'path_b': pdf_b}

    diff_lines = review.run_text_diff(request)
    assert any(line.startswith('-') and 'old line' in line
               for line in diff_lines)
    assert any(line.startswith('+') and 'new line' in line
               for line in diff_lines)

    # No differences -> friendly message instead of an empty popup.
    assert review.format_text_diff([]) == 'No text differences found.'

    # Truncation beyond 5000 lines.
    many = [f'line {i}' for i in range(6000)]
    formatted = review.format_text_diff(many)
    formatted_lines = formatted.splitlines()
    assert len(formatted_lines) == review.TEXT_DIFF_MAX_LINES + 1
    assert 'truncated' in formatted_lines[-1]
    assert '1000' in formatted_lines[-1]

    short = review.format_text_diff(many[:10])
    assert short.splitlines() == many[:10]
