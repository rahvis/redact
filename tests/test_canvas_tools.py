"""GUI-free tests for the canvas tools, the undo/redo edit handlers and the
pure dialog helpers. A FakeWindow/FakeGraph pair records draw_* calls so no
real Tk widgets are needed.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
(c) 2026 WorkOnward Read contributors
"""

import base64
import io

import pytest
from PIL import Image

import fixtures  # noqa: F401  (sys.path side effect)

from workonward_read import annotations as an
from workonward_read import canvas_tools as ct
from workonward_read.canvas_tools import (
    DEFAULT_TOOL_PROPS,
    TOOLS,
    commit_annotation,
    tool_defaults,
)
from workonward_read.image_container import ImageContainer
from workonward_read.state import AppState


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeWidget:
    def __init__(self):
        self.calls = []
        self._counter = 5000

    def _record(self, name, args, kwargs):
        self._counter += 1
        self.calls.append((name, args, kwargs, self._counter))
        return self._counter

    def create_rectangle(self, *args, **kwargs):
        return self._record('create_rectangle', args, kwargs)

    def create_line(self, *args, **kwargs):
        return self._record('create_line', args, kwargs)

    def config(self, *args, **kwargs):
        pass


class FakeGraph:
    def __init__(self):
        self.calls = []
        self.deleted = []
        self._counter = 0
        self.Widget = FakeWidget()

    def _record(self, name, args, kwargs):
        self._counter += 1
        self.calls.append((name, args, kwargs, self._counter))
        return self._counter

    def draw_rectangle(self, *args, **kwargs):
        return self._record('draw_rectangle', args, kwargs)

    def draw_line(self, *args, **kwargs):
        return self._record('draw_line', args, kwargs)

    def draw_oval(self, *args, **kwargs):
        return self._record('draw_oval', args, kwargs)

    def draw_text(self, *args, **kwargs):
        return self._record('draw_text', args, kwargs)

    def draw_image(self, *args, **kwargs):
        return self._record('draw_image', args, kwargs)

    def draw_point(self, *args, **kwargs):
        return self._record('draw_point', args, kwargs)

    def delete_figure(self, figure_id):
        self.deleted.append(figure_id)

    def set_cursor(self, cursor):
        pass

    def erase(self):
        self.calls.append(('erase', (), {}, None))


class FakeElement:
    def __init__(self):
        self.updates = []

    def update(self, *args, **kwargs):
        self.updates.append((args, kwargs))


class FakeWindow:
    def __init__(self):
        self.graph = FakeGraph()
        self.elements = {}

    def __getitem__(self, key):
        if key == '-GRAPH-':
            return self.graph
        return self.elements.setdefault(key, FakeElement())

    def current_location(self):
        return (0, 0)

    def current_size_accurate(self):
        return (1000, 800)

    def refresh(self):
        pass


def make_state(pages=1, page_px=(400, 500)):
    state = AppState()
    state.images = [
        ImageContainer(Image.new('RGB', page_px, (255, 255, 255)),
                       (page_px[0] * 72 / 200, page_px[1] * 72 / 200))
        for _ in range(pages)
    ]
    return state


@pytest.fixture(autouse=True)
def _reset_zoom():
    old = ImageContainer.zoom_factor
    ImageContainer.zoom_factor = 100
    yield
    ImageContainer.zoom_factor = old


def press_drag_release(tool, window, state, start, mid, end):
    tool.on_press(window, state, *start)
    tool.on_drag(window, state, *mid)
    tool.on_release(window, state, *end)


# ---------------------------------------------------------------------------
# Registry / defaults
# ---------------------------------------------------------------------------

def test_all_fifteen_tools_registered():
    assert set(TOOLS) == set(ct.ALL_TOOL_KEYS)
    assert len(TOOLS) == 15
    for tool in TOOLS.values():
        assert isinstance(tool.cursor, str) and tool.cursor
        assert callable(tool.on_press)
        assert callable(tool.on_drag)
        assert callable(tool.on_release)


