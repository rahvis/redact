# CoverUP desktop packaging

This directory contains everything needed to build the Windows and macOS
desktop releases of CoverUP. Linux packaging lives elsewhere
(`snapcraft.yaml`, `appimage/`, `flatpak/`).

## Layout

| Path | Purpose |
|---|---|
| `coverup.spec` | Cross-platform PyInstaller spec (onedir; `.app` bundle on macOS) |
| `constraints.txt` | Shared build constraints (PyInstaller floor) |
| `constraints-macos-x86_64.txt` | Intel-mac-only pin: `cryptography>=48,<49` (no Intel wheels for >=49) |
| `windows/installer.iss` | Inno Setup 6 script (x64 installer) |
| `windows/build_installer.ps1` | One-shot Windows build: venv → PyInstaller → smoke test → ISCC |
| `macos/make_icns.sh` | Regenerates `macos/CoverUP.icns` from `CoverUP.svg` (needs librsvg) |
| `macos/build_app.sh` | One-shot macOS build: venv → PyInstaller → ad-hoc codesign → smoke test |
| `macos/build_dmg.sh` | Stages `CoverUP.app` + `/Applications` symlink + readme into a UDZO DMG |
| `macos/README-Open-Me-First.txt` | Gatekeeper instructions shipped inside the DMG |
| `macos/entitlements.plist` | Hardened-runtime entitlements for the (dormant) notarization path |

## Requirements (all platforms)

- **Python 3.13** with **Tcl/Tk 8.6**. Tcl 9 breaks FreeSimpleGUI
  (see `docs/tcl9-migration.md`); every build script gates on:

  ```
  python -c "import tkinter,sys; v=str(tkinter.Tcl().call('info','patchlevel')); sys.exit(0 if v.startswith('8.6') else 1)"
  ```

- All installs use `pip install --only-binary :all:` so a missing wheel
  fails loudly instead of silently compiling an sdist.
- Tesseract OCR is **not** bundled; OCR features require a system install.
- No universal2 macOS build: pypdfium2 and Pillow ship no universal2
  wheels, so arm64 and x86_64 are built separately.

## Building locally

### Windows installer

Prerequisites: Python 3.13 (`py -3.13`), [Inno Setup 6](https://jrsoftware.org/isinfo.php).

```powershell
# from the repo root
powershell -ExecutionPolicy Bypass -File packaging\windows\build_installer.ps1
# custom interpreter / ISCC location:
#   ... build_installer.ps1 -Python "C:\Python313\python.exe" -Iscc "D:\Inno Setup 6\ISCC.exe"
```

Output: `Output\CoverUP-Setup-<version>-x64.exe`.

Silent install flags (for scripted deployment):

```
CoverUP-Setup-<version>-x64.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

### macOS app + DMG

Prerequisites: python.org Python 3.13 (framework build, ships Tcl/Tk 8.6).
The icon is committed; regenerate it only after changing `CoverUP.svg`
(`brew install librsvg`, then `bash packaging/macos/make_icns.sh`).

```bash
# from the repo root — builds for the native arch (arm64 or x86_64)
bash packaging/macos/build_app.sh          # -> dist/CoverUP.app
bash packaging/macos/build_dmg.sh          # -> dist/CoverUP-<ver>-macOS-<arch>.dmg
# reuse an existing dist/CoverUP.app:
SKIP_BUILD=1 bash packaging/macos/build_dmg.sh
# use a different interpreter:
PYTHON=$(which python3.13) bash packaging/macos/build_app.sh
```

## CI overview (`.github/workflows/release.yml`)

Triggered by pushing a `v*` tag (or manually via *Run workflow*):

1. **test** (Ubuntu): runs the test suite under `xvfb`, and on tag pushes
   verifies the tag matches both `coverup.__version__` and the
   `pyproject.toml` version.
2. **build-windows** (windows-latest): PyInstaller build, frozen
   `--version` smoke test, Inno Setup compile, then a silent-install
   smoke test of the finished installer.
3. **build-macos** (matrix: `macos-14` = arm64, `macos-15-intel` = x86_64):
   `build_app.sh` + `build_dmg.sh` per architecture.
4. **release**: collects all artifacts, writes `SHA256SUMS.txt`, and
   creates a **draft** GitHub release — publish manually after checking
   the artifacts.

## Signing status

- **Windows / SmartScreen:** the installer is unsigned, so SmartScreen
  shows "Windows protected your PC" on new downloads. Users click
  **More info → Run anyway**. The reputation warning fades as download
  counts grow; a code-signing certificate would remove it (a commented
  `SignTool` line is ready in `installer.iss`).
- **macOS / Gatekeeper:** the app is ad-hoc signed but not notarized
  (no paid Apple Developer account). First launch requires right-click →
  Open (macOS 13/14) or System Settings → Privacy & Security →
  **Open Anyway** (macOS 15+). Full instructions ship in the DMG as
  `README-Open-Me-First.txt`. When notarization becomes possible, sign
  with the hardened runtime plus `macos/entitlements.plist`.

Every release includes `SHA256SUMS.txt` so downloads can be verified:

```
shasum -a 256 <file>      # macOS
CertUtil -hashfile <file> SHA256   # Windows
```
