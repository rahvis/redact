"""
Full-text search across PDF documents for CoverUP PDF.

Finds every occurrence of a term in a PDF and reports, per hit, the page
index, a short text context and the bounding rectangles of the matched
characters converted to 200-PPI image pixel space (y-down), matching the
coordinate model used by the CoverUP canvas.

Business module: GUI-toolkit-free per the layering rule.

License: GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

from dataclasses import dataclass, field

import pypdfium2 as pdfium

# Canvas/annotation coordinates are original-image pixels at 200 PPI.
IMPORT_PPI = 200
_PT_TO_PX = IMPORT_PPI / 72.0
_CONTEXT_CHARS = 40


@dataclass
class Hit:
    """A single search match.

    Attributes:
        page_index: 0-based page number the match was found on.
        context: The matched text with up to 40 characters of context on
            either side, whitespace-normalized to a single line.
        rects_px: Bounding rectangles ``[x0, y0, x1, y1]`` of the matched
            characters in 200-PPI image pixel space, y-down. A match that
            wraps across lines produces multiple rectangles.
    """

    page_index: int
    context: str
    rects_px: list = field(default_factory=list)


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


def search_document(pdf_path, term, password=None, match_case=False,
                    progress_cb=None):
    """Search a PDF for every occurrence of ``term``.

    Args:
        pdf_path: Path to the PDF file.
        term: Text to search for. Must not be empty.
        password: Optional password for encrypted PDFs.
        match_case: If True the search is case-sensitive.
        progress_cb: Optional ``progress_cb(pct, msg)`` callback, called
            once per processed page with pct in 0..100.

    Returns:
        list[Hit]: All matches in document order (page order, then
        occurrence order within the page). Empty list if nothing matched
        or the document has no pages.

    Raises:
        ValueError: If ``term`` is empty, or the PDF is encrypted and the
            password is missing or wrong.
    """
    if not term:
        raise ValueError("The search term must not be empty.")

    hits = []
    pdf = _open_pdf(pdf_path, password)
    try:
        total_pages = len(pdf)
        for page_index in range(total_pages):
            page = pdf[page_index]
            textpage = None
            searcher = None
            try:
                _, page_h_pt = page.get_size()
                textpage = page.get_textpage()
                full_text = textpage.get_text_range()
                searcher = textpage.search(term, match_case=match_case)
                while True:
                    occurrence = searcher.get_next()
                    if occurrence is None:
                        break
                    char_index, char_count = occurrence

                    # A match may span several lines (line wraps): pdfium
                    # reports one rectangle per contiguous text run.
                    n_rects = textpage.count_rects(char_index, char_count)
                    rects_px = []
                    for rect_index in range(n_rects):
                        left, bottom, right, top = textpage.get_rect(rect_index)
                        # PDF pt (y-up) -> 200-PPI image px (y-down).
                        rects_px.append([
                            left * _PT_TO_PX,
                            (page_h_pt - top) * _PT_TO_PX,
                            right * _PT_TO_PX,
                            (page_h_pt - bottom) * _PT_TO_PX,
                        ])

                    start = max(0, char_index - _CONTEXT_CHARS)
                    end = min(len(full_text), char_index + char_count + _CONTEXT_CHARS)
                    context = " ".join(full_text[start:end].split())

                    hits.append(Hit(page_index=page_index, context=context,
                                    rects_px=rects_px))
            finally:
                if searcher is not None:
                    searcher.close()
                if textpage is not None:
                    textpage.close()
                page.close()

            if progress_cb:
                progress_cb(int((page_index + 1) * 100 / total_pages),
                            f"{page_index + 1}/{total_pages}")
    finally:
        pdf.close()

    return hits
