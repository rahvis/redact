"""
Integration tests for the Organize Pages group: handler core functions,
journal-vs-images consistency, merge/split/extract, compression request
validation, batch runs and document-properties round trips.

All tests are headless: they exercise the module-level core functions in
workonward_read.handlers.organize (never an sg.Window).

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import os

import pytest
import fixtures
from pypdf import PdfReader

from workonward_read import page_render, pdf_ops
from workonward_read.annotations import UndoStack
from workonward_read.dialogs.organize import (PAGE_SIZES_PT, margins_to_box,
                                              parse_split_ranges,
                                              validate_compress_request)
from workonward_read.handlers import organize as org
from workonward_read.state import AppState

PX_PER_PT = 200.0 / 72.0


def load_state(tmp_path, pages=4, size=(300, 400)):
    """AppState with a real PDF loaded through page_render (200 PPI)."""
    path = fixtures.make_pdf(tmp_path / 'src.pdf', pages=pages, size=size)
    state = AppState()
    state.file_path = path
    state.images = page_render.render_pdf_pages(path, range(pages))
    return state


def effective_display_size(page):
    """(width_pt, height_pt) of a pypdf page as displayed (rotation applied)."""
    rotation = page.rotation % 360
    width = float(page.mediabox.width)
    height = float(page.mediabox.height)
    if rotation in (90, 270):
        return (height, width)
    return (width, height)


# ---------------------------------------------------------------------------
# page_render factories
# ---------------------------------------------------------------------------

def test_make_blank_dimensions():
    container = page_render.make_blank(300, 400)
    try:
        assert container.image.size == (round(300 * PX_PER_PT),
                                        round(400 * PX_PER_PT))
        assert container.image.getpixel((5, 5)) == (255, 255, 255)
        assert container.size == (300.0, 400.0)
    finally:
        container.close()
    with pytest.raises(ValueError):
        page_render.make_blank(0, 100)


def test_render_pdf_pages_matches_import_ppi(tmp_path):
    path = fixtures.make_pdf(tmp_path / 'r.pdf', pages=3, size=(300, 400))
    containers = page_render.render_pdf_pages(path, [2, 0])
    try:
        assert len(containers) == 2
        for container in containers:
            assert container.image.width == pytest.approx(300 * PX_PER_PT, abs=2)
            assert container.image.height == pytest.approx(400 * PX_PER_PT, abs=2)
    finally:
        for container in containers:
            container.close()
    with pytest.raises(ValueError):
        page_render.render_pdf_pages(path, [3])
    with pytest.raises(FileNotFoundError):
        page_render.render_pdf_pages(str(tmp_path / 'missing.pdf'), [0])


def test_render_pdf_pages_encrypted(tmp_path):
    path = fixtures.make_encrypted_pdf(tmp_path / 'enc.pdf',
                                       user_password='pw', pages=1)
    containers = page_render.render_pdf_pages(path, [0], password='pw')
    assert len(containers) == 1
    containers[0].close()


# ---------------------------------------------------------------------------
# Journal-vs-images consistency for a scripted handler-core op sequence
# ---------------------------------------------------------------------------

def test_scripted_ops_keep_journal_and_images_consistent(tmp_path):
    state = load_state(tmp_path, pages=4, size=(300, 400))
    aux = fixtures.make_pdf(tmp_path / 'aux.pdf', pages=2, size=(250, 350))

    # 1. delete page 2 (1-based) -> [P0, P2, P3]
    org.delete_pages_core(state, {'scope': 'ranges', 'spec': '2'})
    assert len(state.images) == 3

    # 2. rotate the current page (index 0) by 90 degrees
    assert state.current_page == 0
    org.rotate_pages_core(state, {'degrees': 90, 'scope': 'current'})

    # 3. insert an A4 blank at position 1 -> [P0r, B, P2, P3]
    org.insert_pages_core(state, {'mode': 'blank', 'position': 1, 'size': 'A4'})

    # 4. insert page 1 of another PDF at position 2 -> [P0r, B, A0, P2, P3]
    org.insert_pages_core(state, {'mode': 'pdf', 'position': 2,
                                  'path': aux, 'pages': '1'})
    assert len(state.images) == 5

    # 5. crop the last page by pixel margins
    state.current_page = 4
    org.crop_pages_core(state, {'scope': 'current', 'unit': 'px',
                                'margins': (10, 20, 30, 40)})

    # 6. move the blank page (position 2, 1-based) to the end
    org.reorder_pages_core(state, {'src': 2, 'dst': 5})
    assert state.current_page == 4  # follows the moved page

    # Replay the journal losslessly on the original file.
    out = str(tmp_path / 'organized.pdf')
    pdf_ops.apply_journal(state.file_path, state.journal, out,
                          state.source_password)
    pages = PdfReader(out).pages

    # Page count matches in both worlds.
    assert len(pages) == len(state.images) == 5

    # Per-page: the PDF's effective display size (points) must match the
    # in-memory image pixels at 200 PPI.
    for i, (page, container) in enumerate(zip(pages, state.images)):
        eff_w, eff_h = effective_display_size(page)
        assert container.image.width == pytest.approx(
            eff_w * PX_PER_PT, abs=2), f'page {i} width'
        assert container.image.height == pytest.approx(
            eff_h * PX_PER_PT, abs=2), f'page {i} height'

    # Rotation survives the replay: final page 0 is the rotated P0.
    assert pages[0].rotation % 360 == 90
    assert all(pages[i].rotation % 360 == 0 for i in range(1, 5))

    # The moved blank page ended up last, at A4 size.
    eff_w, eff_h = effective_display_size(pages[4])
    assert eff_w == pytest.approx(PAGE_SIZES_PT['A4'][0], abs=2e-3)
    assert eff_h == pytest.approx(PAGE_SIZES_PT['A4'][1], abs=2e-3)

    # The cropped page kept its cropped size in points.
    cropped = state.images[3]  # P3 (after the move) is at index 3
    eff_w, eff_h = effective_display_size(pages[3])
    assert cropped.image.width == pytest.approx(eff_w * PX_PER_PT, abs=2)
    assert cropped.image.height == pytest.approx(eff_h * PX_PER_PT, abs=2)

    # The journal serializes (workfile compatibility).
    restored = pdf_ops.PageOpsJournal.from_dict(state.journal.to_dict())
    assert restored.ops == state.journal.ops


def test_delete_refuses_to_remove_all_pages(tmp_path):
    state = load_state(tmp_path, pages=2, size=(200, 300))
    with pytest.raises(ValueError):
        org.delete_pages_core(state, {'scope': 'all'})
    assert len(state.images) == 2
    assert state.journal is None or state.journal.is_empty()


def test_delete_clamps_current_page(tmp_path):
    state = load_state(tmp_path, pages=3, size=(200, 300))
    state.current_page = 2
    org.delete_pages_core(state, {'scope': 'current'})
    assert len(state.images) == 2
    assert state.current_page == 1


def test_insert_blank_current_size_matches_current_page(tmp_path):
    state = load_state(tmp_path, pages=2, size=(300, 400))
    org.insert_pages_core(state, {'mode': 'blank', 'position': 0,
                                  'size': 'current'})
    inserted = state.images[0]
    original = state.images[1]
    assert inserted.image.width == pytest.approx(original.image.width, abs=1)
    assert inserted.image.height == pytest.approx(original.image.height, abs=1)


def test_rotate_all_and_bad_degrees(tmp_path):
    state = load_state(tmp_path, pages=2, size=(300, 400))
    widths = [c.image.width for c in state.images]
    heights = [c.image.height for c in state.images]
    org.rotate_pages_core(state, {'degrees': 270, 'scope': 'all'})
    for container, w, h in zip(state.images, widths, heights):
        assert container.image.size == (h, w)
    with pytest.raises(ValueError):
        org.rotate_pages_core(state, {'degrees': 45, 'scope': 'all'})


def test_reorder_validates_positions(tmp_path):
    state = load_state(tmp_path, pages=2, size=(200, 300))
    with pytest.raises(ValueError):
        org.reorder_pages_core(state, {'src': 0, 'dst': 1})
    with pytest.raises(ValueError):
        org.reorder_pages_core(state, {'src': 1, 'dst': 3})
    # No-op move records nothing
    org.reorder_pages_core(state, {'src': 1, 'dst': 1})
    assert state.journal is None or state.journal.is_empty()


def test_crop_all_pages_validates_before_recording(tmp_path):
    state = load_state(tmp_path, pages=2, size=(300, 400))
    # Margins that consume the whole page must fail without recording ops.
    with pytest.raises(ValueError):
        org.crop_pages_core(state, {'scope': 'all', 'unit': 'px',
                                    'margins': (10000, 0, 10000, 0)})
    assert state.journal is None or state.journal.is_empty()
    org.crop_pages_core(state, {'scope': 'all', 'unit': 'pt',
                                'margins': (18, 18, 18, 18)})
    assert len(state.journal.ops) == 2
    for container in state.images:
        assert container.image.width == pytest.approx(
            (300 - 36) * PX_PER_PT, abs=3)


# ---------------------------------------------------------------------------
# Undo-stack remapping through page ops (state.undo keyed by page index)
# ---------------------------------------------------------------------------

def _seeded_stacks(state, *indices):
    """Give the pages at ``indices`` a distinguishable UndoStack each."""
    stacks = {}
    for idx in indices:
        stack = UndoStack()
        stack.push([])
        state.undo[idx] = stacks[idx] = stack
    return stacks


def test_undo_stacks_shift_down_on_delete(tmp_path):
    state = load_state(tmp_path, pages=3)
    stacks = _seeded_stacks(state, 0, 1)

    org.delete_pages_core(state, {'scope': 'ranges', 'spec': '1'})  # page 0

    # page 0's stack is gone; old page 1's stack now serves new page 0
    assert set(state.undo) == {0}
    assert state.undo[0] is stacks[1]


def test_undo_stacks_cleared_on_rotate_and_crop(tmp_path):
    state = load_state(tmp_path, pages=2)
    stacks = _seeded_stacks(state, 0, 1)

    state.current_page = 0
    org.rotate_pages_core(state, {'degrees': 90, 'scope': 'current'})
    # rotated page's snapshots hold pre-transform coords -> cleared
    assert set(state.undo) == {1}
    assert state.undo[1] is stacks[1]

    state.current_page = 1
    org.crop_pages_core(state, {'scope': 'current', 'unit': 'px',
                                'margins': (10, 10, 10, 10)})
    assert state.undo == {}


def test_undo_stacks_reorder_on_move(tmp_path):
    state = load_state(tmp_path, pages=3)
    stacks = _seeded_stacks(state, 0, 1, 2)

    org.reorder_pages_core(state, {'src': 1, 'dst': 3})  # page 0 -> position 2

    assert state.undo[2] is stacks[0]
    assert state.undo[0] is stacks[1]
    assert state.undo[1] is stacks[2]


def test_undo_stacks_shift_up_on_insert(tmp_path):
    state = load_state(tmp_path, pages=2)
    stacks = _seeded_stacks(state, 0, 1)

    org.insert_pages_core(state, {'mode': 'blank', 'position': 0,
                                  'size': 'A4'})

    assert set(state.undo) == {1, 2}
    assert state.undo[1] is stacks[0]
    assert state.undo[2] is stacks[1]


# ---------------------------------------------------------------------------
# Merge / split / extract cores (file -> file, no loaded document)
# ---------------------------------------------------------------------------

def test_merge_core(tmp_path):
    a = fixtures.make_pdf(tmp_path / 'a.pdf', pages=2)
    b = fixtures.make_pdf(tmp_path / 'b.pdf', pages=3)
    out = str(tmp_path / 'merged.pdf')
    output, total = org.merge_core({'inputs': [a, b], 'output': out})
    assert output == out
    assert total == 5
    assert len(PdfReader(out).pages) == 5


def test_split_core(tmp_path):
    src = fixtures.make_pdf(tmp_path / 'src.pdf', pages=5)
    pattern = str(tmp_path / 'part_{n}.pdf')
    outputs = org.split_core({'input': src, 'ranges': '1-2, 3, 4-',
                              'output_pattern': pattern})
    assert outputs == [str(tmp_path / f'part_{n}.pdf') for n in (1, 2, 3)]
    assert [len(PdfReader(path).pages) for path in outputs] == [2, 1, 2]
    with pytest.raises(ValueError):
        org.split_core({'input': src, 'ranges': '1-9',
                        'output_pattern': pattern})


def test_extract_core_including_encrypted(tmp_path):
    src = fixtures.make_pdf(tmp_path / 'src.pdf', pages=4)
    out = str(tmp_path / 'extract.pdf')
    output, count = org.extract_core({'input': src, 'pages': '2,4',
                                      'output': out})
    assert output == out and count == 2
    assert len(PdfReader(out).pages) == 2

    enc = fixtures.make_encrypted_pdf(tmp_path / 'enc.pdf',
                                      user_password='pw', pages=3)
    out2 = str(tmp_path / 'extract2.pdf')
    org.extract_core({'input': enc, 'pages': '1', 'output': out2},
                     password='pw')
    assert len(PdfReader(out2).pages) == 1
    with pytest.raises(ValueError):
        org.extract_core({'input': enc, 'pages': '1', 'output': out2},
                         password='wrong')


def test_parse_split_ranges():
    assert parse_split_ranges('1-2,3,4-', 5) == [(0, 1), (2, 2), (3, 4)]
    assert parse_split_ranges('2', 3) == [(1, 1)]
    with pytest.raises(ValueError):
        parse_split_ranges('0-2', 5)
    with pytest.raises(ValueError):
        parse_split_ranges('', 5)
    with pytest.raises(ValueError):
        parse_split_ranges('a-b', 5)


# ---------------------------------------------------------------------------
# Crop margin helper
# ---------------------------------------------------------------------------

def test_margins_to_box():
    assert margins_to_box((10, 20, 30, 40), 'px', 800, 600) == [10, 20, 770, 560]
    box = margins_to_box((36, 0, 0, 0), 'pt', 800, 600)
    assert box[0] == pytest.approx(36 * PX_PER_PT)
    with pytest.raises(ValueError):
        margins_to_box((-1, 0, 0, 0), 'px', 800, 600)
    with pytest.raises(ValueError):
        margins_to_box((500, 0, 500, 0), 'px', 800, 600)
    with pytest.raises(ValueError):
        margins_to_box((0, 0, 0, 0), 'inch', 800, 600)
    with pytest.raises(ValueError):
        margins_to_box(('x', 0, 0, 0), 'px', 800, 600)


# ---------------------------------------------------------------------------
# Compression: request validation and core run
# ---------------------------------------------------------------------------

def test_validate_compress_request(tmp_path):
    src = fixtures.make_pdf(tmp_path / 'c.pdf', pages=1)
    good = {'mode': 'raster', 'input': src,
            'output': str(tmp_path / 'out.pdf'), 'dpi': 110, 'quality': 85}
    normalized = validate_compress_request(good)
    assert normalized['dpi'] == 110 and normalized['quality'] == 85

    lossless = validate_compress_request(
        {'mode': 'lossless', 'input': src,
         'output': str(tmp_path / 'out2.pdf'), 'quality': 60})
    assert 'dpi' not in lossless

    for bad in (
        {**good, 'mode': 'zip'},
        {**good, 'input': ''},
        {**good, 'input': str(tmp_path / 'missing.pdf')},
        {**good, 'output': ''},
        {**good, 'output': src},                 # output == input
        {**good, 'dpi': 60},                     # below 72
        {**good, 'dpi': 300},                    # above 200
        {**good, 'quality': 0},
        {**good, 'quality': 'high'},
    ):
        with pytest.raises(ValueError):
            validate_compress_request(bad)


def test_compress_core_raster_reports_sizes(tmp_path):
    src = fixtures.make_pdf(tmp_path / 'c.pdf', pages=2, size=(300, 400))
    out = str(tmp_path / 'compressed.pdf')
    reported = []
    result = org.compress_core(
        {'mode': 'raster', 'input': src, 'output': out, 'dpi': 72,
         'quality': 50},
        progress_cb=lambda pct, msg: reported.append(pct))
    assert result['output'] == out
    assert os.path.isfile(out)
    assert result['before_bytes'] == os.path.getsize(src)
    assert result['after_bytes'] == os.path.getsize(out)
    assert len(PdfReader(out).pages) == 2
    assert reported  # per-page progress arrived


# ---------------------------------------------------------------------------
# Batch runner core
# ---------------------------------------------------------------------------

def test_batch_core_pdf_to_text_with_failure(tmp_path):
    folder = tmp_path / 'in'
    folder.mkdir()
    fixtures.make_text_pdf(folder / 'one.pdf', text='alpha bravo')
    fixtures.make_text_pdf(folder / 'two.pdf', text='charlie delta')
    (folder / 'broken.pdf').write_text('this is not a pdf')

    out_dir = tmp_path / 'out'
    progress = []
    results = org.batch_core(
        {'folder': str(folder), 'tool': 'pdf_text', 'out_dir': str(out_dir)},
        progress_cb=lambda pct, msg: progress.append((pct, msg)))

    assert len(results) == 3
    failures = [r for r in results if r[2]]
    assert len(failures) == 1
    assert os.path.basename(failures[0][0]) == 'broken.pdf'
    ok = [r for r in results if r[2] is None]
    assert len(ok) == 2
    for _input, output, _error in ok:
        assert output.endswith('.txt') and os.path.isfile(output)
    assert len(progress) == 3  # one report per file

    with pytest.raises(ValueError):
        org.batch_core({'folder': str(folder), 'tool': 'nope',
                        'out_dir': str(out_dir)})
    with pytest.raises(ValueError):
        org.batch_core({'folder': str(tmp_path / 'nowhere'),
                        'tool': 'pdf_text', 'out_dir': str(out_dir)})


def test_collect_batch_inputs_only_pdfs(tmp_path):
    fixtures.make_pdf(tmp_path / 'b.pdf', pages=1)
    fixtures.make_pdf(tmp_path / 'a.pdf', pages=1)
    (tmp_path / 'notes.txt').write_text('x')
    inputs = org.collect_batch_inputs(str(tmp_path))
    assert [os.path.basename(p) for p in inputs] == ['a.pdf', 'b.pdf']


# ---------------------------------------------------------------------------
# Document properties round trip
# ---------------------------------------------------------------------------

def test_properties_roundtrip_save_as(tmp_path):
    src = fixtures.make_pdf(tmp_path / 'p.pdf', pages=1)
    out = str(tmp_path / 'meta.pdf')
    metadata = {'title': 'Ünïcode Title', 'author': 'A. Author',
                'subject': 'Testing', 'keywords': 'k1, k2'}
    org.apply_properties_core(src, out, metadata)
    props = pdf_ops.read_properties(out)
    assert props['title'] == 'Ünïcode Title'
    assert props['author'] == 'A. Author'
    assert props['subject'] == 'Testing'
    assert props['keywords'] == 'k1, k2'
    assert props['pages'] == 1


def test_properties_overwrite_in_place(tmp_path):
    src = fixtures.make_pdf(tmp_path / 'p.pdf', pages=2)
    org.apply_properties_core(src, src, {'title': 'First'})
    assert pdf_ops.read_properties(src)['title'] == 'First'
    org.apply_properties_core(src, src, {'title': 'Second', 'author': 'Me'})
    props = pdf_ops.read_properties(src)
    assert props['title'] == 'Second'
    assert props['author'] == 'Me'
    assert props['pages'] == 2
    assert not os.path.exists(src + '.workonward_tmp.pdf')
