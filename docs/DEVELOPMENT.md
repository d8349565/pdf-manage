# 开发说明

## 运行与构建

使用 Windows x64 和 64 位 Python，推荐 Python 3.12。

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m app
```

运行 `build.bat` 会检查程序版本与当前提交的 Git 标签、安装固定版本的构建依赖、生成图标、检查 Python 语法、打包程序，并静态检查 EXE 的版本与依赖。它不会运行功能测试，也不会启动程序。首次安装依赖需要联网。

本地验证尚未发布的工作区改动时，使用 `build.bat --preview`。预览构建保留当前程序版本，要求同版本标签位于当前提交历史中，明确标记构建日志为本地预览；不创建提交或标签。默认正式构建仍要求当前提交带同版本标签。预览安装器沿用下方固定输出位置，仅供本机验收。

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
| `app/regioncapture.py` | 单页区域图片、批注备注编号与展开、剪贴板和 PNG 输出 |
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

### PDF 转 Word 版式导出

转换窗口默认传入 `pdf_mode="layout"`，电子 PDF 与已识别 PDF 复用离线 DOCX 导出器，未识别扫描页继续在临时目录补识别。更多选项中的 `pdf_mode="word"` 保留原来的 Word 重排路径及首次导入确认。

版式模式逐页定位文字和表格，保留可确认的混合字体、字色、粗斜体、标题层级及合并单元格。电子页的非文字图形在内存副本中移除可编辑文字后渲染为带页面底色的背景，避免Word实际PDF导出产生透明灰边；扫描页按本行字形掩膜去字，保留原始色块、边线和紧凑彩色图形。定位表格保留原生行列与合并关系，原稿背景提供边线、底色和图形外观，单元格使用透明底纹和边框。

背景图片受像素上限约束，不能当作可编辑矢量；与图形交叠而不能可靠拆开的文字行保留原图，识别内容放在图片说明中，不能把说明或空单元格算成可编辑正文。字体不会嵌入DOCX，缺少原字体时由Office替换；扫描字体、OCR内容、倾斜文字和复杂叠放仍需核对。验证必须看Microsoft Word实际打开后的逐页效果，页数和字符总量相同只是必要条件。独立新样例与已观察回归集分开记录，详见 `docs/DOCUMENT_LAYOUT_PLAN.md`；2026-10-24 的冻结算法为 `b494f86ee5879bcbb2aff1596d3f7aa06fc5c1019ef8826b6ace9da8bb9d5a08`；之后的批注外观与彩色提示块修复使应用代码发生变化，当前验证范围见该验收记录的最新条目。

回归代码位于 `tests/test_word_fidelity.py`，只生成独立样例，不读取业务文件。运行样例验证需按项目规则获得授权：

```powershell
python -m pytest tests/test_word_fidelity.py tests/test_word_page_positions.py tests/test_reading_flow.py tests/test_document_parts.py tests/test_ocr_export_input.py -q
```

2026-10-23 获授权后，96 项相关检查通过：83 项版式与导出回归、12 项 Word 窗口与转换保护回归、1 项真实 Word 16.0 渲染检查。真实渲染使用三页生成样例，覆盖混合字体和字色、标题、合并单元格、单元格内边距、色块、装饰线、中文和横向双栏；导出后页数、纸张尺寸及逐页文字完整性通过，抽查的 9 处文字边界最大偏差约 4.45 磅。验证中修复了表格文字内边距丢失的问题。此结果不代表任意业务文档或 WPS 的还原效果。

真实 Word 检查默认跳过。获得样例验证授权并安装 Word 后，可设置 `ZHIXU_WORD_FIDELITY_OFFICE=1`，运行 `tests/test_word_fidelity_office.py`；样例及渲染图片写入 pytest 临时目录。本次最终产物位于工作树的 `build/word-fidelity-accepted/`，不入库。新增测试不读取业务文件，不修改 Word 的 PDF 导入确认设置。

随后使用 `tests/generate_scan_word_scenario.py --output <新目录>` 进行四页采购、报价、验收和结算场景验证。输入为 220 DPI 的纯图片 PDF，每页一张图像、零个可提取字符，经过实际离线 OCR 和 Word 16.0 渲染。原件未改变，输出仍为四页，抽查的项目编号与关键金额字符串均保留；但逐页外观检查未通过：深色标题出现白底黑字、浅色背景丢失、双栏清单被误判为表格、文字字号和间距明显改变、印章图像与识别文字重复叠加。该结果不能沿用电子 PDF 样例的通过结论。首次转换结果与逐页对比位于工作树 `build/scan-scenario-review/`；保留原始失败结果供后续修复对照，不将版式失真归因于内置 DOCX 预览。

针对上述实测问题，`app/ocr/scan_layout.py` 从扫描图像的文字框外沿取背景色、从高对比笔画取字色；保留整页非文字图像，清除需重建的正文笔画，再写入可编辑文字和原生表格。扫描页按可见文字框估算字号，使用近似的微软雅黑字体及独立的行宽约束，不把 OCR 搜索层的拉伸字体当成原字体。单元格文字按几何行重新归组，避免小数点、数字拆行与额外插入空格。重复的勾选列表不再仅凭两栏对齐和底色推断为表格。

红色圆形或方形印章保留原始像素，关联 OCR 文字设为 Word 隐藏文字以避免重复显示；开启“显示隐藏文字”时仍可查看这些内容。普通红色正文不按印章处理。图像总像素仍限制在既有的 400 万像素上限内，扫描背景提高到接近原件的分辨率，导出 DOCX 带有文档级 `doNotAutoCompressPictures` 设置，不修改本机 Word 全局配置。其他印章颜色、复杂照片背景及无法确认的原字体不属于已验证范围。

修复后从同一份 `scanned-input.pdf` 完整重跑真实 OCR、DOCX 导出和 Word 16.0 渲染，原件 SHA-256 不变。最终输出仍为四页，三张原生表格分别有 5、10、4 行；抽查的 9 个项目编号、金额及税率字符串均保留，7 处底色 RGB 最大通道误差为 0～1。逐页人工核对确认白色标题、黄底提示、浅色面板、图示边框和印章单次显示恢复；剩余差异包括部分字体粗细、表格线色、勾选符号和局部文字间距，不能宣传为逐像素一致。最终文件为 `build/scan-scenario-fixed/final.docx`、`final-word.pdf` 和可切换修复前后的 `comparison.html`。

相关回归包含新增 `tests/test_scan_word_fidelity.py`，检查浅色块、白色标题、印章隐藏文字、普通红字、独立勾选清单以及小数片段归组。文字/表格/旋转/密集表格等 76 项检查、图片及 OCR 路由等 21 项检查均通过；最终另复核 10 项扫描、电子 PDF 与真实 Word 渲染检查通过（其中部分用例重复执行）。4 项货币二次识别模型用例不涉及本次排版修复，未运行。

`dist-word-fidelity-preview/`、`dist-word-general-preview/`、`dist-word-repair-preview/` 和 `dist-word-repair-final-preview/` 均为历史预览。当前程序位于 `dist-word-fidelity-round4/ZhiXuPDF/ZhiXuPDF.exe`，没有合并、安装或发布。包检查要求包含 `app.ocr.scan_layout` 与字体匹配所需的 Pillow FreeType；本次实际EXE在隔离设置下通过15项运行检查，包含真实离线OCR和可编辑DOCX导出。预览版仍沿用0.8.5版本资源，以路径和转换窗口的“保留原版式（推荐）”选项区分；测试应从该程序窗口发起并选择新输出文件名，避免安装版右键入口或已有文件跳过规则造成混淆。用户真实发票的原PDF与对应DOCX尚未提供，不能据截图推断该文件已修复。

2026-10-23 通用规则验证：六批不同种子的16个生成页面，每批分别实际运行电子PDF与无文字层扫描PDF转换，再由 Microsoft Word 渲染；共192次尝试、191次成功，第三批保留一次OCR校验失败。第六批32份均逐页核对，仍有2处扫描标题错字及字体、字距差异，不标记为全面高保真通过。修复后的旧案例只作回归，详见 [文档还原验收记录](DOCUMENT_LAYOUT_PLAN.md)。本次相关自动检查另发现一项批注测试夹具缺少 `layout_generation` 的既有失败，已在独立导出的未修改 HEAD 中复现，未顺手修改批注代码。

后续按用户要求，由未读产品代码和旧样例的子代理独立生成8类中文文档、11页原稿，冻结应用后实际转换为16份Word并逐页检查22页渲染结果。独立检查确认电子报销单丢章、扫描印章重影/局部擦除，以及扫描表格结构缺失和断线；本轮不能认定通用高保真通过。证据和逐页评审位于 `build/independent-eval-01/`，详见上述验收记录最上方。此批只作后续回归，修复后另出新案例。

图形分离使用 [PyMuPDF 的文字移除选项](https://pymupdf.readthedocs.io/en/latest/page.html#Page.apply_redactions)，字体通过 [WordprocessingML 字体属性](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.wordprocessing.runfonts)写入。

### 阅读优先启动

普通打开在同一用户、登录会话和安装目录内复用已有阅读窗口，以新标签接收文件；同一路径已打开时激活原标签并保留修改。普通命令行传入多个文件时也分别开标签；显式 `--merge-pdf`、`--split-pdf`、转换和工作进程入口保留各自行为。关闭最后一个阅读窗口后退出，不增加托盘常驻或开机启动。

本机转发使用进程锁和限当前用户访问的命名管道，只发送文件路径。主进程先持锁，阅读窗口构建完成后才创建接收端；发送方收到入队确认才退出，等待最多 5 秒，忙碌时顺序处理。Windows 发送方同时将前台激活权限交给已有进程，恢复窗口时保留最大化状态。

整理工具栏和导出面板按需创建，保留原有偏好。首图保留字体兼容和批注外观，文字命中框及悬停摘要稍后在串行队列准备；滚动后也准备缓存命中的可见邻页。当前页复用一份绘制指令和最多两份按提取参数区分的文字解析结果；文字缓存保留原页面引用，关闭或内容版本变化时释放。

普通阅读打开先完成原始字节快照、密码、基础权限和准确页面几何，并尝试一批至多 256 个对象或约 10ms 的签名检查。简单文件直接准备完成，避免短暂只读提示引起布局变化；复杂文件的剩余检查交给首图后的串行队列。检查中保留阅读及基础权限允许的复制和打印，写出与编辑等待检查结束；用户直接请求编辑时先完成检查再执行，异常仍按原规则只读保护。低层 `PdfStore.load()` 默认继续完整检查，只有阅读入口显式选择延后。

`PAGEARK_STARTUP_LOG` 默认关闭；启用后记录 `first_readable_painted` 以及每份文档的载入、实际绘制、辅助信息和保护检查阶段。`request_id` 是本进程内的数字序号，不记录路径、密码或文档内容。窗口出现、图片准备就绪和实际绘制是不同指标，比较前后版本时必须使用同一指标；独立进程重复启动也不代表清空了系统缓存。

### 阅读性能诊断

阅读预览对部分 Identity 映射的 OpenType CFF 字体做本机兼容处理，使用固定版本的 fonttools 解析字符集并核对字符映射。只有命中特征时才建立内存预览副本，保存和导出仍使用原始来源字节。PyInstaller 显式收集所需的动态字体表读取器，第三方许可随程序放入 licenses/。低倍率阅读在滚动停稳后复用既有区域渲染流程，按当前像素密度更新清晰层。

阅读渲染共用串行优先队列，快速滚动时优先显示缓存或低清预览，停稳约 120ms 后请求清晰图。正文缓存仍为 64MiB；缩略图的原始缓存和显示位图合计按 128MiB 预算淘汰。普通批注悬停使用渲染时准备的摘要，不提取整页文字或分析表格。

诊断默认关闭。需要在本机比较阅读表现时，可仅为本次源码运行设置日志位置：

```powershell
New-Item -ItemType Directory -Path build -Force | Out-Null
$env:PAGEARK_READING_LOG = Join-Path $PWD 'build\reading-performance.jsonl'
.venv\Scripts\python.exe -m app
Remove-Item Env:PAGEARK_READING_LOG
```

日志只追加界面事件间隔、排队与渲染耗时、缓存命中、淘汰数量和位图缓存占用，不记录路径、页码、关键词、密码或文档内容。诊断不会自行打开 PDF 或开展模拟测试；设置该变量后，由用户打开需要核对的文件并操作。

批注与文字框选也复用此日志。`annotation_snapshot` 记录批注快照准备耗时、缓存命中及是否仅取几何摘要；`annotation_preview`、`annotation_cache`、`annotation_apply`、`annotation_refresh` 分别记录候选预览、图片缓存、工作区应用和视图刷新耗时；`text_selection_snapshot` 记录首次框选从请求到文字准备完成的等待时间。缓存命中耗时为实际查找时间；后台排队计入等待时间，不将这些记录当作原反馈文件的修复前后对照。

几何批注冷缓存只在后台准备摘要；文字类工具冷缓存通过既有后台任务准备轻量快照，快照返回后核对页面和工具是否仍相同。文字框选使用阅读串行队列，准备期间记住拖动终点和松开状态，取消、切页或文档变化后丢弃结果；表格排版仍在复制时按需准备。缓存查询不会等待原生渲染锁。

批注摘要和编辑准备使用 `iter_annotations()` 沿原生批注链顺序读取，避开固定版本引擎逐条按 xref 从头查找的重复工作；保留标识去重以及链接、弹出框和表单的原有边界。框选文字先筛选相交的块与行，再执行原来的逐字命中，不据页面是否有图片或线条跳过表格分析。

#### 仅生成样本的批注性能对照

`packaging/benchmark_annotations.py` 只接受自身生成且 SHA-256 与清单一致的样本；生成输入不会覆盖已有文件。测量隔离配置、临时文件与诊断，记录应用源码摘要、输入摘要、依赖、原始文字／表格／渲染调用及 20ms Qt 心跳。创建独立目录后先生成一次，基线与候选共用该文件：

```powershell
.venv\Scripts\python.exe packaging/benchmark_annotations.py --generate --sample build/annotation-check/generated.pdf
.venv\Scripts\python.exe packaging/benchmark_annotations.py --root <基线源码目录> --sample build/annotation-check/generated.pdf --output build/annotation-check/baseline-1.json --label baseline-1
.venv\Scripts\python.exe packaging/benchmark_annotations.py --root . --sample build/annotation-check/generated.pdf --output build/annotation-check/candidate-1.json --label candidate-1
```

基线目录可由 `git archive` 导出对应提交的 `app/`，无需切换当前分支。每轮使用新的输出文件，顺序运行两版并交替先后，至少三轮后比较相同阶段的中位数与最大心跳间隔；不要同时运行其他回归来干扰测量。冷／热指应用缓存状态，报告明确记录重复选取前是否有可复用快照；不将进程重启当作清空系统缓存。重新生成的 PDF 元信息可能改变，以本轮输入清单和实际 SHA-256 为准。

经明确授权可运行 `tests/test_annotation_large_pdf.py`；默认在测试临时目录生成大文件，也可将 `ZHIXU_ISSUE33_GENERATED_SAMPLE` 指向上述已验证生成样本以复用输入。其余必要回归见最新验收记录，未获授权时不自动运行模拟检查。性能检查不读取或替换用户业务文件，也不代替安装版与真实屏幕验收。

实际验收应保持电脑、文档、窗口尺寸、系统显示缩放及阅读缩放一致，比较快速上下滚动、立即反向滚动、返回刚读过的页、停稳后的清晰等待，并检查搜索、复制、批注、撤销和关闭文档。源码静态检查通过不代表已获得真实文档的提速结果。

OCR 的 PRD 评估、独立 CPU 增强包构建、合成 PoC、性能记录和未完成的发布验收见 [OCR.md](OCR.md)。OCR 推理依赖只安装到单独构建目录，不添加到基础版依赖清单。

## 更新检查

“关于”的更新按钮旁提供带云盘图标的“蓝奏云”下载入口，无需先检查更新即可使用。点击时先将提取密码 `atxq` 复制到剪贴板，再由系统浏览器打开固定分享链接 `https://leezfy.lanzouq.com/b02qtfd77g`；窗口会显示复制结果，浏览器打开失败时显示可手动访问的链接。此入口不参与版本查询。

