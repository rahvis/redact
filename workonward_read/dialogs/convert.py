"""
Convert dialogs for WorkOnward Read: PDF -> images/text/word/html, images ->
PDF (with ordering) and OCR (source / language / output plus the
tesseract-missing helper).

Every dialog is modal, keep-on-top, centered over the parent window and
returns a plain request dict (or a path string for the tesseract locator)
or None when cancelled.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
(c) 2026 WorkOnward Read contributors
"""

import os

import FreeSimpleGUI as sg

from workonward_read.dialogs.common import (error_popup,
                                            open_modal as _open_modal)
from workonward_read.i18n import _


_PDF_FILE_TYPES = (('PDF', '*.pdf *.PDF'),)
_IMAGE_FILE_TYPES = (('Images', '*.png *.PNG *.jpg *.JPG *.jpeg *.JPEG'),)

# Per-target dialog config: (title, output extension, needs layout note)
_TARGETS = {
    'images': (lambda: _('Convert to Images…'), None, False),
    'text': (lambda: _('Convert to Text…'), '.txt', False),
    'word': (lambda: _('Convert to Word…'), '.docx', True),
    'html': (lambda: _('Convert to HTML…'), '.html', True),
}

_IMAGE_FORMATS = ['PNG', 'JPEG']
_DPI_CHOICES = list(range(50, 601, 25))


def _source_rows(state, file_types=_PDF_FILE_TYPES):
    """Source selection rows: loaded document (if any) or picked file."""
    has_loaded = bool(getattr(state, 'file_path', None))
    loaded_name = os.path.basename(state.file_path) if has_loaded else ''
    return [
        [sg.Radio(_('Loaded document ({name})', name=loaded_name),
                  'SRC', default=has_loaded, disabled=not has_loaded,
                  key='-SRC_LOADED-')],
        [sg.Radio(_('Choose a file:'), 'SRC', default=not has_loaded,
                  key='-SRC_FILE-')],
        [sg.Input(default_text='', key='-INPUT-', expand_x=True),
         sg.FileBrowse(_('Browse'), file_types=file_types)],
    ]


def _read_source(values, state, window):
    """Validate and return (use_loaded, input_path) or None on bad input."""
    if values.get('-SRC_LOADED-') and getattr(state, 'file_path', None):
        return True, state.file_path
    input_path = (values.get('-INPUT-') or '').strip()
    if not input_path or not os.path.isfile(input_path):
        error_popup(window, _('Please choose an existing input file.'))
        return None
    return False, input_path


def _default_output(state, values, suffix, ext):
    """Suggest an output filename derived from the chosen source."""
    source = None
    if values is not None and (values.get('-INPUT-') or '').strip():
        source = values.get('-INPUT-').strip()
    elif getattr(state, 'file_path', None):
        source = state.file_path
    if not source:
        return ''
    stem = os.path.splitext(os.path.basename(source))[0]
    return f'{stem}{suffix}{ext}'


