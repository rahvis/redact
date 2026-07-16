"""
Thumbnails sidebar for WorkOnward Read.

Provides the hidden-by-default sidebar column (:func:`build_sidebar_column`,
key ``'-THUMBS-'``) and :func:`refresh_thumbnails`, which renders 120
px-wide cached PNG thumbnails into ``sg.Image`` elements with tuple keys
``('-THUMB-', idx)`` plus page-number captions.

Event routing: ``main.py`` treats every 2-tuple event as a background-task
event, so thumbnail clicks are delivered as 3-tuple events
``('-THUMB-', idx, 'CLICK')`` (posted from a Tk ``<Button-1>`` binding via
``window.write_event_value``). ``main.py`` routes every tuple event whose
first element is ``'-THUMB-'`` to :func:`handle_thumb_event`, which flips
to the clicked page — no per-key handler registration.

Documents with more than :data:`MAX_THUMB_PAGES` pages skip thumbnail
generation (a popup notes it once per window).

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
(c) 2026 WorkOnward Read contributors
"""

import io
import weakref

import FreeSimpleGUI as sg
from PIL import Image

from workonward_read.i18n import _

THUMB_WIDTH = 120
MAX_THUMB_PAGES = 500
SIDEBAR_WIDTH = 170

# Per-window bookkeeping, keyed by the window object itself (weakly, so
# closed windows never leak entries): number of thumbnail rows created.
_ROW_COUNT = weakref.WeakKeyDictionary()
# Windows already notified about the >MAX_THUMB_PAGES skip.
_SKIP_NOTIFIED = weakref.WeakKeyDictionary()
# Last PNG bytes pushed into each window's sg.Image elements
# (window -> {idx: bytes}); lets refresh skip unchanged thumbnails.
_PUSHED = weakref.WeakKeyDictionary()


def build_sidebar_column():
    """Return the hidden-by-default thumbnails sidebar ``sg.Column``.

    The outer column has key ``'-THUMBS-'`` (toggle its visibility); the
    inner column ``'-THUMBS_INNER-'`` receives the thumbnail rows via
    ``window.extend_layout``.
    """
    inner = sg.Column(
        [[]],
        key='-THUMBS_INNER-',
        background_color='grey',
        pad=0,
    )
    return sg.Column(
        [[inner]],
        key='-THUMBS-',
        visible=False,
        scrollable=True,
        vertical_scroll_only=True,
        size=(SIDEBAR_WIDTH, 10000),
        expand_y=True,
        background_color='grey',
        pad=0,
        sbar_trough_color='lightgrey',
        sbar_background_color='darkgrey',
    )


def thumbnail_png(container, width=THUMB_WIDTH):
    """Return (cached) PNG bytes of a ``width`` px wide thumbnail of the
    container's image. The cache lives on the container and is invalidated
    when ``container.image_version`` (bumped on every image swap — rotate/
    crop go through ``pdf_ops._replace_container_image``) or the image size
    changes."""
    image = getattr(container, 'image', None)
    if image is None:
        return None
    cache_key = (getattr(container, 'image_version', 0), image.size, width)
    cached = getattr(container, '_thumb_cache', None)
    if cached is not None and cached[0] == cache_key:
        return cached[1]
    height = max(1, int(round(image.height * width / max(1, image.width))))
    thumb = image.resize((width, height), resample=Image.Resampling.BILINEAR)
    try:
        with io.BytesIO() as buffer:
            thumb.save(buffer, format='PNG')
            data = buffer.getvalue()
    finally:
        thumb.close()
    container._thumb_cache = (cache_key, data)
    return data


def handle_thumb_event(window, state, event):
    """Flip to the page of the clicked thumbnail. ``event`` is a tuple whose
    second element is the 0-based page index."""
    try:
        idx = int(event[1])
    except (TypeError, ValueError, IndexError):
        return
    if not state.images or not 0 <= idx < len(state.images):
        return
    from workonward_read.handlers.view import flip_to_page
    state.current_page = flip_to_page(window, state.images, idx, state)
    _update_captions(window, len(state.images), state.current_page)


