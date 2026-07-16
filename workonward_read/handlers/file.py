"""
File handlers for WorkOnward Read: open, save-redacted, export-page, print, exit.

The open/save logic was moved verbatim (modulo AppState) from the original
main.py event loop so the toolbar workflow keeps its exact behavior.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import gc
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from datetime import datetime

import FreeSimpleGUI as sg
from fpdf import FPDF

from workonward_read import __version__, ui
from workonward_read.annotations import UndoStack
from workonward_read.document_loader import load_document
from workonward_read.image_container import ImageContainer, close_all_pages, finalize_pages_chunked
from workonward_read.handlers.view import flip_to_page
from workonward_read.dialogs.common import require_document_free
from workonward_read.pdf_ops import PageOpsJournal
from workonward_read.workfile import serialize_journal
from workonward_read.i18n import _, _plural


POINTER_CURSOR = 'arrow' if sg.running_windows() else 'left_ptr'


def _tool_cursor(state):
    """Return the Tk cursor for the currently selected canvas tool."""
    from workonward_read.canvas_tools import TOOLS
    tool = TOOLS.get(state.tool)
    return getattr(tool, 'cursor', 'crosshair')


def _save_worksession(state):
    """Persist the current session (annotations, decorations, journal)."""
    if state.workfile_manager is None:
        return
    state.workfile_manager.save(
        state.images, state.current_page, state.fill_color,
        state.output_quality,
        decorations=state.decorations,
        journal=serialize_journal(state.journal)
    )


def load_path(window, state, load_file_path, error_key='error_loading'):
    """
    Load a document from a path into the application state.

    Used by the Open handler and by the CLI file argument at startup.
    Refused while a background task is using the loaded document (loading
    closes every page image the task might be reading).
    """
    if not require_document_free(window, state):
        return
    try:
        window.set_cursor('watch')
        window['-GRAPH-'].set_cursor('watch')
        window.refresh()

        # Erase graph and close existing images before loading new document
        window['-GRAPH-'].erase()
        close_all_pages(state.images)
        gc.collect()

        ImageContainer.zoom_factor = 100
        (images, file_path, current_page, new_fill_color, new_output_quality,
         extras) = load_document(
            load_file_path, state.import_ppi, window, state.workfile_manager
        )
        state.images = images
        state.file_path = file_path

        # Keep the password used to open an encrypted source for later
        # lossless operations (never persisted).
        state.source_password = extras.get('password')

        # Restored session context (empty on a fresh document).
        state.decorations = extras.get('decorations') or {}
        journal_ops = extras.get('journal') or []
        state.journal = (PageOpsJournal.from_dict({'ops': journal_ops})
                         if journal_ops else None)
        state.undo = {}
        if extras.get('restored'):
            # Restored annotations arrive outside the normal canvas-tool flow
            # (which pushes an undo snapshot before every change). Seed each
            # restored page's stack with ONE snapshot of the empty
            # pre-restore state; the restored annotations stay the live
            # state, so a single Undo removes the whole restored set and
            # Redo brings it back — the closest safe parity with the classic
            # pop-last behavior.
            for page_idx, container in enumerate(state.images):
                if getattr(container, 'annotations', None):
                    stack = UndoStack()
                    stack.push([])
                    state.undo[page_idx] = stack

        # Apply restored settings if available
        if new_fill_color and state.fill_color != new_fill_color:
            state.fill_color = ui.toggle_color(window, state.icons, state.fill_color)
        if new_output_quality and state.output_quality != new_output_quality:
            state.output_quality = ui.toggle_quality(window, state.icons, state.output_quality)

        state.first_load = False
        window['-PROGRESS-'].update(current_count=0)
        state.current_page = flip_to_page(window, state.images, current_page, state)
        window.set_title(_('app_title_with_file', filename=os.path.basename(file_path)))

    except Exception as e:
        window['-PAGE_TOTAL-'].update('0')
        window['-PROGRESS-'].update(current_count=0)
        sg.popup(_(error_key), str(e))

    window.set_cursor(POINTER_CURSOR)
    window['-GRAPH-'].set_cursor(_tool_cursor(state))


def open_document(window, state):
    """Ask for a file and load it (classic LOAD_PDF behavior)."""
    _save_worksession(state)

    # Open home-folder when first time loading a pdf
    if state.first_load:
        # Prefer SNAP_REAL_HOME if available
        snap_real = os.environ.get("SNAP_REAL_HOME")
        home_folder = Path(snap_real) if snap_real else Path.home()
    else:
        home_folder = None

    load_file_path = sg.popup_get_file(
        _('dialog_load_file'),
        initial_folder=home_folder,
        grab_anywhere=True,
        keep_on_top=True,
        no_window=True,
        show_hidden=True,
        file_types=(
            (_('filetype_all'), '*.pdf *.PDF *.jpg *.JPG *.jpeg *.JPEG *.png *.PNG'),
            (_('filetype_pdf'), '*.pdf *.PDF'),
            (_('filetype_image'), '*.jpg *.JPG *.jpeg *.JPEG *.png *.PNG')
        )
    )

    if load_file_path:
        load_path(window, state, load_file_path, error_key='error_occurred')


def _render_pdf_to_path(window, state, save_file_path, export_page):
    """
    Render the loaded document (or just the current page) to a PDF file
    using the existing finalize pipeline. Progress is shown in -PROGRESS-.

    Returns:
        int: Number of pages written.
    """
    out_pdf = FPDF(unit="pt")
    out_pdf.set_creator(f'WorkOnward Read {__version__}')
    out_pdf.set_creation_date(datetime.today())

    try:
        # Quality settings:
        # HIGH: JPEG 90 at full resolution (200 DPI)
        # LOW:  JPEG 85 at 55% scale (~110 DPI)
        quality = 90 if state.output_quality == 'high' else 85
        scale = 1 if state.output_quality == 'high' else 0.55
        images = state.images
        current_page = state.current_page

        if export_page:
            # Single page export
            window['-PROGRESS-'].update(current_count=25)
            window.refresh()

            out_pdf.add_page(format=(
                images[current_page].height_in_pt,
                images[current_page].width_in_pt
            ))

            window['-PROGRESS-'].update(current_count=50)
            window.refresh()

            include_image = images[current_page].finalized_image(
                'JPEG', image_quality=quality, scale=scale,
                decorations=state.decorations,
                page_idx=current_page,
                total_pages=len(images)
            )
            out_pdf.image(include_image, x=0, y=0, w=out_pdf.w)
            del include_image  # Release image bytes immediately

            window['-PROGRESS-'].update(current_count=75)
            window.refresh()

            out_pdf.output(save_file_path)
            total_pages = 1

        else:
            # Use chunked parallel processing for multi-page export
            # This limits memory usage by processing in chunks of 50 pages
            total_pages = len(images)

            # Progress callback for chunked processing (0-90%)
            def update_progress(completed, total):
                progress = int(completed * 90 / total)
                window['-PROGRESS-'].update(current_count=progress)
                window.refresh()

            for img_bytes, page_size in finalize_pages_chunked(
                images,
                img_format='JPEG',
                quality=quality,
                scale=scale,
                chunk_size=50,
                progress_callback=update_progress,
                decorations=state.decorations
            ):
                out_pdf.add_page(format=page_size)
                out_pdf.image(img_bytes, x=0, y=0, w=out_pdf.w)
                del img_bytes  # Release image bytes immediately after adding to PDF
                del page_size

            # Writing PDF to disk (90-100%)
            window['-PROGRESS-'].update(current_count=95)
            window.refresh()

            out_pdf.output(save_file_path)

        window['-PROGRESS-'].update(current_count=100)
        window.refresh()
        return total_pages
    finally:
        del out_pdf
        gc.collect()


def _save_document(window, state, export_page):
    """Shared implementation of Save Redacted PDF / Export Current Page."""
    if not state.images:
        return
    # Save/export consume state.images page by page: refuse while a
    # background task (e.g. OCR-current-doc) is using the document.
    if not require_document_free(window, state):
        return

    # Pre-fill with the loaded filename
    default_filename = ""
    if state.file_path:
        base_name = os.path.splitext(os.path.basename(state.file_path))[0]
        if export_page:
            default_filename = f"{base_name}{_('suffix_page')}{state.current_page + 1}.pdf"
        else:
            default_filename = f"{base_name}{_('suffix_redacted')}.pdf"

    save_file_path = sg.popup_get_file(
        _('dialog_save_pdf'),
        no_window=True,
        show_hidden=True,
        keep_on_top=True,
        save_as=True,
        file_types=((_('filetype_pdf'), '*.pdf *.PDF'),),
        default_extension=".pdf",
        default_path=default_filename
    )

    if not save_file_path:
        return

    try:
        window.set_cursor('watch')
        window['-GRAPH-'].set_cursor('watch')
        window.refresh()

        total_pages = _render_pdf_to_path(window, state, save_file_path, export_page)

        window.set_cursor(POINTER_CURSOR)
        window['-GRAPH-'].set_cursor(_tool_cursor(state))
        _save_worksession(state)

        # Show success message
        window['-PROGRESS-'].update(current_count=0)
        saved_filename = os.path.basename(save_file_path)
        win_loc_x, win_loc_y = window.current_location()
        win_w, win_h = window.current_size_accurate()
        sg.popup_no_titlebar(
            _plural('save_success', 'save_success_plural', total_pages,
                    filename=saved_filename),
            location=(win_loc_x + win_w/2 - 185, win_loc_y + win_h/2 - 200),
            keep_on_top=True,
            background_color='silver',
            button_color='grey'
        )

    except Exception as e:
        window['-PROGRESS-'].update(current_count=0)
        window.set_cursor(POINTER_CURSOR)
        window['-GRAPH-'].set_cursor(_tool_cursor(state))
        sg.popup(_('error_occurred'), str(e))
    finally:
        gc.collect()


def save_redacted(window, state):
    """Save the whole redacted document as PDF."""
    _save_document(window, state, export_page=False)


def export_page(window, state):
    """Export only the current page as PDF."""
    _save_document(window, state, export_page=True)


def _open_with_os(path):
    """Open a file with the platform's default application."""
    if sys.platform == 'darwin':
        subprocess.Popen(['open', path])
    elif sys.platform.startswith('win'):
        os.startfile(path)  # noqa: attribute exists on Windows only
    else:
        subprocess.Popen(['xdg-open', path])


