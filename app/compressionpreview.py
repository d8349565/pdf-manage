"""Two continuous PDF columns sharing one scroll position, scale and render queue."""
from app.themes import PAPER_HINT_COLOR, set_theme_style, ui_color
from bisect import bisect_right
from collections import OrderedDict
import math
from threading import Event

from PyQt6.QtCore import QObject, QRunnable, QRectF, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QAbstractScrollArea

from app.pdfworker import render_preview
from app.output import OperationCancelled


class _Signals(QObject):
    ready = pyqtSignal(tuple, object, str)


class _RenderPair(QRunnable):
    def __init__(self, refs, passwords, key, cancel, signals):
        super().__init__()
        self.refs, self.passwords, self.key = refs, passwords, key
        self.cancel, self.signals = cancel, signals

    def run(self):
        images, error = None, ''
        try:
            images = render_preview(self.refs, self.passwords, None, self.cancel.is_set, width=self.key[2])
        except OperationCancelled:
            pass
        except Exception as exc:
            error = str(exc)
        self.signals.ready.emit(self.key, images, error)


class CompressionPreview(QAbstractScrollArea):
    page_changed = pyqtSignal(int)
    zoom_changed = pyqtSignal(object)
    stopped = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pages, self.passwords, self.sizes = [], [], []
        self.offsets, self.scales = [0], []
        self.images, self.errors = OrderedDict(), {}
        self.mode, self.current_page = 'page', 0
        self.generation, self.busy, self.closed = 0, False, False
        self.cancel = Event()
        self.message = '计算后，可在这里连续滚动对照全部页面'
        self.drag_position = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.signals = _Signals()
        self.signals.ready.connect(self.rendered)
        self.pump = QTimer(self)
        self.pump.setSingleShot(True)
        self.pump.setInterval(50)
        self.pump.timeout.connect(self.render_next)
        self.setMinimumSize(620, 280)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName('压缩前后连续对照预览，两侧同步滚动和缩放')
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.verticalScrollBar().setSingleStep(60)
        self.horizontalScrollBar().setSingleStep(60)
        self.verticalScrollBar().valueChanged.connect(self.scrolled)
        self.horizontalScrollBar().valueChanged.connect(self.viewport().update)
        set_theme_style(self, 'QAbstractScrollArea { background: #EAF0EB; border: 1px solid #CED8D1; border-radius: 6px; }'
                           'QAbstractScrollArea:focus { border-color: #0F766E; }'
                           'QScrollBar:vertical { background: #DFE8E2; width: 14px; margin: 0; }'
                           'QScrollBar:horizontal { background: #DFE8E2; height: 14px; margin: 0; }'
                           'QScrollBar::handle { background: #607D6C; border-radius: 5px; }'
                           'QScrollBar::handle:vertical { min-height: 40px; }'
                           'QScrollBar::handle:horizontal { min-width: 40px; }'
                           'QScrollBar::handle:hover { background: #3E6955; }'
                           'QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }')

    def clear(self, message):
        self.cancel.set()
        self.cancel = Event()
        self.generation += 1
        self.pages, self.passwords, self.sizes = [], [], []
        self.images.clear()
        self.errors.clear()
        self.message = message
        self.relayout()

    def set_documents(self, store, before, after):
        self.clear('正在准备预览…')
        self.pages = list(zip(before, after))
        self.passwords = [tuple(store._source(ref.source_id).password for ref in pair) for pair in self.pages]
        self.sizes = [store.page_size(pair[0]) for pair in self.pages]
        self.relayout()
        self.scroll_to_page(0)
        self.schedule()

    def column_width(self):
        return max(100, (self.viewport().width() - 16) / 2)

    def relayout(self):
        self.offsets, self.scales = [0], []
        max_width = 0
        for width, height in self.sizes:
            scale = (self.column_width() - 28) / max(1, width)
            if self.mode == 'page':
                scale = min(scale, max(100, self.viewport().height() - 48) / max(1, height))
            elif self.mode != 'width':
                scale = self.mode / 100 * 96 / 72
            self.scales.append(scale)
            self.offsets.append(self.offsets[-1] + round(height * scale) + 44)
            max_width = max(max_width, width * scale + 28)
        self.verticalScrollBar().setPageStep(self.viewport().height())
        self.verticalScrollBar().setRange(0, max(0, self.offsets[-1] - self.viewport().height()))
        self.horizontalScrollBar().setPageStep(round(self.column_width()))
        self.horizontalScrollBar().setRange(0, max(0, math.ceil(max_width - self.column_width())))
        self.viewport().update()

    def set_zoom(self, mode):
        if isinstance(mode, (int, float)):
            mode = max(25, min(400, round(mode)))
        if mode == self.mode:
            return
        page = self.current_page
        old_height = self.offsets[page + 1] - self.offsets[page] if self.pages else 1
        fraction = (self.verticalScrollBar().value() - self.offsets[page]) / old_height if self.pages else 0
        horizontal = self.horizontalScrollBar().value() / max(1, self.horizontalScrollBar().maximum())
        self.mode = mode
        self.relayout()
        if self.pages:
            self.verticalScrollBar().setValue(round(self.offsets[page] + fraction * (self.offsets[page + 1] - self.offsets[page])))
        self.horizontalScrollBar().setValue(round(horizontal * self.horizontalScrollBar().maximum()))
        self.zoom_changed.emit(mode)
        self.schedule()

    def step_zoom(self, direction):
        percent = self.scales[self.current_page] * 75 if self.scales else 100
        self.set_zoom(percent * (1.25 if direction > 0 else .8))

    def visible_pages(self):
        if not self.pages:
            return []
        top = self.verticalScrollBar().value()
        first = min(len(self.pages) - 1, max(0, bisect_right(self.offsets, top) - 1))
        last = min(len(self.pages), bisect_right(self.offsets, top + self.viewport().height()))
        return list(range(first, max(first + 1, last)))

    def scrolled(self):
        visible = self.visible_pages()
        page = min(len(self.pages) - 1, max(0, bisect_right(self.offsets, self.verticalScrollBar().value() + 32) - 1))
        if visible and page != self.current_page:
            self.current_page = page
            self.page_changed.emit(self.current_page)
        self.viewport().update()
        self.schedule()

    def scroll_to_page(self, page):
        if self.pages:
            page = min(len(self.pages) - 1, max(0, page))
            self.current_page = page
            self.verticalScrollBar().setValue(self.offsets[page])
            self.page_changed.emit(page)
            self.schedule()

    def image_key(self, page):
        width, height = self.sizes[page]
        wanted = max(320, width * self.scales[page] * self.devicePixelRatioF())
        render_width = min(2400, math.sqrt(4_000_000 * width / height), math.ceil(wanted / 128) * 128)
        return self.generation, page, max(1, round(render_width))

    def schedule(self):
        if not self.closed:
            self.pump.start()

    def render_next(self):
        if self.busy or self.closed:
            return
        visible = self.visible_pages()
        if visible and visible[-1] + 1 < len(self.pages):
            visible.append(visible[-1] + 1)
        for page in visible:
            key = self.image_key(page)
            if key not in self.images and key not in self.errors:
                self.busy = True
                self.pool.start(_RenderPair(self.pages[page], self.passwords[page], key, self.cancel, self.signals))
                return

    def rendered(self, key, images, error):
        self.busy = False
        if self.closed:
            self.stopped.emit()
            return
        if key[0] == self.generation:
            if error:
                self.errors[key] = error
            elif images:
                for old in list(self.images):
                    if old[:2] == key[:2]:
                        del self.images[old]
                self.images[key] = images
                # Keep every visible short/landscape page, otherwise an LRU smaller
                # than the viewport would endlessly evict and rerender those pages.
                while len(self.images) > max(4, len(self.visible_pages()) + 1):
                    self.images.popitem(last=False)
            self.viewport().update()
        self.schedule()

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.fillRect(self.viewport().rect(), QColor(ui_color('#EAF0EB')))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if not self.pages:
            painter.setPen(QColor(ui_color('#53635B')))
            painter.drawText(self.viewport().rect().adjusted(24, 24, -24, -24),
                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)
            return
        column = self.column_width()
        for page in self.visible_pages():
            width, height = self.sizes[page]
            scale = self.scales[page]
            top = self.offsets[page] - self.verticalScrollBar().value()
            key = self.image_key(page)
            images = self.images.get(key)
            if images is None:
                images = next((value for old, value in reversed(self.images.items()) if old[:2] == key[:2]), None)
            elif key in self.images:
                self.images.move_to_end(key)
            for side in range(2):
                left = side * (column + 16)
                painter.save()
                painter.setClipRect(QRectF(left, 0, column, self.viewport().height()))
                x = left + (max(14, (column - width * scale) / 2) - self.horizontalScrollBar().value())
                paper = QRectF(x, top + 29, width * scale, height * scale)
                painter.setPen(QColor(ui_color('#53635B')))
                painter.drawText(QRectF(left + 14, top + 4, column - 28, 24), f'第 {page + 1} 页')
                painter.fillRect(paper, QColor('white'))
                if images:
                    painter.drawImage(paper, images[side])
                else:
                    painter.setPen(QColor(PAPER_HINT_COLOR))
                    painter.drawText(paper.adjusted(16, 16, -16, -16),
                                     Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                                     self.errors.get(key, '正在准备页面…'))
                painter.setPen(QPen(QColor(ui_color('#CAD5CD')), 1))
                painter.drawRect(paper)
                painter.restore()

    def resizeEvent(self, event):
        page = self.current_page if self.pages else 0
        fraction = ((self.verticalScrollBar().value() - self.offsets[page]) /
                    max(1, self.offsets[page + 1] - self.offsets[page])) if self.pages else 0
        super().resizeEvent(event)
        self.relayout()
        if self.pages:
            self.verticalScrollBar().setValue(round(self.offsets[page] +
                                                   fraction * (self.offsets[page + 1] - self.offsets[page])))
        self.scrolled()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.pixelDelta().y() if not event.pixelDelta().isNull() else event.angleDelta().y()
            if delta:
                self.step_zoom(1 if delta > 0 else -1)
            event.accept()
        else:
            super().wheelEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = event.position()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_position is not None:
            delta = event.position() - self.drag_position
            self.drag_position = event.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - round(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - round(delta.y()))
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.drag_position = None
        self.viewport().unsetCursor()
        super().mouseReleaseEvent(event)

    def shutdown(self):
        self.closed = True
        self.cancel.set()
        self.pump.stop()
