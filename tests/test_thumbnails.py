"""
Headless tests for the thumbnails sidebar: refresh logic with a FakeWindow
recording updates, PNG caching, the >500-page skip, click-event routing
(3-tuple events + per-key handler registration) and page flipping.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
(c) 2026 WorkOnward Read contributors
"""

import fixtures  # noqa: F401  (path setup / consistency with other suites)
from PIL import Image

from workonward_read import thumbnails
from workonward_read.state import AppState


# ---------------------------------------------------------------------------
# Fakes (no sg.Window is ever opened)
# ---------------------------------------------------------------------------

class FakeWidget:
    def __init__(self):
        self.bindings = {}

    def bind(self, sequence, fn):
        self.bindings[sequence] = fn

    def config(self, **kwargs):
        pass


class FakeElement:
    def __init__(self, key):
        self.key = key
        self.updates = []
        self.Widget = FakeWidget()

    def update(self, *args, **kwargs):
        self.updates.append((args, kwargs))

    # Graph API used by handlers.view.flip_to_page / load_image_to_graph
    def erase(self):
        pass

    def draw_image(self, data=None, location=None):
        return 42

    def delete_figure(self, figure_id):
        pass


class FakeWindow:
    def __init__(self):
        self.elements = {}
        self.extended = []   # (parent_key, rows)
        self.posted = []     # write_event_value calls

    def __getitem__(self, key):
        return self.elements.setdefault(key, FakeElement(key))

    def extend_layout(self, parent, rows):
        self.extended.append((parent.key, rows))

    def write_event_value(self, key, value):
        self.posted.append((key, value))

    def refresh(self):
        pass


class FakePage:
    """Duck-typed ImageContainer stand-in with a real PIL image."""

    def __init__(self, width=40, height=60, color='white'):
        self.image = Image.new('RGB', (width, height), color)
        self.scaled_image = self.image
        self.id = None
        self.annotations = []

    def refresh(self):
        return self

    def data(self):
        return b'fake-png'

    def draw_annotations_on_graph(self, window):
        pass


def _extended_keys(window):
    keys = []
    for _parent, rows in window.extended:
        for row in rows:
            for element in row:
                keys.append(element.Key)
    return keys


# ---------------------------------------------------------------------------
# build_sidebar_column
# ---------------------------------------------------------------------------

def test_build_sidebar_column_is_hidden_by_default():
    column = thumbnails.build_sidebar_column()
    assert column.Key == '-THUMBS-'
    assert column.visible is False
    inner = column.Rows[0][0]
    assert inner.Key == '-THUMBS_INNER-'


def test_main_layout_contains_the_sidebar():
    from workonward_read import ui
    icons = ui.create_icons(ui.get_fontpath())
    layout = ui.create_layout(icons)
    keys = {getattr(element, 'Key', None) for row in layout for element in row}
    assert '-THUMBS-' in keys
    assert '-GRAPH_COLUMN-' in keys


# ---------------------------------------------------------------------------
# refresh_thumbnails
# ---------------------------------------------------------------------------

def test_refresh_creates_rows_captions_and_bindings():
    window = FakeWindow()
    images = [FakePage(), FakePage(), FakePage()]
    assert thumbnails.refresh_thumbnails(window, images, current_page=1) is True

    keys = _extended_keys(window)
    for idx in range(3):
        assert ('-THUMB-', idx) in keys
        assert ('-THUMB_LABEL-', idx) in keys
        # a Tk click binding was installed on the image element
        assert '<Button-1>' in window[('-THUMB-', idx)].Widget.bindings

    # captions carry the 1-based page numbers
    labels = [element for _p, rows in window.extended for row in rows
              for element in row if element.Key == ('-THUMB_LABEL-', 1)]
    assert labels and labels[0].DisplayText == 'Page 2'


def test_refresh_registers_no_tuple_keys_in_handlers():
    """Thumbnail clicks route via main.py's '-THUMB-' tuple-event branch,
    never via per-key entries mutated into the merged handler registry
    (which would accumulate stale closures forever)."""
    from workonward_read import handlers as registry

    window = FakeWindow()
    thumbnails.refresh_thumbnails(window, [FakePage(), FakePage()])
    tuple_keys = [key for key in registry.HANDLERS if isinstance(key, tuple)]
    assert tuple_keys == []


def test_refresh_updates_existing_and_hides_surplus():
    window = FakeWindow()
    thumbnails.refresh_thumbnails(window, [FakePage(), FakePage(), FakePage()])
    window.extended.clear()

    assert thumbnails.refresh_thumbnails(
        window, [FakePage(), FakePage()], current_page=0) is True
    # No new rows: the first two were updated in place...
    assert window.extended == []
    for idx in range(2):
        args, kwargs = window[('-THUMB-', idx)].updates[-1]
        assert kwargs.get('visible') is True
        assert kwargs.get('data')  # PNG bytes
    # ...and the third was hidden.
    args, kwargs = window[('-THUMB-', 2)].updates[-1]
    assert kwargs.get('visible') is False
    args, kwargs = window[('-THUMB_LABEL-', 2)].updates[-1]
    assert kwargs.get('visible') is False


