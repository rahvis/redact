#!/usr/bin/env bash
# Build CoverUP.app for macOS (native arch of this machine).
#
# Usage (from anywhere; the script cd's to the repo root):
#   bash packaging/macos/build_app.sh
#
# Environment:
#   PYTHON  Python 3.13 interpreter to use.
#           Default: the python.org framework install (which bundles Tcl/Tk 8.6).
#
# Notes:
#   - There is NO universal2 build: pypdfium2 and Pillow do not publish
#     universal2 wheels. Build arm64 on Apple Silicon and x86_64 on Intel.
#   - Intel builds additionally pin cryptography<49 (no Intel macOS wheels
#     for >=49) via packaging/constraints-macos-x86_64.txt.
#   - The app is ad-hoc signed ("-") so it runs locally; distribution builds
#     rely on the Gatekeeper instructions in README-Open-Me-First.txt.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$REPO_ROOT"

PYTHON="${PYTHON:-/Library/Frameworks/Python.framework/Versions/3.13/bin/python3}"
ARCH="$(uname -m)"

echo "==> Repo root:  $REPO_ROOT"
echo "==> Python:     $PYTHON"
echo "==> Arch:       $ARCH"

[ -x "$PYTHON" ] || command -v "$PYTHON" >/dev/null 2>&1 || {
    echo "ERROR: Python not found: $PYTHON (set PYTHON=... to override)" >&2
    exit 1
}

[ -f "${SCRIPT_DIR}/CoverUP.icns" ] || {
    echo "ERROR: packaging/macos/CoverUP.icns missing. Run packaging/macos/make_icns.sh first." >&2
    exit 1
}

# --- Build venv ---------------------------------------------------------------
VENV="${REPO_ROOT}/.venv-build"
echo "==> Creating build venv: $VENV"
"$PYTHON" -m venv "$VENV"
VPY="${VENV}/bin/python"
"$VPY" -m pip install --upgrade pip

# Wheels only: an sdist fallback means a missing wheel and must fail loudly.
PIP_ARGS=(--only-binary :all: -c packaging/constraints.txt)
if [ "$ARCH" = "x86_64" ]; then
    echo "==> Intel build: applying constraints-macos-x86_64.txt (cryptography<49)"
    PIP_ARGS+=(-c packaging/constraints-macos-x86_64.txt)
fi
"$VPY" -m pip install "${PIP_ARGS[@]}" . pyinstaller

# --- Tcl/Tk gate: must be 8.6, not 9.x ------------------------------------------
echo "==> Checking Tcl/Tk version (must be 8.6)..."
"$VPY" -c "import tkinter,sys; v=str(tkinter.Tcl().call('info','patchlevel')); print('Tcl', v); sys.exit(0 if v.startswith('8.6') else 1)" || {
    echo "ERROR: Tcl/Tk is not 8.6 - FreeSimpleGUI requires Tcl 8.6 (see docs/tcl9-migration.md)" >&2
    exit 1
}

# --- PyInstaller build -----------------------------------------------------------
echo "==> Building with PyInstaller..."
"$VPY" -m PyInstaller packaging/coverup.spec --noconfirm

APP="${REPO_ROOT}/dist/CoverUP.app"
[ -d "$APP" ] || { echo "ERROR: $APP was not produced" >&2; exit 1; }

# --- Ad-hoc codesign -------------------------------------------------------------
echo "==> Ad-hoc signing ${APP}..."
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

# --- Smoke test ------------------------------------------------------------------
echo "==> Smoke testing (--version)..."
"${APP}/Contents/MacOS/CoverUP" --version

echo "==> Done: $APP"
