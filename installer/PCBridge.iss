; PC Bridge -- Inno Setup script
;
; Builds a proper Windows installer around the PyInstaller-built
; PCBridge.exe: Start Menu shortcut (with icon), optional Desktop
; shortcut, an uninstaller entry in "Add or Remove Programs", and a
; normal setup wizard -- instead of just handing someone a bare .exe to
; put wherever they like.
;
; Deliberately a PER-USER install (PrivilegesRequired=lowest, installs
; under %LOCALAPPDATA%), not a system-wide Program Files install:
;   1. No admin/UAC prompt needed to install at all.
;   2. Auto-update (see pcbridge_app.py) works by downloading a new
;      PCBridge.exe and swapping it in place at wherever it's currently
;      running from -- that needs write access to its own folder.
;      Program Files is admin-protected, which would break silent
;      auto-update (or require running the app as admin forever, which
;      this project has deliberately avoided everywhere else).
;
; Built by .github/workflows/build-release.yml right after PyInstaller
; produces dist\PCBridge.exe -- see README-DESKTOP.md for the full
; picture (this installer vs. the plain portable exe, which is still
; attached to every release too, since auto-update fetches that one by
; name regardless of how you originally installed).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{782D7173-64AA-424D-86A1-E0CCA98CD72C}
AppName=PC Bridge
AppVersion={#AppVersion}
AppPublisher=PC Bridge
DefaultDirName={localappdata}\Programs\PCBridge
DefaultGroupName=PC Bridge
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=installer_output
OutputBaseFilename=PCBridge-Setup
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\PCBridge.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\PCBridge.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\PC Bridge"; Filename: "{app}\PCBridge.exe"
Name: "{group}\Uninstall PC Bridge"; Filename: "{uninstallexe}"
Name: "{userdesktop}\PC Bridge"; Filename: "{app}\PCBridge.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\PCBridge.exe"; Description: "Launch PC Bridge"; Flags: nowait postinstall skipifsilent
