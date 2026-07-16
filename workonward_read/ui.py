"""
UI layout and icon definitions for WorkOnward Read.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import os
import FreeSimpleGUI as sg

from workonward_read.canvas_tools import ALL_TOOL_KEYS as TOOL_KEYS
from workonward_read.menu import build_menu
from workonward_read.thumbnails import build_sidebar_column
from workonward_read.utils import (get_script_root, find_fonts_folder, find_assets_folder,
                                   make_icons, draw_character)
from workonward_read.i18n import _


# Material Symbols glyphs for UI-icons
GLYPHS = {
    "left": "",
    "right": "",
    "zoom_in": "",
    "zoom_out": "",
    "close": "",
    "save_pdf": "",
    "open_file": "",
    "undo": "",
    "about": "",
    "eraser_off": "",
    "eraser": "",
    "inkdrop_white": "",
    "inkdrop_black": "",
    "delete_all": "",
    "cut": "",
    "low_quality": "",
    "high_quality": "",
}


def get_fontpath():
    """Get the path to the Material Symbols font file."""
    script_root = get_script_root()
    fonts_dir = find_fonts_folder(script_root)
    fontpath = os.path.join(fonts_dir, "MaterialSymbolsOutlined[FILL,GRAD,opsz,wght].ttf")

    if not os.path.exists(fontpath):
        raise FileNotFoundError(f"Font file not found: {fontpath}")

    return fontpath


def create_icons(fontpath):
    """Create the icons dictionary from the glyphs."""
    return make_icons(GLYPHS, fontpath)


def create_app_icon(fontpath):
    """Create the application window icon.

    Loads the branded WorkOnward Read icon from the package assets
    (workonward_read/assets/app_icon_128.png). Falls back to drawing the
    Material Symbols glyph if the asset is missing (e.g. stripped bundles).
    """
    try:
        assets_dir = find_assets_folder(get_script_root())
        icon_path = os.path.join(assets_dir, 'app_icon_128.png')
        if os.path.isfile(icon_path):
            with open(icon_path, 'rb') as fh:
                return fh.read()
    except Exception:
        pass
    return draw_character('', fontpath, font_size=110, width=128, height=128,
                          icon_background=True)


def create_layout(icons, image_bg_color='gray'):
    """Create the main window layout."""
    graph_layout = [[
        sg.Graph(
            canvas_size=(2, 2),
            background_color='silver',
            graph_bottom_left=(0, -2),
            graph_top_right=(2, 0),
            expand_x=False,
            expand_y=False,
            key='-GRAPH-',
            enable_events=True,
            drag_submits=True
        )
    ]]

    layout = [
        [sg.Menu(build_menu(), key='-MENUBAR-')],
        [
            sg.Push(background_color='gray'),
            sg.Push(background_color='gray'),
            sg.Push(background_color='gray'),
            sg.Image(icons['open_file'], key="LOAD_PDF", tooltip=_('tooltip_open'),
                     pad=0, enable_events=True, background_color=image_bg_color),
            sg.Image(icons['save_pdf'], key="SAVE_PDF", tooltip=_('tooltip_save'),
                     pad=0, enable_events=True, background_color=image_bg_color),
            sg.Image(icons['cut'], key="EXPORT_PAGE", tooltip=_('tooltip_export_page'),
                     pad=0, enable_events=True, background_color=image_bg_color),
            sg.Push(background_color='gray'),
            sg.Image(icons['undo'], key='UNDO', tooltip=_('tooltip_undo'),
                     pad=0, enable_events=True, background_color=image_bg_color),
            sg.Image(icons['eraser_off'], key="EDIT_MODE", tooltip=_('tooltip_eraser'),
                     pad=0, enable_events=True, background_color=image_bg_color),
            sg.Image(icons['delete_all'], key="DELETE_ALL", tooltip=_('tooltip_delete_all'),
                     pad=0, enable_events=True, background_color=image_bg_color),
            sg.Image(icons['inkdrop_black'], key='CHANGE_COLOR',
                     tooltip=_('tooltip_color'),
                     pad=0, enable_events=True, background_color=image_bg_color),
            sg.Image(icons['high_quality'], key='TOGGLE_QUALITY',
                     tooltip=_('tooltip_quality'),
                     pad=0, enable_events=True, background_color=image_bg_color),
            sg.Combo(TOOL_KEYS, default_value='redact', key='-TOOL-',
                     enable_events=True, readonly=True, size=(10, 1),
                     pad=(6, 0), tooltip=_('Annotation tool')),
            sg.Push(background_color='gray'),
            sg.Image(icons['left'], key='BACK', tooltip=_('tooltip_prev'),
                     pad=0, enable_events=True, background_color=image_bg_color),
            sg.Input(visible=False, focus=True),
            sg.Input(size=(4, 2), readonly=False, focus=False, change_submits=False,
                     enable_events=True, justification='center', key='-PAGE_NUM-'),
            sg.Text('/', background_color='gray'),
            sg.Text('0', key='-PAGE_TOTAL-', justification='left', background_color='gray'),
            sg.Image(icons['right'], key='FORTH', tooltip=_('tooltip_next'),
                     pad=0, enable_events=True, background_color=image_bg_color),
            sg.Push(background_color='gray'),
            sg.Image(icons['zoom_in'], key='ZOOM_IN', tooltip=_('tooltip_zoom_in'),
                     pad=0, enable_events=True, background_color=image_bg_color),
            sg.Image(icons['zoom_out'], key='ZOOM_OUT', tooltip=_('tooltip_zoom_out'),
                     pad=0, enable_events=True, background_color=image_bg_color),
            sg.Push(background_color='gray'),
            sg.Image(icons['about'], key="ABOUT", tooltip=_('tooltip_about'),
                     pad=0, enable_events=True, background_color=image_bg_color),
            sg.Push(background_color='gray'),
            sg.Push(background_color='gray'),
            sg.Push(background_color='gray'),
        ],
        [
            # Hidden-by-default thumbnails sidebar (View > Thumbnails).
            build_sidebar_column(),
            sg.Column(
                layout=graph_layout,
                background_color='silver',
                size=(2, 2),
                pad=0,
                expand_x=True,
                expand_y=True,
                scrollable=True,
                sbar_trough_color='lightgrey',
                sbar_background_color='darkgrey',
                sbar_relief=sg.RELIEF_RAISED,
                sbar_arrow_color='silver',
                key="-GRAPH_COLUMN-"
            )
        ],
        [
            sg.ProgressBar(
                100,
                key='-PROGRESS-',
                orientation='horizontal',
                bar_color=('green', 'white'),
                size_px=(50, 5),
                pad=(0, 5),
                expand_x=True
            )
        ]
    ]

    return layout


def toggle_edit_mode(window, icons, edit_mode='draw'):
    """Switch mode and set mouse pointer cursor."""
    edit_mode = 'erase' if edit_mode == 'draw' else 'draw'
    edit_icon = icons['eraser'] if edit_mode == 'erase' else icons['eraser_off']
    if edit_mode == 'erase':
        drawing_cursor = 'X_cursor'
    else:
        drawing_cursor = 'crosshair'
    window['-GRAPH-'].set_cursor(drawing_cursor)
    window['EDIT_MODE'].update(data=edit_icon)
    return edit_mode


def toggle_quality(window, icons, output_quality):
    """Toggle output quality setting."""
    output_quality = 'low' if output_quality == 'high' else 'high'
    quality_icon = icons['low_quality'] if output_quality == 'low' else icons['high_quality']
    window['TOGGLE_QUALITY'].update(data=quality_icon)
    return output_quality


def toggle_color(window, icons, fill_color):
    """Toggle fill color between black and white."""
    fill_color = 'white' if fill_color == 'black' else 'black'
    color_icon = icons['inkdrop_black'] if fill_color == 'black' else icons['inkdrop_white']
    window['CHANGE_COLOR'].update(data=color_icon)
    return fill_color
