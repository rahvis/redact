"""Real-browser end-to-end test of the CoverUP Web UI (Playwright).

Drives the actual React + pdf.js app: uploads a PDF, waits for it to render,
drags a redaction bar over the secret, clicks Redact & Download, then proves the
downloaded PDF has no extractable secret. Screenshots are saved to /out.
"""

import io
import os
import sys

from playwright.sync_api import sync_playwright
from pypdf import PdfReader

BASE = os.environ.get("BASE_URL", "http://host.docker.internal:8090")
OUT = os.environ.get("OUT_DIR", "/out")
SAMPLE = os.environ.get("SAMPLE_PDF", f"{OUT}/sample.pdf")
SECRET = "TOPSECRET-SSN-123-45-6789"


def main() -> int:
    results = []

    def check(name, cond):
        results.append((name, cond))
        print(f"  [{'OK' if cond else 'FAIL'}] {name}")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1000}, device_scale_factor=2)
        page.goto(BASE, wait_until="networkidle")

        # 1. empty state renders
        page.wait_for_selector(".dropzone", timeout=15000)
        page.screenshot(path=f"{OUT}/01_dropzone.png")
        check("dropzone (empty state) rendered", page.locator(".dropzone").count() == 1)

        # 2. upload the PDF via the hidden file input
        page.set_input_files("input[type=file]", SAMPLE)

        # 3. pdf.js renders the page to a canvas with real dimensions
        page.wait_for_selector(".page-canvas", timeout=20000)
        page.wait_for_function(
            "() => { const c = document.querySelector('.page-canvas'); return c && c.width > 100 && c.height > 100; }",
            timeout=20000,
        )
        page.wait_for_timeout(600)
        page.screenshot(path=f"{OUT}/02_rendered.png")
        canvas_ok = page.evaluate(
            "() => { const c = document.querySelector('.page-canvas'); return {w:c.width, h:c.height}; }"
        )
        check(f"pdf.js rendered a page canvas {canvas_ok}", canvas_ok["w"] > 100 and canvas_ok["h"] > 100)
        check("toolbar shown after load", page.locator(".toolbar").count() == 1)

        # 4. draw a redaction bar over the secret (top region), via mouse drag on the overlay
        box = page.locator(".overlay").first.bounding_box()
        x0 = box["x"] + box["width"] * 0.10
        y0 = box["y"] + box["height"] * 0.10
        x1 = box["x"] + box["width"] * 0.90
        y1 = box["y"] + box["height"] * 0.19
        page.mouse.move(x0, y0)
        page.mouse.down()
        page.mouse.move((x0 + x1) / 2, (y0 + y1) / 2)
        page.mouse.move(x1, y1)
        page.mouse.up()
        page.wait_for_selector(".region", timeout=5000)
        page.screenshot(path=f"{OUT}/03_bar_drawn.png")
        check("a redaction bar was drawn", page.locator(".region").count() >= 1)
        check("toolbar reflects 1 bar", "1 bar" in page.locator(".tb-count").inner_text())

        # 5. Redact & Download -> capture the file
        with page.expect_download(timeout=30000) as dl_info:
            page.get_by_role("button", name="Redact & Download").click()
        download = dl_info.value
        out_path = f"{OUT}/redacted.pdf"
        download.save_as(out_path)
        check("download filename ends _redacted.pdf", download.suggested_filename.endswith("_redacted.pdf"))
        page.wait_for_timeout(400)
        page.screenshot(path=f"{OUT}/04_after_redact.png")

        # 6. the downloaded PDF must not leak the secret and must have no text layer
        with open(out_path, "rb") as f:
            data = f.read()
        text = "".join(pg.extract_text() or "" for pg in PdfReader(io.BytesIO(data)).pages)
        check("downloaded PDF has NO extractable secret", SECRET not in text)
        check("downloaded PDF has no text layer", text.strip() == "")
        check("downloaded PDF is non-trivial in size", len(data) > 3000)

        browser.close()

    passed = sum(1 for _, c in results if c)
    print(f"\n{passed}/{len(results)} UI checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
