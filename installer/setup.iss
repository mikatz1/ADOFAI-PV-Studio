; ADOFAI PV Studio — Inno Setup 6 Installer Script
; Build via: installer\build_installer.ps1  (or ISCC.exe installer\setup.iss)

#define MyAppName "ADOFAI PV Studio"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "ADOFAI PV Studio"
#define MyAppExeName "ADOFAI PV Studio.exe"

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
OutputBaseFilename=ADOFAI_PV_Studio_Setup_{#MyAppVersion}
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
ShowLanguageDialog=yes
VersionInfoVersion=1.0.0.0
VersionInfoProductName={#MyAppName}
VersionInfoDescription=ADOFAI PV Studio Setup

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"; LicenseFile: "license_ko.txt"
Name: "english"; MessagesFile: "compiler:Default.isl"; LicenseFile: "license_en.txt"
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"; LicenseFile: "license_ja.txt"

[CustomMessages]
korean.TaskDesktop=바탕화면 바로가기 만들기
korean.TaskContextMenu=Windows 탐색기 .adofai 우클릭 메뉴("ADOFAI PV Studio로 열기") 추가
korean.ContextMenuLabel=ADOFAI PV Studio로 열기
korean.UninstallLabel={#MyAppName} 제거
korean.LaunchApp={#MyAppName} 실행
korean.GroupAdditional=추가 작업:
korean.OutputPageTitle=동영상 저장 위치
korean.OutputPageDesc=렌더링한 MP4 파일을 저장할 기본 폴더를 선택하세요.
korean.OutputPageSub=설치 프로그램은 이 폴더를 만들고, 프로그램의 기본 내보내기 위치로 사용합니다. 프로그램 안에서도 언제든 변경할 수 있습니다.
korean.OutputPageLabel=MP4 내보내기 폴더:
korean.OutputPageEmpty=내보내기 폴더를 입력해 주세요.

english.TaskDesktop=Create a desktop shortcut
english.TaskContextMenu=Add "Open with ADOFAI PV Studio" to .adofai right-click menu
english.ContextMenuLabel=Open with ADOFAI PV Studio
english.UninstallLabel=Uninstall {#MyAppName}
english.LaunchApp=Launch {#MyAppName}
english.GroupAdditional=Additional tasks:
english.OutputPageTitle=Video Output Location
english.OutputPageDesc=Choose the default folder where rendered MP4 files will be saved.
english.OutputPageSub=Setup will create this folder and use it as the app's default export location. You can change it later inside the app.
english.OutputPageLabel=MP4 export folder:
english.OutputPageEmpty=Please enter an export folder.

japanese.TaskDesktop=デスクトップにショートカットを作成
japanese.TaskContextMenu=.adofai 右クリックメニューに「ADOFAI PV Studioで開く」を追加
japanese.ContextMenuLabel=ADOFAI PV Studioで開く
japanese.UninstallLabel={#MyAppName} をアンインストール
japanese.LaunchApp={#MyAppName} を起動
japanese.GroupAdditional=追加タスク:
japanese.OutputPageTitle=動画の保存先
japanese.OutputPageDesc=レンダリングしたMP4ファイルを保存する初期フォルダを選択してください。
japanese.OutputPageSub=インストーラーはこのフォルダを作成し、アプリの既定出力先として設定します。アプリ内からもいつでも変更できます。
japanese.OutputPageLabel=MP4出力フォルダ:
japanese.OutputPageEmpty=出力フォルダを入力してください。

[Tasks]
Name: "desktopicon"; Description: "{cm:TaskDesktop}"; GroupDescription: "{cm:GroupAdditional}"
Name: "contextmenu"; Description: "{cm:TaskContextMenu}"; GroupDescription: "{cm:GroupAdditional}"; Flags: unchecked

[InstallDelete]
; Clean up legacy EXE and shortcuts when upgrading from ADOFAI PV Renderer (v2.0.5 and earlier)
Type: files; Name: "{app}\ADOFAI PV Renderer.exe"
Type: files; Name: "{autodesktop}\ADOFAI PV Renderer.lnk"
Type: filesandordirs; Name: "{autoprograms}\ADOFAI PV Renderer"

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autoprograms}\{#MyAppName}\{cm:UninstallLabel}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
; Remove legacy Explorer context menu entry from v2.0.5 and earlier if present
Root: HKA; Subkey: "Software\Classes\.adofai\shell\OpenWithAdofaiPvRenderer"; Flags: deletekey dontcreatekey
; Optional Explorer context menu for .adofai files (HKA works cleanly in both per-user and admin installs)
Root: HKA; Subkey: "Software\Classes\.adofai\shell\OpenWithAdofaiPvStudio"; ValueType: string; ValueName: ""; ValueData: "{cm:ContextMenuLabel}"; Tasks: contextmenu; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\.adofai\shell\OpenWithAdofaiPvStudio"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\{#MyAppExeName}"",0"; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\.adofai\shell\OpenWithAdofaiPvStudio\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: contextmenu

; Remember the chosen MP4 export folder and UI language (read by the app at startup)
Root: HKCU; Subkey: "Software\ADOFAI PV Studio"; ValueType: string; ValueName: "OutputDir"; ValueData: "{code:GetOutputDir}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\ADOFAI PV Studio"; ValueType: string; ValueName: "Language"; ValueData: "{code:GetInstallLang}"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchApp}"; Flags: nowait postinstall skipifsilent
; In-app updates run the installer silently: relaunch the app afterwards
Filename: "{app}\{#MyAppExeName}"; Flags: nowait runasoriginaluser; Check: ShouldSilentRelaunch

[UninstallDelete]
; Clean up extracted runtime cache/agent DLLs on uninstall while preserving user rendered videos
Type: filesandordirs; Name: "{app}\cache"
Type: dirifempty; Name: "{app}\output"
Type: dirifempty; Name: "{app}"

[Code]
var
  OutputPage: TInputDirWizardPage;

function ShouldSilentRelaunch: Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:NOLAUNCH|0}') <> '1');
end;

function GetOutputDir(Param: string): string;
begin
  Result := RemoveBackslashUnlessRoot(OutputPage.Values[0]);
end;

function GetInstallLang(Param: string): string;
var
  Existing: string;
begin
  if WizardSilent then
  begin
    if RegQueryStringValue(HKCU, 'Software\ADOFAI PV Studio', 'Language', Existing) and (Existing <> '') then
    begin
      Result := Existing;
      exit;
    end;
    if RegQueryStringValue(HKCU, 'Software\ADOFAI PV Renderer', 'Language', Existing) and (Existing <> '') then
    begin
      Result := Existing;
      exit;
    end;
  end;
  if ActiveLanguage = 'korean' then
    Result := 'ko'
  else if ActiveLanguage = 'japanese' then
    Result := 'ja'
  else
    Result := 'en';
end;

procedure InitializeWizard;
var
  Prev: string;
begin
  OutputPage := CreateInputDirPage(wpSelectDir,
    CustomMessage('OutputPageTitle'), CustomMessage('OutputPageDesc'),
    CustomMessage('OutputPageSub'), False, '');
  OutputPage.Add(CustomMessage('OutputPageLabel'));
  if RegQueryStringValue(HKCU, 'Software\ADOFAI PV Studio', 'OutputDir', Prev) and (Prev <> '') then
    OutputPage.Values[0] := Prev
  else if RegQueryStringValue(HKCU, 'Software\ADOFAI PV Renderer', 'OutputDir', Prev) and (Prev <> '') then
    OutputPage.Values[0] := Prev
  else
    OutputPage.Values[0] := ExpandConstant('{%USERPROFILE}\Videos\ADOFAI_PV_Studio');
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
