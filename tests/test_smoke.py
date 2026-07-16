"""
Headless-safe smoke tests for packaging/release sanity.

These tests never open a window; they verify the bits the desktop
packaging relies on: importability, version consistency, bundled fonts,
the Tcl/Tk 8.6 requirement, and the CLI entry point.
"""

import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_import_workonward_read():
    """The package imports without side effects (no GUI, no display)."""
    import workonward_read

    assert workonward_read.__version__


def test_version_matches_pyproject():
    """workonward_read.__version__ must equal the version in pyproject.toml."""
    import tomllib

    import workonward_read

    pyproject_path = os.path.join(REPO_ROOT, "pyproject.toml")
    with open(pyproject_path, "rb") as fh:
        pyproject = tomllib.load(fh)

    assert workonward_read.__version__ == pyproject["project"]["version"], (
        f"workonward_read.__version__ ({workonward_read.__version__}) does not match "
        f"pyproject.toml ({pyproject['project']['version']}) - "
        "bump both together before tagging a release"
    )


def test_fonts_folder_resolves_and_has_required_fonts():
    """The resource lookup used by the frozen app must find the fonts."""
    from workonward_read.utils import find_fonts_folder, get_resource_root

    fonts_dir = find_fonts_folder(get_resource_root())
    assert os.path.isdir(fonts_dir)

    entries = os.listdir(fonts_dir)
    assert any(name.startswith("MaterialSymbols") for name in entries), (
        f"MaterialSymbols icon font missing from {fonts_dir}"
    )
    assert "DejaVuSans.ttf" in entries, f"DejaVuSans.ttf missing from {fonts_dir}"


def test_tcl_is_8_6():
    """FreeSimpleGUI requires Tcl 8.6; Tcl 9 breaks it (docs/tcl9-migration.md)."""
    tkinter = pytest.importorskip("tkinter", reason="tkinter not available")

    patchlevel = str(tkinter.Tcl().call("info", "patchlevel"))
    assert patchlevel.startswith("8.6"), (
        f"Tcl/Tk is {patchlevel}, expected 8.6.x - desktop builds must not "
        "ship Tcl 9 (see docs/tcl9-migration.md)"
    )


def test_module_cli_version_exits_zero():
    """`python -m workonward_read --version` works from the repo root."""
    result = subprocess.run(
        [sys.executable, "-m", "workonward_read", "--version"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"`{sys.executable} -m workonward_read --version` failed "
        f"(rc={result.returncode})\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
