; Jisr installer (Inno Setup 6)  ->  Output\Jisr-Setup.exe
; Built by .github/workflows/build-pc.yml after   build_exe.bat ci   has created ..\dist\Jisr\
; Local build (optional):  ISCC.exe /DMyAppVersion=1.0.0 Jisr.iss
; If an Arabic.isl file sits next to this script the wizard also offers Arabic (the workflow downloads it).

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppName "Jisr"
#define MyAppExe "Jisr.exe"

[Setup]
AppId={{B08A48E5-5357-400D-9A11-194DD71FB6A8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Jisr
VersionInfoVersion={#MyAppVersion}.0
VersionInfoProductName={#MyAppName}
VersionInfoDescription={#MyAppName} Setup
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExe}
SetupIconFile=..\assets\jisr.ico
OutputDir=Output
OutputBaseFilename=Jisr-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Administrator by default (so the firewall rule can be added); the user may choose "only for me" instead.
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
CloseApplications=yes
RestartApplications=no
ShowLanguageDialog=yes
UsePreviousLanguage=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
#if FileExists(AddBackslash(SourcePath) + "Arabic.isl")
Name: "arabic"; MessagesFile: "Arabic.isl"
#endif

[CustomMessages]
english.TaskGroupNetwork=Network:
english.TaskFirewall=Allow Jisr through Windows Firewall on private networks (needed so the phone can reach the PC)
#if FileExists(AddBackslash(SourcePath) + "Arabic.isl")
arabic.TaskGroupNetwork=الشبكة:
arabic.TaskFirewall=السماح لـ Jisr عبر جدار حماية ويندوز في الشبكات الخاصة (لازم حتى يصل الهاتف إلى الكمبيوتر)
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "firewall"; Description: "{cm:TaskFirewall}"; GroupDescription: "{cm:TaskGroupNetwork}"; Check: IsAdminInstallMode

[Files]
Source: "..\dist\Jisr\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
; same rules as allow_firewall.bat (private networks only, the program's own port)
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""Jisr TCP"""; Flags: runhidden; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""Jisr UDP"""; Flags: runhidden; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""Jisr TCP"" dir=in action=allow protocol=TCP localport=47810 profile=private"; Flags: runhidden; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""Jisr UDP"" dir=in action=allow protocol=UDP localport=47810 profile=private"; Flags: runhidden; Tasks: firewall
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#MyAppExe}"; RunOnceId: "StopJisr"; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""Jisr TCP"""; RunOnceId: "DelFwTcp"; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""Jisr UDP"""; RunOnceId: "DelFwUdp"; Flags: runhidden

[Code]
// The program manages two things for the user from inside its own window ("Start with Windows" and
// Explorer's "Send to > Jisr"). Take both away when the program is uninstalled.
// Settings and the PIN (%APPDATA%\BTBridge) are kept, so a reinstall does not need pairing again.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Jisr');
    DeleteFile(ExpandConstant('{sendto}\Jisr.lnk'));
  end;
end;
