[Setup]
AppId={{23192498-D1F4-45BA-AD5E-2CD5F2162275}
AppName=Google Perks Manager
AppPublisher=HeiGeAi
AppVersion={#AppVersion}
AppPublisherURL=https://github.com/HeiGeAi/google-perks-manager
DefaultDirName={localappdata}\Programs\GooglePerksManager
DefaultGroupName=Google Perks Manager
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
MinVersion=10.0.19041
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile={#AppIcon}
UninstallDisplayIcon={app}\GooglePerksManager.exe
OutputDir={#OutputFolder}
OutputBaseFilename=GooglePerksManager-Windows-x64-Setup
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Files]
Source: "{#AppSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Bootstrapper}"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\Google Perks Manager"; Filename: "{app}\GooglePerksManager.exe"

[Registry]
Root: HKCU; Subkey: "Software\Classes\googleperksmanager"; ValueType: string; ValueData: "URL:Google Perks Manager"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\googleperksmanager"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\googleperksmanager\shell\open\command"; ValueType: string; ValueData: """{app}\GooglePerksManager.exe"" ""%1"""

[Run]
Filename: "{app}\GooglePerksManager.exe"; Description: "Open Google Perks Manager"; Flags: nowait postinstall skipifsilent

[Code]
function WebViewInstalled(): Boolean;
var
  RuntimeVersion: String;
  ClientsKey: String;
begin
  ClientsKey := 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  Result := (RegQueryStringValue(HKLM32, ClientsKey, 'pv', RuntimeVersion) or
    RegQueryStringValue(HKCU, ClientsKey, 'pv', RuntimeVersion))
    and (RuntimeVersion <> '') and (RuntimeVersion <> '0.0.0.0');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExitCode: Integer;
begin
  Result := '';
  if not WebViewInstalled() then
  begin
    ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
    if not Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'), '/silent /install', '', SW_HIDE,
      ewWaitUntilTerminated, ExitCode) or (ExitCode <> 0) then
      Result := 'Microsoft Edge WebView2 Runtime could not be installed. Check your network and try again.';
  end;
end;