def test_tool_defaults_merge_state_overrides():
    state = make_state()
    assert tool_defaults(state, 'highlight')['color'] == '#ffff00'
    state.tool_props['highlight'] = {'color': '#00ffff'}
    merged = tool_defaults(state, 'highlight')
    assert merged['color'] == '#00ffff'
    assert merged['alpha'] == 0.4
    # defaults are copied, never mutated
    merged['alpha'] = 0.9
    assert DEFAULT_TOOL_PROPS['highlight']['alpha'] == 0.4


# ---------------------------------------------------------------------------
# commit / undo snapshots
# ---------------------------------------------------------------------------

def test_commit_annotation_pushes_undo_first_and_renders():
    window, state = FakeWindow(), make_state()
    ann = commit_annotation(window, state, 'redact',
                            {'p1': [10, 10], 'p2': [50, 50], 'fill': 'black'})
    assert ann is state.images[0].annotations[0]
    assert ann.graph_ids  # preview drawn
    stack = state.undo[0]
    assert stack.can_undo()
    # the pushed snapshot is the PRE-mutation state (empty page)
    assert stack.undo() == []


def test_commit_annotation_without_pages_returns_none():
    window, state = FakeWindow(), AppState()
    assert commit_annotation(window, state, 'redact',
                             {'p1': [0, 0], 'p2': [1, 1], 'fill': 'black'}) is None


# ---------------------------------------------------------------------------
# Drag tools
# ---------------------------------------------------------------------------

def test_redact_tool_commits_normalized_rect_with_fill_color():
    window, state = FakeWindow(), make_state()
    state.fill_color = 'white'
    tool = TOOLS['redact']
    press_drag_release(tool, window, state, (100, 120), (60, 70), (40, 30))
    anns = state.images[0].annotations
    assert len(anns) == 1
    assert anns[0].kind == 'redact'
    assert anns[0].props == {'p1': [40, 30], 'p2': [100, 120], 'fill': 'white'}
    assert state.undo[0].can_undo()
    # preview rectangle was deleted again
    assert window.graph.deleted


def test_redact_tool_zero_area_click_is_ignored():
    window, state = FakeWindow(), make_state()
    tool = TOOLS['redact']
    tool.on_press(window, state, 10, 10)
    tool.on_release(window, state, 10, 10)
    assert state.images[0].annotations == []
    assert 0 not in state.undo


@pytest.mark.parametrize('kind', ['highlight', 'underline', 'strike', 'rect',
                                  'ellipse'])
def test_box_tools_commit_kind_with_defaults(kind):
    window, state = FakeWindow(), make_state()
    tool = TOOLS[kind]
    press_drag_release(tool, window, state, (10, 20), (80, 90), (110, 140))
    anns = state.images[0].annotations
    assert len(anns) == 1
    assert anns[0].kind == kind
    assert anns[0].props['p1'] == [10, 20]
    assert anns[0].props['p2'] == [110, 140]
    for key, value in DEFAULT_TOOL_PROPS[kind].items():
        assert anns[0].props[key] == value
    assert state.undo[0].can_undo()


@pytest.mark.parametrize('kind', ['line', 'arrow'])
def test_line_tools_preserve_direction(kind):
    window, state = FakeWindow(), make_state()
    tool = TOOLS[kind]
    press_drag_release(tool, window, state, (200, 300), (150, 200), (50, 60))
    anns = state.images[0].annotations
    assert anns[0].kind == kind
    assert anns[0].props['p1'] == [200, 300]
    assert anns[0].props['p2'] == [50, 60]


def test_ink_tool_accumulates_points_with_preview_segments():
    window, state = FakeWindow(), make_state()
    tool = TOOLS['ink']
    tool.on_press(window, state, 10, 10)
    tool.on_drag(window, state, 20, 25)
    tool.on_drag(window, state, 30, 15)
    line_previews = [c for c in window.graph.calls if c[0] == 'draw_line']
    assert len(line_previews) >= 2          # incremental preview segments
    tool.on_release(window, state, 40, 20)
    anns = state.images[0].annotations
    assert anns[0].kind == 'ink'
    assert anns[0].props['points'] == [[10, 10], [20, 25], [30, 15], [40, 20]]
    assert anns[0].props['color'] == DEFAULT_TOOL_PROPS['ink']['color']
    assert state.undo[0].can_undo()
    # preview segments removed on release
    assert set(window.graph.deleted) >= {c[3] for c in line_previews[:2]}


