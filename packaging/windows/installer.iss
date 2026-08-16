; Open Orion — Windows installer (Inno Setup).
; Build with:
;   ISCC.exe packaging\windows\installer.iss /DAppVersion=2.8.1
; Expects dist\orion.exe and dist\orion-hud.exe (built by PyInstaller).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "Open Orion"
#define AppPublisher "4matthedev"
#define AppURL "https://github.com/4matthedev/open-orion"

[Setup]
AppId={{E6F4B08A-7C3D-4A1E-9B5F-5D0A4C7E13A2}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
DefaultDirName={autopf}\Open Orion
DefaultGroupName=Open Orion
DisableProgramGroupPage=yes
OutputDir=..\..\dist-installer
OutputBaseFilename=OpenOrion-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\orion.exe
LicenseFile=

[Files]
Source: "..\..\dist\orion.exe";      DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\dist\orion-hud.exe";  DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Open Orion";           Filename: "{app}\orion.exe"; Parameters: "--gui"; WorkingDir: "{app}"; IconFilename: "{app}\orion.exe"
Name: "{autoprograms}\Open Orion HUD";       Filename: "{app}\orion-hud.exe"; WorkingDir: "{app}"; IconFilename: "{app}\orion-hud.exe"
Name: "{autodesktop}\Open Orion";            Filename: "{app}\orion.exe"; Parameters: "--gui"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a {cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Run]
Filename: "{app}\orion.exe"; Parameters: "--gui"; Description: "Launch Open Orion"; Flags: nowait postinstall skipifsilent