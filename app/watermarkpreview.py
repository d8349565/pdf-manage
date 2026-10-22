"""Cached paper and watermark layers; pointer motion never calls the PDF engine."""
import math
from threading import Event

from PyQt6.QtCore import QObject, QPointF, QRunnable, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap, QTransform
from PyQt6.QtWidgets import QWidget

from app.output import OperationCancelled
from app.pdfmarks import watermark_grid, watermark_layout


class AssetSignals(QObject):
    ready = pyqtSignal(tuple, object, str)
    finished = pyqtSignal(tuple)


class AssetJob(QRunnable):
    def __init__(self, key, request, signals):
        super().__init__()
        self.key, self.request, self.signals = key, request, signals
        self.cancel_event = Event()

    def run(self):
        value, error = None, ""
        try:
            from app.pdfworker import render_watermark_asset
            value = render_watermark_asset(*self.request, cancel=self.cancel_event.is_set)
        except OperationCancelled:
            pass
        except Exception as exc:
            error = str(exc)
        self.signals.ready.emit(self.key, value, error)
        self.signals.finished.emit(self.key)


class PagePreview(QWidget):
    moved = pyqtSignal(float, float)
    drag_started = pyqtSignal()
    drag_cancelled = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.image = self.asset = None
        self.page_size = (1, 1)
        self.base_size = (1, 1)
        self.options = {}
        self.message = "正在准备页面预览…"
        self.paper = QRectF()
        self.marks = []
        self._paper_pixmap = self._mark_pixmap = None
        self._paper_key = self._mark_key = None
        self._drag = None
        self._hover = False
        self.setContentsMargins(16, 16, 16, 16)
        self.setMinimumSize(220, 260)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("水印页面预览，可拖动水印，方向键微调，Esc 撤销当前拖动")
        self.setToolTip("拖动水印调整位置；方向键微调，Shift 加大步长。")

    def setText(self, text):
        self.message = text
        self.update()

    def set_image(self, image, page_size=None):
        self.image = image
        self.page_size = page_size or (image.width(), image.height())
        self._paper_key = None
        self.update_geometry()

    def set_asset(self, value):
        self.asset, width, height = value
        self.base_size = (width, height)
        self._mark_key = None
        self.update()

    def set_options(self, options):
        self.options = dict(options)
        self.update_geometry()

    def update_geometry(self):
        self.marks = []
        if self.image is None:
            self.update()
            return
        bounds = self.contentsRect()
        if bounds.width() <= 0 or bounds.height() <= 0:
            self.paper = QRectF()
            self.update()
            return
        width, height = self.page_size
        scale = min(bounds.width() / max(width, 1), bounds.height() / max(height, 1))
        self.paper = QRectF(bounds.x() + (bounds.width() - width * scale) / 2,
                            bounds.y() + (bounds.height() - height * scale) / 2, width * scale, height * scale)
        if self.asset is not None and self.options:
            mark_width, mark_height, locations = watermark_layout(width, height, *self.base_size, self.options)
            self.marks = [QRectF(self.paper.x() + x * scale, self.paper.y() + y * scale,
                                mark_width * scale, mark_height * scale) for x, y in locations]
        if not self.marks:
            self._hover = False
            self._drag = None
            self.unsetCursor()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        if self.image is None or self.paper.isEmpty():
            painter.setPen(QColor("#64748B"))
            painter.drawText(self.contentsRect(), Qt.AlignmentFlag.AlignCenter, self.message)
            return
        ratio = self.devicePixelRatioF()
        paper_key = (self.image.cacheKey(), round(self.paper.width() * ratio), round(self.paper.height() * ratio), ratio)
        if self._paper_key != paper_key:
            self._paper_pixmap = QPixmap.fromImage(self.image).scaled(
                QSize(max(1, paper_key[1]), max(1, paper_key[2])), Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            self._paper_key = paper_key
        painter.drawPixmap(self.paper, self._paper_pixmap, QRectF(self._paper_pixmap.rect()))
        if not self.marks:
            return
        angle = float(self.options.get("angle", 45))
        mark = self.marks[0]
        mark_key = (self.asset.cacheKey(), round(mark.width() * ratio), round(mark.height() * ratio), angle, ratio)
        if self._mark_key != mark_key:
            width, height = self.base_size
            radians = math.radians(angle)
            rotated_width = abs(width * math.cos(radians)) + abs(height * math.sin(radians))
            factor = mark.width() * ratio / max(rotated_width, 1)
            pixmap = QPixmap.fromImage(self.asset).scaled(
                QSize(max(1, round(width * factor)), max(1, round(height * factor))),
                Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self._mark_pixmap = pixmap.transformed(QTransform().rotate(-angle), Qt.TransformationMode.SmoothTransformation)
            self._mark_key = mark_key
        painter.save()
        painter.setClipRect(self.paper)
        painter.setOpacity(float(self.options.get("opacity", .2)))
        for rect in self.marks:
            painter.drawPixmap(rect, self._mark_pixmap, QRectF(self._mark_pixmap.rect()))
        painter.restore()
        if self._hover or self._drag or self.hasFocus():
            painter.setPen(QPen(QColor("#0F766E"), 1, Qt.PenStyle.DashLine))
            for rect in self.marks:
                painter.drawRect(rect.intersected(self.paper))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_geometry()

    def offset(self):
        if self.options.get("tile"):
            return tuple(self.options.get("tile_offset", (.5, .5)))
        if self.options.get("position") == "custom":
            return tuple(self.options.get("offset", (.5, .5)))
        if not self.marks:
            return (.5, .5)
        mark = self.marks[0]
        return ((mark.x() - self.paper.x()) / max(1, self.paper.width() - mark.width()),
                (mark.y() - self.paper.y()) / max(1, self.paper.height() - mark.height()))

    def translate(self, dx, dy, start=None):
        if not self.marks or self.paper.isEmpty():
            return
        x, y = self.offset() if start is None else start
        mark = self.marks[0]
        if self.options.get("tile"):
            width, height = self.page_size
            mw = mark.width() * width / self.paper.width()
            mh = mark.height() * height / self.paper.height()
            rows, columns = watermark_grid(width, height, mw, mh, self.options)
            x, y = (x + dx / (self.paper.width() / columns)) % 1, (y + dy / (self.paper.height() / rows)) % 1
        else:
            x = max(0, min(1, x + dx / max(1, self.paper.width() - mark.width())))
            y = max(0, min(1, y + dy / max(1, self.paper.height() - mark.height())))
        self.moved.emit(x, y)

    def mousePressEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton and self.paper.contains(event.position())
                and any(rect.contains(event.position()) for rect in self.marks)):
            self._drag = (QPointF(event.position()), self.offset())
            self.drag_started.emit()
            self.setFocus()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            origin, offset = self._drag
            delta = event.position() - origin
            self.translate(delta.x(), delta.y(), offset)
            return
        hover = any(rect.contains(event.position()) and self.paper.contains(event.position()) for rect in self.marks)
        if hover != self._hover:
            self._hover = hover
            self.setCursor(Qt.CursorShape.OpenHandCursor if hover else Qt.CursorShape.ArrowCursor)
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag is not None:
            origin, offset = self._drag
            delta = event.position() - origin
            self.translate(delta.x(), delta.y(), offset)
            self._drag = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self._drag:
            self._drag = None
            self.drag_cancelled.emit()
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        directions = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0),
                      Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if event.key() in directions and self.marks:
            dx, dy = directions[event.key()]
            step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 2
            self.translate(dx * step, dy * step)
            event.accept()
            return
        super().keyPressEvent(event)
