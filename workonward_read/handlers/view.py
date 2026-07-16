"""
View handlers for WorkOnward Read (zoom, paging, thumbnails toggle) plus the
shared graph helpers used by other handler groups and main.py.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

from workonward_read import ui


# --- shared graph helpers (moved from main.py) ----------------------------

def scale_graph_to_image(window, image):
    """Adjust Graph element size to the image (e.g. zoom actions)."""
    window['-GRAPH-'].Widget.config(width=image.width, height=image.height)


def load_image_to_graph(window, image, location=(0, 0)):
    """Load image to Graph element and adjust position."""
    window['-GRAPH-'].erase()
    id = window['-GRAPH-'].draw_image(data=image.data(), location=location)

    scale_graph_to_image(window, image.scaled_image)
    image.draw_rectangles_on_graph(window)
    image.id = id
    return id


def flip_to_page(window, images, page):
    """Update graph with next/previous image. Update page number display."""
    try:
        page = int(page)
    except ValueError:
        page = 0
    if page < 0:
        page = len(images) - 1
    if page > len(images) - 1:
        page = 0

    img = images[page]
    scale_graph_to_image(window, img.refresh().image)
    load_image_to_graph(window, img)
    window['-PAGE_NUM-'].update(value=int(page) + 1)
    return page


# --- menu handlers ---------------------------------------------------------

def zoom_in(window, state):
    """Increase zoom on the current page."""
    if not state.images:
        return
    container = state.images[state.current_page]
    container.increase_zoom()
    scale_graph_to_image(window, container.scaled_image)
    load_image_to_graph(window, container)


def zoom_out(window, state):
    """Decrease zoom on the current page."""
    if not state.images:
        return
    container = state.images[state.current_page]
    container.decrease_zoom()
    scale_graph_to_image(window, container.scaled_image)
    load_image_to_graph(window, container)


def prev_page(window, state):
    """Flip to the previous page (wraps around)."""
    if not state.images:
        return
    state.current_page = flip_to_page(window, state.images, state.current_page - 1)


def next_page(window, state):
    """Flip to the next page (wraps around)."""
    if not state.images:
        return
    state.current_page = flip_to_page(window, state.images, state.current_page + 1)


def toggle_thumbnails(window, state):
    """Toggle the thumbnails sidebar flag (panel arrives in a later wave)."""
    state.thumbnails_visible = not state.thumbnails_visible


# --- toolbar-only handlers ---------------------------------------------------

def change_color(window, state):
    """Toggle fill color between black and white (toolbar icon)."""
    try:
        state.fill_color = ui.toggle_color(window, state.icons, state.fill_color)
    except Exception:
        state.fill_color = 'white' if state.fill_color == 'black' else 'black'


def toggle_quality(window, state):
    """Toggle output quality between high and low (toolbar icon)."""
    try:
        state.output_quality = ui.toggle_quality(window, state.icons, state.output_quality)
    except Exception:
        state.output_quality = 'low' if state.output_quality == 'high' else 'high'


def toggle_eraser(window, state):
    """Toggle between the redact and eraser canvas tools (toolbar icon)."""
    current_mode = 'erase' if state.tool == 'eraser' else 'draw'
    try:
        new_mode = ui.toggle_edit_mode(window, state.icons, current_mode)
    except Exception:
        new_mode = 'draw' if current_mode == 'erase' else 'erase'
    state.tool = 'eraser' if new_mode == 'erase' else 'redact'
    try:
        window['-TOOL-'].update(value=state.tool)
    except Exception:
        pass


HANDLERS = {
    'MENU_ZOOM_IN': zoom_in,
    'MENU_ZOOM_OUT': zoom_out,
    'MENU_PREV_PAGE': prev_page,
    'MENU_NEXT_PAGE': next_page,
    'MENU_THUMBNAILS': toggle_thumbnails,
}
