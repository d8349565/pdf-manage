"""Continuous page previews: one background render at a time, bounded cache."""
from collections import OrderedDict
from threading import Event

from PyQt6.QtCore import (QAbstractListModel, QObject, QPoint, QRunnable, QRectF,
                          QSize, Qt, QThreadPool, QTimer, pyqtSignal)
from PyQt6.QtGui import QColor, QPainter, QPen, QTransform
from PyQt6.QtWidgets import QAbstractItemView, QListView, QStyledItemDelegate

from app.workspace import PageEdit


def draw_fitted_page(painter, rectangle, image, angle=0):
    source = QRectF(0, 0, image.width(), image.height())
    rotated = QTransform().rotate(angle).mapRect(source)
    scale = min(rectangle.width() / rotated.width(), rectangle.height() / rotated.height())
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setClipRect(rectangle)
    painter.translate(rectangle.center())
    painter.rotate(angle)
    painter.scale(scale, scale)
    painter.translate(-source.width() / 2, -source.height() / 2)
    painter.drawImage(source, image)
    painter.restore()


class _Signals(QObject):
    ready = pyqtSignal(tuple, object, str)


class _Render(QRunnable):
    def __init__(self, store, ref, width, key, signals, cancel):
        super().__init__()
        self.store, self.ref, self.width = store, ref, width
        self.key, self.signals, self.cancel = key, signals, cancel

    def run(self):
        image, error = None, ''
        try:
            if not self.cancel.is_set():
                image = self.store.render_qimage(self.ref, self.width)
        except Exception as exc:
            error = str(exc)
        if self.cancel.is_set():
            image, error = None, ''
        self.signals.ready.emit(self.key, image, error)


