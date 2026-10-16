"""Read-only rectangle selection for visible and invisible PDF text layers."""
from PyQt6.QtCore import QEvent, QObject, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QKeySequence, QPainterPath, QPen, QShortcut
from PyQt6.QtWidgets import QApplication, QGraphicsPathItem, QMenu


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
        self.overlay = None
        self.shortcut = QShortcut(QKeySequence.StandardKey.Copy, self.view)
        self.shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.shortcut.activated.connect(self.copy)
        self.shortcut.setEnabled(False)
        self.view.viewport().installEventFilter(self)
        self.view.installEventFilter(self)
        self.view.viewport().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.viewport().customContextMenuRequested.connect(self.menu)
        self.view.setToolTip('拖动框选文字；Ctrl+C 复制；按住空格拖动页面（也可 Alt+拖动）')

    def clear(self):
        if self.overlay is not None:
            self.view.scene().removeItem(self.overlay)
        self.overlay = None
        self.text, self.start = '', None
        self.snapshot, self.page_index, self.key = None, None, None
        self.shortcut.setEnabled(False)

    def sync(self):
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
            return False
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape and (self.text or self.start):
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
            try:
                snapshot = self.host.store.text_snapshot(ref)
            except Exception:
                self.host.statusBar().showMessage('当前页文字暂时无法读取，请重试。', 5000)
                return False
            if not any(block.get('lines') for block in snapshot['raw'].get('blocks', ())):
                return False
            self.snapshot, self.page_index, self.key = snapshot, index, ref.render_key
            self.generation = self.view.layout_generation
            self.start = point
            self.view.setFocus(Qt.FocusReason.MouseFocusReason)
            return True
        if self.start is not None and event.type() in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease):
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() != Qt.MouseButton.LeftButton:
                return False
            self.select(self.view.mapToScene(event.position().toPoint()))
            if event.type() == QEvent.Type.MouseButtonRelease:
                self.start = None
                if self.text:
                    self.host.statusBar().showMessage(f'已选取 {len(self.text)} 字 · Ctrl+C 或右键复制', 5000)
            return True
        return False

    def select(self, point):
        import pymupdf as p
        from app.pdfedits import selected_lines, selection_quads
        bounds = self.view.page_rects[self.page_index]
        point = QPointF(max(bounds.left(), min(bounds.right(), point.x())),
                        max(bounds.top(), min(bounds.bottom(), point.y())))
        rectangle = QRectF(self.start, point).normalized().translated(-bounds.topLeft())
        matrix = p.Matrix(self.snapshot['matrix'])
        canonical = p.Rect(rectangle.left(), rectangle.top(), rectangle.right(), rectangle.bottom()) * ~matrix
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
        QApplication.clipboard().setText(self.text)
        self.host.statusBar().showMessage(f'已复制 {len(self.text)} 字', 3500)

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
