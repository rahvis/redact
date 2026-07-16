"""
Review handlers for WorkOnward Read: in-document search and PDF compare.

Multi-window integration note
-----------------------------
Both the search finder (MENU_SEARCH) and the compare results window
(MENU_COMPARE) are NON-MODAL secondary windows following the aux-window
contract (docs/dev-architecture.md): the handler creates the window,
registers ``state.aux_windows[window] = handler_fn`` and returns
immediately. ``main.py``'s ``read_all_windows()`` loop routes the window's
events to the handler, which returns True to stay open; on a falsy return
(or when the window is closed) the loop closes and unregisters it. There
are NO nested event loops here, so the main window keeps dispatching its
own events while these windows are open.

All background work (the search itself, ``compare.compare_pdfs``, the
report export and the text diff) runs via ``tasks.run_task`` against the
MAIN window under per-invocation unique task keys; results arrive through
the ``on_done`` callbacks that main.py's central task handling invokes.
Export and text diff disable their buttons while running and re-enable
them from the DONE/ERROR callbacks.

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


def navigate_to_hit(window, state, current, rects, temp_ids,
                    zoom_factor=None):
    """Show one (already remapped) hit on the main window.

    Clears the previous temporary outlines, flips to the hit's page ONLY
    when it is not the currently displayed one (flipping re-renders the
    whole graph — skipping it keeps clicking through same-page hits cheap),
    and draws the hit outlines at the current zoom.

    Returns the new list of temporary figure ids.
    """
    temp_ids = clear_temp_figures(window, temp_ids)
    if current != state.current_page:
        state.current_page = flip_to_page(window, state.images, current,
                                          state)
    if zoom_factor is None:
        zoom_factor = ImageContainer.zoom_factor
    return draw_hit_outlines(window, rects, zoom_factor)


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
    """Open the non-modal finder as an aux window (see module docstring)."""
    if not is_pdf_loaded(state):
        info_popup(window, _(
            'Search requires a loaded PDF document. Imported images have '
            'no text layer, so searching them is not possible.'))
        return

    # Only one finder at a time: focus the existing one instead.
    for aux_window, handler in state.aux_windows.items():
        if getattr(handler, 'aux_kind', None) == 'search-finder':
            try:
                aux_window.bring_to_front()
            except Exception:
                pass
            return

    finder = review_dialogs.open_search_window(window)
    state.aux_windows[finder] = _make_finder_handler(window, state, finder)


def _make_finder_handler(window, state, finder):
    """Build the aux-window handler driving the search finder.

    ``window`` is the MAIN window (hit navigation flips its pages and draws
    on its graph); ``finder`` is the non-modal finder window. The returned
    handler follows the aux-window contract:
    ``handler(finder, state, event, values) -> bool keep_open``.
    """
    ctx = {
        'hits': [],
        'locations': [],   # per hit: (current_page_index or None, rects)
        'temp_ids': [],
        'selected': -1,
        'searching': False,
    }

    def show_hit(index):
        """Select hit ``index``: flip page, outline rects at current zoom.

        Hits are remapped through ``state.journal`` (search runs on the
        original file): hits on deleted pages are only marked, cropped-away
        match areas show the page without outlines plus a status note.
        """
        hits = ctx['hits']
        if not hits:
            return
        index = max(0, min(int(index), len(hits) - 1))
        ctx['selected'] = index
        hit = hits[index]
        current, rects = (ctx['locations'][index]
                          if index < len(ctx['locations'])
                          else (hit.page_index, hit.rects_px))
        try:
            finder['-RESULTS-'].update(set_to_index=[index],
                                       scroll_to_index=index)
        except Exception:
            pass
        counter = f'{index + 1} / {len(hits)}'
        if current is None:
            # The page was deleted by a page op: skip navigation.
            ctx['temp_ids'] = clear_temp_figures(window, ctx['temp_ids'])
            finder['-COUNT-'].update(counter + ' ' + _('(page removed)'))
            return
        if hit.rects_px and not rects:
            counter += ' ' + _('(match area cropped)')
        finder['-COUNT-'].update(counter)
        # navigate_to_hit skips the full page flip when the hit is on the
        # currently displayed page (clear + redraw outlines only).
        ctx['temp_ids'] = navigate_to_hit(window, state, current, rects,
                                          ctx['temp_ids'])

    def on_search_done(_win, st, hits):
        ctx['searching'] = False
        if finder.was_closed():
            return
        try:
            finder['-FIND-'].update(disabled=False)
        except Exception:
            pass
        hits = hits or []
        ctx['hits'] = hits
        locations = []
        if hits:
            if st.journal is None or st.journal.is_empty():
                locations = [(hit.page_index,
                              [list(r) for r in hit.rects_px])
                             for hit in hits]
            else:
                try:
                    total = page_count(
                        st.file_path,
                        getattr(st, 'source_password', None))
                except Exception:
                    total = max(hit.page_index for hit in hits) + 1
                locations = [remap_hit_location(hit, st.journal, total)
                             for hit in hits]
        ctx['locations'] = locations
        ctx['temp_ids'] = clear_temp_figures(window, ctx['temp_ids'])
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
            ctx['selected'] = -1
            finder['-COUNT-'].update('0 / 0')
            info_popup(finder, _('No matches found.'))

    def on_search_error(_win, _st, _payload):
        # Cleanup hook: main.py shows the standard error popup afterwards.
        ctx['searching'] = False
        if finder.was_closed():
            return
        try:
            finder['-FIND-'].update(disabled=False)
            finder['-COUNT-'].update('0 / 0')
        except Exception:
            pass

    def handle(finder_window, st, event, values):
        if event in (sg.WINDOW_CLOSED, '-CLOSE-'):
            ctx['temp_ids'] = clear_temp_figures(window, ctx['temp_ids'])
            return False

        if event == '-FIND-':
            if ctx['searching']:
                return True
            term = ((values or {}).get('-TERM-') or '').strip()
            if not term:
                error_popup(finder_window, _('Please enter a search term.'))
                return True
            ctx['searching'] = True
            finder_window['-FIND-'].update(disabled=True)
            finder_window['-COUNT-'].update(_('Searching…'))
            run_task(window, perform_search, st, term,
                     bool((values or {}).get('-CASE-')),
                     on_done=on_search_done, on_error=on_search_error)

        elif event == '-RESULTS-':
            try:
                indexes = finder_window['-RESULTS-'].get_indexes()
            except Exception:
                indexes = ()
            if indexes:
                show_hit(indexes[0])

        elif event == '-PREV-':
            show_hit(ctx['selected'] - 1)

        elif event == '-NEXT-':
            show_hit(ctx['selected'] + 1)

        return True

    handle.aux_kind = 'search-finder'
    return handle


# ---------------------------------------------------------------------------
# MENU_COMPARE handler (window-facing wrapper)
# ---------------------------------------------------------------------------

def _show_compare_results(window, state, request, result):
    """Create + register the non-modal compare results aux window."""
    lines = build_result_lines(result)
    results_window = review_dialogs.open_compare_results_window(
        window, lines, verdict_text(result))
    state.aux_windows[results_window] = _make_results_handler(
        window, state, request, result, results_window)
    return results_window


def _make_results_handler(window, state, request, result, results_window):
    """Build the aux-window handler for the compare results window.

    ``window`` is the MAIN window (task events report there). Export and
    text diff run as background tasks with their buttons disabled while
    running; results are shown from the DONE callbacks.
    """
    running = {'export': False, 'textdiff': False}

    def _reset_button(kind, key):
        running[kind] = False
        if not results_window.was_closed():
            try:
                results_window[key].update(disabled=False)
            except Exception:
                pass

    def on_export_done(win, _st, payload):
        _reset_button('export', '-EXPORT-')
        parent = win if results_window.was_closed() else results_window
        info_popup(parent, _('Report saved: {filename}',
                             filename=os.path.basename(str(payload))))

    def on_export_error(_win, _st, _payload):
        # Cleanup hook: main.py shows the standard error popup afterwards.
        _reset_button('export', '-EXPORT-')

    def on_textdiff_done(_win, _st, diff_lines):
        _reset_button('textdiff', '-TEXTDIFF-')
        if results_window.was_closed():
            return
        review_dialogs.show_text_diff_popup(
            results_window, format_text_diff(diff_lines))

    def on_textdiff_error(_win, _st, _payload):
        _reset_button('textdiff', '-TEXTDIFF-')

    def handle(results_win, st, event, values):
        if event in (sg.WINDOW_CLOSED, '-CLOSE-'):
            return False

        if event == '-EXPORT-':
            if running['export']:
                return True
            default_name = 'compare_report.pdf'
            save_path = sg.popup_get_file(
                _('Save report PDF'), save_as=True, no_window=True,
                keep_on_top=True, show_hidden=True,
                file_types=(('PDF', '*.pdf *.PDF'),),
                default_extension='.pdf', default_path=default_name)
            if not save_path:
                return True
            running['export'] = True
            results_win['-EXPORT-'].update(disabled=True)
            run_task(window, export_report, result, request, save_path,
                     on_done=on_export_done, on_error=on_export_error)

        elif event == '-TEXTDIFF-':
            # Text extraction of both PDFs is slow on large documents:
            # run it as a background task, button disabled meanwhile.
            if running['textdiff']:
                return True
            running['textdiff'] = True
            results_win['-TEXTDIFF-'].update(disabled=True)
            run_task(window, run_text_diff, request,
                     on_done=on_textdiff_done, on_error=on_textdiff_error)

        return True

    handle.aux_kind = 'compare-results'
    return handle


def compare(window, state):
    """Ask for a compare request, run it in the background, show results."""
    request = review_dialogs.compare_dialog(window, state)
    if request is None:
        return
    request = resolve_passwords(request, state)

    def on_done(done_window, done_state, result):
        _show_compare_results(done_window, done_state, request, result)

    run_task(window, run_compare, request, on_done=on_done)


HANDLERS = {
    'MENU_COMPARE': compare,
    'MENU_SEARCH': search,
}
