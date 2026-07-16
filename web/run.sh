#!/usr/bin/env bash
# Build and run WorkOnward Read Web, then print the URL.
set -euo pipefail
cd "$(dirname "$0")"

# Host port (override: PORT=9000 ./run.sh). Defaults to 8090.
PORT="${PORT:-8090}"

echo ">> Building workonward-read-web image (first build compiles the frontend; give it a few min)..."
docker build -t workonward-read-web .

echo ">> (Re)starting container on port ${PORT}..."
docker rm -f workonward-read-web >/dev/null 2>&1 || true
docker run -d --name workonward-read-web -p "${PORT}:8080" --tmpfs /tmp workonward-read-web

cat <<EOF

============================================================
 WorkOnward Read Web is running. Open:

   http://localhost:${PORT}

 Upload a PDF, drag black/white bars over sensitive content,
 then click "Redact & Download". Covered content is flattened
 into an image and permanently removed — nothing is stored.

 Stop it with:  docker rm -f workonward-read-web
============================================================
EOF
