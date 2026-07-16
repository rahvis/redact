"""
Typed annotation engine for WorkOnward Read.

Pure, picklable annotation model shared by the canvas (live preview via
``render_on_graph``) and the export pipeline (burn-in via ``render_on_image``,
which runs inside ProcessPoolExecutor workers and is the export ground truth).

All coordinates are in ORIGINAL-image pixel space at 200 PPI, y-down.
This module never imports FreeSimpleGUI or tkinter; ``render_on_graph`` only
calls draw methods on the graph element object it is handed.

WorkOnward Read is free software licensed under GPL-3.0.
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import base64
import io
import math
import os
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache

from PIL import Image, ImageColor, ImageDraw, ImageFont

# All annotation kinds supported by the engine.
ANNOTATION_KINDS = (
    'redact', 'text', 'highlight', 'underline', 'strike', 'ink',
    'rect', 'ellipse', 'line', 'arrow', 'stamp', 'image', 'signature',
)

# Preset stamp text and colors. 'custom' uses props text/color instead.
STAMP_PRESETS = {
    'approved': ('APPROVED', '#1b7d2c'),
    'draft': ('DRAFT', '#808080'),
    'confidential': ('CONFIDENTIAL', '#c62828'),
}

_STAMP_BASE_SIZE = 48          # px font size for stamps at scale 1.0
_DECOR_MARGIN = 24             # px top/bottom (and side) margin for decorations
_DEFAULT_DECOR_SIZE = 24       # px default font size for decorations
_DEFAULT_WATERMARK_OPACITY = 0.15
_DEFAULT_WATERMARK_ANGLE = 45.0
_WATERMARK_WIDTH_FRACTION = 0.7
_DEFAULT_HIGHLIGHT_COLOR = '#ffff00'
_DEFAULT_HIGHLIGHT_ALPHA = 0.4
_UNDO_DEPTH = 25


@dataclass
class Annotation:
    """A single page annotation.

    Attributes:
        id: Stable unique id (uuid4 hex).
        kind: One of ANNOTATION_KINDS.
        props: Kind-specific properties, all coords in original-image px.
        graph_ids: Transient FreeSimpleGUI/tk figure ids; never serialized.
    """

    id: str
    kind: str
    props: dict
    graph_ids: list = field(default_factory=list)


def new_id() -> str:
    """Return a fresh unique annotation id."""
    return uuid.uuid4().hex


def to_dict(ann: Annotation) -> dict:
    """Serialize an Annotation to a plain dict (graph_ids are dropped)."""
    return {'id': ann.id, 'kind': ann.kind, 'props': _deep_copy(ann.props)}


def from_dict(d: dict) -> Annotation:
    """Deserialize a plain dict into an Annotation with empty graph_ids."""
    return Annotation(
        id=str(d.get('id') or new_id()),
        kind=str(d.get('kind', '')),
        props=_deep_copy(d.get('props') or {}),
        graph_ids=[],
    )


def migrate_v1_rectangle(t) -> Annotation:
    """Migrate a v1 rectangle tuple ``(p1, p2, color, graph_id)`` to a redact
    Annotation. The transient graph_id is discarded."""
    p1, p2, color = t[0], t[1], t[2]
    return Annotation(
        id=new_id(),
        kind='redact',
        props={'p1': [p1[0], p1[1]], 'p2': [p2[0], p2[1]], 'fill': color},
        graph_ids=[],
    )


class UndoStack:
    """Snapshot-based undo/redo stack (bounded depth, default 25).

    Callers push a snapshot of the annotation list BEFORE mutating it.
    ``undo``/``redo`` accept the optional current annotation list so the
    opposite stack can record it; without it, redo after undo restores the
    last undone snapshot only.
    """

    def __init__(self, maxlen: int = _UNDO_DEPTH):
        self._undo: deque = deque(maxlen=maxlen)
        self._redo: deque = deque(maxlen=maxlen)

    @staticmethod
    def _snapshot(annotations) -> list:
        snap = []
        for a in annotations:
            snap.append(from_dict(a) if isinstance(a, dict) else from_dict(to_dict(a)))
        return snap

    def push(self, annotations) -> None:
        """Record a deep-copy snapshot of ``annotations`` and clear redo."""
        self._undo.append(self._snapshot(annotations))
        self._redo.clear()

    def undo(self, current=None):
        """Pop and return the most recent snapshot, or None if empty.

        If ``current`` (the live annotation list) is given it is pushed onto
        the redo stack so ``redo`` can restore it later.
        """
        if not self._undo:
            return None
        if current is not None:
            self._redo.append(self._snapshot(current))
        return self._undo.pop()

    def redo(self, current=None):
        """Pop and return the most recently undone state, or None if empty.

        If ``current`` is given it is pushed back onto the undo stack.
        """
        if not self._redo:
            return None
        if current is not None:
            self._undo.append(self._snapshot(current))
        return self._redo.pop()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)


# ---------------------------------------------------------------------------
# Burn-in rendering (export ground truth; pure PIL + stdlib, picklable args)
# ---------------------------------------------------------------------------

def render_on_image(pil_image, ann_dicts, decorations, page_idx, total_pages,
                    page_w_pt, page_h_pt, font_dir):
    """Burn annotations and document decorations into a page image.

    Pure function safe for ProcessPoolExecutor workers: every argument is
    picklable and only PIL + stdlib are used. Alpha kinds (highlight,
    watermark opacity) are composited on RGBA overlays via
    ``Image.alpha_composite``; the result is returned as a new RGB image.
    The input image is never mutated.

    Args:
        pil_image: PIL image of the page (any mode).
        ann_dicts: Iterable of annotation dicts (see ``to_dict``); Annotation
            instances are tolerated.
        decorations: Document-level decorations dict (watermark,
            header_footer, page_numbers, bates) or None.
        page_idx: 0-based page index.
        total_pages: Total page count of the document.
        page_w_pt: Page width in PDF points (accepted per contract).
        page_h_pt: Page height in PDF points (accepted per contract).
        font_dir: Folder containing DejaVuSans.ttf / DejaVuSans-Bold.ttf.

    Returns:
        A new PIL.Image in RGB mode.
    """
    base = pil_image.convert('RGBA')
    for entry in ann_dicts or []:
        d = entry if isinstance(entry, dict) else to_dict(entry)
        base = _render_annotation(base, str(d.get('kind', '')),
                                  d.get('props') or {}, font_dir)
    base = _render_decorations(base, decorations or {}, page_idx,
                               int(total_pages), font_dir)
    rgb = base.convert('RGB')
    base.close()
    return rgb


def _render_annotation(base, kind, props, font_dir):
    """Render one annotation onto the RGBA base; returns the (possibly new)
    base image. Raises ValueError for unknown kinds."""
    draw = ImageDraw.Draw(base)

    if kind == 'redact':
        draw.rectangle(_norm_box(props['p1'], props['p2']),
                       fill=props.get('fill', 'black'))

    elif kind == 'highlight':
        alpha = float(props.get('alpha', _DEFAULT_HIGHLIGHT_ALPHA))
        color = _rgba(props.get('color', _DEFAULT_HIGHLIGHT_COLOR),
                      int(round(alpha * 255)))
        overlay = Image.new('RGBA', base.size, (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        odraw.rectangle(_norm_box(props['p1'], props['p2']), fill=color)
        composed = Image.alpha_composite(base, overlay)
        overlay.close()
        base.close()
        base = composed

    elif kind in ('underline', 'strike'):
        x0, y0, x1, y1 = _norm_box(props['p1'], props['p2'])
        y = y1 if kind == 'underline' else (y0 + y1) / 2.0
        draw.line([(x0, y), (x1, y)], fill=props.get('color', 'black'),
                  width=max(1, int(props.get('width_px', 2))))

    elif kind == 'ink':
        points = [tuple(p) for p in props.get('points', [])]
        width = max(1, int(props.get('width_px', 2)))
        color = props.get('color', 'black')
        if len(points) >= 2:
            draw.line(points, fill=color, width=width, joint='curve')
        elif len(points) == 1:
            x, y = points[0]
            r = max(1.0, width / 2.0)
            draw.ellipse([x - r, y - r, x + r, y + r], fill=color)

    elif kind in ('rect', 'ellipse'):
        box = _norm_box(props['p1'], props['p2'])
        kwargs = {
            'outline': props.get('outline', 'black'),
            'fill': props.get('fill'),
            'width': max(1, int(props.get('width_px', 2))),
        }
        if kind == 'rect':
            draw.rectangle(box, **kwargs)
        else:
            draw.ellipse(box, **kwargs)

    elif kind in ('line', 'arrow'):
        p1 = tuple(props['p1'])
        p2 = tuple(props['p2'])
        width = max(1, int(props.get('width_px', 2)))
        color = props.get('color', 'black')
        draw.line([p1, p2], fill=color, width=width)
        if kind == 'arrow':
            draw.polygon(_arrow_head(p1, p2, width), fill=color)

    elif kind == 'text':
        font = _load_font(font_dir, props.get('size_px', 24),
                          bool(props.get('bold', False)))
        draw.text(tuple(props['pos']), str(props.get('text', '')),
                  font=font, fill=props.get('color', 'black'))

    elif kind == 'stamp':
        layer = _build_stamp_layer(props, font_dir)
        x, y = props['pos']
        _paste_rgba(base, layer, x, y)
        layer.close()

    elif kind in ('image', 'signature'):
        img = _decode_png(props.get('png_b64', ''))
        if img is not None:
            scale = float(props.get('scale', 1.0))
            if scale != 1.0:
                scaled = img.resize(
                    (max(1, int(img.width * scale)),
                     max(1, int(img.height * scale))),
                    resample=Image.Resampling.LANCZOS)
                img.close()
                img = scaled
            x, y = props['pos']
            _paste_rgba(base, img, x, y)
            img.close()

    else:
        raise ValueError('Unknown annotation kind: %r' % (kind,))

    return base


def _render_decorations(base, decorations, page_idx, total_pages, font_dir):
    """Render document-level decorations onto the RGBA base; returns base."""
    bates_str = format_bates(decorations.get('bates'), page_idx)

    watermark = decorations.get('watermark')
    if watermark:
        base = _render_watermark(base, watermark, font_dir)

    header_footer = decorations.get('header_footer')
    if header_footer:
        size = int(header_footer.get('size_px', _DEFAULT_DECOR_SIZE))
        font = _load_font(font_dir, size)
        color = header_footer.get('color', 'black')
        band, _slot = _parse_position(header_footer.get('position'),
                                      default_band='header')
        for slot in ('left', 'center', 'right'):
            text = substitute_template(str(header_footer.get(slot) or ''),
                                       page_idx + 1, total_pages, bates_str)
            if text:
                _draw_positioned_text(base, text, band, slot, font, color)

    page_numbers = decorations.get('page_numbers')
    if page_numbers:
        template = str(page_numbers.get('template') or '{page} / {total}')
        start_at = int(page_numbers.get('start_at', 1))
        text = substitute_template(template, start_at + page_idx,
                                   total_pages, bates_str)
        band, slot = _parse_position(page_numbers.get('position'),
                                     default_band='footer',
                                     default_slot='center')
        font = _load_font(font_dir,
                          int(page_numbers.get('size_px', _DEFAULT_DECOR_SIZE)))
        _draw_positioned_text(base, text, band, slot, font,
                              page_numbers.get('color', 'black'))

    bates = decorations.get('bates')
    if bates and bates_str:
        band, slot = _parse_position(bates.get('position'),
                                     default_band='footer',
                                     default_slot='right')
        font = _load_font(font_dir,
                          int(bates.get('size_px', _DEFAULT_DECOR_SIZE)))
        _draw_positioned_text(base, bates_str, band, slot, font,
                              bates.get('color', 'black'))

    return base


def _render_watermark(base, cfg, font_dir):
    """Composite a diagonal centered watermark (text or PNG) onto base."""
    opacity = float(cfg.get('opacity', _DEFAULT_WATERMARK_OPACITY))
    angle = float(cfg.get('angle', _DEFAULT_WATERMARK_ANGLE))
    scale = float(cfg.get('scale', 1.0))
    target_w = max(1.0, base.width * _WATERMARK_WIDTH_FRACTION * scale)

    if cfg.get('png_b64'):
        layer = _decode_png(cfg['png_b64'])
        if layer is None:
            return base
        factor = target_w / max(1, layer.width)
        resized = layer.resize(
            (max(1, int(layer.width * factor)),
             max(1, int(layer.height * factor))),
            resample=Image.Resampling.LANCZOS)
        layer.close()
        layer = resized
        alpha_band = layer.getchannel('A').point(
            lambda a: int(a * max(0.0, min(1.0, opacity))))
        layer.putalpha(alpha_band)
    else:
        text = str(cfg.get('text') or '')
        if not text:
            return base
        probe = _load_font(font_dir, 100, bold=True)
        probe_bbox = _text_bbox(text, probe)
        probe_w = max(1, probe_bbox[2] - probe_bbox[0])
        size = max(8, int(100.0 * target_w / probe_w))
        font = _load_font(font_dir, size, bold=True)
        bbox = _text_bbox(text, font)
        layer = Image.new('RGBA',
                          (max(1, bbox[2] - bbox[0]), max(1, bbox[3] - bbox[1])),
                          (0, 0, 0, 0))
        ldraw = ImageDraw.Draw(layer)
        fill = _rgba(cfg.get('color', '#808080'),
                     int(round(max(0.0, min(1.0, opacity)) * 255)))
        ldraw.text((-bbox[0], -bbox[1]), text, font=font, fill=fill)

    rotated = layer.rotate(angle, expand=True,
                           resample=Image.Resampling.BICUBIC)
    layer.close()
    x = (base.width - rotated.width) / 2.0
    y = (base.height - rotated.height) / 2.0
    _paste_rgba(base, rotated, x, y)
    rotated.close()
    return base


# ---------------------------------------------------------------------------
# Template helpers (public: usable by GUI for previews and by tests)
# ---------------------------------------------------------------------------

def substitute_template(template: str, page, total, bates: str = '') -> str:
    """Substitute ``{page} {total} {date} {bates}`` tokens in a template.

    ``{date}`` becomes today's ISO date. Unknown braces are left untouched.
    """
    return (template
            .replace('{page}', str(page))
            .replace('{total}', str(total))
            .replace('{date}', date.today().isoformat())
            .replace('{bates}', bates or ''))


def format_bates(cfg, page_idx: int) -> str:
    """Return the bates label for a page: ``{prefix}{number}`` where number is
    ``start + page_idx`` zero-padded to ``digits``. Empty string if cfg is
    falsy."""
    if not cfg:
        return ''
    number = int(cfg.get('start', 1)) + int(page_idx)
    digits = max(1, int(cfg.get('digits', 6)))
    return '%s%0*d' % (str(cfg.get('prefix', '')), digits, number)


# ---------------------------------------------------------------------------
# Hit testing
# ---------------------------------------------------------------------------

def hit_test(annotations, x, y):
    """Return the topmost annotation at (x, y) in original-image px, or None.

    Iterates in reverse (topmost first). Boxed kinds use point-in-bbox;
    line-like kinds use distance-to-segment with tolerance
    ``max(6, width_px)``; text/stamp/image use a bbox stored in
    ``props['_bbox']`` at render time or an estimate.
    """
    for ann in reversed(list(annotations)):
        kind, props = _kind_props(ann)

        if kind in ('redact', 'highlight', 'rect', 'ellipse'):
            if _in_box((x, y), _norm_box(props['p1'], props['p2'])):
                return ann

        elif kind in ('line', 'arrow', 'underline', 'strike'):
            tol = max(6.0, float(props.get('width_px', 1)))
            if _dist_to_segment((x, y), props['p1'], props['p2']) <= tol:
                return ann

        elif kind == 'ink':
            tol = max(6.0, float(props.get('width_px', 1)))
            points = props.get('points') or []
            if len(points) == 1:
                if math.dist((x, y), points[0]) <= tol:
                    return ann
            else:
                for a, b in zip(points, points[1:]):
                    if _dist_to_segment((x, y), a, b) <= tol:
                        return ann

        elif kind in ('text', 'stamp', 'image', 'signature'):
            bbox = props.get('_bbox') or estimate_bbox(kind, props)
            if bbox and _in_box((x, y), tuple(bbox)):
                return ann

    return None


def estimate_bbox(kind, props):
    """Estimate the axis-aligned bbox (x0, y0, x1, y1) of a positioned
    annotation (text / stamp / image / signature). Returns None when it
    cannot be estimated."""
    if kind == 'text':
        x, y = props['pos']
        size = float(props.get('size_px', 24))
        text = str(props.get('text', ''))
        w = 0.6 * size * max(1, len(text))
        return (x, y, x + w, y + 1.25 * size)

    if kind == 'stamp':
        x, y = props['pos']
        text, _color = _stamp_text_color(props)
        size = _STAMP_BASE_SIZE * float(props.get('scale', 1.0))
        w0 = 0.62 * size * max(1, len(text)) + size
        h0 = 1.6 * size
        rad = math.radians(float(props.get('angle', 0.0)))
        w = abs(w0 * math.cos(rad)) + abs(h0 * math.sin(rad))
        h = abs(w0 * math.sin(rad)) + abs(h0 * math.cos(rad))
        return (x, y, x + w, y + h)

    if kind in ('image', 'signature'):
        img = _decode_png(props.get('png_b64', ''))
        if img is None:
            return None
        w, h = img.size
        img.close()
        scale = float(props.get('scale', 1.0))
        x, y = props['pos']
        return (x, y, x + w * scale, y + h * scale)

    return None


# ---------------------------------------------------------------------------
# Live canvas preview (no FreeSimpleGUI import; duck-typed graph element)
# ---------------------------------------------------------------------------

def render_on_graph(graph_element, ann, zoom):
    """Draw an annotation preview on a FreeSimpleGUI Graph element.

    Only methods of the passed-in object are used (draw_rectangle, draw_line,
    draw_oval, draw_text, draw_image, draw_point, and
    ``graph_element.Widget.create_line/create_rectangle`` for arrow heads and
    stipple highlights) — this module never imports FreeSimpleGUI.

    Coordinates are scaled by ``zoom / 100`` and y is negated for the graph's
    y-up coordinate system (matching the existing rectangle model). Widget
    calls use raw canvas coordinates (y-down).

    Returns the list of created figure ids (also stored on ann.graph_ids).
    """
    factor = float(zoom) / 100.0
    kind, props = _kind_props(ann)
    ids = []

    def g(p):
        return (p[0] * factor, -p[1] * factor)

    def c(p):
        return (int(round(p[0] * factor)), int(round(p[1] * factor)))

    width = max(1, int(round(float(props.get('width_px', 2)) * factor)))

    if kind == 'redact':
        fill = props.get('fill', 'black')
        ids.append(graph_element.draw_rectangle(
            g(props['p1']), g(props['p2']),
            fill_color=fill, line_color=fill))

    elif kind == 'highlight':
        x0, y0 = c(props['p1'])
        x1, y1 = c(props['p2'])
        ids.append(graph_element.Widget.create_rectangle(
            x0, y0, x1, y1, stipple='gray50', outline='',
            fill=props.get('color', _DEFAULT_HIGHLIGHT_COLOR)))

    elif kind in ('underline', 'strike'):
        x0, y0, x1, y1 = _norm_box(props['p1'], props['p2'])
        y = y1 if kind == 'underline' else (y0 + y1) / 2.0
        ids.append(graph_element.draw_line(
            g((x0, y)), g((x1, y)),
            color=props.get('color', 'black'), width=width))

    elif kind == 'ink':
        points = props.get('points') or []
        color = props.get('color', 'black')
        if len(points) == 1:
            ids.append(graph_element.draw_point(
                g(points[0]), size=max(2, width), color=color))
        else:
            for a, b in zip(points, points[1:]):
                ids.append(graph_element.draw_line(
                    g(a), g(b), color=color, width=width))

    elif kind in ('rect', 'ellipse'):
        method = (graph_element.draw_rectangle if kind == 'rect'
                  else graph_element.draw_oval)
        ids.append(method(
            g(props['p1']), g(props['p2']),
            fill_color=props.get('fill'),
            line_color=props.get('outline', 'black'),
            line_width=width))

    elif kind == 'line':
        ids.append(graph_element.draw_line(
            g(props['p1']), g(props['p2']),
            color=props.get('color', 'black'), width=width))

    elif kind == 'arrow':
        x0, y0 = c(props['p1'])
        x1, y1 = c(props['p2'])
        ids.append(graph_element.Widget.create_line(
            x0, y0, x1, y1, arrow='last',
            fill=props.get('color', 'black'), width=width))

    elif kind == 'text':
        size = max(1, int(round(float(props.get('size_px', 24))
                                * factor * 0.75)))
        style = 'bold' if props.get('bold') else 'normal'
        ids.append(graph_element.draw_text(
            str(props.get('text', '')), g(props['pos']),
            color=props.get('color', 'black'),
            font=('DejaVu Sans', size, style), text_location='nw'))

    elif kind == 'stamp':
        text, color = _stamp_text_color(props)
        scale = float(props.get('scale', 1.0))
        size = max(1, int(round(_STAMP_BASE_SIZE * scale * factor * 0.75)))
        bbox = estimate_bbox('stamp', props)
        ids.append(graph_element.draw_rectangle(
            g((bbox[0], bbox[1])), g((bbox[2], bbox[3])),
            line_color=color, line_width=2))
        ids.append(graph_element.draw_text(
            text, g(props['pos']), color=color,
            font=('DejaVu Sans', size, 'bold'),
            angle=float(props.get('angle', 0.0)), text_location='nw'))

    elif kind in ('image', 'signature'):
        img = _decode_png(props.get('png_b64', ''))
        if img is not None:
            scale = float(props.get('scale', 1.0)) * factor
            if scale != 1.0:
                scaled = img.resize(
                    (max(1, int(img.width * scale)),
                     max(1, int(img.height * scale))),
                    resample=Image.Resampling.BILINEAR)
                img.close()
                img = scaled
            with io.BytesIO() as buf:
                img.save(buf, format='PNG')
                data = buf.getvalue()
            img.close()
            ids.append(graph_element.draw_image(
                data=data, location=g(props['pos'])))

    else:
        raise ValueError('Unknown annotation kind: %r' % (kind,))

    if not isinstance(ann, dict):
        ann.graph_ids = ids
    return ids


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _deep_copy(obj):
    """Deep-copy JSON-like structures (dict/list/tuple/scalars)."""
    if isinstance(obj, dict):
        return {k: _deep_copy(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_deep_copy(v) for v in obj]
    return obj


def _kind_props(ann):
    """Return (kind, props) for an Annotation or a plain dict."""
    if isinstance(ann, dict):
        return str(ann.get('kind', '')), ann.get('props') or {}
    return ann.kind, ann.props


def _norm_box(p1, p2):
    """Return a normalized (x0, y0, x1, y1) from two corner points."""
    return (min(p1[0], p2[0]), min(p1[1], p2[1]),
            max(p1[0], p2[0]), max(p1[1], p2[1]))


def _in_box(point, box):
    x, y = point
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def _dist_to_segment(point, a, b):
    """Distance from point to the segment a-b."""
    px, py = point
    ax, ay = a[0], a[1]
    bx, by = b[0], b[1]
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _arrow_head(p1, p2, width):
    """Return the 3 points of an arrow-head polygon at p2."""
    angle = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
    head = max(10.0, 4.0 * width)
    spread = math.radians(25)
    b1 = (p2[0] - head * math.cos(angle - spread),
          p2[1] - head * math.sin(angle - spread))
    b2 = (p2[0] - head * math.cos(angle + spread),
          p2[1] - head * math.sin(angle + spread))
    return [tuple(p2), b1, b2]


def _rgba(color, alpha=255):
    """Parse a color name/hex into an (r, g, b, a) tuple."""
    try:
        rgb = ImageColor.getrgb(color)
    except (ValueError, TypeError):
        rgb = (0, 0, 0)
    return (rgb[0], rgb[1], rgb[2], max(0, min(255, int(alpha))))


@lru_cache(maxsize=64)
def _truetype(path, size):
    try:
        return ImageFont.truetype(path, size)
    except (OSError, TypeError):
        return ImageFont.load_default()


def _load_font(font_dir, size_px, bold=False):
    """Load DejaVuSans[-Bold].ttf from font_dir at size_px (px)."""
    name = 'DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'
    return _truetype(os.path.join(font_dir or '', name),
                     max(1, int(round(float(size_px)))))


def _text_bbox(text, font):
    """Measure text bbox with a throwaway 1x1 drawing context."""
    tmp = Image.new('RGBA', (1, 1))
    bbox = ImageDraw.Draw(tmp).textbbox((0, 0), text, font=font)
    tmp.close()
    return bbox


def _decode_png(png_b64):
    """Decode a base64 PNG string into an RGBA PIL image, or None."""
    if not png_b64:
        return None
    try:
        raw = base64.b64decode(png_b64)
        img = Image.open(io.BytesIO(raw))
        img.load()
        return img.convert('RGBA')
    except Exception:
        return None


def _paste_rgba(base, layer, x, y):
    """Alpha-composite ``layer`` onto ``base`` at (x, y) in place, cropping
    the layer to the base bounds (handles negative / overflowing offsets)."""
    x, y = int(round(x)), int(round(y))
    src_x, src_y = max(0, -x), max(0, -y)
    x, y = max(0, x), max(0, y)
    if x >= base.width or y >= base.height:
        return
    crop_w = min(layer.width - src_x, base.width - x)
    crop_h = min(layer.height - src_y, base.height - y)
    if crop_w <= 0 or crop_h <= 0:
        return
    region = layer.crop((src_x, src_y, src_x + crop_w, src_y + crop_h))
    base.alpha_composite(region, (x, y))
    region.close()


def _stamp_text_color(props):
    """Resolve stamp text (uppercased) and color from preset or props."""
    preset = str(props.get('preset', 'custom'))
    if preset in STAMP_PRESETS:
        text, color = STAMP_PRESETS[preset]
        return text, color
    return str(props.get('text', '')).upper(), props.get('color', '#c62828')


def _build_stamp_layer(props, font_dir):
    """Build the rotated, bordered, uppercase stamp as an RGBA layer."""
    text, color = _stamp_text_color(props)
    scale = float(props.get('scale', 1.0))
    angle = float(props.get('angle', 0.0))
    size = max(8, int(round(_STAMP_BASE_SIZE * scale)))
    font = _load_font(font_dir, size, bold=True)
    bbox = _text_bbox(text or ' ', font)
    pad = max(6, size // 4)
    border = max(2, size // 12)
    w = (bbox[2] - bbox[0]) + 2 * (pad + border)
    h = (bbox[3] - bbox[1]) + 2 * (pad + border)
    layer = Image.new('RGBA', (max(1, w), max(1, h)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rectangle([border // 2, border // 2,
                    layer.width - 1 - border // 2,
                    layer.height - 1 - border // 2],
                   outline=color, width=border)
    draw.text((pad + border - bbox[0], pad + border - bbox[1]),
              text, font=font, fill=color)
    if angle:
        rotated = layer.rotate(angle, expand=True,
                               resample=Image.Resampling.BICUBIC)
        layer.close()
        layer = rotated
    return layer


def _parse_position(pos, default_band='footer', default_slot='center'):
    """Parse a decoration position like 'footer-right' / ('header', 'left')
    into (band, slot)."""
    band, slot = default_band, default_slot
    if isinstance(pos, (tuple, list)):
        tokens = [str(t) for t in pos]
    else:
        tokens = str(pos or '').replace('_', '-').replace(' ', '-').split('-')
    for token in tokens:
        token = token.lower()
        if token in ('header', 'footer'):
            band = token
        elif token in ('left', 'center', 'right'):
            slot = token
    return band, slot


def _draw_positioned_text(base, text, band, slot, font, color):
    """Draw text into a header/footer slot with 24 px margins."""
    draw = ImageDraw.Draw(base)
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    if slot == 'left':
        x = _DECOR_MARGIN
    elif slot == 'right':
        x = base.width - _DECOR_MARGIN - w
    else:
        x = (base.width - w) / 2.0
    y = _DECOR_MARGIN if band == 'header' else base.height - _DECOR_MARGIN - h
    draw.text((x - bbox[0], y - bbox[1]), text, font=font, fill=color)
