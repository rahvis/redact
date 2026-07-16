"""
Annotate dialogs for WorkOnward Read: text, stamp, image and signature
placement dialogs (opened by the click-to-place canvas tools) plus the
document decoration dialogs (watermark, header & footer / page numbers /
Bates numbering).

Every dialog is modal, keep-on-top, centered over the parent window and
returns a plain request dict (annotation props / decoration config) or None
when cancelled.

Pure image helpers (``encode_png_b64``, ``render_typed_signature``,
``ink_strokes_to_png``) live at module level so they are unit-testable
without opening a window.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
(c) 2026 WorkOnward Read contributors
"""

import base64
import io
import os

import FreeSimpleGUI as sg
from PIL import Image, ImageDraw, ImageFont

from workonward_read.annotations import STAMP_PRESETS
from workonward_read.canvas_tools import tool_defaults
from workonward_read.dialogs.common import (error_popup, file_open_row,
                                            open_modal as _open_modal)
from workonward_read.i18n import _
from workonward_read.utils import find_fonts_folder, get_resource_root


# Embedded PNGs (image / signature annotations, image watermarks) are
# downscaled until they fit in this many bytes.
MAX_EMBED_PNG_BYTES = 500 * 1024

COLOR_CHOICES = ['black', 'white', 'red', 'green', 'blue', 'yellow',
                 'orange', 'purple', 'gray']

_POSITIONS = ['header-left', 'header-center', 'header-right',
              'footer-left', 'footer-center', 'footer-right']

_IMAGE_FILE_TYPES = (('Images', '*.png *.PNG *.jpg *.JPG *.jpeg *.JPEG'),)


def _font_dir():
    try:
        return find_fonts_folder(get_resource_root())
    except Exception:
        return ''


# ---------------------------------------------------------------------------
# Pure helpers (GUI-free, unit-testable)
# ---------------------------------------------------------------------------

def encode_png_b64(pil_image, max_bytes=MAX_EMBED_PNG_BYTES):
    """Encode a PIL image as base64 PNG, downscaling by 20% steps until the
    encoded size fits in ``max_bytes`` (or the image is tiny already)."""
    img = pil_image.convert('RGBA')
    try:
        while True:
            with io.BytesIO() as buffer:
                img.save(buffer, format='PNG')
                data = buffer.getvalue()
            if len(data) <= max_bytes or img.width <= 64 or img.height <= 64:
                return base64.b64encode(data).decode('ascii')
            smaller = img.resize(
                (max(1, int(img.width * 0.8)), max(1, int(img.height * 0.8))),
                resample=Image.Resampling.LANCZOS)
            img.close()
            img = smaller
    finally:
        if img is not pil_image:
            img.close()


def load_image_file_b64(path, max_bytes=MAX_EMBED_PNG_BYTES):
    """Load an image file and return its (possibly downscaled) base64 PNG."""
    with Image.open(path) as img:
        img.load()
        return encode_png_b64(img, max_bytes)


