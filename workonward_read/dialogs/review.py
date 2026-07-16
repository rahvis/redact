"""
Review dialogs for WorkOnward Read: the non-modal in-document search finder
window, the compare-PDFs request dialog and the compare results window.

``compare_dialog`` follows the standard dialog contract (modal, keep-on-top,
centered, returns a plain request dict or None). ``open_search_window`` and
``open_compare_results_window`` intentionally return the created NON-MODAL
``sg.Window`` instead: both are registered as aux windows in
``state.aux_windows`` by :mod:`workonward_read.handlers.review` and driven by
main.py's ``read_all_windows()`` loop (aux-window contract in
docs/dev-architecture.md).

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import os

import FreeSimpleGUI as sg

from workonward_read.dialogs.common import centered, error_popup, file_open_row
from workonward_read.i18n import _


DPI_CHOICES = [75, 100, 150]
DEFAULT_DPI = 100
DEFAULT_THRESHOLD = 24

_PDF_FILE_TYPES = (('PDF', '*.pdf *.PDF'),)


# ---------------------------------------------------------------------------
# Search finder (non-modal second window, driven by handlers/review.py)
# ---------------------------------------------------------------------------

def open_search_window(window):
    """Create and return the NON-MODAL search finder window.

    Keys: ``-TERM-`` (entry), ``-CASE-`` (match-case checkbox), ``-FIND-``,
    ``-RESULTS-`` (hit listbox, one 'p.N: …context…' line per hit),
    ``-PREV-`` / ``-NEXT-`` (hit navigation), ``-COUNT-`` (hit counter) and
    ``-CLOSE-``. The caller registers it in ``state.aux_windows``; main.py's
    loop routes its events and closes it.
    """
    layout = [
        [sg.Text(_('Find')),
         sg.Input(key='-TERM-', size=(32, 1), focus=True),
         sg.Checkbox(_('Match case'), key='-CASE-'),
         sg.Button(_('Find'), key='-FIND-', bind_return_key=True)],
        [sg.Listbox(values=[], key='-RESULTS-', size=(72, 12),
                    enable_events=True, expand_x=True, expand_y=True,
                    horizontal_scroll=True)],
        [sg.Button(_('Previous'), key='-PREV-'),
         sg.Button(_('Next'), key='-NEXT-'),
         sg.Text('0 / 0', key='-COUNT-', size=(16, 1)),
         sg.Push(),
         sg.Button(_('Close'), key='-CLOSE-')],
    ]
    return sg.Window(
        _('Search Document'), layout, keep_on_top=True, finalize=True,
        resizable=True, location=centered(window, 620, 330))


# ---------------------------------------------------------------------------
# Compare request dialog (standard modal dialog contract)
# ---------------------------------------------------------------------------

def compare_dialog(window, state):
    """Compare-PDFs dialog: file A (defaults to the loaded PDF), file B,
    render DPI (75/100/150) and the pixel threshold option.

    Returns ``{'path_a', 'path_b', 'dpi', 'threshold'}`` or None on cancel.
    """
    default_a = ''
    file_path = getattr(state, 'file_path', None)
    if file_path and str(file_path).lower().endswith('.pdf'):
        default_a = str(file_path)

    layout = [
        [sg.Text(_('File A (original)'))],
        file_open_row('-FILE_A-', file_types=_PDF_FILE_TYPES,
                      default_path=default_a),
        [sg.Text(_('File B (revised)'))],
        file_open_row('-FILE_B-', file_types=_PDF_FILE_TYPES),
        [sg.Text(_('Resolution (DPI)')),
         sg.Combo(DPI_CHOICES, default_value=DEFAULT_DPI, key='-DPI-',
                  readonly=True, size=(6, 1)),
         sg.Text(_('Pixel threshold')),
         sg.Spin(values=list(range(1, 256)),
                 initial_value=DEFAULT_THRESHOLD,
                 key='-THRESHOLD-', size=(5, 1))],
        [sg.Push(),
         sg.Button(_('OK'), key='-OK-'),
         sg.Button(_('Cancel'), key='-CANCEL-')],
    ]
    dialog = sg.Window(
        _('Compare PDFs'), layout, modal=True, keep_on_top=True,
        finalize=True, location=centered(window))

    result = None
    while True:
        event, values = dialog.read()
        if event in (sg.WINDOW_CLOSED, '-CANCEL-'):
            break
        if event == '-OK-':
            path_a = (values.get('-FILE_A-') or '').strip()
            path_b = (values.get('-FILE_B-') or '').strip()
            if not path_a or not os.path.isfile(path_a):
                error_popup(dialog, _('Please choose an existing file A.'))
                continue
            if not path_b or not os.path.isfile(path_b):
                error_popup(dialog, _('Please choose an existing file B.'))
                continue
            try:
                dpi = int(values.get('-DPI-') or DEFAULT_DPI)
            except (TypeError, ValueError):
                dpi = DEFAULT_DPI
            try:
                threshold = max(1, min(255, int(values.get('-THRESHOLD-')
                                                or DEFAULT_THRESHOLD)))
            except (TypeError, ValueError):
                threshold = DEFAULT_THRESHOLD
            result = {'path_a': path_a, 'path_b': path_b,
                      'dpi': dpi, 'threshold': threshold}
            break

    dialog.close()
    return result


# ---------------------------------------------------------------------------
# Compare results window (driven by handlers/review.py)
# ---------------------------------------------------------------------------

def open_compare_results_window(window, lines, verdict):
    """Create and return the NON-MODAL compare results window.

    Keys: ``-PAGES-`` (per-page listbox), ``-EXPORT-`` (report PDF save-as),
    ``-TEXTDIFF-`` (scrollable text diff popup) and ``-CLOSE-``. The caller
    registers it in ``state.aux_windows``; main.py's loop routes its events
    and closes it.
    """
    layout = [
        [sg.Text(verdict, key='-VERDICT-')],
        [sg.Listbox(values=list(lines), key='-PAGES-', size=(64, 14),
                    expand_x=True, expand_y=True, horizontal_scroll=True)],
        [sg.Button(_('Export report PDF…'), key='-EXPORT-'),
         sg.Button(_('Show text diff'), key='-TEXTDIFF-'),
         sg.Push(),
         sg.Button(_('Close'), key='-CLOSE-')],
    ]
    return sg.Window(
        _('Compare Results'), layout, modal=False, keep_on_top=True,
        finalize=True, resizable=True, location=centered(window, 560, 400))


def show_text_diff_popup(window, text):
    """Show the (possibly truncated) unified text diff in a scrollable popup."""
    sg.popup_scrolled(
        text, title=_('Text diff'), size=(100, 32),
        keep_on_top=True, location=centered(window, 820, 540))
