; Inno Setup script for CoverUP PDF (Windows x64)
;
; Build (from the repo root, after PyInstaller has produced dist\CoverUP\):
;   ISCC.exe packaging\windows\installer.iss /DCOVERUP_VERSION=0.4.2
;
; If /DCOVERUP_VERSION is not passed, the COVERUP_VERSION environment
; variable is used, falling back to 0.0.0 (which should never ship).

#ifndef COVERUP_VERSION
  #define COVERUP_VERSION GetEnv("COVERUP_VERSION")
#endif
#if COVERUP_VERSION == ""
  #define COVERUP_VERSION "0.0.0"
#endif

#define MyAppName "CoverUP PDF"
#define MyAppVersion COVERUP_VERSION
#define MyAppPublisher "digidigital"
#define MyAppURL "https://coverup.digidigital.de"
#define MyAppExeName "CoverUP.exe"

[Setup]
; Fixed AppId — NEVER change this GUID, or upgrades will install side-by-side
; instead of updating the existing installation.
AppId={{127D1393-98AE-44C7-8229-6931C3D14A85}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\CoverUP
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; 64-bit only (x64 and ARM64 machines with x64 emulation).
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Per-user install by default; the dialog lets admins install machine-wide.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
LicenseFile=..\..\LICENSE
OutputBaseFilename=CoverUP-Setup-{#MyAppVersion}-x64
SetupIconFile=..\..\CoverUP.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

; Code signing (dormant): configure a SignTool named "signtool" in the Inno
; Setup IDE or pass /S"signtool=..." on the ISCC command line, then uncomment:
;SignTool=signtool

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "assocpdf"; Description: "Register CoverUP in the ""Open with"" menu for PDF, PNG and JPG files (does not change your default apps)"; Flags: unchecked

[Files]
; The whole PyInstaller onedir output (built via packaging\coverup.spec).
Source: "..\..\dist\CoverUP\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start Menu entry is always created.
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; "Open with" registration only — CoverUP is never made the default handler.
; HKA resolves to HKCU for per-user installs and HKLM for admin installs.
Root: HKA; Subkey: "Software\Classes\Applications\CoverUP.exe"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppName}"; Flags: uninsdeletekey; Tasks: assocpdf
Root: HKA; Subkey: "Software\Classes\Applications\CoverUP.exe\SupportedTypes"; ValueType: string; ValueName: ".pdf"; ValueData: ""; Tasks: assocpdf
Root: HKA; Subkey: "Software\Classes\Applications\CoverUP.exe\SupportedTypes"; ValueType: string; ValueName: ".png"; ValueData: ""; Tasks: assocpdf
Root: HKA; Subkey: "Software\Classes\Applications\CoverUP.exe\SupportedTypes"; ValueType: string; ValueName: ".jpg"; ValueData: ""; Tasks: assocpdf
Root: HKA; Subkey: "Software\Classes\Applications\CoverUP.exe\SupportedTypes"; ValueType: string; ValueName: ".jpeg"; ValueData: ""; Tasks: assocpdf
Root: HKA; Subkey: "Software\Classes\Applications\CoverUP.exe\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: assocpdf

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