def _update_captions(window, count, current_page):
    """Recolor the page-number captions so the current page stands out."""
    for idx in range(min(count, _ROW_COUNT.get(window, count))):
        try:
            window[('-THUMB_LABEL-', idx)].update(
                text_color='yellow' if idx == current_page else 'white')
        except Exception:
            pass


def _bind_click(window, idx):
    """Bind a Tk click on thumbnail ``idx`` that posts the 3-tuple event
    ('-THUMB-', idx, 'CLICK') (2-tuples are reserved for task events)."""
    def post(_tk_event, _i=idx):
        try:
            window.write_event_value(('-THUMB-', _i, 'CLICK'), None)
        except Exception:
            pass
    for key in (('-THUMB-', idx), ('-THUMB_LABEL-', idx)):
        try:
            window[key].Widget.bind('<Button-1>', post)
        except Exception:
            pass


def refresh_thumbnails(window, images, current_page=0, notify=None):
    """
    Render/refresh the sidebar thumbnails for ``images``.

    Existing ``('-THUMB-', idx)`` elements are updated in place — but ONLY
    when their page bitmap changed since the last refresh (tracked via the
    container thumbnail cache; captions are always recolored). Missing
    elements are appended to ``'-THUMBS_INNER-'`` via
    ``window.extend_layout`` (with click bindings); surplus ones from a
    previously larger document are hidden.

    Documents with more than :data:`MAX_THUMB_PAGES` pages skip generation;
    ``notify(window, message)`` (default: ``dialogs.common.info_popup``)
    reports it once per window.

    Returns:
        bool: True when thumbnails were (re)generated, False when skipped.
    """
    images = images or []
    if len(images) > MAX_THUMB_PAGES:
        if not _SKIP_NOTIFIED.get(window):
            _SKIP_NOTIFIED[window] = True
            if notify is None:
                from workonward_read.dialogs.common import info_popup
                notify = info_popup
            try:
                notify(window, _(
                    'Thumbnails are skipped for documents with more than '
                    '{max} pages.', max=MAX_THUMB_PAGES))
            except Exception:
                pass
        return False
    _SKIP_NOTIFIED.pop(window, None)

    existing = _ROW_COUNT.get(window, 0)
    pushed = _PUSHED.setdefault(window, {})
    new_rows = []
    for idx, container in enumerate(images):
        data = thumbnail_png(container)
        caption = _('Page {number}', number=idx + 1)
        color = 'yellow' if idx == current_page else 'white'
        if idx < existing:
            try:
                # thumbnail_png returns the SAME cached bytes object while a
                # page is unchanged: skip pushing identical image data.
                if pushed.get(idx) is not data:
                    window[('-THUMB-', idx)].update(data=data, visible=True)
                    pushed[idx] = data
                window[('-THUMB_LABEL-', idx)].update(
                    value=caption, visible=True, text_color=color)
            except Exception:
                pass
        else:
            new_rows.append([sg.Image(
                data=data, key=('-THUMB-', idx), pad=((16, 16), (8, 0)),
                background_color='grey')])
            new_rows.append([sg.Text(
                caption, key=('-THUMB_LABEL-', idx), pad=((16, 16), (0, 8)),
                text_color=color, background_color='grey')])
            pushed[idx] = data

    if new_rows:
        try:
            window.extend_layout(window['-THUMBS_INNER-'], new_rows)
        except Exception:
            pass
        for idx in range(existing, len(images)):
            _bind_click(window, idx)

    # Hide leftovers from a previously larger document (and forget their
    # pushed data so they are re-pushed when they become visible again).
    for idx in range(len(images), existing):
        try:
            window[('-THUMB-', idx)].update(visible=False)
            window[('-THUMB_LABEL-', idx)].update(visible=False)
        except Exception:
            pass
        pushed.pop(idx, None)

    _ROW_COUNT[window] = max(existing, len(images))

    # Let the scrollable column pick up the new content size.
    try:
        window.refresh()
        window['-THUMBS_INNER-'].contents_changed()
    except Exception:
        pass
    return True
