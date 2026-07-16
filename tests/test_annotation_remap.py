"""Regression tests: annotations must be remapped when pages are rotated or
cropped through PageOpsJournal.apply_to_images (they used to keep stale
coordinates, burning redactions in the wrong place and EXPOSING covered
text), and cropped containers must keep the legacy inverted size convention
(size[0] = WIDTH in pt, stored in height_in_pt) used by the export path.

License: GPL-3.0
(c) 2026 WorkOnward Read contributors
"""

import pytest
import fixtures  # noqa: F401  (path setup)
from PIL import Image

from workonward_read.image_container import ImageContainer
from workonward_read.pdf_ops import PT_PER_PX, PageOpsJournal

SECRET = (10, 10, 10)          # "secret ink" — dark but not redaction-black
PAGE_PX = (400, 600)           # portrait page, px at 200 PPI
PAGE_SIZE = (144.0, 216.0)     # (w_pt, h_pt) = px * 72 / 200, legacy order
SECRET_BOX = (100, 200, 140, 230)  # x0, y0, x1, y1 (exclusive)


def secret_container():
    """White portrait page with a block of 'secret' pixels covered by a
    redact annotation (plus an ink stroke and a text label on the block)."""
    img = Image.new('RGB', PAGE_PX, 'white')
    for x in range(SECRET_BOX[0], SECRET_BOX[2]):
        for y in range(SECRET_BOX[1], SECRET_BOX[3]):
            img.putpixel((x, y), SECRET)
    container = ImageContainer(img, PAGE_SIZE)
    container.add_annotation('redact', {
        'p1': [SECRET_BOX[0] - 5, SECRET_BOX[1] - 5],
        'p2': [SECRET_BOX[2] + 5, SECRET_BOX[3] + 5],
        'fill': 'black',
    })
    container.add_annotation('ink', {
        'points': [[110, 205], [130, 225]], 'color': 'blue', 'width_px': 2})
    container.add_annotation('text', {
        'pos': [110, 205], 'text': 'x', 'size_px': 12,
        'color': 'black', 'bold': False})
    return container


def count_secret_pixels(img):
    colors = img.getcolors(img.width * img.height) or []
    return sum(count for count, color in colors if color == SECRET)


def test_fixture_sanity_redaction_covers_without_ops():
    container = secret_container()
    assert count_secret_pixels(container.image) == 40 * 30
    out = container.finalized_image('PIL')
    assert count_secret_pixels(out) == 0
    out.close()
    container.close()


@pytest.mark.parametrize('degrees', [90, 180, 270])
def test_redaction_still_covers_secret_after_rotate(degrees):
    container = secret_container()
    journal = PageOpsJournal()
    journal.record(('rotate', {0: degrees}))
    images = [container]
    journal.apply_to_images(images, {})

    # secret pixels moved with the bitmap …
    assert count_secret_pixels(container.image) == 40 * 30
    # … and the remapped redaction still covers every one of them
    out = container.finalized_image('PIL')
    assert count_secret_pixels(out) == 0, \
        f'secret pixels exposed after {degrees} degree rotation'
    out.close()
    container.close()


def test_redaction_still_covers_secret_after_crop_inside():
    container = secret_container()
    journal = PageOpsJournal()
    journal.record(('crop', 0, [50, 150, 300, 400]))  # secret fully inside
    journal.apply_to_images([container], {})

    assert container.image.size == (250, 250)
    assert count_secret_pixels(container.image) == 40 * 30
    out = container.finalized_image('PIL')
    assert count_secret_pixels(out) == 0, 'secret pixels exposed after crop'
    out.close()
    container.close()


def test_redaction_still_covers_secret_after_partial_crop():
    # The crop cuts through the redact box; the surviving part of the secret
    # block must stay covered (redact keeps its page intersection).
    container = secret_container()
    journal = PageOpsJournal()
    journal.record(('crop', 0, [120, 150, 300, 400]))
    journal.apply_to_images([container], {})

    remaining = (SECRET_BOX[2] - 120) * (SECRET_BOX[3] - SECRET_BOX[1])
    assert count_secret_pixels(container.image) == remaining
    out = container.finalized_image('PIL')
    assert count_secret_pixels(out) == 0, \
        'secret pixels exposed after partial crop'
    out.close()
    container.close()


