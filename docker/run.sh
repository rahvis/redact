#!/usr/bin/env bash
#
# Build the WorkOnward Read desktop-in-browser image and run it, then print the URL.
# Usage:  ./docker/run.sh
set -euo pipefail

cd "$(dirname "$0")/.."          # repo root (build context)

mkdir -p workonward-read-files   # host folder shared with the app (/files)

echo ">> Building image 'workonward-read-desktop' (first build pulls deps; give it a few min)..."
docker build -f docker/Dockerfile -t workonward-read-desktop .

echo ">> (Re)starting container 'workonward-read-desktop'..."
docker rm -f workonward-read-desktop >/dev/null 2>&1 || true
docker run -d --name workonward-read-desktop \
    -p 6080:6080 \
    -v "$PWD/workonward-read-files:/files" \
    workonward-read-desktop

cat <<EOF

============================================================
 WorkOnward Read is running. Open this in your browser:

   http://localhost:6080/vnc.html?autoconnect=true&resize=scale

 Put PDFs/images in ./workonward-read-files to open them from inside
 the app (they appear at /files in the file dialog), and your
 redacted exports saved there show up on your Mac too.

 Stop it with:   docker rm -f workonward-read-desktop
============================================================
EOF