class _Pages(QAbstractListModel):
    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def rowCount(self, parent=None):
        if parent is not None and parent.isValid():
            return 0
        return len(self.view.rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return f'第 {self.view.rows[index.row()] + 1} 页'
        if role == Qt.ItemDataRole.SizeHintRole:
            return self.view.item_size(index.row())


class _Paper(QStyledItemDelegate):
    def paint(self, painter, option, index):
        view = self.parent()
        source_row = view.rows[index.row()]
        area = view.paper_rect(source_row,option.rect)
        painter.save()
        painter.setPen(QColor('#53635B'))
        painter.drawText(option.rect.adjusted(18, 2, -18, 0), Qt.AlignmentFlag.AlignTop,
                         f'第 {source_row + 1} 页 · {index.row() + 1} / {len(view.rows)}')
        painter.fillRect(area, QColor('white'))
        key = view.image_key(source_row)
        image = view.images.get(key)
        if image is not None:
            view.images.move_to_end(key)
            if view.draw_content:
                view.draw_content(painter, area, source_row, image)
            else:
                draw_fitted_page(painter, area, image)
        else:
            painter.setPen(QColor('#738078'))
            message = view.errors.get(key, '正在准备页面…')
            painter.drawText(area.adjusted(16, 16, -16, -16),
                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, message)
            if key not in view.errors:
                view.schedule()
        painter.setPen(QPen(QColor('#CED8D1'), 1))
        painter.drawRect(area)
        painter.restore()


class ScrollablePagePreview(QListView):
    page_changed = pyqtSignal(int)
    image_ready = pyqtSignal(int)

    def __init__(self, store, pages, parent=None, *, paper_size=None, draw_content=None):
        super().__init__(parent)
        self.store, self.pages = store, pages
        self.paper_size = paper_size or (lambda row: store.page_size(pages[row]))
        self.draw_content = draw_content
        self.fit_page = False
        self.max_paper_width = 520
        self.render_when_hidden = False
        self._page_wheel = 0
        self.rows, self.remove_group = [], ''
        self.images, self.errors = OrderedDict(), {}
        self.generation, self.busy, self.closed = 0, False, False
        self.cancel = Event()
        self.current_source_row = None
        self.signals = _Signals()
        self.signals.ready.connect(self.rendered)
        self.pump = QTimer(self)
        self.pump.setSingleShot(True)
        self.pump.timeout.connect(self.render_next)
        self.page_model = _Pages(self)
        self.setModel(self.page_model)
        self.setItemDelegate(_Paper(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setSpacing(0)
        self.setMinimumSize(330, 320)
        self.setStyleSheet('QListView { background: #EAF0EB; border: 1px solid #D5DFD6; border-radius: 8px; }')
        self.setAccessibleName('连续页面预览，滚轮查看上下页')
        self.verticalScrollBar().setSingleStep(55)
        self.verticalScrollBar().valueChanged.connect(self.schedule)

    def set_rows(self, rows, remove_group=''):
        rows = list(rows)
        if rows == self.rows and remove_group == self.remove_group:
            return
        self.cancel.set()
        self.cancel = Event()
        self.generation += 1
        self.page_model.beginResetModel()
        self.rows, self.remove_group = rows, remove_group
        self.images.clear()
        self.errors.clear()
        self.current_source_row = None
        self.page_model.endResetModel()
        self.schedule()

    def item_size(self, position):
        if self.fit_page:
            return QSize(max(100,self.viewport().width()-4),max(120,self.viewport().height()-2))
        width, height = self.paper_size(self.rows[position])
        paper_width = min(self.max_paper_width, max(100, self.viewport().width() - 38), 1200 * width / height)
        return QSize(max(100, self.viewport().width() - 4), round(paper_width * height / width) + 54)

    def paper_rect(self,row,rectangle):
        width,height=self.paper_size(row)
        available=max(80,self.viewport().width()-38)
        if self.fit_page:
            scale=min(available/width,max(80,self.viewport().height()-58)/height)
            paper_width,paper_height=width*scale,height*scale
        else:
            paper_width=min(self.max_paper_width,available,1200*width/height)
            paper_height=paper_width*height/width
        return QRectF(rectangle.x()+(rectangle.width()-paper_width)/2,
                      rectangle.y()+31,paper_width,paper_height)

    def set_fit_page(self,enabled):
        row=self.current_source_row
        self.fit_page=enabled
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerItem if enabled else QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(1 if enabled else 55)
        self._page_wheel=0
        self.refresh_pages(layout=True)
        if row is not None:
            self.scroll_to_page(row)

    def wheelEvent(self,event):
        if self.fit_page and not event.modifiers():
            delta=event.angleDelta().y() or event.pixelDelta().y()
            self._page_wheel+=delta
            unit=120 if event.angleDelta().y() else 45
            steps=int(self._page_wheel/unit)
            self._page_wheel-=steps*unit
            if steps:
                bar=self.verticalScrollBar();bar.setValue(bar.value()-steps)
            event.accept()
            return
        super().wheelEvent(event)

    def image_key(self, row):
        width, height = self.store.page_size(self.pages[row])
        render_width = max(1, round(min(650, 1200 * width / height)))
        return self.generation, row, render_width

    def schedule(self, *_):
        if not self.closed and not self.pump.isActive():
            self.pump.start(0)

    def visible_positions(self):
        if not self.rows:
            return []
        top = self.indexAt(QPoint(8, 8))
        bottom = self.indexAt(QPoint(8, max(8, self.viewport().height() - 8)))
        start = top.row() if top.isValid() else 0
        stop = bottom.row() if bottom.isValid() else start + 1
        return list(range(max(0, start), min(len(self.rows), max(stop, start) + 2)))

    def render_next(self):
        if self.closed or not self.isVisible() and (not self.render_when_hidden or not self.window().isVisible()):
            return
        positions = self.visible_positions()
        if positions:
            current = self.rows[positions[0]]
            if current != self.current_source_row:
                self.current_source_row = current
                self.page_changed.emit(current)
        if self.busy:
            return
        for position in positions:
            row = self.rows[position]
            key = self.image_key(row)
            if key in self.images or key in self.errors:
                continue
            ref = self.pages[row]
            if self.remove_group:
                ref = ref.edited(PageEdit('seam_remove', annotation_id=self.remove_group))
            self.busy = True
            QThreadPool.globalInstance().start(_Render(self.store, ref, key[2], key, self.signals, self.cancel))
            return

    def rendered(self, key, image, error):
        self.busy = False
        if self.closed:
            return
        if key[0] == self.generation:
            if error:
                self.errors[key] = error
            elif image is not None:
                self.images[key] = image
                while len(self.images) > 8:
                    self.images.popitem(last=False)
                self.image_ready.emit(key[1])
            self.viewport().update()
        self.schedule()

    def refresh_pages(self, *, layout=False):
        if layout:
            self.doItemsLayout()
        self.viewport().update()
        self.schedule()

    def scroll_to_page(self, row):
        if row in self.rows:
            self.scrollTo(self.page_model.index(self.rows.index(row)), QAbstractItemView.ScrollHint.PositionAtTop)
            self.schedule()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh_pages(layout=True)

    def showEvent(self, event):
        super().showEvent(event)
        self.schedule()

    def shutdown(self):
        self.closed = True
        self.cancel.set()
        self.pump.stop()
