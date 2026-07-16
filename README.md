# WorkOnward Read

![WorkOnward Read logo](WorkOnwardRead.svg)

**WorkOnward Read** is a privacy-first, offline, open-source PDF editor and
redaction suite. It permanently removes sensitive content from PDFs and images
by rasterizing pages and burning redaction bars into the pixels — nothing to
"un-hide" later — and adds a full toolbox around it: annotate, sign, organize
pages, convert, OCR, compare, and batch-process documents. Everything runs
locally on your machine; no cloud services, no telemetry, no account.

## Features

### Redact & Protect

- **Permanent redaction** — draw black or white bars over anything sensitive; pages are rasterized and the bars become part of the image, so covered content is gone for good.
- **Sanitize documents** — strip metadata (XMP and document info), embedded JavaScript, embedded files, and auto-run actions in one step.
- **Password protection** — encrypt PDFs with AES-256 (open password and/or owner password).
- **Permission restrictions** — allow or deny printing, copying, and modification via owner-password permission flags.
- **Remove security** — decrypt a PDF when you know its password.

### Annotate & Sign

- **Highlight, underline, strikethrough** — mark up text passages.
- **Freehand drawing** — pencil tool for hand-drawn marks.
- **Shapes** — rectangles, ellipses, lines, and arrows.
- **Stamps** — Approved / Draft / custom stamps.
- **Text boxes & typewriter** — place text anywhere on a page.
- **Insert images** — drop an image onto any page.
- **Fill & Sign** — type, draw, or place an image signature, burned in on export.
- **Certificate-based digital signatures** — sign with a PKCS#12 (.p12) certificate via pyHanko, invisible or visible signature box.
- **Signature validation** — integrity and certificate-chain report for signed PDFs.
- **Form filling** — fill AcroForm fields losslessly, with optional flattening.
- **Measure** — simple ruler tool (points to cm/in via the page size).

### Organize Pages

- **Merge PDFs** — combine multiple documents losslessly.
- **Delete, reorder, rotate** — full page management with thumbnails.
- **Split & extract** — split by page ranges or pull out selected pages.
- **Insert pages** — from another PDF or blank pages.
- **Crop pages** — trim page margins.
- **Watermarks** — text or image watermarks.
- **Headers & footers** — repeatable page furniture.
- **Page numbering & Bates numbering** — sequential stamps for legal workflows.

### Convert & Export

- **Images to PDF** — turn PNG/JPG files (one or many) into a PDF.
- **PDF to Word (.docx)** — paragraph-level text reconstruction.
- **PDF to text, HTML, or images** — export content in open formats.
- **Compress** — raster mode (flatten + downsample) or lossless mode (image recompression + stream compression).
- **Export single pages** — save the current page as an image.

### OCR

