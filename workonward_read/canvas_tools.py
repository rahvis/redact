"""
Canvas tool framework for WorkOnward Read.

Defines the :class:`CanvasTool` protocol and the built-in tools that operate
on the ``-GRAPH-`` element. All coordinates arriving at tools are already
zoom-corrected to ORIGINAL-image pixel space (main.py divides raw graph
coordinates by ``zoom_factor / 100``) and y-flipped to y-down, matching the
typed annotation model in :mod:`workonward_read.annotations`.

Every annotation-adding action pushes an undo snapshot FIRST
(``state.undo[page].push(...)``, see :func:`commit_annotation`).

Tool properties (color / width / alpha / font size) come from
``state.tool_props[tool_key]`` with the sane defaults in
:data:`DEFAULT_TOOL_PROPS`. A dedicated tool-options popup is deliberately
deferred; overriding ``state.tool_props`` is the supported hook.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import math
from typing import Protocol

from workonward_read import annotations as an
from workonward_read.annotations import UndoStack
from workonward_read.image_container import ImageContainer
from workonward_read.i18n import _


class CanvasTool(Protocol):
    """Protocol for canvas tools driven by -GRAPH- events."""

    cursor: str  # Tk cursor name while the tool is active

    def on_press(self, window, state, x, y): ...

    def on_drag(self, window, state, x, y): ...    # called per -GRAPH- drag event

    def on_release(self, window, state, x, y): ... # -GRAPH-+UP


# Sane per-tool defaults; state.tool_props[tool_key] overrides individual keys.
DEFAULT_TOOL_PROPS = {
    'text': {'size_px': 32, 'color': 'black', 'bold': False},
    'highlight': {'color': '#ffff00', 'alpha': 0.4},
    'underline': {'color': 'red', 'width_px': 3},
    'strike': {'color': 'red', 'width_px': 3},
    'ink': {'color': 'blue', 'width_px': 4},
    'rect': {'outline': 'red', 'fill': None, 'width_px': 3},
    'ellipse': {'outline': 'red', 'fill': None, 'width_px': 3},
    'line': {'color': 'red', 'width_px': 3},
    'arrow': {'color': 'red', 'width_px': 3},
    'stamp': {'preset': 'approved', 'text': '', 'color': '#c62828',
              'angle': 0.0, 'scale': 1.0},
    'image': {'scale': 1.0},
    'signature': {'scale': 1.0},
}

# Import resolution used for the measure tool (see docs/dev-architecture.md).
_MEASURE_PPI = 200.0


def _zoom_factor():
    """Current display zoom as a multiplier (1.0 == 100%)."""
    return ImageContainer.zoom_factor / 100


def _normalized(p1, p2):
    """Return (top_left, bottom_right) regardless of drag direction."""
    return (
        (min(p1[0], p2[0]), min(p1[1], p2[1])),
        (max(p1[0], p2[0]), max(p1[1], p2[1])),
    )


def current_container(state):
    """Return the ImageContainer of the current page, or None."""
    if state.images and 0 <= state.current_page < len(state.images):
        return state.images[state.current_page]
    return None


def tool_defaults(state, tool_key):
    """Merged tool properties: DEFAULT_TOOL_PROPS overlaid with
    ``state.tool_props[tool_key]``."""
    props = dict(DEFAULT_TOOL_PROPS.get(tool_key, {}))
    try:
        overrides = (state.tool_props or {}).get(tool_key) or {}
    except AttributeError:
        overrides = {}
    props.update(overrides)
    return props


def push_undo_snapshot(state, container):
    """Push a snapshot of the current page's annotations onto its UndoStack
    (created on demand). Must be called BEFORE mutating the list."""
    stack = state.undo.setdefault(state.current_page, UndoStack())
    stack.push(container.annotations)
    return stack


def commit_annotation(window, state, kind, props):
    """Add an annotation to the current page: push an undo snapshot first,
    append the annotation, then draw its live preview on the graph.

    Returns the new Annotation, or None when no page is loaded."""
    container = current_container(state)
    if container is None:
        return None
    push_undo_snapshot(state, container)
    ann = container.add_annotation(kind, props)
    try:
        an.render_on_graph(window['-GRAPH-'], ann, ImageContainer.zoom_factor)
    except Exception:
        ann.graph_ids = []
    return ann


class _PreviewMixin:
    """Shared temporary-figure bookkeeping for drag tools."""

    def __init__(self):
        self._start = None
        self._preview_ids = []

    def _delete_preview(self, window):
        for figure_id in self._preview_ids:
            try:
                window['-GRAPH-'].delete_figure(figure_id)
            except Exception:
                pass
        self._preview_ids = []


class RedactTool(_PreviewMixin):
    """
    Draw redaction rectangles.

    Shows a temporary red preview rectangle while dragging and commits a
    filled 'redact' annotation (state.fill_color) on release. Drags in any
    direction are normalized to a top-left/bottom-right pair.
    """

    cursor = 'crosshair'

    def on_press(self, window, state, x, y):
        self._start = (int(x), int(y))
        self._preview_ids = []

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
            self._preview_ids = [window['-GRAPH-'].draw_rectangle(
                (p1[0] * factor, -p1[1] * factor),
                (p2[0] * factor, -p2[1] * factor),
                fill_color='red',
                line_color='red',
                line_width=None
            )]
        except Exception:
            self._preview_ids = []

    def on_release(self, window, state, x, y):
        self._delete_preview(window)

        if self._start is None:
            return
        start = self._start
        self._start = None

        p1, p2 = _normalized(start, (int(x), int(y)))
        if p1[0] == p2[0] or p1[1] == p2[1]:
            return  # zero-area click, nothing to redact

        commit_annotation(window, state, 'redact', {
            'p1': [p1[0], p1[1]],
            'p2': [p2[0], p2[1]],
            'fill': state.fill_color,
        })


class EraserTool:
    """
    Erase the topmost annotation (any kind) under the click position.

    Uses :func:`workonward_read.annotations.hit_test` over all annotation kinds
    and pushes an undo snapshot before removing, so erasing is undoable.
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
            container = current_container(state)
            if container is not None:
                hit = an.hit_test(container.annotations, x, y)
                if hit is not None:
                    push_undo_snapshot(state, container)
                    for figure_id in (hit.graph_ids or []):
                        try:
                            window['-GRAPH-'].delete_figure(figure_id)
                        except Exception:
                            pass
                    container.annotations = [
                        item for item in container.annotations if item is not hit
                    ]
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


