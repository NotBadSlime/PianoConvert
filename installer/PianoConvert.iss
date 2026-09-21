#define MyAppName "PianoConvert"
#define MyAppVersion "0.3.1"
#define MyAppExeName "PianoConvert.exe"

[Setup]
AppId={{A7C2E4D1-9B18-4F3A-8C55-7B3E9D2A1F04}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\PianoConvert
DefaultGroupName=PianoConvert
OutputDir=output
OutputBaseFilename=PianoConvertSetup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务"; Flags: checkedonce

[Files]
Source: "..\dist\PianoConvert\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\PianoConvert"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\PianoConvert"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 PianoConvert"; Flags: nowait postinstall skipifsilent
