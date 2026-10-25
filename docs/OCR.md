# 离线 OCR 增强功能

纸序 PDF 0.4 系列支持通过独立增强包在 Windows x64 CPU 上识别扫描件。
基础程序不包含 OCR 模型和推理库，未安装增强包时其余功能正常使用。

## 安装与使用

1. 获取与当前主程序系列对应的 `ZhiXuPDF-OCR-win-x64.zip`。
2. 打开“扫描 → 文字识别（OCR）”，选择“安装本地 OCR 增强包”；PDF 转 Word 窗口也提供安装入口。
3. 选择当前页、全部页面或指定页码，默认跳过已有有效文字的页面，生成新的 `_OCR.pdf`。
4. 打开识别副本后，使用 Ctrl+F 搜索或拖动框选、Ctrl+C 复制。金额、编号和特殊符号请核对原件。

默认安装目录为 `%LOCALAPPDATA%/ZhiXuPDF/ocr/`，安装窗口显示完整路径并允许选择其他磁盘。
不需要用户安装 Python、配置环境变量或联网下载。
清单校验包版本、主程序系列、路径及全部文件；重装保留上一份组件备份。
主程序的主/次版本升级后需要对应增强包，例如 0.4 系列不能使用 0.3 系列包。
已有 PDF 中的错误识别文字不会随着软件升级自行改变，应从原扫描 PDF 重新识别。

## 文件位置与空间管理

从“文件 → 存储管理”查看位置、统计用量、打开目录和更改保存位置；OCR 窗口也提供“存储位置”入口。
选择 OCR 或缓存所在文件夹后，程序在其中建立 `ZhiXuPDF/ocr` 或 `ZhiXuPDF/cache`，实际完整路径会显示在窗口中。

| 内容 | 默认位置与生命周期 |
| --- | --- |
| 当前 OCR 模型、字典、推理运行库 | `%LOCALAPPDATA%/ZhiXuPDF/ocr/`，可迁移至自选目录 |
| 安装过程 | 安装目录上一级的 `.ocr-install-*`，完成、失败或取消时清理 |
| 重装前组件 | 同一级的 `.ocr-backup-*`，保留到用户确认清理 |
| 任务缓存 | `%LOCALAPPDATA%/ZhiXuPDF/cache/`，可选其他磁盘；识别图像、压缩预览、图片导入、转换快照正常结束后删除 |
| 原 ZIP | 保留在用户选择的原位置，程序不另存、不删除 |
| PDF、Word、Excel、TXT、图片输出 | 使用各窗口显示的位置，可设置统一默认输出文件夹，每次仍可另选 |
| 输出校验临时文件 | 跟随输出目录，保证完整提交；正常结束或取消时删除 |
| 页面预览、缩略图、搜索缓存 | 内存中的有界缓存，关闭文档/程序后释放，不形成磁盘文件 |
| 窗口和存储偏好 | Windows 当前用户注册表 `HKEY_CURRENT_USER/Software/PDFManage/ZhiXuPDF`；窗口显示实际保存位置 |

迁移 OCR 先复制、校验完整性，再保存新位置；取消、校验失败或设置无法保存时保留原位置。
原组件目录保留以便恢复，并在“以前位置”中列出。确认新位置可正常识别后，可选择“清理以前组件”释放原磁盘空间。
缓存切换只影响新任务，旧位置仍可查看和清理。更改默认输出不会移动或删除已经导出的文件。
OCR 与缓存使用独立目录，输出不得保存在缓存目录内。

清理需在界面中确认。缓存只清理带有本程序任务标记、且已结束的任务目录；活动任务通过跨进程锁保护。
旧备份与以前组件只清理通过完整清单校验的目录；未登记文件、目录链接、正在使用的组件会跳过。
清理以前组件前还须验证当前安装完整，避免丢失恢复副本。当前组件、原 ZIP、源文档和正常导出结果不在清理范围内。
异常断电可能留下安装或输出校验临时文件，其所在位置按上表显示规则人工核对，不扫描用户文档目录进行自动删除。

## 文档导出

