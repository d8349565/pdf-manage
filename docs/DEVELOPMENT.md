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
- 安装包：安装 Inno Setup 后运行 `build_setup.bat`，输出 `dist-installer/ZhiXuPDF-Setup.exe`。

## 代码位置

| 位置 | 内容 |
| --- | --- |
| `app/__init__.py` | 名称、程序文件名、版本、项目地址 |
| `app/ui.py`、`app/aboutui.py` | 主窗口与关于窗口 |
| `app/workspace.py`、`app/pdfio.py` | 页面状态、撤销和 PDF 读写 |
| `app/printing.py`、`app/pdf2excel/` | 打印和表格转 Excel |
| `app/compression.py`、`app/imaging.py` | 压缩与图片转换 |
| `app/wordconvert.py`、`app/wordui.py` | Word 批量转换 |
| `app/assets/` | 应用资源 |
| `packaging/`、`pdf-manage.spec` | 打包、安装器和第三方许可文件 |
| `tests/` | 回归检查，测试文档由代码生成 |

## 验证

```powershell
.venv\Scripts\python.exe -m compileall -q app packaging
.venv\Scripts\python.exe -m pytest
```

打印机、Microsoft Word、安装器和复杂 PDF 还需要在实际环境中核对。静态打包检查通过不代表全部功能已经验收。

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
