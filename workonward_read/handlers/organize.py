"""
Organize handlers for WorkOnward Read (page-level operations) plus
compression, batch processing and document properties.

File -> file tools (merge / split / extract / compress / batch /
properties) work without a loaded document. Operations on the loaded
document (delete / rotate / reorder / insert / crop) are recorded into
``state.journal`` (a :class:`workonward_read.pdf_ops.PageOpsJournal`) AND
applied to ``state.images`` through the very same journal replay code
(single-op ``apply_to_images`` with the page factories from
:mod:`workonward_read.page_render`), so the on-screen document and the journal
never diverge. ``MENU_SAVE_ORGANIZED`` replays the whole journal losslessly
on the original file via ``pdf_ops.apply_journal``.

Core logic lives in module-level ``*_core`` functions taking plain request
dicts + the AppState so it is headless-testable; the sg.Window-facing
wrappers stay thin.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import os

from workonward_read import convert, page_render, pdf_ops, tasks, thumbnails
from workonward_read.dialogs import organize as dialogs
from workonward_read.dialogs.common import (error_popup, info_popup,
                                            parse_page_ranges)
from workonward_read.i18n import _


# ---------------------------------------------------------------------------
# Journal-consistent single-op apply
# ---------------------------------------------------------------------------

# Page factories used when replaying insert ops on the in-memory images.
JOURNAL_CALLBACKS = {
    'make_blank': page_render.make_blank,
    'render_pdf_pages': page_render.render_pdf_pages,
}


def record_and_apply(state, op):
    """Record ``op`` into ``state.journal`` (created on demand) and apply it
    to ``state.images`` via the same journal replay code, keeping the two
    worlds consistent. ``state.current_page`` is clamped afterwards."""
    if state.journal is None:
        state.journal = pdf_ops.PageOpsJournal()
    state.journal.record(op)
    # Replay exactly what was recorded (record() normalizes the op).
    recorded = state.journal.ops[-1]
    single = pdf_ops.PageOpsJournal()
    single.record(recorded)
    single.apply_to_images(state.images, JOURNAL_CALLBACKS)
    if state.images:
        state.current_page = max(0, min(state.current_page, len(state.images) - 1))
    else:
        state.current_page = 0


def _resolve_scope(state, scope, spec):
    """Turn a scope selection ('current'/'all'/'ranges' + spec) into a
    sorted list of 0-based page indices. Raises ValueError on bad specs."""
    total = len(state.images)
    if scope == 'all':
        return list(range(total))
    if scope == 'ranges':
        return parse_page_ranges(spec, total)
    return [state.current_page]


# ---------------------------------------------------------------------------
# Core operations (headless-testable)
# ---------------------------------------------------------------------------

def delete_pages_core(state, request):
    """Delete the requested pages. Raises ValueError when the selection is
    invalid or would delete every page."""
    indices = _resolve_scope(state, request.get('scope', 'current'),
                             request.get('spec', ''))
    if len(indices) >= len(state.images):
        raise ValueError('Cannot delete every page of the document.')
    record_and_apply(state, ('delete', indices))
    return indices


def rotate_pages_core(state, request):
    """Rotate the requested pages clockwise by request['degrees']."""
    degrees = int(request.get('degrees', 90))
    if degrees % 90 != 0 or degrees % 360 == 0:
        raise ValueError('Rotation must be 90, 180 or 270 degrees.')
    indices = _resolve_scope(state, request.get('scope', 'current'),
                             request.get('spec', ''))
    record_and_apply(state, ('rotate', {idx: degrees for idx in indices}))
    return indices


def reorder_pages_core(state, request):
    """Move page request['src'] to position request['dst'] (1-based)."""
    total = len(state.images)
    src = int(request['src']) - 1
    dst = int(request['dst']) - 1
    if not 0 <= src < total:
        raise ValueError(f'bad token: {request["src"]}')
    if not 0 <= dst < total:
        raise ValueError(f'bad token: {request["dst"]}')
    if src == dst:
        return
    record_and_apply(state, ('move', src, dst))
    state.current_page = dst


def current_page_size_pt(state):
    """(w_pt, h_pt) of the current page derived from its image pixels
    (px -> pt at 200 PPI)."""
    image = state.images[state.current_page].image
    return (image.width * 72.0 / page_render.IMPORT_PPI,
            image.height * 72.0 / page_render.IMPORT_PPI)


def insert_pages_core(state, request):
    """Insert a blank page or pages from another PDF at
    request['position'] (0-based). See dialogs.organize.insert_pages_dialog
    for the request shape."""
    total = len(state.images)
    position = int(request.get('position', total))
    if not 0 <= position <= total:
        raise ValueError(f'Insert position {position + 1} is out of range.')

    if request.get('mode') == 'pdf':
        path = request.get('path') or ''
        if not os.path.isfile(path):
            raise ValueError(f'File not found: {path}')
        src_pages = pdf_ops.read_properties(path)['pages']
        spec = (request.get('pages') or '').strip()
        indices = parse_page_ranges(spec, src_pages) if spec else list(range(src_pages))
        record_and_apply(state, ('insert_pdf', position, path, indices))
        return len(indices)

    size = request.get('size', 'A4')
    if size == 'current':
        w_pt, h_pt = current_page_size_pt(state)
    else:
        try:
            w_pt, h_pt = dialogs.PAGE_SIZES_PT[size]
        except KeyError:
            raise ValueError(f'Unknown page size: {size!r}')
    record_and_apply(state, ('insert_blank', position, [w_pt, h_pt]))
    return 1


def crop_pages_core(state, request):
    """Crop the current page or all pages by the requested margins.
    Margins are converted per page (each page may have its own pixel
    size). Raises ValueError on invalid margins."""
    scope = request.get('scope', 'current')
    indices = (list(range(len(state.images))) if scope == 'all'
               else [state.current_page])
    unit = request.get('unit', 'px')
    margins = request.get('margins', (0, 0, 0, 0))
    # Validate every box before recording anything (all-or-nothing).
    boxes = []
    for idx in indices:
        image = state.images[idx].image
        boxes.append(dialogs.margins_to_box(margins, unit,
                                            image.width, image.height))
    for idx, box in zip(indices, boxes):
        record_and_apply(state, ('crop', idx, box))
    return indices


def merge_core(request, passwords=None, progress_cb=None):
    """Merge request['inputs'] into request['output'].
    Returns (output, total_pages)."""
    if progress_cb:
        progress_cb(5, _('Merging…'))
    total = pdf_ops.merge_pdfs(request['inputs'], request['output'],
                               passwords=passwords)
    if progress_cb:
        progress_cb(100, '')
    return request['output'], total


def split_core(request, password=None, progress_cb=None):
    """Split request['input'] into one file per range token.
    Returns the list of written paths."""
    total = pdf_ops.read_properties(request['input'], password=password)['pages']
    ranges = dialogs.parse_split_ranges(request['ranges'], total)
    if progress_cb:
        progress_cb(10, _('Splitting…'))
    outputs = pdf_ops.split_pdf(request['input'], ranges,
                                request['output_pattern'], password=password)
    if progress_cb:
        progress_cb(100, '')
    return outputs


def extract_core(request, password=None, progress_cb=None):
    """Extract the requested pages of request['input'] to request['output'].
    Returns (output, page_count)."""
    total = pdf_ops.read_properties(request['input'], password=password)['pages']
    pages = parse_page_ranges(request['pages'], total)
    if progress_cb:
        progress_cb(10, _('Extracting…'))
    pdf_ops.extract_pages(request['input'], pages, request['output'],
                          password=password)
    if progress_cb:
        progress_cb(100, '')
    return request['output'], len(pages)


def compress_core(request, password=None, progress_cb=None):
    """Run the requested compression (already validated). Returns
    ``{'output', 'before_bytes', 'after_bytes'}``."""
    request = dialogs.validate_compress_request(request)
    before = os.path.getsize(request['input'])
    if request['mode'] == 'raster':
        convert.compress_pdf_raster(
            request['input'], request['output'], dpi=request['dpi'],
            jpeg_quality=request['quality'], password=password,
            progress_cb=progress_cb)
    else:
        convert.compress_pdf_lossless(
            request['input'], request['output'],
            image_quality=request['quality'], password=password)
    return {'output': request['output'], 'before_bytes': before,
            'after_bytes': os.path.getsize(request['output'])}


# Batch tools: key -> (tool_fn, out_ext, extra kwargs). '' out_ext means the
# tool's second argument is a directory (pdf_to_images).
def _batch_tools():
    tools = {
        'compress_raster': (convert.compress_pdf_raster, '.pdf', {}),
        'compress_lossless': (convert.compress_pdf_lossless, '.pdf', {}),
        'pdf_text': (convert.pdf_to_text, '.txt', {}),
        'pdf_images': (convert.pdf_to_images, '', {}),
    }
    try:
        from workonward_read import ocr
        tools['ocr'] = (ocr.make_searchable_pdf, '.pdf', {})
    except ImportError:
        pass
    return tools


def ocr_available():
    """True when the OCR module imports and a tesseract binary is found."""
    try:
        from workonward_read import ocr
        return ocr.find_tesseract() is not None
    except Exception:
        return False


def collect_batch_inputs(folder):
    """Sorted list of PDF files directly inside ``folder``."""
    if not folder or not os.path.isdir(folder):
        raise ValueError(f'Folder not found: {folder}')
    return sorted(
        os.path.join(folder, name) for name in os.listdir(folder)
        if name.lower().endswith('.pdf')
        and os.path.isfile(os.path.join(folder, name))
    )


def batch_core(request, progress_cb=None):
    """Run the requested batch tool over every PDF in request['folder'].
    Returns ``convert.batch_apply`` results:
    list of (input, output|None, error|None)."""
    tools = _batch_tools()
    tool = request.get('tool')
    if tool not in tools:
        raise ValueError(f'Unknown batch tool: {tool!r}')
    inputs = collect_batch_inputs(request['folder'])
    if not inputs:
        raise ValueError('The folder contains no PDF files.')
    tool_fn, out_ext, kwargs = tools[tool]
    if tool == 'pdf_images':
        # pdf_to_images' second argument is a directory; use one
        # sub-directory per input so page images never collide.
        def tool_fn(input_path, target, **kw):  # noqa: F811
            convert.pdf_to_images(input_path, target, **kw)
            return target
    return convert.batch_apply(tool_fn, inputs, request['out_dir'],
                               progress_cb=progress_cb, out_ext=out_ext,
                               **kwargs)


def apply_properties_core(input_path, output, metadata, password=None):
    """Write ``metadata`` (title/author/subject/keywords) into a copy of
    ``input_path`` saved as ``output``. When output == input the file is
    safely overwritten via a temporary sibling. Returns the output path."""
    metadata = {key: value for key, value in dict(metadata).items()
                if key in ('title', 'author', 'subject', 'keywords')}
    if os.path.abspath(output) == os.path.abspath(input_path):
        tmp = output + '.workonward_tmp.pdf'
        pdf_ops.write_properties(input_path, tmp, metadata, password=password)
        os.replace(tmp, output)
    else:
        pdf_ops.write_properties(input_path, output, metadata, password=password)
    return output


# ---------------------------------------------------------------------------
# Window plumbing
# ---------------------------------------------------------------------------

def _refresh_after_change(window, state):
    """Refresh graph, page counters and thumbnails after page ops."""
    from workonward_read.handlers.view import flip_to_page
    try:
        window['-PAGE_TOTAL-'].update(len(state.images))
    except Exception:
        pass
    if state.images:
        state.current_page = flip_to_page(
            window, state.images, state.current_page, state)
    if state.thumbnails_visible:
        thumbnails.refresh_thumbnails(window, state.images, state.current_page)


def _require_document(window, state):
    if not state.images:
        info_popup(window, _('Open a document first.'))
        return False
    return True


def _source_password(state, path):
    """Password for `path` when it is the loaded (encrypted) document."""
    if path and state.file_path and \
            os.path.abspath(path) == os.path.abspath(state.file_path):
        return state.source_password
    return None


def _run_in_background(window, state, fn, *args, on_done=None, **kwargs):
    """Start fn via tasks.run_task and register the completion callback the
    way main.py's task-event handling expects."""
    state.busy = True
    if on_done is not None:
        state.task_callbacks['-TASK-'] = on_done
    tasks.run_task(window, fn, *args, **kwargs)


