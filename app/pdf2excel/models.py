from __future__ import annotations
from dataclasses import dataclass, field

@dataclass(frozen=True)
class CellStyle:
    font_name: str | None = None
    font_size: float | None = None
    bold: bool = False
    italic: bool = False
    font_color: str | None = None      # 十六进制，类似 '#FF0000'
    fill_color: str | None = None      # 十六进制，类似 '#FFFF00'
    horizontal: str | None = None      # 'left', 'center', 'right'
    vertical: str | None = None        # 'top', 'center', 'bottom'
    border_left: str | None = None     # 'thin', 'medium', 'thick'
    border_right: str | None = None
    border_top: str | None = None
    border_bottom: str | None = None

    @property
    def horizontal_alignment(self) -> str | None:
        return self.horizontal

    @property
    def vertical_alignment(self) -> str | None:
        return self.vertical

@dataclass(frozen=True)
class CellModel:
    row: int
    col: int
    text: str = ''
    value: object = None               # 强类型值 (int, float, datetime 等)
    number_format: str | None = None   # Excel 数字格式代码，如 '0%', '#,##0.00'
    bbox: tuple[float, float, float, float] | None = None
    row_span: int = 1
    col_span: int = 1
    style: CellStyle = field(default_factory=CellStyle)
    confidence: float = 1.0

@dataclass
class TableModel:
    page_no: int
    bbox: tuple[float, float, float, float]
    row_count: int
    col_count: int
    cells: list[CellModel]
    row_heights: list[float]           # 以 PDF 点为单位
    col_widths: list[float]            # 以 PDF 点为单位
    confidence: float = 1.0
    continuation_of: int | None = None  # 若跨页，则记录上一个表格的索引

@dataclass
class PageAnalysis:
    page_no: int
    text_length: int
    text_blocks: int
    drawing_count: int
    image_count: int
    image_area_ratio: float            # 总图像面积 / 页面面积
    is_electronic: bool

@dataclass
class ConversionResult:
    tables: list[TableModel]
    pages_analyzed: int
    pages_with_tables: int
    pages_skipped: int                 # 扫描等跳过的页面
    warnings: list[str]
