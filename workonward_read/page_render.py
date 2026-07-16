"""
Page rendering helpers for page-organization operations.

Provides the two page factories consumed by
:meth:`workonward_read.pdf_ops.PageOpsJournal.apply_to_images` callbacks:

- :func:`make_blank` creates a white ImageContainer page of a given size in
  points at the application import resolution (200 PPI).
- :func:`render_pdf_pages` renders selected pages of a PDF file into
  ImageContainers, reusing document_loader's pypdfium2 rendering approach
  (JPEG round-trip for memory discipline, one page at a time, all pdfium
  resources released before the next page is rendered).

Coordinates follow the application-wide convention: ``px = pt * 200/72``.
No FreeSimpleGUI / tkinter imports (headless-testable).

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
(c) 2026 WorkOnward Read contributors
"""

from PIL import Image

from workonward_read import pdfium_io
# Application-wide import resolution — canonical home: geometry.py.
from workonward_read.geometry import IMPORT_PPI, PX_PER_PT
from workonward_read.image_container import ImageContainer
from workonward_read.pdfium_io import PDFIUM_LOCK


def make_blank(w_pt, h_pt):
    """
    Create a blank (white) page container of ``w_pt`` x ``h_pt`` points.

    The backing PIL image is sized at 200 PPI (``px = round(pt * 200/72)``)
    so pixel-space operations (crop boxes, annotations) stay consistent with
    the rest of the application.

    Returns:
        ImageContainer: A new container for the blank page. The ``size``
        tuple is handed in as ``(w_pt, h_pt)``, mirroring how
        document_loader hands pdfium's ``page.get_size()`` to the container.
    """
    w_pt = float(w_pt)
    h_pt = float(h_pt)
    if w_pt <= 0 or h_pt <= 0:
        raise ValueError(f"Blank page size must be positive, got {w_pt} x {h_pt} pt.")
    w_px = max(1, round(w_pt * PX_PER_PT))
    h_px = max(1, round(h_pt * PX_PER_PT))
    image = Image.new("RGB", (w_px, h_px), "white")
    return ImageContainer(image, (w_pt, h_pt))


def render_pdf_pages(path, indices, password=None):
    """
    Render 0-based page ``indices`` of the PDF at ``path`` into containers.

    Mirrors :func:`workonward_read.document_loader.load_document`'s approach:
    each page is rendered with pypdfium2 at the import resolution, JPEG
    round-tripped (memory discipline for large pages) and wrapped in an
    ImageContainer with the pdfium page size (points) as its ``size``.
    Rendering is sequential and every pdfium resource is closed before the
    next page renders. On any failure, already-created containers are closed
    before the exception propagates.

    Args:
        path: PDF file path.
        indices: Iterable of 0-based page indices to render, in output order.
        password: Optional password for encrypted PDFs.

    Returns:
        list[ImageContainer]: One container per requested index.
    """
    indices = list(indices)
    pdf = pdfium_io.open_pdf(path, password)

    containers = []
    scale = IMPORT_PPI / 72.0
    try:
        with PDFIUM_LOCK:
            total = len(pdf)
        for index in indices:
            if not isinstance(index, int) or isinstance(index, bool) or \
                    not 0 <= index < total:
                raise ValueError(
                    f"Page index {index!r} out of range for '{path}' ({total} pages)."
                )
            # JPEG round-trip like document_loader: keeps the in-memory
            # representation compact and detaches it from pdfium buffers.
            # All pdfium work happens under PDFIUM_LOCK (per page).
            loaded = pdfium_io.render_page_to_pil(pdf, index, scale,
                                                  jpeg_roundtrip=True)
            page_size = pdfium_io.get_page_size(pdf, index)
            containers.append(ImageContainer(loaded, page_size))
    except Exception:
        for container in containers:
            try:
                container.close()
            except Exception:
                pass
        raise
    finally:
        pdfium_io.close_pdf(pdf)
    return containers


# Page factories consumed by PageOpsJournal.apply_to_images when replaying
# insert ops on the in-memory images. Shared by handlers.organize (live page
# ops) and document_loader (work-session restore) — kept here so importing it
# never pulls in the handlers package (avoids import cycles).
JOURNAL_CALLBACKS = {
    'make_blank': make_blank,
    'render_pdf_pages': render_pdf_pages,
}