def convert_dialog(window, state, target):
    """
    Shared PDF-conversion dialog parameterized by ``target`` ('images',
    'text', 'word' or 'html').

    Returns a request dict::

        {'target': ..., 'use_loaded': bool, 'input_path': str,
         'output': str, 'fmt': 'PNG'|'JPEG', 'dpi': int,
         'embed_page_images': bool}

    or None when cancelled. For 'images' the output is a directory; for the
    other targets it is the output file path.
    """
    if target not in _TARGETS:
        raise ValueError(f'Unknown conversion target: {target!r}')
    title_fn, out_ext, layout_note = _TARGETS[target]

    rows = [[sg.Frame(_('Source'), _source_rows(state))]]

    if target == 'images':
        rows.append([sg.Text(_('Format')),
                     sg.Combo(_IMAGE_FORMATS, default_value='PNG',
                              key='-FORMAT-', readonly=True, size=(8, 1)),
                     sg.Text(_('DPI')),
                     sg.Spin(values=_DPI_CHOICES, initial_value=200,
                             key='-DPI-', size=(6, 1))])
        rows.append([sg.Text(_('Output folder'))])
        rows.append([sg.Input(default_text='', key='-OUTPUT-', expand_x=True),
                     sg.FolderBrowse(_('Browse'))])
    else:
        if layout_note:
            rows.append([sg.Text(
                _('Note: layout is not preserved — paragraphs only.'),
                text_color='gray')])
        if target == 'html':
            rows.append([sg.Checkbox(_('Embed page images'),
                                     default=False, key='-EMBED-')])
        rows.append([sg.Text(_('Output file'))])
        rows.append([sg.Input(default_text='', key='-OUTPUT-', expand_x=True),
                     sg.FileSaveAs(_('Browse'),
                                   file_types=((out_ext.lstrip('.').upper(),
                                                f'*{out_ext}'),),
                                   default_extension=out_ext)])

    rows.append([sg.Push(),
                 sg.Button(_('OK'), key='-OK-'),
                 sg.Button(_('Cancel'), key='-CANCEL-')])

    dialog = _open_modal(title_fn(), rows, window)
    result = None
    while True:
        event, values = dialog.read()
        if event in (sg.WINDOW_CLOSED, '-CANCEL-'):
            break
        if event != '-OK-':
            continue

        source = _read_source(values, state, window)
        if source is None:
            continue
        use_loaded, input_path = source

        output = (values.get('-OUTPUT-') or '').strip()
        if not output:
            output = _default_output(
                state, values, '_converted', out_ext or '')
        if not output:
            error_popup(window, _('Please choose an output location.'))
            continue
        if out_ext and not output.lower().endswith(out_ext):
            output += out_ext

        try:
            dpi = int(values.get('-DPI-') or 200)
        except (TypeError, ValueError):
            dpi = 200

        result = {
            'target': target,
            'use_loaded': use_loaded,
            'input_path': input_path,
            'output': output,
            'fmt': values.get('-FORMAT-') or 'PNG',
            'dpi': dpi,
            'embed_page_images': bool(values.get('-EMBED-')),
        }
        break

    dialog.close()
    return result


def images_to_pdf_dialog(window, state):
    """
    Multi-select image picker with an order list plus save-as output.

    Returns ``{'image_paths': [str, ...], 'output': str}`` or None.
    """
    layout = [
        [sg.Text(_('Images (top to bottom = page order)'))],
        [sg.Listbox(values=[], key='-LIST-', size=(56, 8),
                    select_mode=sg.LISTBOX_SELECT_MODE_SINGLE)],
        [sg.Input(key='-ADD-', visible=False, enable_events=True),
         sg.FilesBrowse(_('Add images…'), target='-ADD-',
                        file_types=_IMAGE_FILE_TYPES),
         sg.Button(_('Move up'), key='-UP-'),
         sg.Button(_('Move down'), key='-DOWN-'),
         sg.Button(_('Remove'), key='-REMOVE-')],
        [sg.Text(_('Output file'))],
        [sg.Input(default_text='', key='-OUTPUT-', expand_x=True),
         sg.FileSaveAs(_('Browse'), file_types=_PDF_FILE_TYPES,
                       default_extension='.pdf')],
        [sg.Push(),
         sg.Button(_('OK'), key='-OK-'),
         sg.Button(_('Cancel'), key='-CANCEL-')],
    ]
    dialog = _open_modal(_('Images to PDF…'), layout, window)

    paths = []
    result = None
    while True:
        event, values = dialog.read()
        if event in (sg.WINDOW_CLOSED, '-CANCEL-'):
            break

        if event == '-ADD-':
            raw = values.get('-ADD-') or ''
            for path in raw.split(';'):
                path = path.strip()
                if path and path not in paths:
                    paths.append(path)
            dialog['-LIST-'].update(values=paths)

        elif event in ('-UP-', '-DOWN-'):
            selection = values.get('-LIST-') or []
            if selection:
                idx = paths.index(selection[0])
                new_idx = idx - 1 if event == '-UP-' else idx + 1
                if 0 <= new_idx < len(paths):
                    paths[idx], paths[new_idx] = paths[new_idx], paths[idx]
                    dialog['-LIST-'].update(
                        values=paths, set_to_index=[new_idx])

        elif event == '-REMOVE-':
            selection = values.get('-LIST-') or []
            if selection and selection[0] in paths:
                paths.remove(selection[0])
                dialog['-LIST-'].update(values=paths)

        elif event == '-OK-':
            if not paths:
                error_popup(window, _('Please add at least one image.'))
                continue
            output = (values.get('-OUTPUT-') or '').strip()
            if not output:
                error_popup(window, _('Please choose an output location.'))
                continue
            if not output.lower().endswith('.pdf'):
                output += '.pdf'
            result = {'image_paths': list(paths), 'output': output}
            break

    dialog.close()
    return result