def test_thumbnail_png_cache_invalidates_on_image_swap():
    from workonward_read.pdf_ops import _replace_container_image

    page = FakePage(width=240, height=360)
    first = thumbnails.thumbnail_png(page)
    assert first is thumbnails.thumbnail_png(page)  # cached bytes reused

    # Page ops (rotate/crop) swap container.image via
    # pdf_ops._replace_container_image, which bumps image_version.
    _replace_container_image(page, page.image.transpose(
        Image.Transpose.ROTATE_270))
    assert page.image_version == 1
    second = thumbnails.thumbnail_png(page)
    assert second is not first

    thumb = Image.open(__import__('io').BytesIO(second))
    assert thumb.width == thumbnails.THUMB_WIDTH


def test_thumbnail_png_cache_keyed_on_version_not_object_identity():
    """A same-size in-place bitmap change is picked up once the version is
    bumped (id(image) reuse can no longer poison the cache)."""
    page = FakePage(width=100, height=100, color='white')
    first = thumbnails.thumbnail_png(page)
    # Same object, same size: cached bytes stay.
    assert thumbnails.thumbnail_png(page) is first
    # Bumping the version (what every image swap does) invalidates.
    page.image_version = getattr(page, 'image_version', 0) + 1
    assert thumbnails.thumbnail_png(page) is not first


def test_refresh_skips_documents_over_500_pages():
    window = FakeWindow()
    notes = []
    images = [object()] * (thumbnails.MAX_THUMB_PAGES + 1)

    result = thumbnails.refresh_thumbnails(
        window, images, notify=lambda win, message: notes.append(message))
    assert result is False
    assert window.extended == []
    assert len(notes) == 1

    # The note appears only once per window.
    thumbnails.refresh_thumbnails(
        window, images, notify=lambda win, message: notes.append(message))
    assert len(notes) == 1


# ---------------------------------------------------------------------------
# Click routing
# ---------------------------------------------------------------------------

def test_handle_thumb_event_flips_to_the_page():
    window = FakeWindow()
    state = AppState()
    state.images = [FakePage(), FakePage(), FakePage()]

    thumbnails.handle_thumb_event(window, state, ('-THUMB-', 2))
    assert state.current_page == 2
    args, kwargs = window['-PAGE_NUM-'].updates[-1]
    assert kwargs == {'value': 3}

    # Out-of-range clicks are ignored.
    thumbnails.handle_thumb_event(window, state, ('-THUMB-', 99))
    assert state.current_page == 2


def test_click_binding_posts_a_three_tuple_event():
    """2-tuple events are consumed by main.py's task handling, so thumbnail
    clicks must arrive as 3-tuples that fall through to HANDLERS."""
    window = FakeWindow()
    thumbnails.refresh_thumbnails(window, [FakePage(), FakePage()])

    binding = window[('-THUMB-', 1)].Widget.bindings['<Button-1>']
    binding(None)  # simulate the Tk click callback
    event, payload = window.posted[-1]
    assert event == ('-THUMB-', 1, 'CLICK')
    assert len(event) == 3  # must NOT look like a ('-TASK-', ...) event


def test_thumb_event_routes_like_main_would():
    """Replicates main.py's tuple-event prefix branch: tuple events whose
    first element is '-THUMB-' go to thumbnails.handle_thumb_event."""
    window = FakeWindow()
    state = AppState()
    state.images = [FakePage(), FakePage(), FakePage()]
    thumbnails.refresh_thumbnails(window, state.images)

    event = ('-THUMB-', 1, 'CLICK')
    # the exact routing condition main.py uses
    assert isinstance(event, tuple) and event and event[0] == '-THUMB-'
    thumbnails.handle_thumb_event(window, state, event)
    assert state.current_page == 1


def test_refresh_updates_only_invalidated_thumbnails():
    """After a single-page rotate, refresh pushes new image data to exactly
    that page's sg.Image (unchanged pages skip the update)."""
    from workonward_read.pdf_ops import _replace_container_image

    window = FakeWindow()
    images = [FakePage(40, 60), FakePage(40, 60), FakePage(40, 60)]
    thumbnails.refresh_thumbnails(window, images, current_page=0)

    baseline = {idx: len(window[('-THUMB-', idx)].updates) for idx in range(3)}

    # Rotate page 1 only (image swap bumps its version).
    _replace_container_image(images[1], images[1].image.transpose(
        Image.Transpose.ROTATE_270))
    thumbnails.refresh_thumbnails(window, images, current_page=0)

    counts = {idx: len(window[('-THUMB-', idx)].updates) - baseline[idx]
              for idx in range(3)}
    assert counts == {0: 0, 1: 1, 2: 0}
