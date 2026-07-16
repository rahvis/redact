"""
Secure PDF redaction engine for WorkOnward Read Web.

Faithful, stateless port of the desktop WorkOnward Read redaction model:

    1. Each PDF page is RASTERIZED to a bitmap with pypdfium2. Rasterizing
       destroys the text layer and any invisible/hidden layers — the page
       becomes pixels only, so nothing is selectable or extractable.
    2. Redaction bars are painted as SOLID FILLED PIXELS onto that bitmap
       (Pillow ImageDraw.rectangle with a solid fill). The covered pixels are
       overwritten and gone — not a separate overlay object that could be
       removed.
    3. The bitmaps are reassembled into a brand-new PDF with fpdf2, one image
       per page and NO text layer.

The result: covered content cannot be recovered by copy/paste, pdftotext,
"remove overlay", or OCR of the redacted area. This mirrors (and for the whole
page, exceeds) Adobe Acrobat's "apply redactions" guarantee.

Everything runs in memory; nothing is written to disk.
"""

from __future__ import annotations

import io
import threading
from dataclasses import dataclass
from typing import Iterable

import pypdfium2 as pdfium
from PIL import Image, ImageDraw
from fpdf import FPDF

# pdfium is NOT thread-safe. FastAPI runs blocking work in a threadpool, so
# concurrent redactions could otherwise call into pdfium simultaneously and
# crash. Serialize all pdfium access through this lock.
_PDFIUM_LOCK = threading.Lock()

# Render resolution (DPI). Higher = crisper output, larger file.
#   high       -> 150 DPI, JPEG q90   (visually faithful)
#   compressed -> 100 DPI, JPEG q80   (smaller file, the desktop app's "low" mode)
QUALITY_PRESETS = {
    "high": {"dpi": 150, "jpeg_quality": 90},
    "compressed": {"dpi": 100, "jpeg_quality": 80},
}

# Guards against a "render bomb": a tiny PDF can declare a huge MediaBox (the PDF
# spec allows up to 14400 pt = 200 in per side), which would rasterize to a
# multi-gigabyte bitmap and OOM the process. We cap the rendered pixels per page
# (downscaling that page if needed — it still redacts correctly, just at lower
# resolution) and cap the total page count.
MAX_RENDER_SIDE_PX = 5000     # longest side of any rendered page
MAX_PAGES = 2000              # refuse absurd page counts

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)


class RedactionError(Exception):
    """Raised for user-correctable problems (bad password, corrupt PDF)."""


class PasswordRequired(RedactionError):
    """PDF is encrypted and no / an incorrect password was supplied."""


@dataclass(frozen=True)
class Region:
    """A single redaction bar, in normalized page coordinates.

    x, y, w, h are fractions (0..1) of the page's *rendered* width/height, with
    the origin at the TOP-LEFT of the page as displayed (rotation applied) —
    exactly how pdf.js reports viewport coordinates on the frontend.
    """

    page: int
    x: float
    y: float
    w: float
    h: float
    color: str = "black"  # "black" | "white"

    @property
    def fill(self) -> tuple[int, int, int]:
        return WHITE if self.color == "white" else BLACK


def _open_document(pdf_bytes: bytes, password: str | None) -> pdfium.PdfDocument:
    try:
        # pypdfium2 accepts a raw bytes buffer directly (no temp file needed).
        pdf = pdfium.PdfDocument(pdf_bytes, password=password or None)
        # Force a read so encryption errors surface here, not mid-render.
        _ = len(pdf)
        return pdf
    except pdfium.PdfiumError as exc:
        msg = str(exc).lower()
        if "password" in msg or "encrypt" in msg or "auth" in msg:
            raise PasswordRequired(
                "This PDF is password-protected. Provide the correct password."
            ) from exc
        raise RedactionError(f"Could not open PDF: {exc}") from exc


def _regions_by_page(regions: Iterable[Region], page_count: int) -> dict[int, list[Region]]:
    grouped: dict[int, list[Region]] = {}
    for r in regions:
        if 0 <= r.page < page_count:
            grouped.setdefault(r.page, []).append(r)
    return grouped


