"""End-to-end test against a RUNNING WorkOnward Read Web container.

Builds a source PDF with known secret text, POSTs it to /api/redact with a
redaction region, and proves through the real HTTP path that:
  1. the response is a valid application/pdf,
  2. the secret is NOT extractable from the output (no text layer),
  3. the covered pixels are actually solid black,
  4. page count is preserved,
  5. quality + multi-page + white bars all work.

Usage: BASE_URL=http://host.docker.internal:8080 python _api_e2e_test.py
"""

import io
import os
import sys

import requests
import pypdfium2 as pdfium
from fpdf import FPDF
from pypdf import PdfReader

BASE = os.environ.get("BASE_URL", "http://localhost:8080")
SECRET = "TOPSECRET-SSN-123-45-6789"
VISIBLE = "This line must stay visible."


def make_source_pdf(pages=1) -> bytes:
    pdf = FPDF(unit="pt", format=(612, 792))
    for _ in range(pages):
        pdf.add_page()
        pdf.set_font("Helvetica", size=24)
        pdf.set_xy(72, 100)
        pdf.cell(0, 30, SECRET)
        pdf.set_xy(72, 400)
        pdf.cell(0, 30, VISIBLE)
    return bytes(pdf.output())


def post_redact(pdf_bytes, regions, quality="high"):
    import json
    resp = requests.post(
        f"{BASE}/api/redact",
        files={"file": ("secret.pdf", pdf_bytes, "application/pdf")},
        data={"regions": json.dumps(regions), "quality": quality},
        timeout=60,
    )
    return resp


def extract_text(pdf_bytes):
    return "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf_bytes)).pages)


def check(name, cond):
    status = "OK" if cond else "FAIL"
    print(f"  [{status}] {name}")
    if not cond:
        raise AssertionError(name)


def main() -> int:
    print(f"Target: {BASE}")

    # 0. health
    h = requests.get(f"{BASE}/api/health", timeout=10)
    check("GET /api/health -> 200", h.status_code == 200 and h.json().get("status") == "ok")

    # 1. single-page, black bar over the secret
    src = make_source_pdf(1)
    check("source PDF leaks the secret (sanity)", SECRET in extract_text(src))

    regions = [{"page": 0, "x": 0.05, "y": 0.10, "w": 0.90, "h": 0.10, "color": "black"}]
    r = post_redact(src, regions, "high")
    check("POST /api/redact -> 200", r.status_code == 200)
    check("content-type application/pdf", r.headers.get("content-type", "").startswith("application/pdf"))
    check("attachment filename set", "secret_redacted.pdf" in r.headers.get("content-disposition", ""))

    out = r.content
    out_text = extract_text(out)
    check("redacted output has NO extractable secret", SECRET not in out_text)
    check("redacted output has NO text layer at all", out_text.strip() == "")
    check("output is a valid 1-page PDF", len(PdfReader(io.BytesIO(out)).pages) == 1)

    # verify the bar is solid black in the rendered output
    doc = pdfium.PdfDocument(out)
    img = doc[0].render(scale=2).to_pil().convert("RGB")
    px = img.getpixel((int(0.5 * img.width), int(0.15 * img.height)))
    check(f"redaction bar is solid black (got {px})", max(px) < 20)
    # and content OUTSIDE the bar is NOT all black (page not fully covered)
    below = img.getpixel((int(0.5 * img.width), int(0.60 * img.height)))
    check(f"area outside bar is preserved (got {below})", min(below) > 200)
    doc.close()

    # 2. multi-page + white bar + compressed quality
    src3 = make_source_pdf(3)
    regions3 = [
        {"page": 0, "x": 0.05, "y": 0.10, "w": 0.9, "h": 0.10, "color": "black"},
        {"page": 2, "x": 0.05, "y": 0.10, "w": 0.9, "h": 0.10, "color": "white"},
    ]
    r3 = post_redact(src3, regions3, "compressed")
    check("multi-page redact -> 200", r3.status_code == 200)
    out3 = r3.content
    check("3-page output preserved", len(PdfReader(io.BytesIO(out3)).pages) == 3)
    check("multi-page output has no secret", SECRET not in extract_text(out3))

    doc3 = pdfium.PdfDocument(out3)
    # page 0: black bar
    p0 = doc3[0].render(scale=2).to_pil().convert("RGB")
    check("page0 black bar solid", max(p0.getpixel((int(0.5 * p0.width), int(0.15 * p0.height)))) < 20)
    # page 2: white bar (covers the secret with white)
    p2 = doc3[2].render(scale=2).to_pil().convert("RGB")
    check("page2 white bar solid", min(p2.getpixel((int(0.5 * p2.width), int(0.15 * p2.height)))) > 235)
    doc3.close()

    # 3. bad request handling
    bad = requests.post(
        f"{BASE}/api/redact",
        files={"file": ("x.txt", b"not a pdf", "text/plain")},
        data={"regions": "[]"},
        timeout=30,
    )
    check("non-PDF upload rejected (400)", bad.status_code == 400)

    print("\nALL API E2E CHECKS PASSED ✅  redaction works through the live HTTP service")
    return 0


if __name__ == "__main__":
    sys.exit(main())