# ---------------------------------------------------------------------------
# Click-to-place tools (dialog stubbed)
# ---------------------------------------------------------------------------

def test_click_place_tool_opens_dialog_and_commits(monkeypatch):
    from workonward_read.dialogs import annotate as annotate_dialogs

    window, state = FakeWindow(), make_state()
    captured = {}

    def fake_text_dialog(win, st, pos):
        captured['pos'] = pos
        return {'pos': list(pos), 'text': 'Hi', 'size_px': 32,
                'color': 'black', 'bold': False}

    monkeypatch.setattr(annotate_dialogs, 'text_dialog', fake_text_dialog)
    TOOLS['text'].on_release(window, state, 123, 456)
    assert captured['pos'] == (123, 456)
    anns = state.images[0].annotations
    assert anns[0].kind == 'text'
    assert anns[0].props['text'] == 'Hi'
    assert state.undo[0].can_undo()


def test_click_place_tool_cancel_adds_nothing(monkeypatch):
    from workonward_read.dialogs import annotate as annotate_dialogs

    window, state = FakeWindow(), make_state()
    monkeypatch.setattr(annotate_dialogs, 'stamp_dialog',
                        lambda win, st, pos: None)
    TOOLS['stamp'].on_release(window, state, 10, 10)
    assert state.images[0].annotations == []
    assert 0 not in state.undo


def test_armed_signature_placed_without_dialog_and_disarmed(monkeypatch):
    """Fill & Sign arming: the armed payload is placed directly at the
    click (no dialog), consumed one-shot, and the next click re-prompts."""
    from workonward_read.dialogs import annotate as annotate_dialogs
    from workonward_read.handlers.sign import apply_fill_sign

    window, state = FakeWindow(), make_state()
    dialog_calls = []
    monkeypatch.setattr(
        annotate_dialogs, 'signature_dialog',
        lambda win, st, pos: dialog_calls.append(pos) or None)

    apply_fill_sign(state, 'ARMED_PNG_B64')
    assert state.tool == 'signature'

    TOOLS['signature'].on_release(window, state, 120, 340)
    assert dialog_calls == []                       # no re-prompt
    anns = state.images[0].annotations
    assert len(anns) == 1
    assert anns[0].kind == 'signature'
    assert anns[0].props['png_b64'] == 'ARMED_PNG_B64'
    assert anns[0].props['pos'] == [120, 340]
    assert anns[0].props['scale'] == 1.0
    assert state.undo[0].can_undo()                 # undo snapshot pushed
    # DISARMED: the payload was consumed...
    assert 'png_b64' not in state.tool_props['signature']

    # ...so the next click routes to the dialog again (which cancels here).
    TOOLS['signature'].on_release(window, state, 10, 10)
    assert dialog_calls == [(10, 10)]
    assert len(state.images[0].annotations) == 1


def test_armed_signature_keeps_tool_prop_overrides(monkeypatch):
    """Arming merges tool_props overrides (e.g. scale) into the placement."""
    from workonward_read.handlers.sign import apply_fill_sign

    window, state = FakeWindow(), make_state()
    state.tool_props['signature'] = {'scale': 2.0}
    apply_fill_sign(state, 'PNGDATA')

    TOOLS['signature'].on_release(window, state, 5, 6)
    ann = state.images[0].annotations[0]
    assert ann.props['scale'] == 2.0
    assert ann.props['png_b64'] == 'PNGDATA'
    # the persistent override survives the one-shot disarm
    assert state.tool_props['signature'] == {'scale': 2.0}


