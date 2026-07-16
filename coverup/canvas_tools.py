"""
Canvas tool framework for CoverUP PDF.

Defines the :class:`CanvasTool` protocol and the built-in tools that operate
on the ``-GRAPH-`` element. All coordinates arriving at tools are already
zoom-corrected to ORIGINAL-image pixel space (main.py divides raw graph
coordinates by ``zoom_factor / 100``) and y-flipped to y-down, matching the
rectangle model used by :class:`coverup.image_container.ImageContainer`.

Wave 2 registers additional tools (text, highlight, ink, …) via
:func:`register_tool`.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

from typing import Protocol

from coverup.image_container import ImageContainer


class CanvasTool(Protocol):
    """Protocol for canvas tools driven by -GRAPH- events."""

    cursor: str  # Tk cursor name while the tool is active

    def on_press(self, window, state, x, y): ...

    def on_drag(self, window, state, x, y): ...    # called per -GRAPH- drag event

    def on_release(self, window, state, x, y): ... # -GRAPH-+UP


def _zoom_factor():
    """Current display zoom as a multiplier (1.0 == 100%)."""
    return ImageContainer.zoom_factor / 100


def _normalized(p1, p2):
    """Return (top_left, bottom_right) regardless of drag direction."""
    return (
        (min(p1[0], p2[0]), min(p1[1], p2[1])),
        (max(p1[0], p2[0]), max(p1[1], p2[1])),
    )


class RedactTool:
    """
    Draw redaction rectangles.

    Shows a temporary red preview rectangle while dragging and commits a
    filled rectangle (state.fill_color) on release. Drags in any direction
    are normalized to a top-left/bottom-right pair.
    """

    cursor = 'crosshair'

    def __init__(self):
        self._start = None
        self._temp_rectangle = None

    def _delete_preview(self, window):
        if self._temp_rectangle is not None:
            try:
                window['-GRAPH-'].delete_figure(self._temp_rectangle)
            except Exception:
                pass
            self._temp_rectangle = None

    def on_press(self, window, state, x, y):
        self._start = (int(x), int(y))
        self._temp_rectangle = None

    def on_drag(self, window, state, x, y):
        if self._start is None:
            self.on_press(window, state, x, y)
            return

        self._delete_preview(window)

        p1, p2 = _normalized(self._start, (x, y))
        if p1[0] == p2[0] or p1[1] == p2[1]:
            return

        factor = _zoom_factor()
        try:
            self._temp_rectangle = window['-GRAPH-'].draw_rectangle(
                (p1[0] * factor, -p1[1] * factor),
                (p2[0] * factor, -p2[1] * factor),
                fill_color='red',
                line_color='red',
                line_width=None
            )
        except Exception:
            self._temp_rectangle = None

    def on_release(self, window, state, x, y):
        self._delete_preview(window)

        if self._start is None:
            return
        start = self._start
        self._start = None

        p1, p2 = _normalized(start, (int(x), int(y)))
        if p1[0] == p2[0] or p1[1] == p2[1]:
            return  # zero-area click, nothing to redact

        if not state.images or not (0 <= state.current_page < len(state.images)):
            return

        # ImageContainer.draw_rectangle expects graph-space (zoomed) points
        # and converts back to original px internally.
        factor = _zoom_factor()
        state.images[state.current_page].draw_rectangle(
            window,
            (p1[0] * factor, p1[1] * factor),
            (p2[0] * factor, p2[1] * factor),
            fill=state.fill_color
        )


class EraserTool:
    """
    Erase the topmost redaction rectangle under the click position.

    Mirrors the classic toolbar eraser: after one erase action the tool
    reverts to 'redact' (the toolbar icon and tool selector are updated).
    """

    cursor = 'X_cursor'

    def on_press(self, window, state, x, y):
        pass

    def on_drag(self, window, state, x, y):
        pass

    def on_release(self, window, state, x, y):
        try:
            if state.images and 0 <= state.current_page < len(state.images):
                container = state.images[state.current_page]
                for rect in reversed(container.rectangles):
                    (x0, y0), (x1, y1) = tuple(rect[0]), tuple(rect[1])
                    if (min(x0, x1) <= x <= max(x0, x1)
                            and min(y0, y1) <= y <= max(y0, y1)):
                        try:
                            window['-GRAPH-'].delete_figure(rect[3])
                        except Exception:
                            pass
                        container.rectangles = [
                            item for item in container.rectangles if item is not rect
                        ]
                        break
        finally:
            # Classic behavior: erasing is a one-shot action.
            state.tool = 'redact'
            try:
                window['-GRAPH-'].set_cursor(RedactTool.cursor)
            except Exception:
                pass
            try:
                if state.icons:
                    window['EDIT_MODE'].update(data=state.icons['eraser_off'])
            except Exception:
                pass
            try:
                window['-TOOL-'].update(value='redact')
            except Exception:
                pass


# Tool keys planned for the full suite; unregistered keys fall back to a
# "next build step" popup in main.py until wave 2 registers them.
ALL_TOOL_KEYS = [
    'redact', 'eraser', 'text', 'highlight', 'underline', 'strike', 'ink',
    'rect', 'ellipse', 'line', 'arrow', 'stamp', 'image', 'signature',
    'measure',
]

TOOLS: dict = {
    'redact': RedactTool(),
    'eraser': EraserTool(),
}


def register_tool(key, tool):
    """Register (or replace) a canvas tool under the given key."""
    TOOLS[key] = tool
