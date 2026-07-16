"""
Pure pixel-space geometry helpers for page transforms.

Coordinates follow the application-wide convention (docs/dev-architecture.md):
ORIGINAL-image pixels at 200 PPI, y-down. Rotation is CLOCKWISE, mirroring
PDF ``/Rotate`` semantics and the PIL transposes used by
``pdf_ops.PageOpsJournal.apply_to_images`` — a point transformed with
:func:`rotate_point_cw` lands exactly on the pixel the PIL transpose moved it
to (pixel-index convention, hence the ``- 1`` terms).

The central entry point is :func:`transform_annotation`, which remaps every
geometry property of an annotation (``p1``/``p2`` corner pairs, ``pos``
anchors, ``points`` lists and the stamp ``angle``) through a page rotation or
crop so annotations stay glued to the page content the user placed them on.

Business module: never imports FreeSimpleGUI or tkinter and contains no
user-visible strings.

License: GPL-3.0
(c) 2026 CoverUP contributors
"""

from workonward_read import annotations as annotations_engine

# Kinds whose p1/p2 describe an axis-aligned box (corner order is free and is
# re-normalized after a transform).
BOXED_KINDS = ('redact', 'highlight', 'underline', 'strike', 'rect', 'ellipse')
# Kinds whose p1 -> p2 direction matters (arrow head sits at p2): the corner
# order is preserved.
SEGMENT_KINDS = ('line', 'arrow')
# Kinds positioned by a single anchor point.
POS_KINDS = ('text', 'stamp', 'image', 'signature')


def rotate_point_cw(pt, degrees, old_w, old_h):
    """Rotate a pixel point clockwise inside an ``old_w`` x ``old_h`` image.

    Args:
        pt: (x, y) in original-image px, y-down.
        degrees: 0/90/180/270 (multiples of 90, clockwise).
        old_w, old_h: Pixel size of the image BEFORE the rotation.

    Returns:
        [x', y'] — the position of the same pixel after the image is rotated
        clockwise (pixel-index convention, matching PIL transpose).
    """
    x, y = float(pt[0]), float(pt[1])
    degrees = int(degrees) % 360
    if degrees == 0:
        return [x, y]
    if degrees == 90:
        return [old_h - 1 - y, x]
    if degrees == 180:
        return [old_w - 1 - x, old_h - 1 - y]
    if degrees == 270:
        return [y, old_w - 1 - x]
    raise ValueError(f'Rotation must be a multiple of 90 degrees, got {degrees}.')


def translate_point(pt, dx, dy):
    """Return ``pt`` shifted by (dx, dy) as a fresh [x, y] list."""
    return [float(pt[0]) + dx, float(pt[1]) + dy]


def _kind_props(ann):
    """(kind, props) for an Annotation instance or a plain annotation dict."""
    if isinstance(ann, dict):
        return str(ann.get('kind', '')), ann.setdefault('props', {})
    return ann.kind, ann.props


def _norm_box(p1, p2):
    return (min(p1[0], p2[0]), min(p1[1], p2[1]),
            max(p1[0], p2[0]), max(p1[1], p2[1]))


def _boxes_intersect(a, b):
    """Inclusive intersection test for (x0, y0, x1, y1) boxes."""
    return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]


def _geometry_bbox(kind, props):
    """Axis-aligned bbox of an annotation's geometry, or None if unknown."""
    if kind in BOXED_KINDS or kind in SEGMENT_KINDS:
        return _norm_box(props['p1'], props['p2'])
    if kind == 'ink':
        points = props.get('points') or []
        if not points:
            return None
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return (min(xs), min(ys), max(xs), max(ys))
    if kind in POS_KINDS:
        bbox = annotations_engine.estimate_bbox(kind, props)
        if bbox is not None:
            return tuple(bbox)
        x, y = props['pos']
        return (x, y, x, y)
    return None


def _rotate_annotation(kind, props, degrees, old_w, old_h):
    """Rotate one annotation's geometry props in place."""
    if kind in BOXED_KINDS:
        p1 = rotate_point_cw(props['p1'], degrees, old_w, old_h)
        p2 = rotate_point_cw(props['p2'], degrees, old_w, old_h)
        x0, y0, x1, y1 = _norm_box(p1, p2)
        props['p1'] = [x0, y0]
        props['p2'] = [x1, y1]
    elif kind in SEGMENT_KINDS:
        props['p1'] = rotate_point_cw(props['p1'], degrees, old_w, old_h)
        props['p2'] = rotate_point_cw(props['p2'], degrees, old_w, old_h)
    elif kind == 'ink':
        props['points'] = [rotate_point_cw(p, degrees, old_w, old_h)
                           for p in props.get('points') or []]
    elif kind in POS_KINDS:
        props['pos'] = rotate_point_cw(props['pos'], degrees, old_w, old_h)
        if kind == 'stamp':
            # Stamp angles are counterclockwise-positive (PIL rotate); a
            # clockwise page rotation reduces the angle.
            props['angle'] = (float(props.get('angle', 0.0)) - degrees) % 360


