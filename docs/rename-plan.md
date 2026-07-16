# Rebrand: CoverUP → WorkOnward Read

Binding naming map for the rename sweep (executed after wave-1 code lands, before wave-2
integration). GPL-3.0 note: this is a rebranded fork — all original copyright notices
("(c) 2024 - 2026 Björn Seipel") stay in source headers, and README/About credit
"WorkOnward Read is based on CoverUP by Björn Seipel (digidigital)".

| Item | Old | New |
|---|---|---|
| Product display name | CoverUP / CoverUP PDF | WorkOnward Read |
| Python package dir | `coverup/` | `workonward_read/` |
| Imports | `from coverup.x import y` | `from workonward_read.x import y` |
| Launcher script | `CoverUP.py` | `WorkOnwardRead.py` |
| pyproject `[project] name` | `coverup-pdf` | `workonward-read` |
| Console scripts | `coverup`, `coverup-gui` | `workonward-read`, `workonward-read-gui` |
| Windows exe / PyInstaller name | `CoverUP` | `WorkOnwardRead` (display: WorkOnward Read) |
| macOS app | `CoverUP.app` | `WorkOnward Read.app` (CFBundleName "WorkOnward Read") |
| Bundle identifier | `de.digidigital.coverup` | `org.workonward.read` |
| Icons | `CoverUP.ico/.svg/.icns` | `WorkOnwardRead.ico/.svg/.icns` |
| appdirs data dir | `user_data_dir('CoverUP','digidigital')` | `user_data_dir('WorkOnwardRead','WorkOnward')` |
| Installer artifact | `CoverUP-Setup-<v>-x64.exe` | `WorkOnwardRead-Setup-<v>-x64.exe` |
| DMG artifacts | `CoverUP-<v>-macOS-<arch>.dmg` | `WorkOnwardRead-<v>-macOS-<arch>.dmg` |
| Inno AppId GUID | existing | NEW GUID (different product identity) |
| WM_CLASS | `coverup` | `workonward-read` |
| translations `app_title*` | "CoverUP" in all 26 languages | "WorkOnward Read" (product-name token replace) |
| Flatpak/Snap/AppImage ids | `de.digidigital.coverup.*`, `coverup` | `org.workonward.read.*`, `workonward-read` |
| Web frontend title/UI | CoverUP | WorkOnward Read |
| Web image | `ghcr.io/rahvis/coverup-web` | `ghcr.io/rahvis/workonward-read-web` |
| Web service names | `coverup-web` | `workonward-read-web` |
| Docs (README, packaging/README, dev docs) | CoverUP | WorkOnward Read (+fork credit) |

Unchanged: repo checkout folder name (local path), GPL-3.0 license, original copyright
headers, deploy domain/host secrets, `docs/acrobat-feature-research.md` history references
to CoverUP's origins where factual ("CoverUP's core redaction pipeline" → reword to
"the app's core redaction pipeline" where trivial).

Order of operations:
1. `git mv` package dir, launcher, icons; rewrite imports (`coverup.` → `workonward_read.`).
2. String sweep: UI titles, translations product token, About text, workfile appdirs.
3. Packaging sweep: spec, iss (new GUID), macOS scripts, release.yml artifact names.
4. Linux packaging + web sweep.
5. Docs sweep + fork attribution.
6. Full test suite + `--version` smoke (`WorkOnward Read <version>`).

Status: EXECUTED 2026-07-16
