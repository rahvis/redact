#!/usr/bin/env python3
"""
WorkOnward Read - Main application entry point.

A tool for redacting PDF files and images.

The event loop is intentionally thin: it reads ALL open windows via
``sg.read_all_windows()``. Menu events (normalized via ``rsplit('::', 1)``)
and toolbar icon events dispatch through the handler registry in
:mod:`workonward_read.handlers`; canvas interaction dispatches through
:mod:`workonward_read.canvas_tools`; background work reports back via
``(('-TASK-', seq), ...)`` tuple events under per-invocation unique keys
(see :mod:`workonward_read.tasks`) and is routed centrally no matter which
window it arrives on. Non-modal secondary windows register a handler in
``state.aux_windows`` and get their events routed by the same loop
(aux-window contract in docs/dev-architecture.md).

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
(c) 2026 WorkOnward Read contributors
"""

import os
import sys
import argparse
from multiprocessing import freeze_support

import FreeSimpleGUI as sg

from workonward_read import __version__, canvas_tools, tasks, thumbnails
from workonward_read.image_container import ImageContainer
from workonward_read.workfile import WorkfileManager, get_default_datadir, serialize_journal
from workonward_read.state import AppState
from workonward_read.handlers import HANDLERS, TOOLBAR_HANDLERS
from workonward_read.handlers.view import flip_to_page, load_image_to_graph, scale_graph_to_image  # noqa: F401 (re-export)
from workonward_read.dialogs.common import error_popup, info_popup
from workonward_read.ui import get_fontpath, create_icons, create_app_icon, create_layout
from workonward_read.i18n import _


def configure_canvas(event, canvas, frame_id, images, current_page):
    """Adjust canvas size. Necessary to update scrollbars."""
    try:
        canvas.itemconfig(frame_id, width=images[current_page].scaled_image.width + 40)
    except IndexError:
        pass


def configure_frame(event, canvas):
    """Adjust scrollregion. Necessary to update scrollbars."""
    canvas.configure(scrollregion=canvas.bbox("all"))


def _graph_to_original(values, factor):
    """Convert raw -GRAPH- coordinates to original-image px (y-down)."""
    x, y = values['-GRAPH-']
    return x / factor, -y / factor


def _handle_task_event(window, state, event, values):
    """Handle ``(('-TASK-', seq), 'PROGRESS'/'DONE'/'ERROR')`` tuple events.

    ``window`` is always the MAIN window: the shared ``-PROGRESS-`` bar
    lives there and error popups center on it. Every task reports under its
    own unique key, so concurrent tasks deliver DONE/ERROR payloads to
    their OWN callbacks; the progress bar is shared, so with overlapping
    tasks the last reporter wins (accepted behavior).
    """
    key, kind = event
    payload = (values or {}).get(event)

    if kind == 'PROGRESS':
        try:
            pct, _msg = payload
            window['-PROGRESS-'].update(current_count=int(pct))
        except Exception:
            pass
        return

    on_done, on_error = tasks.pop_callbacks(key)
    window['-PROGRESS-'].update(current_count=0)
    if kind == 'ERROR':
        # Cleanup hook first (release doc locks, re-enable buttons), then
        # the standard blocking error popup.
        if callable(on_error):
            on_error(window, state, payload)
        error_popup(window, _('error_occurred'), payload)
    elif kind == 'DONE':
        if callable(on_done):
            on_done(window, state, payload)


def _route_aux_window_event(state, aux_window, event, values):
    """Dispatch one event to the owning aux-window handler.

    Handlers return True to keep their window open; on a falsy return (or
    when no handler is registered — e.g. the window was closed by the OS)
    the window is closed and unregistered. Returns the keep-open decision.
    """
    handler = state.aux_windows.get(aux_window)
    keep_open = False
    if handler is not None:
        keep_open = bool(handler(aux_window, state, event, values))
    if not keep_open:
        state.aux_windows.pop(aux_window, None)
        try:
            aux_window.close()
        except Exception:
            pass
    return keep_open


