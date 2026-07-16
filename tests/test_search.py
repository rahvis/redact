"""
Tests for coverup.search.

License: GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import pytest

import fixtures
from coverup.search import Hit, search_document

PT_TO_PX = 200 / 72.0
# fixtures.make_pdf default page size (A4 portrait, pt)
PAGE_W_PT, PAGE_H_PT = 595.28, 841.89


def _make_search_pdf(tmp_path):
    return fixtures.make_pdf(
        tmp_path / "search.pdf",
        pages=3,
        texts=[
            "alpha needle in the haystack",
            "nothing to see on this page",
            "big Needle up high\nsome filler line\nlow needle down here",
        ],
    )


def test_finds_term_on_correct_pages(tmp_path):
    pdf = _make_search_pdf(tmp_path)
    hits = search_document(pdf, "needle")

    assert all(isinstance(h, Hit) for h in hits)
    # page 0 has one, page 1 none, page 2 has two (Needle + needle)
    assert [h.page_index for h in hits] == [0, 2, 2]


def test_context_contains_match_and_surroundings(tmp_path):
    pdf = _make_search_pdf(tmp_path)
    hits = search_document(pdf, "needle")

    first = hits[0]
    assert "needle" in first.context
    # +-40 chars of context around a short page keeps neighbors visible
    assert "alpha" in first.context
    assert "haystack" in first.context


def test_match_case(tmp_path):
    pdf = fixtures.make_pdf(
        tmp_path / "case.pdf",
        pages=2,
        texts=["only Needle capitalized here", "no match at all"],
    )
    insensitive = search_document(pdf, "needle", match_case=False)
    sensitive_lower = search_document(pdf, "needle", match_case=True)
    sensitive_exact = search_document(pdf, "Needle", match_case=True)

    assert [h.page_index for h in insensitive] == [0]
    assert sensitive_lower == []
    assert [h.page_index for h in sensitive_exact] == [0]


def test_rects_within_page_bounds(tmp_path):
    pdf = _make_search_pdf(tmp_path)
    hits = search_document(pdf, "needle")

    page_w_px = PAGE_W_PT * PT_TO_PX
    page_h_px = PAGE_H_PT * PT_TO_PX
    assert hits
    for hit in hits:
        assert hit.rects_px, "every hit must carry at least one rectangle"
        for x0, y0, x1, y1 in hit.rects_px:
            assert 0 <= x0 < x1 <= page_w_px + 0.5
            assert 0 <= y0 < y1 <= page_h_px + 0.5


def test_rect_positions_plausible(tmp_path):
    # Text starts at x=72pt / y=72pt in the fixture, so the first rect
    # must sit near the top-left text origin (in 200-PPI px, y-down).
    pdf = fixtures.make_pdf(tmp_path / "pos.pdf", pages=1,
                            texts=["needle right at the start"])
    hits = search_document(pdf, "needle")

    assert len(hits) == 1
    x0, y0, x1, y1 = hits[0].rects_px[0]
    assert x0 == pytest.approx(72 * PT_TO_PX, abs=30)
    assert y0 == pytest.approx(72 * PT_TO_PX, abs=60)
    assert x1 - x0 > 10  # a six-char word is clearly wider than 10 px


def test_rect_y_increases_for_lower_lines(tmp_path):
    pdf = fixtures.make_pdf(
        tmp_path / "lines.pdf",
        pages=1,
        texts=["needle on the first line\nplain middle line\nneedle on a lower line"],
    )
    hits = search_document(pdf, "needle")

    assert len(hits) == 2
    top_rect = hits[0].rects_px[0]
    bottom_rect = hits[1].rects_px[0]
    assert bottom_rect[1] > top_rect[1]
    assert bottom_rect[3] > top_rect[3]


def test_empty_term_raises(tmp_path):
    pdf = fixtures.make_text_pdf(tmp_path / "t.pdf")
    with pytest.raises(ValueError):
        search_document(pdf, "")


def test_no_match_returns_empty_list(tmp_path):
    pdf = fixtures.make_text_pdf(tmp_path / "t.pdf", text="plain content")
    assert search_document(pdf, "zzz-not-there") == []


def test_unicode_term(tmp_path):
    pdf = fixtures.make_pdf(tmp_path / "uni.pdf", pages=1,
                            texts=["Grüße from Köln with café"])
    hits = search_document(pdf, "Köln")
    assert [h.page_index for h in hits] == [0]
    assert "Köln" in hits[0].context


def test_encrypted_pdf_with_password(tmp_path):
    pdf = fixtures.make_encrypted_pdf(tmp_path / "enc.pdf",
                                      user_password="secret", pages=2)
    hits = search_document(pdf, "Encrypted", password="secret")
    assert [h.page_index for h in hits] == [0, 1]


def test_encrypted_pdf_without_password_raises(tmp_path):
    pdf = fixtures.make_encrypted_pdf(tmp_path / "enc.pdf",
                                      user_password="secret")
    with pytest.raises(ValueError):
        search_document(pdf, "Encrypted")
    with pytest.raises(ValueError):
        search_document(pdf, "Encrypted", password="wrong")


def test_progress_callback_reaches_100(tmp_path):
    pdf = _make_search_pdf(tmp_path)
    calls = []
    search_document(pdf, "needle", progress_cb=lambda pct, msg: calls.append(pct))
    assert len(calls) == 3
    assert calls[-1] == 100
    assert calls == sorted(calls)
