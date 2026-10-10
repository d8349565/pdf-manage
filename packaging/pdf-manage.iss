#define MyAppExeName "ZhiXuPDF.exe"
#define MyAppName GetStringFileInfo("..\dist\ZhiXuPDF\" + MyAppExeName, "ProductName")
#define MyAppVersion GetStringFileInfo("..\dist\ZhiXuPDF\" + MyAppExeName, "ProductVersion")
#define MyAppProgId "PDFManage.Document"

[Setup]
AppId={{0E4A38D0-8475-4A57-98C2-1AD3F468A0E8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\PDFManage
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist-installer
OutputBaseFilename=ZhiXuPDF-Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ChangesAssociations=yes

[Files]
Source: "..\dist\ZhiXuPDF\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\ZhiXuPDF\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："
Name: "wordcontext"; Description: "Word 文件和文件夹添加「Word 转 PDF」右键入口"; GroupDescription: "资源管理器："

[Registry]
Root: HKCU; Subkey: "Software\Classes\{#MyAppProgId}"; ValueType: string; ValueName: ""; ValueData: "PDF 文档"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\{#MyAppProgId}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"",1"
Root: HKCU; Subkey: "Software\Classes\{#MyAppProgId}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "{#MyAppProgId}"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\Applications\{#MyAppExeName}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppName}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\{#MyAppExeName}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"",1"
Root: HKCU; Subkey: "Software\Classes\Applications\{#MyAppExeName}\SupportedTypes"; ValueType: string; ValueName: ".pdf"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\{#MyAppExeName}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""
Root: HKCU; Subkey: "Software\PDFManage\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "{#MyAppName}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\PDFManage\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "在本机阅读、整理和打印 PDF"
Root: HKCU; Subkey: "Software\PDFManage\Capabilities\FileAssociations"; ValueType: string; ValueName: ".pdf"; ValueData: "{#MyAppProgId}"
Root: HKCU; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "PDFManage"; ValueData: "Software\PDFManage\Capabilities"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.doc\shell\PageArkWordToPDF"; ValueType: string; ValueName: ""; ValueData: "Word 转 PDF"; Flags: uninsdeletekey; Tasks: wordcontext
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.doc\shell\PageArkWordToPDF"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName}"; Tasks: wordcontext
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.doc\shell\PageArkWordToPDF"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Single"; Tasks: wordcontext
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.doc\shell\PageArkWordToPDF\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" --word-to-pdf ""%1"""; Tasks: wordcontext
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.docx\shell\PageArkWordToPDF"; ValueType: string; ValueName: ""; ValueData: "Word 转 PDF"; Flags: uninsdeletekey; Tasks: wordcontext
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.docx\shell\PageArkWordToPDF"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName}"; Tasks: wordcontext
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.docx\shell\PageArkWordToPDF"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Single"; Tasks: wordcontext
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.docx\shell\PageArkWordToPDF\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" --word-to-pdf ""%1"""; Tasks: wordcontext
Root: HKCU; Subkey: "Software\Classes\Directory\shell\PageArkWordToPDF"; ValueType: string; ValueName: ""; ValueData: "批量 Word 转 PDF"; Flags: uninsdeletekey; Tasks: wordcontext
Root: HKCU; Subkey: "Software\Classes\Directory\shell\PageArkWordToPDF"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName}"; Tasks: wordcontext
Root: HKCU; Subkey: "Software\Classes\Directory\shell\PageArkWordToPDF"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Single"; Tasks: wordcontext
Root: HKCU; Subkey: "Software\Classes\Directory\shell\PageArkWordToPDF\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" --word-to-pdf ""%1"""; Tasks: wordcontext

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
Filename: "ms-settings:defaultapps?registeredAppUser=PDFManage"; Description: "在 Windows 设置中选择默认 PDF 应用"; Flags: shellexec postinstall skipifsilent unchecked
