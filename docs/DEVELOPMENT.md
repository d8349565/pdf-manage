# 开发说明

## 运行与构建

使用 Windows x64 和 64 位 Python，推荐 Python 3.12。

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m app
```

运行 `build.bat` 会检查程序版本与当前提交的 Git 标签、安装固定版本的构建依赖、生成图标、检查 Python 语法、打包程序，并静态检查 EXE 的版本与依赖。它不会运行功能测试，也不会启动程序。首次安装依赖需要联网。

- 程序目录：`dist/ZhiXuPDF/`，不能只复制 EXE。
- 构建报告：`build/build-report.json`，仅保留本机。
- 安装包：安装 Inno Setup 后运行 `build.bat`（或 `build_setup.bat`），输出 `dist-installer/ZhiXuPDF-Setup.exe`。

**安装器构建规则**：所有本地、CI、验收及预览构建，只允许在仓库根目录的 `dist-installer/` 内直接生成安装器，文件名固定为 `ZhiXuPDF-Setup.exe`。
本仓库位于 `D:\code\pdf-manage` 时，完整路径为 `D:\code\pdf-manage\dist-installer\ZhiXuPDF-Setup.exe`。
禁止在 `build/`、临时目录或其他目录编译安装器后再搬运，禁止修改 `/O` 为其他目录或使用 `/F` 改名。
基础程序目录仍为 `dist/ZhiXuPDF/`，OCR 增强包仍在独立的 `dist-ocr/`；这些产物都不加入 Git。

基础安装器只包含 `dist/ZhiXuPDF/`，不打入 OCR 推理依赖或模型。可选增强包的构建命令见 `docs/OCR.md`；
增强包、模型缓存和构建依赖分别保存在忽略的 `dist-ocr/` 与 `build/`，不能加入 Git。
升级主程序的主/次版本时必须重建匹配系列的增强包。
没有 Inno Setup 编译器时脚本仅完成基础程序，不将上一轮安装包报告为本次产物。
版本标签推送后，Windows 检查流程分别构建并保存基础安装 EXE、独立 OCR ZIP 和验收报告，不将二进制加入 Git。
可用 `python packaging/verify_exe.py --skip-runtime --installer dist-installer/ZhiXuPDF-Setup.exe` 核对安装器与程序的版本一致。

## 代码位置

| 位置 | 内容 |
| --- | --- |
| `app/__init__.py` | 名称、程序文件名、版本、项目地址 |
| `app/ui.py`、`app/aboutui.py` | 主窗口与关于窗口 |
| `app/workspace.py`、`app/pdfio.py` | 页面状态、撤销和 PDF 读写 |
| `app/printing.py`、`app/pdf2excel/` | 打印和表格转 Excel |
| `app/compression.py`、`app/imaging.py` | 压缩与图片转换 |
| `app/ocr/`、`app/ocrui.py` | 可选离线 OCR、文字层输出与操作弹窗 |
| `app/storage.py`、`app/storageui.py` | 存储偏好、组件迁移、任务临时目录与受限清理 |
| `app/rasterpdf.py`、`app/rasterui.py` | 图片版 PDF 输出与未知签名的安全影像副本 |
| `app/textselection.py`、`app/ocr/word_export.py` | 阅读区框选复制与 OCR 文字层导出可编辑 DOCX |
| `app/wordconvert.py`、`app/wordui.py` | Word 批量转换 |
| `app/assets/` | 应用资源 |
| `packaging/`、`pdf-manage.spec` | 打包、安装器和第三方许可文件 |
| `tests/` | 回归检查，测试文档由代码生成 |

## 验证

安装器的 `pdfcontext` 选项按当前用户登记 PDF 右键入口。拆分使用 `--split-pdf`；合并由 `--pdf-shell-server` 进程外 COM 接收完整选区，每次调用打开独立窗口。窗口全关后空闲 60 秒退出；升级取消该选项或卸载会清理两项入口及专用 CLSID。

`markdowncontext` 选项为 `.md`、`.markdown` 登记“转换为 PDF”入口，命令为 `--markdown-to-pdf "输入.md"`，直接打开预填文件的 Markdown 转换窗口。升级取消该选项或卸载时清理专用菜单项。Markdown 和 Word 转换窗口支持拖入本机文件；Word 窗口复用现有文件夹扫描、去重及子文件夹选项。修改右键入口后需要重新构建并安装更新的安装包。

源码运行也可用 `python -m app --split-pdf "输入.pdf"` 或 `python -m app --merge-pdf "一.pdf" "二.pdf"` 检查入口预填行为。`tests/test_shell_split.py` 使用合成 PDF 检查入口加载、每 N 页拆分、来源保护和窗口正常退出；页面插入回归见 `tests/test_insert_pages.py`。资源管理器多选与安装卸载需要重新构建安装包实机验收。本轮检查与验收项目见 `docs/ACCEPTANCE.md` 顶部。

```powershell
.venv\Scripts\python.exe -m compileall -q app packaging
.venv\Scripts\python.exe -m pytest
```

打印机、Microsoft Word、安装器和复杂 PDF 还需要在实际环境中核对。静态打包检查通过不代表全部功能已经验收。

OCR 的 PRD 评估、独立 CPU 增强包构建、合成 PoC、性能记录和未完成的发布验收见 [OCR.md](OCR.md)。OCR 推理依赖只安装到单独构建目录，不添加到基础版依赖清单。

## 版本与提交规则

初始版本为 `0.1.0`，初始化提交对应标签 `v0.1.0`，描述为“初始化仓库”。

以后程序版本与 Git 版本保持一致：

1. 每次提交前更新 `app/__init__.py` 的 `__version__`，格式为三段数字。
2. 修复问题或小改动递增第三段，新增完整功能递增第二段。
3. 提交后创建同版本的注释标签，例如程序 `0.1.1` 对应 `v0.1.1`。
4. 同一标签不得移动或覆盖；程序、EXE 文件属性、安装包和 Git 标签使用同一版本。

`packaging/check_version.py` 会阻止版本与当前提交标签不一致的 Git 工作区打包。GitHub 源码 ZIP 没有 Git 元数据时，使用源文件中的版本。

BAT 文件保留 CRLF 行尾；`build_setup.bat` 使用 GBK 编码和 936 码页，Git 属性已声明编码。

Windows 安装与文件关联沿用 `PDFManage` 注册标识，以保持原有打开方式的兼容性。
