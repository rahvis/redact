"""GUI smoke tests: layouts and menu spec build without opening a Window.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
(c) 2026 WorkOnward Read contributors
"""

import importlib

from workonward_read import menu


def test_menu_spec_builds():
    spec = menu.build_menu()
    assert isinstance(spec, list)
    assert len(spec) == 7  # File, Edit, View, Tools, Protect, Sign, Help
    for entry in spec:
        assert isinstance(entry, list) and len(entry) == 2
        title, items = entry
        assert isinstance(title, str) and title
        assert isinstance(items, list) and items


def test_menu_submenus_present():
    spec = menu.build_menu()
    file_items = spec[0][1]
    tools_items = spec[3][1]

    # File > Convert holds the four PDF conversion items
    convert_idx = file_items.index('Convert')
    convert_sub = file_items[convert_idx + 1]
    assert isinstance(convert_sub, list)
    convert_keys = {item.rsplit('::', 1)[-1] for item in convert_sub}
    assert convert_keys == {'MENU_CONVERT_IMAGES', 'MENU_CONVERT_TEXT',
                            'MENU_CONVERT_WORD', 'MENU_CONVERT_HTML'}

    # Tools > Organize Pages holds the eight page-organization items
    organize_idx = tools_items.index('Organize Pages')
    organize_sub = tools_items[organize_idx + 1]
    assert isinstance(organize_sub, list)
    organize_keys = {item.rsplit('::', 1)[-1] for item in organize_sub}
    assert organize_keys == {'MENU_MERGE', 'MENU_SPLIT', 'MENU_INSERT_PAGES',
                             'MENU_DELETE_PAGES', 'MENU_REORDER_PAGES',
                             'MENU_ROTATE_PAGES', 'MENU_EXTRACT_PAGES',
                             'MENU_CROP'}


def _layout_keys(layout):
    keys = set()
    for row in layout:
        for element in row:
            key = getattr(element, 'Key', None)
            if key is not None:
                keys.add(key)
    return keys


def test_create_layout_returns_list_with_expected_elements():
    from workonward_read import ui

    icons = ui.create_icons(ui.get_fontpath())
    layout = ui.create_layout(icons)

    assert isinstance(layout, list)
    keys = _layout_keys(layout)

    # New phase-0 elements
    assert '-MENUBAR-' in keys
    assert '-TOOL-' in keys

    # Every classic toolbar element still exists
    for classic in ('LOAD_PDF', 'SAVE_PDF', 'EXPORT_PAGE', 'UNDO', 'EDIT_MODE',
                    'DELETE_ALL', 'CHANGE_COLOR', 'TOGGLE_QUALITY', 'BACK',
                    'FORTH', 'ZOOM_IN', 'ZOOM_OUT', 'ABOUT', '-PAGE_NUM-',
                    '-PAGE_TOTAL-', '-PROGRESS-'):
        assert classic in keys, f'missing classic element {classic}'


def test_tool_selector_offers_all_tool_keys():
    from workonward_read import ui

    assert ui.TOOL_KEYS == [
        'redact', 'eraser', 'text', 'highlight', 'underline', 'strike', 'ink',
        'rect', 'ellipse', 'line', 'arrow', 'stamp', 'image', 'signature',
        'measure',
    ]


def test_registered_tools_are_a_subset_of_the_selector():
    from workonward_read import canvas_tools, ui

    assert set(canvas_tools.TOOLS) <= set(ui.TOOL_KEYS)
    assert 'redact' in canvas_tools.TOOLS
    assert 'eraser' in canvas_tools.TOOLS
    for tool in canvas_tools.TOOLS.values():
        assert isinstance(tool.cursor, str)
        assert callable(tool.on_press)
        assert callable(tool.on_drag)
        assert callable(tool.on_release)


def test_import_main_succeeds():
    module = importlib.import_module('workonward_read.main')
    assert callable(module.main)