class BoxTool(_PreviewMixin):
    """Drag-box annotation tool (highlight, underline, strike, rect,
    ellipse). Shows an outline-rectangle preview while dragging and commits
    a p1/p2 annotation with the current tool props on release."""

    cursor = 'crosshair'

    def __init__(self, kind):
        super().__init__()
        self.kind = kind

    def _preview_color(self, state):
        props = tool_defaults(state, self.kind)
        return props.get('color') or props.get('outline') or 'red'

    def on_press(self, window, state, x, y):
        self._start = (int(x), int(y))
        self._preview_ids = []

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
            self._preview_ids = [window['-GRAPH-'].draw_rectangle(
                (p1[0] * factor, -p1[1] * factor),
                (p2[0] * factor, -p2[1] * factor),
                fill_color=None,
                line_color=self._preview_color(state),
                line_width=1
            )]
        except Exception:
            self._preview_ids = []

    def on_release(self, window, state, x, y):
        self._delete_preview(window)

        if self._start is None:
            return
        start = self._start
        self._start = None

        p1, p2 = _normalized(start, (int(x), int(y)))
        if p1[0] == p2[0] or p1[1] == p2[1]:
            return  # zero-area drag

        props = tool_defaults(state, self.kind)
        props['p1'] = [p1[0], p1[1]]
        props['p2'] = [p2[0], p2[1]]
        commit_annotation(window, state, self.kind, props)


class LineTool(_PreviewMixin):
    """Drag tool for 'line' and 'arrow': line preview while dragging,
    commits a p1→p2 annotation (direction preserved) on release."""

    cursor = 'crosshair'

    def __init__(self, kind):
        super().__init__()
        self.kind = kind

    def on_press(self, window, state, x, y):
        self._start = (int(x), int(y))
        self._preview_ids = []

    def on_drag(self, window, state, x, y):
        if self._start is None:
            self.on_press(window, state, x, y)
            return

        self._delete_preview(window)
        factor = _zoom_factor()
        props = tool_defaults(state, self.kind)
        try:
            self._preview_ids = [window['-GRAPH-'].draw_line(
                (self._start[0] * factor, -self._start[1] * factor),
                (x * factor, -y * factor),
                color=props.get('color', 'red'),
                width=1
            )]
        except Exception:
            self._preview_ids = []

    def on_release(self, window, state, x, y):
        self._delete_preview(window)

        if self._start is None:
            return
        start = self._start
        self._start = None

        end = (int(x), int(y))
        if start == end:
            return  # zero-length click

        props = tool_defaults(state, self.kind)
        props['p1'] = [start[0], start[1]]
        props['p2'] = [end[0], end[1]]
        commit_annotation(window, state, self.kind, props)