def main():
    """Main application entry point."""
    freeze_support()

    # Frozen-app guard: windowed builds have no console streams.
    if getattr(sys, 'frozen', False) and sys.stdout is None:
        sys.stdout = open(os.devnull, 'w')
        sys.stderr = open(os.devnull, 'w')

    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description=_('cli_description'),
        prog='WorkOnward Read'
    )
    parser.add_argument(
        'file',
        nargs='?',
        default=None,
        help=_('cli_file_help')
    )
    parser.add_argument(
        '--version', '-v',
        action='store_true',
        help=_('cli_version_help')
    )
    args = parser.parse_args()

    # Handle --version flag
    if args.version:
        print(f"WorkOnward Read {__version__}")
        sys.exit(0)

    # Store CLI file path for loading after window is created
    cli_file_path = args.file

    # Initialize
    history_length = 30
    image_bg_color = 'gray'
    state = AppState()

    # Load fonts and create icons
    fontpath = get_fontpath()
    app_icon = create_app_icon(fontpath)
    state.icons = create_icons(fontpath)

    # Check for / create datadir (migrates workfiles from the previous
    # product's data directory on first run)
    datadir = get_default_datadir()

    # Initialize workfile manager
    state.workfile_manager = WorkfileManager(datadir, history_length)

    # Create layout
    layout = create_layout(state.icons, image_bg_color)

    sg.theme('LightBlue2')

    # Create window at top-left corner
    window = sg.Window(
        _('app_title'),
        layout,
        icon=app_icon,
        element_justification="center",
        background_color='grey',
        size=(1300, 900),
        resizable=True,
        finalize=True,
        location=(0, 0)
    )

    # Set WM_CLASS for proper taskbar icon matching (Linux/Flatpak)
    try:
        window.TKroot.wm_class('workonward-read', 'workonward-read')
    except Exception:
        pass  # Ignore on non-Linux platforms

    # Detect changes of window size
    frame_id = window['-GRAPH_COLUMN-'].Widget.frame_id
    canvas = window['-GRAPH_COLUMN-'].Widget.canvas
    window.bind('<Configure>', 'Configure_Event')

    # Load file from command line argument if provided
    if cli_file_path:
        from workonward_read.handlers.file import load_path
        load_path(window, state, cli_file_path, error_key='error_loading')

    graph_dragging = False

    # Main event loop: reads the main window AND every registered aux
    # window. read_all_windows() with no timeout blocks exactly like
    # window.read(); write_event_value events are per-window queued and
    # arrive attributed to the window they were written to.
    while True:
        event_window, event, values = sg.read_all_windows()

        # Background task events (('-TASK-', seq), 'PROGRESS'/'DONE'/'ERROR')
        # are routed centrally no matter which window they arrived on.
        if tasks.is_task_event(event):
            _handle_task_event(window, state, event, values)
            continue

        # Thumbnail sidebar clicks arrive as ('-THUMB-', idx, 'CLICK')
        # tuple events posted to the main window.
        if isinstance(event, tuple) and event and event[0] == '-THUMB-':
            thumbnails.handle_thumb_event(window, state, event)
            continue

        # Events belonging to a registered non-modal secondary window.
        if event_window is not None and event_window is not window:
            _route_aux_window_event(state, event_window, event, values)
            continue

        if event_window is None or event in (sg.WINDOW_CLOSED, 'EXIT'):
            break

        # Normalize menu events '<label>::<KEY>' -> '<KEY>'
        if isinstance(event, str) and '::' in event:
            event = event.rsplit('::', 1)[-1]

        if event == 'Configure_Event':
            configure_canvas(event, canvas, frame_id, state.images, state.current_page)
            configure_frame(event, canvas)

        elif event == '-TOOL-':
            state.tool = values['-TOOL-']
            tool = canvas_tools.TOOLS.get(state.tool)
            window['-GRAPH-'].set_cursor(getattr(tool, 'cursor', 'crosshair'))
            try:
                eraser_icon = 'eraser' if state.tool == 'eraser' else 'eraser_off'
                window['EDIT_MODE'].update(data=state.icons[eraser_icon])
            except Exception:
                pass

        elif event == '-GRAPH-':
            if state.images:
                factor = ImageContainer.zoom_factor / 100
                x, y = _graph_to_original(values, factor)
                tool = canvas_tools.TOOLS.get(state.tool)
                if tool is not None:
                    if not graph_dragging:
                        graph_dragging = True
                        tool.on_press(window, state, x, y)
                    else:
                        tool.on_drag(window, state, x, y)

        elif event == '-GRAPH-+UP':
            graph_dragging = False
            if state.images:
                factor = ImageContainer.zoom_factor / 100
                x, y = _graph_to_original(values, factor)
                tool = canvas_tools.TOOLS.get(state.tool)
                if tool is None:
                    info_popup(window, _('This tool arrives in the next build step.'))
                else:
                    tool.on_release(window, state, x, y)

        elif event == '-PAGE_NUM-':
            if state.images:
                try:
                    page = int(values['-PAGE_NUM-'])
                    state.current_page = flip_to_page(window, state.images, page - 1, state)
                except ValueError:
                    pass

        elif event in TOOLBAR_HANDLERS:
            TOOLBAR_HANDLERS[event](window, state)

        elif event in HANDLERS:
            HANDLERS[event](window, state)

    # Close any remaining aux windows before shutting down.
    for aux_window in list(state.aux_windows):
        state.aux_windows.pop(aux_window, None)
        try:
            aux_window.close()
        except Exception:
            pass

    # Save workfile only if we have loaded images
    if state.images:
        try:
            state.workfile_manager.save(
                state.images, state.current_page,
                state.fill_color, state.output_quality,
                decorations=state.decorations,
                journal=serialize_journal(state.journal)
            )
        except Exception:
            pass  # Don't crash on exit if save fails

    window.close()


if __name__ == "__main__":
    main()
