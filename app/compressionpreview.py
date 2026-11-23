"""Two continuous PDF columns sharing one scroll position, scale and render queue."""
from app.i18n import tr
from app.themes import PAPER_HINT_COLOR, set_theme_style, ui_color
from bisect import bisect_right
from collections import OrderedDict
import math
from threading import Event

from PyQt6.QtCore import QObject, QRect, QRectF, QRunnable, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QAbstractScrollArea

from app.pdfworker import render_preview
from app.output import OperationCancelled


class _Signals(QObject):
    ready = pyqtSignal(tuple, object, str)


class _RenderPair(QRunnable):
    def __init__(self, refs, passwords, key, cancel, signals, *, clip=None, scale=None):
        super().__init__()
        self.refs, self.passwords, self.key = refs, passwords, key
        self.cancel, self.signals = cancel, signals
        self.clip, self.scale = clip, scale

    def run(self):
        images, error = None, ''
        try:
            if self.clip is not None and self.scale is not None:
                images = render_preview(self.refs, self.passwords, None, self.cancel.is_set,
                                        clip=self.clip, scale=self.scale)
            else:
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
        self.images, self.detail_tiles, self.errors = OrderedDict(), OrderedDict(), {}
        self.mode, self.current_page = 'page', 0
        self.generation, self.busy, self.closed = 0, False, False
        self.cancel = Event()
        self.message = tr('点击右上角「开始压缩」，即可在此左右同步对照画质与细节')
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
        self.setAccessibleName(tr('压缩前后连续对照预览，两侧同步滚动和缩放'))
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.verticalScrollBar().setSingleStep(60)
        self.horizontalScrollBar().setSingleStep(60)
        self.verticalScrollBar().valueChanged.connect(self.scrolled)
        self.horizontalScrollBar().valueChanged.connect(self._horizontal_scrolled)
        set_theme_style(self, 'QAbstractScrollArea { background: #F4F7F6; border: none; border-bottom-left-radius: 9px; border-bottom-right-radius: 9px; }'
                           'QScrollBar:vertical { background: transparent; width: 8px; margin: 2px 1px; }'
                           'QScrollBar:horizontal { background: transparent; height: 8px; margin: 1px 2px; }'
                           'QScrollBar::handle { background: #C5D5D2; border-radius: 4px; }'
                           'QScrollBar::handle:vertical { min-height: 36px; }'
                           'QScrollBar::handle:horizontal { min-width: 36px; }'
                           'QScrollBar::handle:hover { background: #99B2AC; }'
                           'QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }')

    def clear(self, message):
        self.cancel.set()
        self.cancel = Event()
        self.generation += 1
        self.pages, self.passwords, self.sizes = [], [], []
        self.images.clear()
        self.detail_tiles.clear()
        self.errors.clear()
        self.message = message
        self.relayout()

    def set_documents(self, store, before, after):
        self.clear(tr('正在准备预览…'))
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

    def _horizontal_scrolled(self):
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

    def _detail_plan(self, page):
        """Return (tile_key, clip_uv, target_scale, visible_uv) when zoomed past whole-page cap."""
        width, height = self.sizes[page]
        scale = self.scales[page]
        dpr = max(1.0, self.devicePixelRatioF())
        wanted_w = width * scale * dpr
        base_w = self.image_key(page)[2]
        if wanted_w <= base_w * 1.05:
            return None
        column = self.column_width()
        vp_h = self.viewport().height()
        pw, ph = width * scale, height * scale
        if pw <= 1 or ph <= 1:
            return None
        x0 = max(14, (column - pw) / 2) - self.horizontalScrollBar().value()
        y0 = self.offsets[page] - self.verticalScrollBar().value() + 29
        paper = QRectF(x0, y0, pw, ph)
        vis = paper.intersected(QRectF(0, 0, column, vp_h))
        if vis.width() < 2 or vis.height() < 2:
            return None
        vis_uv = (
            max(0.0, (vis.left() - paper.left()) / pw),
            max(0.0, (vis.top() - paper.top()) / ph),
            min(1.0, (vis.right() - paper.left()) / pw),
            min(1.0, (vis.bottom() - paper.top()) / ph),
        )
        # Add 64px overscan around the visible column so small drags reuse the same crisp tile.
        over = paper.intersected(QRectF(-64, -64, column + 128, vp_h + 128))
        u0 = max(0.0, math.floor(((over.left() - paper.left()) / pw) * 250) / 250)
        v0 = max(0.0, math.floor(((over.top() - paper.top()) / ph) * 250) / 250)
        u1 = min(1.0, math.ceil(((over.right() - paper.left()) / pw) * 250) / 250)
        v1 = min(1.0, math.ceil(((over.bottom() - paper.top()) / ph) * 250) / 250)
        if u1 <= u0 + 1e-4 or v1 <= v0 + 1e-4:
            return None
        target_scale = scale * dpr
        scale_bucket = round(target_scale * 100)
        clip_uv = (u0, v0, u1, v1)
        tile_key = (self.generation, page, 'tile', scale_bucket, u0, v0, u1, v1)
        return tile_key, clip_uv, target_scale, vis_uv

    def _find_covering_tile(self, page, scale_bucket, vis_uv):
        u0, v0, u1, v1 = vis_uv
        eps = 1e-4
        for key, value in reversed(self.detail_tiles.items()):
            if key[0] == self.generation and key[1] == page and key[3] == scale_bucket:
                tu0, tv0, tu1, tv1 = value[0]
                if tu0 <= u0 + eps and tv0 <= v0 + eps and tu1 >= u1 - eps and tv1 >= v1 - eps:
                    return key, value
        return None, None

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
        if self.drag_position is not None:
            return
        for page in self.visible_pages():
            plan = self._detail_plan(page)
            if not plan:
                continue
            tile_key, clip_uv, target_scale, vis_uv = plan
            existing_key, _ = self._find_covering_tile(page, tile_key[3], vis_uv)
            if existing_key is None and tile_key not in self.errors:
                self.busy = True
                self.pool.start(_RenderPair(
                    self.pages[page], self.passwords[page], tile_key, self.cancel, self.signals,
                    clip=clip_uv, scale=target_scale,
                ))
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
                if len(key) > 3 and key[2] == 'tile':
                    self.detail_tiles[key] = (key[4:8], images)
                    self.detail_tiles.move_to_end(key)
                    while len(self.detail_tiles) > 6:
                        self.detail_tiles.popitem(last=False)
                else:
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
        vp = self.viewport().rect()
        painter.fillRect(vp, QColor(ui_color('#F4F7F6')))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        mid_x = vp.width() / 2.0
        if not self.pages:
            # Subtle dual-column skeleton guides + centered pill message
            sep_pen = QPen(QColor(ui_color('#D5E2DE')), 1.0, Qt.PenStyle.DashLine)
            painter.setPen(sep_pen)
            painter.drawLine(int(mid_x), 18, int(mid_x), vp.height() - 18)
            card_h = min(vp.height() - 68, 280)
            card_w = min(mid_x - 72, card_h * 0.72)
            if card_w >= 80 and card_h >= 100:
                card_y = (vp.height() - card_h) / 2.0
                skeleton_pen = QPen(QColor(ui_color('#D5E2DE')), 1.0, Qt.PenStyle.DashLine)
                painter.setPen(skeleton_pen)
                painter.setBrush(QColor(ui_color('#F8FBFA')))
                for cx in (mid_x / 2.0, mid_x + mid_x / 2.0):
                    painter.drawRoundedRect(QRectF(cx - card_w / 2.0, card_y, card_w, card_h), 6.0, 6.0)
            # Centered guidance badge (auto-grows vertically for wrapped long-locale text)
            fm = painter.fontMetrics()
            max_text_w = max(120, int(vp.width() - 96))
            flags = Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap
            bounds = fm.boundingRect(QRect(0, 0, max_text_w, 240), flags, self.message)
            pill_w = min(vp.width() - 64, bounds.width() + 36)
            pill_h = max(40, bounds.height() + 20)
            pill = QRectF((vp.width() - pill_w) / 2.0, (vp.height() - pill_h) / 2.0, pill_w, pill_h)
            painter.setPen(QPen(QColor(ui_color('#D5E2DE')), 1.0))
            painter.setBrush(QColor(ui_color('#FFFFFF')))
            painter.drawRoundedRect(pill, 8.0, 8.0)
            painter.setPen(QColor(ui_color('#526B65')))
            painter.drawText(pill.adjusted(14, 8, -14, -8), flags, self.message)
            return
        # Center vertical comparison divider
        painter.setPen(QPen(QColor(ui_color('#E2EEEC')), 1.0))
        painter.drawLine(int(mid_x), 0, int(mid_x), vp.height())
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
            detail_pair = None
            detail_uv = None
            plan = self._detail_plan(page)
            if plan is not None:
                tkey, found = self._find_covering_tile(page, plan[0][3], plan[3])
                if tkey is not None and found is not None:
                    detail_uv, detail_pair = found
                    self.detail_tiles.move_to_end(tkey)
                else:
                    vu0, vv0, vu1, vv1 = plan[3]
                    for tkey, (tuv, tpair) in reversed(self.detail_tiles.items()):
                        if tkey[0] == self.generation and tkey[1] == page and tkey[3] == plan[0][3]:
                            tu0, tv0, tu1, tv1 = tuv
                            if min(tu1, vu1) > max(tu0, vu0) and min(tv1, vv1) > max(tv0, vv0):
                                detail_uv, detail_pair = tuv, tpair
                                break
            for side in range(2):
                left = side * (column + 16)
                painter.save()
                painter.setClipRect(QRectF(left, 0, column, self.viewport().height()))
                x = left + (max(14, (column - width * scale) / 2) - self.horizontalScrollBar().value())
                paper = QRectF(x, top + 29, width * scale, height * scale)
                painter.setPen(QColor(ui_color('#526B65')))
                painter.drawText(QRectF(left + 14, top + 6, column - 28, 22), f'第 {page + 1} 页')
                # Soft paper shadow
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(ui_color('#E4EAE7')))
                painter.drawRect(paper.translated(0, 2))
                painter.fillRect(paper, QColor('white'))
                if images:
                    painter.drawImage(paper, images[side])
                    if detail_pair is not None and detail_uv is not None:
                        tu0, tv0, tu1, tv1 = detail_uv
                        tile_rect = QRectF(
                            paper.left() + tu0 * paper.width(),
                            paper.top() + tv0 * paper.height(),
                            (tu1 - tu0) * paper.width(),
                            (tv1 - tv0) * paper.height(),
                        )
                        painter.drawImage(tile_rect, detail_pair[side])
                else:
                    painter.setPen(QColor(PAPER_HINT_COLOR))
                    painter.drawText(paper.adjusted(16, 16, -16, -16),
                                     Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                                     self.errors.get(key, '正在准备页面…'))
                painter.setPen(QPen(QColor(ui_color('#CAD5CD')), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
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
        was_dragging = self.drag_position is not None
        self.drag_position = None
        self.viewport().unsetCursor()
        if was_dragging:
            self.schedule()
        super().mouseReleaseEvent(event)

    def shutdown(self):
        self.closed = True
        self.cancel.set()
        self.pump.stop()
