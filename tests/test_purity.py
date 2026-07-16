"""Layering test: business modules must not import FreeSimpleGUI or tkinter.

Each module is imported in a fresh subprocess so sys.modules is isolated.
Modules that do not exist yet mid-wave are skipped.

Licensed under GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

import os
import subprocess
import sys

import pytest

BUSINESS_MODULES = [
    'pdf_ops', 'annotations', 'convert', 'ocr',
    'signing', 'forms', 'compare', 'search', 'geometry',
]

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHECK_TEMPLATE = """
import importlib
import sys

try:
    importlib.import_module("workonward_read.{module}")
except ImportError:
    print("SKIP")
    raise SystemExit(0)

if "FreeSimpleGUI" in sys.modules:
    print("FAIL: FreeSimpleGUI imported")
    raise SystemExit(1)
if "tkinter" in sys.modules:
    print("FAIL: tkinter imported")
    raise SystemExit(1)
print("OK")
"""


@pytest.mark.parametrize('module', BUSINESS_MODULES)
def test_business_module_is_gui_free(module):
    proc = subprocess.run(
        [sys.executable, '-c', _CHECK_TEMPLATE.format(module=module)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=120,
    )
    output = proc.stdout.strip()

    if output == 'SKIP':
        pytest.skip(f'workonward_read.{module} does not exist yet')

    assert proc.returncode == 0, (
        f'workonward_read.{module} purity check failed:\n{proc.stdout}\n{proc.stderr}'
    )
    assert output.endswith('OK'), f'unexpected output: {proc.stdout}\n{proc.stderr}'
