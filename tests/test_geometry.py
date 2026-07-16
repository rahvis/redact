"""Unit tests for workonward_read.geometry (pure pixel-space transforms).

The rotation formula is validated against PIL's actual bitmap transposes:
a point transformed with rotate_point_cw must land exactly on the pixel the
transpose moved it to.

License: GPL-3.0
(c) 2026 CoverUP contributors
"""

import pytest
import fixtures  # noqa: F401  (path setup / consistency with other suites)
from PIL import Image

from workonward_read import geometry
from workonward_read.annotations import Annotation, new_id
from workonward_read.pdf_ops import _CW_TRANSPOSE


def make_ann(kind, **props):
    return Annotation(id=new_id(), kind=kind, props=props)


# ---------------------------------------------------------------------------
# rotate_point_cw / translate_point
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('degrees', [90, 180, 270])
def test_rotate_point_cw_matches_pil_transpose(degrees):
    """Marked pixels must land exactly where PIL's transpose moves them."""
    w, h = 7, 5
    marks = {(0, 0): (255, 0, 0), (6, 0): (0, 255, 0), (0, 4): (0, 0, 255),
             (6, 4): (255, 255, 0), (3, 2): (255, 0, 255)}
    img = Image.new('RGB', (w, h), 'white')
    for (x, y), color in marks.items():
        img.putpixel((x, y), color)

    rotated = img.transpose(_CW_TRANSPOSE[degrees])
    for (x, y), color in marks.items():
        nx, ny = geometry.rotate_point_cw((x, y), degrees, w, h)
        assert float(nx).is_integer() and float(ny).is_integer()
        assert rotated.getpixel((int(nx), int(ny))) == color, \
            f'({x},{y}) rot {degrees}'


def test_rotate_point_cw_90_formula():
    # 90 CW: (x, y) -> (old_h - 1 - y, x)
    assert geometry.rotate_point_cw((3, 2), 90, 10, 20) == [17, 3]
    assert geometry.rotate_point_cw((3, 2), 0, 10, 20) == [3, 2]
    with pytest.raises(ValueError):
        geometry.rotate_point_cw((0, 0), 45, 10, 10)


def test_rotate_point_cw_four_times_is_identity():
    w, h = 11, 7
    pt = [4.0, 5.0]
    dims = (w, h)
    for _ in range(4):
        pt = geometry.rotate_point_cw(pt, 90, dims[0], dims[1])
        dims = (dims[1], dims[0])
    assert pt == [4.0, 5.0]
    assert dims == (w, h)


def test_translate_point():
    assert geometry.translate_point((5, 8), -2, 3) == [3.0, 11.0]


# ---------------------------------------------------------------------------
# transform_annotation: rotate
# ---------------------------------------------------------------------------

def test_rotate_normalizes_boxed_corners():
    ann = make_ann('redact', p1=[10, 20], p2=[30, 60], fill='black')
    out = geometry.transform_annotation(ann, ('rotate', 90, 100, 200))
    assert out is ann
    # corners rotate to (200-1-20, 10)=(179,10) and (200-1-60, 30)=(139,30);
    # re-normalized so p1 is the min corner.
    assert ann.props['p1'] == [139, 10]
    assert ann.props['p2'] == [179, 30]


def test_rotate_preserves_segment_direction():
    ann = make_ann('arrow', p1=[10, 10], p2=[50, 10], color='black', width_px=2)
    geometry.transform_annotation(ann, ('rotate', 90, 100, 100))
    # head (p2) stays p2: (99-10, 10)=(89,10) and (99-10, 50)=(89,50)
    assert ann.props['p1'] == [89, 10]
    assert ann.props['p2'] == [89, 50]


def test_rotate_ink_points_and_text_pos():
    ink = make_ann('ink', points=[[10, 20], [30, 40]], color='blue', width_px=2)
    geometry.transform_annotation(ink, ('rotate', 180, 100, 200))
    assert ink.props['points'] == [[89, 179], [69, 159]]

    text = make_ann('text', pos=[10, 20], text='x', size_px=24,
                    color='black', bold=False)
    geometry.transform_annotation(text, ('rotate', 90, 100, 200))
    assert text.props['pos'] == [179, 10]


def test_rotate_adjusts_stamp_angle():
    stamp = make_ann('stamp', pos=[10, 10], preset='approved', text='',
                     color='', angle=30.0, scale=1.0)
    geometry.transform_annotation(stamp, ('rotate', 90, 100, 100))
    assert stamp.props['angle'] == pytest.approx(300.0)  # (30 - 90) % 360


def test_rotate_annotation_four_times_is_identity():
    kinds = [
        make_ann('redact', p1=[10, 20], p2=[30, 60], fill='black'),
        make_ann('line', p1=[5, 6], p2=[70, 80], color='black', width_px=2),
        make_ann('ink', points=[[10, 20], [30, 40], [50, 5]],
                 color='blue', width_px=2),
        make_ann('text', pos=[12, 34], text='x', size_px=24,
                 color='black', bold=False),
        make_ann('stamp', pos=[40, 40], preset='draft', text='', color='',
                 angle=15.0, scale=1.0),
    ]
    import copy
    originals = [copy.deepcopy(a.props) for a in kinds]
    dims = (100, 200)
    for _ in range(4):
        for ann in kinds:
            geometry.transform_annotation(ann, ('rotate', 90, dims[0], dims[1]))
        dims = (dims[1], dims[0])
    for ann, original in zip(kinds, originals):
        # exact identity (integer inputs stay exact through 4x90 degrees;
        # float results compare equal to the original ints)
        assert ann.props == original