def tesseract_missing_dialog(window):
    """
    Info dialog shown when no tesseract binary can be found: install hints
    plus a 'Locate binary…' file picker.

    Returns the chosen binary path (str) or None.
    """
    hints = [
        _('Tesseract OCR was not found on this system.'),
        _('Install it with one of:'),
        '    macOS:      brew install tesseract',
        '    Windows:  winget install UB-Mannheim.TesseractOCR',
        '    Linux:       sudo apt install tesseract-ocr',
        _('Or locate an existing tesseract binary below:'),
    ]
    layout = [[sg.Text(line)] for line in hints]
    layout.append([
        sg.Input(default_text='', key='-BINARY-', expand_x=True),
        sg.FileBrowse(_('Locate binary…')),
    ])
    layout.append([sg.Push(),
                   sg.Button(_('OK'), key='-OK-'),
                   sg.Button(_('Cancel'), key='-CANCEL-')])

    dialog = _open_modal(_('Tesseract OCR not found'), layout, window)
    result = None
    while True:
        event, values = dialog.read()
        if event in (sg.WINDOW_CLOSED, '-CANCEL-'):
            break
        if event == '-OK-':
            path = (values.get('-BINARY-') or '').strip()
            if not path or not os.path.isfile(path):
                error_popup(window, _('Please choose an existing file.'))
                continue
            result = path
            break

    dialog.close()
    return result


def ocr_dialog(window, state, languages):
    """
    OCR dialog: source (loaded document as currently redacted, or a PDF
    file), tesseract language and output save-as.

    Returns ``{'use_loaded': bool, 'input_path': str|None, 'lang': str,
    'output': str}`` or None.
    """
    languages = list(languages) or ['eng']
    default_lang = 'eng' if 'eng' in languages else languages[0]
    has_loaded = bool(getattr(state, 'images', None))
    loaded_name = os.path.basename(state.file_path) \
        if getattr(state, 'file_path', None) else _('untitled')

    layout = [
        [sg.Frame(_('Source'), [
            [sg.Radio(_('Loaded document, as currently redacted ({name})',
                        name=loaded_name),
                      'SRC', default=has_loaded, disabled=not has_loaded,
                      key='-SRC_LOADED-')],
            [sg.Radio(_('Choose a PDF file:'), 'SRC', default=not has_loaded,
                      key='-SRC_FILE-')],
            [sg.Input(default_text='', key='-INPUT-', expand_x=True),
             sg.FileBrowse(_('Browse'), file_types=_PDF_FILE_TYPES)],
        ])],
        [sg.Text(_('Language')),
         sg.Combo(languages, default_value=default_lang, key='-LANG-',
                  readonly=True, size=(12, 1))],
        [sg.Text(_('Output file'))],
        [sg.Input(default_text='', key='-OUTPUT-', expand_x=True),
         sg.FileSaveAs(_('Browse'), file_types=_PDF_FILE_TYPES,
                       default_extension='.pdf')],
        [sg.Push(),
         sg.Button(_('OK'), key='-OK-'),
         sg.Button(_('Cancel'), key='-CANCEL-')],
    ]
    dialog = _open_modal(_('OCR (make searchable PDF)…'), layout, window)

    result = None
    while True:
        event, values = dialog.read()
        if event in (sg.WINDOW_CLOSED, '-CANCEL-'):
            break
        if event != '-OK-':
            continue

        use_loaded = bool(values.get('-SRC_LOADED-')) and has_loaded
        input_path = None
        if not use_loaded:
            input_path = (values.get('-INPUT-') or '').strip()
            if not input_path or not os.path.isfile(input_path):
                error_popup(window, _('Please choose an existing input file.'))
                continue

        output = (values.get('-OUTPUT-') or '').strip()
        if not output:
            error_popup(window, _('Please choose an output location.'))
            continue
        if not output.lower().endswith('.pdf'):
            output += '.pdf'

        result = {
            'use_loaded': use_loaded,
            'input_path': input_path,
            'lang': values.get('-LANG-') or default_lang,
            'output': output,
        }
        break

    dialog.close()
    return result
