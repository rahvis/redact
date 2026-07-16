"""
Convert handlers for WorkOnward Read (PDF to images/text/word/html, images to
PDF, OCR).

The window-facing handlers stay thin: they open the dialogs in
:mod:`workonward_read.dialogs.convert` and hand the actual work to
:func:`workonward_read.tasks.run_task` with an ``on_done`` completion
callback. The module-level cores (``run_convert``, ``run_images_to_pdf``,
``run_ocr`` and the settings helpers) are headless and unit-testable.

OCR of the LOADED document consumes ``state.images`` on the worker thread,
so the handler holds the document busy-set lock (``state.doc_lock``) for
the whole task duration — document-mutating entry points (page ops,
save/export) refuse to run meanwhile, making mid-OCR page-op races
impossible.

For OCR of the loaded document the pages are rendered through the
ImageContainer finalize path (``finalized_image``), i.e. with every
annotation and document decoration burned in, so text hidden by redactions
never reaches the OCR text layer.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import json
import os

from workonward_read import convert, ocr
from workonward_read.dialogs import convert as convert_dialogs
from workonward_read.dialogs.common import (error_popup, info_popup,
                                            require_document_free)
from workonward_read.i18n import _
from workonward_read.tasks import run_task
from workonward_read.workfile import get_default_datadir


SETTINGS_FILENAME = 'settings.json'
TESSERACT_PATH_KEY = 'tesseract_path'

# state.doc_lock busy-set reason while OCR-current-document runs.
OCR_DOC_REASON = 'ocr-current-document'


# ---------------------------------------------------------------------------
# Settings JSON (small app settings file in the workfile data directory)
# ---------------------------------------------------------------------------

def settings_path(datadir=None):
    """Return the path of the app settings JSON in the workfile datadir."""
    if datadir is None:
        datadir = get_default_datadir()
    return os.path.join(datadir, SETTINGS_FILENAME)


def load_settings(datadir=None):
    """Load the settings dict; missing or unreadable file yields ``{}``."""
    try:
        with open(settings_path(datadir), 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(settings, datadir=None):
    """Write the settings dict as JSON (best effort, returns the path)."""
    path = settings_path(datadir)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(settings, fh, indent=2)
    return path


def get_saved_tesseract_path(datadir=None):
    """Return the user-configured tesseract binary path or None."""
    value = load_settings(datadir).get(TESSERACT_PATH_KEY)
    return value if isinstance(value, str) and value else None


def save_tesseract_path(path, datadir=None):
    """Persist a user-chosen tesseract binary path in the settings JSON."""
    settings = load_settings(datadir)
    settings[TESSERACT_PATH_KEY] = path
    return save_settings(settings, datadir)


# ---------------------------------------------------------------------------
# Headless cores (unit-testable without a window)
# ---------------------------------------------------------------------------

def _resolve_source(request, state):
    """
    Return ``(input_path, password)`` for a conversion request: the loaded
    file (with ``state.source_password``) or the picked file (no password).
    """
    if request.get('use_loaded'):
        if not getattr(state, 'file_path', None):
            raise ValueError('No document is loaded.')
        return state.file_path, getattr(state, 'source_password', None)
    input_path = (request.get('input_path') or '').strip()
    if not input_path:
        raise ValueError('No input file was selected.')
    return input_path, None


def run_convert(request, state, progress_cb=None):
    """
    Execute a conversion request from ``dialogs.convert.convert_dialog``.

    Returns:
        list[str]: The written output path(s) — one path per image for the
        'images' target, a single-element list otherwise.
    """
    input_path, password = _resolve_source(request, state)
    target = request['target']
    output = request['output']

    if target == 'images':
        return convert.pdf_to_images(
            input_path, output,
            fmt=request.get('fmt', 'PNG'),
            dpi=int(request.get('dpi', 200)),
            password=password,
            progress_cb=progress_cb)
    if target == 'text':
        return [convert.pdf_to_text(input_path, output, password=password)]
    if target == 'word':
        return [convert.pdf_to_docx(input_path, output, password=password)]
    if target == 'html':
        return [convert.pdf_to_html(
            input_path, output, password=password,
            embed_page_images=bool(request.get('embed_page_images')))]
    raise ValueError(f'Unknown conversion target: {target!r}')


def run_images_to_pdf(request):
    """Build a PDF from ordered image paths. Returns the output path."""
    return convert.images_to_pdf(request['image_paths'], request['output'])


def iter_redacted_pages(images, decorations=None):
    """
    Lazily yield each page of the loaded document as a PIL image with all
    annotations (and document decorations) burned in via the ImageContainer
    finalize path — the same rendering used by save-redacted, so covered
    (redacted) content never leaks into downstream consumers such as OCR.
    Only one burned page image is alive at a time.
    """
    total = len(images)
    for idx, container in enumerate(images):
        burned = container.finalized_image(
            'PIL', decorations=decorations or {}, page_idx=idx,
            total_pages=total)
        yield burned
        try:
            burned.close()
        except Exception:
            pass


def run_ocr(request, state, tess_path=None, progress_cb=None):
    """
    Execute an OCR request from ``dialogs.convert.ocr_dialog``.

    The loaded document is OCR'd from its CURRENTLY REDACTED raster
    (annotations burned in); a picked file goes through
    ``ocr.make_searchable_pdf``. Returns the output path.
    """
    output = request['output']
    lang = request.get('lang') or 'eng'

    if request.get('use_loaded'):
        images = getattr(state, 'images', None)
        if not images:
            raise ValueError('No document is loaded.')
        # ImageContainer.size is (width_pt, height_pt) as handed to FPDF;
        # the attribute names are historic (height_in_pt holds size[0]).
        page_sizes_pt = [(c.height_in_pt, c.width_in_pt) for c in images]
        return ocr.make_searchable_from_images(
            iter_redacted_pages(images, getattr(state, 'decorations', None)),
            page_sizes_pt, output, lang=lang, tess_path=tess_path,
            progress_cb=progress_cb)

    input_path = (request.get('input_path') or '').strip()
    if not input_path:
        raise ValueError('No input file was selected.')
    return ocr.make_searchable_pdf(
        input_path, output, lang=lang, tess_path=tess_path,
        password=state.password_for(input_path),
        progress_cb=progress_cb)


# ---------------------------------------------------------------------------
# Window-facing handlers (thin wrappers)
# ---------------------------------------------------------------------------

def _success_message(paths):
    """Human-readable success text listing the written output path(s)."""
    paths = list(paths)
    if len(paths) == 1:
        return _('Saved: {path}', path=paths[0])
    directory = os.path.dirname(paths[0]) if paths else ''
    return _('Saved {count} files to {directory}',
             count=len(paths), directory=directory)


def _make_convert_handler(target):
    """Build the menu handler for one conversion target."""
    def handler(window, state):
        request = convert_dialogs.convert_dialog(window, state, target)
        if not request:
            return

        def on_done(win, st, result):
            paths = result if isinstance(result, list) else [result]
            info_popup(win, _success_message(paths))

        run_task(window, run_convert, request, state, on_done=on_done)
    return handler


def images_to_pdf(window, state):
    """Menu handler: build a PDF from a user-ordered list of images."""
    request = convert_dialogs.images_to_pdf_dialog(window, state)
    if not request:
        return

    def on_done(win, st, result):
        info_popup(win, _success_message([result]))

    run_task(window, run_images_to_pdf, request, on_done=on_done)


def ocr_document(window, state):
    """Menu handler: OCR the loaded document (redacted raster) or a file."""
    tess_path = ocr.find_tesseract(get_saved_tesseract_path())
    if tess_path is None:
        chosen = convert_dialogs.tesseract_missing_dialog(window)
        if not chosen:
            return
        save_tesseract_path(chosen)
        tess_path = ocr.find_tesseract(chosen)
        if tess_path is None:
            error_popup(window, _(
                'Tesseract was not usable at the selected location.'))
            return

    languages = ocr.available_languages(tess_path) or ['eng']
    request = convert_dialogs.ocr_dialog(window, state, languages)
    if not request:
        return

    # OCR of the LOADED document reads state.images on the worker thread:
    # hold the doc lock for the whole task (released in on_done/on_error)
    # so page ops / save / export cannot run concurrently.
    use_loaded = bool(request.get('use_loaded'))
    if use_loaded:
        if not require_document_free(window, state):
            return
        state.acquire_doc(OCR_DOC_REASON)

    def on_done(win, st, result):
        if use_loaded:
            st.release_doc(OCR_DOC_REASON)
        info_popup(win, _success_message([result]))

    def on_error(win, st, payload):
        if use_loaded:
            st.release_doc(OCR_DOC_REASON)

    run_task(window, run_ocr, request, state,
             on_done=on_done, on_error=on_error, tess_path=tess_path)


HANDLERS = {
    'MENU_CONVERT_IMAGES': _make_convert_handler('images'),
    'MENU_CONVERT_TEXT': _make_convert_handler('text'),
    'MENU_CONVERT_WORD': _make_convert_handler('word'),
    'MENU_CONVERT_HTML': _make_convert_handler('html'),
    'MENU_IMAGES_TO_PDF': images_to_pdf,
    'MENU_OCR': ocr_document,
}