def _human_size(num_bytes):
    value = float(num_bytes)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return f'{value:.1f} {unit}' if unit != 'B' else f'{int(value)} {unit}'
        value /= 1024
    return f'{int(num_bytes)} B'


# ---------------------------------------------------------------------------
# Menu handlers
# ---------------------------------------------------------------------------

def merge(window, state):
    """MENU_MERGE: merge several PDFs into a new file (background task)."""
    request = dialogs.merge_dialog(window)
    if not request:
        return
    passwords = {}
    for path in request['inputs']:
        password = _source_password(state, path)
        if password:
            passwords[path] = password

    def on_done(win, st, result):
        output, pages = result
        info_popup(win, _('Merged {pages} pages into\n{path}',
                          pages=pages, path=output))

    _run_in_background(window, state, merge_core, request,
                       passwords=passwords, on_done=on_done)


def split(window, state):
    """MENU_SPLIT: split a PDF into one file per page range."""
    request = dialogs.split_dialog(window, default_input=state.file_path or '')
    if not request:
        return
    password = _source_password(state, request['input'])

    def on_done(win, st, outputs):
        info_popup(win, _('Wrote {count} files:\n{files}',
                          count=len(outputs), files='\n'.join(outputs)))

    _run_in_background(window, state, split_core, request,
                       password=password, on_done=on_done)