class InkTool:
    """Freehand ink: accumulates points during the drag, drawing incremental
    preview segments, and commits one 'ink' annotation on release."""

    cursor = 'pencil'

    def __init__(self):
        self._points = []
        self._preview_ids = []

    def _delete_preview(self, window):
        for figure_id in self._preview_ids:
            try:
                window['-GRAPH-'].delete_figure(figure_id)
            except Exception:
                pass
        self._preview_ids = []

    def on_press(self, window, state, x, y):
        self._points = [[int(x), int(y)]]
        self._preview_ids = []

    def on_drag(self, window, state, x, y):
        if not self._points:
            self.on_press(window, state, x, y)
            return
        point = [int(x), int(y)]
        previous = self._points[-1]
        if point == previous:
            return
        self._points.append(point)

        factor = _zoom_factor()
        props = tool_defaults(state, 'ink')
        width = max(1, int(round(float(props.get('width_px', 2)) * factor)))
        try:
            self._preview_ids.append(window['-GRAPH-'].draw_line(
                (previous[0] * factor, -previous[1] * factor),
                (point[0] * factor, -point[1] * factor),
                color=props.get('color', 'blue'),
                width=width
            ))
        except Exception:
            pass

    def on_release(self, window, state, x, y):
        points = self._points
        self._points = []
        self._delete_preview(window)

        if not points:
            return
        last = [int(x), int(y)]
        if points[-1] != last:
            points.append(last)

        props = tool_defaults(state, 'ink')
        props['points'] = points
        commit_annotation(window, state, 'ink', props)


class ClickPlaceTool:
    """Click-to-place tools (text, stamp, image, signature): on release the
    matching dialog from dialogs/annotate.py opens pre-filled with the click
    position; the returned props dict is committed as an annotation."""

    cursor = 'tcross'

    _DIALOGS = {
        'text': 'text_dialog',
        'stamp': 'stamp_dialog',
        'image': 'image_dialog',
        'signature': 'signature_dialog',
    }

    def __init__(self, kind):
        self.kind = kind

    def on_press(self, window, state, x, y):
        pass

    def on_drag(self, window, state, x, y):
        pass

    def on_release(self, window, state, x, y):
        if current_container(state) is None:
            return
        # Imported lazily so canvas_tools stays importable without dialogs.
        from workonward_read.dialogs import annotate as annotate_dialogs
        dialog = getattr(annotate_dialogs, self._DIALOGS[self.kind])
        props = dialog(window, state, (int(x), int(y)))
        if props:
            commit_annotation(window, state, self.kind, props)


class MeasureTool(_PreviewMixin):
    """Measure distances: drag shows a live line with the pixel distance,
    release pops up px / cm / inch (200 PPI import resolution). Never adds
    an annotation."""

    cursor = 'crosshair'

    def on_press(self, window, state, x, y):
        self._start = (int(x), int(y))
        self._preview_ids = []

    def on_drag(self, window, state, x, y):
        if self._start is None:
            self.on_press(window, state, x, y)
            return

        self._delete_preview(window)
        factor = _zoom_factor()
        distance = math.dist(self._start, (x, y))
        try:
            self._preview_ids.append(window['-GRAPH-'].draw_line(
                (self._start[0] * factor, -self._start[1] * factor),
                (x * factor, -y * factor),
                color='red', width=1))
            mid_x = (self._start[0] + x) / 2.0
            mid_y = (self._start[1] + y) / 2.0
            self._preview_ids.append(window['-GRAPH-'].draw_text(
                '%.0f px' % distance,
                (mid_x * factor, -mid_y * factor + 12),
                color='red'))
        except Exception:
            pass

    def on_release(self, window, state, x, y):
        self._delete_preview(window)
        if self._start is None:
            return
        start = self._start
        self._start = None

        distance_px = math.dist(start, (int(x), int(y)))
        if distance_px < 1:
            return

        inches = distance_px / _MEASURE_PPI
        centimeters = inches * 2.54
        points = distance_px * 72.0 / _MEASURE_PPI
        self._show_result(window, _(
            'Distance: {px} px  |  {cm} cm  |  {inch} in  |  {pt} pt',
            px='%.0f' % distance_px,
            cm='%.2f' % centimeters,
            inch='%.2f' % inches,
            pt='%.1f' % points,
        ))

    @staticmethod
    def _show_result(window, message):
        # Imported lazily; kept as a hook so tests can stub it out.
        from workonward_read.dialogs import common as dialogs_common
        dialogs_common.info_popup(window, message)


# Tool keys for the full suite (every key is registered below).
ALL_TOOL_KEYS = [
    'redact', 'eraser', 'text', 'highlight', 'underline', 'strike', 'ink',
    'rect', 'ellipse', 'line', 'arrow', 'stamp', 'image', 'signature',
    'measure',
]

TOOLS: dict = {
    'redact': RedactTool(),
    'eraser': EraserTool(),
    'text': ClickPlaceTool('text'),
    'highlight': BoxTool('highlight'),
    'underline': BoxTool('underline'),
    'strike': BoxTool('strike'),
    'ink': InkTool(),
    'rect': BoxTool('rect'),
    'ellipse': BoxTool('ellipse'),
    'line': LineTool('line'),
    'arrow': LineTool('arrow'),
    'stamp': ClickPlaceTool('stamp'),
    'image': ClickPlaceTool('image'),
    'signature': ClickPlaceTool('signature'),
    'measure': MeasureTool(),
}


def register_tool(key, tool):
    """Register (or replace) a canvas tool under the given key."""
    TOOLS[key] = tool