def _crop_annotation(kind, props, box):
    """Crop one annotation's geometry props in place.

    Returns False when the annotation lies fully outside the crop box (the
    caller drops it), True otherwise.
    """
    x0, y0, x1, y1 = (float(v) for v in box)
    new_w, new_h = x1 - x0, y1 - y0
    bbox = _geometry_bbox(kind, props)
    if bbox is not None and not _boxes_intersect(bbox, (x0, y0, x1, y1)):
        return False

    if kind in BOXED_KINDS:
        bx0, by0, bx1, by1 = _norm_box(props['p1'], props['p2'])
        bx0, by0 = bx0 - x0, by0 - y0
        bx1, by1 = bx1 - x0, by1 - y0
        # Keep the full transformed box for 'redact', only clipped to the new
        # page bounds — the intersection with the page is exactly the region
        # that must stay covered, so redaction coverage is preserved. Other
        # boxed kinds are clamped the same way (partially-outside boxes).
        bx0, by0 = max(0.0, bx0), max(0.0, by0)
        bx1, by1 = min(new_w, bx1), min(new_h, by1)
        props['p1'] = [bx0, by0]
        props['p2'] = [bx1, by1]
    elif kind in SEGMENT_KINDS:
        props['p1'] = translate_point(props['p1'], -x0, -y0)
        props['p2'] = translate_point(props['p2'], -x0, -y0)
    elif kind == 'ink':
        props['points'] = [translate_point(p, -x0, -y0)
                           for p in props.get('points') or []]
    elif kind in POS_KINDS:
        props['pos'] = translate_point(props['pos'], -x0, -y0)
    return True


def transform_annotation(ann, op):
    """Remap an annotation's geometry through a page transform.

    Args:
        ann: :class:`workonward_read.annotations.Annotation` or a plain
            annotation dict (mutated in place either way).
        op: ``('rotate', degrees, old_w, old_h)`` — clockwise rotation of an
            ``old_w`` x ``old_h`` px page — or ``('crop', (x0, y0, x1, y1))``
            with the crop box in the page's pixel space.

    Returns:
        The same (mutated) annotation, or None when a crop leaves none of the
        annotation's geometry on the page (the annotation is to be dropped).
    """
    kind, props = _kind_props(ann)
    if op[0] == 'rotate':
        _rotate_annotation(kind, props, int(op[1]) % 360, op[2], op[3])
        return ann
    if op[0] == 'crop':
        return ann if _crop_annotation(kind, props, op[1]) else None
    raise ValueError(f'Unknown transform op: {op!r}')


def transform_annotations(annotations, op):
    """Apply :func:`transform_annotation` to a list, dropping removed ones."""
    result = []
    for ann in annotations:
        transformed = transform_annotation(ann, op)
        if transformed is not None:
            result.append(transformed)
    return result


def transform_rect(rect, ops, page_w, page_h):
    """Map an axis-aligned rect through a page's cumulative transform ops.

    Args:
        rect: [x0, y0, x1, y1] in the page's ORIGINAL pixel space.
        ops: Sequence of ``('rotate', degrees)`` / ``('crop', [x0,y0,x1,y1])``
            entries as produced by
            :meth:`workonward_read.pdf_ops.PageOpsJournal.transform_ops_for_original`.
        page_w, page_h: Pixel size of the page BEFORE the first op (the page
            size is tracked through rotations and crops internally).

    Returns:
        The transformed [x0, y0, x1, y1] (clipped to the page after crops),
        or None when a crop removed the rectangle entirely.
    """
    x0, y0, x1, y1 = (float(v) for v in rect)
    w, h = float(page_w), float(page_h)
    for op in ops:
        if op[0] == 'rotate':
            degrees = int(op[1]) % 360
            a = rotate_point_cw((x0, y0), degrees, w, h)
            b = rotate_point_cw((x1, y1), degrees, w, h)
            x0, y0, x1, y1 = _norm_box(a, b)
            if degrees in (90, 270):
                w, h = h, w
        elif op[0] == 'crop':
            cx0, cy0, cx1, cy1 = (float(v) for v in op[1])
            if not _boxes_intersect((x0, y0, x1, y1), (cx0, cy0, cx1, cy1)):
                return None
            w, h = cx1 - cx0, cy1 - cy0
            x0 = max(0.0, x0 - cx0)
            y0 = max(0.0, y0 - cy0)
            x1 = min(w, x1 - cx0)
            y1 = min(h, y1 - cy0)
        else:
            raise ValueError(f'Unknown transform op: {op!r}')
    return [x0, y0, x1, y1]
