#define MyAppExeName "ZhiXuPDF.exe"
#ifndef MyAppPackageDir
  #define MyAppPackageDir "..\dist\ZhiXuPDF"
#endif
#define MyAppName GetStringFileInfo(MyAppPackageDir + "\" + MyAppExeName, "ProductName")
#define MyAppVersion GetStringFileInfo(MyAppPackageDir + "\" + MyAppExeName, "ProductVersion")
#define MyAppProgId "PDFManage.Document"
#dim ImageExtensions[7] {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
#define ImageIndex

[Setup]
AppId={{0E4A38D0-8475-4A57-98C2-1AD3F468A0E8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
VersionInfoVersion={#MyAppVersion}
VersionInfoProductVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\PDFManage
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; All installer builds use this directory and filename, including preview builds.
OutputDir=..\dist-installer
OutputBaseFilename=ZhiXuPDF-Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern windows11
WizardSizePercent=120
WizardImageFile=wizard-large.bmp
WizardSmallImageFile=wizard-small.bmp
WizardImageBackColor=$EEF4ED
DisableWelcomePage=no
DisableProgramGroupPage=yes
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ChangesAssociations=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"

[LangOptions]
DialogFontName=Microsoft YaHei UI
DialogFontSize=9
WelcomeFontName=Microsoft YaHei UI
WelcomeFontSize=17

[Messages]
WelcomeLabel1=欢迎使用纸序 PDF
WelcomeLabel2=阅读、整理、转换与压缩，在本机轻松处理文档。%n%n安装向导将为您准备好程序和常用快捷工具。
WizardSelectTasks=设置快捷工具
SelectTasksDesc=选择您常用的打开方式
SelectTasksLabel2=右键快捷工具统一启用，只会在适用的文件上显示对应操作。
FinishedLabel=纸序 PDF 已准备就绪。%n%n您可以打开程序开始处理文档；已勾选的快捷工具也已配置完成。

[Files]
Source: "{#MyAppPackageDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MyAppPackageDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："
Name: "contextmenus"; Description: "启用右键快捷工具（PDF、图片、Word、Markdown）"; GroupDescription: "文件操作："

[Registry]
; Remove only this application's entries when upgrading with the unified task off.
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.doc\shell\PageArkWordToPDF"; ValueType: none; Flags: deletekey; Tasks: not contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.docx\shell\PageArkWordToPDF"; ValueType: none; Flags: deletekey; Tasks: not contextmenus
Root: HKCU; Subkey: "Software\Classes\Directory\shell\PageArkWordToPDF"; ValueType: none; Flags: deletekey; Tasks: not contextmenus
#sub ImageRegistry
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\{#ImageExtensions[ImageIndex]}\shell\ZhiXuImageToPDF"; ValueType: none; Flags: deletekey; Tasks: not contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\{#ImageExtensions[ImageIndex]}\shell\ZhiXuCompressImage"; ValueType: none; Flags: deletekey; Tasks: not contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\{#ImageExtensions[ImageIndex]}\shell\ZhiXuImageToPDF"; ValueType: string; ValueName: ""; ValueData: "图片转 PDF"; Flags: uninsdeletekey; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\{#ImageExtensions[ImageIndex]}\shell\ZhiXuImageToPDF"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName}"; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\{#ImageExtensions[ImageIndex]}\shell\ZhiXuImageToPDF"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Single"; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\{#ImageExtensions[ImageIndex]}\shell\ZhiXuImageToPDF\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" --images-to-pdf ""%1"""; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\{#ImageExtensions[ImageIndex]}\shell\ZhiXuCompressImage"; ValueType: string; ValueName: ""; ValueData: "压缩图片"; Flags: uninsdeletekey; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\{#ImageExtensions[ImageIndex]}\shell\ZhiXuCompressImage"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName}"; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\{#ImageExtensions[ImageIndex]}\shell\ZhiXuCompressImage"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Single"; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\{#ImageExtensions[ImageIndex]}\shell\ZhiXuCompressImage\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" --compress-image ""%1"""; Tasks: contextmenus
#endsub
#for {ImageIndex = 0; ImageIndex < DimOf(ImageExtensions); ImageIndex++} ImageRegistry
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.md\shell\ZhiXuMarkdownToPDF"; ValueType: none; Flags: deletekey; Tasks: not contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.markdown\shell\ZhiXuMarkdownToPDF"; ValueType: none; Flags: deletekey; Tasks: not contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.md\shell\ZhiXuMarkdownToPDF"; ValueType: string; ValueName: ""; ValueData: "转换为 PDF"; Flags: uninsdeletekey; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.md\shell\ZhiXuMarkdownToPDF"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName}"; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.md\shell\ZhiXuMarkdownToPDF"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Single"; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.md\shell\ZhiXuMarkdownToPDF\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" --markdown-to-pdf ""%1"""; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.markdown\shell\ZhiXuMarkdownToPDF"; ValueType: string; ValueName: ""; ValueData: "转换为 PDF"; Flags: uninsdeletekey; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.markdown\shell\ZhiXuMarkdownToPDF"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName}"; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.markdown\shell\ZhiXuMarkdownToPDF"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Single"; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.markdown\shell\ZhiXuMarkdownToPDF\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" --markdown-to-pdf ""%1"""; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\PDFManageSplit"; ValueType: none; Flags: deletekey; Tasks: not contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\PDFManageMerge"; ValueType: none; Flags: deletekey; Tasks: not contextmenus
Root: HKCU; Subkey: "Software\Classes\CLSID\{{6C0E4D2A-73C6-4D15-AF0E-619C12573B89}"; ValueType: none; Flags: deletekey; Tasks: not contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\PDFManageSplit"; ValueType: string; ValueName: ""; ValueData: "用纸序 PDF 拆分"; Flags: uninsdeletekey; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\PDFManageSplit"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName}"; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\PDFManageSplit"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Single"; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\PDFManageSplit\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" --split-pdf ""%1"""; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\PDFManageMerge"; ValueType: string; ValueName: ""; ValueData: "用纸序 PDF 合并"; Flags: uninsdeletekey; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\PDFManageMerge"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName}"; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\PDFManageMerge"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Player"; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\PDFManageMerge\DropTarget"; ValueType: string; ValueName: "CLSID"; ValueData: "{{6C0E4D2A-73C6-4D15-AF0E-619C12573B89}"; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\CLSID\{{6C0E4D2A-73C6-4D15-AF0E-619C12573B89}"; ValueType: string; ValueName: ""; ValueData: "纸序 PDF 多选合并入口"; Flags: uninsdeletekey; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\CLSID\{{6C0E4D2A-73C6-4D15-AF0E-619C12573B89}\LocalServer32"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" --pdf-shell-server"; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\CLSID\{{6C0E4D2A-73C6-4D15-AF0E-619C12573B89}\PythonCOM"; ValueType: string; ValueName: ""; ValueData: "app.pdfshell.MergeDropTarget"; Tasks: contextmenus
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
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.doc\shell\PageArkWordToPDF"; ValueType: string; ValueName: ""; ValueData: "Word 转 PDF"; Flags: uninsdeletekey; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.doc\shell\PageArkWordToPDF"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName}"; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.doc\shell\PageArkWordToPDF"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Single"; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.doc\shell\PageArkWordToPDF\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" --word-to-pdf ""%1"""; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.docx\shell\PageArkWordToPDF"; ValueType: string; ValueName: ""; ValueData: "Word 转 PDF"; Flags: uninsdeletekey; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.docx\shell\PageArkWordToPDF"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName}"; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.docx\shell\PageArkWordToPDF"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Single"; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.docx\shell\PageArkWordToPDF\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" --word-to-pdf ""%1"""; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\Directory\shell\PageArkWordToPDF"; ValueType: string; ValueName: ""; ValueData: "批量 Word 转 PDF"; Flags: uninsdeletekey; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\Directory\shell\PageArkWordToPDF"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName}"; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\Directory\shell\PageArkWordToPDF"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Single"; Tasks: contextmenus
Root: HKCU; Subkey: "Software\Classes\Directory\shell\PageArkWordToPDF\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" --word-to-pdf ""%1"""; Tasks: contextmenus

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
Filename: "ms-settings:defaultapps?registeredAppUser=PDFManage"; Description: "在 Windows 设置中选择默认 PDF 应用"; Flags: shellexec postinstall skipifsilent unchecked

[Code]
procedure InitializeWizard;
var
  ToolsHint: TNewStaticText;
begin
  WizardForm.WelcomeLabel1.Font.Color := $3A4A17;
  WizardForm.FinishedHeadingLabel.Font.Color := $3A4A17;
  WizardForm.TasksList.Height := WizardForm.TasksList.Height - ScaleY(58);
  ToolsHint := TNewStaticText.Create(WizardForm);
  ToolsHint.Parent := WizardForm.SelectTasksPage;
  ToolsHint.Left := WizardForm.TasksList.Left;
  ToolsHint.Top := WizardForm.TasksList.Top + WizardForm.TasksList.Height + ScaleY(12);
  ToolsHint.Width := WizardForm.TasksList.Width;
  ToolsHint.Height := ScaleY(46);
  ToolsHint.AutoSize := False;
  ToolsHint.WordWrap := True;
  ToolsHint.Caption := '图片：转为 PDF、压缩图片' + #13#10 + '文档：PDF 拆分合并、Word 与 Markdown 转 PDF';
  ToolsHint.Font.Color := $656B52;
end;

#ifdef ZhiXuPreprocessOutput
#call SaveToFile(ZhiXuPreprocessOutput)
#endif
