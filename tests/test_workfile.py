"""Tests for workfile v2 persistence (save/load/migrate/prune).

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import json
import os

import fixtures

from workonward_read.utils import encode_filepath
from workonward_read.workfile import WorkfileManager


class FakePage:
    """Minimal stand-in for ImageContainer: only .rectangles is needed."""

    def __init__(self, rectangles=None):
        self.rectangles = list(rectangles) if rectangles else []


def make_manager(tmp_path, history_length=30):
    datadir = tmp_path / 'data'
    datadir.mkdir(exist_ok=True)
    return WorkfileManager(str(datadir), history_length), datadir


def test_v2_round_trip(tmp_path):
    doc = fixtures.make_pdf(tmp_path / 'doc.pdf', pages=2)
    manager, datadir = make_manager(tmp_path)
    manager.set_file_path(doc)

    pages = [
        FakePage([((10, 20), (30, 40), 'black', 7),
                  ((5, 6), (7, 8), 'white', 8)]),
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
    assert loaded['rectangles'][0] == [
        [(10, 20), (30, 40), 'black', None],
        [(5, 6), (7, 8), 'white', None],
    ]
    assert loaded['rectangles'][1] == []


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
    # Stale graph ids are dropped, coordinates come back as tuples
    assert loaded['rectangles'][0] == [[(1, 2), (3, 4), 'black', None]]
    assert loaded['rectangles'][1] == [[(9, 9), (20, 30), 'white', None]]
    assert loaded['pages'] == 2
    assert loaded['current_page'] == 1
    assert loaded['fill_color'] == 'white'
    assert loaded['output_quality'] == 'high'


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
    pages = [FakePage(loaded['rectangles'][0])]
    manager.save(pages, 0, 'black', 'high')

    with open(workfile, encoding='utf-8') as fh:
        raw = json.load(fh)
    assert raw['version'] == 2
    assert 'rectangles' not in raw
    assert raw['annotations'][0][0]['props']['p1'] == [1, 1]


def test_save_without_rectangles_deletes_workfile(tmp_path):
    manager, datadir = make_manager(tmp_path)
    file_path = str(tmp_path / 'doc.pdf')
    manager.set_file_path(file_path)

    manager.save([FakePage([((0, 0), (1, 1), 'black', None)])], 0, 'black', 'high')
    workfile = datadir / encode_filepath(file_path)
    assert workfile.is_file()

    # No rectangles on any page -> workfile removed
    manager.save([FakePage(), FakePage()], 0, 'black', 'high')
    assert not workfile.exists()

    # No images at all -> workfile removed too
    manager.save([FakePage([((0, 0), (1, 1), 'black', None)])], 0, 'black', 'high')
    manager.save([], 0, 'black', 'high')
    assert not workfile.exists()


def test_pruning_keeps_newest(tmp_path):
    manager, datadir = make_manager(tmp_path, history_length=3)
    for i in range(7):
        manager.set_file_path(str(tmp_path / f'doc{i}.pdf'))
        manager.save([FakePage([((0, 0), (1, 1), 'black', None)])], 0, 'black', 'high')

    files = os.listdir(datadir)
    assert len(files) == 3
    # The most recently saved workfile survives pruning
    assert encode_filepath(str(tmp_path / 'doc6.pdf')) in files


def test_load_missing_or_broken_returns_none(tmp_path):
    manager, datadir = make_manager(tmp_path)
    assert manager.load() is None  # no file_path set

    manager.set_file_path(str(tmp_path / 'nothing.pdf'))
    assert manager.load() is None  # no workfile on disk

    broken = datadir / encode_filepath(str(tmp_path / 'nothing.pdf'))
    broken.write_text('{not json', encoding='utf-8')
    assert manager.load() is None
