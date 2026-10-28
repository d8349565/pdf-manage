"""Reader sidebar overlay and reversible full-screen presentation."""
from PyQt6.QtCore import QEvent, QObject, QPoint, QPropertyAnimation, QEasingCurve, QTimer, Qt
from PyQt6.QtGui import QAction, QCursor, QKeySequence
from PyQt6.QtWidgets import QApplication, QStyle, QToolButton, QWidget

from app.themes import set_theme_style


class ImmersiveReading(QObject):
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.panel = host.left_panel
        self.surface = host.preview_stack
        self.expanded = False
        self.fullscreen = False
        self._document = None
        self._saved_chrome = []
        self._split_sizes = host.reader_split.sizes()
        self.overlay = QWidget(self.surface)
        self.overlay.hide()
        self.edge = QToolButton(self.surface)
        self.edge.setText('›')
        self.edge.setAccessibleName('展开页面与大纲侧栏')
        self.edge.setToolTip('页面与大纲侧栏 (F8)')
        self.edge.setFixedSize(18, 64)
        set_theme_style(self.edge, 'QToolButton { min-width: 0; min-height: 0; background: #EDF6F1; color: #0F6557; border: 1px solid #D5E5DD; border-radius: 5px; padding: 0; }')
        self.edge.clicked.connect(lambda: self.reveal(keyboard=True))
        self.edge.installEventFilter(self)
        self.auto_action = QAction('侧栏自动隐藏', host)
        self.auto_action.setCheckable(True)
        self.auto_action.setChecked(True)
        self.auto_action.setToolTip('触碰左侧边缘展开；取消勾选可固定侧栏')
        self.auto_action.toggled.connect(self._change_auto)
        self.pin = QToolButton(self.panel)
        self.pin.setDefaultAction(self.auto_action)
        self.panel.layout().insertWidget(1, self.pin)
        self.fullscreen_action = QAction('全屏阅读', host)
        self.fullscreen_action.setCheckable(True)
        self.fullscreen_action.setShortcut(QKeySequence('F11'))
        self.fullscreen_action.toggled.connect(self.set_fullscreen)
        host.addAction(self.fullscreen_action)
        self.exit_button = QToolButton(self.surface)
        self.exit_button.setText('退出全屏 (F11)')
        self.exit_button.setAccessibleName('退出全屏阅读')
        self.exit_button.clicked.connect(lambda: self.fullscreen_action.setChecked(False))
        self.exit_button.hide()
        self.animation = QPropertyAnimation(self.panel, b'pos', self)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.animation.finished.connect(self._finished)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.setInterval(650)
        self.hide_timer.timeout.connect(self._hide_if_away)
        self.surface.installEventFilter(self)
        self.panel.installEventFilter(self)
        host.installEventFilter(self)
        host.stack.currentChanged.connect(self.sync)
        host.act_pages.toggled.connect(self.request_visible)
        self._change_auto(True)

    def _change_auto(self, enabled):
        self.animation.stop()
        self.hide_timer.stop()
        self.expanded = False
        self.overlay.hide()
        if enabled:
            self._split_sizes = self.host.reader_split.sizes()
            self.panel.setParent(self.overlay)
            self._position()
        else:
            self.host.reader_split.insertWidget(0, self.panel)
            self.host.reader_split.setSizes(self._split_sizes)
            self.host.act_pages.setChecked(True)
        self.sync()

    def _available(self):
        return bool(self.host.ws.pages) and self.host.stack.currentIndex() == 0

    def sync(self, *_):
        available = self._available()
        document = (id(self.host.ws.pages), self.host.ws.path)
        if document != self._document or not available:
            self.collapse(animate=False)
            self._document = document
        auto = self.auto_action.isChecked()
        self.edge.setVisible(available and auto)
        if not auto:
            self.panel.setVisible(available and self.host.act_pages.isChecked())
        self.exit_button.setVisible(self.fullscreen)
        if self.fullscreen:
            for widget, _ in self._saved_chrome:
                widget.hide()
        self._position()

    def request_visible(self, on):
        if self.auto_action.isChecked():
            self.reveal(keyboard=True) if on else self.collapse()
        else:
            self.sync()

    def _position(self):
        self.edge.move(0, max(0, (self.surface.height() - self.edge.height()) // 2))
        self.edge.raise_()
        self.exit_button.adjustSize()
        self.exit_button.move(max(0, self.surface.width() - self.exit_button.width() - 12), 8)
        self.exit_button.raise_()
        if self.auto_action.isChecked():
            width = min(300, max(180, self.surface.width() - 48))
            changed = self.overlay.width() != width or self.overlay.height() != self.surface.height()
            self.overlay.setGeometry(0, 0, width, self.surface.height())
            self.panel.resize(self.overlay.size())
            if changed or self.animation.state() != QPropertyAnimation.State.Running:
                self.animation.stop()
                self.panel.move(0 if self.expanded else -width, 0)

    def _move(self, animate):
        self.animation.stop()
        target = QPoint(0 if self.expanded else -self.overlay.width(), 0)
        duration = QApplication.style().styleHint(QStyle.StyleHint.SH_Widget_Animation_Duration)
        # Respect the Windows system animation preference as well as Qt's style.
        if duration > 0:
            import sys
            if sys.platform == 'win32':
                import ctypes
                enabled = ctypes.c_int(1)
                if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0) and not enabled.value:
                    duration = 0
        if animate and duration > 0 and self.panel.pos() != target:
            distance = abs(self.panel.x() - target.x()) / max(1, self.overlay.width())
            self.animation.setDuration(max(1, int(min(220, duration) * distance)))
            self.animation.setStartValue(self.panel.pos())
            self.animation.setEndValue(target)
            self.animation.start()
        else:
            self.panel.move(target)
            self._finished()

    def reveal(self, *, keyboard=False):
        if not self._available():
            return
        if not self.auto_action.isChecked():
            self.host.act_pages.setChecked(True)
            return
        self.hide_timer.stop()
        if not self.expanded:
            self.expanded = True
            self._position()
            # A hidden panel starts outside its clipping container.
            if self.overlay.isHidden():
                self.panel.move(-self.overlay.width(), 0)
            self.overlay.show()
            self.panel.show()
            self.overlay.raise_()
            self._move(True)
        outline = getattr(self.host, 'outline_panel', None)
        if outline and self.host.sidebar_mode.currentIndex() == 1:
            outline.refresh()
            outline.sync_position(force=True)
        if keyboard:
            (outline.tree if outline and self.host.sidebar_mode.currentIndex() == 1
             else self.host.sidebar_mode).setFocus()
        self.hide_timer.start()

    def collapse(self, *, animate=True):
        self.hide_timer.stop()
        self.expanded = False
        if self.auto_action.isChecked():
            self._move(animate)

    def _finished(self):
        if not self.expanded:
            self.overlay.hide()

    def _hide_if_away(self):
        if not self.expanded:
            return
        focus = QApplication.focusWidget()
        point = QCursor.pos()
        interacting = (QApplication.activePopupWidget() or QApplication.activeModalWidget()
                       or QApplication.mouseButtons()
                       or focus is self.panel or (focus and self.panel.isAncestorOf(focus))
                       or self.panel.rect().contains(self.panel.mapFromGlobal(point))
                       or self.edge.rect().contains(self.edge.mapFromGlobal(point)))
        if interacting:
            self.hide_timer.start()
        else:
            self.collapse()

    def set_fullscreen(self, enabled):
        if enabled == self.fullscreen:
            return
        host = self.host
        self.fullscreen = enabled
        self.collapse(animate=False)
        if enabled:
            self._window_state = host.windowState()
            self._geometry = host.saveGeometry()
            self._normal_rect = host.geometry()
            self._mode = host.stack.currentIndex()
            self._auto = self.auto_action.isChecked()
            self._pages_visible = host.act_pages.isChecked()
            widgets = (host.menuWidget(), host.workbench_bar, host.tab_bar, host.statusBar(), host.tools_panel)
            self._saved_chrome = [(widget, not widget.isHidden()) for widget in widgets]
            host.stack.setCurrentIndex(0)
            self.auto_action.setChecked(True)
            for widget, _ in self._saved_chrome:
                widget.hide()
            host.showFullScreen()
        else:
            host.setWindowState(self._window_state)
            host.restoreGeometry(self._geometry)
            if not self._window_state & Qt.WindowState.WindowMaximized:
                host.setGeometry(self._normal_rect)
            for widget, visible in self._saved_chrome:
                widget.setVisible(visible)
            self.auto_action.setChecked(self._auto)
            host.act_pages.setChecked(self._pages_visible)
            host.stack.setCurrentIndex(self._mode)
            host._sync_tab_bar()
            self._saved_chrome = []
        self.fullscreen_action.setChecked(enabled)
        self.sync()

    def eventFilter(self, watched, event):
        kind = event.type()
        if watched is self.edge and kind == QEvent.Type.Enter:
            self.reveal()
        elif watched is self.panel and kind == QEvent.Type.Leave:
            self.hide_timer.start()
        elif watched is self.surface and kind == QEvent.Type.Resize:
            self._position()
        elif watched is self.surface and kind == QEvent.Type.Hide:
            self.collapse(animate=False)
        elif (watched is self.host and kind == QEvent.Type.WindowDeactivate
              and not QApplication.activePopupWidget() and not QApplication.activeModalWidget()):
            self.collapse(animate=False)
        return False

    def shutdown(self):
        self.animation.stop()
        self.hide_timer.stop()