def print_document(window, state):
    """
    Render the current document to a temporary PDF via the existing save
    pipeline and hand it to the OS default viewer for printing.
    """
    if not state.images:
        return
    if not require_document_free(window, state):
        return

    fd, tmp_path = tempfile.mkstemp(prefix='workonward_read_print_', suffix='.pdf')
    os.close(fd)

    try:
        window.set_cursor('watch')
        window['-GRAPH-'].set_cursor('watch')
        window.refresh()

        _render_pdf_to_path(window, state, tmp_path, export_page=False)
        window['-PROGRESS-'].update(current_count=0)
        _open_with_os(tmp_path)
    except Exception as e:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        window['-PROGRESS-'].update(current_count=0)
        sg.popup(_('error_occurred'), str(e))
    finally:
        window.set_cursor(POINTER_CURSOR)
        window['-GRAPH-'].set_cursor(_tool_cursor(state))
        gc.collect()


def exit_app(window, state):
    """Request application shutdown (the event loop breaks on 'EXIT')."""
    window.write_event_value('EXIT', None)


HANDLERS = {
    'MENU_OPEN': open_document,
    'MENU_SAVE_REDACTED': save_redacted,
    'MENU_EXPORT_PAGE': export_page,
    'MENU_PRINT': print_document,
    'MENU_EXIT': exit_app,
}
