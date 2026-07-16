"""
Review handlers for WorkOnward Read: in-document search and PDF compare.

Multi-window integration note (MENU_SEARCH)
-------------------------------------------
The search finder is a second, NON-MODAL ``sg.Window``. ``main.py``'s single
event loop reads only the main window and must not be modified, so the finder
gets its own short event loop INSIDE the handler: a modal-less
``finder.read(timeout=100)`` loop that runs until the finder is closed. The
background search started with :func:`workonward_read.tasks.run_task` reports its
tuple events (``('-SEARCH-', 'PROGRESS'/'DONE'/'ERROR')``) directly to the
finder window, so no main-loop dispatch (and no ``state.task_callbacks``
entry) is needed for it — the least invasive approach available without
touching ``main.py``. The main window stays visible (and hit navigation
flips its pages / draws on its graph), it just does not dispatch its own
events while the finder is open.

MENU_COMPARE follows the standard single-loop pattern instead: the request
dialog is modal, ``compare.compare_pdfs`` runs via ``run_task`` with the MAIN
window and the default ``'-TASK-'`` key (progress bar + error popup handled
by ``main.py``), and the completion callback stored in
``state.task_callbacks['-TASK-']`` opens the results window, which — being a
leaf modal window — again has its own small loop for the export / text-diff
buttons.

Module-level core functions (``perform_search``, ``hit_rect_to_graph``,
``run_compare``, ``build_result_lines``, ``export_report`` …) are headless
and unit-tested without any GUI (tests/test_review_integration.py).

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import os

import FreeSimpleGUI as sg

from workonward_read import geometry
from workonward_read.compare import compare_pdfs, export_diff_report, text_diff
from workonward_read.dialogs import review as review_dialogs
from workonward_read.dialogs.common import error_popup, info_popup
from workonward_read.handlers.view import flip_to_page
from workonward_read.image_container import ImageContainer
from workonward_read.i18n import _
from workonward_read.search import page_count, search_document
from workonward_read.tasks import run_task


# Text diffs longer than this many lines are truncated in the popup.
TEXT_DIFF_MAX_LINES = 5000

_SEARCH_TASK_KEY = '-SEARCH-'
_EXPORT_TASK_KEY = '-EXPORT-'


# ---------------------------------------------------------------------------
# Search: headless core functions
# ---------------------------------------------------------------------------

def is_pdf_loaded(state):
    """True when a document is loaded AND it came from a PDF file.

    Image imports (PNG/JPEG) have no text layer, so in-document search is
    impossible for them.
    """
    return (bool(getattr(state, 'images', None))
            and bool(getattr(state, 'file_path', None))
            and str(state.file_path).lower().endswith('.pdf'))


def perform_search(state, term, match_case=False, progress_cb=None):
    """Search the loaded PDF (``state.file_path``) for ``term``.

    Raises ValueError for empty/whitespace-only terms; encrypted sources use
    ``state.source_password``. Returns ``list[search.Hit]``.
    """
    term = (term or '').strip()
    if not term:
        raise ValueError(_('Please enter a search term.'))
    return search_document(
        state.file_path, term,
        password=getattr(state, 'source_password', None),
        match_case=match_case, progress_cb=progress_cb)


def format_hit(hit):
    """Render one hit as a listbox line: ``p.N: …context…`` (1-based page)."""
    return f'p.{hit.page_index + 1}: {hit.context}'


def remap_hit_location(hit, journal, original_page_count):
    """Map a search hit (ORIGINAL-document coordinates) through the page-ops
    journal onto the current in-memory document.

    Search always runs on the original PDF file, so both the hit's page
    index and its rectangles must be remapped when pages were deleted,
    moved, inserted, rotated or cropped.

    Args:
        hit: A :class:`workonward_read.search.Hit`.
        journal: ``state.journal`` (a PageOpsJournal) or None.
        original_page_count: Page count of the original PDF file.

    Returns:
        ``(current_page_index, rects_px)`` — ``current_page_index`` is None
        when the hit's page was deleted (the hit cannot be navigated to);
        ``rects_px`` may be empty when every matched rectangle was cropped
        away or the page size is unknown (the page is still shown, just
        without outlines).
    """
    if journal is None or journal.is_empty():
        return hit.page_index, [list(rect) for rect in hit.rects_px]
    current = journal.map_original_index(hit.page_index, original_page_count)
    if current is None:
        return None, []
    ops = journal.transform_ops_for_original(hit.page_index,
                                             original_page_count)
    if not ops:
        return current, [list(rect) for rect in hit.rects_px]
    size = list(getattr(hit, 'page_size_px', None) or [])
    if len(size) != 2:
        return current, []
    rects = []
    for rect in hit.rects_px:
        transformed = geometry.transform_rect(rect, ops, size[0], size[1])
        if transformed is not None:
            rects.append(transformed)
    return current, rects


def hit_rect_to_graph(rect_px, zoom_factor):
    """Scale a hit rectangle from original-image px (y-down) to graph coords.

    The graph is y-up, so coordinates are scaled by ``zoom_factor / 100`` and
    y is negated — exactly like ``annotations.render_on_graph``.

    Returns ``((x0, -y0), (x1, -y1))`` scaled, ready for ``draw_rectangle``.
    """
    factor = float(zoom_factor) / 100.0
    x0, y0, x1, y1 = rect_px
    return (x0 * factor, -y0 * factor), (x1 * factor, -y1 * factor)


def draw_hit_outlines(window, rects_px, zoom_factor):
    """Draw temporary red outline rectangles for one hit on the main graph.

    Returns the list of created figure ids (temporary — never stored as
    annotations; cleared via :func:`clear_temp_figures`).
    """
    graph = window['-GRAPH-']
    figure_ids = []
    for rect in rects_px or []:
        top_left, bottom_right = hit_rect_to_graph(rect, zoom_factor)
        try:
            figure_ids.append(graph.draw_rectangle(
                top_left, bottom_right, line_color='red', line_width=2))
        except Exception:
            pass
    return figure_ids


def clear_temp_figures(window, figure_ids):
    """Delete temporary hit-outline figures. Returns an empty list."""
    graph = window['-GRAPH-']
    for figure_id in figure_ids or []:
        try:
            graph.delete_figure(figure_id)
        except Exception:
            pass
    return []


# ---------------------------------------------------------------------------
# Compare: headless core functions
# ---------------------------------------------------------------------------

def resolve_passwords(request, state):
    """Return a copy of ``request`` with password_a/password_b filled in when
    a compared path is the loaded (possibly encrypted) source document."""
    resolved = dict(request)
    source = getattr(state, 'file_path', None)
    password = getattr(state, 'source_password', None)
    if source and password:
        source = os.path.abspath(str(source))
        if os.path.abspath(resolved['path_a']) == source:
            resolved['password_a'] = password
        if os.path.abspath(resolved['path_b']) == source:
            resolved['password_b'] = password
    return resolved


def run_compare(request, progress_cb=None):
    """Run ``compare.compare_pdfs`` from a plain request dict."""
    return compare_pdfs(
        request['path_a'], request['path_b'],
        dpi=int(request.get('dpi', review_dialogs.DEFAULT_DPI)),
        threshold=int(request.get('threshold', review_dialogs.DEFAULT_THRESHOLD)),
        password_a=request.get('password_a'),
        password_b=request.get('password_b'),
        progress_cb=progress_cb)


def build_result_lines(result):
    """Per-page listbox lines: ``p.N: X.XX% changed - identical|different``."""
    lines = []
    for page in result.pages:
        verdict = _('identical') if page.changed_ratio == 0 else _('different')
        lines.append(f'p.{page.page_index + 1}: '
                     f'{page.changed_ratio * 100.0:.2f}% '
                     + _('changed') + f' - {verdict}')
    return lines


def verdict_text(result):
    """One-line summary verdict for the results window."""
    if result.identical:
        return _('The documents are identical ({pages} pages).',
                 pages=result.page_count_a)
    changed = sum(1 for page in result.pages if page.changed_ratio > 0)
    return _('The documents are different: {changed} of {total} pages '
             'changed (A: {a} pages, B: {b} pages).',
             changed=changed, total=len(result.pages),
             a=result.page_count_a, b=result.page_count_b)


def export_report(result, request, output_pdf):
    """Export the side-by-side diff report PDF at the compare DPI."""
    export_diff_report(
        result, request['path_a'], request['path_b'], output_pdf,
        dpi=int(request.get('dpi', review_dialogs.DEFAULT_DPI)),
        password_a=request.get('password_a'),
        password_b=request.get('password_b'))
    return output_pdf


def run_text_diff(request):
    """Run ``compare.text_diff`` from a plain request dict."""
    return text_diff(
        request['path_a'], request['path_b'],
        password_a=request.get('password_a'),
        password_b=request.get('password_b'))


def format_text_diff(diff_lines, max_lines=TEXT_DIFF_MAX_LINES):
    """Join unified-diff lines, truncating beyond ``max_lines`` lines."""
    if not diff_lines:
        return _('No text differences found.')
    if len(diff_lines) <= max_lines:
        return '\n'.join(diff_lines)
    shown = '\n'.join(diff_lines[:max_lines])
    return shown + '\n' + _('… truncated ({count} more lines)',
                            count=len(diff_lines) - max_lines)


# ---------------------------------------------------------------------------
# MENU_SEARCH handler (window-facing wrapper)
# ---------------------------------------------------------------------------

def search(window, state):
    """Open the non-modal finder window and drive it (see module docstring)."""
    if not is_pdf_loaded(state):
        info_popup(window, _(
            'Search requires a loaded PDF document. Imported images have '
            'no text layer, so searching them is not possible.'))
        return

    finder = review_dialogs.open_search_window(window)
    hits = []
    locations = []   # per hit: (current_page_index or None, remapped rects)
    temp_ids = []
    selected = -1
    searching = False

    def show_hit(index):
        """Select hit ``index``: flip page, outline rects at current zoom.

        Hits are remapped through ``state.journal`` (search runs on the
        original file): hits on deleted pages are only marked, cropped-away
        match areas show the page without outlines plus a status note.
        """
        nonlocal selected, temp_ids
        if not hits:
            return
        index = max(0, min(int(index), len(hits) - 1))
        selected = index
        hit = hits[index]
        current, rects = (locations[index] if index < len(locations)
                          else (hit.page_index, hit.rects_px))
        try:
            finder['-RESULTS-'].update(set_to_index=[index],
                                       scroll_to_index=index)
        except Exception:
            pass
        counter = f'{index + 1} / {len(hits)}'
        # flip_to_page redraws the graph (erasing old temp figures too), the
        # explicit clear keeps the bookkeeping exact when the page is unchanged.
        temp_ids = clear_temp_figures(window, temp_ids)
        if current is None:
            # The page was deleted by a page op: skip navigation.
            finder['-COUNT-'].update(counter + ' ' + _('(page removed)'))
            return
        if hit.rects_px and not rects:
            counter += ' ' + _('(match area cropped)')
        finder['-COUNT-'].update(counter)
        state.current_page = flip_to_page(window, state.images,
                                          current, state)
        temp_ids = draw_hit_outlines(window, rects,
                                     ImageContainer.zoom_factor)

    while True:
        event, values = finder.read(timeout=100)

        if event in (sg.WINDOW_CLOSED, '-CLOSE-'):
            break
        if event == sg.TIMEOUT_EVENT:
            continue

        # Background search events: ('-SEARCH-', 'PROGRESS'/'DONE'/'ERROR')
        if (isinstance(event, tuple) and len(event) == 2
                and event[0] == _SEARCH_TASK_KEY):
            payload = (values or {}).get(event)
            if event[1] == 'PROGRESS':
                continue
            searching = False
            try:
                finder['-FIND-'].update(disabled=False)
            except Exception:
                pass
            if event[1] == 'ERROR':
                finder['-COUNT-'].update('0 / 0')
                error_popup(finder, _('error_occurred'), payload)
            elif event[1] == 'DONE':
                hits = payload or []
                locations = []
                if hits:
                    if state.journal is None or state.journal.is_empty():
                        locations = [(hit.page_index,
                                      [list(r) for r in hit.rects_px])
                                     for hit in hits]
                    else:
                        try:
                            total = page_count(
                                state.file_path,
                                getattr(state, 'source_password', None))
                        except Exception:
                            total = max(hit.page_index for hit in hits) + 1
                        locations = [
                            remap_hit_location(hit, state.journal, total)
                            for hit in hits]
                temp_ids = clear_temp_figures(window, temp_ids)
                lines = []
                for hit, (current, _rects) in zip(hits, locations):
                    line = format_hit(hit)
                    if current is None:
                        line += ' ' + _('(page removed)')
                    lines.append(line)
                finder['-RESULTS-'].update(values=lines)
                if hits:
                    show_hit(0)
                else:
                    selected = -1
                    finder['-COUNT-'].update('0 / 0')
                    info_popup(finder, _('No matches found.'))
            continue

        if event == '-FIND-':
            if searching:
                continue
            term = (values.get('-TERM-') or '').strip()
            if not term:
                error_popup(finder, _('Please enter a search term.'))
                continue
            searching = True
            finder['-FIND-'].update(disabled=True)
            finder['-COUNT-'].update(_('Searching…'))
            run_task(finder, perform_search, state, term,
                     bool(values.get('-CASE-')), key=_SEARCH_TASK_KEY)

        elif event == '-RESULTS-':
            try:
                indexes = finder['-RESULTS-'].get_indexes()
            except Exception:
                indexes = ()
            if indexes:
                show_hit(indexes[0])

        elif event == '-PREV-':
            show_hit(selected - 1)

        elif event == '-NEXT-':
            show_hit(selected + 1)

    clear_temp_figures(window, temp_ids)
    finder.close()


# ---------------------------------------------------------------------------
# MENU_COMPARE handler (window-facing wrapper)
# ---------------------------------------------------------------------------

def _show_compare_results(window, state, request, result):
    """Results window loop: per-page list, report export, text diff."""
    lines = build_result_lines(result)
    results_window = review_dialogs.open_compare_results_window(
        window, lines, verdict_text(result))
    exporting = False

    while True:
        event, values = results_window.read()

        if event in (sg.WINDOW_CLOSED, '-CLOSE-'):
            break

        # Report export task events: ('-EXPORT-', 'DONE'/'ERROR'/'PROGRESS')
        if (isinstance(event, tuple) and len(event) == 2
                and event[0] == _EXPORT_TASK_KEY):
            if event[1] == 'PROGRESS':
                continue
            exporting = False
            try:
                results_window['-EXPORT-'].update(disabled=False)
            except Exception:
                pass
            payload = (values or {}).get(event)
            if event[1] == 'ERROR':
                error_popup(results_window, _('error_occurred'), payload)
            elif event[1] == 'DONE':
                info_popup(results_window,
                           _('Report saved: {filename}',
                             filename=os.path.basename(str(payload))))
            continue

        if event == '-EXPORT-':
            if exporting:
                continue
            default_name = 'compare_report.pdf'
            save_path = sg.popup_get_file(
                _('Save report PDF'), save_as=True, no_window=True,
                keep_on_top=True, show_hidden=True,
                file_types=(('PDF', '*.pdf *.PDF'),),
                default_extension='.pdf', default_path=default_name)
            if not save_path:
                continue
            exporting = True
            results_window['-EXPORT-'].update(disabled=True)
            run_task(results_window, export_report, result, request,
                     save_path, key=_EXPORT_TASK_KEY)

        elif event == '-TEXTDIFF-':
            try:
                diff_lines = run_text_diff(request)
            except Exception as exc:
                error_popup(results_window, _('error_occurred'), exc)
                continue
            review_dialogs.show_text_diff_popup(
                results_window, format_text_diff(diff_lines))

    results_window.close()


def compare(window, state):
    """Ask for a compare request, run it in the background, show results."""
    request = review_dialogs.compare_dialog(window, state)
    if request is None:
        return
    request = resolve_passwords(request, state)

    def on_done(done_window, done_state, result):
        _show_compare_results(done_window, done_state, request, result)

    state.task_callbacks['-TASK-'] = on_done
    state.busy = True
    run_task(window, run_compare, request, key='-TASK-')


HANDLERS = {
    'MENU_COMPARE': compare,
    'MENU_SEARCH': search,
}
