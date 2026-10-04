; Inno Setup script for the Windows installer. Built by packaging/build.py, e.g.
;   iscc /DMyVersion=1.1.0 /DSourceDir=dist\Riffarchy /DOutputDir=dist /DOutputName=... /DIconFile=... riffarchy.iss
; Per-user install: no admin rights needed, lands in %LOCALAPPDATA%\Programs\Riffarchy.

#ifndef MyVersion
  #define MyVersion "0.0.0"
#endif

[Setup]
AppId={{6F1C2D8E-5B7A-4E3C-9A2F-8D41B6C0E7A5}
AppName=Riffarchy
AppVersion={#MyVersion}
AppPublisher=Joson Mathew
AppPublisherURL=https://github.com/jmathew499/riffarchy
AppSupportURL=https://github.com/jmathew499/riffarchy/issues
DefaultDirName={autopf}\Riffarchy
DefaultGroupName=Riffarchy
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename={#OutputName}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\Riffarchy.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\..\LICENSE

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Riffarchy"; Filename: "{app}\Riffarchy.exe"
Name: "{autodesktop}\Riffarchy"; Filename: "{app}\Riffarchy.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Riffarchy.exe"; Description: "{cm:LaunchProgram,Riffarchy}"; Flags: nowait postinstall skipifsilent
