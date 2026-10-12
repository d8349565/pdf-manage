"""OCR 模块核心数据契约与接口定义。

保持无状态与强类型，避免业务层与 UI 层直接依赖特定 OCR 引擎实现。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

ProgressCallback = Callable[[int, int, str], None]
CancelCheck = Callable[[], bool]


@dataclass(frozen=True)
class OCRLine:
    """单行文字识别结果。

    所有坐标均为渲染图像的像素坐标系统：
    - bbox: (x0, y0, x1, y1)
    - polygon: (x0, y0, x1, y1, x2, y2, x3, y3)
    """

    text: str
    bbox: tuple[float, float, float, float]
    polygon: tuple[float, ...]
    confidence: float
    page_index: int


@dataclass(frozen=True)
class OCRRunOptions:
    """OCR 执行选项。"""

    page_indices: Sequence[int] | None = None
    skip_existing_text_layer: bool = True
    render_dpi: int = 200
    output_path: str | None = None
    overwrite_existing: bool = False


@dataclass(frozen=True)
class OCRPageFailure:
    """单页识别或写入失败信息。"""

    page_index: int
    reason: str


@dataclass
class OCRJobResult:
    """OCR 任务整体执行报告。"""

    output_path: str
    processed_pages: list[int] = field(default_factory=list)
    skipped_pages: list[int] = field(default_factory=list)
    failed_pages: list[OCRPageFailure] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.processed_pages) + len(self.skipped_pages) + len(self.failed_pages)

    @property
    def success(self) -> bool:
        return len(self.processed_pages) > 0 and len(self.failed_pages) == 0