def extract(window, state):
    """MENU_EXTRACT_PAGES: extract selected pages into a new PDF."""
    request = dialogs.extract_dialog(window, default_input=state.file_path or '')
    if not request:
        return
    password = _source_password(state, request['input'])

    def on_done(win, st, result):
        output, pages = result
        info_popup(win, _('Extracted {pages} pages into\n{path}',
                          pages=pages, path=output))

    _run_in_background(window, state, extract_core, request,
                       password=password, on_done=on_done)


def _loaded_doc_op(window, state, dialog_fn, core_fn):
    """Shared wrapper for loaded-document page ops."""
    if not _require_document(window, state):
        return
    request = dialog_fn()
    if not request:
        return
    try:
        core_fn(state, request)
    except ValueError as exc:
        error_popup(window, _('error_occurred'), exc)
        return
    _refresh_after_change(window, state)


def delete_pages(window, state):
    """MENU_DELETE_PAGES."""
    _loaded_doc_op(
        window, state,
        lambda: dialogs.delete_pages_dialog(
            window, len(state.images), state.current_page),
        delete_pages_core)


def rotate_pages(window, state):
    """MENU_ROTATE_PAGES."""
    _loaded_doc_op(
        window, state,
        lambda: dialogs.rotate_pages_dialog(
            window, len(state.images), state.current_page),
        rotate_pages_core)