def render_typed_signature(name, font_dir=None, size_px=72, color='black'):
    """Render ``name`` as a slanted, script-ish RGBA signature image using
    the bundled DejaVu font. Returns a PIL image or None for empty names."""
    name = (name or '').strip()
    if not name:
        return None
    font_dir = font_dir if font_dir is not None else _font_dir()
    try:
        font = ImageFont.truetype(
            os.path.join(font_dir, 'DejaVuSans-Bold.ttf'), size_px)
    except (OSError, TypeError):
        font = ImageFont.load_default()

    probe = Image.new('RGBA', (1, 1))
    bbox = ImageDraw.Draw(probe).textbbox((0, 0), name, font=font)
    probe.close()
    pad = max(4, size_px // 4)
    width = (bbox[2] - bbox[0]) + 2 * pad
    height = (bbox[3] - bbox[1]) + 2 * pad

    img = Image.new('RGBA', (max(1, width), max(1, height)), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((pad - bbox[0], pad - bbox[1]), name,
                             font=font, fill=color)

    # Shear for a handwriting-like slant.
    slant = 0.30
    out_width = img.width + int(img.height * slant)
    sheared = img.transform(
        (out_width, img.height), Image.AFFINE,
        (1, slant, -slant * img.height, 0, 1, 0),
        resample=Image.Resampling.BICUBIC)
    img.close()
    return sheared


def ink_strokes_to_png(strokes, width_px=3, color='black', margin=8):
    """Convert captured ink strokes (list of point lists, y-down) into a
    tightly-cropped transparent RGBA image. Returns None when empty."""
    points = [p for stroke in (strokes or []) for p in stroke]
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, y0 = min(xs), min(ys)
    width = int(max(xs) - x0) + 2 * margin
    height = int(max(ys) - y0) + 2 * margin

    img = Image.new('RGBA', (max(1, width), max(1, height)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for stroke in strokes:
        pts = [(p[0] - x0 + margin, p[1] - y0 + margin) for p in stroke]
        if len(pts) >= 2:
            draw.line(pts, fill=color, width=width_px, joint='curve')
        elif len(pts) == 1:
            x, y = pts[0]
            r = max(1, width_px)
            draw.ellipse([x - r, y - r, x + r, y + r], fill=color)
    return img


# ---------------------------------------------------------------------------
# Dialog helpers
# ---------------------------------------------------------------------------

def _color_choices(default):
    choices = list(COLOR_CHOICES)
    if default and default not in choices:
        choices.insert(0, default)
    return choices


# ---------------------------------------------------------------------------
# Annotation placement dialogs (click-to-place tools)
# ---------------------------------------------------------------------------

def text_dialog(window, state, pos):
    """Multiline text annotation dialog. Returns 'text' props or None."""
    defaults = tool_defaults(state, 'text')
    layout = [
        [sg.Text(_('Text:'))],
        [sg.Multiline(default_text='', key='-TEXT-', size=(42, 5))],
        [sg.Text(_('Size (px)')),
         sg.Spin(values=list(range(8, 201)), initial_value=int(defaults.get('size_px', 32)),
                 key='-SIZE-', size=(5, 1)),
         sg.Checkbox(_('Bold'), default=bool(defaults.get('bold', False)), key='-BOLD-'),
         sg.Text(_('Color')),
         sg.Combo(_color_choices(defaults.get('color', 'black')),
                  default_value=defaults.get('color', 'black'),
                  key='-COLOR-', readonly=True, size=(10, 1))],
        [sg.Push(), sg.Button(_('OK'), key='-OK-'), sg.Button(_('Cancel'), key='-CANCEL-')],
    ]
    dialog = _open_modal(_('Add Text'), layout, window)
    event, values = dialog.read()
    dialog.close()
    if event != '-OK-':
        return None
    text = (values.get('-TEXT-') or '').rstrip('\n')
    if not text.strip():
        return None
    try:
        size_px = int(values.get('-SIZE-') or 32)
    except (TypeError, ValueError):
        size_px = 32
    return {
        'pos': [int(pos[0]), int(pos[1])],
        'text': text,
        'size_px': size_px,
        'color': values.get('-COLOR-') or 'black',
        'bold': bool(values.get('-BOLD-')),
    }


def stamp_dialog(window, state, pos):
    """Stamp annotation dialog (preset / custom text). Returns props or None."""
    defaults = tool_defaults(state, 'stamp')
    presets = list(STAMP_PRESETS) + ['custom']
    angles = list(range(-180, 181, 15))
    scales = [0.5, 0.75, 1.0, 1.5, 2.0, 3.0]
    layout = [
        [sg.Text(_('Preset')),
         sg.Combo(presets, default_value=defaults.get('preset', 'approved'),
                  key='-PRESET-', readonly=True, size=(14, 1))],
        [sg.Text(_('Custom text')),
         sg.Input(default_text=str(defaults.get('text', '')), key='-TEXT-', size=(24, 1))],
        [sg.Text(_('Color')),
         sg.Combo(_color_choices(defaults.get('color', '#c62828')),
                  default_value=defaults.get('color', '#c62828'),
                  key='-COLOR-', size=(10, 1)),
         sg.Text(_('Angle')),
         sg.Spin(values=angles, initial_value=int(defaults.get('angle', 0)),
                 key='-ANGLE-', size=(5, 1)),
         sg.Text(_('Scale')),
         sg.Combo(scales, default_value=float(defaults.get('scale', 1.0)),
                  key='-SCALE-', size=(5, 1))],
        [sg.Push(), sg.Button(_('OK'), key='-OK-'), sg.Button(_('Cancel'), key='-CANCEL-')],
    ]
    dialog = _open_modal(_('Add Stamp'), layout, window)
    event, values = dialog.read()
    dialog.close()
    if event != '-OK-':
        return None
    preset = values.get('-PRESET-') or 'custom'
    text = (values.get('-TEXT-') or '').strip()
    if preset == 'custom' and not text:
        return None
    try:
        angle = float(values.get('-ANGLE-') or 0)
    except (TypeError, ValueError):
        angle = 0.0
    try:
        scale = float(values.get('-SCALE-') or 1.0)
    except (TypeError, ValueError):
        scale = 1.0
    return {
        'pos': [int(pos[0]), int(pos[1])],
        'preset': preset,
        'text': text,
        'color': values.get('-COLOR-') or '#c62828',
        'angle': angle,
        'scale': scale,
    }


def image_dialog(window, state, pos):
    """Image annotation dialog: pick a file, embed it as (≤500 KB) PNG."""
    defaults = tool_defaults(state, 'image')
    scales = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0]
    layout = [
        [sg.Text(_('Image file'))],
        file_open_row('-FILE-', file_types=_IMAGE_FILE_TYPES),
        [sg.Text(_('Scale')),
         sg.Combo(scales, default_value=float(defaults.get('scale', 1.0)),
                  key='-SCALE-', size=(6, 1))],
        [sg.Push(), sg.Button(_('OK'), key='-OK-'), sg.Button(_('Cancel'), key='-CANCEL-')],
    ]
    dialog = _open_modal(_('Insert Image'), layout, window)
    event, values = dialog.read()
    dialog.close()
    if event != '-OK-':
        return None
    path = (values.get('-FILE-') or '').strip()
    if not path:
        return None
    try:
        png_b64 = load_image_file_b64(path)
    except Exception as exc:
        error_popup(window, _('error_occurred'), exc)
        return None
    try:
        scale = float(values.get('-SCALE-') or 1.0)
    except (TypeError, ValueError):
        scale = 1.0
    return {'pos': [int(pos[0]), int(pos[1])], 'png_b64': png_b64, 'scale': scale}


def signature_dialog(window, state, pos):
    """Signature dialog with three tabs: Type (name rendered via DejaVu),
    Draw (captured ink strokes) and Image (file picker). Returns
    'signature' props or None."""
    graph_size = (380, 140)
    type_tab = [
        [sg.Text(_('Name'))],
        [sg.Input(key='-SIG_NAME-', size=(34, 1))],
    ]
    draw_tab = [
        [sg.Graph(canvas_size=graph_size,
                  graph_bottom_left=(0, graph_size[1]),
                  graph_top_right=(graph_size[0], 0),
                  background_color='white', key='-SIG_GRAPH-',
                  enable_events=True, drag_submits=True)],
        [sg.Button(_('Clear'), key='-SIG_CLEAR-')],
    ]
    image_tab = [
        [sg.Text(_('Image file'))],
        file_open_row('-SIG_FILE-', file_types=_IMAGE_FILE_TYPES),
    ]
    layout = [
        [sg.TabGroup([[
            sg.Tab(_('Type'), type_tab, key='-TAB_TYPE-'),
            sg.Tab(_('Draw'), draw_tab, key='-TAB_DRAW-'),
            sg.Tab(_('Image'), image_tab, key='-TAB_IMAGE-'),
        ]], key='-TABS-')],
        [sg.Push(), sg.Button(_('OK'), key='-OK-'), sg.Button(_('Cancel'), key='-CANCEL-')],
    ]
    dialog = _open_modal(_('Add Signature'), layout, window)

    strokes = []
    dragging = False
    result = None
    while True:
        event, values = dialog.read()
        if event in (sg.WINDOW_CLOSED, '-CANCEL-'):
            break

        if event == '-SIG_GRAPH-':
            point = values.get('-SIG_GRAPH-')
            if point is None:
                continue
            point = [int(point[0]), int(point[1])]
            if not dragging:
                dragging = True
                strokes.append([point])
            else:
                previous = strokes[-1][-1]
                if point != previous:
                    strokes[-1].append(point)
                    try:
                        dialog['-SIG_GRAPH-'].draw_line(
                            tuple(previous), tuple(point), color='black', width=3)
                    except Exception:
                        pass

        elif event == '-SIG_GRAPH-+UP':
            dragging = False

        elif event == '-SIG_CLEAR-':
            strokes = []
            dragging = False
            try:
                dialog['-SIG_GRAPH-'].erase()
            except Exception:
                pass

        elif event == '-OK-':
            tab = values.get('-TABS-')
            img = None
            if tab == '-TAB_TYPE-':
                img = render_typed_signature(values.get('-SIG_NAME-'))
                if img is None:
                    error_popup(window, _('Please enter a name.'))
                    continue
            elif tab == '-TAB_DRAW-':
                img = ink_strokes_to_png(strokes)
                if img is None:
                    error_popup(window, _('Please draw a signature first.'))
                    continue
            else:
                path = (values.get('-SIG_FILE-') or '').strip()
                if not path:
                    error_popup(window, _('Please choose an image file.'))
                    continue
                try:
                    with Image.open(path) as loaded:
                        loaded.load()
                        img = loaded.convert('RGBA')
                except Exception as exc:
                    error_popup(window, _('error_occurred'), exc)
                    continue
            png_b64 = encode_png_b64(img)
            img.close()
            result = {'pos': [int(pos[0]), int(pos[1])],
                      'png_b64': png_b64, 'scale': 1.0}
            break

    dialog.close()
    return result


# ---------------------------------------------------------------------------
# Document decoration dialogs
# ---------------------------------------------------------------------------

def watermark_dialog(window, state):
    """Watermark dialog (text or image, opacity, angle, scale).

    Returns a watermark config dict, ``{'remove': True}`` to clear an
    existing watermark, or None on cancel."""
    current = (getattr(state, 'decorations', None) or {}).get('watermark') or {}
    image_mode = bool(current.get('png_b64'))
    angles = list(range(-180, 181, 15))
    scales = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0]
    layout = [
        [sg.Radio(_('Text'), 'WM_MODE', default=not image_mode, key='-MODE_TEXT-'),
         sg.Radio(_('Image'), 'WM_MODE', default=image_mode, key='-MODE_IMAGE-')],
        [sg.Text(_('Text')),
         sg.Input(default_text=str(current.get('text', 'CONFIDENTIAL')),
                  key='-TEXT-', size=(28, 1))],
        [sg.Text(_('Image file'))],
        file_open_row('-FILE-', file_types=_IMAGE_FILE_TYPES),
        [sg.Text(_('Opacity (%)')),
         sg.Slider(range=(5, 100), resolution=5, orientation='h', size=(24, 14),
                   default_value=int(round(float(current.get('opacity', 0.15)) * 100)),
                   key='-OPACITY-')],
        [sg.Text(_('Angle')),
         sg.Spin(values=angles, initial_value=int(current.get('angle', 45)),
                 key='-ANGLE-', size=(5, 1)),
         sg.Text(_('Scale')),
         sg.Combo(scales, default_value=float(current.get('scale', 1.0)),
                  key='-SCALE-', size=(6, 1)),
         sg.Text(_('Color')),
         sg.Combo(_color_choices(current.get('color', 'gray')),
                  default_value=current.get('color', 'gray'),
                  key='-COLOR-', size=(10, 1))],
        [sg.Push(),
         sg.Button(_('OK'), key='-OK-'),
         sg.Button(_('Remove'), key='-REMOVE-', visible=bool(current)),
         sg.Button(_('Cancel'), key='-CANCEL-')],
    ]
    dialog = _open_modal(_('Watermark'), layout, window)
    event, values = dialog.read()
    dialog.close()
    if event == '-REMOVE-':
        return {'remove': True}
    if event != '-OK-':
        return None

    try:
        opacity = max(0.0, min(1.0, float(values.get('-OPACITY-') or 15) / 100.0))
    except (TypeError, ValueError):
        opacity = 0.15
    try:
        angle = float(values.get('-ANGLE-') or 45)
    except (TypeError, ValueError):
        angle = 45.0
    try:
        scale = float(values.get('-SCALE-') or 1.0)
    except (TypeError, ValueError):
        scale = 1.0
    request = {'opacity': opacity, 'angle': angle, 'scale': scale}

    if values.get('-MODE_IMAGE-'):
        path = (values.get('-FILE-') or '').strip()
        if not path:
            return None
        try:
            request['png_b64'] = load_image_file_b64(path)
        except Exception as exc:
            error_popup(window, _('error_occurred'), exc)
            return None
    else:
        text = (values.get('-TEXT-') or '').strip()
        if not text:
            return None
        request['text'] = text
        request['color'] = values.get('-COLOR-') or 'gray'
    return request


def header_footer_dialog(window, state):
    """Header & footer / page numbers / Bates numbering dialog.

    Returns ``{'header_footer': dict|None, 'page_numbers': dict|None,
    'bates': dict|None}`` or None on cancel. A None value means "remove this
    decoration"."""
    decorations = getattr(state, 'decorations', None) or {}
    hf = decorations.get('header_footer') or {}
    pn = decorations.get('page_numbers')
    bates = decorations.get('bates')

    hf_frame = sg.Frame(_('Header & footer text'), [
        [sg.Text(_('Left'), size=(8, 1)),
         sg.Input(default_text=str(hf.get('left', '')), key='-HF_LEFT-', size=(30, 1))],
        [sg.Text(_('Center'), size=(8, 1)),
         sg.Input(default_text=str(hf.get('center', '')), key='-HF_CENTER-', size=(30, 1))],
        [sg.Text(_('Right'), size=(8, 1)),
         sg.Input(default_text=str(hf.get('right', '')), key='-HF_RIGHT-', size=(30, 1))],
        [sg.Text(_('Position')),
         sg.Combo(['header', 'footer'], default_value=hf.get('position', 'header'),
                  key='-HF_POS-', readonly=True, size=(8, 1)),
         sg.Text(_('Size (px)')),
         sg.Spin(values=list(range(8, 73)), initial_value=int(hf.get('size_px', 24)),
                 key='-HF_SIZE-', size=(5, 1))],
    ])

    pn_defaults = pn or {}
    pn_frame = sg.Frame(_('Page numbers'), [
        [sg.Checkbox(_('Add page numbers'), default=bool(pn), key='-PN_ON-')],
        [sg.Text(_('Template'), size=(8, 1)),
         sg.Input(default_text=str(pn_defaults.get('template', '{page} / {total}')),
                  key='-PN_TEMPLATE-', size=(20, 1)),
         sg.Text(_('Start at')),
         sg.Spin(values=list(range(1, 10000)), initial_value=int(pn_defaults.get('start_at', 1)),
                 key='-PN_START-', size=(6, 1))],
        [sg.Text(_('Position'), size=(8, 1)),
         sg.Combo(_POSITIONS, default_value=pn_defaults.get('position', 'footer-center'),
                  key='-PN_POS-', readonly=True, size=(14, 1))],
    ])

    bates_defaults = bates or {}
    bates_frame = sg.Frame(_('Bates numbering'), [
        [sg.Checkbox(_('Add Bates numbers'), default=bool(bates), key='-BT_ON-')],
        [sg.Text(_('Prefix'), size=(8, 1)),
         sg.Input(default_text=str(bates_defaults.get('prefix', '')),
                  key='-BT_PREFIX-', size=(12, 1)),
         sg.Text(_('Start')),
         sg.Spin(values=list(range(1, 1000000)),
                 initial_value=int(bates_defaults.get('start', 1)),
                 key='-BT_START-', size=(7, 1)),
         sg.Text(_('Digits')),
         sg.Spin(values=list(range(1, 13)),
                 initial_value=int(bates_defaults.get('digits', 6)),
                 key='-BT_DIGITS-', size=(4, 1))],
        [sg.Text(_('Position'), size=(8, 1)),
         sg.Combo(_POSITIONS, default_value=bates_defaults.get('position', 'footer-right'),
                  key='-BT_POS-', readonly=True, size=(14, 1))],
    ])

    layout = [
        [hf_frame],
        [pn_frame],
        [bates_frame],
        [sg.Text(_('Templates substitute {page}, {total}, {date} and {bates}.'),
                 text_color='gray')],
        [sg.Push(), sg.Button(_('OK'), key='-OK-'), sg.Button(_('Cancel'), key='-CANCEL-')],
    ]
    dialog = _open_modal(_('Header & Footer'), layout, window)
    event, values = dialog.read()
    dialog.close()
    if event != '-OK-':
        return None

    def _spin_int(key, fallback):
        try:
            return int(values.get(key) or fallback)
        except (TypeError, ValueError):
            return fallback

    left = (values.get('-HF_LEFT-') or '').strip()
    center = (values.get('-HF_CENTER-') or '').strip()
    right = (values.get('-HF_RIGHT-') or '').strip()
    header_footer = None
    if left or center or right:
        header_footer = {
            'left': left,
            'center': center,
            'right': right,
            'position': values.get('-HF_POS-') or 'header',
            'size_px': _spin_int('-HF_SIZE-', 24),
        }

    page_numbers = None
    if values.get('-PN_ON-'):
        page_numbers = {
            'template': (values.get('-PN_TEMPLATE-') or '{page} / {total}').strip()
                        or '{page} / {total}',
            'start_at': _spin_int('-PN_START-', 1),
            'position': values.get('-PN_POS-') or 'footer-center',
        }

    bates_cfg = None
    if values.get('-BT_ON-'):
        bates_cfg = {
            'prefix': values.get('-BT_PREFIX-') or '',
            'start': _spin_int('-BT_START-', 1),
            'digits': _spin_int('-BT_DIGITS-', 6),
            'position': values.get('-BT_POS-') or 'footer-right',
        }

    return {
        'header_footer': header_footer,
        'page_numbers': page_numbers,
        'bates': bates_cfg,
    }
