#ifndef PayloadDir
  #error PayloadDir must point to a prepared offline bundle.
#endif
#ifndef OutputDir
  #define OutputDir "..\dist\installer"
#endif
#ifndef AppVersion
  #define AppVersion "2.2.1"
#endif
#ifndef AppNumericVersion
  #define AppNumericVersion "2.2.1.0"
#endif
#ifdef DeltaFrom
  #ifndef AppUpdateOnly
    #error DeltaFrom requires AppUpdateOnly.
  #endif
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
SetupMutex=LudivoxaSetup
ArchitecturesAllowed=x64os
WizardStyle=modern
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir={#OutputDir}
#ifdef AppUpdateOnly
#ifdef DeltaFrom
OutputBaseFilename=Ludivoxa-{#AppVersion}-Delta-from-{#DeltaFrom}
#else
OutputBaseFilename=Ludivoxa-{#AppVersion}-App-Update
#endif
#else
OutputBaseFilename=Ludivoxa-{#AppVersion}-Full-Offline-Setup
#endif
SetupIconFile=..\src\ludivoxa\resources\icons\icon.ico
UninstallDisplayIcon={app}\Ludivoxa.exe
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
#ifdef DeltaFrom
#include DeltaDeletes
#else
; importlib.metadata must see only the newly installed application version.
Type: filesandordirs; Name: "{app}\_internal\ludivoxa-*.dist-info"
#endif

[Files]
#ifdef AppUpdateOnly
#ifdef DeltaFrom
Source: "{#DeltaManifest}"; Flags: dontcopy
#include DeltaFiles
#else
Source: "{#PayloadDir}\Ludivoxa.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
#endif
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "Stop-LocalRuntime.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion
#else
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Excludes: "config.default.yml"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PayloadDir}\config.default.yml"; DestDir: "{app}"; DestName: "config.yml"; Flags: onlyifdoesntexist uninsneveruninstall
#endif

[Icons]
Name: "{group}\Ludivoxa"; Filename: "{app}\Ludivoxa.exe"; WorkingDir: "{app}"
Name: "{group}\Uninstall Ludivoxa"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Ludivoxa"; Filename: "{app}\Ludivoxa.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Ludivoxa.exe"; Description: "Open Ludivoxa"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
Filename: "{app}\Ludivoxa.exe"; WorkingDir: "{app}"; Flags: nowait runasoriginaluser; Check: IsUpdateRestart

[Code]
var
  LegacyMetadata: String;

function FileAttributes(const Path: String): Cardinal;
  external 'GetFileAttributesW@kernel32.dll stdcall';

function FindLegacyMetadata: String;
var
  Directory: String;
  Entry: TFindRec;
begin
  Result := '';
  if not FileExists(ExpandConstant('{app}\Interpreter.exe')) or
     not FileExists(ExpandConstant('{app}\config.yml')) then Exit;
  Directory := ExpandConstant('{app}\_internal');
  if (FileAttributes(ExpandConstant('{app}')) and $410) <> $10 then Exit;
  if (FileAttributes(Directory) and $410) <> $10 then Exit;
  Directory := AddBackslash(Directory);
  if FindFirst(Directory + 'interpreter_v2-1.*.dist-info', Entry) then
  begin
    try
      if ((Entry.Attributes and $410) = $10) then
        Result := Directory + Entry.Name;
    finally
      FindClose(Entry);
    end;
  end;
end;

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

#ifdef DeltaFrom
function DeltaBaseMatches: Boolean;
var
  Lines: TArrayOfString;
  Root, Expected, RelativePath, Path, Parent: String;
  I, Separator: Integer;
  Attr: Cardinal;
begin
  Result := False;
  Root := ExpandConstant('{app}');
  try
    ExtractTemporaryFile('ludivoxa-delta-base.sha256');
    if not LoadStringsFromFile(ExpandConstant('{tmp}\ludivoxa-delta-base.sha256'), Lines) then Exit;
    if GetArrayLength(Lines) = 0 then Exit;
    for I := 0 to GetArrayLength(Lines) - 1 do
    begin
      Separator := Pos('  ', Lines[I]);
      Expected := Copy(Lines[I], 1, Separator - 1);
      RelativePath := Copy(Lines[I], Separator + 2, MaxInt);
      StringChangeEx(RelativePath, '/', '\', True);
      Path := AddBackslash(Root) + RelativePath;
      Parent := ExtractFileDir(Path);
      while True do
      begin
        Attr := FileAttributes(Parent);
        if Attr = $FFFFFFFF then
        begin
          if (Expected <> '-') or (Parent = Root) then Exit;
        end
        else if (Attr and $410) <> $10 then Exit;
        if Parent = Root then Break;
        Parent := ExtractFileDir(Parent);
      end;
      Attr := FileAttributes(Path);
      if Expected = '-' then
      begin
        if Attr <> $FFFFFFFF then Exit;
      end
      else
      begin
        if (Attr = $FFFFFFFF) or ((Attr and $410) <> 0) then Exit;
        if CompareText(GetSHA256OfFile(Path), Expected) <> 0 then Exit;
      end;
    end;
    Result := True;
  except
    Log('Could not validate delta base: ' + GetExceptionMessage);
  end;
end;
#endif

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  LegacyMetadata := FindLegacyMetadata;
#ifdef AppUpdateOnly
  if ((LegacyMetadata = '') and not FileExists(ExpandConstant('{app}\Ludivoxa.exe'))) or
     (not FileExists(ExpandConstant('{app}\config.yml'))) then
  begin
    Result := 'Select an existing Ludivoxa installation containing Ludivoxa.exe and config.yml, or use the full offline installer first.';
    Exit;
  end;
#endif
#ifdef DeltaFrom
  if not DeltaBaseMatches then
  begin
    Result := 'This installation does not match the smaller update. Download and run the standard App-Update.exe from the Ludivoxa release.';
    Exit;
  end;
#endif
  StopOwnedRuntime;
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  LegacyPackage: String;
begin
  if (CurStep <> ssPostInstall) or (LegacyMetadata = '') or
     not FileExists(ExpandConstant('{app}\Ludivoxa.exe')) then Exit;
  if DeleteFile(ExpandConstant('{app}\Interpreter.exe')) then
  begin
    DelTree(LegacyMetadata, True, True, True);
    LegacyPackage := ExpandConstant('{app}\_internal\interpreter');
    if (FileAttributes(LegacyPackage) and $410) = $10 then
      DelTree(LegacyPackage, True, True, True);
  end
  else
    Log('Could not remove the previous Ludivoxa executable.');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    StopOwnedRuntime;
end;
