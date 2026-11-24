; ADOFAI Offline PV Renderer — Inno Setup 6 Installer Script
; Build via: installer\build_installer.ps1  (or ISCC.exe installer\setup.iss)

#define MyAppName "ADOFAI PV Renderer"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "ADOFAI PV Renderer"
#define MyAppExeName "ADOFAI PV Renderer.exe"

[Setup]
AppId={{9F4C7B2A-6E1D-4A85-B31C-7D4E89A2F104}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=ADOFAI_PV_Renderer_Setup_{#MyAppVersion}
SetupIconFile=..\assets\app_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
WizardStyle=modern
WizardImageFile=wizard_banner.bmp
WizardSmallImageFile=wizard_small.bmp
WizardImageStretch=yes
Compression=lzma2/normal
SolidCompression=yes
CloseApplications=force
RestartApplications=no
ShowLanguageDialog=auto
VersionInfoVersion=2.0.0.0
VersionInfoProductName={#MyAppName}
VersionInfoDescription=ADOFAI Offline PV Renderer 설치 프로그램

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"; LicenseFile: "license_ko.txt"
Name: "english"; MessagesFile: "compiler:Default.isl"; LicenseFile: "license_en.txt"

[CustomMessages]
korean.TaskDesktop=바탕화면 바로가기 만들기
korean.TaskContextMenu=Windows 탐색기 .adofai 우클릭 메뉴("ADOFAI PV Renderer로 열기") 추가
korean.ContextMenuLabel=ADOFAI PV Renderer로 열기
korean.UninstallLabel={#MyAppName} 제거
korean.LaunchApp={#MyAppName} 실행
korean.GroupAdditional=추가 작업:
korean.OutputPageTitle=동영상 저장 위치
korean.OutputPageDesc=렌더링한 MP4 파일을 저장할 기본 폴더를 선택하세요.
korean.OutputPageSub=설치 프로그램은 이 폴더를 만들고, 프로그램의 기본 내보내기 위치로 사용합니다. 프로그램 안에서도 언제든 변경할 수 있습니다.
korean.OutputPageLabel=MP4 내보내기 폴더:
korean.OutputPageEmpty=내보내기 폴더를 입력해 주세요.

english.TaskDesktop=Create a desktop shortcut
english.TaskContextMenu=Add "Open with ADOFAI PV Renderer" to .adofai right-click menu
english.ContextMenuLabel=Open with ADOFAI PV Renderer
english.UninstallLabel=Uninstall {#MyAppName}
english.LaunchApp=Launch {#MyAppName}
english.GroupAdditional=Additional tasks:
english.OutputPageTitle=Video Output Location
english.OutputPageDesc=Choose the default folder where rendered MP4 files will be saved.
english.OutputPageSub=Setup will create this folder and use it as the app's default export location. You can change it later inside the app.
english.OutputPageLabel=MP4 export folder:
english.OutputPageEmpty=Please enter an export folder.

[Tasks]
Name: "desktopicon"; Description: "{cm:TaskDesktop}"; GroupDescription: "{cm:GroupAdditional}"
Name: "contextmenu"; Description: "{cm:TaskContextMenu}"; GroupDescription: "{cm:GroupAdditional}"; Flags: unchecked

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autoprograms}\{#MyAppName}\{cm:UninstallLabel}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
; Optional Explorer context menu for .adofai files (HKA works cleanly in both per-user and admin installs)
Root: HKA; Subkey: "Software\Classes\.adofai\shell\OpenWithAdofaiPvRenderer"; ValueType: string; ValueName: ""; ValueData: "{cm:ContextMenuLabel}"; Tasks: contextmenu; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\.adofai\shell\OpenWithAdofaiPvRenderer"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\{#MyAppExeName}"",0"; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\.adofai\shell\OpenWithAdofaiPvRenderer\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: contextmenu

; Remember the chosen MP4 export folder (read by the app at startup)
Root: HKCU; Subkey: "Software\ADOFAI PV Renderer"; ValueType: string; ValueName: "OutputDir"; ValueData: "{code:GetOutputDir}"; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchApp}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up extracted runtime cache/agent DLLs on uninstall while preserving user rendered videos
Type: filesandordirs; Name: "{app}\cache"
Type: dirifempty; Name: "{app}\output"
Type: dirifempty; Name: "{app}"

[Code]
var
  OutputPage: TInputDirWizardPage;

function GetOutputDir(Param: string): string;
begin
  Result := RemoveBackslashUnlessRoot(OutputPage.Values[0]);
end;

procedure InitializeWizard;
var
  Prev: string;
begin
  OutputPage := CreateInputDirPage(wpSelectDir,
    CustomMessage('OutputPageTitle'), CustomMessage('OutputPageDesc'),
    CustomMessage('OutputPageSub'), False, '');
  OutputPage.Add(CustomMessage('OutputPageLabel'));
  if RegQueryStringValue(HKCU, 'Software\ADOFAI PV Renderer', 'OutputDir', Prev) and (Prev <> '') then
    OutputPage.Values[0] := Prev
  else
    OutputPage.Values[0] := ExpandConstant('{%USERPROFILE}\Videos\ADOFAI_PV_Renderer');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = OutputPage.ID then
  begin
    if Trim(OutputPage.Values[0]) = '' then
    begin
      MsgBox(CustomMessage('OutputPageEmpty'), mbError, MB_OK);
      Result := False;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    ForceDirectories(GetOutputDir(''));
end;