def test_unarmed_click_place_tool_routes_to_dialog(monkeypatch):
    """Without an armed payload the signature tool opens its dialog."""
    from workonward_read.dialogs import annotate as annotate_dialogs

    window, state = FakeWindow(), make_state()
    monkeypatch.setattr(
        annotate_dialogs, 'signature_dialog',
        lambda win, st, pos: {'pos': list(pos), 'png_b64': 'DIALOG_PNG',
                              'scale': 1.0})
    TOOLS['signature'].on_release(window, state, 30, 40)
    ann = state.images[0].annotations[0]
    assert ann.props['png_b64'] == 'DIALOG_PNG'
    assert ann.props['pos'] == [30, 40]


# ---------------------------------------------------------------------------
# Measure tool
# ---------------------------------------------------------------------------

def test_measure_tool_shows_units_and_adds_no_annotation(monkeypatch):
    window, state = FakeWindow(), make_state()
    messages = []
    monkeypatch.setattr(ct.MeasureTool, '_show_result',
                        staticmethod(lambda win, msg: messages.append(msg)))
    tool = ct.MeasureTool()
    tool.on_press(window, state, 0, 0)
    tool.on_drag(window, state, 100, 0)
    tool.on_release(window, state, 200, 0)   # 200 px = 1 inch = 2.54 cm
    assert state.images[0].annotations == []
    assert 0 not in state.undo
    assert len(messages) == 1
    assert '200' in messages[0]
    assert '2.54' in messages[0]
    assert '1.00' in messages[0]


# ---------------------------------------------------------------------------
# Eraser over mixed annotation kinds
# ---------------------------------------------------------------------------

def _add_mixed(container):
    container.add_annotation('redact', {'p1': [0, 0], 'p2': [100, 100],
                                        'fill': 'black'})
    container.add_annotation('ink', {'points': [[200, 200], [250, 250]],
                                     'color': 'blue', 'width_px': 6})
    container.add_annotation('text', {'pos': [300, 300], 'text': 'Hello',
                                      'size_px': 40, 'color': 'black',
                                      'bold': False})


def test_eraser_removes_topmost_hit_and_reverts_to_redact():
    window, state = FakeWindow(), make_state()
    container = state.images[0]
    _add_mixed(container)
    container.annotations[2].graph_ids = [77, 78]
    state.tool = 'eraser'

    TOOLS['eraser'].on_release(window, state, 310, 320)   # inside text bbox
    kinds = [a.kind for a in container.annotations]
    assert kinds == ['redact', 'ink']
    assert 77 in window.graph.deleted and 78 in window.graph.deleted
    assert state.tool == 'redact'                          # one-shot revert
    assert state.undo[0].can_undo()                        # erase is undoable
    snapshot = state.undo[0].undo()
    assert [a.kind for a in snapshot] == ['redact', 'ink', 'text']


def test_eraser_hits_ink_and_boxed_kinds():
    window, state = FakeWindow(), make_state()
    container = state.images[0]
    _add_mixed(container)
    state.tool = 'eraser'
    TOOLS['eraser'].on_release(window, state, 225, 226)    # near ink segment
    assert [a.kind for a in container.annotations] == ['redact', 'text']

    state.tool = 'eraser'
    TOOLS['eraser'].on_release(window, state, 50, 50)      # inside redact box
    assert [a.kind for a in container.annotations] == ['text']


def test_eraser_miss_changes_nothing_but_still_reverts():
    window, state = FakeWindow(), make_state()
    container = state.images[0]
    _add_mixed(container)
    state.tool = 'eraser'
    TOOLS['eraser'].on_release(window, state, 390, 480)    # empty area
    assert len(container.annotations) == 3
    assert 0 not in state.undo
    assert state.tool == 'redact'


# ---------------------------------------------------------------------------
# Undo / redo via the edit handlers (add -> undo -> redo)
# ---------------------------------------------------------------------------

