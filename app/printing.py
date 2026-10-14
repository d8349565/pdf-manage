from __future__ import annotations

from collections import OrderedDict
from bisect import bisect_left
from dataclasses import dataclass, field
import math

from PyQt6.QtCore import QEvent, QMarginsF, QObject, QRunnable, QRectF, QSize, QThreadPool, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QImage,
    QPageLayout,
    QPageSize,
    QPainter,
    QPen,
)
from PyQt6.QtPrintSupport import QPrinter, QPrinterInfo
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.icons import toolbar_icon
from app.pdfio import PdfError, PdfStore
from app.output import OperationCancelled, check_cancel
from app.progress import run_operation
from app.workspace import PageRef, Workspace, parse_page_range

DUPLEX_MODES = {
    "none": QPrinter.DuplexMode.DuplexNone,
    "long_edge": QPrinter.DuplexMode.DuplexLongSide,
    "short_edge": QPrinter.DuplexMode.DuplexShortSide,
}

# 打印时页面光栅化的目标密度。QPrinter 默认按屏幕分辨率作画，出来的纸面是糊的；
# 这里固定按 300 dpi 渲染位图，再由 QPainter 落到打印机自己的高分辨率画布上。
PRINT_DPI = 300
MAX_PRINT_PX = 5000
MAX_PRINT_PIXELS = 32_000_000
MAX_PREVIEW_PX = 1400
MAX_PREVIEW_PIXELS = 2_000_000
PREVIEW_CACHE_SIZE = 48
PREVIEW_CACHE_BYTES = 64 * 1024 * 1024

FALLBACK_PAPERS = (
    QPageSize.PageSizeId.A4,
    QPageSize.PageSizeId.A3,
    QPageSize.PageSizeId.A5,
    QPageSize.PageSizeId.B5,
    QPageSize.PageSizeId.Letter,
    QPageSize.PageSizeId.Legal,
)

MARGIN_PRESETS = (
    ("无边距（充满可打印区）", 0.0),
    ("窄边距 5 毫米", 5.0),
    ("常规边距 10 毫米", 10.0),
    ("宽边距 20 毫米", 20.0),
)


@dataclass
class PrintOptions:
    printer_name: str = ""
    duplex: str = "none"  # "none" | "long_edge" | "short_edge"
    orientation: str = "auto"  # "auto" | "portrait" | "landscape"
    copies: int = 1
    page_indices: list[int] = field(default_factory=list)
    paper: QPageSize.PageSizeId | None = None  # None = 跟随打印机当前纸张
    margin_mm: float = 0.0
    pages_per_face: int = 1
    cell_order: str = "row"
    borders: bool = False
    reverse: bool = False


@dataclass(frozen=True)
class SheetGeometry:
    """一张纸的真实尺寸与四边空白，单位毫米，已按方向摆好。"""

    width: float
    height: float
    left: float
    top: float
    right: float
    bottom: float

    @property
    def content_width(self) -> float:
        return max(self.width - self.left - self.right, 1.0)

    @property
    def content_height(self) -> float:
        return max(self.height - self.top - self.bottom, 1.0)

    @property
    def portrait(self) -> bool:
        return self.height >= self.width


@dataclass(frozen=True)
class Sheet:
    """一张物理纸：正面一页、背面一页（单面或末张可能没有背面）。"""

    front: int | None
    back: int | None
    front_portrait: bool
    back_portrait: bool
    front_pages: tuple[int, ...] = ()
    back_pages: tuple[int, ...] = ()

    @property
    def mixed(self) -> bool:
        return self.back is not None and self.front_portrait != self.back_portrait


def fit_rect(outer: QRectF, aspect: float) -> QRectF:
    """把宽高比为 aspect 的内容等比放进 outer 并居中。"""
    if aspect <= 0 or outer.width() <= 0 or outer.height() <= 0:
        return QRectF(outer)
    if outer.width() / outer.height() > aspect:
        height = outer.height()
        width = height * aspect
    else:
        width = outer.width()
        height = width / aspect
    return QRectF(
        outer.x() + (outer.width() - width) / 2,
        outer.y() + (outer.height() - height) / 2,
        width,
        height,
    )


def page_is_portrait(ref: PageRef, orientation_opt: str, store: PdfStore) -> bool:
    if orientation_opt == "portrait":
        return True
    if orientation_opt == "landscape":
        return False
    try:
        wpt, hpt = store.page_size(ref)
    except Exception:
        wpt, hpt = 595.0, 842.0
    return hpt >= wpt


def build_sheets(
    pages: list[PageRef], options: PrintOptions, store: PdfStore
) -> list[Sheet]:
    """把要打的页序列排成物理纸张。双面时两页共用一张纸，正反各自保留朝向。"""
    valid = [i for i in options.page_indices if 0 <= i < len(pages)]
    if not valid:
        return []
    if options.pages_per_face not in (1, 2, 4, 6, 9, 16) or options.cell_order not in ("row", "column"):
        raise ValueError("不支持的多页排版设置。")
    if options.reverse:
        valid.reverse()
    if options.pages_per_face > 1:
        count = options.pages_per_face
        faces = [tuple(valid[i:i + count]) for i in range(0, len(valid), count)]
        portrait = (options.orientation == "portrait" if options.orientation != "auto" else count not in (2, 6))
        stride = 1 if options.duplex == "none" else 2
        sheets = []
        for i in range(0, len(faces), stride):
            front = faces[i]
            back = faces[i + 1] if stride == 2 and i + 1 < len(faces) else ()
            sheets.append(Sheet(front[0], back[0] if back else None, portrait, portrait, front, back))
        return sheets

    def orient(index: int) -> bool:
        return page_is_portrait(pages[index], options.orientation, store)

    if options.duplex == "none":
        return [Sheet(i, None, orient(i), orient(i)) for i in valid]
    sheets: list[Sheet] = []
    for start in range(0, len(valid), 2):
        front = valid[start]
        back = valid[start + 1] if start + 1 < len(valid) else None
        front_p = orient(front)
        back_p = orient(back) if back is not None else front_p
        sheets.append(Sheet(front, back, front_p, back_p))
    return sheets


def face_pages(sheet, back=False):
    indices = sheet.back_pages if back else sheet.front_pages
    index = sheet.back if back else sheet.front
    return indices or (() if index is None else (index,))


