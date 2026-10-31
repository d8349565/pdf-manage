"""Reader sidebar overlay and reversible full-screen presentation."""
from PyQt6.QtCore import QEvent, QObject, QPoint, QPropertyAnimation, QEasingCurve, QSize, QTimer, Qt
from PyQt6.QtGui import QAction, QCursor, QKeySequence, QRegion
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QStyle, QToolButton, QWidget

from app.icons import toolbar_icon
from app.themes import set_theme_style


class ImmersiveReading(QObject):
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self._active = False
        self.panel = host.left_panel
        self.surface = host.preview_stack
        self.expanded = False
        self.fullscreen = False
        self.borderless = False
        self._borderless_chrome = []
        self._keyboard_reveal = False
        self._syncing_visibility = False
        self._document = None
        self._saved_chrome = []
        self._split_sizes = host.reader_split.sizes()
        self.overlay = QWidget(self.surface)
        self.overlay.hide()
        self.edge = QToolButton(self.surface)
        self.edge.setObjectName('readerSidebarHandle')
        self.edge.setAccessibleName('展开页面与大纲侧栏')
        self.edge.setToolTip('页面与大纲侧栏 (F8)')
        self.edge.setFixedSize(28, 42)
        self.edge.setIconSize(QSize(16, 16))
        self.edge.setCursor(Qt.CursorShape.PointingHandCursor)
        set_theme_style(self.edge, '''
            QToolButton#readerSidebarHandle {
                min-width: 0; min-height: 0; padding: 0;
                background: #FFFFFF; color: #134E4A;
                border: 1px solid #D7E3DF; border-left: none;
                border-top-left-radius: 0; border-bottom-left-radius: 0;
                border-top-right-radius: 8px; border-bottom-right-radius: 8px;
            }
            QToolButton#readerSidebarHandle:hover { background: #F0FDFA; border-color: #99B2AC; }
            QToolButton#readerSidebarHandle:pressed { background: #CCFBF1; }
            QToolButton#readerSidebarHandle:focus { border-color: #0F766E; }
        ''')
        self.edge.clicked.connect(lambda: self._toggle())
        self.auto_action = QAction('侧栏自动隐藏', host)
        self.auto_action.setCheckable(True)
        self.auto_action.setChecked(True)
        self.auto_action.setToolTip('触碰左侧边缘展开；取消勾选可固定侧栏')
        self.auto_action.toggled.connect(self._change_auto)
        header = QWidget(self.panel)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)
        self.panel.layout().removeWidget(host.sidebar_mode)
        host.sidebar_mode.setMinimumWidth(0)
        header_layout.addWidget(host.sidebar_mode, 1)
        self.pin = QToolButton(header)
        self.pin.setObjectName('readerSidebarPin')
        self.pin.setText('固定')
        self.pin.setCheckable(True)
        self.pin.setFixedSize(44, 28)
        self.pin.setAccessibleName('固定页面与大纲侧栏')
        self.pin.setCursor(Qt.CursorShape.PointingHandCursor)
        set_theme_style(self.pin, '''
            QToolButton#readerSidebarPin {
                min-width: 0; min-height: 0; padding: 0 4px; border-radius: 6px;
                background: transparent; color: #4D6B67; border: 1px solid transparent;
            }
            QToolButton#readerSidebarPin:hover { background: #F0FDFA; border-color: #D7E3DF; }
            QToolButton#readerSidebarPin:checked { background: #CCFBF1; color: #0F6557; border-color: #99B2AC; }
            QToolButton#readerSidebarPin:focus { border-color: #0F766E; }
        ''')
        self.pin.toggled.connect(lambda fixed: self.auto_action.setChecked(not fixed))
        header_layout.addWidget(self.pin)
        self.panel.layout().insertWidget(0, header)
        self.fullscreen_action = QAction('全屏阅读', host)
        self.fullscreen_action.setCheckable(True)
        self.fullscreen_action.setShortcut(QKeySequence('F11'))
        self.fullscreen_action.toggled.connect(self.set_fullscreen)
        host.addAction(self.fullscreen_action)
        self.borderless_action = QAction('无边框模式', host)
        self.borderless_action.setCheckable(True)
        self.borderless_action.setShortcut(QKeySequence('Ctrl+F11'))
        self.borderless_action.setToolTip('只显示页面，页面外背景跟随纸面颜色；按 Esc 退出')
        self.borderless_action.toggled.connect(self.set_borderless)
        host.addAction(self.borderless_action)
        self.exit_button = QToolButton(self.surface)
        self.exit_button.setText('退出全屏 (F11)')
        self.exit_button.setAccessibleName('退出全屏阅读')
        self.exit_button.clicked.connect(lambda: self.fullscreen_action.setChecked(False))
        self.exit_button.hide()
        self.animation = QPropertyAnimation(self.panel, b'pos', self)
        self.animation.valueChanged.connect(self._sync_edge)
        self.animation.finished.connect(self._finished)
        self.reveal_timer = QTimer(self)
        self.reveal_timer.setSingleShot(True)
        self.reveal_timer.setInterval(80)
        self.reveal_timer.timeout.connect(self.reveal)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.setInterval(450)
        self.hide_timer.timeout.connect(self._hide_if_away)
        self._active = True
        self.edge.installEventFilter(self)
        self.surface.installEventFilter(self)
        self.panel.installEventFilter(self)
        host.installEventFilter(self)
        host.stack.currentChanged.connect(self.sync)
        host.act_pages.toggled.connect(self.request_visible)
        QApplication.instance().installEventFilter(self)
        self._change_auto(True)

    def _change_auto(self, enabled):
        self.animation.stop()
        self.reveal_timer.stop()
        self.hide_timer.stop()
        self.expanded = False
        self._keyboard_reveal = False
        self.overlay.hide()
        self.pin.blockSignals(True)
        self.pin.setChecked(not enabled)
        self.pin.blockSignals(False)
        self.pin.setToolTip('取消固定，回到正文后自动收起' if not enabled else '固定侧栏，阅读时保持展开')
        if enabled:
            self._split_sizes = self.host.reader_split.sizes()
            self.panel.setParent(self.overlay)
            self._position()
        else:
            self.host.reader_split.insertWidget(0, self.panel)
            sizes = self._split_sizes
            if not sizes or sizes[0] < 40:
                sizes = [self.overlay.width(), max(200, self.host.reader_split.width() - self.overlay.width())]
            self.host.reader_split.setSizes(sizes)
            self.host.act_pages.setChecked(True)
        self.sync()
        self._sync_pages_action()
        self.host._thumb_timer.start(0)

    def _available(self):
        return bool(self.host.ws.pages) and self.host.stack.currentIndex() == 0

    def sync(self, *_):
        if not self._active or self._syncing_visibility:
            return
        available = self._available()
        self.borderless_action.setEnabled(bool(self.host.ws.pages))
        if self.borderless:
            if not self.host.ws.pages:
                self.set_borderless(False)
                return
            self.overlay.hide()
            self.panel.hide()
            self.edge.hide()
            self.exit_button.hide()
            for widget, _ in self._borderless_chrome:
                widget.hide()
            return
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
        self._edge_icon()

    def request_visible(self, on):
        if self._syncing_visibility or self.borderless:
            return
        if self.auto_action.isChecked():
            self.reveal(keyboard=True) if on else self.collapse()
        else:
            self.sync()

    def _sync_pages_action(self):
        if not self.auto_action.isChecked() or self.host.act_pages.isChecked() == self.expanded:
            return
        self._syncing_visibility = True
        try:
            self.host.act_pages.setChecked(self.expanded)
        finally:
            self._syncing_visibility = False

    def _position(self):
        self.exit_button.adjustSize()
        self.exit_button.move(max(0, self.surface.width() - self.exit_button.width() - 12), 8)
        self.exit_button.raise_()
        if self.auto_action.isChecked():
            width = min(300, max(180, self.surface.width() - 48))
            changed = self.overlay.width() != width or self.overlay.height() != self.surface.height()
            running = self.animation.state() == QPropertyAnimation.State.Running
            progress = 1 + self.panel.x() / max(1, self.overlay.width())
            self.overlay.setGeometry(0, 0, width, self.surface.height())
            self.panel.resize(self.overlay.size())
            if changed and running:
                self.animation.stop()
                self.panel.move(round((progress - 1) * width), 0)
                self._move(True)
            elif not running:
                self.panel.move(0 if self.expanded else -width, 0)
        self._sync_edge()

    def _sync_edge(self, *_):
        x = max(0, self.panel.x() + self.panel.width()) if self.auto_action.isChecked() and self.overlay.isVisible() else 0
        self.edge.move(x, max(0, (self.surface.height() - self.edge.height()) // 2))
        self.edge.raise_()
        if self.auto_action.isChecked():
            visible = self.panel.geometry().intersected(self.overlay.rect())
            mask = QRegion(visible) if not visible.isEmpty() else QRegion(0, 0, 1, max(1, self.overlay.height()))
            self.overlay.setMask(mask)

    def _edge_icon(self):
        self.edge.setIcon(toolbar_icon('chevron-left' if self.expanded else 'panel-left', size=16))
        self.edge.setAccessibleName('收起页面与大纲侧栏' if self.expanded else '展开页面与大纲侧栏')
        self.edge.setToolTip('收起侧栏 (F8)' if self.expanded else '页面与大纲侧栏 (F8)')

    def _toggle(self, *, keyboard=False):
        self.collapse() if self.expanded else self.reveal(keyboard=keyboard)

    def _animation_duration(self):
        duration = QApplication.style().styleHint(QStyle.StyleHint.SH_Widget_Animation_Duration)
        # Respect the Windows system animation preference as well as Qt's style.
        if duration > 0:
            import sys
            if sys.platform == 'win32':
                import ctypes
                enabled = ctypes.c_int(1)
                if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0) and not enabled.value:
                    duration = 0
        return duration

    def _move(self, animate):
        self.animation.stop()
        target = QPoint(0 if self.expanded else -self.overlay.width(), 0)
        duration = self._animation_duration()
        self._edge_icon()
        if animate and duration > 0 and self.panel.pos() != target:
            distance = abs(self.panel.x() - target.x()) / max(1, self.overlay.width())
            self.animation.setEasingCurve(QEasingCurve.Type.OutQuart if self.expanded else QEasingCurve.Type.OutCubic)
            self.animation.setDuration(max(1, int(min(220 if self.expanded else 150, duration) * distance)))
            self.animation.setStartValue(self.panel.pos())
            self.animation.setEndValue(target)
            self.animation.start()
        else:
            self.panel.move(target)
            self._sync_edge()
            self._finished()

    def reveal(self, *, keyboard=False):
        if not self._active or not self._available() or self.borderless:
            return
        if not self.auto_action.isChecked():
            self.host.act_pages.setChecked(True)
            return
        self.hide_timer.stop()
        self.reveal_timer.stop()
        QApplication.instance().installEventFilter(self)
        self._keyboard_reveal |= keyboard
        if not self.expanded:
            self._position()
            self.expanded = True
            # A hidden panel starts outside its clipping container.
            if self.overlay.isHidden():
                self.panel.move(-self.overlay.width(), 0)
            self.overlay.show()
            self.panel.show()
            self.overlay.raise_()
            self.panel.layout().activate()
            self.host.sidebar_stack.layout().activate()
            self.host._fit_thumbs()
            self._move(True)
            self._sync_edge()
            self._sync_pages_action()
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
        self.reveal_timer.stop()
        if not self.expanded and animate:
            return
        self.expanded = False
        self._keyboard_reveal = False
        self._sync_pages_action()
        if self.auto_action.isChecked():
            self._move(animate)

    def _finished(self):
        if not self.expanded:
            self.overlay.hide()
        self._sync_edge()

    def _hide_if_away(self):
        if not self.expanded:
            return
        focus = QApplication.focusWidget()
        point = QCursor.pos()
        interacting = (QApplication.activePopupWidget() or QApplication.activeModalWidget()
                       or QApplication.mouseButtons()
                       or (self._keyboard_reveal and (focus is self.panel
                           or (focus and self.panel.isAncestorOf(focus))))
                       or self.panel.rect().contains(self.panel.mapFromGlobal(point))
                       or self.edge.rect().contains(self.edge.mapFromGlobal(point)))
        if interacting:
            self.hide_timer.start()
        else:
            self.collapse()

    def set_borderless(self, enabled, *, fit_width=True):
        if enabled == self.borderless:
            self.borderless_action.setChecked(enabled)
            return
        host = self.host
        if enabled and (not host.ws.pages or host._saving or getattr(host, '_task_dialog', None)):
            self.borderless_action.setChecked(False)
            return
        if enabled:
            tools = getattr(host, 'annotation_tools', None)
            if tools is not None and tools.deactivate() is False:
                self.borderless_action.setChecked(False)
                return
            self._borderless_mode = host.stack.currentIndex()
            self._borderless_pages = host.act_pages.isChecked()
            self._borderless_split = host.reader_split.sizes()
            widgets = (host.menuWidget(), host.workbench_bar, host.tab_bar, host.statusBar(),
                       host.tools_panel, host.navigation_bar, host.banner, host.search_bar)
            self._borderless_chrome = [(widget, not widget.isHidden()) for widget in widgets]
        self.borderless = enabled
        if enabled:
            self.collapse(animate=False)
        host.preview_view.set_borderless(enabled)
        host.setProperty('borderlessReader', enabled)
        frame = host.reader_frame
        frame.setProperty('borderlessReader', enabled)
        # Reapply inherited QSS so the reader frame also drops its cached border margins.
        host.setStyleSheet(host.styleSheet())
        if enabled:
            host.stack.setCurrentIndex(0)
        else:
            for widget, visible in self._borderless_chrome:
                widget.setVisible(visible)
            self._borderless_chrome = []
            host.act_pages.setChecked(self._borderless_pages)
            host.stack.setCurrentIndex(self._borderless_mode)
            host.reader_split.setSizes(self._borderless_split)
            host._sync_tab_bar()
        self.borderless_action.setChecked(enabled)
        self.sync()
        if enabled and fit_width:
            host._set_zoom_mode('width')
        host.reading_orb.sync()
        (host.arrange_view if host._in_arrange() else host.preview_view).setFocus()

    def set_fullscreen(self, enabled):
        if enabled == self.fullscreen:
            return
        borderless = self.borderless
        if borderless:
            self.set_borderless(False)
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
        if enabled:
            host._set_zoom_mode('width')
        if borderless:
            # Returning from F11 restores the existing borderless session, including manual zoom.
            self.set_borderless(True, fit_width=False)

    def eventFilter(self, watched, event):
        if not self._active:
            return False
        kind = event.type()
        if (watched is self.edge and kind == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter)):
            if not event.isAutoRepeat():
                self._toggle(keyboard=True)
            return True
        if self.auto_action.isChecked():
            if isinstance(watched, QWidget) and (watched is self.panel or self.panel.isAncestorOf(watched)):
                if kind == QEvent.Type.KeyPress:
                    self._keyboard_reveal = True
                elif kind == QEvent.Type.MouseButtonPress:
                    self._keyboard_reveal = False
            if kind == QEvent.Type.MouseButtonPress:
                view = self.host.preview_view
                main_click = isinstance(watched, QWidget) and (watched is view or view.isAncestorOf(watched))
                if watched is self.host.windowHandle():
                    point = event.globalPosition().toPoint()
                    main_click = (view.rect().contains(view.mapFromGlobal(point))
                        and not self.panel.geometry().intersected(self.overlay.rect()).contains(self.overlay.mapFromGlobal(point))
                        and not self.edge.rect().contains(self.edge.mapFromGlobal(point)))
                if main_click:
                    self.collapse()
        if watched is self.edge and kind == QEvent.Type.Enter:
            if not self.expanded:
                self.reveal_timer.start()
            self.hide_timer.stop()
        elif watched is self.edge and kind == QEvent.Type.Leave:
            self.reveal_timer.stop()
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
        self._active = False
        QApplication.instance().removeEventFilter(self)
        for widget in (self.edge, self.surface, self.panel, self.host):
            widget.removeEventFilter(self)
        self.animation.stop()
        self.reveal_timer.stop()
        self.hide_timer.stop()
