#ifndef PayloadDir
  #error PayloadDir must point to a prepared offline bundle.
#endif
#ifndef OutputDir
  #define OutputDir "..\dist\installer"
#endif
#define AppVersion "1.0.0"

[Setup]
AppId={{6C80F14D-818C-4A3F-B431-5389B73CA221}
AppName=Ludivoxa
AppVersion={#AppVersion}
AppPublisher=Ludivoxa contributors
AppPublisherURL=https://github.com/hungdinhxuan/Ludivoxa
DefaultDirName={localappdata}\Programs\Ludivoxa
DefaultGroupName=Ludivoxa
PrivilegesRequired=lowest
SetupArchitecture=x64
ArchitecturesAllowed=x64os
WizardStyle=modern
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir={#OutputDir}
OutputBaseFilename=Ludivoxa-{#AppVersion}-Full-Offline-Setup
SetupIconFile=..\src\interpreter\resources\icons\icon.ico
UninstallDisplayIcon={app}\Interpreter.exe
Compression=lzma2/fast
SolidCompression=yes
LZMANumBlockThreads=4
DiskSpanning=yes
DiskSliceSize=2100000000
CloseApplications=yes
RestartApplications=no
MinVersion=10.0.17763
SetupLogging=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Excludes: "config.default.yml"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PayloadDir}\config.default.yml"; DestDir: "{app}"; DestName: "config.yml"; Flags: onlyifdoesntexist uninsneveruninstall

[Icons]
Name: "{group}\Ludivoxa"; Filename: "{app}\Interpreter.exe"; WorkingDir: "{app}"
Name: "{group}\Uninstall Ludivoxa"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Ludivoxa"; Filename: "{app}\Interpreter.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Interpreter.exe"; Description: "Open Ludivoxa"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[Code]
procedure StopOwnedRuntime;
var
  ResultCode: Integer;
  StopScript: String;
begin
  StopScript := ExpandConstant('{app}\Stop-LocalRuntime.ps1');
  if FileExists(StopScript) then
    Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
      '-NoProfile -ExecutionPolicy Bypass -File "' + StopScript + '"',
      ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopOwnedRuntime;
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    StopOwnedRuntime;
end;
