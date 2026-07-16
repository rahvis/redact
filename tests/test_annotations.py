"""Tests for the typed annotation engine (workonward_read/annotations.py).

WorkOnward Read is free software licensed under GPL-3.0.
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import base64
import io
import pickle
from concurrent.futures import ProcessPoolExecutor
from datetime import date

import pytest
from PIL import Image

import fixtures  # noqa: F401  (path check; helpers synthesized inline)

from workonward_read import annotations as an
from workonward_read.annotations import (
    Annotation,
    UndoStack,
    estimate_bbox,
    format_bates,
    from_dict,
    hit_test,
    migrate_v1_rectangle,
    render_on_graph,
    render_on_image,
    substitute_template,
    to_dict,
)
from workonward_read.utils import find_fonts_folder, get_package_dir

FONT_DIR = find_fonts_folder(get_package_dir())

WHITE = (255, 255, 255)
PAGE_SIZE = (800, 1000)


def white_page(size=PAGE_SIZE):
    return Image.new('RGB', size, WHITE)


def red_png_b64(size=(20, 20)):
    img = Image.new('RGBA', size, (255, 0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode('ascii')


def make_ann(kind, **props):
    return Annotation(id=an.new_id(), kind=kind, props=props)


def render(img, anns, decorations=None, page_idx=0, total=1):
    dicts = [to_dict(a) if isinstance(a, Annotation) else a for a in anns]
    return render_on_image(img, dicts, decorations or {}, page_idx, total,
                           595.28, 841.89, FONT_DIR)


def all_kind_annotations():
    """One annotation of each of the 13 kinds."""
    png = red_png_b64()
    return [
        make_ann('redact', p1=[20, 20], p2=[120, 80], fill='black'),
        make_ann('text', pos=[50, 100], text='Héllo Ünïcode',
                 size_px=40, color='black', bold=True),
        make_ann('highlight', p1=[200, 200], p2=[400, 260],
                 color='#ffff00', alpha=0.4),
        make_ann('underline', p1=[100, 490], p2=[300, 520],
                 color='red', width_px=4),
        make_ann('strike', p1=[100, 540], p2=[300, 560],
                 color='blue', width_px=4),
        make_ann('ink', points=[[400, 600], [450, 650], [500, 600]],
                 color='blue', width_px=6),
        make_ann('rect', p1=[600, 100], p2=[700, 200], outline='black',
                 fill=None, width_px=3),
        make_ann('ellipse', p1=[600, 300], p2=[700, 380], outline='black',
                 fill='#00ff00', width_px=2),
        make_ann('line', p1=[100, 750], p2=[300, 750], color='black',
                 width_px=3),
        make_ann('arrow', p1=[100, 800], p2=[300, 800], color='black',
                 width_px=3),
        make_ann('stamp', pos=[100, 300], preset='approved', text='',
                 color='', angle=0, scale=1.0),
        make_ann('image', pos=[500, 800], png_b64=png, scale=2.0),
        make_ann('signature', pos=[550, 900], png_b64=png, scale=1.0),
    ]


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def test_to_dict_from_dict_roundtrip_all_kinds():
    for ann in all_kind_annotations():
        ann.graph_ids = [1, 2, 3]
        d = to_dict(ann)
        assert 'graph_ids' not in d
        back = from_dict(d)
        assert back.id == ann.id
        assert back.kind == ann.kind
        assert back.props == ann.props
        assert back.graph_ids == []
        # props are deep-copied, not shared
        assert back.props is not ann.props


def test_from_dict_generates_id_when_missing():
    a = from_dict({'kind': 'redact', 'props': {'p1': [0, 0], 'p2': [1, 1],
                                               'fill': 'black'}})
    assert a.id
    assert a.kind == 'redact'


def test_migrate_v1_rectangle():
    ann = migrate_v1_rectangle(((10, 20), (110, 80), 'white', 42))
    assert ann.kind == 'redact'
    assert ann.props == {'p1': [10, 20], 'p2': [110, 80], 'fill': 'white'}
    assert ann.graph_ids == []
    assert ann.id


# ---------------------------------------------------------------------------
# UndoStack
# ---------------------------------------------------------------------------

def test_undo_redo_semantics():
    stack = UndoStack()
    s0 = [make_ann('redact', p1=[0, 0], p2=[10, 10], fill='black')]
    stack.push(s0)
    s1 = s0 + [make_ann('line', p1=[0, 0], p2=[5, 5], color='red',
                        width_px=1)]
    stack.push(s1)
    s2 = s1 + [make_ann('text', pos=[1, 1], text='x', size_px=12,
                        color='black', bold=False)]

    restored = stack.undo(s2)
    assert [a.id for a in restored] == [a.id for a in s1]
    assert stack.can_redo()

    redone = stack.redo(restored)
    assert [a.id for a in redone] == [a.id for a in s2]

    # undo twice back to s0
    r1 = stack.undo(redone)
    r0 = stack.undo(r1)
    assert [a.id for a in r0] == [a.id for a in s0]
    assert stack.undo() is None


def test_undo_empty_returns_none():
    stack = UndoStack()
    assert stack.undo() is None
    assert stack.redo() is None


def test_push_clears_redo():
    stack = UndoStack()
    stack.push([make_ann('redact', p1=[0, 0], p2=[1, 1], fill='black')])
    stack.undo([])
    assert stack.can_redo()
    stack.push([])
    assert not stack.can_redo()


def test_undo_stack_capped_at_25():
    stack = UndoStack()
    ids = []
    for i in range(30):
        a = make_ann('redact', p1=[i, i], p2=[i + 1, i + 1], fill='black')
        ids.append(a.id)
        stack.push([a])
    # Newest snapshot comes back first
    first = stack.undo()
    assert first[0].id == ids[29]
    count = 1
    while stack.undo() is not None:
        count += 1
    assert count == 25


def test_snapshots_are_deep_copies():
    ann = make_ann('redact', p1=[0, 0], p2=[10, 10], fill='black')
    stack = UndoStack()
    stack.push([ann])
    ann.props['fill'] = 'white'
    restored = stack.undo()
    assert restored[0].props['fill'] == 'black'


# ---------------------------------------------------------------------------
# Burn-in rendering
# ---------------------------------------------------------------------------

def test_render_returns_rgb_and_does_not_mutate_input():
    img = white_page()
    out = render(img, [make_ann('redact', p1=[20, 20], p2=[120, 80],
                                fill='black')])
    assert out.mode == 'RGB'
    assert out.size == img.size
    # input untouched
    assert img.getpixel((50, 50)) == WHITE
    assert out.getpixel((50, 50)) == (0, 0, 0)


def test_render_empty_annotations_is_noop():
    out = render(white_page(), [])
    assert out.getpixel((400, 500)) == WHITE


def test_redact_black_pixels():
    out = render(white_page(), [make_ann('redact', p1=[20, 20],
                                         p2=[120, 80], fill='black')])
    assert out.getpixel((70, 50)) == (0, 0, 0)
    assert out.getpixel((300, 300)) == WHITE


def test_highlight_blends_yellowish():
    out = render(white_page(), [make_ann('highlight', p1=[200, 200],
                                         p2=[400, 260], color='#ffff00',
                                         alpha=0.4)])
    r, g, b = out.getpixel((300, 230))
    # white composited with yellow at alpha 0.4 -> blue channel ~153
    assert r >= 250 and g >= 250
    assert 140 <= b <= 170
    assert out.getpixel((100, 100)) == WHITE


def test_text_draws_pixels_in_bbox():
    out = render(white_page(), [make_ann('text', pos=[50, 100],
                                         text='Héllo Ünïcode', size_px=40,
                                         color='black', bold=True)])
    found = any(out.getpixel((x, y)) != WHITE
                for x in range(50, 350, 2) for y in range(100, 160, 2))
    assert found
    # far away untouched
    assert out.getpixel((700, 700)) == WHITE


def test_underline_and_strike_lines():
    out = render(white_page(), [
        make_ann('underline', p1=[100, 490], p2=[300, 520], color='red',
                 width_px=4),
        make_ann('strike', p1=[100, 540], p2=[300, 560], color='blue',
                 width_px=4),
    ])
    r, g, b = out.getpixel((200, 520))       # underline at bottom edge
    assert r > 200 and g < 100 and b < 100
    r, g, b = out.getpixel((200, 550))       # strike at vertical middle
    assert b > 200 and r < 100
    # above the underline stays white
    assert out.getpixel((200, 495)) == WHITE


def test_ink_stroke():
    out = render(white_page(), [make_ann('ink',
                                         points=[[400, 600], [450, 650],
                                                 [500, 600]],
                                         color='blue', width_px=6)])
    r, g, b = out.getpixel((425, 625))
    assert b > 200 and r < 100


def test_rect_outline_only_and_ellipse_filled():
    out = render(white_page(), [
        make_ann('rect', p1=[600, 100], p2=[700, 200], outline='black',
                 fill=None, width_px=3),
        make_ann('ellipse', p1=[600, 300], p2=[700, 380], outline='black',
                 fill='#00ff00', width_px=2),
    ])
    assert out.getpixel((650, 101)) != WHITE     # top edge drawn
    assert out.getpixel((650, 150)) == WHITE     # interior stays white
    r, g, b = out.getpixel((650, 340))           # ellipse center green
    assert g > 200 and r < 100


def test_line_and_arrow():
    out = render(white_page(), [
        make_ann('line', p1=[100, 750], p2=[300, 750], color='black',
                 width_px=3),
        make_ann('arrow', p1=[100, 800], p2=[300, 800], color='black',
                 width_px=3),
    ])
    assert out.getpixel((200, 750)) == (0, 0, 0)
    assert out.getpixel((200, 800)) == (0, 0, 0)
    # arrow head widens near tip beyond the shaft width
    head_dark = any(out.getpixel((295, 800 + dy)) != WHITE
                    for dy in range(-8, 9))
    assert head_dark


def test_stamp_burn_in_and_rotation_moves_pixels():
    a0 = make_ann('stamp', pos=[100, 300], preset='approved', text='',
                  color='', angle=0, scale=1.0)
    a45 = make_ann('stamp', pos=[100, 300], preset='approved', text='',
                   color='', angle=45, scale=1.0)
    out0 = render(white_page(), [a0])
    out45 = render(white_page(), [a45])
    # green preset pixels exist somewhere in the stamp region
    greens0 = sum(1 for x in range(100, 500, 3) for y in range(300, 420, 3)
                  if out0.getpixel((x, y)) != WHITE)
    assert greens0 > 0
    assert out0.tobytes() != out45.tobytes()


def test_stamp_custom_uses_props_color_and_text():
    out = render(white_page(), [make_ann('stamp', pos=[100, 300],
                                         preset='custom', text='paid',
                                         color='#0000ff', angle=0,
                                         scale=1.0)])
    blueish = any(out.getpixel((x, y))[2] > 150
                  and out.getpixel((x, y))[0] < 120
                  for x in range(100, 400, 2) for y in range(300, 400, 2))
    assert blueish


def test_image_and_signature_paste():
    png = red_png_b64((20, 20))
    out = render(white_page(), [
        make_ann('image', pos=[500, 800], png_b64=png, scale=2.0),
        make_ann('signature', pos=[50, 900], png_b64=png, scale=1.0),
    ])
    assert out.getpixel((520, 820)) == (255, 0, 0)   # scaled 2x -> 40x40
    assert out.getpixel((541, 841)) == WHITE          # beyond scaled extent
    assert out.getpixel((60, 910)) == (255, 0, 0)


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        render(white_page(), [{'id': 'x', 'kind': 'sparkle', 'props': {}}])


def _full_page_highlight_blend(page, p1, p2, color, alpha):
    """Replicate the previous full-page-overlay highlight arithmetic."""
    from PIL import ImageDraw
    base = page.convert('RGBA')
    overlay = Image.new('RGBA', base.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rectangle(
        an._norm_box(p1, p2), fill=an._rgba(color, round(alpha * 255)))
    expected = Image.alpha_composite(base, overlay).convert('RGB')
    overlay.close()
    base.close()
    return expected


def test_highlight_bbox_overlay_pixel_equal_to_full_page_blend():
    """The bbox-sized highlight overlay must blend pixel-identically to the
    old full-page-overlay implementation (gradient background so any blend
    or offset error shows)."""
    w, h = 300, 200
    page = Image.new('RGB', (w, h))
    page.putdata([((x * 7) % 256, (y * 5) % 256, (x + y) % 256)
                  for y in range(h) for x in range(w)])
    p1, p2 = [35, 20], [220, 140]
    color, alpha = '#3366ff', 0.35

    out = render(page, [make_ann('highlight', p1=p1, p2=p2,
                                 color=color, alpha=alpha)])
    expected = _full_page_highlight_blend(page, p1, p2, color, alpha)
    assert out.tobytes() == expected.tobytes()


def test_highlight_bbox_overlay_clamps_out_of_page_boxes():
    """Boxes reaching outside the page blend exactly like the old code
    (PIL clipped the full-page overlay drawing to the page)."""
    w, h = 120, 90
    page = Image.new('RGB', (w, h))
    page.putdata([((x * 3) % 256, (y * 11) % 256, 200)
                  for y in range(h) for x in range(w)])
    p1, p2 = [-10, -10], [50, 60]

    out = render(page, [make_ann('highlight', p1=p1, p2=p2,
                                 color='#ffff00', alpha=0.4)])
    expected = _full_page_highlight_blend(page, p1, p2, '#ffff00', 0.4)
    assert out.tobytes() == expected.tobytes()

    # fully outside the page: no-op, no crash
    off = render(page, [make_ann('highlight', p1=[500, 500], p2=[600, 600],
                                 color='#ffff00', alpha=0.4)])
    assert off.tobytes() == page.convert('RGB').tobytes()


# ---------------------------------------------------------------------------
# Decorations
# ---------------------------------------------------------------------------

def test_watermark_center_tinted_corner_untouched():
    out = render(white_page(),
                 [], decorations={'watermark': {'text': 'CONFIDENTIAL'}})
    assert out.getpixel((5, 5)) == WHITE
    cx, cy = PAGE_SIZE[0] // 2, PAGE_SIZE[1] // 2
    tinted = any(out.getpixel((x, y)) != WHITE
                 for x in range(cx - 120, cx + 120, 3)
                 for y in range(cy - 120, cy + 120, 3))
    assert tinted


def test_watermark_png_b64():
    out = render(white_page(), [], decorations={
        'watermark': {'png_b64': red_png_b64((50, 50)), 'opacity': 0.5}})
    cx, cy = PAGE_SIZE[0] // 2, PAGE_SIZE[1] // 2
    r, g, b = out.getpixel((cx, cy))
    assert r > g and r > b            # red tint in the center
    assert g > 100                    # but semi-transparent, not solid red
    assert out.getpixel((5, 5)) == WHITE


def test_header_footer_and_page_numbers_draw_in_bands():
    decorations = {
        'header_footer': {'left': 'ACME', 'center': '', 'right': '{date}',
                          'position': 'header', 'size_px': 28},
        'page_numbers': {'template': '{page} / {total}', 'start_at': 1,
                         'position': 'footer-center'},
    }
    out = render(white_page(), [], decorations=decorations,
                 page_idx=1, total=3)
    w, h = out.size
    header_dark = any(out.getpixel((x, y)) != WHITE
                      for x in range(20, w - 20, 3) for y in range(20, 60, 2))
    footer_dark = any(out.getpixel((x, y)) != WHITE
                      for x in range(w // 3, 2 * w // 3, 2)
                      for y in range(h - 60, h - 10, 2))
    assert header_dark
    assert footer_dark
    # middle of page untouched
    assert out.getpixel((w // 2, h // 2)) == WHITE


def test_bates_footer_pixels():
    out = render(white_page(), [], decorations={
        'bates': {'prefix': 'AB', 'start': 100, 'digits': 6,
                  'position': 'footer-right'}})
    w, h = out.size
    dark = any(out.getpixel((x, y)) != WHITE
               for x in range(w // 2, w - 10, 2)
               for y in range(h - 60, h - 10, 2))
    assert dark


def _dark_bbox(img, threshold=128):
    """(x0, y0, x1, y1) bbox of pixels darker than ``threshold``, or None."""
    mask = img.convert('L').point(lambda v: 255 if v < threshold else 0)
    return mask.getbbox()


def test_decoration_preview_scale_halves_footer_metrics():
    """decor_scale=0.5 on a half-size bitmap renders the footer text at
    half the size (and half the margin) of the scale-1.0 rendering, still
    horizontally centered — the zoomed preview matches the export."""
    decorations = {'page_numbers': {'template': 'PAGE {page} OF {total}',
                                    'start_at': 1,
                                    'position': 'footer-center',
                                    'size_px': 48}}
    full = render_on_image(white_page((800, 1000)), [], decorations, 0, 1,
                           595.28, 841.89, FONT_DIR)
    half = render_on_image(white_page((400, 500)), [], decorations, 0, 1,
                           595.28, 841.89, FONT_DIR, decor_scale=0.5)

    full_box = _dark_bbox(full)
    half_box = _dark_bbox(half)
    assert full_box and half_box

    # Text height at 50% zoom is about half the 100% height.
    full_h = full_box[3] - full_box[1]
    half_h = half_box[3] - half_box[1]
    assert abs(half_h - full_h / 2.0) <= max(2.0, 0.15 * full_h / 2.0)

    # Bottom margin scales with the zoom too (24 px -> 12 px).
    full_gap = full.height - full_box[3]
    half_gap = half.height - half_box[3]
    assert abs(half_gap - full_gap / 2.0) <= 2.0

    # Horizontally centered in both renderings.
    full_center = (full_box[0] + full_box[2]) / 2.0 / full.width
    half_center = (half_box[0] + half_box[2]) / 2.0 / half.width
    assert abs(full_center - 0.5) < 0.02
    assert abs(half_center - 0.5) < 0.02
    assert abs(full_center - half_center) < 0.02


def test_decoration_scale_default_is_identity():
    """The export path (no decor_scale argument) is unchanged."""
    decorations = {'bates': {'prefix': 'AB', 'start': 1, 'digits': 4,
                             'position': 'footer-right'}}
    implicit = render_on_image(white_page((400, 300)), [], decorations, 0, 1,
                               595.28, 841.89, FONT_DIR)
    explicit = render_on_image(white_page((400, 300)), [], decorations, 0, 1,
                               595.28, 841.89, FONT_DIR, decor_scale=1.0)
    assert implicit.tobytes() == explicit.tobytes()


def test_bates_and_page_number_substitution_across_3_pages():
    cfg = {'prefix': 'AB', 'start': 100, 'digits': 6}
    assert [format_bates(cfg, i) for i in range(3)] == [
        'AB000100', 'AB000101', 'AB000102']
    for i in range(3):
        assert substitute_template('{page} / {total}', i + 1, 3) == \
            '%d / 3' % (i + 1)
    assert substitute_template('p{page} d{date} b{bates}', 2, 3, 'AB000101') \
        == 'p2 d%s bAB000101' % date.today().isoformat()
    assert format_bates(None, 0) == ''
    assert format_bates({'prefix': '', 'start': 7, 'digits': 3}, 0) == '007'


# ---------------------------------------------------------------------------
# hit_test
# ---------------------------------------------------------------------------

def test_hit_test_boxed_kinds():
    redact = make_ann('redact', p1=[20, 20], p2=[120, 80], fill='black')
    assert hit_test([redact], 50, 50) is redact
    assert hit_test([redact], 500, 500) is None
    # normalized corners: p2 before p1 still hits
    swapped = make_ann('highlight', p1=[400, 260], p2=[200, 200],
                       color='#ffff00', alpha=0.4)
    assert hit_test([swapped], 300, 230) is swapped


def test_hit_test_reverse_order_topmost_wins():
    a = make_ann('redact', p1=[0, 0], p2=[100, 100], fill='black')
    b = make_ann('rect', p1=[0, 0], p2=[100, 100], outline='red',
                 fill=None, width_px=1)
    assert hit_test([a, b], 50, 50) is b


def test_hit_test_line_tolerance():
    line = make_ann('line', p1=[0, 0], p2=[100, 100], color='black',
                    width_px=1)
    assert hit_test([line], 50, 54) is line      # ~2.8 px from segment
    assert hit_test([line], 50, 70) is None      # ~14 px away
    # tolerance grows with width
    fat = make_ann('line', p1=[0, 0], p2=[100, 100], color='black',
                   width_px=30)
    assert hit_test([fat], 50, 70) is fat


def test_hit_test_ink_and_text_and_image():
    ink = make_ann('ink', points=[[400, 600], [450, 650], [500, 600]],
                   color='blue', width_px=6)
    assert hit_test([ink], 426, 627) is ink
    assert hit_test([ink], 450, 700) is None

    text = make_ann('text', pos=[50, 100], text='Hello', size_px=40,
                    color='black', bold=False)
    assert hit_test([text], 70, 120) is text
    assert hit_test([text], 70, 400) is None

    image = make_ann('image', pos=[500, 800], png_b64=red_png_b64((20, 20)),
                     scale=2.0)
    assert hit_test([image], 530, 830) is image
    assert hit_test([image], 600, 900) is None


def test_hit_test_prefers_stored_bbox():
    text = make_ann('text', pos=[50, 100], text='Hello', size_px=40,
                    color='black', bold=False)
    text.props['_bbox'] = [50, 100, 500, 500]
    assert hit_test([text], 400, 400) is text


def test_hit_test_empty_list():
    assert hit_test([], 10, 10) is None


def test_estimate_bbox_stamp_rotation_grows_height():
    props = {'pos': [0, 0], 'preset': 'approved', 'angle': 0, 'scale': 1.0}
    flat = estimate_bbox('stamp', props)
    rotated = estimate_bbox('stamp', dict(props, angle=45))
    assert (rotated[3] - rotated[1]) > (flat[3] - flat[1])


# ---------------------------------------------------------------------------
# render_on_graph (duck-typed fake graph; module must not import sg)
# ---------------------------------------------------------------------------

class FakeWidget:
    def __init__(self, start=1000):
        self.calls = []
        self._counter = start

    def _record(self, name, args, kwargs):
        self._counter += 1
        self.calls.append((name, args, kwargs, self._counter))
        return self._counter

    def create_rectangle(self, *args, **kwargs):
        return self._record('create_rectangle', args, kwargs)

    def create_line(self, *args, **kwargs):
        return self._record('create_line', args, kwargs)


class FakeGraph:
    def __init__(self):
        self.calls = []
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


def test_annotations_module_does_not_import_gui():
    import os
    import subprocess
    import sys
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = (
        "import sys; import workonward_read.annotations; "
        "bad = [m for m in sys.modules "
        "if 'FreeSimpleGUI' in m or m == 'tkinter' or m.startswith('tkinter.')]; "
        "sys.exit(1 if bad else 0)"
    )
    result = subprocess.run([sys.executable, '-c', code], cwd=repo_root)
    assert result.returncode == 0


def test_render_on_graph_redact_zoom_scaling():
    graph = FakeGraph()
    ann = make_ann('redact', p1=[100, 200], p2=[300, 400], fill='black')
    ids = render_on_graph(graph, ann, 50)
    assert ids
    assert ann.graph_ids == ids
    name, args, kwargs, _ = graph.calls[0]
    assert name == 'draw_rectangle'
    assert args[0] == (50.0, -100.0)      # scaled by 0.5, y negated
    assert args[1] == (150.0, -200.0)
    assert kwargs['fill_color'] == 'black'


def test_render_on_graph_highlight_uses_widget_stipple():
    graph = FakeGraph()
    ann = make_ann('highlight', p1=[10, 10], p2=[50, 30], color='#ffff00',
                   alpha=0.4)
    ids = render_on_graph(graph, ann, 100)
    assert len(ids) == 1
    name, args, kwargs, fid = graph.Widget.calls[0]
    assert name == 'create_rectangle'
    assert kwargs['stipple'] == 'gray50'
    assert kwargs['fill'] == '#ffff00'
    assert args == (10, 10, 50, 30)
    assert ids[0] == fid


def test_render_on_graph_arrow_uses_widget_create_line():
    graph = FakeGraph()
    ann = make_ann('arrow', p1=[0, 0], p2=[100, 0], color='red', width_px=2)
    render_on_graph(graph, ann, 200)
    name, args, kwargs, _ = graph.Widget.calls[0]
    assert name == 'create_line'
    assert kwargs['arrow'] == 'last'
    assert args == (0, 0, 200, 0)         # zoom 200 doubles coords


def test_render_on_graph_all_kinds_produce_ids():
    for ann in all_kind_annotations():
        graph = FakeGraph()
        ids = render_on_graph(graph, ann, 100)
        assert ids, 'no figure ids for kind %s' % ann.kind


def test_render_on_graph_image_png_cached_per_zoom_and_scale(monkeypatch):
    """Image/signature previews cache the decoded+scaled+re-encoded PNG on
    the annotation (transient), keyed by (zoom, scale, payload)."""
    calls = {'decode': 0}
    real_decode = an._decode_png

    def counting_decode(png_b64):
        calls['decode'] += 1
        return real_decode(png_b64)

    monkeypatch.setattr(an, '_decode_png', counting_decode)

    ann = make_ann('signature', pos=[10, 10], png_b64=red_png_b64(),
                   scale=1.0)
    render_on_graph(FakeGraph(), ann, 100)
    assert calls['decode'] == 1

    # Same zoom again (page flip / redraw): served from the cache.
    graph = FakeGraph()
    render_on_graph(graph, ann, 100)
    assert calls['decode'] == 1
    assert graph.calls and graph.calls[0][0] == 'draw_image'

    # Zoom change: re-decode + re-encode.
    render_on_graph(FakeGraph(), ann, 140)
    assert calls['decode'] == 2

    # Scale-prop change invalidates.
    ann.props['scale'] = 2.0
    render_on_graph(FakeGraph(), ann, 140)
    assert calls['decode'] == 3

    # Payload change invalidates.
    ann.props['png_b64'] = red_png_b64((10, 10))
    render_on_graph(FakeGraph(), ann, 140)
    assert calls['decode'] == 4

    # The cache is transient: never serialized.
    assert set(to_dict(ann)) == {'id', 'kind', 'props'}
    assert '_graph_png_cache' not in to_dict(ann)['props']


def test_render_on_graph_ink_segments_and_text_font():
    graph = FakeGraph()
    ink = make_ann('ink', points=[[0, 0], [10, 10], [20, 0]], color='blue',
                   width_px=2)
    ids = render_on_graph(graph, ink, 100)
    assert len(ids) == 2                  # two segments

    graph = FakeGraph()
    text = make_ann('text', pos=[10, 20], text='Hi', size_px=40,
                    color='black', bold=True)
    render_on_graph(graph, text, 100)
    name, args, kwargs, _ = graph.calls[0]
    assert name == 'draw_text'
    assert args[0] == 'Hi'
    assert kwargs['font'][2] == 'bold'
    assert kwargs['text_location'] == 'nw'


# ---------------------------------------------------------------------------
# Picklability / worker execution
# ---------------------------------------------------------------------------

def test_render_on_image_args_picklable_and_runs_in_process_pool():
    img = Image.new('RGB', (200, 250), WHITE)
    anns = [
        to_dict(make_ann('redact', p1=[10, 10], p2=[60, 40], fill='black')),
        to_dict(make_ann('highlight', p1=[80, 80], p2=[150, 120],
                         color='#ffff00', alpha=0.4)),
    ]
    decorations = {'bates': {'prefix': 'AB', 'start': 1, 'digits': 4,
                             'position': 'footer-right'}}
    args = (img, anns, decorations, 0, 3, 595.28, 841.89, FONT_DIR)
    pickle.dumps(args)  # must not raise

    with ProcessPoolExecutor(max_workers=1) as pool:
        out = pool.submit(render_on_image, *args).result(timeout=120)

    assert out.mode == 'RGB'
    assert out.getpixel((30, 25)) == (0, 0, 0)
    r, g, b = out.getpixel((100, 100))
    assert r >= 250 and g >= 250 and 140 <= b <= 170
