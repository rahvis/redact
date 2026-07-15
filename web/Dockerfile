# CoverUP Web — full-stack PDF redaction, one image, one URL.
#
# Stage 1 builds the React/Vite frontend to static assets.
# Stage 2 is a slim Python runtime that runs the FastAPI redaction API AND
# serves those static assets from the same origin (http://localhost:8080).

# ---- Stage 1: build the frontend ----
FROM node:20-slim AS frontend
WORKDIR /web
COPY frontend/package.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build          # emits /web/dist (self-contained, bundled pdf.js worker)

# ---- Stage 2: python runtime (API + static SPA) ----
FROM python:3.12-slim AS runtime

# fonts-dejavu helps pdfium rasterize pages whose fonts are not embedded.
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app.py backend/redaction.py ./
# The compiled SPA is served by FastAPI at "/" (see app.py static mount).
COPY --from=frontend /web/dist ./static

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
