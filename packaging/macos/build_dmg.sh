#!/usr/bin/env bash
# Build a distributable DMG for WorkOnward Read.
#
# Usage (from anywhere; the script cd's to the repo root):
#   bash packaging/macos/build_dmg.sh
#
# Environment:
#   SKIP_BUILD=1  Skip the app build (use an existing "dist/WorkOnward Read.app"),
#                 e.g. in CI where build_app.sh already ran.
#   PYTHON=...    Forwarded to build_app.sh.
#
# Output: dist/WorkOnwardRead-<version>-macOS-<arch>.dmg
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$REPO_ROOT"

if [ "${SKIP_BUILD:-0}" != "1" ]; then
    bash "${SCRIPT_DIR}/build_app.sh"
fi

APP="${REPO_ROOT}/dist/WorkOnward Read.app"
[ -d "$APP" ] || { echo "ERROR: $APP not found (run build_app.sh first or unset SKIP_BUILD)" >&2; exit 1; }

# Parse version from workonward_read/__init__.py (no import needed).
VER="$(sed -n 's/^__version__ *= *["'\'']\([^"'\'']*\)["'\''].*/\1/p' "${REPO_ROOT}/workonward_read/__init__.py")"
[ -n "$VER" ] || { echo "ERROR: could not parse version from workonward_read/__init__.py" >&2; exit 1; }

ARCH="$(uname -m)"
DMG="${REPO_ROOT}/dist/WorkOnwardRead-${VER}-macOS-${ARCH}.dmg"

echo "==> Version: $VER  Arch: $ARCH"
echo "==> Staging DMG contents..."

STAGE="$(mktemp -d)/WorkOnwardRead"
mkdir -p "$STAGE"
trap 'rm -rf "$(dirname "$STAGE")"' EXIT

cp -R "$APP" "${STAGE}/WorkOnward Read.app"
ln -s /Applications "${STAGE}/Applications"
cp "${SCRIPT_DIR}/README-Open-Me-First.txt" "${STAGE}/README-Open-Me-First.txt"

echo "==> Creating DMG..."
rm -f "$DMG"
hdiutil create -volname "WorkOnward Read ${VER}" -srcfolder "$STAGE" -ov -format UDZO "$DMG"

echo "==> Done: $DMG"
