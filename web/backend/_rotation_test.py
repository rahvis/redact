"""Verify redaction is correct on ROTATED pages.

A rotated page (/Rotate 90) is the classic place where a normalized-coordinate
redaction can land on the wrong content if the render orientations disagree.

This test:
  1. builds a portrait page with SECRET text,
  2. rotates it 90 degrees (/Rotate),
  3. renders it the way the backend does (pypdfium2, rotation applied) and
     locates where SECRET actually appears in the rendered raster,
  4. redacts exactly that normalized box via the engine,
  5. confirms the output page has the ROTATED aspect ratio, the box is solid
     black, and no text is extractable.
"""

import io
import sys

import pypdfium2 as pdfium
from fpdf import FPDF
from pypdf import PdfReader, PdfWriter
from PIL import Image

from redaction import Region, redact_pdf

SECRET = "SECRET-ROTATED-9-8-7"


def make_rotated_pdf() -> bytes:
    # Portrait page, SECRET near the top.
    pdf = FPDF(unit="pt", format=(612, 792))
    pdf.add_page()
    pdf.set_font("Helvetica", size=40)
    pdf.set_xy(72, 90)
    pdf.cell(0, 40, SECRET)
    src = bytes(pdf.output())

    # Apply /Rotate 90.
    reader = PdfReader(io.BytesIO(src))
    writer = PdfWriter()
    page = reader.pages[0]
    page.rotate(90)
    writer.add_page(page)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def secret_bbox_normalized(pdf_bytes) -> tuple:
    """Render as the backend does and find SECRET's normalized bbox."""
    doc = pdfium.PdfDocument(pdf_bytes)
    page = doc[0]
    img = page.render(scale=2).to_pil().convert("L")
    doc.close()
    w, h = img.size
    # Find dark (text) pixels.
    px = img.load()
    minx, miny, maxx, maxy = w, h, 0, 0
    found = False
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            if px[x, y] < 100:
                found = True
                minx, miny = min(minx, x), min(miny, y)
                maxx, maxy = max(maxx, x), max(maxy, y)
    if not found:
        raise AssertionError("no text pixels found in rendered rotated page")
    # Pad a little and normalize.
    pad = 6
    return (
        max(0, (minx - pad)) / w,
        max(0, (miny - pad)) / h,
        min(w, (maxx + pad)) / w,
        min(h, (maxy + pad)) / h,
        w,
        h,
    )


def main() -> int:
    src = make_rotated_pdf()

    # Sanity: rotated source is landscape when displayed, and leaks the secret.
    doc = pdfium.PdfDocument(src)
    rw, rh = doc[0].render(scale=1).to_pil().size
    doc.close()
    print(f"[src] rendered (displayed) size = {rw}x{rh} (landscape if w>h): {'OK' if rw > rh else 'UNEXPECTED'}")
    src_text = "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(src)).pages)
    assert SECRET.replace("-", "") in src_text.replace("-", ""), f"secret missing in source: {src_text!r}"
    print(f"[src] secret extractable: OK ({src_text.strip()!r})")

    nx0, ny0, nx1, ny1, rimg_w, rimg_h = secret_bbox_normalized(src)
    region = Region(page=0, x=nx0, y=ny0, w=nx1 - nx0, h=ny1 - ny0, color="black")
    print(f"[loc] SECRET normalized bbox on rendered page: x={nx0:.3f} y={ny0:.3f} w={region.w:.3f} h={region.h:.3f}")

    out = redact_pdf(src, [region], quality="high")

    # (1) output page has ROTATED (landscape) aspect
    doc2 = pdfium.PdfDocument(out)
    out_img = doc2[0].render(scale=2).to_pil().convert("RGB")
    ow, oh = out_img.size
    assert ow > oh, f"output not landscape (rotation lost): {ow}x{oh}"
    print(f"[out] output page is landscape {ow}x{oh}: OK (rotation preserved)")

    # (2) the SECRET region is now solid black
    cx = int((nx0 + nx1) / 2 * ow)
    cy = int((ny0 + ny1) / 2 * oh)
    px = out_img.getpixel((cx, cy))
    assert max(px) < 25, f"secret region not covered on rotated page: {px}"
    print(f"[out] SECRET region center pixel {px}: OK (covered)")
    doc2.close()

    # (3) no extractable text
    out_text = "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(out)).pages)
    assert SECRET not in out_text and out_text.strip() == "", f"leak: {out_text!r}"
    print(f"[out] no extractable text: OK")

    print("\nROTATION REDACTION CORRECT ✅  bar lands on the secret and content is gone")
    return 0


if __name__ == "__main__":
    sys.exit(main())
