"""OCR 子包对外数据结构与接口契约。

本文件只放纯数据类与函数签名级别约定，**禁止依赖** RapidOCR / PyMuPDF / PyQt6
等具体实现，便于 PoC、单元测试与未来引擎替换。

约定来自 PRD §5.3 / §6 / §7。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence


ProgressCallback = Callable[[int, int, str], None]
"""统一的进度回调，签名 ``(current, total, message)``。

* ``current`` / ``total`` 是已处理的页数（不含跳过 / 失败）；
* ``message`` 是给人看的当前阶段描述，会直接显示在进度弹窗。
"""

CancelCheck = Callable[[], bool]
"""返回 True 表示用户已请求取消。OCR 引擎在每个页面前应主动询问。"""


@dataclass(frozen=True)
class OCRLine:
    """单条识别出的文字行。

    字段全部来自 PRD §5.3：

    * ``text``         ——  识别后的文字（已去除两端空白）；
    * ``bbox``         ——  原始渲染图像中的文字包围盒 ``(x0, y0, x1, y1)``，像素；
    * ``polygon``      ——  文字区域四边形坐标 ``(x0, y0, x1, y1, x2, y2, x3, y3)``，
                          像素，按顺时针或检测器默认顺序；
    * ``confidence``   ——  识别置信度，0~1；
    * ``page_index``   ——  页面索引（0-based），与 PyMuPDF 的 ``page.number`` 对齐。
    """

    text: str
    bbox: tuple[float, float, float, float]
    polygon: tuple[float, float, float, float, float, float, float, float]
    confidence: float
    page_index: int


@dataclass(frozen=True)
class OCRPageFailure:
    """单页识别失败记录，会汇总到 :class:`OCRJobResult` 中。"""

    page_index: int
    reason: str


@dataclass
class OCRRunOptions:
    """OCR 任务运行参数。

    字段名直接对应 PRD §4.2 / §6.1 的语义，便于 UI 与 service 之间复用。
    """

    page_indices: Sequence[int]
    """需要处理的页面索引（0-based）。空表示全部页面。"""

    skip_existing_text_layer: bool = True
    """默认跳过已有可读文字层的页面（PRD §3.1 OCR-04）。"""

    render_dpi: int = 200
    """渲染 DPI。PRD §4.2 允许后续按测试结果调整。"""

    output_path: str | None = None
    """最终 PDF 输出路径；为 None 时由 service 自动生成 ``<原名>_OCR.pdf``。"""

    overwrite_existing: bool = False
    """目标文件已存在时是否覆盖。False 时要求用户先确认或自动重命名。"""


@dataclass
class OCRJobResult:
    """一次 OCR 任务的结果汇总。

    UI 层只需要读取本结构，不需要感知内部引擎细节。
    """

    output_path: str
    processed_pages: list[int] = field(default_factory=list)
    skipped_pages: list[int] = field(default_factory=list)
    failed_pages: list[OCRPageFailure] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.processed_pages) + len(self.skipped_pages) + len(self.failed_pages)


# ---------------------------------------------------------------------------
# 服务层接口签名（仅声明，便于在不同实现间替换）
# ---------------------------------------------------------------------------

def run_ocr(
    source_path: str,
    destination_path: str,
    page_indices: Iterable[int] | None,
    options: OCRRunOptions,
    progress_callback: ProgressCallback | None,
    cancel_check: CancelCheck | None,
) -> OCRJobResult:
    """对外暴露的 OCR 任务入口。

    实现位于 :mod:`app.ocr.service`，本契约只描述语义：

    1. 检查模型与运行环境（PRD §4.3 步骤 1）；
    2. 验证 PDF 可处理性、跳过条件与数字签名；
    3. 逐页渲染 → 识别 → 写入隐藏文字层；
    4. 校验输出文件（页数 / 顺序 / 旋转 / 渲染一致性）；
    5. 原子替换目标文件，未确认前不覆盖。

    所有引擎层异常必须转换为 :class:`OCRJobResult` 的失败项或 ``warnings``，
    不允许直接抛出 Python 堆栈给用户。
    """
    raise NotImplementedError("app.ocr.service.run_ocr 尚未实现")