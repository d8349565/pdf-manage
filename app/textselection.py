"""Read-only rectangle selection for visible and invisible PDF text layers."""
from time import perf_counter

from PyQt6.QtCore import QEvent, QObject, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QKeySequence, QPainterPath, QPen, QShortcut
from PyQt6.QtWidgets import QApplication, QGraphicsPathItem, QMenu

from app.rendering import RenderSignals


class TextSelection(QObject):
    def __init__(self, host):
        super().__init__(host)
        self.host, self.view = host, host.preview_view
        self.snapshot = None
        self.page_index = None
        self.key = None
        self.generation = None
        self.start = None
        self.text = ''
        self.area = None
        self.overlay = None
        self._pending = None
        self._selection_serial = 0
        self.signals = RenderSignals(self)
        self.signals.done.connect(self._snapshot_ready, Qt.ConnectionType.QueuedConnection)
        self.shortcut = QShortcut(QKeySequence.StandardKey.Copy, self.view)
        self.shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.shortcut.activated.connect(self.copy)
        self.shortcut.setEnabled(False)
        self.view.viewport().installEventFilter(self)
        self.view.installEventFilter(self)
        self.view.viewport().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.viewport().customContextMenuRequested.connect(self.menu)
        self.view.setToolTip('点击链接跳转；拖动框选文字；Ctrl+C 复制；按住空格拖动页面（也可 Alt+拖动）')

    def clear(self):
        self._pending = None
        self._selection_serial += 1
        if self.overlay is not None:
            self.view.scene().removeItem(self.overlay)
        self.overlay = None
        self.text, self.start, self.area = '', None, None
        self.snapshot, self.page_index, self.key = None, None, None
        self.shortcut.setEnabled(False)

    def sync(self):
        if self._pending is not None and not self._pending_current(self._pending):
            self.clear()
        if self.page_index is not None and (
                self.page_index >= len(self.host.ws.pages)
                or self.host.ws.pages[self.page_index].render_key != self.key
                or self.view.layout_generation != self.generation):
            self.clear()

    def active(self):
        tools = getattr(self.host, 'annotation_tools', None)
        return (getattr(self.host, '_task_dialog', None) is None
                and not (tools and tools.isVisible() and tools.tool.currentData() != 'browse'))

    def eventFilter(self, watched, event):
        self.sync()
        if not self.active():
            if self._pending is not None:
                self.clear()
            return False
        if (event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape
                and (self.text or self.start is not None or self._pending is not None)):
            self.clear()
            return True
        if watched is not self.view.viewport() or not self.host.ws.pages:
            return False
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self.clear()
            if event.modifiers() & Qt.KeyboardModifier.AltModifier:
                return False
            point = self.view.mapToScene(event.position().toPoint())
            index = self.view.page_at_point(point)
            if not 0 <= index < len(self.view.page_rects) or not self.view.page_rects[index].contains(point):
                return False
            ref = self.host.ws.pages[index]
            reason = self.host.store.operation_reason([ref], 'copy')
            if reason:
                self.host.statusBar().showMessage(reason, 5000)
                return False
            if index != self.host.ws.preview_index():
                self.host.go_to_page(index + 1, reveal=False)
            generation = self.view.layout_generation
            snapshot = self.host.store.cached_text_snapshot(ref, include_tables=False)
            if snapshot is not None:
                return self._begin(snapshot, ref, index, point, generation)
            key = ('text_selection', self._selection_serial, *ref.render_key)
            pending = dict(key=key, pages=self.host.ws.pages, ref=ref, index=index,
                           generation=generation, start=point, end=point, released=False,
                           started=perf_counter())
            self._pending = pending
            self.view.setFocus(Qt.FocusReason.MouseFocusReason)
            self.host.statusBar().showMessage('正在准备当前页文字…')
            self.host.rendering.request(ref, None, key, self.signals,
                lambda: self._pending is not pending or not self._pending_current(pending), 50, kind='text')
            return True
        if self._pending is not None and event.type() in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease):
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() != Qt.MouseButton.LeftButton:
                return False
            if not self._pending['released']:
                self._pending['end'] = self.view.mapToScene(event.position().toPoint())
                self._pending['released'] = event.type() == QEvent.Type.MouseButtonRelease
            return True
        if self.start is not None and event.type() in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease):
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() != Qt.MouseButton.LeftButton:
                return False
            self.select(self.view.mapToScene(event.position().toPoint()))
            if event.type() == QEvent.Type.MouseButtonRelease:
                self._finish()
            return True
        return False

    def _pending_current(self, pending):
        index, pages = pending['index'], self.host.ws.pages
        return (pages is pending['pages'] and index < len(pages) and pages[index] is pending['ref']
                and self.host.ws.preview_index() == index
                and self.view.layout_generation == pending['generation'] and self.active())

    def _begin(self, snapshot, ref, index, point, generation):
        if not any(block.get('lines') for block in snapshot['raw'].get('blocks', ())):
            return False
        self.snapshot, self.page_index, self.key = snapshot, index, ref.render_key
        self.generation, self.start = generation, point
        self.view.setFocus(Qt.FocusReason.MouseFocusReason)
        return True

    def _snapshot_ready(self, key, result):
        pending = self._pending
        if pending is None or pending['key'] != key:
            return
        self._pending = None
        if (not self._pending_current(pending) or result.cancelled
                or result.generation != self.host.rendering.generation):
            return
        self.host.rendering.diagnostics.record('text_selection_snapshot',
            duration_ms=round((perf_counter() - pending['started']) * 1000, 2), failed=bool(result.error))
        if result.error or result.value is None:
            self.host.statusBar().showMessage('当前页文字暂时无法读取，请重试。', 5000)
            return
        if not self._begin(result.value, pending['ref'], pending['index'], pending['start'], pending['generation']):
            self.host.statusBar().showMessage('当前页没有可框选的文字层。', 5000)
            return
        self.select(pending['end'])
        if pending['released']:
            self._finish()
        else:
            self.host.statusBar().showMessage('当前页文字已准备好，可继续拖动选择。', 3500)

    def _finish(self):
        self.start = None
        if self.text:
            self.host.statusBar().showMessage(f'已选取 {len(self.text)} 字 · Ctrl+C 或右键复制', 5000)

    def select(self, point):
        import pymupdf as p
        from app.pdfedits import selected_lines, selection_quads
        bounds = self.view.page_rects[self.page_index]
        point = QPointF(max(bounds.left(), min(bounds.right(), point.x())),
                        max(bounds.top(), min(bounds.bottom(), point.y())))
        rectangle = QRectF(self.start, point).normalized().translated(-bounds.topLeft())
        matrix = p.Matrix(self.snapshot['matrix'])
        canonical = p.Rect(rectangle.left(), rectangle.top(), rectangle.right(), rectangle.bottom()) * ~matrix
        self.area = canonical
        from app.documentlayout import selection_text
        self.text = selection_text(self.snapshot['raw'], canonical, self.snapshot.get('tables', ()))
        path = QPainterPath()
        for quad in selection_quads(self.snapshot['raw'], canonical):
            points = [p.Point(quad[i]) * matrix for i in (0, 1, 3, 2)]
            path.moveTo(bounds.topLeft() + QPointF(*points[0]))
            for mapped in points[1:]:
                path.lineTo(bounds.topLeft() + QPointF(*mapped))
            path.closeSubpath()
        if self.overlay is None:
            self.overlay = QGraphicsPathItem()
            self.overlay.setZValue(3)
            self.overlay.setPen(QPen(Qt.PenStyle.NoPen))
            self.overlay.setBrush(QColor(20, 149, 135, 70))
            self.view.scene().addItem(self.overlay)
        self.overlay.setPath(path)
        self.shortcut.setEnabled(bool(self.text))

    def copy(self):
        self.sync()
        if not self.text or self.page_index is None:
            return
        if not self.host._allow_operation('copy'):
            return
        plain_fallback = False
        if 'tables' not in self.snapshot:
            from app.progress import run_operation
            ref = self.host.ws.pages[self.page_index]
            try:
                snapshot = run_operation(self.host, '整理所选文字',
                    lambda progress: self.host.store.text_snapshot(ref))
            except Exception:
                plain_fallback = True
            else:
                self.sync()
                if not self.text or self.page_index is None:
                    return
                from app.documentlayout import selection_text
                self.snapshot = snapshot
                self.text = selection_text(snapshot['raw'], self.area, snapshot.get('tables', ()))
        self.sync()
        if not self.text or self.page_index is None:
            return
        QApplication.clipboard().setText(self.text)
        self.host.statusBar().showMessage(
            '表格排版无法读取，已按普通文字复制。' if plain_fallback else f'已复制 {len(self.text)} 字', 5000 if plain_fallback else 3500)

    def menu(self, position):
        self.sync()
        if not self.text or not self.active():
            return
        menu = QMenu(self.view)
        copy = menu.addAction('复制文字\tCtrl+C')
        copy.triggered.connect(self.copy)
        clear = menu.addAction('取消选择')
        clear.triggered.connect(self.clear)
        menu.exec(self.view.viewport().mapToGlobal(position))