def reorder_pages(window, state):
    """MENU_REORDER_PAGES."""
    _loaded_doc_op(
        window, state,
        lambda: dialogs.reorder_pages_dialog(
            window, len(state.images), state.current_page),
        reorder_pages_core)


def insert_pages(window, state):
    """MENU_INSERT_PAGES."""
    _loaded_doc_op(
        window, state,
        lambda: dialogs.insert_pages_dialog(
            window, len(state.images), state.current_page),
        insert_pages_core)


def crop(window, state):
    """MENU_CROP."""
    if not _require_document(window, state):
        return
    image = state.images[state.current_page].image
    _loaded_doc_op(
        window, state,
        lambda: dialogs.crop_dialog(
            window, len(state.images), state.current_page,
            image.width, image.height),
        crop_pages_core)


def save_organized(window, state):
    """MENU_SAVE_ORGANIZED: replay the journal losslessly on the original
    file (background task)."""
    import FreeSimpleGUI as sg
    if not state.file_path or not str(state.file_path).lower().endswith('.pdf'):
        info_popup(window, _('Open a PDF document first.'))
        return
    if state.journal is None or state.journal.is_empty():
        info_popup(window, _('There are no page changes to save yet.'))
        return
    base = os.path.splitext(os.path.basename(state.file_path))[0]
    output = sg.popup_get_file(
        _('Save Organized PDF…'), no_window=True, save_as=True,
        keep_on_top=True, file_types=dialogs.PDF_FILE_TYPES,
        default_extension='.pdf', default_path=f'{base}_organized.pdf')
    if not output:
        return
    if os.path.abspath(output) == os.path.abspath(state.file_path):
        error_popup(window, _('Please choose a different file than the source.'))
        return

    def on_done(win, st, result):
        info_popup(win, _('Saved organized PDF to\n{path}', path=output))

    _run_in_background(window, state, pdf_ops.apply_journal,
                       state.file_path, state.journal, output,
                       state.source_password, on_done=on_done)


