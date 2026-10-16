; Inno Setup 6 source-distribution installer.
; Requires Python 3.11 and pip on the target PC; this does not bundle a Python runtime.
#define MyAppName "ORION Database Enterprise"
#define MyAppVersion "2.0"
[Setup]
AppId={{8F82C94C-BADA-4F0C-AC36-33C011BDB1E5}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\ORION Database Enterprise
DefaultGroupName={#MyAppName}
OutputDir=Output
OutputBaseFilename=ORION_Database_Enterprise_v2_Setup
Compression=lzma
SolidCompression=yes
PrivilegesRequired=admin
[Files]
Source: "..\orion_db\*"; DestDir: "{app}\orion_db"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\plugins\*"; DestDir: "{app}\plugins"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\config\*"; DestDir: "{app}\config"; Flags: ignoreversion onlyifdoesntexist
Source: "..\knowledge\*"; DestDir: "{app}\knowledge"; Flags: recursesubdirs createallsubdirs onlyifdoesntexist
Source: "..\scripts\*"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "..\requirements*.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
[Icons]
Name: "{group}\Install Python Dependencies"; Filename: "{app}\scripts\INSTALL_WINDOWS.bat"
Name: "{group}\Start ORION Server"; Filename: "{app}\scripts\START_SERVER.bat"
Name: "{group}\Start ORION Desktop"; Filename: "{app}\scripts\START_DESKTOP.bat"
[Run]
Filename: "{app}\scripts\INSTALL_WINDOWS.bat"; Description: "Install dependencies and prepare demo"; Flags: postinstall shellexec skipifsilent