def test_rotate_remaps_ink_points_and_text_pos():
    container = secret_container()
    journal = PageOpsJournal()
    journal.record(('rotate', {0: 90}))
    journal.apply_to_images([container], {})

    by_kind = {ann.kind: ann for ann in container.annotations}
    # 90 CW on a 400x600 page: (x, y) -> (599 - y, x)
    assert by_kind['ink'].props['points'] == [[394, 110], [374, 130]]
    assert by_kind['text'].props['pos'] == [394, 110]
    container.close()


def test_crop_translates_ink_points_and_text_pos():
    container = secret_container()
    journal = PageOpsJournal()
    journal.record(('crop', 0, [50, 150, 300, 400]))
    journal.apply_to_images([container], {})

    by_kind = {ann.kind: ann for ann in container.annotations}
    assert by_kind['ink'].props['points'] == [[60, 55], [80, 75]]
    assert by_kind['text'].props['pos'] == [60, 55]
    container.close()


def test_four_quarter_rotations_are_identity():
    container = secret_container()
    original_props = [dict(ann.props) for ann in container.annotations]
    journal = PageOpsJournal()
    for _ in range(4):
        journal.record(('rotate', {0: 90}))
    journal.apply_to_images([container], {})

    assert container.image.size == PAGE_PX
    assert (container.height_in_pt, container.width_in_pt) == PAGE_SIZE
    for ann, props in zip(container.annotations, original_props):
        assert ann.props == props
    out = container.finalized_image('PIL')
    assert count_secret_pixels(out) == 0
    out.close()
    container.close()


# ---------------------------------------------------------------------------
# Crop size-swap regression (legacy inverted container size convention)
# ---------------------------------------------------------------------------

def test_crop_container_size_uses_legacy_convention():
    """Cropped containers must store size[0]=WIDTH (in height_in_pt), the
    convention every other page uses on the export path
    (handlers/file.py: add_page(format=(height_in_pt, width_in_pt)))."""
    container = ImageContainer(Image.new('RGB', PAGE_PX, 'white'), PAGE_SIZE)
    # sanity: a fresh page already follows the legacy convention
    assert container.height_in_pt == PAGE_SIZE[0]   # WIDTH in pt
    assert container.width_in_pt == PAGE_SIZE[1]    # HEIGHT in pt

    journal = PageOpsJournal()
    journal.record(('crop', 0, [50, 100, 350, 250]))  # 300 x 150 px
    journal.apply_to_images([container], {})

    crop_w_pt = 300 * PT_PER_PX
    crop_h_pt = 150 * PT_PER_PX
    # The export path builds format=(height_in_pt, width_in_pt): fpdf must
    # receive (crop_w_pt, crop_h_pt), exactly like non-cropped pages.
    export_format = (container.height_in_pt, container.width_in_pt)
    assert export_format == pytest.approx((crop_w_pt, crop_h_pt))
    assert container.size == pytest.approx((crop_w_pt, crop_h_pt))
    # aspect ratio of the exported page matches the crop's pixel aspect
    assert export_format[0] / export_format[1] == pytest.approx(300 / 150)
    assert container.image.size == (300, 150)
    container.close()


def test_rotate_container_size_keeps_legacy_convention():
    container = ImageContainer(Image.new('RGB', PAGE_PX, 'white'), PAGE_SIZE)
    journal = PageOpsJournal()
    journal.record(('rotate', {0: 90}))
    journal.apply_to_images([container], {})

    # rotated 90: width and height swap in both worlds
    assert container.image.size == (PAGE_PX[1], PAGE_PX[0])
    assert (container.height_in_pt, container.width_in_pt) == \
        (PAGE_SIZE[1], PAGE_SIZE[0])
    assert container.size == (PAGE_SIZE[1], PAGE_SIZE[0])
    container.close()
