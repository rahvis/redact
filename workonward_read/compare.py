"""
Visual and textual PDF comparison for WorkOnward Read.

Renders two PDFs page by page, computes per-page pixel differences with
bounding-box regions (pure Python clustering, no numpy), produces a unified
text diff, and can export a side-by-side landscape diff report PDF.

Business module: GUI-toolkit-free per the layering rule.

License: GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import difflib
import os
from dataclasses import dataclass, field

import pypdfium2 as pdfium
from fpdf import FPDF
from PIL import Image, ImageChops

# Side length of the clustering grid cells in rendered pixels.
_CELL = 16


@dataclass
class PageDiff:
    """Difference summary for one page pair.

    Attributes:
        page_index: 0-based page number.
        changed_ratio: Fraction of pixels (0.0 .. 1.0) that differ.
        regions_px: Bounding boxes ``[x0, y0, x1, y1]`` of changed areas in
            rendered pixel space at the dpi used for the comparison.
    """

    page_index: int
    changed_ratio: float
    regions_px: list = field(default_factory=list)


@dataclass
class CompareResult:
    """Result of comparing two PDFs page by page."""

    page_count_a: int
    page_count_b: int
    pages: list = field(default_factory=list)  # list[PageDiff]
    identical: bool = False


def _open_pdf(pdf_path, password=None):
    """Open a PDF with pypdfium2, translating password failures to ValueError."""
    try:
        if password:
            return pdfium.PdfDocument(pdf_path, password=password)
        return pdfium.PdfDocument(pdf_path)
    except pdfium.PdfiumError as exc:
        message = str(exc).lower()
        if "password" in message or "encrypt" in message:
            raise ValueError(
                "The PDF is encrypted and requires a valid password."
            ) from exc
        raise


def _render_page(pdf, page_index, dpi, grayscale=True):
    """Render one page to a PIL image at the given dpi."""
    page = pdf[page_index]
    try:
        image = page.render(scale=dpi / 72.0, grayscale=grayscale).to_pil()
        if grayscale and image.mode != "L":
            converted = image.convert("L")
            image.close()
            image = converted
        return image
    finally:
        page.close()


def _pad_to(image, size):
    """Pad a grayscale image to ``size`` with white; no-op if already sized."""
    if image.size == size:
        return image
    canvas = Image.new("L", size, 255)
    canvas.paste(image, (0, 0))
    image.close()
    return canvas


def _find_regions(mask):
    """Cluster a binary difference mask into bounding boxes.

    The mask (mode "L", values 0 or 255) is scanned on a 16x16 pixel grid;
    cells containing at least one changed pixel are merged with adjacent
    changed cells (8-connectivity) and each connected component is reported
    as one bounding box ``[x0, y0, x1, y1]`` clipped to the image.
    """
    width, height = mask.size
    data = mask.tobytes()

    cells = set()
    for y in range(height):
        row = data[y * width:(y + 1) * width]
        grid_y = y // _CELL
        x = row.find(255)
        while x != -1:
            grid_x = x // _CELL
            cells.add((grid_x, grid_y))
            # Skip the rest of this cell; look again from the next cell on.
            x = row.find(255, (grid_x + 1) * _CELL)

    regions = []
    remaining = set(cells)
    while remaining:
        seed = remaining.pop()
        stack = [seed]
        component = [seed]
        while stack:
            cx, cy = stack.pop()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    neighbor = (cx + dx, cy + dy)
                    if neighbor in remaining:
                        remaining.discard(neighbor)
                        stack.append(neighbor)
                        component.append(neighbor)
        xs = [c[0] for c in component]
        ys = [c[1] for c in component]
        regions.append([
            min(xs) * _CELL,
            min(ys) * _CELL,
            min((max(xs) + 1) * _CELL, width),
            min((max(ys) + 1) * _CELL, height),
        ])

    regions.sort()
    return regions


def compare_pdfs(path_a, path_b, dpi=100, threshold=24, password_a=None,
                 password_b=None, progress_cb=None):
    """Compare two PDFs visually, page by page.

    Both documents are rendered grayscale at ``dpi``; page pairs are padded
    to a common canvas (white background), differenced pixel-wise and
    thresholded. Changed pixels are clustered into bounding-box regions.
    Pages present in only one document count as fully changed.

    Args:
        path_a: Path to the first PDF.
        path_b: Path to the second PDF.
        dpi: Render resolution for the comparison.
        threshold: Grayscale delta (0-255) above which a pixel counts as changed.
        password_a: Optional password for the first PDF.
        password_b: Optional password for the second PDF.
        progress_cb: Optional ``progress_cb(pct, msg)`` callback.

    Returns:
        CompareResult: Per-page diffs; ``identical`` is True only when both
        page counts match and no page has any changed pixel.

    Raises:
        ValueError: If a PDF is encrypted and its password is missing or wrong.
    """
    pdf_a = _open_pdf(path_a, password_a)
    try:
        pdf_b = _open_pdf(path_b, password_b)
        try:
            count_a = len(pdf_a)
            count_b = len(pdf_b)
            total = max(count_a, count_b)
            pages = []
            identical = count_a == count_b

            for page_index in range(total):
                if page_index < count_a and page_index < count_b:
                    image_a = _render_page(pdf_a, page_index, dpi)
                    image_b = _render_page(pdf_b, page_index, dpi)
                    size = (max(image_a.width, image_b.width),
                            max(image_a.height, image_b.height))
                    image_a = _pad_to(image_a, size)
                    image_b = _pad_to(image_b, size)

                    diff = ImageChops.difference(image_a, image_b)
                    mask = diff.point(lambda p: 255 if p > threshold else 0)

                    changed_pixels = mask.histogram()[255]
                    total_pixels = size[0] * size[1]
                    ratio = (changed_pixels / total_pixels) if total_pixels else 0.0
                    regions = _find_regions(mask) if changed_pixels else []

                    image_a.close()
                    image_b.close()
                    diff.close()
                    mask.close()
                else:
                    # Extra page in exactly one document: fully changed.
                    source = pdf_a if page_index < count_a else pdf_b
                    page = source[page_index]
                    try:
                        width_pt, height_pt = page.get_size()
                    finally:
                        page.close()
                    width_px = max(1, round(width_pt * dpi / 72.0))
                    height_px = max(1, round(height_pt * dpi / 72.0))
                    ratio = 1.0
                    regions = [[0, 0, width_px, height_px]]

                if ratio > 0:
                    identical = False
                pages.append(PageDiff(page_index=page_index,
                                      changed_ratio=ratio,
                                      regions_px=regions))

                if progress_cb:
                    progress_cb(int((page_index + 1) * 100 / total),
                                f"{page_index + 1}/{total}")

            return CompareResult(page_count_a=count_a, page_count_b=count_b,
                                 pages=pages, identical=identical)
        finally:
            pdf_b.close()
    finally:
        pdf_a.close()


def text_diff(path_a, path_b, password_a=None, password_b=None):
    """Produce a unified diff of the extracted text of two PDFs.

    Text is extracted with pypdfium2 page by page and split into lines;
    the returned list holds the unified-diff lines (no trailing newlines).

    Raises:
        ValueError: If a PDF is encrypted and its password is missing or wrong.
    """
    def extract_lines(pdf_path, password):
        pdf = _open_pdf(pdf_path, password)
        try:
            lines = []
            for page_index in range(len(pdf)):
                page = pdf[page_index]
                textpage = None
                try:
                    textpage = page.get_textpage()
                    lines.extend(textpage.get_text_range().splitlines())
                finally:
                    if textpage is not None:
                        textpage.close()
                    page.close()
            return lines
        finally:
            pdf.close()

    lines_a = extract_lines(path_a, password_a)
    lines_b = extract_lines(path_b, password_b)
    return list(difflib.unified_diff(
        lines_a, lines_b,
        fromfile=os.path.basename(path_a),
        tofile=os.path.basename(path_b),
        lineterm="",
    ))


def export_diff_report(result, path_a, path_b, output_pdf, dpi=100,
                       password_a=None, password_b=None):
    """Export a side-by-side visual diff report PDF.

    One landscape page per compared page: the page from document A on the
    left, the page from document B on the right, red rectangles drawn over
    the diff regions, and a header with the filenames and changed
    percentage. Pages missing from one document show an empty red frame on
    that side.

    Args:
        result: CompareResult from :func:`compare_pdfs`. For the red
            rectangles to line up, ``dpi`` must equal the dpi used there.
        path_a: Path to the first PDF.
        path_b: Path to the second PDF.
        output_pdf: Destination path for the report.
        dpi: Render resolution; must match the compare dpi.
        password_a: Optional password for the first PDF.
        password_b: Optional password for the second PDF.
    """
    pdf_a = _open_pdf(path_a, password_a)
    try:
        pdf_b = _open_pdf(path_b, password_b)
        try:
            report = FPDF(orientation="landscape", unit="pt", format="A4")
            report.set_auto_page_break(False)

            margin = 24.0
            gutter = 12.0
            header_height = 26.0
            total = max(result.page_count_a, result.page_count_b)

            for page_index in range(total):
                report.add_page()
                page_w = report.w
                page_h = report.h
                half_width = (page_w - 2 * margin - gutter) / 2.0
                avail_height = page_h - margin - header_height - margin

                diff = (result.pages[page_index]
                        if page_index < len(result.pages) else None)
                changed_pct = diff.changed_ratio * 100.0 if diff else 0.0

                report.set_font("helvetica", size=10)
                report.set_text_color(0, 0, 0)
                report.set_xy(margin, margin)
                header = (f"{os.path.basename(path_a)}  vs  "
                          f"{os.path.basename(path_b)}  |  "
                          f"{page_index + 1}/{total}  |  "
                          f"{changed_pct:.1f}%")
                report.cell(page_w - 2 * margin, 12, header)

                sides = ((pdf_a, result.page_count_a),
                         (pdf_b, result.page_count_b))
                for side, (source, count) in enumerate(sides):
                    x_off = margin + side * (half_width + gutter)
                    y_off = margin + header_height
                    report.set_draw_color(255, 0, 0)
                    report.set_line_width(1.5)
                    if page_index < count:
                        image = _render_page(source, page_index, dpi,
                                             grayscale=False)
                        try:
                            scale = min(half_width / image.width,
                                        avail_height / image.height)
                            disp_w = image.width * scale
                            disp_h = image.height * scale
                            report.image(image, x=x_off, y=y_off,
                                         w=disp_w, h=disp_h)
                            if diff:
                                for rx0, ry0, rx1, ry1 in diff.regions_px:
                                    report.rect(x_off + rx0 * scale,
                                                y_off + ry0 * scale,
                                                (rx1 - rx0) * scale,
                                                (ry1 - ry0) * scale)
                        finally:
                            image.close()
                    else:
                        # Page missing on this side: empty red frame.
                        report.rect(x_off, y_off, half_width, avail_height)

            report.output(str(output_pdf))
        finally:
            pdf_b.close()
    finally:
        pdf_a.close()