def _paint_regions(image: Image.Image, regions: list[Region]) -> None:
    """Overwrite the pixels under each region with a solid fill (destructive)."""
    draw = ImageDraw.Draw(image)
    iw, ih = image.width, image.height
    for r in regions:
        # Normalized (0..1) -> absolute pixel coordinates at the render DPI.
        x0 = max(0.0, min(1.0, r.x)) * iw
        y0 = max(0.0, min(1.0, r.y)) * ih
        x1 = max(0.0, min(1.0, r.x + r.w)) * iw
        y1 = max(0.0, min(1.0, r.y + r.h)) * ih
        if x1 <= x0 or y1 <= y0:
            continue  # zero-area / inverted box
        draw.rectangle([x0, y0, x1, y1], fill=r.fill)


def redact_pdf(
    pdf_bytes: bytes,
    regions: Iterable[Region],
    quality: str = "high",
    password: str | None = None,
) -> bytes:
    """Redact `pdf_bytes` and return the bytes of a new, flattened PDF.

    Args:
        pdf_bytes: the uploaded PDF.
        regions:   redaction bars in normalized coordinates.
        quality:   "high" or "compressed".
        password:  password for encrypted PDFs (optional).

    Returns:
        The redacted PDF as bytes (every page is a flat image; no text layer).
    """
    preset = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["high"])
    dpi = preset["dpi"]
    jpeg_quality = preset["jpeg_quality"]
    scale = dpi / 72.0

    with _PDFIUM_LOCK:
        return _redact_locked(pdf_bytes, regions, dpi, jpeg_quality, scale, password)


def _redact_locked(pdf_bytes, regions, dpi, jpeg_quality, scale, password) -> bytes:
    pdf = _open_document(pdf_bytes, password)
    try:
        page_count = len(pdf)
        if page_count == 0:
            raise RedactionError("The PDF has no pages.")
        if page_count > MAX_PAGES:
            raise RedactionError(
                f"The PDF has {page_count} pages; the limit is {MAX_PAGES}."
            )

        grouped = _regions_by_page(regions, page_count)

        out = FPDF(unit="pt")
        out.set_creator("WorkOnward Read Web")
        out.set_producer("WorkOnward Read Web (pypdfium2 + Pillow + fpdf2)")
        # Do NOT copy the source document's metadata/title — start clean.

        for i in range(page_count):
            page = pdf[i]
            try:
                # Cap the render resolution so an oversized MediaBox can't blow
                # up memory. Downscaling still yields a correct redaction.
                w_pt0, h_pt0 = page.get_size()
                eff_scale = scale
                longest_px = max(w_pt0, h_pt0) * scale
                if longest_px > MAX_RENDER_SIDE_PX:
                    eff_scale = scale * (MAX_RENDER_SIDE_PX / longest_px)

                # Rasterize the page AS DISPLAYED (rotation applied by pdfium).
                image = page.render(scale=eff_scale).to_pil()
                if image.mode != "RGB":
                    rgb = image.convert("RGB")
                    image.close()
                    image = rgb

                _paint_regions(image, grouped.get(i, []))

                # Derive page size (points) from the rendered bitmap and the
                # scale actually used, so the output page matches the image
                # exactly regardless of page rotation or downscaling.
                w_pt = image.width / eff_scale
                h_pt = image.height / eff_scale

                with io.BytesIO() as buf:
                    image.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
                    jpeg_bytes = buf.getvalue()
                image.close()

                out.add_page(format=(w_pt, h_pt))
                out.image(jpeg_bytes, x=0, y=0, w=w_pt, h=h_pt)
                del jpeg_bytes
            finally:
                page.close()

        result = out.output()  # fpdf2 returns a bytearray
        return bytes(result)
    finally:
        pdf.close()


def page_dimensions(pdf_bytes: bytes, password: str | None = None) -> list[dict]:
    """Return per-page rendered aspect info for validation/inspection.

    Sizes are in PDF points at the page's displayed orientation.
    """
    with _PDFIUM_LOCK:
        return _page_dimensions_locked(pdf_bytes, password)


def _page_dimensions_locked(pdf_bytes, password) -> list[dict]:
    pdf = _open_document(pdf_bytes, password)
    try:
        pages = []
        for i in range(len(pdf)):
            page = pdf[i]
            try:
                w, h = page.get_size()
                pages.append({"page": i, "width": w, "height": h})
            finally:
                page.close()
        return pages
    finally:
        pdf.close()