“关于”支持手动检查最新正式版和打开对应 Release 页面。自动检查默认开启，已有的开启或关闭选择继续保留。开启时，启动 15 秒后判断是否需要检查，之后每小时判断一次是否到期；在关于窗口重新开启后也沿用此安排。自动请求间隔至少 24 小时，失败也计入间隔，手动检查不受此间隔限制。关闭选项立即停止后续定时检查，后续启动也不安排后台检查；已经发出的请求仍会正常结束。偏好与最近一次版本结果保存在现有应用设置的 `updates/` 下。资源管理器 PDF 合并窗口使用相同入口，纯转换窗口不主动检查。

检查优先使用 GitHub 公开接口 `GET /repos/d8349565/pdf-manage/releases/latest`（若遇 `403` / `429` 限流则自动回退至 `HEAD /d8349565/pdf-manage/releases/latest` 读取 `302 Location` 标签），不需要令牌，不读取或上传文档、文件路径及账号信息。按数字比较 `X.Y.Z` / `vX.Y.Z`，忽略预发布和草稿，接口模式下要求 Release 已完成上传非空的 `ZhiXuPDF-Setup.exe`（兼容历史上的 `ZhiXuPDF-Setup vX.Y.Z.exe` 以及 GitHub 上传自动替换空格生成的 `ZhiXuPDF-Setup.vX.Y.Z.exe`，版本须与标签一致）。发布时应先上传安装包，再将 Release 设为正式最新版本；只推送标签或生成 Actions artifact 不会提供应用更新。

