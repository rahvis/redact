# WorkOnward Read — PDF Redaction Software

![WorkOnward Read logo](WorkOnwardRead.svg)

**WorkOnward Read** is a free software, developed in Python, designed to provide a secure and straightforward method for redacting PDF files. It enables users to conceal sensitive text passages by overlaying them with black or white bars.

Users can import PDF documents into WorkOnward Read, which are then converted into images. This conversion process ensures that the text cannot be copied from the document or indexed without OCR, enhancing the security of your information. Additionally, invisible layers within the PDF are not converted, providing an extra layer of security.

It also supports the import of PNG and JPG files, in addition to PDFs.

Given that image-based PDFs can become quite large, **WorkOnward Read** offers two modes: a high-quality mode that maintains the visual fidelity of the document, and a compressed mode that reduces file size at the expense of some visual quality.

Whether you're dealing with a single page or an entire document, **WorkOnward Read** provides a flexible and easy solution for all your PDF redaction needs.

## Credits — based on CoverUP

**WorkOnward Read is based on [CoverUP](https://github.com/digidigital/CoverUP) by Björn Seipel ([digidigital](https://digidigital.de)), GPL-3.0.**
All original copyright notices are preserved in the source headers. Support the
original author: [Buy him a pizza!](https://buymeacoffee.com/digidigital) 👍

---

![A screenshot of PDF redaction Software | Ein Screenshot der Software zum Schwärzen von PDF-Dokumenten](https://raw.githubusercontent.com/digidigital/CoverUP/main/Screenshots/CoverUP_screenshot.png)

---

## Features

- Import PDF, PNG, and JPG files
- Draw black or white redaction bars over sensitive content
- Password-protected PDF support
- High-quality and compressed export modes
- Session persistence - continue where you left off
- Undo functionality for corrections
- Zoom in/out for precise redaction
- Command-line file argument support
- Export single pages or entire documents
- **Multi-language support** (25 languages including English, German, Spanish, French, Chinese, and more)

## Installation

### Linux - Snap Store (upstream CoverUP)

The Snap Store channel below ships the **upstream CoverUP** app by digidigital,
not WorkOnward Read. To install WorkOnward Read on Linux, build the snap from
this repository (`snapcraft.yaml`) or use the AppImage/Flatpak recipes in
`appimage/` and `flatpak/`.

[![Get it from the Snap Store](https://snapcraft.io/static/images/badges/en/snap-store-black.svg)](https://snapcraft.io/coverup)

```bash
sudo snap install coverup   # upstream CoverUP, not WorkOnward Read
```

### Python Package (pip)

```bash
# WorkOnward Read (this project), from a checkout of this repository:
pip install .

# The upstream CoverUP package remains available on PyPI as:
pip install coverup-pdf
```

### Windows / Other

[Windows Installer and other download options](https://github.com/rahvis/redact/releases/latest)

## Downloads (Windows & macOS)

Each GitHub release ships three desktop artifacts:

- **`WorkOnwardRead-Setup-<version>-x64.exe`** — Windows 64-bit installer (per-user by default, no admin required). Silent install: `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART`
- **`WorkOnwardRead-<version>-macOS-arm64.dmg`** — macOS on Apple Silicon (M1 and newer)
- **`WorkOnwardRead-<version>-macOS-x86_64.dmg`** — macOS on Intel processors

**First-launch warnings** (the app is open source and not code-signed with a paid certificate):

- **Windows SmartScreen:** if you see "Windows protected your PC", click **More info → Run anyway**.
- **macOS Gatekeeper:** right-click the app and choose **Open**, then **Open** again. On macOS 15+ you may instead need **System Settings → Privacy & Security → Open Anyway**. Full instructions are included in the DMG (`README-Open-Me-First.txt`).

**Verify your download:** every release includes a `SHA256SUMS.txt`; compare it with `shasum -a 256 <file>` (macOS) or `CertUtil -hashfile <file> SHA256` (Windows).

**Build it yourself:** see [`packaging/README.md`](packaging/README.md) for local Windows/macOS build instructions.

## Usage

### Graphical Interface

Simply launch **WorkOnward Read** and use the toolbar to:
1. Open a PDF or image file
2. Draw redaction bars by clicking and dragging
3. Use the eraser tool to remove bars
4. Save the redacted document

### Command Line

```bash
# Open a file directly
workonward-read document.pdf

# Open an image
workonward-read screenshot.png

# Show version
workonward-read --version
```

## Development

### Requirements

- Python 3.9+
- Dependencies listed in `requirements.txt`

### Setup

```bash
# Clone the repository
git clone https://github.com/rahvis/redact.git
cd redact

# Install dependencies
pip install -r requirements.txt

# Run from source
python WorkOnwardRead.py

# Or install as package
pip install -e .
workonward-read
```

### Building Packages

#### Python Package (PyPI)

```bash
# Install build tools
pip install build twine

# Build the package
python -m build

# Upload to PyPI (requires PyPI credentials)
twine upload dist/*
```

#### Windows (PyInstaller)

```bash
# Install PyInstaller
pip install pyinstaller

# Build (onedir; also used by the Windows installer and macOS app)
pyinstaller packaging/workonward_read.spec --noconfirm
```

### Internationalization (i18n)

WorkOnward Read supports 25 languages. The UI automatically detects the system language and displays translations accordingly.

**Supported languages:** English, German, Spanish, French, Italian, Portuguese, Romanian, Dutch, Swedish, Danish, Norwegian, Icelandic, Polish, Czech, Slovak, Bulgarian, Serbian, Croatian, Slovenian, Greek, Turkish, Lithuanian, Latvian, Estonian, Chinese, Hindi

Translations are stored in `workonward_read/translations.py`. To add or modify translations, edit the `TRANSLATIONS` dictionary in that file.

## License

This project is licensed under the GPL-3.0 License - see the [LICENSE](LICENSE) file for details.

WorkOnward Read is based on CoverUP by Björn Seipel (digidigital), GPL-3.0.
Original copyright: (c) 2024 - 2026 Björn Seipel.

## FOSS Credits

- [CoverUP](https://github.com/digidigital/CoverUP) - the original application this project is based on
- [FreeSimpleGUI](https://github.com/spyoungtech/FreeSimpleGui) - GUI framework
- [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) - PDF rendering
- [fpdf2](https://py-pdf.github.io/fpdf2/) - PDF creation
- [Pillow](https://python-pillow.org/) - Image processing
- [Material Symbols](https://fonts.google.com/icons) - UI icons

---

# Schwärzen von PDF Dokumenten mit WorkOnward Read

**WorkOnward Read** ist eine kostenlose Software, die in Python entwickelt wurde, um eine sichere und unkomplizierte Methode zur Schwärzung von PDF-Dateien bereitzustellen. Sie ermöglicht es den Benutzern, sensible Textpassagen zu verbergen, indem sie diese mit schwarzen oder weißen Balken überlagern.

Benutzer können PDF-Dokumente in **WorkOnward Read** importieren, die dann in Bilder umgewandelt werden. Dieser Umwandlungsprozess stellt sicher, dass der Text nicht ohne zusätzliche Texterkennung kopiert oder indexiert werden kann, was die Sicherheit der Informationen erhöht. Zusätzlich werden unsichtbare Schichten innerhalb der PDF nicht konvertiert, was eine zusätzliche Sicherheitsebene gegen versehentliche Veröffentlichung bietet.

Es unterstützt auch den Import von PNG- und JPG-Dateien, zusätzlich zu PDFs.

Da bildbasierte PDFs recht groß werden können, bietet WorkOnward Read zwei Exportoptonen an: einen Modus in hoher Qualität, der die visuelle Genauigkeit des Dokuments weitestgehend beibehält, und einen komprimierten Modus, der die Dateigröße der exportierten PDF-Datei auf Kosten von visueller Qualität reduziert.

Ob Sie mit einer einzelnen Seite oder einem gesamten Dokument arbeiten, **WorkOnward Read** bietet eine flexible und einfache Lösung für alle Ihre Bedürfnisse zur Schwärzung von PDFs.

**WorkOnward Read basiert auf [CoverUP](https://github.com/digidigital/CoverUP) von Björn Seipel ([digidigital](https://digidigital.de)), GPL-3.0.**

## Installation

### Linux - Snap Store (Original CoverUP)

Der folgende Snap-Store-Kanal liefert das **Original CoverUP** von digidigital,
nicht WorkOnward Read:

[![Get it from the Snap Store](https://snapcraft.io/static/images/badges/en/snap-store-black.svg)](https://snapcraft.io/coverup)

```bash
sudo snap install coverup   # Original CoverUP, nicht WorkOnward Read
```

### Python-Paket (pip)

```bash
# WorkOnward Read (dieses Projekt), aus diesem Repository:
pip install .

# Das Original-CoverUP-Paket bleibt auf PyPI verfügbar als:
pip install coverup-pdf
```

### Windows / Andere

[Windows Installer und andere Downloadoptionen](https://github.com/rahvis/redact/releases/latest)
