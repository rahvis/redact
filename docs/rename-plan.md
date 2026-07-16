# Rebrand: previous product name → WorkOnward Read

Binding naming map for the rename sweep (executed after wave-1 code lands, before wave-2
integration). GPL-3.0 note: this is a rebranded fork — all original copyright notices
("(c) 2024 - 2026 Björn Seipel") stay in source headers, and README/About credit
"WorkOnward Read is based on open-source software by Björn Seipel (digidigital), GPL-3.0".

| Item | Old | New |
| --- | --- | --- |
| Product display name | (previous name) | WorkOnward Read |
| Python package dir | (previous name)/ | `workonward_read/` |
| Imports | `from <previous name>.x import y` | `from workonward_read.x import y` |
| Launcher script | (previous name).py | `WorkOnwardRead.py` |
| pyproject `[project] name` | (previous name)-pdf | `workonward-read` |
| Console scripts | (previous name), (previous name)-gui | `workonward-read`, `workonward-read-gui` |
| Windows exe / PyInstaller name | (previous name) | `WorkOnwardRead` (display: WorkOnward Read) |
| macOS app | (previous name).app | `WorkOnward Read.app` (CFBundleName "WorkOnward Read") |
| Bundle identifier | de.digidigital.(previous name) | `org.workonward.read` |
| Icons | (previous name).ico/.svg/.icns | `WorkOnwardRead.ico/.svg/.icns` |
| appdirs data dir | `user_data_dir('(previous name)','digidigital')` | `user_data_dir('WorkOnwardRead','WorkOnward')` |
| Installer artifact | (previous name)-Setup-\<v\>-x64.exe | `WorkOnwardRead-Setup-<v>-x64.exe` |
| DMG artifacts | (previous name)-\<v\>-macOS-\<arch\>.dmg | `WorkOnwardRead-<v>-macOS-<arch>.dmg` |
| Inno AppId GUID | existing | NEW GUID (different product identity) |
| WM_CLASS | (previous name) | `workonward-read` |
| translations `app_title*` | previous product name in all 26 languages | "WorkOnward Read" (product-name token replace) |
| Flatpak/Snap/AppImage ids | de.digidigital.(previous name).*, (previous name) | `org.workonward.read.*`, `workonward-read` |
| Web frontend title/UI | (previous name) | WorkOnward Read |
| Web image | ghcr.io/rahvis/(previous name)-web | `ghcr.io/rahvis/workonward-read-web` |
| Web service names | (previous name)-web | `workonward-read-web` |
| Docs (README, packaging/README, dev docs) | (previous name) | WorkOnward Read (+fork credit) |

Unchanged: repo checkout folder name (local path), GPL-3.0 license, original copyright
headers, deploy domain/host secrets. Historical references to the previous product name
in prose were reworded ("the previous product's core redaction pipeline" → "the app's
core redaction pipeline" where trivial).

Order of operations:

1. `git mv` package dir, launcher, icons; rewrite imports (previous package name → `workonward_read.`).
2. String sweep: UI titles, translations product token, About text, workfile appdirs.
3. Packaging sweep: spec, iss (new GUID), macOS scripts, release.yml artifact names.
4. Linux packaging + web sweep.
5. Docs sweep + fork attribution.
6. Full test suite + `--version` smoke (`WorkOnward Read <version>`).

Status: EXECUTED 2026-07-16