def compress(window, state):
    """MENU_COMPRESS: raster or lossless compression (background task)."""
    request = dialogs.compress_dialog(window, default_input=state.file_path or '')
    if not request:
        return
    try:
        request = dialogs.validate_compress_request(request)
    except ValueError as exc:
        error_popup(window, _('error_occurred'), exc)
        return
    password = _source_password(state, request['input'])

    def on_done(win, st, result):
        before = result['before_bytes']
        after = result['after_bytes']
        percent = (100.0 * after / before) if before else 100.0
        info_popup(win, _(
            'Compressed\n{path}\n{before} -> {after} ({percent:.0f}% of original)',
            path=result['output'], before=_human_size(before),
            after=_human_size(after), percent=percent))

    _run_in_background(window, state, compress_core, request,
                       password=password, on_done=on_done)


def batch(window, state):
    """MENU_BATCH: apply one tool to every PDF in a folder (background
    task) with a per-file progress and a failure summary."""
    request = dialogs.batch_dialog(window, include_ocr=ocr_available())
    if not request:
        return
    try:
        inputs = collect_batch_inputs(request['folder'])
        if not inputs:
            raise ValueError(_('The folder contains no PDF files.'))
    except ValueError as exc:
        error_popup(window, _('error_occurred'), exc)
        return

    def on_done(win, st, results):
        failures = [(path, error) for path, _out, error in results if error]
        ok = len(results) - len(failures)
        message = _('Batch finished: {ok} of {total} files processed.',
                    ok=ok, total=len(results))
        if failures:
            lines = '\n'.join(
                f'{os.path.basename(path)}: {error}' for path, error in failures)
            message += '\n\n' + _('Failed files:') + '\n' + lines
        info_popup(win, message)

    _run_in_background(window, state, batch_core, request, on_done=on_done)


def properties(window, state):
    """MENU_PROPERTIES: view/edit document properties."""
    import FreeSimpleGUI as sg
    input_path = state.file_path
    if not input_path or not str(input_path).lower().endswith('.pdf'):
        input_path = sg.popup_get_file(
            _('Document Properties…'), no_window=True, keep_on_top=True,
            file_types=dialogs.PDF_FILE_TYPES)
        if not input_path:
            return
    password = _source_password(state, input_path)
    try:
        props = pdf_ops.read_properties(input_path, password=password)
    except Exception as exc:
        error_popup(window, _('error_occurred'), exc)
        return
    request = dialogs.properties_dialog(window, props,
                                        default_output=input_path)
    if not request:
        return
    output = request['output']
    if os.path.abspath(output) == os.path.abspath(input_path):
        confirmed = sg.popup_ok_cancel(
            _('Overwrite the original file?\n{path}', path=input_path),
            keep_on_top=True)
        if confirmed != 'OK':
            return
    try:
        apply_properties_core(input_path, output, request['metadata'],
                              password=password)
    except Exception as exc:
        error_popup(window, _('error_occurred'), exc)
        return
    info_popup(window, _('Saved document properties to\n{path}', path=output))


HANDLERS = {
    'MENU_MERGE': merge,
    'MENU_SPLIT': split,
    'MENU_INSERT_PAGES': insert_pages,
    'MENU_DELETE_PAGES': delete_pages,
    'MENU_REORDER_PAGES': reorder_pages,
    'MENU_ROTATE_PAGES': rotate_pages,
    'MENU_EXTRACT_PAGES': extract,
    'MENU_CROP': crop,
    'MENU_SAVE_ORGANIZED': save_organized,
    'MENU_COMPRESS': compress,
    'MENU_BATCH': batch,
    'MENU_PROPERTIES': properties,
}
