"""
Export and conversion tools for WorkOnward Read.

Converts PDFs to images, plain text, Word documents, HTML and "text CSV",
builds PDFs from images, and offers two compression strategies (raster
flatten and lossless image re-encoding). Pure business logic: no GUI
imports, plain-English exception messages, data in / data out.

Licensed under GPL-3.0. (c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import base64
import csv
import gc
import html as html_module
import io
import os
import re
from typing import Callable, Optional

from fpdf import FPDF
from PIL import Image

from workonward_read import pdfium_io
from workonward_read.pdfium_io import PDFIUM_LOCK

# Import PPI used by the app when sizing image pages (see document_loader).
IMPORT_PPI = 200

# Splits a text line into "columns" for the text-CSV export: one or more
# tabs, or runs of two or more spaces, are treated as column separators.
_COLUMN_SPLIT = re.compile(r"\t+|\s{2,}")

ProgressCb = Optional[Callable[[int, str], None]]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _report(progress_cb: ProgressCb, done: int, total: int, msg: str) -> None:
    """Invoke progress_cb(pct, msg) if given, never letting it break the job."""
    if progress_cb is None or total <= 0:
        return
    try:
        progress_cb(int(done * 100 / total), msg)
    except Exception:
        pass


def _resolve_pages(total: int, pages) -> list:
    """Validate an optional list of 0-based page indices against total."""
    if pages is None:
        return list(range(total))
    indices = list(pages)
    for idx in indices:
        if not isinstance(idx, int) or idx < 0 or idx >= total:
            raise ValueError(
                f"Page index {idx!r} is out of range (document has {total} pages)."
            )
    return indices


def _a4_fit_scale(width: int, height: int, ppi: int = IMPORT_PPI) -> float:
    """
    Scale factor that fits an image into DIN A4 at `ppi`, mirroring the
    app's import logic in document_loader.load_document. Values >= 1 mean
    the image already fits and must not be resized.
    """
    a4_short_side = round(8.267 * ppi)
    a4_long_side = round(11.693 * ppi)

    if height >= width:  # portrait
        if width / height >= 210 / 297:
            return a4_short_side / width
        return a4_long_side / height
    # landscape
    if width / height >= 297 / 210:
        return a4_long_side / width
    return a4_short_side / height


def _extract_page_text(pdf, index: int) -> str:
    """Extract the full text of one page via a pdfium textpage.

    Holds PDFIUM_LOCK for the whole per-page call sequence (pdfium is not
    thread-safe).
    """
    with PDFIUM_LOCK:
        page = pdf[index]
        textpage = None
        try:
            textpage = page.get_textpage()
            return textpage.get_text_bounded() or ""
        finally:
            if textpage is not None:
                try:
                    textpage.close()
                except Exception:
                    pass
            try:
                page.close()
            except Exception:
                pass


def _paragraphs_per_page(input_path: str, password: Optional[str] = None) -> list:
    """
    Extract text as paragraphs, one list of paragraph strings per page.

    Uses pdfminer.six layout analysis: text lines are collected per page,
    sorted top-to-bottom, and merged into paragraphs. A vertical gap larger
    than 0.6x the current line height starts a new paragraph.
    """
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTTextContainer, LTTextLine
    from pdfminer.pdfdocument import PDFPasswordIncorrect

    if not input_path or not os.path.isfile(input_path):
        raise FileNotFoundError(f"File not found: {input_path}")

    pages = []
    try:
        for page_layout in extract_pages(input_path, password=password or ""):
            lines = []
            for element in page_layout:
                if not isinstance(element, LTTextContainer):
                    continue
                if isinstance(element, LTTextLine):
                    candidates = [element]
                else:
                    candidates = [
                        obj for obj in element if isinstance(obj, LTTextLine)
                    ]
                for line in candidates:
                    text = line.get_text().strip()
                    if text:
                        lines.append((line.y1, line.y0, text))

            # pdfminer's y axis points up: sort top of page first.
            lines.sort(key=lambda item: (-item[0], item[1]))

            paragraphs = []
            current = []
            prev_y0 = None
            for y1, y0, text in lines:
                line_height = max(y1 - y0, 1.0)
                gap = (prev_y0 - y1) if prev_y0 is not None else 0.0
                if current and gap > 0.6 * line_height:
                    paragraphs.append(" ".join(current))
                    current = []
                current.append(text)
                prev_y0 = y0
            if current:
                paragraphs.append(" ".join(current))
            pages.append(paragraphs)
    except PDFPasswordIncorrect as exc:
        raise ValueError(
            "The PDF is encrypted and requires a correct password."
        ) from exc
    return pages


# ---------------------------------------------------------------------------
# PDF -> images / text / docx / html / csv
# ---------------------------------------------------------------------------

def pdf_to_images(input_path: str, out_dir: str, fmt: str = "PNG", dpi: int = 200,
                  pages=None, password: Optional[str] = None,
                  progress_cb: ProgressCb = None) -> list:
    """
    Render PDF pages to image files named '<stem>_page_<n>.<ext>' (n 1-based).

    Rendering is sequential (pdfium is not thread-safe); each page's
    resources are released before the next is rendered to keep memory flat
    on large documents. Rotated pages render in their displayed orientation.

    Args:
        input_path: Source PDF path.
        out_dir: Directory for the image files (created if missing).
        fmt: Pillow format name, e.g. 'PNG', 'JPEG', 'TIFF'.
        dpi: Render resolution.
        pages: Optional list of 0-based page indices; None = all pages.
        password: Password for encrypted PDFs.
        progress_cb: Optional callable(pct:int, msg:str).

    Returns:
        List of written file paths in page order.
    """
    if dpi <= 0:
        raise ValueError("dpi must be a positive number.")
    fmt = (fmt or "PNG").upper()
    if fmt == "JPG":
        fmt = "JPEG"
    ext = "jpg" if fmt == "JPEG" else fmt.lower()

    pdf = pdfium_io.open_pdf(input_path, password)
    written = []
    try:
        with PDFIUM_LOCK:
            total_pages = len(pdf)
        indices = _resolve_pages(total_pages, pages)
        os.makedirs(out_dir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(input_path))[0]
        scale = dpi / 72
        total = len(indices)

        for done, page_index in enumerate(indices, start=1):
            # Render under PDFIUM_LOCK (per page); encode/save outside it.
            pil_image = pdfium_io.render_page_to_pil(pdf, page_index, scale)
            try:
                if fmt == "JPEG" and pil_image.mode != "RGB":
                    converted = pil_image.convert("RGB")
                    pil_image.close()
                    pil_image = converted
                out_path = os.path.join(
                    out_dir, f"{stem}_page_{page_index + 1}.{ext}"
                )
                pil_image.save(out_path, format=fmt)
                written.append(out_path)
            finally:
                try:
                    pil_image.close()
                except Exception:
                    pass
            _report(progress_cb, done, total, f"{done}/{total}")
            if done % 10 == 0:
                gc.collect()
    finally:
        pdfium_io.close_pdf(pdf)
    return written


def pdf_to_text(input_path: str, output: str, password: Optional[str] = None) -> str:
    """
    Extract all text from a PDF to a UTF-8 text file.

    Pages are extracted with pypdfium2 textpages and joined with a
    form-feed character ('\\f'). Returns the output path.
    """
    pdf = pdfium_io.open_pdf(input_path, password)
    try:
        with PDFIUM_LOCK:
            total = len(pdf)
        with open(output, "w", encoding="utf-8") as fh:
            for index in range(total):
                if index > 0:
                    fh.write("\f")
                fh.write(_extract_page_text(pdf, index))
    finally:
        pdfium_io.close_pdf(pdf)
    return output


def pdf_to_docx(input_path: str, output: str, password: Optional[str] = None) -> str:
    """
    Convert a PDF's text content to a Word (.docx) document.

    Text is extracted with pdfminer.six, merged into paragraphs by a
    vertical-gap heuristic (a gap greater than 0.6x the line height starts
    a new paragraph), and written with python-docx. A page break separates
    the content of consecutive PDF pages. Layout, images and tables are
    not reconstructed. Returns the output path.
    """
    from docx import Document

    page_paragraphs = _paragraphs_per_page(input_path, password)

    document = Document()
    for page_index, paragraphs in enumerate(page_paragraphs):
        if page_index > 0:
            document.add_page_break()
        for paragraph in paragraphs:
            document.add_paragraph(paragraph)
    document.save(output)
    return output


def pdf_to_html(input_path: str, output: str, password: Optional[str] = None,
                embed_page_images: bool = False) -> str:
    """
    Convert a PDF to a simple, self-contained semantic HTML file.

    One <section> per page, one <p> per paragraph (same paragraph-merge
    heuristic as pdf_to_docx). All text is HTML-escaped. When
    embed_page_images is True each page is additionally rendered at 100 dpi
    and embedded as a base64 JPEG data URI <img>. Returns the output path.
    """
    page_paragraphs = _paragraphs_per_page(input_path, password)

    page_images = []
    if embed_page_images:
        pdf = pdfium_io.open_pdf(input_path, password)
        try:
            with PDFIUM_LOCK:
                total = len(pdf)
            for index in range(total):
                # Render under PDFIUM_LOCK (per page); encode outside it.
                pil_image = pdfium_io.render_page_to_pil(pdf, index,
                                                         scale=100 / 72)
                try:
                    if pil_image.mode != "RGB":
                        converted = pil_image.convert("RGB")
                        pil_image.close()
                        pil_image = converted
                    with io.BytesIO() as buffer:
                        pil_image.save(buffer, format="JPEG", quality=80)
                        page_images.append(
                            base64.b64encode(buffer.getvalue()).decode("ascii")
                        )
                finally:
                    try:
                        pil_image.close()
                    except Exception:
                        pass
        finally:
            pdfium_io.close_pdf(pdf)

    stem = os.path.splitext(os.path.basename(input_path))[0]
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        f"<title>{html_module.escape(stem)}</title>",
        "</head>",
        "<body>",
    ]
    for page_index, paragraphs in enumerate(page_paragraphs):
        parts.append(f'<section data-page="{page_index + 1}">')
        for paragraph in paragraphs:
            parts.append(f"<p>{html_module.escape(paragraph)}</p>")
        if embed_page_images and page_index < len(page_images):
            parts.append(
                '<img alt="" style="max-width:100%" '
                f'src="data:image/jpeg;base64,{page_images[page_index]}">'
            )
        parts.append("</section>")
    parts.append("</body>")
    parts.append("</html>")

    with open(output, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    return output


def pdf_to_csv_text(input_path: str, output: str, password: Optional[str] = None) -> str:
    """
    Export PDF text lines as a "text CSV" file.

    This is NOT table reconstruction: every text line becomes one CSV row,
    split into columns at tabs or runs of two-or-more spaces. It is useful
    for roughly tabular text but makes no attempt to detect real table
    geometry. Returns the output path.
    """
    pdf = pdfium_io.open_pdf(input_path, password)
    try:
        with PDFIUM_LOCK:
            total = len(pdf)
        with open(output, "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            for index in range(total):
                text = _extract_page_text(pdf, index)
                for line in text.splitlines():
                    cells = [cell.strip() for cell in _COLUMN_SPLIT.split(line)]
                    cells = [cell for cell in cells if cell]
                    if cells:
                        writer.writerow(cells)
    finally:
        pdfium_io.close_pdf(pdf)
    return output


# ---------------------------------------------------------------------------
# Images -> PDF
# ---------------------------------------------------------------------------

def images_to_pdf(image_paths, output: str) -> str:
    """
    Build a PDF with each image on its own page, sized like the app import.

    Mirrors document_loader.load_document: images are treated as
    IMPORT_PPI (200) PPI material; images larger than DIN A4 at that PPI
    are scaled down to fit A4 (LANCZOS), smaller images keep their size.
    The page size in points is int(px / 200 * 72) per axis. Returns the
    output path.
    """
    paths = list(image_paths)
    if not paths:
        raise ValueError("No images given to convert.")

    pdf = FPDF(unit="pt")
    pdf.set_auto_page_break(False)

    for path in paths:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"File not found: {path}")
        with Image.open(path) as source:
            width, height = source.size
            scale_factor = _a4_fit_scale(width, height, IMPORT_PPI)
            if scale_factor < 1:
                width = int(width * scale_factor)
                height = int(height * scale_factor)
                page_image = source.resize(
                    (width, height), resample=Image.Resampling.LANCZOS
                )
            else:
                page_image = source.copy()

        try:
            if page_image.mode not in ("RGB", "L"):
                converted = page_image.convert("RGB")
                page_image.close()
                page_image = converted

            width_pt = int(width / IMPORT_PPI * 72)
            height_pt = int(height / IMPORT_PPI * 72)
            pdf.add_page(format=(width_pt, height_pt))
            pdf.image(page_image, x=0, y=0, w=width_pt, h=height_pt)
        finally:
            try:
                page_image.close()
            except Exception:
                pass

    pdf.output(output)
    return output


# ---------------------------------------------------------------------------
# Compression
# ---------------------------------------------------------------------------

def compress_pdf_raster(input_path: str, output: str, dpi: int = 110,
                        jpeg_quality: int = 85, password: Optional[str] = None,
                        progress_cb: ProgressCb = None) -> str:
    """
    Compress a PDF by re-rasterizing every page.

    Each page is rendered with pypdfium2 at `dpi`, JPEG-encoded at
    `jpeg_quality`, and placed on a page of the ORIGINAL size in points in
    a rebuilt, image-only PDF. WARNING: this flattens everything — text,
    vector graphics, links, form fields and the text layer are all replaced
    by a single page image; text is no longer selectable or extractable.
    Returns the output path.
    """
    if dpi <= 0:
        raise ValueError("dpi must be a positive number.")

    pdf = pdfium_io.open_pdf(input_path, password)
    try:
        with PDFIUM_LOCK:
            total = len(pdf)
        if total == 0:
            raise ValueError("The PDF has no pages.")

        out_pdf = FPDF(unit="pt")
        out_pdf.set_auto_page_break(False)
        scale = dpi / 72

        for index in range(total):
            # pdfium work under the (per-page) lock; JPEG re-encode outside.
            width_pt, height_pt = pdfium_io.get_page_size(pdf, index)
            pil_image = pdfium_io.render_page_to_pil(pdf, index, scale)
            try:
                if pil_image.mode != "RGB":
                    converted = pil_image.convert("RGB")
                    pil_image.close()
                    pil_image = converted
                with io.BytesIO() as buffer:
                    pil_image.save(
                        buffer, format="JPEG", quality=jpeg_quality, optimize=True
                    )
                    buffer.seek(0)
                    out_pdf.add_page(format=(width_pt, height_pt))
                    out_pdf.image(buffer, x=0, y=0, w=width_pt, h=height_pt)
            finally:
                try:
                    pil_image.close()
                except Exception:
                    pass
            _report(progress_cb, index + 1, total, f"{index + 1}/{total}")
            if (index + 1) % 10 == 0:
                gc.collect()

        out_pdf.output(output)
    finally:
        pdfium_io.close_pdf(pdf)
    return output


def compress_pdf_lossless(input_path: str, output: str, image_quality: int = 75,
                          password: Optional[str] = None) -> dict:
    """
    Compress a PDF while keeping its text layer intact.

    Embedded images are re-encoded as JPEG at `image_quality` via
    pypdf's ImageFile.replace, then every page's content streams are
    Flate-compressed. Images that cannot be re-encoded (e.g. 1-bit masks
    or exotic color spaces) are skipped gracefully and kept as-is.

    Returns:
        dict with 'before_bytes' and 'after_bytes' file sizes. Note the
        result can be larger than the input for already-optimal files.
    """
    from pypdf import PdfReader, PdfWriter

    if not input_path or not os.path.isfile(input_path):
        raise FileNotFoundError(f"File not found: {input_path}")

    reader = PdfReader(input_path)
    if reader.is_encrypted:
        if not password:
            raise ValueError(
                "The PDF is encrypted and requires a correct password."
            )
        if not reader.decrypt(password):
            raise ValueError(
                "The PDF is encrypted and requires a correct password."
            )

    writer = PdfWriter()
    writer.append(reader)

    for page in writer.pages:
        try:
            images = list(page.images)
        except Exception:
            images = []
        for image_file in images:
            try:
                pil_image = image_file.image
                if pil_image is None:
                    continue
                if pil_image.mode not in ("RGB", "L"):
                    pil_image = pil_image.convert("RGB")
                image_file.replace(pil_image, quality=image_quality)
            except Exception:
                # Keep the original image if re-encoding is not possible
                # (1-bit stencils, masks, unusual color spaces, ...).
                continue
        try:
            page.compress_content_streams()
        except Exception:
            pass

    with open(output, "wb") as fh:
        writer.write(fh)

    return {
        "before_bytes": os.path.getsize(input_path),
        "after_bytes": os.path.getsize(output),
    }


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------

def batch_apply(tool_fn, input_paths, out_dir: str, progress_cb: ProgressCb = None,
                out_ext: Optional[str] = None, **kwargs) -> list:
    """
    Apply a conversion tool to many inputs, collecting per-file results.

    For every input, tool_fn is called as tool_fn(input_path, output_target,
    **kwargs). output_target is out_dir/<stem><out_ext>; out_ext defaults to
    the input's own extension (pass e.g. '.txt' for pdf_to_text, '.docx'
    for pdf_to_docx, or '' for tools such as pdf_to_images whose second
    argument is a directory). Colliding stems get a numeric suffix.
    Failures never abort the batch.

    Returns:
        list of (input_path, output|None, error|None) tuples in input
        order. On success output is the tool's returned path (if it returns
        a string) or output_target, and error is None; on failure output is
        None and error is the exception message.
    """
    paths = list(input_paths)
    os.makedirs(out_dir, exist_ok=True)

    results = []
    used_targets = set()
    total = len(paths)

    for index, input_path in enumerate(paths, start=1):
        stem = os.path.splitext(os.path.basename(input_path))[0]
        ext = out_ext if out_ext is not None else os.path.splitext(input_path)[1]
        target = os.path.join(out_dir, stem + ext)
        bump = 1
        while target in used_targets:
            bump += 1
            target = os.path.join(out_dir, f"{stem}_{bump}{ext}")
        used_targets.add(target)

        try:
            result = tool_fn(input_path, target, **kwargs)
            output = result if isinstance(result, str) else target
            results.append((input_path, output, None))
        except Exception as exc:
            results.append((input_path, None, str(exc)))
        _report(progress_cb, index, total, os.path.basename(input_path))

    return results
