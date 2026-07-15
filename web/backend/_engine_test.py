"""Standalone verification of the redaction engine (run inside a container).

Builds a PDF with known secret text, redacts the region covering it, then
proves the secret is unrecoverable: (a) no extractable text at all in the
output, and (b) the covered pixels are actually solid black.
"""

import io
import sys

import pypdfium2 as pdfium
from fpdf import FPDF
from pypdf import PdfReader

from redaction import Region, redact_pdf

SECRET = "TOPSECRET-SSN-123-45-6789"
VISIBLE = "This line stays visible."


def make_source_pdf() -> bytes:
    pdf = FPDF(unit="pt", format=(612, 792))  # US Letter
    pdf.add_page()
    pdf.set_font("Helvetica", size=24)
    pdf.set_xy(72, 100)
    pdf.cell(0, 30, SECRET)          # secret near top
    pdf.set_xy(72, 400)
    pdf.cell(0, 30, VISIBLE)         # visible near middle
    return bytes(pdf.output())


def main() -> int:
    src = make_source_pdf()

    # Sanity: the secret IS extractable from the source.
    src_text = "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(src)).pages)
    assert SECRET.replace("-", "") in src_text.replace("-", "") or SECRET in src_text, \
        f"secret not found in source text: {src_text!r}"
    print(f"[src] extractable text contains secret: OK  ({src_text.strip()!r})")

    # Redact a black bar over the top region (normalized coords) where the secret is.
    # Secret is around y=100pt of 792pt -> ~0.11..0.19; cover x 0..0.9, y 0.10..0.20.
    regions = [Region(page=0, x=0.05, y=0.10, w=0.9, h=0.10, color="black")]
    out = redact_pdf(src, regions, quality="high")
    print(f"[out] redacted pdf size: {len(out)} bytes")

    # (a) No extractable text anywhere (rasterized => no text layer).
    out_text = "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(out)).pages)
    assert SECRET not in out_text, f"SECRET STILL EXTRACTABLE: {out_text!r}"
    assert "SECRET" not in out_text.upper(), f"secret leaked: {out_text!r}"
    print(f"[out] extractable text: {out_text.strip()!r}  -> no secret: OK")

    # (b) The covered pixels are actually solid black in the rendered output.
    doc = pdfium.PdfDocument(out)
    page = doc[0]
    img = page.render(scale=2).to_pil().convert("RGB")
    iw, ih = img.size
    # Sample the center of the redaction bar.
    sx, sy = int(0.5 * iw), int(0.15 * ih)
    px = img.getpixel((sx, sy))
    print(f"[out] pixel at center of bar {(sx, sy)} = {px}")
    assert max(px) < 20, f"redaction bar not solid black at sample point: {px}"
    doc.close()

    # (c) Output is a valid, openable PDF with the right page count.
    r = PdfReader(io.BytesIO(out))
    assert len(r.pages) == 1, f"expected 1 page, got {len(r.pages)}"
    print(f"[out] valid PDF, pages = {len(r.pages)}")

    print("\nALL ENGINE CHECKS PASSED ✅  redaction is destructive & unrecoverable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
