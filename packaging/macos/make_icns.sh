#!/usr/bin/env bash
# Generate packaging/macos/CoverUP.icns from the repo's CoverUP.svg.
#
# Requirements:
#   - rsvg-convert  (brew install librsvg)
#   - iconutil      (ships with macOS)
#
# Usage: bash packaging/macos/make_icns.sh
#
# The .icns is committed to the repo so CI runners do not need librsvg.
# Re-run this script whenever CoverUP.svg changes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SVG="${REPO_ROOT}/CoverUP.svg"
OUT_ICNS="${SCRIPT_DIR}/CoverUP.icns"

command -v rsvg-convert >/dev/null 2>&1 || {
    echo "ERROR: rsvg-convert not found. Install it with: brew install librsvg" >&2
    exit 1
}
command -v iconutil >/dev/null 2>&1 || {
    echo "ERROR: iconutil not found (are you on macOS?)" >&2
    exit 1
}
[ -f "$SVG" ] || { echo "ERROR: SVG not found: $SVG" >&2; exit 1; }

ICONSET="$(mktemp -d)/CoverUP.iconset"
mkdir -p "$ICONSET"
trap 'rm -rf "$(dirname "$ICONSET")"' EXIT

# Apple iconset sizes: base + @2x variants.
render() {
    local px="$1" name="$2"
    rsvg-convert -w "$px" -h "$px" "$SVG" -o "${ICONSET}/${name}"
}

render 16   icon_16x16.png
render 32   icon_16x16@2x.png
render 32   icon_32x32.png
render 64   icon_32x32@2x.png
render 128  icon_128x128.png
render 256  icon_128x128@2x.png
render 256  icon_256x256.png
render 512  icon_256x256@2x.png
render 512  icon_512x512.png
render 1024 icon_512x512@2x.png

iconutil -c icns "$ICONSET" -o "$OUT_ICNS"

echo "Created: $OUT_ICNS"