- PDF 转 Word：扫描页在任务临时目录中自动补识别；已有 OCR 文本直接生成可编辑 DOCX，无需 Word。OCR 和表单页面优先按原页定位文字、表格和图片，普通正文保留段落结构。
- 普通电子 PDF 重排、Word 转 PDF：使用本机 Microsoft Word；PDF 原生导入要求 Word 2013 或更高版本。
- PDF 转 TXT：保留阅读顺序、自然段和表格制表符，不包含字体或边框。
- PDF 转 Excel：可选“全部内容（按页导出）”与“仅表格”。全部内容保留正文、表格、图片和无表格页面；表格仍保留数值格式与合并结构。未完成 OCR 的扫描页保留图像，先 OCR 后才能编辑文字。

可编辑导出恢复自然段、缩进、原生分栏、表格、重复页眉页脚和可确认的图像区域。
Word 定位版式适合保留扫描表单外观，新增较长内容时可能需要调整文字框或表格尺寸；Excel 全部内容按阅读顺序组织，压缩空白，不承诺像素一致的页面外观。
Word/Excel 在任务私有文档中统一页面显示方向、正文方向和表格坐标，并保留原裁剪区域；全内容工作表按共用 XY 网格放置，同一行的字段保持从左至右，不逐块追加成竖向列表。
确认的扫描背景颜色重建为目标文字框或单元格底纹；保留残余边框、印章与插图，避免底图遮住已导出的文字。无法确认的背景及复杂手写仍需核对。
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

密集表格优先使用模型返回的逐字坐标写入文字层，保留列间空白；逐字内容不完整或方向不适用时保留整行结果。
扫描表格网格支持合并断续线段，边缘缺失时仅在至少三条横线端点一致且上下边界完整的情况下补齐外框。
复制、TXT、Word 和 Excel 复用该网格。启用逐字定位需要同时更新主程序与 OCR 增强包，并从原扫描 PDF 重新识别。
协议仍为 1：主程序显式请求逐字框，旧增强包回退为整行；新增强包收到旧主程序请求时仍返回整行。

Word 排版与 Excel 组织参考 [Adobe 的文字流与页面版式说明](https://helpx.adobe.com/ph_fil/acrobat/how-to/convert-pdf-retain-page-layout.html) 和 [Excel 工作表设置](https://helpx.adobe.com/acrobat/desktop/save-export-documents/convert-to-other-formats/pdf-to-excel.html)，本项目的坐标放置与文字完整性校验为独立实现。

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

## 增强包体积优化

在模型、字典、OCR 参数和核心计算 DLL 不变的前提下，精简了未调用的视频插件、
可选图像编解码/字体显示组件、网络 TLS 组件及其他推理后端，并对相同 DLL 去重。
保留 NumPy、Shapely 自身的库目录及全部许可说明；ZIP 仍采用通用 Deflate 格式，压缩级别提高到 9。

| 体积 | 原包 | 精简包 | 减少 |
| --- | --- | --- | --- |
| ZIP | 120.10 MiB | 91.48 MiB | 23.83% |
| 解压目录 | 275.20 MiB | 202.16 MiB | 26.54% |

九页合成文档及一页获授权原稿的新旧包对照中，文字、包围盒、多边形和置信度逐项完全一致。
模型与核心计算二进制校验值相同；精简 ZIP 实际安装、九页模型 PoC 和现有 0.4.0 基础程序的十项联合运行检查通过。
这证明当前验证范围内的输出一致，不作为任意输入的准确率承诺。
已有 0.4 系列主程序可直接安装精简包。组件更新仍保留旧安装备份，历史备份不计入当前组件目录的体积。
本机对照记录位于 `build/ocr-slim-validation/`，不加入 Git。

DLL 目录收集依据 [PyInstaller hooks 说明](https://pyinstaller.org/en/latest/hooks.html)；
视频插件用途见 [OpenCV 官方仓库说明](https://github.com/opencv/opencv-python/issues/353)。

资料：[RapidOCR 使用说明](https://rapidai.github.io/RapidOCRDocs/main/install_usage/rapidocr/usage/)、
[固定模型清单](https://github.com/RapidAI/RapidOCR/blob/v3.9.2/python/rapidocr/default_models.yaml)、
[PyMuPDF 文字接口](https://pymupdf.readthedocs.io/en/latest/page.html)。
