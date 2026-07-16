"""Tests for workfile v2 persistence (save/load/migrate/prune).

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
(c) 2026 WorkOnward Read contributors
"""

import base64
import io
import json
import os

import fixtures
from PIL import Image

from workonward_read import annotations as an
from workonward_read.annotations import Annotation
from workonward_read.pdf_ops import PageOpsJournal
from workonward_read.utils import encode_filepath
from workonward_read.workfile import WorkfileManager, serialize_journal


class FakePage:
    """Minimal stand-in for ImageContainer: only .annotations is needed."""

    def __init__(self, annotations=None):
        self.annotations = list(annotations) if annotations else []


def make_ann(kind, **props):
    return Annotation(id=an.new_id(), kind=kind, props=props)


def redact(p1, p2, fill='black'):
    return make_ann('redact', p1=list(p1), p2=list(p2), fill=fill)


def tiny_png_b64():
    img = Image.new('RGBA', (4, 4), (255, 0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode('ascii')


def all_kind_annotations():
    png = tiny_png_b64()
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


def make_manager(tmp_path, history_length=30):
    datadir = tmp_path / 'data'
    datadir.mkdir(exist_ok=True)
    return WorkfileManager(str(datadir), history_length), datadir


def test_v2_round_trip(tmp_path):
    doc = fixtures.make_pdf(tmp_path / 'doc.pdf', pages=2)
    manager, datadir = make_manager(tmp_path)
    manager.set_file_path(doc)

    pages = [
        FakePage([redact((10, 20), (30, 40), 'black'),
                  redact((5, 6), (7, 8), 'white')]),
        FakePage(),
    ]
    manager.save(pages, 1, 'white', 'low')

    workfile = datadir / encode_filepath(doc)
    assert workfile.is_file()
    with open(workfile, encoding='utf-8') as fh:
        raw = json.load(fh)

    assert raw['version'] == 2
    assert raw['pages'] == 2
    assert raw['current_page'] == 1
    assert raw['fill_color'] == 'white'
    assert raw['output_quality'] == 'low'
    assert raw['decorations'] == {}
    assert raw['journal'] == []
    assert len(raw['annotations']) == 2
    assert raw['annotations'][1] == []

    first = raw['annotations'][0][0]
    assert first['kind'] == 'redact'
    assert isinstance(first['id'], str) and len(first['id']) == 32
    assert first['props'] == {'p1': [10, 20], 'p2': [30, 40], 'fill': 'black'}

    # Distinct annotation ids, no graph ids and no passwords persisted
    ids = [a['id'] for page in raw['annotations'] for a in page]
    assert len(ids) == len(set(ids))
    text = workfile.read_text(encoding='utf-8')
    assert 'password' not in text.lower()
    assert 'graph' not in text.lower()

    loaded = manager.load()
    assert loaded['pages'] == 2
    assert loaded['current_page'] == 1
    assert loaded['fill_color'] == 'white'
    assert loaded['output_quality'] == 'low'
    assert loaded['decorations'] == {}
    assert loaded['journal'] == []
    assert [a['props'] for a in loaded['annotations'][0]] == [
        {'p1': [10, 20], 'p2': [30, 40], 'fill': 'black'},
        {'p1': [5, 6], 'p2': [7, 8], 'fill': 'white'},
    ]
    assert [a['id'] for a in loaded['annotations'][0]] == \
        [pages[0].annotations[0].id, pages[0].annotations[1].id]
    assert loaded['annotations'][1] == []


def test_round_trip_every_kind_with_decorations_and_journal(tmp_path):
    manager, _datadir = make_manager(tmp_path)
    manager.set_file_path(str(tmp_path / 'full.pdf'))

    annotations = all_kind_annotations()
    pages = [FakePage(annotations), FakePage(), FakePage([redact((1, 1), (9, 9))])]

    decorations = {
        'watermark': {'text': 'CONFIDENTIAL', 'opacity': 0.3, 'angle': 45,
                      'scale': 1.0, 'color': 'gray'},
        'header_footer': {'left': 'ACME', 'center': '', 'right': '{date}',
                          'position': 'header', 'size_px': 24},
        'page_numbers': {'template': '{page} / {total}', 'start_at': 1,
                         'position': 'footer-center'},
        'bates': {'prefix': 'AB', 'start': 100, 'digits': 6,
                  'position': 'footer-right'},
    }
    journal = PageOpsJournal()
    journal.record(('rotate', {1: 90}))
    journal.record(('delete', [2]))
    journal_ops = serialize_journal(journal)

    manager.save(pages, 2, 'black', 'high',
                 decorations=decorations, journal=journal_ops)
    loaded = manager.load()

    assert loaded is not None
    assert loaded['decorations'] == decorations
    assert loaded['journal'] == [['rotate', {'1': 90}], ['delete', [2]]]

    # The journal survives a JSON round trip through PageOpsJournal
    restored = PageOpsJournal.from_dict({'ops': loaded['journal']})
    assert restored.ops == [('rotate', {1: 90}), ('delete', [2])]

    # Every annotation kind survives with identical id/kind/props
    assert len(loaded['annotations'][0]) == len(annotations)
    for original, entry in zip(annotations, loaded['annotations'][0]):
        assert entry['id'] == original.id
        assert entry['kind'] == original.kind
        assert entry['props'] == original.props
        rebuilt = an.from_dict(entry)
        assert rebuilt.kind == original.kind
        assert rebuilt.props == original.props
        assert rebuilt.graph_ids == []
    assert loaded['annotations'][1] == []
    assert len(loaded['annotations'][2]) == 1


def test_decorations_or_journal_alone_keep_workfile(tmp_path):
    manager, datadir = make_manager(tmp_path)
    file_path = str(tmp_path / 'deco.pdf')
    manager.set_file_path(file_path)

    manager.save([FakePage()], 0, 'black', 'high',
                 decorations={'watermark': {'text': 'X'}})
    workfile = datadir / encode_filepath(file_path)
    assert workfile.is_file()

    loaded = manager.load()
    assert loaded['annotations'] == [[]]
    assert loaded['decorations'] == {'watermark': {'text': 'X'}}

    # journal only
    manager.save([FakePage()], 0, 'black', 'high',
                 journal=[['rotate', {'0': 90}]])
    loaded = manager.load()
    assert loaded['journal'] == [['rotate', {'0': 90}]]


def test_v1_file_loads_and_migrates(tmp_path):
    manager, datadir = make_manager(tmp_path)
    file_path = str(tmp_path / 'ünïcode döc.pdf')  # unicode paths must hash fine
    manager.set_file_path(file_path)

    v1_data = {
        'rectangles': [[[[1, 2], [3, 4], 'black', 99]],
                       [[[9, 9], [20, 30], 'white', 100]]],
        'pages': 2,
        'current_page': 1,
        'fill_color': 'white',
        'output_quality': 'high',
    }
    with open(datadir / encode_filepath(file_path), 'w', encoding='utf-8') as fh:
        json.dump(v1_data, fh)

    loaded = manager.load()
    assert loaded is not None
    # Rectangles migrate to redact annotations; stale graph ids are dropped
    assert len(loaded['annotations']) == 2
    first = loaded['annotations'][0][0]
    assert first['kind'] == 'redact'
    assert first['props'] == {'p1': [1, 2], 'p2': [3, 4], 'fill': 'black'}
    assert first['id']
    second = loaded['annotations'][1][0]
    assert second['props'] == {'p1': [9, 9], 'p2': [20, 30], 'fill': 'white'}
    assert loaded['pages'] == 2
    assert loaded['current_page'] == 1
    assert loaded['fill_color'] == 'white'
    assert loaded['output_quality'] == 'high'
    assert loaded['decorations'] == {}
    assert loaded['journal'] == []


def test_v2_redact_only_file_still_loads(tmp_path):
    """Early v2 files (redact-only, no decorations/journal keys) load fine."""
    manager, datadir = make_manager(tmp_path)
    file_path = str(tmp_path / 'early-v2.pdf')
    manager.set_file_path(file_path)

    early_v2 = {
        'version': 2,
        'annotations': [[{'id': 'a' * 32, 'kind': 'redact',
                          'props': {'p1': [1, 1], 'p2': [5, 5],
                                    'fill': 'black'}}]],
        'pages': 1,
        'current_page': 0,
        'fill_color': 'black',
        'output_quality': 'high',
    }
    with open(datadir / encode_filepath(file_path), 'w', encoding='utf-8') as fh:
        json.dump(early_v2, fh)

    loaded = manager.load()
    assert loaded is not None
    assert loaded['annotations'][0][0]['kind'] == 'redact'
    assert loaded['annotations'][0][0]['id'] == 'a' * 32
    assert loaded['decorations'] == {}
    assert loaded['journal'] == []


def test_v1_then_save_produces_v2(tmp_path):
    manager, datadir = make_manager(tmp_path)
    file_path = str(tmp_path / 'old.pdf')
    manager.set_file_path(file_path)
    workfile = datadir / encode_filepath(file_path)

    with open(workfile, 'w', encoding='utf-8') as fh:
        json.dump({'rectangles': [[[[1, 1], [2, 2], 'black', 5]]],
                   'pages': 1, 'current_page': 0,
                   'fill_color': 'black', 'output_quality': 'high'}, fh)

    loaded = manager.load()
    pages = [FakePage([an.from_dict(entry) for entry in loaded['annotations'][0]])]
    manager.save(pages, 0, 'black', 'high')

    with open(workfile, encoding='utf-8') as fh:
        raw = json.load(fh)
    assert raw['version'] == 2
    assert 'rectangles' not in raw
    assert raw['annotations'][0][0]['props']['p1'] == [1, 1]


def test_save_without_annotations_deletes_workfile(tmp_path):
    manager, datadir = make_manager(tmp_path)
    file_path = str(tmp_path / 'doc.pdf')
    manager.set_file_path(file_path)

    manager.save([FakePage([redact((0, 0), (1, 1))])], 0, 'black', 'high')
    workfile = datadir / encode_filepath(file_path)
    assert workfile.is_file()

    # No annotations on any page (and nothing else to keep) -> workfile removed
    manager.save([FakePage(), FakePage()], 0, 'black', 'high')
    assert not workfile.exists()

    # No images at all -> workfile removed too
    manager.save([FakePage([redact((0, 0), (1, 1))])], 0, 'black', 'high')
    manager.save([], 0, 'black', 'high')
    assert not workfile.exists()


def test_pruning_keeps_newest(tmp_path):
    manager, datadir = make_manager(tmp_path, history_length=3)
    for i in range(7):
        manager.set_file_path(str(tmp_path / f'doc{i}.pdf'))
        manager.save([FakePage([redact((0, 0), (1, 1))])], 0, 'black', 'high')

    files = os.listdir(datadir)
    assert len(files) == 3
    # The most recently saved workfile survives pruning
    assert encode_filepath(str(tmp_path / 'doc6.pdf')) in files


def test_serialize_journal_forms():
    journal = PageOpsJournal()
    journal.record(('move', 0, 1))
    assert serialize_journal(journal) == [['move', 0, 1]]
    assert serialize_journal(None) == []
    assert serialize_journal([]) == []
    assert serialize_journal([['move', 0, 1]]) == [['move', 0, 1]]
    assert serialize_journal({'ops': [['move', 0, 1]]}) == [['move', 0, 1]]


def test_load_missing_or_broken_returns_none(tmp_path):
    manager, datadir = make_manager(tmp_path)
    assert manager.load() is None  # no file_path set

    manager.set_file_path(str(tmp_path / 'nothing.pdf'))
    assert manager.load() is None  # no workfile on disk

    broken = datadir / encode_filepath(str(tmp_path / 'nothing.pdf'))
    broken.write_text('{not json', encoding='utf-8')
    assert manager.load() is None
