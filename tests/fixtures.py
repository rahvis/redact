"""Synthesized test fixtures for the WorkOnward Read test suite.

All fixtures are generated at test time with fpdf2 / Pillow / pypdf —
no binary files are committed to the repository.
"""

import io

from fpdf import FPDF
from PIL import Image, ImageDraw


def make_pdf(path, pages=3, texts=None, size=(595.28, 841.89)):
    """Create a text PDF with `pages` pages. texts[i] is written on page i."""
    pdf = FPDF(unit="pt", format=size)
    pdf.set_auto_page_break(False)
    for i in range(pages):
        pdf.add_page()
        pdf.set_font("helvetica", size=14)
        text = texts[i] if texts and i < len(texts) else f"Page {i + 1} content"
        pdf.set_xy(72, 72)
        pdf.multi_cell(size[0] - 144, 18, text)
    pdf.output(str(path))
    return str(path)


def make_text_pdf(path, text="The quick brown fox jumps over the lazy dog.", pages=1):
    return make_pdf(path, pages=pages, texts=[text] * pages)


def make_encrypted_pdf(path, user_password="secret", pages=2, owner_password=None):
    """Create an AES-encrypted PDF via pypdf."""
    from pypdf import PdfReader, PdfWriter

    plain = io.BytesIO()
    pdf = FPDF(unit="pt")
    for i in range(pages):
        pdf.add_page()
        pdf.set_font("helvetica", size=14)
        pdf.set_xy(72, 72)
        pdf.cell(200, 18, f"Encrypted page {i + 1}")
    plain.write(pdf.output())
    plain.seek(0)

    writer = PdfWriter()
    writer.append(PdfReader(plain))
    writer.encrypt(
        user_password=user_password,
        owner_password=owner_password or user_password,
        algorithm="AES-256",
    )
    with open(path, "wb") as fh:
        writer.write(fh)
    return str(path)


def make_form_pdf(path):
    """Create a PDF containing simple AcroForm text fields."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import (
        ArrayObject,
        BooleanObject,
        DictionaryObject,
        NameObject,
        NumberObject,
        TextStringObject,
    )

    plain = io.BytesIO()
    pdf = FPDF(unit="pt")
    pdf.add_page()
    pdf.set_font("helvetica", size=12)
    pdf.set_xy(72, 72)
    pdf.cell(120, 16, "Name:")
    pdf.set_xy(72, 110)
    pdf.cell(120, 16, "City:")
    plain.write(pdf.output())
    plain.seek(0)

    writer = PdfWriter()
    writer.append(PdfReader(plain))
    page = writer.pages[0]

    fields = ArrayObject()
    for name, rect in (
        ("name", [200, 700, 400, 720]),
        ("city", [200, 660, 400, 680]),
    ):
        annot = DictionaryObject(
            {
                NameObject("/FT"): NameObject("/Tx"),
                NameObject("/T"): TextStringObject(name),
                NameObject("/V"): TextStringObject(""),
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/Widget"),
                NameObject("/Rect"): ArrayObject(NumberObject(v) for v in rect),
                NameObject("/Ff"): NumberObject(0),
            }
        )
        ref = writer._add_object(annot)
        annot[NameObject("/P")] = page.indirect_reference
        if "/Annots" not in page:
            page[NameObject("/Annots")] = ArrayObject()
        page["/Annots"].append(ref)
        fields.append(ref)

    writer._root_object[NameObject("/AcroForm")] = DictionaryObject(
        {
            NameObject("/Fields"): fields,
            NameObject("/NeedAppearances"): BooleanObject(True),
        }
    )
    with open(path, "wb") as fh:
        writer.write(fh)
    return str(path)


def make_image(path, size=(400, 300), color=(255, 255, 255), text=None):
    """Create a PNG/JPEG image, optionally with large black text drawn on it."""
    img = Image.new("RGB", size, color)
    if text:
        draw = ImageDraw.Draw(img)
        draw.text((20, size[1] // 2 - 10), text, fill=(0, 0, 0))
    img.save(str(path))
    return str(path)


def make_scan_pdf(path, text="SCANNED DOCUMENT TEXT", dpi=200):
    """Create an image-only PDF (no text layer) that simulates a scan."""
    w_px, h_px = int(8.27 * dpi), int(11.69 * dpi)  # A4
    img = Image.new("RGB", (w_px, h_px), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    # Large simple glyphs so OCR has a fair chance
    draw.text((w_px // 8, h_px // 3), text, fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    buf.seek(0)

    pdf = FPDF(unit="pt", format=(w_px * 72 / dpi, h_px * 72 / dpi))
    pdf.add_page()
    pdf.image(buf, x=0, y=0, w=pdf.w)
    pdf.output(str(path))
    return str(path)
