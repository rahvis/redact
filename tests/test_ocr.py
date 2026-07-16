"""
Tests for coverup.ocr — Tesseract OCR producing searchable PDFs.

OCR tests are skipped when no tesseract binary is installed; the input
validation tests run everywhere.

CoverUP is licensed under GPL-3.0. (c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import io
import os

import pytest
import pypdfium2 as pdfium
from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter

import fixtures  # noqa: F401 (path added by conftest)
from coverup import ocr

TESSERACT = ocr.find_tesseract()
needs_tesseract = pytest.mark.skipif(
    TESSERACT is None, reason="tesseract binary not installed")

WORDS = ("HELLO", "WORLD", "42")
TEXT = "HELLO WORLD 42"
DPI = 200


def make_big_text_image(text=TEXT, dpi=DPI):
    """A4-sized white image with very large black text (easy for OCR)."""
    w_px, h_px = int(8.27 * dpi), int(11.69 * dpi)
    img = Image.new("RGB", (w_px, h_px), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=80)
    draw.text((w_px // 8, h_px // 3), text, fill=(0, 0, 0), font=font)
    return img


def make_big_scan_pdf(path, text=TEXT, dpi=DPI):
    """Image-only PDF (no text layer) with large text, like a scan."""
    img = make_big_text_image(text=text, dpi=dpi)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    buf.seek(0)

    w_pt, h_pt = img.width * 72 / dpi, img.height * 72 / dpi
    pdf = FPDF(unit="pt", format=(w_pt, h_pt))
    pdf.add_page()
    pdf.image(buf, x=0, y=0, w=w_pt, h=h_pt)
    pdf.output(str(path))
    return str(path)


def extract_text_pdfium(path, password=None):
    """Extract all text from a PDF via pypdfium2."""
    doc = pdfium.PdfDocument(str(path), password=password)
    try:
        chunks = []
        for page in doc:
            textpage = page.get_textpage()
            chunks.append(textpage.get_text_range())
            textpage.close()
            page.close()
        return "\n".join(chunks)
    finally:
        doc.close()


def page_sizes_pdfium(path, password=None):
    doc = pdfium.PdfDocument(str(path), password=password)
    try:
        return [doc.get_page(i).get_size() for i in range(len(doc))]
    finally:
        doc.close()


def assert_contains_ocr_words(text):
    """OCR-fuzz tolerant check: any of the expected words present."""
    lowered = text.lower()
    assert any(word.lower() in lowered for word in WORDS), \
        "none of {} found in extracted text: {!r}".format(WORDS, text[:200])


# ---------------------------------------------------------------- discovery

@needs_tesseract
def test_find_tesseract_returns_existing_path():
    path = ocr.find_tesseract()
    assert path is not None
    assert os.path.isfile(path)
    import pytesseract
    assert pytesseract.pytesseract.tesseract_cmd == path


@needs_tesseract
def test_find_tesseract_prefers_valid_user_path():
    real = ocr.find_tesseract()
    assert ocr.find_tesseract(user_path=real) == real


@needs_tesseract
def test_find_tesseract_falls_back_on_bad_user_path():
    path = ocr.find_tesseract(user_path="/nonexistent/tesseract-binary")
    assert path is not None
    assert os.path.isfile(path)


@needs_tesseract
def test_available_languages_contains_eng():
    langs = ocr.available_languages()
    assert isinstance(langs, list)
    assert "eng" in langs


# ---------------------------------------------------------------- plain OCR

@needs_tesseract
def test_ocr_image_to_text():
    img = make_big_text_image()
    text = ocr.ocr_image_to_text(img)
    assert_contains_ocr_words(text)
    img.close()


# ------------------------------------------------------- searchable PDF core

@needs_tesseract
def test_make_searchable_pdf_text_and_geometry(tmp_path):
    src = make_big_scan_pdf(tmp_path / "scan.pdf")
    out = str(tmp_path / "searchable.pdf")

    # Source really has no text layer.
    assert extract_text_pdfium(src).strip() == ""

    progress = []
    result = ocr.make_searchable_pdf(
        src, out, progress_cb=lambda pct, msg: progress.append((pct, msg)))
    assert result == out
    assert os.path.getsize(out) > 0

    # OCR text is extractable from the output.
    assert_contains_ocr_words(extract_text_pdfium(out))

    # Page sizes match the source within 1 pt.
    src_sizes = page_sizes_pdfium(src)
    out_sizes = page_sizes_pdfium(out)
    assert len(out_sizes) == len(src_sizes) == 1
    for (sw, sh), (ow, oh) in zip(src_sizes, out_sizes):
        assert abs(sw - ow) <= 1.0
        assert abs(sh - oh) <= 1.0

    # Progress was reported and finished at 100.
    assert progress
    assert progress[-1][0] == 100
    assert all(0 <= pct <= 100 for pct, _ in progress)


@needs_tesseract
def test_make_searchable_pdf_page_still_renders_visibly(tmp_path):
    src = make_big_scan_pdf(tmp_path / "scan.pdf")
    out = str(tmp_path / "searchable.pdf")
    ocr.make_searchable_pdf(src, out)

    doc = pdfium.PdfDocument(out)
    try:
        bitmap = doc.get_page(0).render(scale=72 / 72)
        pil = bitmap.to_pil().convert("L")
    finally:
        doc.close()
    darkest = pil.getextrema()[0]
    pil.close()
    # The drawn text must survive as visible (non-white) pixels.
    assert darkest < 128


@needs_tesseract
def test_make_searchable_from_images_multipage(tmp_path):
    img1 = make_big_text_image("HELLO WORLD 42")
    img2 = make_big_text_image("HELLO WORLD 42")
    sizes = [(img1.width * 72 / DPI, img1.height * 72 / DPI)] * 2
    out = str(tmp_path / "multi.pdf")

    ocr.make_searchable_from_images([img1, img2], sizes, out)
    img1.close()
    img2.close()

    assert len(page_sizes_pdfium(out)) == 2
    assert_contains_ocr_words(extract_text_pdfium(out))
    for (w, h), (ew, eh) in zip(page_sizes_pdfium(out), sizes):
        assert abs(w - ew) <= 1.0
        assert abs(h - eh) <= 1.0


@needs_tesseract
def test_make_searchable_pdf_encrypted_input(tmp_path):
    plain = make_big_scan_pdf(tmp_path / "scan.pdf")
    encrypted = str(tmp_path / "scan-enc.pdf")

    writer = PdfWriter()
    writer.append(PdfReader(plain))
    writer.encrypt(user_password="secret", algorithm="AES-256")
    with open(encrypted, "wb") as fh:
        writer.write(fh)

    out = str(tmp_path / "searchable.pdf")
    ocr.make_searchable_pdf(encrypted, out, password="secret")
    assert_contains_ocr_words(extract_text_pdfium(out))

    with pytest.raises(ValueError):
        ocr.make_searchable_pdf(encrypted, str(tmp_path / "x.pdf"),
                                password="wrong")


@needs_tesseract
def test_make_searchable_pdf_zero_pages(tmp_path):
    """A PDF whose page tree is empty is rejected with ValueError."""
    empty = str(tmp_path / "empty.pdf")
    writer = PdfWriter()
    with open(empty, "wb") as fh:
        writer.write(fh)

    with pytest.raises(ValueError):
        ocr.make_searchable_pdf(empty, str(tmp_path / "out.pdf"))


# --------------------------------------------------- validation (no binary)

def test_make_searchable_from_images_empty_raises(tmp_path):
    with pytest.raises(ValueError):
        ocr.make_searchable_from_images([], [], str(tmp_path / "out.pdf"))


@needs_tesseract
def test_make_searchable_from_images_count_mismatch(tmp_path):
    img = make_big_text_image()
    sizes = [(595.0, 842.0), (595.0, 842.0)]
    with pytest.raises(ValueError):
        ocr.make_searchable_from_images([img], sizes,
                                        str(tmp_path / "out.pdf"))
    img.close()


def test_available_languages_graceful_without_binary(monkeypatch):
    monkeypatch.setattr(ocr, "find_tesseract", lambda tess_path=None: None)
    assert ocr.available_languages() == []


def test_missing_tesseract_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr, "find_tesseract", lambda *a, **k: None)
    img = Image.new("RGB", (10, 10), (255, 255, 255))
    with pytest.raises(RuntimeError):
        ocr.ocr_image_to_text(img)
    with pytest.raises(RuntimeError):
        ocr.make_searchable_from_images([img], [(72.0, 72.0)],
                                        str(tmp_path / "out.pdf"))
    with pytest.raises(RuntimeError):
        ocr.make_searchable_pdf("whatever.pdf", str(tmp_path / "out.pdf"))
    img.close()
