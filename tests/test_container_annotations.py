"""End-to-end tests for the typed-annotation ImageContainer export pipeline.

Builds ImageContainers from PIL images, adds one annotation of every kind
plus document decorations (incl. Bates), runs finalize_pages_chunked both
in-process (serial executor) and through the real ProcessPoolExecutor, and
asserts the produced PDF pages contain the expected pixels.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import base64
import io
from concurrent.futures import Future

import pypdfium2 as pdfium
import pytest
from fpdf import FPDF
from PIL import Image

import fixtures  # noqa: F401  (sys.path side effect)

from workonward_read import annotations as an
from workonward_read import image_container as ic
from workonward_read.annotations import ANNOTATION_KINDS
from workonward_read.image_container import (
    ImageContainer,
    delete_all_annotations,
    export_annotations,
    finalize_pages_chunked,
)

WHITE = (255, 255, 255)
PAGE_PX = (800, 1000)                     # original-image px at 200 PPI
PAGE_PT = (288.0, 360.0)                  # px * 72 / 200 (width_pt, height_pt)

DECORATIONS = {
    'watermark': {'text': 'CONFIDENTIAL', 'opacity': 0.5},
    'header_footer': {'left': 'ACME', 'center': '', 'right': '{date}',
                      'position': 'header', 'size_px': 28},
    'page_numbers': {'template': '{page} / {total}', 'start_at': 1,
                     'position': 'footer-center'},
    'bates': {'prefix': 'AB', 'start': 100, 'digits': 6,
              'position': 'footer-right'},
}


def red_png_b64(size=(20, 20)):
    img = Image.new('RGBA', size, (255, 0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode('ascii')


def all_kind_props():
    """(kind, props) for every one of the 13 annotation kinds."""
    png = red_png_b64()
    return [
        ('redact', {'p1': [20, 20], 'p2': [120, 80], 'fill': 'black'}),
        ('text', {'pos': [50, 100], 'text': 'Héllo Ünïcode', 'size_px': 40,
                  'color': 'black', 'bold': True}),
        ('highlight', {'p1': [200, 200], 'p2': [400, 260],
                       'color': '#ffff00', 'alpha': 0.4}),
        ('underline', {'p1': [100, 490], 'p2': [300, 520],
                       'color': 'red', 'width_px': 4}),
        ('strike', {'p1': [100, 540], 'p2': [300, 560],
                    'color': 'blue', 'width_px': 4}),
        ('ink', {'points': [[400, 600], [450, 650], [500, 600]],
                 'color': 'blue', 'width_px': 6}),
        ('rect', {'p1': [600, 100], 'p2': [700, 200], 'outline': 'black',
                  'fill': None, 'width_px': 3}),
        ('ellipse', {'p1': [600, 300], 'p2': [700, 380], 'outline': 'black',
                     'fill': '#00ff00', 'width_px': 2}),
        ('line', {'p1': [100, 750], 'p2': [300, 750], 'color': 'black',
                  'width_px': 3}),
        ('arrow', {'p1': [100, 800], 'p2': [300, 800], 'color': 'black',
                   'width_px': 3}),
        ('stamp', {'pos': [100, 300], 'preset': 'approved', 'text': '',
                   'color': '', 'angle': 0, 'scale': 1.0}),
        ('image', {'pos': [500, 800], 'png_b64': png, 'scale': 2.0}),
        ('signature', {'pos': [550, 900], 'png_b64': png, 'scale': 1.0}),
    ]


def build_containers():
    """3 white pages; page 0 carries one annotation of every kind, page 2 a
    single redaction. Page 1 stays empty (decorations only)."""
    containers = [
        ImageContainer(Image.new('RGB', PAGE_PX, WHITE), PAGE_PT)
        for _ in range(3)
    ]
    kinds_seen = set()
    for kind, props in all_kind_props():
        containers[0].add_annotation(kind, props)
        kinds_seen.add(kind)
    assert kinds_seen == set(ANNOTATION_KINDS)
    containers[2].add_annotation(
        'redact', {'p1': [40, 40], 'p2': [140, 120], 'fill': 'black'})
    return containers


def build_pdf(tmp_path, results, name):
    out_pdf = FPDF(unit='pt')
    for img_bytes, page_size in results:
        out_pdf.add_page(format=page_size)
        out_pdf.image(img_bytes, x=0, y=0, w=out_pdf.w)
    path = str(tmp_path / name)
    out_pdf.output(path)
    return path


def render_pdf_pages(path):
    """Render the output PDF back to PIL images at the original 200 PPI."""
    pdf = pdfium.PdfDocument(path)
    try:
        pages = []
        for i in range(len(pdf)):
            page = pdf[i]
            pil = page.render(scale=200 / 72).to_pil().convert('RGB')
            pages.append(pil)
            page.close()
        return pages
    finally:
        pdf.close()


def region_has_nonwhite(img, x_range, y_range, tolerance=25):
    for x in range(*x_range):
        for y in range(*y_range):
            r, g, b = img.getpixel((x, y))
            if (255 - r) + (255 - g) + (255 - b) > 3 * tolerance:
                return True
    return False


def assert_burned_pages(pages):
    """Pixel assertions shared by every export path."""
    assert len(pages) == 3
    p0 = pages[0]
    assert p0.size == PAGE_PX

    # Redaction burned black
    r, g, b = p0.getpixel((70, 50))
    assert r < 60 and g < 60 and b < 60, f'redact not black: {(r, g, b)}'

    # Highlight blends yellowish over white (blue channel drops)
    r, g, b = p0.getpixel((300, 230))
    assert r >= 225 and g >= 225, f'highlight rgb: {(r, g, b)}'
    assert 110 <= b <= 200, f'highlight blue channel: {b}'

    # Text bbox contains non-white pixels
    assert region_has_nonwhite(p0, (50, 350, 3), (95, 165, 2)), \
        'text pixels missing'

    # Watermark tint near the page center
    cx, cy = PAGE_PX[0] // 2, PAGE_PX[1] // 2
    assert region_has_nonwhite(p0, (cx - 120, cx + 120, 3),
                               (cy - 120, cy + 120, 3), tolerance=8), \
        'watermark tint missing'

    # Bates text area non-white on EVERY page (footer-right)
    w, h = PAGE_PX
    for idx, page in enumerate(pages):
        assert region_has_nonwhite(page, (w // 2, w - 8, 2),
                                   (h - 70, h - 8, 2)), \
            f'bates missing on page {idx}'

    # Page 2 redaction burned too
    r, g, b = pages[2].getpixel((90, 80))
    assert r < 60 and g < 60 and b < 60

    # Page 1 (decorations only) keeps a white body outside decoration areas
    r, g, b = pages[1].getpixel((60, 700))
    assert r > 200 and g > 200 and b > 200


# ---------------------------------------------------------------------------
# In-process (serial executor) path
# ---------------------------------------------------------------------------

class _SerialExecutor:
    """Drop-in ProcessPoolExecutor replacement that runs submissions inline
    in the current process (still returning real Futures for wait())."""

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def submit(self, fn, *args, **kwargs):
        future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as error:  # pragma: no cover - defensive
            future.set_exception(error)
        return future


def test_finalize_chunked_in_process_burns_all_kinds(tmp_path, monkeypatch):
    monkeypatch.setattr(ic, 'ProcessPoolExecutor', _SerialExecutor)
    containers = build_containers()

    progress = []
    results = list(finalize_pages_chunked(
        containers, img_format='JPEG', quality=92, scale=1, chunk_size=2,
        progress_callback=lambda done, total: progress.append((done, total)),
        decorations=DECORATIONS))

    assert len(results) == 3
    assert progress[-1] == (3.0, 3)
    # source images untouched
    assert containers[0].image.getpixel((70, 50)) == WHITE

    pdf_path = build_pdf(tmp_path, results, 'serial.pdf')
    assert_burned_pages(render_pdf_pages(pdf_path))


# ---------------------------------------------------------------------------
# Real ProcessPoolExecutor path
# ---------------------------------------------------------------------------

def test_finalize_chunked_process_pool_burns_all_kinds(tmp_path):
    containers = build_containers()

    results = list(finalize_pages_chunked(
        containers, img_format='JPEG', quality=92, scale=1, chunk_size=50,
        decorations=DECORATIONS))

    assert len(results) == 3
    pdf_path = build_pdf(tmp_path, results, 'pooled.pdf')
    assert_burned_pages(render_pdf_pages(pdf_path))


def test_finalize_worker_args_are_picklable():
    import pickle
    containers = build_containers()
    page = containers[0]
    ann_dicts = [an.to_dict(a) for a in page.annotations]
    args = (0, b'... image bytes ...', ann_dicts, 'JPEG', 92, 1,
            (page.height_in_pt, page.width_in_pt), DECORATIONS, 3,
            ic.default_font_dir())
    pickle.dumps(args)  # must not raise


# ---------------------------------------------------------------------------
# finalized_image (single-page export path)
# ---------------------------------------------------------------------------

def test_finalized_image_burns_annotations_and_decorations():
    containers = build_containers()
    out = containers[0].finalized_image(
        'PIL', decorations=DECORATIONS, page_idx=0, total_pages=3)
    assert out.mode == 'RGB'
    assert out.getpixel((70, 50)) == (0, 0, 0)
    r, g, b = out.getpixel((300, 230))
    assert r >= 250 and g >= 250 and 140 <= b <= 170
    # bates present
    w, h = PAGE_PX
    assert region_has_nonwhite(out, (w // 2, w - 8, 2), (h - 70, h - 8, 2))
    # original untouched
    assert containers[0].image.getpixel((70, 50)) == WHITE
    out.close()


def test_finalized_image_jpeg_bytes():
    containers = build_containers()
    data = containers[2].finalized_image(
        'JPEG', image_quality=92, scale=1,
        decorations=DECORATIONS, page_idx=2, total_pages=3)
    assert isinstance(data, bytes)
    img = Image.open(io.BytesIO(data))
    img.load()
    r, g, b = img.getpixel((90, 80))
    assert r < 60 and g < 60 and b < 60
    img.close()


def test_display_data_scales_decorations_with_zoom():
    """At 50% zoom the preview footer text is ~half the 100% preview's
    height (and stays horizontally centered) — the decoration metrics are
    rendered at the display zoom, not at original-px sizes."""
    decorations = {'page_numbers': {'template': 'PAGE {page} OF {total}',
                                    'start_at': 1,
                                    'position': 'footer-center',
                                    'size_px': 48}}

    def dark_bbox(img):
        mask = img.convert('L').point(lambda v: 255 if v < 128 else 0)
        return mask.getbbox()

    old_zoom = ImageContainer.zoom_factor
    try:
        container = ImageContainer(Image.new('RGB', PAGE_PX, WHITE), PAGE_PT)
        ImageContainer.zoom_factor = 100
        container.scale_image()
        full = Image.open(io.BytesIO(container.display_data(
            decorations, 0, 1)))
        full.load()

        ImageContainer.zoom_factor = 50
        container.scale_image()
        half = Image.open(io.BytesIO(container.display_data(
            decorations, 0, 1)))
        half.load()
    finally:
        ImageContainer.zoom_factor = old_zoom

    full_box, half_box = dark_bbox(full), dark_bbox(half)
    assert full_box and half_box
    full_h = full_box[3] - full_box[1]
    half_h = half_box[3] - half_box[1]
    assert abs(half_h - full_h / 2.0) <= max(2.0, 0.15 * full_h / 2.0)

    full_center = (full_box[0] + full_box[2]) / 2.0 / full.width
    half_center = (half_box[0] + half_box[2]) / 2.0 / half.width
    assert abs(full_center - 0.5) < 0.02
    assert abs(half_center - 0.5) < 0.02
    full.close()
    half.close()


def test_display_data_burns_decorations_without_touching_original():
    container = build_containers()[1]
    data = container.display_data(DECORATIONS, 1, 3)
    preview = Image.open(io.BytesIO(data))
    preview.load()
    cx, cy = preview.width // 2, preview.height // 2
    assert region_has_nonwhite(preview.convert('RGB'),
                               (cx - 120, cx + 120, 3),
                               (cy - 120, cy + 120, 3), tolerance=8)
    preview.close()
    # original image data unchanged
    assert container.image.getpixel((PAGE_PX[0] // 2, PAGE_PX[1] // 2)) == WHITE
    # empty decorations short-circuit to the plain scaled image
    assert container.display_data({}, 1, 3) == container.data()


# ---------------------------------------------------------------------------
# export / delete helpers
# ---------------------------------------------------------------------------

def test_export_annotations_and_delete_all():
    containers = build_containers()
    exported = export_annotations(containers)
    assert exported is not None
    assert len(exported) == 3
    assert [d['kind'] for d in exported[0]] == list(dict(all_kind_props()))
    assert exported[1] == []
    assert all(isinstance(d, dict) and 'id' in d for d in exported[0])

    deleted = {'called': False}
    assert delete_all_annotations(containers,
                                  lambda: deleted.__setitem__('called', True))
    assert deleted['called']
    assert all(page.annotations == [] for page in containers)
    assert export_annotations(containers) is None
    assert export_annotations([]) is None


def test_finalize_empty_pages_raises():
    with pytest.raises(ValueError):
        list(finalize_pages_chunked([]))