def test_undo_redo_flow_via_edit_handlers():
    from workonward_read.handlers import edit

    window, state = FakeWindow(), make_state()
    commit_annotation(window, state, 'redact',
                      {'p1': [0, 0], 'p2': [10, 10], 'fill': 'black'})
    commit_annotation(window, state, 'line',
                      {'p1': [0, 0], 'p2': [50, 50], 'color': 'red',
                       'width_px': 3})
    container = state.images[0]
    assert [a.kind for a in container.annotations] == ['redact', 'line']

    edit.undo(window, state)
    assert [a.kind for a in container.annotations] == ['redact']

    edit.undo(window, state)
    assert container.annotations == []

    edit.redo(window, state)
    assert [a.kind for a in container.annotations] == ['redact']

    edit.redo(window, state)
    assert [a.kind for a in container.annotations] == ['redact', 'line']

    # nothing left to redo; extra calls are no-ops
    edit.redo(window, state)
    assert [a.kind for a in container.annotations] == ['redact', 'line']


def test_undo_without_stack_or_images_is_noop():
    from workonward_read.handlers import edit

    window = FakeWindow()
    edit.undo(window, AppState())
    edit.redo(window, AppState())

    state = make_state()
    edit.undo(window, state)      # no stack for the page yet
    assert state.images[0].annotations == []


def test_delete_all_pushes_snapshots_and_is_undoable(monkeypatch):
    import FreeSimpleGUI as sg
    from workonward_read.handlers import edit

    monkeypatch.setattr(sg, 'popup_ok_cancel', lambda *a, **k: 'OK')

    window, state = FakeWindow(), make_state(pages=2)
    _add_mixed(state.images[0])
    state.images[1].add_annotation('redact', {'p1': [1, 1], 'p2': [5, 5],
                                              'fill': 'black'})

    edit.delete_all(window, state)
    assert state.images[0].annotations == []
    assert state.images[1].annotations == []

    # undoable per page
    edit.undo(window, state)
    assert [a.kind for a in state.images[0].annotations] == \
        ['redact', 'ink', 'text']
    state.current_page = 1
    edit.undo(window, state)
    assert [a.kind for a in state.images[1].annotations] == ['redact']


# ---------------------------------------------------------------------------
# Pure dialog helpers (no window needed)
# ---------------------------------------------------------------------------

def test_encode_png_b64_respects_size_budget():
    from workonward_read.dialogs.annotate import encode_png_b64

    import random
    random.seed(7)
    noisy = Image.new('RGB', (900, 900))
    noisy.putdata([(random.randrange(256), random.randrange(256),
                    random.randrange(256)) for _ in range(900 * 900)])
    b64 = encode_png_b64(noisy, max_bytes=200 * 1024)
    raw = base64.b64decode(b64)
    assert len(raw) <= 200 * 1024
    img = Image.open(io.BytesIO(raw))
    img.load()
    assert img.width < 900                    # was downscaled
    img.close()
    noisy.close()


def test_render_typed_signature_and_ink_strokes_png():
    from workonward_read.dialogs.annotate import (
        ink_strokes_to_png,
        render_typed_signature,
    )

    sig = render_typed_signature('Jane Doe')
    assert sig is not None and sig.mode == 'RGBA'
    assert sig.getbbox() is not None          # something was drawn
    sig.close()
    assert render_typed_signature('   ') is None

    ink = ink_strokes_to_png([[[0, 0], [40, 30], [80, 0]], [[10, 40]]])
    assert ink is not None and ink.mode == 'RGBA'
    assert ink.getbbox() is not None
    ink.close()
    assert ink_strokes_to_png([]) is None


def test_signature_props_render_via_engine():
    """A typed signature round-trips through the annotation engine burn-in."""
    from workonward_read.dialogs.annotate import (
        encode_png_b64,
        render_typed_signature,
    )
    from workonward_read.utils import find_fonts_folder, get_package_dir

    sig = render_typed_signature('Jane Doe')
    props = {'pos': [50, 50], 'png_b64': encode_png_b64(sig), 'scale': 1.0}
    sig.close()
    page = Image.new('RGB', (600, 300), (255, 255, 255))
    out = an.render_on_image(
        page, [{'id': 'x', 'kind': 'signature', 'props': props}], {}, 0, 1,
        216.0, 108.0, find_fonts_folder(get_package_dir()))
    non_white = any(out.getpixel((x, y)) != (255, 255, 255)
                    for x in range(50, 500, 4) for y in range(50, 200, 4))
    assert non_white
    out.close()
    page.close()
