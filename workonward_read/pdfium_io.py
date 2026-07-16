"""
Serialized pypdfium2 access for WorkOnward Read.

pdfium is NOT thread-safe: two threads driving pdfium at the same time
(e.g. a compare-report export on a worker thread while a text diff runs on
another) corrupt shared library state and crash. Every in-process pdfium
call sequence must therefore hold the module-level :data:`PDFIUM_LOCK`.
The helpers here acquire it internally; call sites that talk to pdfium
directly (page loops) take ``with PDFIUM_LOCK:`` around each per-page call
sequence — per page, not per document, so long render jobs don't starve
other pdfium users.

``document_loader``'s ProcessPoolExecutor page renderers need NO lock: they
run in separate worker PROCESSES, each with its own pdfium library instance
and address space, so there is no shared pdfium state to protect.

This module also consolidates the previously drifted per-module PDF open
helpers into one canonical :func:`open_pdf` (existence check + password
failures mapped to :data:`PASSWORD_ERROR`).

Business module: GUI-toolkit-free per the layering rule.

License: GPL-3.0
(c) 2024 - 2026 Björn Seipel
(c) 2026 WorkOnward Read contributors
"""

import io
import os
import threading
from contextlib import contextmanager

import pypdfium2 as pdfium
from PIL import Image

# Serializes ALL pdfium calls in this process (re-entrant so helpers can
# nest inside a caller-held lock).
PDFIUM_LOCK = threading.RLock()

# The ONE canonical password-failure message (kept stable for callers and
# tests that match on 'password').
PASSWORD_ERROR = "The PDF is encrypted and requires a correct password."


def open_pdf(path, password=None):
    """Open a PDF with pypdfium2 under :data:`PDFIUM_LOCK`.

    Args:
        path: PDF file path.
        password: Optional password for encrypted PDFs.

    Returns:
        pdfium.PdfDocument: The open document. The caller owns it and must
        close it (see :func:`close_pdf` / :func:`pdfium_session`).

    Raises:
        FileNotFoundError: If ``path`` is empty or not an existing file.
        ValueError: :data:`PASSWORD_ERROR` when the PDF is encrypted and the
            password is missing or wrong; ``'Could not open PDF: …'`` for
            any other pdfium open failure.
    """
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")
    with PDFIUM_LOCK:
        try:
            if password:
                return pdfium.PdfDocument(path, password=password)
            return pdfium.PdfDocument(path)
        except pdfium.PdfiumError as exc:
            message = str(exc).lower()
            if "password" in message or "encrypt" in message:
                raise ValueError(PASSWORD_ERROR) from exc
            raise ValueError(f"Could not open PDF: {exc}") from exc


def close_pdf(doc):
    """Close a pdfium document under the lock, swallowing close errors."""
    with PDFIUM_LOCK:
        try:
            doc.close()
        except Exception:
            pass


@contextmanager
def pdfium_session(path, password=None):
    """Context manager: yield the open document while holding the lock.

    The WHOLE with-block runs under :data:`PDFIUM_LOCK` and the document is
    closed on exit — intended for short call sequences (page counts, single
    extractions). Long per-page loops should use :func:`open_pdf` plus
    per-page ``with PDFIUM_LOCK:`` blocks instead so they don't starve
    other pdfium users.
    """
    with PDFIUM_LOCK:
        doc = open_pdf(path, password)
        try:
            yield doc
        finally:
            close_pdf(doc)


def page_count(doc):
    """Number of pages of an open document (under the lock)."""
    with PDFIUM_LOCK:
        return len(doc)


def get_page_size(doc, index):
    """``(width_pt, height_pt)`` of page ``index`` (under the lock)."""
    with PDFIUM_LOCK:
        page = doc[index]
        try:
            return page.get_size()
        finally:
            page.close()


def render_page_to_pil(doc_or_path, index, scale, jpeg_roundtrip=False,
                       grayscale=False, password=None):
    """Render one page to a PIL image with all pdfium work under the lock.

    Args:
        doc_or_path: An open ``pdfium.PdfDocument`` OR a file path (opened
            and closed for this single render).
        index: 0-based page index.
        scale: Render scale (``dpi / 72``).
        jpeg_roundtrip: When True the rendered bitmap is JPEG round-tripped
            (encode + reload) so the returned image is compact and fully
            detached from pdfium buffers — the memory discipline used for
            long-lived page images (document import / page-op replay).
        grayscale: Render in grayscale; the result is guaranteed mode 'L'.
        password: Optional password, used only when ``doc_or_path`` is a path.

    Returns:
        PIL.Image: The rendered page image (caller closes it).
    """
    if isinstance(doc_or_path, (str, os.PathLike)):
        with pdfium_session(str(doc_or_path), password) as doc:
            return _render_locked(doc, index, scale, jpeg_roundtrip, grayscale)
    with PDFIUM_LOCK:
        return _render_locked(doc_or_path, index, scale, jpeg_roundtrip,
                              grayscale)


def _render_locked(doc, index, scale, jpeg_roundtrip, grayscale):
    """Render helper; the caller already holds :data:`PDFIUM_LOCK`."""
    page = doc[index]
    try:
        bitmap = page.render(scale=scale, grayscale=grayscale)
        try:
            image = bitmap.to_pil()
        finally:
            try:
                bitmap.close()
            except Exception:
                pass
    finally:
        page.close()

    if grayscale and image.mode != "L":
        converted = image.convert("L")
        image.close()
        image = converted

    if jpeg_roundtrip:
        with io.BytesIO() as buffer:
            image.save(buffer, format="JPEG")
            image.close()
            buffer.seek(0)
            loaded = Image.open(buffer)
            loaded.load()
        image = loaded
    return image
