; Inno Setup script for WorkOnward Read (Windows x64)
;
; Build (from the repo root, after PyInstaller has produced dist\WorkOnwardRead\):
;   ISCC.exe packaging\windows\installer.iss /DWORKONWARD_VERSION=0.5.1
;
; If /DWORKONWARD_VERSION is not passed, the WORKONWARD_VERSION environment
; variable is used, falling back to 0.0.0 (which should never ship).
;
; WorkOnward Read is based on CoverUP by Björn Seipel (digidigital), GPL-3.0.

#ifndef WORKONWARD_VERSION
  #define WORKONWARD_VERSION GetEnv("WORKONWARD_VERSION")
#endif
#if WORKONWARD_VERSION == ""
  #define WORKONWARD_VERSION "0.0.0"
#endif

#define MyAppName "WorkOnward Read"
#define MyAppVersion WORKONWARD_VERSION
#define MyAppPublisher "WorkOnward"
#define MyAppURL "https://workonward.org"
#define MyAppExeName "WorkOnwardRead.exe"

[Setup]
; Fixed AppId — NEVER change this GUID, or upgrades will install side-by-side
; instead of updating the existing installation. (This GUID is NEW for
; WorkOnward Read; it intentionally differs from the upstream CoverUP GUID
; so both products can coexist.)
AppId={{012B1272-0E8E-43EE-9B4D-FC337322CBC8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\WorkOnwardRead
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; 64-bit only (x64 and ARM64 machines with x64 emulation).
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Per-user install by default; the dialog lets admins install machine-wide.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
LicenseFile=..\..\LICENSE
OutputDir=..\..\Output
OutputBaseFilename=WorkOnwardRead-Setup-{#MyAppVersion}-x64
SetupIconFile=..\..\WorkOnwardRead.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Wizard imagery (100% and 200% DPI variants live next to this script).
WizardImageFile=wizard_image*.bmp
WizardSmallImageFile=wizard_small*.bmp

; Code signing (dormant): configure a SignTool named "signtool" in the Inno
; Setup IDE or pass /S"signtool=..." on the ISCC command line, then uncomment:
;SignTool=signtool

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "assocpdf"; Description: "Register WorkOnward Read in the ""Open with"" menu for PDF, PNG and JPG files (does not change your default apps)"; Flags: unchecked

[Files]
; The whole PyInstaller onedir output (built via packaging\workonward_read.spec).
Source: "..\..\dist\WorkOnwardRead\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start Menu entry is always created.
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; "Open with" registration only — WorkOnward Read is never made the default handler.
; HKA resolves to HKCU for per-user installs and HKLM for admin installs.
Root: HKA; Subkey: "Software\Classes\Applications\WorkOnwardRead.exe"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppName}"; Flags: uninsdeletekey; Tasks: assocpdf
Root: HKA; Subkey: "Software\Classes\Applications\WorkOnwardRead.exe\SupportedTypes"; ValueType: string; ValueName: ".pdf"; ValueData: ""; Tasks: assocpdf
Root: HKA; Subkey: "Software\Classes\Applications\WorkOnwardRead.exe\SupportedTypes"; ValueType: string; ValueName: ".png"; ValueData: ""; Tasks: assocpdf
Root: HKA; Subkey: "Software\Classes\Applications\WorkOnwardRead.exe\SupportedTypes"; ValueType: string; ValueName: ".jpg"; ValueData: ""; Tasks: assocpdf
Root: HKA; Subkey: "Software\Classes\Applications\WorkOnwardRead.exe\SupportedTypes"; ValueType: string; ValueName: ".jpeg"; ValueData: ""; Tasks: assocpdf
Root: HKA; Subkey: "Software\Classes\Applications\WorkOnwardRead.exe\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: assocpdf

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
