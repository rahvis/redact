<#
.SYNOPSIS
    Build the CoverUP Windows app (PyInstaller onedir) and the Inno Setup installer.

.DESCRIPTION
    Run from the repository root:

        powershell -ExecutionPolicy Bypass -File packaging\windows\build_installer.ps1

    Steps:
      1. Create a build venv (.venv-build) with Python 3.13
      2. pip install the project + PyInstaller (wheels only, constrained)
      3. Verify Tcl/Tk is 8.6 (Tcl 9 breaks FreeSimpleGUI, see docs/tcl9-migration.md)
      4. PyInstaller build via packaging/coverup.spec -> dist\CoverUP\
      5. Smoke test the frozen exe (--version)
      6. Compile the installer with Inno Setup -> Output\CoverUP-Setup-<ver>-x64.exe

.PARAMETER Python
    Command used to create the build venv. Default: 'py -3.13'.

.PARAMETER Iscc
    Path to the Inno Setup compiler. Default: standard Inno Setup 6 location.
#>
[CmdletBinding()]
param(
    [string]$Python = 'py -3.13',
    [string]$Iscc = 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
)

$ErrorActionPreference = 'Stop'

# --- Locate repo root (this script lives in packaging\windows) ---------------
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot '..\..')
Set-Location $RepoRoot
Write-Host "Repo root: $RepoRoot"

# --- Parse version from coverup/__init__.py ----------------------------------
$initContent = Get-Content (Join-Path $RepoRoot 'coverup\__init__.py') -Raw
if ($initContent -notmatch '__version__\s*=\s*["'']([^"'']+)["'']') {
    throw 'Could not parse __version__ from coverup\__init__.py'
}
$Version = $Matches[1]
Write-Host "CoverUP version: $Version"

# --- Create build venv --------------------------------------------------------
$VenvDir = Join-Path $RepoRoot '.venv-build'
Write-Host "Creating build venv at $VenvDir (using: $Python)"
# $Python may be a multi-word command like 'py -3.13'
$pythonParts = $Python -split '\s+'
& $pythonParts[0] @($pythonParts[1..($pythonParts.Count - 1)]) -m venv $VenvDir
if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }

$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'

& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed' }

# Wheels only: an sdist fallback means a missing wheel and must fail loudly.
& $VenvPython -m pip install --only-binary :all: -c packaging\constraints.txt . pyinstaller
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }

# --- Tcl/Tk gate: must be 8.6, not 9.x ----------------------------------------
Write-Host 'Checking Tcl/Tk version (must be 8.6)...'
& $VenvPython -c "import tkinter,sys; v=str(tkinter.Tcl().call('info','patchlevel')); print('Tcl', v); sys.exit(0 if v.startswith('8.6') else 1)"
if ($LASTEXITCODE -ne 0) { throw 'Tcl/Tk is not 8.6 - FreeSimpleGUI requires Tcl 8.6 (see docs/tcl9-migration.md)' }

# --- PyInstaller build ---------------------------------------------------------
Write-Host 'Building with PyInstaller...'
& $VenvPython -m PyInstaller packaging\coverup.spec --noconfirm
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }

# --- Smoke test the frozen exe -------------------------------------------------
$FrozenExe = Join-Path $RepoRoot 'dist\CoverUP\CoverUP.exe'
if (-not (Test-Path $FrozenExe)) { throw "Frozen exe not found: $FrozenExe" }
Write-Host 'Smoke testing frozen exe (--version)...'
& $FrozenExe --version
if ($LASTEXITCODE -ne 0) { throw 'Frozen exe --version smoke test failed' }

# --- Build the installer -------------------------------------------------------
if (-not (Test-Path $Iscc)) {
    throw "ISCC.exe not found at '$Iscc'. Install Inno Setup 6 or pass -Iscc <path>."
}
Write-Host "Compiling installer with $Iscc ..."
& $Iscc "/DCOVERUP_VERSION=$Version" packaging\windows\installer.iss
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup compilation failed' }

Write-Host ''
Write-Host "Done. Installer: Output\CoverUP-Setup-$Version-x64.exe"