- **Searchable PDFs** — recognize text in scans via Tesseract (auto-detected when installed; see [OCR prerequisites](#ocr-prerequisites)).

### Review & Compare

- **Compare two PDFs** — page-image difference detection with a side-by-side report.
- **Text search** — find text across the document with hit navigation.
- **Document properties** — view and edit title, author, and other metadata.

### Batch

- **Batch processing** — apply a tool across a whole folder of documents in one run.

## Security model

Redaction in WorkOnward Read is destructive by design: every page is
rasterized to an image, redaction bars are painted as solid pixels onto that
image, and a brand-new PDF is rebuilt from the images with no text layer.
Covered content cannot be recovered by copy/paste, `pdftotext`, "remove the
overlay", or OCR of the redacted area — the pixels underneath are simply
overwritten. Because the output is a fresh file, hidden layers, annotations,
and attachments from the source do not carry over. The separate **Sanitize**
tool strips metadata, JavaScript, embedded files, and auto-run actions from
PDFs you don't want to rasterize, and password protection uses **AES-256**
encryption.

## Downloads

Grab the latest release from the
[GitHub Releases page](https://github.com/rahvis/redact/releases/latest).
Each release ships:

- **`WorkOnwardRead-Setup-<version>-x64.exe`** — Windows 10/11 (64-bit) installer; per-user by default, no admin rights required.
- **`WorkOnwardRead-<version>-macOS-arm64.dmg`** — macOS on Apple Silicon (M1 and newer).
- **`WorkOnwardRead-<version>-macOS-x86_64.dmg`** — macOS on Intel processors.
- **`SHA256SUMS.txt`** — checksums for verifying your download.

### Install on Windows

1. Download and run the installer.
2. If SmartScreen shows "Windows protected your PC", click **More info → Run anyway** (the app is open source but not code-signed with a paid certificate).
3. Silent install for scripted deployment: `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART`.

### Install on macOS

1. Open the DMG and drag **WorkOnward Read.app** to **Applications**.
2. First launch: right-click the app and choose **Open**, then **Open** again. On macOS 15+ use **System Settings → Privacy & Security → Open Anyway** instead. Full instructions ship inside the DMG (`README-Open-Me-First.txt`).

### Verify your download

```bash
shasum -a 256 <file>                 # macOS
CertUtil -hashfile <file> SHA256     # Windows
```

Compare the output against the matching line in `SHA256SUMS.txt`.

### Linux

Build recipes for Snap (`snapcraft.yaml`), AppImage (`appimage/`), and
Flatpak (`flatpak/`) are included in the repository, or run from source
(below).

## Web app

A browser-based version of the redaction workflow lives in [`web/`](web/):
upload a PDF, drag redaction bars over sensitive content, and download a
flattened, permanently redacted file. Processing happens in memory on your own
instance — files are never written to disk or sent anywhere else.

Self-host it with Docker:

```bash
cd web
docker compose up --build   # → http://localhost:8090
```

See [`web/README.md`](web/README.md) for the architecture and API, and
[`web/deploy/README.md`](web/deploy/README.md) for production deployment.

## Run from source

Requirements: **Python 3.13** with **Tcl/Tk 8.6** (Tcl 9 is not yet supported
by the GUI toolkit — see `docs/tcl9-migration.md`).

```bash
git clone https://github.com/rahvis/redact.git
cd redact

python -m venv .venv && source .venv/bin/activate
pip install -e .

# launch the GUI
python -m workonward_read
# or:
workonward-read document.pdf     # open a file directly
workonward-read --version
```

## Build installers locally

The Windows installer and macOS DMGs can be built on your own machine — see
[`packaging/README.md`](packaging/README.md) for one-shot build scripts,
requirements, and the CI release pipeline.

## OCR prerequisites

OCR features use [Tesseract](https://github.com/tesseract-ocr/tesseract),
which is not bundled. Install it once and WorkOnward Read detects it
automatically:

- **Windows:** installer from [UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki)
- **macOS:** `brew install tesseract`
- **Debian/Ubuntu:** `sudo apt install tesseract-ocr`
- **Fedora:** `sudo dnf install tesseract`

## Languages

The app UI is translated into **26 languages** (English, German, Spanish,
French, Italian, Portuguese, Romanian, Dutch, Swedish, Danish, Norwegian,
Icelandic, Polish, Czech, Slovak, Bulgarian, Serbian, Croatian, Slovenian,
Greek, Turkish, Lithuanian, Latvian, Estonian, Chinese, and Hindi) and
follows your system language automatically. Translations live in
`workonward_read/translations.py`.

## License & credits

This project is licensed under the **GPL-3.0** — see [LICENSE](LICENSE).

WorkOnward Read is based on open-source software by Björn Seipel
([digidigital](https://digidigital.de)), GPL-3.0. All original copyright
notices — (c) 2024 - 2026 Björn Seipel — are preserved in the source headers.
Support the original author: [buy him a pizza!](https://buymeacoffee.com/digidigital)

Open-source libraries used:

- [FreeSimpleGUI](https://github.com/spyoungtech/FreeSimpleGui) — GUI framework
- [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) — PDF rendering
- [pypdf](https://github.com/py-pdf/pypdf) — lossless PDF operations
- [fpdf2](https://py-pdf.github.io/fpdf2/) — PDF creation
- [Pillow](https://python-pillow.org/) — image processing
- [pyHanko](https://github.com/MatthiasValvekens/pyHanko) — digital signatures
- [Material Symbols](https://fonts.google.com/icons) — UI icons

## Contributing

Bug reports and pull requests are welcome at
[github.com/rahvis/redact](https://github.com/rahvis/redact/issues). Please
run the test suite (`pytest tests/`) before submitting changes.
