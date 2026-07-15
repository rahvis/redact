"""
CoverUP Web — FastAPI backend.

Serves the redaction API and (in production) the built React frontend from a
single origin, so the whole product lives at ONE URL (e.g. http://localhost:8080).

Endpoints
---------
GET  /api/health              -> liveness probe
POST /api/redact              -> multipart upload; streams back the redacted PDF

The redact request is stateless: the uploaded PDF is processed in memory,
streamed back, and dropped — no database, no logging of file bytes. (Starlette
spools multipart parts >1 MB to a temp file during parsing; the container mounts
/tmp as tmpfs so that spillover stays in RAM, off the physical disk.)
"""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from redaction import (
    PasswordRequired,
    RedactionError,
    Region,
    redact_pdf,
)

# 200 MB upload ceiling — generous for scanned docs, bounded to avoid OOM.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024

app = FastAPI(title="CoverUP Web", version="1.0.0")

# CORS only matters in dev (Vite on :5173 hitting the API on :8080). In the
# single-container build the frontend is same-origin, so this is a no-op there.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def reject_oversized(request: Request, call_next):
    """Reject too-large uploads by Content-Length BEFORE the body is buffered."""
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            if int(cl) > MAX_UPLOAD_BYTES + 1024 * 1024:  # + slack for multipart overhead
                return JSONResponse(
                    status_code=413,
                    content={"detail": "File exceeds the 200 MB limit."},
                )
        except ValueError:
            pass
    return await call_next(request)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "coverup-web"}


def _parse_regions(raw: str) -> list[Region]:
    try:
        data: Any = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid regions JSON: {exc}") from exc

    if not isinstance(data, list):
        raise HTTPException(status_code=400, detail="regions must be a JSON array")

    regions: list[Region] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail=f"regions[{i}] must be an object")
        try:
            regions.append(
                Region(
                    page=int(item["page"]),
                    x=float(item["x"]),
                    y=float(item["y"]),
                    w=float(item["w"]),
                    h=float(item["h"]),
                    color="white" if str(item.get("color", "black")).lower() == "white" else "black",
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400, detail=f"regions[{i}] is malformed: {exc}"
            ) from exc
    return regions


@app.post("/api/redact")
async def redact(
    file: UploadFile = File(...),
    regions: str = Form("[]"),
    quality: str = Form("high"),
    password: str = Form(""),
) -> Response:
    filename = file.filename or "document.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(pdf_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds the 200 MB limit.")

    parsed_regions = _parse_regions(regions)
    quality = quality if quality in ("high", "compressed") else "high"

    try:
        # Redaction is CPU-bound (rasterize + encode); run it off the event
        # loop. pdfium access inside is serialized by a lock in redaction.py.
        redacted = await run_in_threadpool(
            redact_pdf,
            pdf_bytes,
            parsed_regions,
            quality,
            password or None,
        )
    except PasswordRequired as exc:
        # 401 signals the frontend to prompt for (or re-ask) the password.
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except RedactionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return Response(
        content=redacted,
        media_type="application/pdf",
        headers={
            "Content-Disposition": _content_disposition(filename),
            "Content-Length": str(len(redacted)),
            "Cache-Control": "no-store",
        },
    )


def _redacted_name(original: str) -> str:
    base = os.path.basename(original)
    stem = base[:-4] if base.lower().endswith(".pdf") else base
    return f"{stem}_redacted.pdf"


def _content_disposition(original: str) -> str:
    """Build a safe Content-Disposition header for the redacted file.

    The upload filename is attacker-controlled, so we must not let quotes,
    backslashes, control chars, or non-ASCII bytes break out of / corrupt the
    header. We emit an ASCII-sanitized `filename="..."` plus an RFC 5987
    `filename*` carrying the exact (possibly Unicode) name.
    """
    name = _redacted_name(original)
    # Strip control characters entirely.
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    # ASCII fallback: replace quotes/backslashes/non-ASCII with underscores.
    ascii_name = re.sub(r'[^\x20-\x7e]', "_", name).replace('"', "_").replace("\\", "_")
    if not ascii_name.strip() or not ascii_name.lower().endswith(".pdf"):
        ascii_name = "redacted.pdf"
    encoded = quote(name, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


# --- Static frontend (production single-container build) --------------------
# The Docker build copies the compiled React app to ./static. When present, we
# mount it at the root so the SPA and API share one origin. In local dev this
# folder does not exist and the block is skipped (Vite serves the frontend).
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