# ---------------------------------------------------------------------------
# transform_annotation: crop
# ---------------------------------------------------------------------------

def test_crop_translates_and_drops_outside():
    inside = make_ann('rect', p1=[120, 130], p2=[150, 160], outline='black',
                      fill=None, width_px=2)
    outside = make_ann('rect', p1=[0, 0], p2=[50, 50], outline='black',
                       fill=None, width_px=2)
    box = (100, 100, 300, 400)
    assert geometry.transform_annotation(inside, ('crop', box)) is inside
    assert inside.props['p1'] == [20, 30]
    assert inside.props['p2'] == [50, 60]
    assert geometry.transform_annotation(outside, ('crop', box)) is None


def test_crop_clamps_partially_outside_boxed_kind():
    ann = make_ann('highlight', p1=[50, 50], p2=[150, 150],
                   color='#ffff00', alpha=0.4)
    geometry.transform_annotation(ann, ('crop', (100, 100, 300, 400)))
    assert ann.props['p1'] == [0, 0]
    assert ann.props['p2'] == [50, 50]


def test_crop_redact_keeps_page_intersection():
    # A redact reaching beyond the crop box on every side keeps covering the
    # entire visible page region.
    ann = make_ann('redact', p1=[0, 0], p2=[500, 500], fill='black')
    geometry.transform_annotation(ann, ('crop', (100, 100, 300, 400)))
    assert ann.props['p1'] == [0, 0]
    assert ann.props['p2'] == [200, 300]  # full new page


def test_crop_ink_and_pos_kinds():
    ink = make_ann('ink', points=[[120, 120], [400, 500]],
                   color='blue', width_px=2)
    geometry.transform_annotation(ink, ('crop', (100, 100, 300, 400)))
    assert ink.props['points'] == [[20, 20], [300, 400]]

    text = make_ann('text', pos=[150, 150], text='keep', size_px=24,
                    color='black', bold=False)
    assert geometry.transform_annotation(
        text, ('crop', (100, 100, 300, 400))) is text
    assert text.props['pos'] == [50, 50]

    gone = make_ann('text', pos=[1000, 1000], text='gone', size_px=24,
                    color='black', bold=False)
    assert geometry.transform_annotation(
        gone, ('crop', (100, 100, 300, 400))) is None


def test_crop_keeps_pos_kind_whose_body_reaches_into_the_box():
    # Anchor above/left of the crop box, but the estimated bbox reaches in.
    text = make_ann('text', pos=[90, 90], text='long enough text', size_px=24,
                    color='black', bold=False)
    assert geometry.transform_annotation(
        text, ('crop', (100, 100, 300, 400))) is text
    assert text.props['pos'] == [-10, -10]


def test_transform_annotations_filters_dropped():
    anns = [
        make_ann('redact', p1=[120, 120], p2=[140, 140], fill='black'),
        make_ann('redact', p1=[0, 0], p2=[10, 10], fill='black'),
    ]
    result = geometry.transform_annotations(anns, ('crop', (100, 100, 300, 400)))
    assert len(result) == 1
    assert result[0] is anns[0]


def test_transform_annotation_accepts_plain_dicts():
    ann = {'id': 'x', 'kind': 'redact',
           'props': {'p1': [10, 20], 'p2': [30, 40], 'fill': 'black'}}
    out = geometry.transform_annotation(ann, ('rotate', 180, 100, 100))
    assert out is ann
    assert ann['props']['p1'] == [69, 59]
    assert ann['props']['p2'] == [89, 79]
    with pytest.raises(ValueError):
        geometry.transform_annotation(ann, ('frobnicate',))


# ---------------------------------------------------------------------------
# transform_rect (search-hit remapping support)
# ---------------------------------------------------------------------------

def test_transform_rect_rotate_90():
    # page 100x200; rect corners rotate like any other points.
    rect = geometry.transform_rect([10, 20, 30, 60], [('rotate', 90)], 100, 200)
    assert rect == [139, 10, 179, 30]


def test_transform_rect_crop_clips_and_drops():
    ops = [('crop', [100, 100, 300, 400])]
    assert geometry.transform_rect([120, 120, 150, 150], ops, 500, 500) == \
        [20, 20, 50, 50]
    # partially outside: clipped to the new page
    assert geometry.transform_rect([50, 50, 150, 150], ops, 500, 500) == \
        [0, 0, 50, 50]
    # fully outside: gone
    assert geometry.transform_rect([0, 0, 40, 40], ops, 500, 500) is None


def test_transform_rect_chains_ops_and_tracks_page_size():
    # rotate 90 on a 100x200 page, then crop the rotated (200x100) page.
    ops = [('rotate', 90), ('crop', [100, 0, 200, 100])]
    rect = geometry.transform_rect([10, 20, 30, 60], ops, 100, 200)
    # after rotate: [139, 10, 179, 30]; after crop -100 in x: [39, 10, 79, 30]
    assert rect == [39, 10, 79, 30]
