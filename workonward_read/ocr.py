"""
Optional Tesseract OCR support producing searchable PDFs.

Renders scanned (image-only) PDF pages, runs Tesseract on each page image
and merges the resulting invisible text layer underneath the visible page
image, yielding a searchable PDF whose pages look identical to the source.

Tesseract is an optional external dependency: `find_tesseract` locates the
binary (user-configured path, PATH lookup, then common install locations)
and all other functions degrade gracefully or raise plain exceptions when
it is unavailable.

CoverUP is licensed under GPL-3.0. (c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import io
import os
import shutil
from typing import Callable, Iterable, Optional, Sequence

import pypdfium2 as pdfium
import pytesseract
from fpdf import FPDF
from PIL import Image
from pypdf import PdfReader, PdfWriter, Transformation

# Common install locations checked when tesseract is neither user-configured
# nor on PATH (macOS Homebrew, macOS/Linux /usr/local, Windows default).
_PLATFORM_DEFAULT_PATHS = [
    '/opt/homebrew/bin/tesseract',
    '/usr/local/bin/tesseract',
    'C:\\Program Files\\Tesseract-OCR\\tesseract.exe',
]

ProgressCb = Optional[Callable[[int, str], None]]


def find_tesseract(user_path: Optional[str] = None) -> Optional[str]:
    """
    Locate the tesseract binary and configure pytesseract to use it.

    Search order:
    1. `user_path` if it points to an existing file,
    2. `shutil.which('tesseract')` (PATH lookup),
    3. well-known platform install locations.

    Args:
        user_path: Optional user-configured path to the tesseract binary.

    Returns:
        The path to the tesseract binary, or None if not found. When found,
        `pytesseract.pytesseract.tesseract_cmd` is set to the returned path.
    """
    candidates = []
    if user_path:
        candidates.append(user_path)

    which_path = shutil.which('tesseract')
    if which_path:
        candidates.append(which_path)

    candidates.extend(_PLATFORM_DEFAULT_PATHS)

    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            pytesseract.pytesseract.tesseract_cmd = candidate
            return candidate

    return None


def available_languages(tess_path: Optional[str] = None) -> list:
    """
    Return the list of language codes installed for tesseract.

    Args:
        tess_path: Optional explicit path to the tesseract binary.

    Returns:
        List of language codes (e.g. ['eng', 'osd']), or an empty list when
        tesseract is missing or the query fails.
    """
    if find_tesseract(tess_path) is None:
        return []
    try:
        return list(pytesseract.get_languages(config=''))
    except Exception:
        return []


def ocr_image_to_text(pil_image: Image.Image, lang: str = 'eng',
                      tess_path: Optional[str] = None) -> str:
    """
    Run OCR on a PIL image and return the recognized plain text.

    Args:
        pil_image: Source image.
        lang: Tesseract language code (default 'eng').
        tess_path: Optional explicit path to the tesseract binary.

    Returns:
        The recognized text (may be empty for blank images).

    Raises:
        RuntimeError: If tesseract cannot be found.
    """
    if find_tesseract(tess_path) is None:
        raise RuntimeError('Tesseract OCR binary not found. '
                           'Install tesseract or configure its path.')
    return pytesseract.image_to_string(pil_image, lang=lang)


def _ocr_text_pdf_page(image: Image.Image, dpi: float, lang: str) -> bytes:
    """
    Run tesseract on one page image and return an invisible-text-only,
    single-page PDF (bytes) geometrically matching the image at `dpi`.
    """
    dpi_int = max(1, int(round(dpi)))
    # Tag the image so PIL-side metadata agrees, and pass --dpi explicitly
    # because tesseract otherwise guesses resolution (wrong page geometry).
    image.info['dpi'] = (dpi_int, dpi_int)
    config = f'--dpi {dpi_int} -c textonly_pdf=1'
    return pytesseract.image_to_pdf_or_hocr(
        image, extension='pdf', lang=lang, config=config)


def _image_pdf_page(image: Image.Image, w_pt: float, h_pt: float,
                    jpeg_quality: int) -> bytes:
    """
    Build a single-page PDF (bytes) showing `image` full-bleed on a
    `w_pt` x `h_pt` points page, compressed as JPEG.
    """
    rgb = image if image.mode == 'RGB' else image.convert('RGB')
    buffer = io.BytesIO()
    try:
        rgb.save(buffer, format='JPEG', quality=jpeg_quality)
        buffer.seek(0)

        pdf = FPDF(unit='pt', format=(w_pt, h_pt))
        pdf.set_auto_page_break(False)
        pdf.set_margin(0)
        pdf.add_page()
        pdf.image(buffer, x=0, y=0, w=w_pt, h=h_pt)
        return bytes(pdf.output())
    finally:
        buffer.close()
        if rgb is not image:
            rgb.close()


def make_searchable_from_images(page_images: Iterable[Image.Image],
                                page_sizes_pt: Sequence,
                                output: str,
                                lang: str = 'eng',
                                tess_path: Optional[str] = None,
                                jpeg_quality: int = 90,
                                progress_cb: ProgressCb = None) -> str:
    """
    Build a searchable PDF from page images plus an OCR text layer.

    For every page image an invisible-text-only PDF page is produced with
    tesseract (`textonly_pdf=1`), a visible image page is built with fpdf2
    at the requested page size, and the text layer is scaled onto the image
    page so extracted text lines up with the rendered scan.

    Pages are processed one at a time so memory stays bounded for large
    documents (`page_images` may be a generator).

    Args:
        page_images: Iterable of PIL images, one per page, in page order.
        page_sizes_pt: Sequence of (width_pt, height_pt) per page; its length
            defines the page count and must match `page_images`.
        output: Path of the searchable PDF to write.
        lang: Tesseract language code (default 'eng').
        tess_path: Optional explicit path to the tesseract binary.
        jpeg_quality: JPEG quality for the visible page images (1-100).
        progress_cb: Optional callable(pct: int, msg: str).

    Returns:
        The output path.

    Raises:
        ValueError: If there are no pages or counts do not match.
        RuntimeError: If tesseract cannot be found.
    """
    total = len(page_sizes_pt)
    if total == 0:
        raise ValueError('Document has no pages to OCR.')

    if find_tesseract(tess_path) is None:
        raise RuntimeError('Tesseract OCR binary not found. '
                           'Install tesseract or configure its path.')

    writer = PdfWriter()
    processed = 0
    images_iter = iter(page_images)

    for index, size in enumerate(page_sizes_pt):
        try:
            image = next(images_iter)
        except StopIteration:
            raise ValueError('Fewer page images than page sizes were '
                             'provided ({} < {}).'.format(index, total))

        if progress_cb:
            progress_cb(int(index * 100 / total),
                        'OCR page {} of {}'.format(index + 1, total))

        w_pt, h_pt = float(size[0]), float(size[1])
        if w_pt <= 0 or h_pt <= 0:
            raise ValueError(
                'Invalid page size for page {}: {}'.format(index + 1, size))

        # DPI implied by the pixel dimensions vs. the target page size.
        dpi = image.width * 72.0 / w_pt

        text_pdf_bytes = _ocr_text_pdf_page(image, dpi, lang)
        image_pdf_bytes = _image_pdf_page(image, w_pt, h_pt, jpeg_quality)

        image_reader = PdfReader(io.BytesIO(image_pdf_bytes))
        text_reader = PdfReader(io.BytesIO(text_pdf_bytes))

        # Attach to the writer first, then merge (merging reader pages that
        # are not attached to a writer is deprecated in pypdf).
        image_page = writer.add_page(image_reader.pages[0])
        if len(text_reader.pages) > 0:
            text_page = text_reader.pages[0]
            # Scale tesseract's page geometry onto the image page mediabox.
            tw = float(text_page.mediabox.width)
            th = float(text_page.mediabox.height)
            iw = float(image_page.mediabox.width)
            ih = float(image_page.mediabox.height)
            if tw > 0 and th > 0:
                sx = iw / tw
                sy = ih / th
                image_page.merge_transformed_page(
                    text_page, Transformation().scale(sx, sy))
        processed += 1
        del text_pdf_bytes, image_pdf_bytes

    # Detect surplus images (mismatched inputs) without consuming a generator
    # beyond one extra element.
    try:
        next(images_iter)
    except StopIteration:
        pass
    else:
        raise ValueError('More page images than page sizes were provided '
                         '(> {}).'.format(total))

    with open(output, 'wb') as fh:
        writer.write(fh)
    writer.close()

    if progress_cb:
        progress_cb(100, 'OCR finished: {} pages'.format(processed))
    return str(output)


def make_searchable_pdf(input: str, output: str, lang: str = 'eng',
                        dpi: int = 200, tess_path: Optional[str] = None,
                        password: Optional[str] = None,
                        progress_cb: ProgressCb = None) -> str:
    """
    OCR a scanned PDF into a searchable PDF with identical page geometry.

    Pages are rendered with pypdfium2 at `dpi`, then delegated to
    `make_searchable_from_images` with the source page sizes so the output
    pages match the input within rendering precision.

    Args:
        input: Path to the source (scanned) PDF.
        output: Path of the searchable PDF to write.
        lang: Tesseract language code (default 'eng').
        dpi: Render resolution for OCR (default 200).
        tess_path: Optional explicit path to the tesseract binary.
        password: Password for encrypted source PDFs.
        progress_cb: Optional callable(pct: int, msg: str).

    Returns:
        The output path.

    Raises:
        ValueError: If the PDF cannot be opened (bad password/corrupt file)
            or has no pages.
        RuntimeError: If tesseract cannot be found.
    """
    if find_tesseract(tess_path) is None:
        raise RuntimeError('Tesseract OCR binary not found. '
                           'Install tesseract or configure its path.')

    try:
        document = pdfium.PdfDocument(input, password=password)
    except pdfium.PdfiumError as exc:
        raise ValueError('Could not open PDF (wrong password or corrupt '
                         'file): {}'.format(exc))

    try:
        page_count = len(document)
        if page_count == 0:
            raise ValueError('Document has no pages to OCR.')

        page_sizes_pt = []
        for index in range(page_count):
            page = document.get_page(index)
            try:
                page_sizes_pt.append(page.get_size())
            finally:
                page.close()

        def rendered_pages():
            """Lazily render pages so only one bitmap is alive at a time."""
            for index in range(page_count):
                page = document.get_page(index)
                try:
                    bitmap = page.render(scale=dpi / 72.0)
                    pil_image = bitmap.to_pil()
                    bitmap.close()
                finally:
                    page.close()
                yield pil_image
                pil_image.close()

        return make_searchable_from_images(
            rendered_pages(), page_sizes_pt, output,
            lang=lang, tess_path=tess_path, progress_cb=progress_cb)
    finally:
        document.close()
