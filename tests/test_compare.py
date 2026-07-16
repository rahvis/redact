"""
Tests for workonward_read.compare.

License: GPL-3.0
(c) 2024 - 2026 Björn Seipel
(c) 2026 WorkOnward Read contributors
"""

import os

import pypdfium2 as pdfium
import pytest
from fpdf import FPDF

import fixtures
from fixtures import runtime_pw
from workonward_read.compare import (
    CompareResult,
    PageDiff,
    compare_pdfs,
    export_diff_report,
    text_diff,
)

DPI = 100
PAGE_W_PT, PAGE_H_PT = 595.28, 841.89
# Black square drawn only into document B (pt, from top-left).
SQ_X, SQ_Y, SQ_SIZE = 200.0, 300.0, 80.0


def _make_pair_pdf(path, with_square):
    """Two runs of this produce pixel-identical pages except for the square."""
    pdf = FPDF(unit="pt", format=(PAGE_W_PT, PAGE_H_PT))
    pdf.set_auto_page_break(False)
    pdf.add_page()
    pdf.set_font("helvetica", size=14)
    pdf.set_xy(72, 72)
    pdf.cell(300, 18, "Shared content that both documents carry")
    if with_square:
        pdf.set_fill_color(0, 0, 0)
        pdf.rect(SQ_X, SQ_Y, SQ_SIZE, SQ_SIZE, style="F")
    pdf.output(str(path))
    return str(path)


def test_identical_file(tmp_path):
    pdf = fixtures.make_pdf(tmp_path / "same.pdf", pages=3)
    result = compare_pdfs(pdf, pdf, dpi=DPI)

    assert isinstance(result, CompareResult)
    assert result.identical is True
    assert result.page_count_a == result.page_count_b == 3
    assert len(result.pages) == 3
    for page in result.pages:
        assert isinstance(page, PageDiff)
        assert page.changed_ratio == 0.0
        assert page.regions_px == []


def test_added_black_square_flagged(tmp_path):
    path_a = _make_pair_pdf(tmp_path / "a.pdf", with_square=False)
    path_b = _make_pair_pdf(tmp_path / "b.pdf", with_square=True)
    result = compare_pdfs(path_a, path_b, dpi=DPI)

    assert result.identical is False
    assert len(result.pages) == 1
    page = result.pages[0]
    assert page.changed_ratio > 0.0
    # Square is ~80x80pt of a 595x842pt page: small but not tiny
    assert page.changed_ratio < 0.1
    assert len(page.regions_px) == 1

    # Expected square position in rendered px at DPI (y-down)
    exp_x0 = SQ_X * DPI / 72.0
    exp_y0 = SQ_Y * DPI / 72.0
    exp_x1 = (SQ_X + SQ_SIZE) * DPI / 72.0
    exp_y1 = (SQ_Y + SQ_SIZE) * DPI / 72.0

    x0, y0, x1, y1 = page.regions_px[0]
    # Region overlaps the square...
    assert x0 < exp_x1 and x1 > exp_x0
    assert y0 < exp_y1 and y1 > exp_y0
    # ...and does not exceed it by more than one grid cell + rounding.
    slack = 17
    assert x0 >= exp_x0 - slack
    assert y0 >= exp_y0 - slack
    assert x1 <= exp_x1 + slack
    assert y1 <= exp_y1 + slack


def test_different_page_counts(tmp_path):
    path_a = fixtures.make_pdf(tmp_path / "a.pdf", pages=2)
    path_b = fixtures.make_pdf(tmp_path / "b.pdf", pages=3)
    result = compare_pdfs(path_a, path_b, dpi=DPI)

    assert result.page_count_a == 2
    assert result.page_count_b == 3
    assert result.identical is False
    assert len(result.pages) == 3

    # Common pages have identical content in the fixture
    assert result.pages[0].changed_ratio == 0.0
    assert result.pages[1].changed_ratio == 0.0

    extra = result.pages[2]
    assert extra.changed_ratio == 1.0
    assert len(extra.regions_px) == 1
    x0, y0, x1, y1 = extra.regions_px[0]
    assert (x0, y0) == (0, 0)
    assert x1 == pytest.approx(PAGE_W_PT * DPI / 72.0, abs=1)
    assert y1 == pytest.approx(PAGE_H_PT * DPI / 72.0, abs=1)


def test_encrypted_inputs(tmp_path):
    enc = fixtures.make_encrypted_pdf(tmp_path / "enc.pdf",
                                      user_password=runtime_pw("secret"), pages=2)
    result = compare_pdfs(enc, enc, password_a=runtime_pw("secret"), password_b=runtime_pw("secret"))
    assert result.identical is True

    with pytest.raises(ValueError):
        compare_pdfs(enc, enc)
    with pytest.raises(ValueError):
        compare_pdfs(enc, enc, password_a=runtime_pw("secret"), password_b=runtime_pw("wrong"))


def test_progress_callback(tmp_path):
    path_a = fixtures.make_pdf(tmp_path / "a.pdf", pages=2)
    path_b = fixtures.make_pdf(tmp_path / "b.pdf", pages=3)
    calls = []
    compare_pdfs(path_a, path_b, progress_cb=lambda pct, msg: calls.append(pct))
    assert len(calls) == 3
    assert calls[-1] == 100


def test_text_diff_shows_changed_line(tmp_path):
    path_a = fixtures.make_text_pdf(tmp_path / "a.pdf",
                                    text="Hello World and more")
    path_b = fixtures.make_text_pdf(tmp_path / "b.pdf",
                                    text="Hello Mars and more")
    diff = text_diff(path_a, path_b)

    assert isinstance(diff, list)
    assert any(line.startswith("-") and "World" in line for line in diff)
    assert any(line.startswith("+") and "Mars" in line for line in diff)


def test_text_diff_identical_is_empty(tmp_path):
    pdf = fixtures.make_text_pdf(tmp_path / "a.pdf")
    assert text_diff(pdf, pdf) == []


def test_export_diff_report(tmp_path):
    path_a = _make_pair_pdf(tmp_path / "a.pdf", with_square=False)
    path_b = _make_pair_pdf(tmp_path / "b.pdf", with_square=True)
    result = compare_pdfs(path_a, path_b, dpi=DPI)

    out = tmp_path / "report.pdf"
    export_diff_report(result, path_a, path_b, str(out), dpi=DPI)

    assert out.is_file()
    assert os.path.getsize(out) > 5000  # embeds two rendered page images

    report = pdfium.PdfDocument(str(out))
    try:
        assert len(report) == max(result.page_count_a, result.page_count_b)
    finally:
        report.close()


def test_export_diff_report_page_count_mismatch(tmp_path):
    path_a = fixtures.make_pdf(tmp_path / "a.pdf", pages=1)
    path_b = fixtures.make_pdf(tmp_path / "b.pdf", pages=3)
    result = compare_pdfs(path_a, path_b, dpi=DPI)

    out = tmp_path / "report.pdf"
    export_diff_report(result, path_a, path_b, str(out), dpi=DPI)

    report = pdfium.PdfDocument(str(out))
    try:
        assert len(report) == 3
    finally:
        report.close()