网络使用异步 Qt Network，保留证书验证、拒绝重定向、限制响应为 1 MiB，并设 10 秒无数据超时与 15 秒总超时。安装包不在程序内下载或执行；用户点击后，由系统浏览器打开固定仓库的 Release 页面，下载后先关闭纸序 PDF，再运行原有按用户安装器。关闭主窗口取消在途请求。

`pdf-manage.spec` 保留 `QtNetwork.pyd`、`Qt6Network.dll` 和 Windows 原生 `tls/qschannelbackend.dll`；继续排除 Python SSL / OpenSSL，不引入新的下载器、提权、计划任务或常驻更新服务。`packaging/verify_exe.py` 会静态核对这些打包依赖。Qt 官方的 [TLS 打包说明](https://doc.qt.io/qt-6/ssl.html#considerations-while-packaging-your-application) 支持 Windows 仅携带 Schannel 后端。

该方案减少程序自行更新的行为，不能保证消除安全软件误报。SmartScreen 会检查文件及发布者信誉，新文件即使已签名也可能提示；正式分发应使用一致的可信发布者身份签名程序与安装器，保持现有目录式打包、禁用 UPX，不能引导用户关闭杀毒或绕过证书验证。依据见 [Microsoft SmartScreen 信誉说明](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)。

新增回归代码见 `tests/test_updates.py`，本次仅做源码与打包配置静态检查，未运行模拟测试、联网功能验收、安全软件扫描或安装升级验证。发布前需由用户核对手动检查、自动提示与关闭选项、网络失败后重试、打包后的 HTTPS 能力及保留文件关联的覆盖安装。

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

## OFD 导入

`app/ofd.py` 将常见发票和回单转换为完整的 PDF 工作副本，使用已有 PyMuPDF、Pillow 和系统字体读取能力，不新增依赖。`PdfStore.load` 通过现有 PDF 工作进程执行转换；主进程轮询进度，取消时终止工作进程并清理专属缓存。成功后来源路径仍指向 OFD 原件，`source.data` 保存 PDF 字节快照，供阅读、打印和写出复用。保存入口强制另存 PDF，底层同时拒绝 OFD 后缀和原件文件身份对应的目标。

转换器遵循资源引用、页面实际尺寸、模板和图层顺序、图元边界裁剪及仿射变换。设置归档体积、资源大小、XML 节点、字体数量、图元数量和嵌套深度限制；不执行文档动作，也不读取与显示无关的附件和自定义业务标签。未知可见图元、数字签名、使用权限声明和复杂裁剪等未支持结构会拒绝转换，避免静默输出缺失内容的副本。字体替代、专用字形会单独提示；该路径不验证或迁移数字签名。

`tests/test_ofd.py` 包含生成文档的文字定位回归，以及使用外部授权样本的可选验证。外部样本通过环境变量 `ZHIXU_OFD_SAMPLE` 指定，并将 pytest 的 `--basetemp` 指向仓库外的专用验证目录；未指定样本时仅跳过样本检查。样本检查覆盖打开、像素预览、保存后图像一致性、打印到 PDF、界面取消和另存、原件哈希与覆盖保护。真实样本及所有派生 PDF、图片、运行日志均不得入库；不要将文件名、业务正文或本机路径固化到测试与文档中。当前已用一份授权发票完成上述验证，尚未验证实体打印机和更多银行回单版式。

2026-10-21：缺省与显式 `DeltaX` 的文字定位检查，以及 `tests/test_ocr_ui.py` 中的 OCR 另存成功、取消和已有界面检查，共 8 项通过。本次仅使用生成文档；未读取业务样本、未执行真实 OCR 推理或实体打印。
