#!/usr/bin/env bash
#
# Build the CoverUP web image and run it, then print the URL.
# Usage:  ./docker/run.sh
set -euo pipefail

cd "$(dirname "$0")/.."          # repo root (build context)

mkdir -p coverup-files           # host folder shared with the app (/files)

echo ">> Building image 'coverup-web' (first build pulls deps; give it a few min)..."
docker build -f docker/Dockerfile -t coverup-web .

echo ">> (Re)starting container 'coverup-web'..."
docker rm -f coverup-web >/dev/null 2>&1 || true
docker run -d --name coverup-web \
    -p 6080:6080 \
    -v "$PWD/coverup-files:/files" \
    coverup-web

cat <<EOF

============================================================
 CoverUP is running. Open this in your browser:

   http://localhost:6080/vnc.html?autoconnect=true&resize=scale

 Put PDFs/images in ./coverup-files to open them from inside
 the app (they appear at /files in the file dialog), and your
 redacted exports saved there show up on your Mac too.

 Stop it with:   docker rm -f coverup-web
============================================================
EOF