def face_cells(outer, indices, options):
    """The same grid and ordering drive preview and printer output."""
    columns, rows = {1: (1, 1), 2: (2, 1), 4: (2, 2), 6: (3, 2),
                     9: (3, 3), 16: (4, 4)}[options.pages_per_face]
    width, height = outer.width() / columns, outer.height() / rows
    gap = min(width, height) * .02 if options.pages_per_face > 1 else 0
    for position, index in enumerate(indices):
        row, column = (divmod(position, columns) if options.cell_order == "row"
                       else (position % rows, position // rows))
        yield index, QRectF(outer.x() + column * width + gap / 2,
                           outer.y() + row * height + gap / 2, width - gap, height - gap)


def binding_flip_axis(duplex: str, portrait: bool) -> str:
    """装订边决定纸该往哪边翻：'lr' 左右翻（像翻书），'ud' 上下翻（像翻日历）。

    沿长边装订时轴线在长边上：纵向纸的长边是左右两侧，所以左右翻；横向纸反之。
    """
    if duplex not in ("long_edge", "short_edge"):
        return ""
    return "lr" if (duplex == "long_edge") == portrait else "ud"


def back_is_upside_down(duplex: str, portrait: bool, view_flip: str) -> bool:
    """按 view_flip 的方式翻过来看，背面内容是不是倒着的。"""
    axis = binding_flip_axis(duplex, portrait)
    return bool(axis) and axis != view_flip


def front_binding_edge(duplex: str, portrait: bool) -> str:
    axis = binding_flip_axis(duplex, portrait)
    if not axis:
        return ""
    return "left" if axis == "lr" else "top"


def back_binding_edge(duplex: str, portrait: bool, view_flip: str) -> str:
    """纸翻过来之后，装订边看上去落在背面的哪一边。"""
    edge = front_binding_edge(duplex, portrait)
    if not edge:
        return ""
    if view_flip == "lr":
        return {"left": "right", "right": "left"}.get(edge, edge)
    return {"top": "bottom", "bottom": "top"}.get(edge, edge)


def apply_sheet_layout(
    printer: QPrinter, options: PrintOptions, portrait: bool
) -> SheetGeometry:
    """把纸张、方向、边距写进打印机，并读回真正生效的尺寸（边距会被硬件下限顶上去）。"""
    printer.setFullPage(False)
    # 优先使用 QPrinter 原生配置方法分别设置纸张、朝向与边距。
    # 避免通过整包 printer.setPageLayout(layout) 时因 Windows 物理驱动的边距/单位精度校验
    # 导致 setPageLayout 返回 False，从而使整包设置（包括方向）被驱动丢弃并回滚为默认纵向。
    if options.paper is not None:
        printer.setPageSize(QPageSize(options.paper))

    target_orient = (
        QPageLayout.Orientation.Portrait if portrait else QPageLayout.Orientation.Landscape
    )
    printer.setPageOrientation(target_orient)

    margin = max(float(options.margin_mm), 0.0)
    printer.setPageMargins(
        QMarginsF(margin, margin, margin, margin), QPageLayout.Unit.Millimeter
    )

    final = printer.pageLayout()
    full = final.fullRect(QPageLayout.Unit.Millimeter)
    margins = final.margins(QPageLayout.Unit.Millimeter)

    width = full.width()
    height = full.height()
    left = margins.left()
    top = margins.top()
    right = margins.right()
    bottom = margins.bottom()

    # 驱动防御兜底：部分物理打印机驱动或老旧虚拟驱动，在 Win32 DEVMODE 下可能固定
    # 报告物理纵向纸张尺寸（或未转置 fullRect），导致横向尺寸计算异常。
    # 此处依据所请求的朝向强制校准尺寸与边距方向，确保任何驱动下版面均绝对正确。
    if portrait and width > height:
        width, height = height, width
        left, top, right, bottom = top, right, bottom, left
    elif not portrait and height > width:
        width, height = height, width
        left, top, right, bottom = top, right, bottom, left

    return SheetGeometry(
        width,
        height,
        left,
        top,
        right,
        bottom,
    )


def render_pages_to_printer(
    printer: QPrinter,
    store: PdfStore,
    pages: list[PageRef],
    options: PrintOptions,
    painter: QPainter | None = None,
    *,
    progress_callback=None,
    cancel_check=None,
) -> None:
    """按物理纸张的面序把页面画到打印机上。"""
    sheets = build_sheets(pages, options, store)
    if not sheets:
        return

    faces: list[tuple[tuple[int, ...], bool]] = []
    for sheet in sheets:
        if sheet.front is not None:
            faces.append((face_pages(sheet), sheet.front_portrait))
        if sheet.back is not None:
            faces.append((face_pages(sheet, True), sheet.back_portrait))
    if not faces:
        return
    check_cancel(cancel_check)
    store.require_operation(pages, "print")
    if progress_callback:
        progress_callback(0, len(faces), "正在准备打印任务")

    printer.setDuplex(DUPLEX_MODES.get(options.duplex, QPrinter.DuplexMode.DuplexNone))
    printer.setCopyCount(options.copies)

    # 第一面的纸张方向必须在 QPainter 开启前设定，否则 QPainter 会沿用旧纸张朝向。
    geometry = apply_sheet_layout(printer, options, faces[0][1])

    should_end = False
    if painter is None:
        painter = QPainter()
        if not painter.begin(printer):
            raise PdfError("无法启动打印任务，请检查打印机连接和驱动。")
        should_end = True

    try:
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        applied_portrait = faces[0][1]
        for position, (indices, portrait) in enumerate(faces):
            check_cancel(cancel_check)
            if position > 0:
                # 朝向变了才回写打印机：每次回写都是一趟驱动往返，长文档上很费时间。
                if portrait != applied_portrait:
                    geometry = apply_sheet_layout(printer, options, portrait)
                    applied_portrait = portrait
                if not printer.newPage():
                    raise PdfError(f"发送第 {position + 1} 页失败，请检查打印机状态。")

            vp = painter.viewport()
            if vp.width() <= 0 or vp.height() <= 0:
                raise PdfError("打印机没有提供有效的可打印区域。")

            # 跨页切换朝向时，QPainter 的 viewport 可能未同步转置，防御性校准绘制区域
            draw_rect = QRectF(vp)
            if portrait and draw_rect.width() > draw_rect.height():
                draw_rect = QRectF(draw_rect.x(), draw_rect.y(), draw_rect.height(), draw_rect.width())
            elif not portrait and draw_rect.height() > draw_rect.width():
                draw_rect = QRectF(draw_rect.x(), draw_rect.y(), draw_rect.height(), draw_rect.width())

            for page_index, cell in face_cells(draw_rect, indices, options):
                check_cancel(cancel_check)
                wanted = int(geometry.content_width / 25.4 * PRINT_DPI * cell.width() / draw_rect.width())
                render_w = max(1, min(wanted, int(cell.width()), MAX_PRINT_PX))
                width_pt, height_pt = store.page_size(pages[page_index])
                render_w = min(render_w, max(1, int(math.sqrt(MAX_PRINT_PIXELS * width_pt / max(height_pt, 1)))))
                try:
                    image = store.render_qimage(pages[page_index], render_w)
                except Exception as exc:
                    raise PdfError(f"工作区第 {page_index + 1} 页渲染失败：{exc}") from exc
                if image.isNull():
                    raise PdfError(f"工作区第 {page_index + 1} 页无法渲染，打印已停止。")
                check_cancel(cancel_check)
                painter.drawImage(fit_rect(cell, image.width() / image.height()), image)
                if options.borders:
                    painter.setPen(QPen(QColor("#64748B"), max(1, draw_rect.width() * .0007)))
                    painter.drawRect(cell)
                del image
            if progress_callback:
                progress_callback(position + 1, len(faces), f"已发送 {position + 1} / {len(faces)} 页")
        check_cancel(cancel_check)
        if should_end and not painter.end():
            raise PdfError("结束打印任务失败，请检查打印机队列。")
    except Exception:
        printer.abort()
        raise
    finally:
        if should_end and painter.isActive():
            painter.end()


class _PreviewSignals(QObject):
    done = pyqtSignal(tuple, object, str)


class _PreviewJob(QRunnable):
    def __init__(self, store, ref, key, signals, pending):
        super().__init__()
        self.store, self.ref, self.key = store, ref, key
        self.signals, self.pending = signals, pending

    def run(self):
        if self.key not in self.pending:
            return
        try:
            image = self.store.render_qimage(self.ref, self.key[-1])
            error = "" if not image.isNull() else "页面图像为空"
        except Exception as exc:
            image, error = QImage(), str(exc)
        self.signals.done.emit(self.key, image, error)


class SheetPreview(QWidget):
    """按物理纸张画预览：一行一张纸，双面时正反并排，翻面方向由装订方式和纸向决定。"""

    zoom_steps = pyqtSignal(int)

    PAD = 26
    HEADER_H = 24
    CAPTION_H = 24
    FACE_GAP = 52

    @dataclass
    class _Row:
        top: float  # 纸面区域上沿（标题在它上方）
        row_h: float
        row_w: float
        front_w: float
        front_h: float
        back_w: float
        back_h: float
        front_geo: SheetGeometry
        back_geo: SheetGeometry

    def __init__(self, store: PdfStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.pages: list[PageRef] = []
        self.sheets: list[Sheet] = []
        self.geometries: dict[bool, SheetGeometry] = {}
        self.duplex = "none"
        self.options = PrintOptions()
        self.scale = 1.0  # 像素 / 毫米
        self._rows: list[SheetPreview._Row] = []
        self._row_bottoms: list[float] = []
        self._cache: OrderedDict[tuple, QImage] = OrderedDict()
        self._cache_bytes = 0
        self._pending: set[tuple] = set()
        self._errors: dict[tuple, str] = {}
        self._generation = 0
        self._rendering_enabled = True
        # 无 QObject 父节点；关闭窗口后，运行中的任务仍可安全发出完成信号。
        self._signals = _PreviewSignals()
        self._signals.done.connect(self._on_image, Qt.ConnectionType.QueuedConnection)

    # ---- 数据与布局 ----

    def set_data(
        self,
        pages: list[PageRef],
        sheets: list[Sheet],
        geometries: dict[bool, SheetGeometry],
        duplex: str,
        options: PrintOptions | None = None,
    ) -> None:
        if tuple(p.render_key for p in pages) != tuple(p.render_key for p in self.pages):
            self._generation += 1
            self._pending.clear()
            self._cache.clear()
            self._cache_bytes = 0
            self._errors.clear()
        self.pages = list(pages)
        self.sheets = sheets
        self.geometries = geometries
        self.duplex = duplex
        self.options = options or PrintOptions(duplex=duplex)
        self._relayout()

    def set_scale(self, scale: float) -> None:
        scale = max(0.4, min(float(scale), 8.0))
        if abs(scale - self.scale) > 1e-6:
            self.scale = scale
            self._relayout()

    def fit_scale(self, viewport: QSize) -> float:
        if not self.sheets:
            return 1.0
        extra_w = 2 * self.PAD + (self.FACE_GAP if self.duplex != "none" else 0) + 24
        extra_h = 2 * self.PAD + self.HEADER_H + self.CAPTION_H
        avail_w = max(viewport.width() - extra_w, 80)
        avail_h = max(viewport.height() - extra_h, 80)
        max_w = 1.0
        max_h = 1.0
        duplex = self.duplex != "none"
        for sheet in self.sheets:
            front = self.geometries.get(sheet.front_portrait)
            back = self.geometries.get(sheet.back_portrait)
            if front is None:
                continue
            width = front.width
            height = front.height
            if duplex and back is not None:
                width += back.width
                height = max(height, back.height)
            max_w = max(max_w, width)
            max_h = max(max_h, height)
        return min(avail_w / max_w, avail_h / max_h)

    def sheet_top(self, index: int) -> int:
        if not self._rows:
            return 0
        index = max(0, min(index, len(self._rows) - 1))
        return int(self._rows[index].top - self.HEADER_H)

    def sheet_at(self, y: int) -> int:
        return min(bisect_left(self._row_bottoms, y), max(len(self._rows) - 1, 0))

    def _relayout(self) -> None:
        self._rows = []
        self._row_bottoms = []
        y = float(self.PAD)
        widest = 240.0
        duplex = self.duplex != "none"
        for sheet in self.sheets:
            front_geo = self.geometries.get(sheet.front_portrait)
            if front_geo is None:
                continue
            back_geo = self.geometries.get(sheet.back_portrait) or front_geo
            front_w = front_geo.width * self.scale
            front_h = front_geo.height * self.scale
            if duplex:
                back_w = back_geo.width * self.scale
                back_h = back_geo.height * self.scale
                row_w = front_w + self.FACE_GAP + back_w
                row_h = max(front_h, back_h)
            else:
                back_w = back_h = 0.0
                row_w = front_w
                row_h = front_h
            self._rows.append(
                SheetPreview._Row(
                    y + self.HEADER_H,
                    row_h,
                    row_w,
                    front_w,
                    front_h,
                    back_w,
                    back_h,
                    front_geo,
                    back_geo,
                )
            )
            self._row_bottoms.append(y + self.HEADER_H + row_h)
            y += self.HEADER_H + row_h + self.CAPTION_H + self.PAD
            widest = max(widest, row_w)
        self.setMinimumSize(int(widest + 2 * self.PAD), int(y))
        self.update()

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt 回调
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta != 0:
                self.zoom_steps.emit(1 if delta > 0 else -1)
                event.accept()
                return
        super().wheelEvent(event)

    # ---- 绘制 ----

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 回调
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.fillRect(event.rect(), QColor("#EDF2F1"))

        if not self._rows:
            painter.setPen(QColor("#94A3B8"))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "当前没有要打印的页面"
            )
            return

        dirty = QRectF(event.rect())
        start = bisect_left(self._row_bottoms, dirty.top() - self.CAPTION_H)
        for index in range(start, len(self._rows)):
            row = self._rows[index]
            top = row.top - self.HEADER_H
            if top + self.HEADER_H + row.row_h + self.CAPTION_H < dirty.top():
                continue
            if top > dirty.bottom():
                break
            self._paint_row(painter, index, row)

    def _paint_row(self, painter: QPainter, index: int, row: _Row) -> None:
        sheet = self.sheets[index]
        x0 = (self.width() - row.row_w) / 2
        duplex = self.duplex != "none"

        painter.setPen(QColor("#64748B"))
        painter.setFont(self._font(10, bold=True))
        painter.drawText(
            QRectF(x0, row.top - self.HEADER_H, row.row_w, self.HEADER_H),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            f"第 {index + 1} 张纸",
        )

        front_label = self._face_label(face_pages(sheet))
        front_y = row.top + (row.row_h - row.front_h) / 2
        front_rect = QRectF(x0, front_y, row.front_w, row.front_h)
        self._paint_face(
            painter,
            front_rect,
            row.front_geo,
            sheet.front,
            binding=front_binding_edge(self.duplex, sheet.front_portrait),
            caption=f"正面 · {front_label}" if duplex else front_label,
            page_indices=face_pages(sheet),
        )

        if not duplex:
            return

        axis = binding_flip_axis(self.duplex, sheet.front_portrait)
        back_y = row.top + (row.row_h - row.back_h) / 2
        back_rect = QRectF(
            front_rect.right() + self.FACE_GAP, back_y, row.back_w, row.back_h
        )
        self._paint_flip_arrow(
            painter,
            QRectF(front_rect.right(), row.top, self.FACE_GAP, row.row_h),
            axis,
        )
        if sheet.mixed:
            back_binding = front_binding_edge(self.duplex, sheet.back_portrait)
        else:
            back_binding = back_binding_edge(self.duplex, sheet.back_portrait, axis)
        self._paint_face(
            painter,
            back_rect,
            row.back_geo,
            sheet.back,
            binding=back_binding,
            caption=f"背面 · {self._face_label(face_pages(sheet, True))}",
            page_indices=face_pages(sheet, True),
        )

    def _page_label(self, page_index: int | None) -> str:
        return "空白" if page_index is None else f"第 {page_index + 1} 页"

    def _face_label(self, indices):
        return "第 " + ", ".join(str(i + 1) for i in indices) + " 页" if indices else "空白"

    def _paint_face(
        self,
        painter: QPainter,
        rect: QRectF,
        geometry: SheetGeometry,
        page_index: int | None,
        *,
        binding: str,
        caption: str,
        page_indices=None,
    ) -> None:
        painter.fillRect(rect.translated(4, 4), QColor(15, 23, 42, 26))
        painter.fillRect(rect, QColor("#FFFFFF"))

        if page_index is None:
            painter.setPen(QColor("#B6C2C0"))
            painter.setFont(self._font(10))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "此面空白")
        else:
            content = QRectF(
                rect.x() + geometry.left * self.scale,
                rect.y() + geometry.top * self.scale,
                geometry.content_width * self.scale,
                geometry.content_height * self.scale,
            )
            if min(geometry.left, geometry.top, geometry.right, geometry.bottom) * self.scale > 2:
                painter.setPen(QPen(QColor("#DCE7E4"), 1, Qt.PenStyle.DashLine))
                painter.drawRect(content)
            indices = (page_index,) if page_indices is None else page_indices
            for index, cell in face_cells(content, indices, self.options):
                self._paint_content(painter, cell, index)
                if self.options.borders:
                    painter.setPen(QPen(QColor("#64748B"), max(.5, content.width() * .0007)))
                    painter.drawRect(cell)

        painter.setPen(QPen(QColor("#9AA8B2"), 1))
        painter.drawRect(rect)
        if binding:
            self._paint_binding_edge(painter, rect, binding)

        painter.setPen(QColor("#64748B"))
        painter.setFont(self._font(9))
        painter.drawText(
            QRectF(rect.x(), rect.bottom() + 2, rect.width(), self.CAPTION_H),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
            caption,
        )

    def _paint_content(
        self, painter: QPainter, content: QRectF, page_index: int
    ) -> None:
        dpr = max(self.devicePixelRatioF(), 1.0)
        image = self._image(page_index, int(content.width() * dpr))
        if image is None:
            painter.setPen(QColor("#CBD5E1"))
            painter.setFont(self._font(9))
            key = self._image_key(page_index, int(content.width() * dpr))
            message = "无法预览此页" if key in self._errors else "正在生成预览…"
            painter.drawText(content, Qt.AlignmentFlag.AlignCenter, message)
            return
        painter.drawImage(fit_rect(content, image.width() / image.height()), image)

    def _paint_binding_edge(self, painter: QPainter, rect: QRectF, edge: str) -> None:
        painter.setPen(QPen(QColor("#0F766E"), 3, Qt.PenStyle.DashLine))
        inset = 2.0
        if edge == "left":
            painter.drawLine(
                int(rect.left() + inset), int(rect.top() + 6),
                int(rect.left() + inset), int(rect.bottom() - 6),
            )
        elif edge == "right":
            painter.drawLine(
                int(rect.right() - inset), int(rect.top() + 6),
                int(rect.right() - inset), int(rect.bottom() - 6),
            )
        elif edge == "top":
            painter.drawLine(
                int(rect.left() + 6), int(rect.top() + inset),
                int(rect.right() - 6), int(rect.top() + inset),
            )
        else:
            painter.drawLine(
                int(rect.left() + 6), int(rect.bottom() - inset),
                int(rect.right() - 6), int(rect.bottom() - inset),
            )

    def _paint_flip_arrow(self, painter: QPainter, gap: QRectF, axis: str) -> None:
        cx, cy = gap.center().x(), gap.center().y()
        painter.setPen(QPen(QColor("#94A3B8"), 2))
        if axis == "lr":
            painter.drawLine(int(cx - 10), int(cy), int(cx + 10), int(cy))
            painter.drawLine(int(cx + 10), int(cy), int(cx + 4), int(cy - 5))
            painter.drawLine(int(cx + 10), int(cy), int(cx + 4), int(cy + 5))
        else:
            painter.drawLine(int(cx), int(cy - 10), int(cx), int(cy + 10))
            painter.drawLine(int(cx), int(cy + 10), int(cx - 5), int(cy + 4))
            painter.drawLine(int(cx), int(cy + 10), int(cx + 5), int(cy + 4))
        painter.setPen(QColor("#94A3B8"))
        painter.setFont(self._font(8))
        painter.drawText(
            QRectF(gap.x(), cy + 14, gap.width(), 16),
            Qt.AlignmentFlag.AlignCenter,
            "左右翻" if axis == "lr" else "上下翻",
        )

    def _font(self, point: int, bold: bool = False) -> QFont:
        font = QFont(self.font())
        font.setPointSize(point)
        font.setBold(bold)
        return font

    def _image_key(self, page_index: int, width: int) -> tuple:
        bucket = max(96, min(((int(width) + 63) // 64) * 64, MAX_PREVIEW_PX))
        ref = self.pages[page_index]
        width_pt, height_pt = self.store.page_size(ref)
        bucket = min(bucket, max(1, int(math.sqrt(MAX_PREVIEW_PIXELS * width_pt / max(height_pt, 1)))))
        return (self._generation, *ref.render_key, bucket)

    def _image(self, page_index: int, width: int) -> QImage | None:
        if not (0 <= page_index < len(self.pages)):
            return None
        key = self._image_key(page_index, width)
        image = self._cache.get(key)
        if image is None:
            if self._rendering_enabled and key not in self._pending:
                self._pending.add(key)
                QThreadPool.globalInstance().start(
                    _PreviewJob(self.store, self.pages[page_index], key, self._signals, self._pending), -1)
            return None
        self._cache.move_to_end(key)
        return None if image.isNull() else image

    def _on_image(self, key, image, error):
        if key not in self._pending:
            return
        self._pending.discard(key)
        old = self._cache.pop(key, None)
        if old is not None:
            self._cache_bytes -= old.sizeInBytes()
        self._cache[key] = image
        self._cache_bytes += image.sizeInBytes()
        if error:
            self._errors[key] = error
            self.setToolTip(f"预览失败：{error}。可以尝试打印，失败时会说明具体页码。")
        while len(self._cache) > PREVIEW_CACHE_SIZE or (self._cache_bytes > PREVIEW_CACHE_BYTES and len(self._cache) > 1):
            dropped_key, dropped = self._cache.popitem(last=False)
            self._cache_bytes -= dropped.sizeInBytes()
            self._errors.pop(dropped_key, None)
        self.update()

    def pause_rendering(self):
        self._rendering_enabled = False
        self._pending.clear()

    def resume_rendering(self):
        self._rendering_enabled = True
        self.update()


class PrintDialog(QDialog):
    """集成纸张预览与打印设置的一体化对话框。"""

    def __init__(self, parent: QWidget | None, ws: Workspace, store: PdfStore) -> None:
        super().__init__(parent)
        self.ws = ws
        self.store = store
        self.pages = list(ws.pages)
        # 默认的 QPrinter 按屏幕分辨率作画，纸面会发虚，必须要高分辨率模式。
        self.printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        self._auto_fit = True
        self._sheet_index = 0
        self._geometry_cache: dict[tuple, dict[bool, SheetGeometry]] = {}
        self._printing = False

        self.setWindowTitle("打印")
        self.resize(1120, 760)
        self.setMinimumSize(880, 600)
        self.setStyleSheet(
            """
            QDialog {
                background: #FFFFFF;
                color: #1E293B;
                font-family: "Microsoft YaHei", sans-serif;
            }
            QLabel {
                color: #1E293B;
            }
            QFrame#sidebar {
                background: #F8FAF9;
                border-left: 1px solid #D7E8E4;
            }
            QWidget#sidebarInner {
                background: #F8FAF9;
            }
            QScrollArea#sidebarScroll {
                background: transparent;
                border: none;
            }
            QPushButton#primaryBtn {
                background: #0F766E;
                color: #FFFFFF;
                font-weight: bold;
                border: none;
                border-radius: 6px;
                padding: 8px 20px;
                min-width: 80px;
            }
            QPushButton#primaryBtn:hover {
                background: #0D9488;
            }
            QPushButton#primaryBtn:pressed {
                background: #115E59;
            }
            QPushButton#primaryBtn:disabled {
                background: #CBD5E1;
                color: #F8FAFC;
            }
            QPushButton#secondaryBtn {
                background: #FFFFFF;
                color: #134E4A;
                border: 1px solid #D7E8E4;
                border-radius: 6px;
                padding: 8px 18px;
                min-width: 80px;
            }
            QPushButton#secondaryBtn:hover {
                background: #F0FDFA;
                border-color: #5EEAD4;
            }
            QToolButton#previewBtn {
                background: #FFFFFF;
                border: 1px solid #D7E8E4;
                border-radius: 4px;
                padding: 4px;
            }
            QToolButton#previewBtn:hover {
                background: #F0FDFA;
                border-color: #5EEAD4;
            }
            QToolButton#previewBtn:checked {
                background: #CCFBF1;
                border-color: #0F766E;
            }
            QToolButton#previewBtn:checked:hover {
                background: #99F6E4;
                border-color: #0F766E;
            }
            QComboBox, QSpinBox, QLineEdit {
                background: #FFFFFF;
                border: 1px solid #D7E8E4;
                border-radius: 6px;
                padding: 6px 10px;
                color: #1E293B;
            }
            QComboBox:focus, QSpinBox:focus, QLineEdit:focus {
                border-color: #0F766E;
            }
            QComboBox:disabled {
                color: #94A3B8;
                background: #F1F5F9;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            """
        )

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_preview_pane(), 1)
        root.addWidget(self._build_sidebar())

        self._reload_papers()
        self._refresh()

    # ---- 左侧预览 ----

    def _build_preview_pane(self) -> QWidget:
        container = QWidget()
        lay = QVBoxLayout(container)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        toolbar.addWidget(self._tool_btn("zoom-out", "缩小", self._zoom_out, shortcut="Ctrl+-"))
        toolbar.addWidget(self._tool_btn("zoom-in", "放大", self._zoom_in, shortcut="Ctrl+="))
        self.btn_fit = self._tool_btn("layout-grid", "适应窗口", self._on_fit_clicked, shortcut="Ctrl+0")
        self.btn_fit.setCheckable(True)
        self.btn_fit.setChecked(True)
        toolbar.addWidget(self.btn_fit)
        toolbar.addSpacing(14)

        self.btn_prev = self._tool_btn("chevron-left", "上一张纸", self._prev_sheet)
        self.label_sheet = QLabel("第 1 / 1 张纸")
        self.label_sheet.setStyleSheet("color: #4D6B67; font-size: 13px;")
        self.btn_next = self._tool_btn("chevron-right", "下一张纸", self._next_sheet)
        toolbar.addWidget(self.btn_prev)
        toolbar.addWidget(self.label_sheet)
        toolbar.addWidget(self.btn_next)

        toolbar.addStretch(1)
        lay.addLayout(toolbar)

        self.label_hint = QLabel("")
        self.label_hint.setWordWrap(True)
        self.label_hint.setStyleSheet(
            "background: #F0FDFA; border: 1px solid #CCFBF1; border-radius: 6px;"
            "padding: 7px 10px; color: #134E4A; font-size: 12px;"
        )
        lay.addWidget(self.label_hint)

        self.canvas = SheetPreview(self.store)
        self.canvas.zoom_steps.connect(self._on_zoom_steps)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.canvas)
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet(
            "QScrollArea { background: #EDF2F1; border: 1px solid #CBD5E1; border-radius: 4px; }"
        )
        self.scroll.verticalScrollBar().valueChanged.connect(self._update_sheet_counter)
        self.scroll.viewport().installEventFilter(self)
        lay.addWidget(self.scroll, 1)

        self.label_summary = QLabel("")
        self.label_summary.setStyleSheet("color: #475569; font-size: 12px;")
        lay.addWidget(self.label_summary)
        return container

    def _tool_btn(
        self, icon_name: str, tip: str, callback, shortcut: str | None = None
    ) -> QToolButton:
        btn = QToolButton()
        btn.setObjectName("previewBtn")
        btn.setIcon(toolbar_icon(icon_name, "#134E4A", 18))
        btn.setIconSize(QSize(18, 18))
        btn.setToolTip(f"{tip} ({shortcut})" if shortcut else tip)
        if shortcut:
            btn.setShortcut(shortcut)
        btn.clicked.connect(callback)
        return btn

    # ---- 右侧设置 ----

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(346)
        outer = QVBoxLayout(sidebar)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setObjectName("sidebarScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("sidebarInner")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(20, 18, 20, 12)
        lay.setSpacing(9)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        title = QLabel("打印设置")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #134E4A;")
        lay.addWidget(title)

        # 1. 打印机
        lay.addWidget(self._caption("打印机"))
        self.combo_printer = QComboBox()
        printers = QPrinterInfo.availablePrinterNames()
        default_name = QPrinterInfo.defaultPrinterName()
        if printers:
            self.combo_printer.addItems(printers)
            if default_name in printers:
                self.combo_printer.setCurrentText(default_name)
        else:
            self.combo_printer.addItem("系统默认打印机")
        self.combo_printer.currentIndexChanged.connect(self._on_printer_changed)
        lay.addWidget(self.combo_printer)

        # 2. 打印范围
        lay.addWidget(self._caption("打印范围"))
        self.btn_range_group = QButtonGroup(self)
        self.radio_all = QRadioButton(f"全部页面 ({len(self.pages)} 页)")
        self.radio_all.setChecked(True)
        self.btn_range_group.addButton(self.radio_all)
        lay.addWidget(self.radio_all)

        selected_count = len(self.ws.target_indices())
        self.radio_sel = QRadioButton(f"已选页面 ({selected_count} 页)")
        self.btn_range_group.addButton(self.radio_sel)
        if selected_count == 0:
            self.radio_sel.setEnabled(False)
        lay.addWidget(self.radio_sel)

        range_row = QHBoxLayout()
        range_row.setSpacing(8)
        self.radio_custom = QRadioButton("页码范围")
        self.btn_range_group.addButton(self.radio_custom)
        self.edit_range = QLineEdit()
        self.edit_range.setPlaceholderText("例如 1-3, 5")
        self.edit_range.setEnabled(False)
        range_row.addWidget(self.radio_custom)
        range_row.addWidget(self.edit_range, 1)
        lay.addLayout(range_row)

        self.label_range_error = QLabel("")
        self.label_range_error.setStyleSheet("color: #DC2626; font-size: 11px;")
        self.label_range_error.setVisible(False)
        lay.addWidget(self.label_range_error)

        self.btn_range_group.buttonToggled.connect(self._on_range_mode_changed)
        self.edit_range.textChanged.connect(self._on_custom_range_changed)

        # 3. 份数
        copies_row = QHBoxLayout()
        copies_row.addWidget(self._caption("打印份数"))
        copies_row.addStretch(1)
        self.spin_copies = QSpinBox()
        self.spin_copies.setRange(1, 999)
        self.spin_copies.setValue(1)
        self.spin_copies.valueChanged.connect(self._refresh)
        copies_row.addWidget(self.spin_copies)
        lay.addLayout(copies_row)

        lay.addWidget(self._caption("每面排列"))
        self.combo_nup = QComboBox()
        for count in (1, 2, 4, 6, 9, 16):
            self.combo_nup.addItem(f"{count} 页", count)
        self.combo_nup.currentIndexChanged.connect(self._refresh)
        lay.addWidget(self.combo_nup)
        self.combo_cell_order = QComboBox()
        self.combo_cell_order.addItem("先横向，再纵向", "row")
        self.combo_cell_order.addItem("先纵向，再横向", "column")
        self.combo_cell_order.currentIndexChanged.connect(self._refresh)
        lay.addWidget(self.combo_cell_order)
        from PyQt6.QtWidgets import QCheckBox
        self.check_borders = QCheckBox("显示页面边框")
        self.check_borders.toggled.connect(self._refresh)
        lay.addWidget(self.check_borders)
        self.check_reverse = QCheckBox("按相反页序打印")
        self.check_reverse.toggled.connect(self._refresh)
        lay.addWidget(self.check_reverse)

        # 4. 纸张
        lay.addWidget(self._caption("纸张大小"))
        self.combo_paper = QComboBox()
        self.combo_paper.currentIndexChanged.connect(self._on_paper_changed)
        lay.addWidget(self.combo_paper)

        # 5. 页面方向
        lay.addWidget(self._caption("页面方向"))
        self.combo_orient = QComboBox()
        self.combo_orient.addItem("自动横纵向（推荐）", "auto")
        self.combo_orient.addItem("纵向", "portrait")
        self.combo_orient.addItem("横向", "landscape")
        self.combo_orient.currentIndexChanged.connect(self._on_paper_changed)
        lay.addWidget(self.combo_orient)

        # 6. 页边距
        lay.addWidget(self._caption("页边距"))
        self.combo_margin = QComboBox()
        for text, value in MARGIN_PRESETS:
            self.combo_margin.addItem(text, value)
        self.combo_margin.currentIndexChanged.connect(self._on_paper_changed)
        lay.addWidget(self.combo_margin)

        # 7. 纸张面数
        lay.addWidget(self._caption("纸张面数"))
        self.btn_duplex_group = QButtonGroup(self)
        self.radio_single = QRadioButton("单面打印")
        self.radio_single.setChecked(True)
        self.btn_duplex_group.addButton(self.radio_single)
        lay.addWidget(self.radio_single)

        self.radio_double = QRadioButton("双面打印")
        self.btn_duplex_group.addButton(self.radio_double)
        lay.addWidget(self.radio_double)

        self.combo_duplex_mode = QComboBox()
        self.combo_duplex_mode.addItem("沿长边装订（竖版左右翻，横版上下翻）", "long_edge")
        self.combo_duplex_mode.addItem("沿短边装订（竖版上下翻，横版左右翻）", "short_edge")
        self.combo_duplex_mode.setEnabled(False)
        self.combo_duplex_mode.currentIndexChanged.connect(self._on_duplex_changed)
        lay.addWidget(self.combo_duplex_mode)

        self.label_duplex_warn = QLabel("")
        self.label_duplex_warn.setWordWrap(True)
        self.label_duplex_warn.setStyleSheet(
            "background: #FFFBEB; border: 1px solid #FDE68A; border-radius: 6px;"
            "padding: 7px 9px; color: #92400E; font-size: 11px;"
        )
        self.label_duplex_warn.setVisible(False)
        lay.addWidget(self.label_duplex_warn)

        self.btn_duplex_group.buttonToggled.connect(self._on_duplex_changed)
        lay.addStretch(1)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(20, 8, 20, 20)
        btn_row.setSpacing(10)
        btn_cancel = QPushButton("取消")
        btn_cancel.setObjectName("secondaryBtn")
        btn_cancel.clicked.connect(self.reject)
        self.btn_print = QPushButton("打印")
        self.btn_print.setObjectName("primaryBtn")
        self.btn_print.clicked.connect(self.do_print)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(self.btn_print)
        outer.addLayout(btn_row)
        return sidebar

    def _caption(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-weight: bold; color: #334155; margin-top: 6px;")
        return label

    # ---- 交互 ----

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt 回调
        if (
            hasattr(self, "scroll")
            and obj is self.scroll.viewport()
            and event.type() == QEvent.Type.Resize
            and self.isVisible()
            and getattr(self, "_auto_fit", False)
        ):
            self._fit_window()
        return super().eventFilter(obj, event)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 回调
        super().showEvent(event)
        if getattr(self, "_auto_fit", False):
            self._fit_window()
            if hasattr(self, "btn_fit"):
                self.btn_fit.setChecked(True)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 回调
        super().resizeEvent(event)
        if getattr(self, "_auto_fit", False) and self.isVisible():
            self._fit_window()

    def _on_fit_clicked(self) -> None:
        self._auto_fit = True
        if hasattr(self, "btn_fit"):
            self.btn_fit.setChecked(True)
        self._fit_window()

    def _on_zoom_steps(self, steps: int) -> None:
        if steps > 0:
            self._zoom_in()
        else:
            self._zoom_out()

    def _zoom_in(self) -> None:
        self._auto_fit = False
        if hasattr(self, "btn_fit"):
            self.btn_fit.setChecked(False)
        self.canvas.set_scale(self.canvas.scale * 1.25)
        self.scroll.verticalScrollBar().setValue(self.canvas.sheet_top(self._sheet_index))
        self._update_sheet_counter()

    def _zoom_out(self) -> None:
        self._auto_fit = False
        if hasattr(self, "btn_fit"):
            self.btn_fit.setChecked(False)
        self.canvas.set_scale(self.canvas.scale / 1.25)
        self.scroll.verticalScrollBar().setValue(self.canvas.sheet_top(self._sheet_index))
        self._update_sheet_counter()

    def _fit_window(self) -> None:
        self.canvas.set_scale(self.canvas.fit_scale(self.scroll.viewport().size()))
        self.scroll.verticalScrollBar().setValue(self.canvas.sheet_top(self._sheet_index))
        self._update_sheet_counter()

    def _prev_sheet(self) -> None:
        self._goto_sheet(self._sheet_index - 1)

    def _next_sheet(self) -> None:
        self._goto_sheet(self._sheet_index + 1)

    def _goto_sheet(self, index: int) -> None:
        total = len(self.canvas.sheets)
        if total == 0:
            return
        index = max(0, min(index, total - 1))
        self.scroll.verticalScrollBar().setValue(self.canvas.sheet_top(index))
        self._set_sheet_counter(index)

    def _update_sheet_counter(self) -> None:
        """以视口中线所在的那张纸为「当前」，滚到底时计数不会停在上一张。"""
        middle = self.scroll.verticalScrollBar().value() + self.scroll.viewport().height() // 2
        self._set_sheet_counter(self.canvas.sheet_at(middle))

    def _set_sheet_counter(self, index: int) -> None:
        total = len(self.canvas.sheets)
        self._sheet_index = max(0, min(index, total - 1)) if total else 0
        current = self._sheet_index + 1 if total else 0
        self.label_sheet.setText(
            f"第 {current} / {total} 张纸" if total else "没有可打印的纸张"
        )
        self.btn_prev.setEnabled(current > 1)
        self.btn_next.setEnabled(0 < current < total)

    def _on_printer_changed(self) -> None:
        name = self.combo_printer.currentText()
        if name and name != "系统默认打印机":
            self.printer.setPrinterName(name)
        self._reload_papers()
        self._refresh()

    def _on_paper_changed(self) -> None:
        self._refresh()

    def _on_duplex_changed(self) -> None:
        is_double = self.radio_double.isChecked()
        self.combo_duplex_mode.setEnabled(is_double)
        self._refresh()

    def _on_range_mode_changed(self) -> None:
        self.edit_range.setEnabled(self.radio_custom.isChecked())
        self.label_range_error.setVisible(False)
        self._refresh()

    def _on_custom_range_changed(self) -> None:
        if self.radio_custom.isChecked():
            self._refresh()

    def _reload_papers(self) -> None:
        """纸张清单跟着打印机走：只列这台机器认的纸，选不出打不了的组合。"""
        wanted = self.combo_paper.currentData()
        info = QPrinterInfo(self.printer)
        sizes = [] if info.isNull() else info.supportedPageSizes()
        ids: list[QPageSize.PageSizeId] = []
        for size in sizes:
            pid = size.id()
            if pid != QPageSize.PageSizeId.Custom and pid not in ids:
                ids.append(pid)
        if not ids:
            ids = list(FALLBACK_PAPERS)
        default_id = None if info.isNull() else info.defaultPageSize().id()

        self.combo_paper.blockSignals(True)
        self.combo_paper.clear()
        for pid in ids:
            size = QPageSize(pid)
            mm = size.size(QPageSize.Unit.Millimeter)
            self.combo_paper.addItem(
                f"{size.name()}（{mm.width():.0f} × {mm.height():.0f} 毫米）", pid
            )
        target = wanted if wanted in ids else default_id
        index = self.combo_paper.findData(target) if target is not None else -1
        self.combo_paper.setCurrentIndex(index if index >= 0 else 0)
        self.combo_paper.blockSignals(False)

    # ---- 选项与刷新 ----

    def _collect_indices(self) -> list[int]:
        if self.radio_all.isChecked():
            self.label_range_error.setVisible(False)
            return list(range(len(self.pages)))
        if self.radio_sel.isChecked():
            self.label_range_error.setVisible(False)
            indices = self.ws.target_indices()
            return sorted(indices) if indices else list(range(len(self.pages)))
        text = self.edit_range.text().strip()
        if not text:
            self.label_range_error.setText("请输入页码范围")
            self.label_range_error.setVisible(True)
            return []
        try:
            indices = parse_page_range(text, len(self.pages))
            self.label_range_error.setVisible(False)
            return indices
        except ValueError as exc:
            self.label_range_error.setText(str(exc))
            self.label_range_error.setVisible(True)
            return []

    def _collect_options(self) -> PrintOptions:
        printer_name = self.combo_printer.currentText()
        if printer_name == "系统默认打印机":
            printer_name = ""
        if self.radio_single.isChecked():
            duplex = "none"
        else:
            duplex = self.combo_duplex_mode.currentData() or "long_edge"
        return PrintOptions(
            printer_name=printer_name,
            duplex=duplex,
            orientation=self.combo_orient.currentData() or "auto",
            copies=self.spin_copies.value(),
            page_indices=self._collect_indices(),
            paper=self.combo_paper.currentData(),
            margin_mm=float(self.combo_margin.currentData() or 0.0),
            pages_per_face=self.combo_nup.currentData() or 1,
            cell_order=self.combo_cell_order.currentData() or "row",
            borders=self.check_borders.isChecked(),
            reverse=self.check_reverse.isChecked(),
        )

    def _geometries(self, options: PrintOptions) -> dict[bool, SheetGeometry]:
        """问打印机要纸张尺寸是一趟驱动往返，同一套设置只问一次，输入页码时才不卡。"""
        key = (self.printer.printerName(), options.paper, options.margin_mm)
        cached = self._geometry_cache.get(key)
        if cached is None:
            cached = {
                True: apply_sheet_layout(self.printer, options, True),
                False: apply_sheet_layout(self.printer, options, False),
            }
            self._geometry_cache[key] = cached
        return cached

    def _refresh(self) -> None:
        options = self._collect_options()
        duplex = options.duplex
        is_double = duplex != "none"
        self.label_hint.setVisible(is_double)

        geometries = self._geometries(options)
        sheets = build_sheets(self.pages, options, self.store)
        self.canvas.set_data(self.pages, sheets, geometries, duplex, options)
        self.combo_cell_order.setVisible(options.pages_per_face > 1)
        self.check_borders.setVisible(options.pages_per_face > 1)
        if getattr(self, "_auto_fit", False) and self.isVisible():
            self._fit_window()

        pages_n = len(options.page_indices)
        sheets_n = len(sheets)
        if sheets_n:
            text = f"{pages_n} 页内容 → 需要 {sheets_n} 张纸"
            single_sheets = math.ceil(pages_n / options.pages_per_face)
            if is_double and single_sheets > sheets_n:
                text += f"（双面，比单面省 {single_sheets - sheets_n} 张）"
            if options.copies > 1:
                text += f" × {options.copies} 份 = {sheets_n * options.copies} 张"
            geometry = geometries[sheets[0].front_portrait]
            text += f" · 实际页边距 {geometry.left:.1f} 毫米（内容按可打印区等比缩放）"
        else:
            text = "还没有选定要打印的页面"
        self.label_summary.setText(text)

        self.btn_print.setEnabled(sheets_n > 0 and not self._printing)
        self._update_hint()
        self._update_duplex_warning(options)
        self._update_sheet_counter()

    def _update_hint(self) -> None:
        duplex = "none" if self.radio_single.isChecked() else (
            self.combo_duplex_mode.currentData() or "long_edge"
        )
        if duplex == "none":
            return
        mixed = any(sheet.mixed for sheet in self.canvas.sheets)
        axes = set()
        for sheet in self.canvas.sheets:
            axes.add(binding_flip_axis(duplex, sheet.front_portrait))
            if sheet.back is not None:
                axes.add(binding_flip_axis(duplex, sheet.back_portrait))
        axes.discard("")
        if axes == {"lr"}:
            how = "左右翻开（像翻书）"
        elif axes == {"ud"}:
            how = "上下翻开（像翻日历）"
        else:
            how = "竖版左右翻（像翻书）、横版上下翻（像翻日历）"
        text = f"打出来后按{how}，正反两面字都朝上。绿色虚线是装订边。"
        if mixed:
            text += " 同一张纸一面横一面竖时，翻面后把纸转 90° 再看背面。"
        self.label_hint.setText(text)

    def _update_duplex_warning(self, options: PrintOptions) -> None:
        if options.duplex == "none" or not options.printer_name:
            self.label_duplex_warn.setVisible(False)
            return
        info = QPrinterInfo.printerInfo(options.printer_name)
        supported = [] if info.isNull() else info.supportedDuplexModes()
        mode = DUPLEX_MODES.get(options.duplex)
        if supported and mode not in supported:
            self.label_duplex_warn.setText(
                f"「{options.printer_name}」没有报告支持自动双面。仍可发送，"
                "但可能只打单面，需要你把纸取出来翻面重放。"
            )
            self.label_duplex_warn.setVisible(True)
        else:
            self.label_duplex_warn.setVisible(False)

    def do_print(self) -> None:
        if self._printing:
            return
        options = self._collect_options()
        if not options.page_indices:
            QMessageBox.warning(self, "没有可打印的页面", "请指定有效的打印页码范围。")
            return
        self._printing = True
        self.btn_print.setEnabled(False)
        self.canvas.pause_rendering()
        pages = list(self.pages)

        def work(progress, cancelled):
            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            if options.printer_name:
                printer.setPrinterName(options.printer_name)
            if not printer.isValid():
                raise PdfError("没有可用的打印机，请先连接或安装打印机。")
            render_pages_to_printer(printer, self.store, pages, options,
                                    progress_callback=progress, cancel_check=cancelled)

        try:
            run_operation(self, "发送打印任务", work, cancellable=True)
            self.accept()
        except OperationCancelled:
            QMessageBox.information(self, "已取消打印", "已请求停止发送。已经交给打印机的页面可能仍会打印，可在系统打印队列中取消。")
        except Exception as exc:
            QMessageBox.critical(self, "打印失败", f"发送打印任务时出错：{exc}\n\n任务可能已经部分发送，请检查系统打印队列后再重试。")
        finally:
            self._printing = False
            self.canvas.resume_rendering()
            self._refresh()

    def reject(self) -> None:
        if self._printing:
            dialog = getattr(self, "_task_dialog", None)
            if dialog is not None:
                dialog.request_cancel()
            return
        self.canvas.pause_rendering()
        super().reject()

    def closeEvent(self, event) -> None:
        if self._printing:
            self.reject()
            event.ignore()
            return
        self.canvas.pause_rendering()
        super().closeEvent(event)
