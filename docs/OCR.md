# 离线 OCR 增强功能

纸序 PDF 0.4 系列支持通过独立增强包在 Windows x64 CPU 上识别扫描件。
基础程序不包含 OCR 模型和推理库，未安装增强包时其余功能正常使用。

## 安装与使用

1. 获取与当前主程序系列对应的 `ZhiXuPDF-OCR-win-x64.zip`。
2. 打开“组织 → 文字识别”，选择“安装本地 OCR 增强包”；PDF 转 Word 窗口也提供安装入口。
3. 选择当前页、全部页面或指定页码，默认跳过已有有效文字的页面，生成新的 `_OCR.pdf`。
4. 打开识别副本后，使用 Ctrl+F 搜索或拖动框选、Ctrl+C 复制。金额、编号和特殊符号请核对原件。

安装目录为 `%LOCALAPPDATA%/ZhiXuPDF/ocr/`，不需要用户安装 Python、配置环境变量或联网下载。
清单校验包版本、主程序系列、路径及全部文件；重装保留上一份组件备份。
主程序的主/次版本升级后需要对应增强包，例如 0.4 系列不能使用 0.3 系列包。
已有 PDF 中的错误识别文字不会随着软件升级自行改变，应从原扫描 PDF 重新识别。

## 文档导出

- PDF 转 Word：扫描页在任务临时目录中自动补识别；已有 OCR 文本直接生成可编辑 DOCX，无需 Word。
- 普通电子 PDF 重排、Word 转 PDF：使用本机 Microsoft Word；PDF 原生导入要求 Word 2013 或更高版本。
- PDF 转 TXT：保留阅读顺序、自然段和表格制表符，不包含字体或边框。
- PDF 表格转 Excel：支持电子表格和已完成 OCR 的扫描表格，输出有证据的行列及合并单元格。

可编辑导出恢复自然段、缩进、原生分栏、表格、重复页眉页脚和可确认的图像区域。
严重倾斜、透视、手写、不规则表格及任意复杂版面的精确外观恢复尚未验收。
具体算法与边界见 [文档导出优化方案](DOCUMENT_LAYOUT_PLAN.md)。

## 文件保护

始终保留源 PDF。识别使用独立文档和 CPU 推理进程；临时文件校验后提交，取消不留下不完整的最终输出。
未保存修改须先保存，源文件在任务中变化时不提交结果；签名文件及权限不足的文档被拒绝。
识别副本保留密码加密设置；Word/TXT 导出加密文件需先另存获授权的解密副本。
表格不能承载全部文字时，导出器保留文字和图像并提示结构降级，不静默丢失内容。

## 技术与构建

固定 RapidOCR 3.9.2、ONNX Runtime CPU 1.23.2；使用 PP-OCRv6-small 检测/识别模型和轻量方向模型。
完整依赖在 `packaging/requirements-ocr.txt`。识别字典来自对应 ONNX 元数据，模型及字典绑定校验。
推理进程禁止在线下载及网络请求，只接收本地 PNG。

```powershell
python -m pip install -r packaging/requirements-build.txt
python -m pip install --target build/ocr-deps -r packaging/requirements-ocr.txt
python packaging/build_ocr.py --deps build/ocr-deps --download-models --output dist-ocr/v0.4.0
```

首次构建模型显式联网下载；已有经过校验的 `build/ocr-models` 时去掉 `--download-models`。
每次增强包构建使用新的输出目录，脚本不会覆盖已有包。
增强 ZIP 与 `build/` 模型、依赖缓存均不进入 Git。

基础程序和安装器继续使用 `build.bat`。仅将基础 `dist/ZhiXuPDF/` 放入安装器，增强包单独分发。
无需在安装脚本中调用模型下载或将依赖安装到用户 Python 环境。

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q --basetemp build/tests-release --junitxml=build/regressions.xml
python packaging/ocr_poc.py --runtime dist-ocr/v0.4.0/package --output build/poc-release
python packaging/verify_exe.py --run-runtime --ocr-runtime dist-ocr/v0.4.0/package
python packaging/measure_ocr.py --base dist/ZhiXuPDF --ocr dist-ocr/v0.4.0 --report build/ocr-size-report.json
```

## 当前验收范围

已核验合成文档的中文提取、搜索、复制、150/200/300 DPI、页面旋转与裁剪、取消、签名/权限/加密、独立安装、原件保护。
九页实际模型 PoC 为八页识别、一页跳过、零失败，原搜索 UI 命中八处，页面渲染一致。
获授权单份办公样本的两张表格、TXT/Word/Excel 和关键金额交叉核验通过，实际 Word 16.0 渲染为一页，原件哈希不变；用户确认最新版正常。
另有独立双栏、三页页眉页脚和扫描插图通过实际 Word 互操作。

上述结果不能作为 30 页真实办公测试集的 CER 结论。固定目标硬件、Windows 10、真实打印与安装/卸载仍需补齐。
首次合成性能记录为十页 46.250 秒、一百页 375.391 秒，观测工作集约 0.9～1.0 GiB；不作为所有机器的性能承诺。
模型压缩大小不代表完整增强包大小，发行体积以同时生成的测量报告为准。

资料：[RapidOCR 使用说明](https://rapidai.github.io/RapidOCRDocs/main/install_usage/rapidocr/usage/)、
[固定模型清单](https://github.com/RapidAI/RapidOCR/blob/v3.9.2/python/rapidocr/default_models.yaml)、
[PyMuPDF 文字接口](https://pymupdf.readthedocs.io/en/latest/page.html)。
