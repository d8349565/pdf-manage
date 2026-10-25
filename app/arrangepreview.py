"""Temporary enlarged page preview without taking focus from page sorting."""
from PyQt6.QtCore import QEvent, QObject, QPoint, QSize, QTimer, Qt
from PyQt6.QtGui import QCursor, QPixmap
from PyQt6.QtWidgets import QApplication, QLabel, QMenu

from app.rendering import RenderSignals


class ArrangePreview(QObject):
    def __init__(self, host, view):
        super().__init__(host)
        self.host, self.view = host, view
        self.key = None
        self.pinned = False
        self.alt = False
        self.popup = QLabel(host, Qt.WindowType.ToolTip | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.popup.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.popup.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.popup.setStyleSheet("background: white; border: 1px solid #94A3B8; padding: 6px;")
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(140)
        self.timer.timeout.connect(self.show_hover)
        self.signals = RenderSignals()
        self.signals.done.connect(self.rendered)
        view.setMouseTracking(True)
        view.viewport().setMouseTracking(True)
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        view.customContextMenuRequested.connect(self.context_menu)
        QApplication.instance().installEventFilter(self)
        view.verticalScrollBar().valueChanged.connect(self.hide)
        view.horizontalScrollBar().valueChanged.connect(self.hide)

    def hide(self, *_):
        self.timer.stop()
        self.key = None
        self.pinned = False
        self.popup.hide()
        self.host.rendering.prune()

    def show_hover(self):
        if self.alt and self.view.isVisible() and not QApplication.mouseButtons():
            point = self.view.viewport().mapFromGlobal(QCursor.pos())
            self.show(self.view.indexAt(point))

    def show(self, index, pinned=False):
        if not index.isValid() or not 0 <= index.row() < len(self.host.ws.pages):
            self.hide()
            return
        ref = self.host.ws.pages[index.row()]
        key = (ref.render_key, index.row(), pinned)
        if self.key == key:
            return
        self.key, self.pinned = key, pinned
        screen = self.view.screen().availableGeometry()
        width, height = self.host.store.page_size(ref)
        scale = min(640 / max(width, 1), (screen.height() - 100) / max(height, 1),
                    (screen.width() - 40) / max(width, 1))
        self.image_size = QSize(max(1, int(width * scale)), max(1, int(height * scale)))
        self.popup.setFixedSize(self.image_size + QSize(14, 14))
        self.popup.clear()
        self.popup.setText("正在生成清晰预览…")
        anchor = self.view.viewport().mapToGlobal(self.view.visualRect(index).topRight()) + QPoint(12, 0)
        x = min(max(screen.left(), anchor.x()), screen.right() - self.popup.width() + 1)
        y = min(max(screen.top(), anchor.y()), screen.bottom() - self.popup.height() + 1)
        self.popup.move(x, y)
        self.popup.show()
        generation = self.host.rendering.generation
        def stale():
            return (self.key != key or not self.view.isVisible()
                    or self.host.rendering.generation != generation
                    or index.row() >= len(self.host.ws.pages)
                    or self.host.ws.pages[index.row()] is not ref)
        pixels = min(1600, max(200, int(self.image_size.width() * self.view.devicePixelRatioF())))
        self.host.rendering.request(ref, pixels, key, self.signals, stale, 50)

    def rendered(self, key, result):
        if self.key != key:
            return
        if result.cancelled:
            self.hide()
        elif result.value is None or result.error:
            self.popup.setText("预览未完成，请重试")
        else:
            ratio = self.view.devicePixelRatioF()
            pixmap = QPixmap.fromImage(result.value).scaled(
                self.image_size * ratio, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            pixmap.setDevicePixelRatio(ratio)
            self.popup.setPixmap(pixmap)

    def context_menu(self, point):
        index = self.view.indexAt(point)
        if not index.isValid():
            index = self.view.currentIndex()
        menu = QMenu(self.view)
        action = menu.addAction("放大预览（Esc 关闭）") if index.isValid() else None
        self.view.add_page_paste_action(menu,point)
        if menu.exec(self.view.viewport().mapToGlobal(point)) is action and action is not None:
            self.show(index, pinned=True)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Type.ApplicationDeactivate:
            self.alt = False
            self.hide()
        if not self.view.isVisible() or QApplication.activeModalWidget() is not None:
            self.alt = False
            if self.key is not None:
                self.hide()
            return False
        if kind == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Alt:
                point = self.view.viewport().mapFromGlobal(QCursor.pos())
                if self.view.indexAt(point).isValid():
                    self.alt = True
                    self.timer.start()
                    return True
            elif event.key() == Qt.Key.Key_Escape and self.key is not None:
                self.hide()
                return True
        elif kind == QEvent.Type.KeyRelease and event.key() == Qt.Key.Key_Alt:
            claimed = self.alt
            self.alt = False
            if not self.pinned:
                self.hide()
            return claimed
        elif kind == QEvent.Type.MouseButtonPress:
            self.hide()
        elif watched is self.view.viewport():
            if kind == QEvent.Type.MouseMove and not self.pinned:
                if event.buttons():
                    self.hide()
                elif event.modifiers() & Qt.KeyboardModifier.AltModifier:
                    self.alt = True
                    self.timer.start()
                else:
                    self.hide()
            elif kind in (QEvent.Type.Leave, QEvent.Type.Hide) and not self.pinned:
                self.hide()
        return False
