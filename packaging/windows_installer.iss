#ifndef PayloadDir
  #error PayloadDir must point to a prepared offline bundle.
#endif
#ifndef OutputDir
  #define OutputDir "..\dist\installer"
#endif
#ifndef AppVersion
  #define AppVersion "1.1.2"
#endif
#ifndef AppNumericVersion
  #define AppNumericVersion "1.1.2.0"
#endif

[Setup]
AppId={{6C80F14D-818C-4A3F-B431-5389B73CA221}
AppName=Ludivoxa
AppVersion={#AppVersion}
VersionInfoVersion={#AppNumericVersion}
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
#ifdef AppUpdateOnly
OutputBaseFilename=Ludivoxa-{#AppVersion}-App-Update
#else
OutputBaseFilename=Ludivoxa-{#AppVersion}-Full-Offline-Setup
#endif
SetupIconFile=..\src\interpreter\resources\icons\icon.ico
UninstallDisplayIcon={app}\Interpreter.exe
Compression=lzma2/fast
SolidCompression=yes
LZMANumBlockThreads=4
#ifdef AppUpdateOnly
DiskSpanning=no
#else
DiskSpanning=yes
DiskSliceSize=2100000000
#endif
CloseApplications=yes
RestartApplications=no
MinVersion=10.0.17763
SetupLogging=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[InstallDelete]
; importlib.metadata must see only the newly installed application version.
Type: filesandordirs; Name: "{app}\_internal\interpreter_v2-*.dist-info"

[Files]
#ifdef AppUpdateOnly
Source: "{#PayloadDir}\Interpreter.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "Stop-LocalRuntime.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion
#else
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Excludes: "config.default.yml"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PayloadDir}\config.default.yml"; DestDir: "{app}"; DestName: "config.yml"; Flags: onlyifdoesntexist uninsneveruninstall
#endif

[Icons]
Name: "{group}\Ludivoxa"; Filename: "{app}\Interpreter.exe"; WorkingDir: "{app}"
Name: "{group}\Uninstall Ludivoxa"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Ludivoxa"; Filename: "{app}\Interpreter.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Interpreter.exe"; Description: "Open Ludivoxa"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
Filename: "{app}\Interpreter.exe"; WorkingDir: "{app}"; Flags: nowait runasoriginaluser; Check: IsUpdateRestart

[Code]
function IsUpdateRestart: Boolean;
begin
  Result := ExpandConstant('{param:LUDIVOXARESTART|0}') = '1';
end;

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
#ifdef AppUpdateOnly
  if (not FileExists(ExpandConstant('{app}\Interpreter.exe'))) or
     (not FileExists(ExpandConstant('{app}\config.yml'))) then
  begin
    Result := 'Select an existing Ludivoxa installation containing Interpreter.exe and config.yml, or use the full offline installer first.';
    Exit;
  end;
#endif
  StopOwnedRuntime;
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    StopOwnedRuntime;
end;
