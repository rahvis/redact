"""
Organize dialogs for WorkOnward Read: merge / split / extract, page-level
operations on the loaded document (delete, rotate, reorder, insert, crop),
compression, batch processing and document properties.

Every dialog is modal, keep-on-top, centered over the parent window and
returns a plain request dict or None when cancelled. Pure validation
helpers (``parse_split_ranges``, ``margins_to_box``,
``validate_compress_request``) live at module level so they are
unit-testable without opening a window.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import os

import FreeSimpleGUI as sg

from workonward_read.dialogs.common import (error_popup, file_open_row,
                                            open_modal as _open_modal,
                                            parse_page_ranges)
from workonward_read.geometry import PT_PER_PX, PX_PER_PT
from workonward_read.i18n import _

PDF_FILE_TYPES = (('PDF', '*.pdf *.PDF'),)

# Named page sizes in points (width, height).
PAGE_SIZES_PT = {
    'A4': (595.276, 841.890),
    'Letter': (612.0, 792.0),
}

ROTATION_CHOICES = (90, 180, 270)

# Batch tools: key -> translated label (built lazily so labels translate).
BATCH_TOOL_KEYS = ('compress_raster', 'compress_lossless', 'ocr',
                   'pdf_text', 'pdf_images')


def batch_tool_labels(include_ocr=True):
    """Return an ordered {label: key} mapping for the batch tool combo."""
    labels = {
        _('Compress (raster)'): 'compress_raster',
        _('Compress (lossless)'): 'compress_lossless',
    }
    if include_ocr:
        labels[_('Recognize Text (OCR)')] = 'ocr'
    labels[_('PDF to Text')] = 'pdf_text'
    labels[_('PDF to Images')] = 'pdf_images'
    return labels


# ---------------------------------------------------------------------------
# Pure helpers (GUI-free, unit-testable)
# ---------------------------------------------------------------------------

def parse_split_ranges(spec, total):
    """
    Parse a 1-based split spec like ``'1-3, 4, 5-'`` into a list of 0-based
    inclusive ``(start, end)`` tuples — one output part per comma token.

    Each token must describe a contiguous range (``N``, ``N-M`` or ``N-``);
    :func:`workonward_read.dialogs.common.parse_page_ranges` does the token
    validation.

    Raises:
        ValueError: ``'bad token: X'`` for malformed/out-of-range tokens.
    """
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError(f'bad token: {spec!r}')
    ranges = []
    for token in spec.split(','):
        indices = parse_page_ranges(token, total)
        ranges.append((indices[0], indices[-1]))
    return ranges


def margins_to_box(margins, unit, width_px, height_px):
    """
    Convert crop ``margins`` (left, top, right, bottom) in ``unit``
    ('px' or 'pt') into a crop box ``[x0, y0, x1, y1]`` in original-image
    pixels (200 PPI, y-down) for a page of ``width_px`` x ``height_px``.

    Raises:
        ValueError: For negative margins, unknown units or margins that
            leave no page area.
    """
    try:
        left, top, right, bottom = (float(v) for v in margins)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'Invalid margin values: {margins!r}') from exc
    if min(left, top, right, bottom) < 0:
        raise ValueError(_('Margins must not be negative.'))
    if unit == 'pt':
        left, top, right, bottom = (v * PX_PER_PT
                                    for v in (left, top, right, bottom))
    elif unit != 'px':
        raise ValueError(f'Unknown margin unit: {unit!r}')
    x0, y0 = left, top
    x1, y1 = width_px - right, height_px - bottom
    if x1 - x0 < 1 or y1 - y0 < 1:
        raise ValueError(_('Margins leave no page area to keep.'))
    return [x0, y0, x1, y1]


def validate_compress_request(request):
    """
    Validate a compress request dict (see :func:`compress_dialog`) and
    return a normalized copy.

    Required keys: ``mode`` ('raster'|'lossless'), ``input``, ``output``;
    raster mode additionally needs ``dpi`` (72-200); both modes use
    ``quality`` (1-100, defaults to 85).

    Raises:
        ValueError: On any invalid/missing field.
    """
    request = dict(request or {})
    mode = request.get('mode')
    if mode not in ('raster', 'lossless'):
        raise ValueError(f'Unknown compression mode: {mode!r}')
    input_path = (request.get('input') or '').strip()
    if not input_path:
        raise ValueError(_('An input PDF is required.'))
    if not os.path.isfile(input_path):
        raise ValueError(f'File not found: {input_path}')
    output = (request.get('output') or '').strip()
    if not output:
        raise ValueError(_('An output path is required.'))
    if os.path.abspath(output) == os.path.abspath(input_path):
        raise ValueError(_('Output must differ from the input file.'))
    try:
        quality = int(request.get('quality', 85))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            _('Quality must be a number between 1 and 100.')) from exc
    if not 1 <= quality <= 100:
        raise ValueError(_('Quality must be a number between 1 and 100.'))
    normalized = {'mode': mode, 'input': input_path, 'output': output,
                  'quality': quality}
    if mode == 'raster':
        try:
            dpi = int(request.get('dpi', 110))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                _('DPI must be a number between 72 and 200.')) from exc
        if not 72 <= dpi <= 200:
            raise ValueError(_('DPI must be a number between 72 and 200.'))
        normalized['dpi'] = dpi
    return normalized


# ---------------------------------------------------------------------------
# Dialog plumbing
# ---------------------------------------------------------------------------

def _read_and_close(dialog):
    event, values = dialog.read()
    dialog.close()
    return event, values


def _ok_cancel_row():
    return [sg.Push(), sg.Button(_('OK'), key='-OK-'),
            sg.Button(_('Cancel'), key='-CANCEL-')]


# ---------------------------------------------------------------------------
# Merge / split / extract (file -> file, no loaded document required)
# ---------------------------------------------------------------------------

def merge_dialog(window):
    """Multi-file merge dialog with ordering. Returns
    ``{'inputs': [path, ...], 'output': path}`` or None."""
    layout = [
        [sg.Text(_('Files to merge (in order):'))],
        [sg.Listbox(values=[], key='-LIST-', size=(58, 8),
                    select_mode=sg.LISTBOX_SELECT_MODE_SINGLE)],
        [sg.Input(visible=False, enable_events=True, key='-ADD_PATHS-'),
         sg.FilesBrowse(_('Add…'), file_types=PDF_FILE_TYPES,
                        target='-ADD_PATHS-'),
         sg.Button(_('Remove'), key='-REMOVE-'),
         sg.Button(_('Move Up'), key='-UP-'),
         sg.Button(_('Move Down'), key='-DOWN-')],
        [sg.Text(_('Output file:'))],
        file_open_row('-OUT-', file_types=PDF_FILE_TYPES, save_as=True),
        _ok_cancel_row(),
    ]
    dialog = _open_modal(_('Merge PDFs'), layout, window)
    files = []
    result = None
    while True:
        event, values = dialog.read()
        if event in (sg.WINDOW_CLOSED, '-CANCEL-'):
            break
        if event == '-ADD_PATHS-':
            raw = values.get('-ADD_PATHS-') or ''
            for path in raw.split(';'):
                path = path.strip()
                if path and path not in files:
                    files.append(path)
            dialog['-LIST-'].update(values=files)
        elif event in ('-REMOVE-', '-UP-', '-DOWN-'):
            selected = values.get('-LIST-') or []
            if not selected:
                continue
            idx = files.index(selected[0])
            if event == '-REMOVE-':
                files.pop(idx)
            elif event == '-UP-' and idx > 0:
                files[idx - 1], files[idx] = files[idx], files[idx - 1]
                idx -= 1
            elif event == '-DOWN-' and idx < len(files) - 1:
                files[idx + 1], files[idx] = files[idx], files[idx + 1]
                idx += 1
            dialog['-LIST-'].update(values=files)
            if files and event != '-REMOVE-':
                dialog['-LIST-'].update(set_to_index=[idx])
        elif event == '-OK-':
            output = (values.get('-OUT-') or '').strip()
            if len(files) < 2:
                error_popup(window, _('Add at least two PDF files to merge.'))
                continue
            if not output:
                error_popup(window, _('Please choose an output file.'))
                continue
            result = {'inputs': list(files), 'output': output}
            break
    dialog.close()
    return result


def split_dialog(window, default_input=''):
    """Split dialog. Returns ``{'input', 'ranges', 'output_pattern'}`` or
    None. ``ranges`` is the raw 1-based spec (one output part per comma
    token); the handler validates it against the real page count."""
    layout = [
        [sg.Text(_('PDF to split:'))],
        file_open_row('-IN-', file_types=PDF_FILE_TYPES,
                      default_path=default_input or ''),
        [sg.Text(_('Page ranges (one output file per range, e.g. 1-3,4,5-):'))],
        [sg.Input(key='-RANGES-', size=(40, 1))],
        [sg.Text(_('Output file (a part number is appended):'))],
        file_open_row('-OUT-', file_types=PDF_FILE_TYPES, save_as=True),
        _ok_cancel_row(),
    ]
    dialog = _open_modal(_('Split PDF'), layout, window)
    event, values = _read_and_close(dialog)
    if event != '-OK-':
        return None
    input_path = (values.get('-IN-') or '').strip()
    ranges = (values.get('-RANGES-') or '').strip()
    output = (values.get('-OUT-') or '').strip()
    if not input_path or not ranges or not output:
        return None
    stem, ext = os.path.splitext(output)
    pattern = f'{stem}_{{n}}{ext or ".pdf"}'
    return {'input': input_path, 'ranges': ranges, 'output_pattern': pattern}


def extract_dialog(window, default_input=''):
    """Extract-pages dialog. Returns ``{'input', 'pages', 'output'}`` or
    None; ``pages`` is the raw 1-based range spec."""
    layout = [
        [sg.Text(_('Source PDF:'))],
        file_open_row('-IN-', file_types=PDF_FILE_TYPES,
                      default_path=default_input or ''),
        [sg.Text(_('Pages to extract (e.g. 1-3,7,9-):'))],
        [sg.Input(key='-PAGES-', size=(40, 1))],
        [sg.Text(_('Output file:'))],
        file_open_row('-OUT-', file_types=PDF_FILE_TYPES, save_as=True),
        _ok_cancel_row(),
    ]
    dialog = _open_modal(_('Extract Pages'), layout, window)
    event, values = _read_and_close(dialog)
    if event != '-OK-':
        return None
    input_path = (values.get('-IN-') or '').strip()
    pages = (values.get('-PAGES-') or '').strip()
    output = (values.get('-OUT-') or '').strip()
    if not input_path or not pages or not output:
        return None
    return {'input': input_path, 'pages': pages, 'output': output}


# ---------------------------------------------------------------------------
# Loaded-document page operations
# ---------------------------------------------------------------------------

def _scope_rows(total, current, default='current'):
    return [
        [sg.Radio(_('Current page'), 'SCOPE', default=default == 'current',
                  key='-SCOPE_CURRENT-'),
         sg.Radio(_('All pages'), 'SCOPE', default=default == 'all',
                  key='-SCOPE_ALL-'),
         sg.Radio(_('Pages:'), 'SCOPE', default=default == 'ranges',
                  key='-SCOPE_RANGES-'),
         sg.Input(key='-RANGES-', size=(16, 1))],
    ]


def _scope_from_values(values):
    if values.get('-SCOPE_ALL-'):
        return 'all', ''
    if values.get('-SCOPE_RANGES-'):
        return 'ranges', (values.get('-RANGES-') or '').strip()
    return 'current', ''


def delete_pages_dialog(window, total, current):
    """Delete-pages dialog. Returns ``{'scope', 'spec'}`` or None."""
    layout = [
        [sg.Text(_('Delete pages of the loaded document ({total} pages).',
                   total=total))],
        *_scope_rows(total, current),
        _ok_cancel_row(),
    ]
    dialog = _open_modal(_('Delete Pages'), layout, window)
    event, values = _read_and_close(dialog)
    if event != '-OK-':
        return None
    scope, spec = _scope_from_values(values)
    return {'scope': scope, 'spec': spec}


def rotate_pages_dialog(window, total, current):
    """Rotate-pages dialog. Returns ``{'degrees', 'scope', 'spec'}`` or None."""
    layout = [
        [sg.Text(_('Rotate clockwise by:')),
         sg.Radio('90°', 'DEG', default=True, key='-DEG_90-'),
         sg.Radio('180°', 'DEG', key='-DEG_180-'),
         sg.Radio('270°', 'DEG', key='-DEG_270-')],
        *_scope_rows(total, current),
        _ok_cancel_row(),
    ]
    dialog = _open_modal(_('Rotate Pages'), layout, window)
    event, values = _read_and_close(dialog)
    if event != '-OK-':
        return None
    degrees = 180 if values.get('-DEG_180-') else 270 if values.get('-DEG_270-') else 90
    scope, spec = _scope_from_values(values)
    return {'degrees': degrees, 'scope': scope, 'spec': spec}


def reorder_pages_dialog(window, total, current):
    """Move-page dialog. Returns ``{'src', 'dst'}`` (1-based) or None."""
    pages = list(range(1, total + 1))
    layout = [
        [sg.Text(_('Move page')),
         sg.Spin(values=pages, initial_value=current + 1, key='-SRC-', size=(6, 1)),
         sg.Text(_('to position')),
         sg.Spin(values=pages, initial_value=current + 1, key='-DST-', size=(6, 1))],
        _ok_cancel_row(),
    ]
    dialog = _open_modal(_('Reorder Pages'), layout, window)
    event, values = _read_and_close(dialog)
    if event != '-OK-':
        return None
    try:
        src = int(values.get('-SRC-'))
        dst = int(values.get('-DST-'))
    except (TypeError, ValueError):
        return None
    return {'src': src, 'dst': dst}


def insert_pages_dialog(window, total, current):
    """Insert-pages dialog (blank page or pages from another PDF).

    Returns ``{'mode': 'blank'|'pdf', 'position': int (0-based insert
    index), 'size': 'A4'|'Letter'|'current', 'path': str, 'pages': str}``
    or None."""
    positions = list(range(1, total + 2))
    sizes = [_('A4'), _('Letter'), _('Same as current page')]
    layout = [
        [sg.Radio(_('Blank page'), 'MODE', default=True, key='-MODE_BLANK-'),
         sg.Combo(sizes, default_value=sizes[0], key='-SIZE-', readonly=True,
                  size=(22, 1))],
        [sg.Radio(_('Pages from PDF'), 'MODE', key='-MODE_PDF-')],
        file_open_row('-FILE-', file_types=PDF_FILE_TYPES),
        [sg.Text(_('Pages (e.g. 1-3,7; empty = all):')),
         sg.Input(key='-PAGES-', size=(16, 1))],
        [sg.Text(_('Insert at position:')),
         sg.Spin(values=positions, initial_value=min(current + 2, total + 1),
                 key='-POS-', size=(6, 1))],
        _ok_cancel_row(),
    ]
    dialog = _open_modal(_('Insert Pages'), layout, window)
    event, values = _read_and_close(dialog)
    if event != '-OK-':
        return None
    try:
        position = int(values.get('-POS-')) - 1
    except (TypeError, ValueError):
        position = total
    size_label = values.get('-SIZE-') or sizes[0]
    size = ('A4' if size_label == sizes[0]
            else 'Letter' if size_label == sizes[1] else 'current')
    if values.get('-MODE_PDF-'):
        path = (values.get('-FILE-') or '').strip()
        if not path:
            return None
        return {'mode': 'pdf', 'position': position, 'path': path,
                'pages': (values.get('-PAGES-') or '').strip(),
                'size': size}
    return {'mode': 'blank', 'position': position, 'size': size,
            'path': '', 'pages': ''}


def crop_dialog(window, total, current, width_px, height_px):
    """Crop dialog (numeric margins). Returns
    ``{'margins': (l, t, r, b), 'unit': 'px'|'pt', 'scope':
    'current'|'all'}`` or None."""
    width_pt = width_px * PT_PER_PX
    height_pt = height_px * PT_PER_PX
    layout = [
        [sg.Text(_('Current page: {w_px} x {h_px} px ({w_pt} x {h_pt} pt)',
                   w_px=int(width_px), h_px=int(height_px),
                   w_pt=int(round(width_pt)), h_pt=int(round(height_pt))))],
        [sg.Text(_('Margins to remove:'))],
        [sg.Text(_('Left'), size=(6, 1)), sg.Input('0', key='-L-', size=(8, 1)),
         sg.Text(_('Top'), size=(6, 1)), sg.Input('0', key='-T-', size=(8, 1))],
        [sg.Text(_('Right'), size=(6, 1)), sg.Input('0', key='-R-', size=(8, 1)),
         sg.Text(_('Bottom'), size=(6, 1)), sg.Input('0', key='-B-', size=(8, 1))],
        [sg.Text(_('Unit')),
         sg.Combo(['px', 'pt'], default_value='px', key='-UNIT-', readonly=True,
                  size=(5, 1))],
        [sg.Radio(_('Current page'), 'SCOPE', default=True, key='-SCOPE_CURRENT-'),
         sg.Radio(_('All pages'), 'SCOPE', key='-SCOPE_ALL-')],
        _ok_cancel_row(),
    ]
    dialog = _open_modal(_('Crop Pages'), layout, window)
    event, values = _read_and_close(dialog)
    if event != '-OK-':
        return None
    margins = tuple((values.get(key) or '0').strip() or '0'
                    for key in ('-L-', '-T-', '-R-', '-B-'))
    scope = 'all' if values.get('-SCOPE_ALL-') else 'current'
    return {'margins': margins, 'unit': values.get('-UNIT-') or 'px',
            'scope': scope}


# ---------------------------------------------------------------------------
# Compress / batch / properties
# ---------------------------------------------------------------------------

def compress_dialog(window, default_input=''):
    """Compression dialog. Returns a request dict for
    :func:`validate_compress_request` or None."""
    layout = [
        [sg.Text(_('PDF to compress:'))],
        file_open_row('-IN-', file_types=PDF_FILE_TYPES,
                      default_path=default_input or ''),
        [sg.Radio(_('Raster (flattens pages to images)'), 'MODE', default=True,
                  key='-MODE_RASTER-'),
         sg.Radio(_('Lossless (keeps text layer)'), 'MODE', key='-MODE_LOSSLESS-')],
        [sg.Text(_('DPI (raster mode)')),
         sg.Slider(range=(72, 200), resolution=1, orientation='h', size=(24, 14),
                   default_value=110, key='-DPI-')],
        [sg.Text(_('Image quality')),
         sg.Slider(range=(1, 100), resolution=1, orientation='h', size=(24, 14),
                   default_value=85, key='-QUALITY-')],
        [sg.Text(_('Output file:'))],
        file_open_row('-OUT-', file_types=PDF_FILE_TYPES, save_as=True),
        _ok_cancel_row(),
    ]
    dialog = _open_modal(_('Compress PDF'), layout, window)
    event, values = _read_and_close(dialog)
    if event != '-OK-':
        return None
    mode = 'lossless' if values.get('-MODE_LOSSLESS-') else 'raster'
    try:
        dpi = int(float(values.get('-DPI-') or 110))
    except (TypeError, ValueError):
        dpi = 110
    try:
        quality = int(float(values.get('-QUALITY-') or 85))
    except (TypeError, ValueError):
        quality = 85
    return {'mode': mode,
            'input': (values.get('-IN-') or '').strip(),
            'output': (values.get('-OUT-') or '').strip(),
            'dpi': dpi, 'quality': quality}


def batch_dialog(window, include_ocr=True):
    """Batch dialog. Returns ``{'folder', 'tool', 'out_dir'}`` (tool is a
    BATCH_TOOL_KEYS entry) or None."""
    labels = batch_tool_labels(include_ocr)
    label_list = list(labels)
    layout = [
        [sg.Text(_('Folder with PDF files:'))],
        [sg.Input(key='-FOLDER-', expand_x=True), sg.FolderBrowse()],
        [sg.Text(_('Tool')),
         sg.Combo(label_list, default_value=label_list[0], key='-TOOL-',
                  readonly=True, size=(28, 1))],
        [sg.Text(_('Output folder:'))],
        [sg.Input(key='-OUT-', expand_x=True), sg.FolderBrowse()],
        _ok_cancel_row(),
    ]
    dialog = _open_modal(_('Batch Process'), layout, window)
    event, values = _read_and_close(dialog)
    if event != '-OK-':
        return None
    folder = (values.get('-FOLDER-') or '').strip()
    out_dir = (values.get('-OUT-') or '').strip()
    tool = labels.get(values.get('-TOOL-'))
    if not folder or not out_dir or not tool:
        return None
    return {'folder': folder, 'tool': tool, 'out_dir': out_dir}


def properties_dialog(window, props, default_output=''):
    """Document-properties dialog over ``props`` (from
    ``pdf_ops.read_properties``). Returns ``{'metadata': {title, author,
    subject, keywords}, 'output': path}`` or None. ``output`` may equal the
    source path (overwrite; the handler confirms)."""
    def row(label, key, value):
        return [sg.Text(label, size=(9, 1)),
                sg.Input(default_text='' if value is None else str(value),
                         key=key, size=(42, 1))]

    info_bits = [
        (_('Pages'), props.get('pages')),
        (_('Creator'), props.get('creator')),
        (_('Producer'), props.get('producer')),
        (_('Created'), props.get('created')),
        (_('Modified'), props.get('modified')),
    ]
    info_rows = [[sg.Text(f'{label}: {value}', text_color='gray')]
                 for label, value in info_bits if value not in (None, '')]

    layout = [
        row(_('Title'), '-TITLE-', props.get('title')),
        row(_('Author'), '-AUTHOR-', props.get('author')),
        row(_('Subject'), '-SUBJECT-', props.get('subject')),
        row(_('Keywords'), '-KEYWORDS-', props.get('keywords')),
        *info_rows,
        [sg.Text(_('Save as (keep the source path to overwrite):'))],
        file_open_row('-OUT-', file_types=PDF_FILE_TYPES, save_as=True,
                      default_path=default_output or ''),
        _ok_cancel_row(),
    ]
    dialog = _open_modal(_('Document Properties'), layout, window)
    event, values = _read_and_close(dialog)
    if event != '-OK-':
        return None
    output = (values.get('-OUT-') or '').strip()
    if not output:
        return None
    return {
        'metadata': {
            'title': (values.get('-TITLE-') or '').strip(),
            'author': (values.get('-AUTHOR-') or '').strip(),
            'subject': (values.get('-SUBJECT-') or '').strip(),
            'keywords': (values.get('-KEYWORDS-') or '').strip(),
        },
        'output': output,
    }
