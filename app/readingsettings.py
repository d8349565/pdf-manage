"""An edge-docked, session-only shortcut to the reader's shared settings menu."""
from PyQt6.QtCore import QEvent, QPoint, QPropertyAnimation, QEasingCurve, QSize, QTimer, Qt
from PyQt6.QtGui import QAction, QColor, QCursor, QIcon, QPainter, QPen, QRegion
from PyQt6.QtWidgets import QApplication, QGraphicsDropShadowEffect, QStyle, QToolButton

from app.icons import toolbar_icon


class ReadingSettingsOrb(QToolButton):
    SIZE = 42
    PEEK = 14
    HIDE_DELAY = 600

    def __init__(self, host, menu):
        # viewport.scroll() 会移动其子控件；悬浮入口必须与 viewport 同级。
        super().__init__(host.preview_view)
        self.host, self.settings_menu = host, menu
        self.view = host.preview_view
        self.temporarily_hidden = False
        self.expanded = False
        self.setObjectName('readingSettingsOrb')
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setIconSize(QSize(22, 22))
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName('阅读设置')
        self.setStyleSheet('''
            QToolButton#readingSettingsOrb {
                min-width: 40px; min-height: 40px; padding: 0; border-radius: 21px;
                background: #F8FFFC; border: 1px solid #A8C9BB;
            }
            QToolButton#readingSettingsOrb:hover { background: #E6F5EE; border-color: #4B9B80; }
            QToolButton#readingSettingsOrb:pressed { background: #D3EBDF; }
        ''')
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(12)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(19, 78, 74, 30))
        self.setGraphicsEffect(shadow)
        self.animation = QPropertyAnimation(self, b'pos', self)
        duration = QApplication.style().styleHint(QStyle.StyleHint.SH_Widget_Animation_Duration)
        self.animation.setDuration(min(120, duration) if duration > 0 else 0)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.finished.connect(self._finish_animation)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.setInterval(self.HIDE_DELAY)
        self.hide_timer.timeout.connect(self._hide_if_away)
        self.clicked.connect(self.open_settings)
        menu.aboutToShow.connect(self._menu_opened)
        menu.aboutToHide.connect(self._menu_closed)
        self.hide_action = QAction('本次运行隐藏悬浮球', host)
        self.hide_action.triggered.connect(self.hide_temporarily)
        self.restore_action = QAction(toolbar_icon('book-open'), '显示阅读悬浮球', host)
        self.restore_action.triggered.connect(self.restore)
        menu.addSeparator()
        menu.addAction(self.hide_action)
        self.view.setMouseTracking(True)
        self.view.installEventFilter(self)
        self.view.viewport().setMouseTracking(True)
        self.view.viewport().installEventFilter(self)
        self.hide()

    def _target(self):
        viewport = self.view.viewport()
        x = viewport.width() - (self.SIZE + 12 if self.expanded else self.PEEK)
        return viewport.pos() + QPoint(max(4, x), max(8, viewport.height() - self.SIZE - 20))

    def _move(self, animate):
        self.animation.stop()
        target = self._target()
        if animate and self.isVisible() and target != self.pos():
            self.animation.setStartValue(self.pos())
            self.animation.setEndValue(target)
            self.animation.start()
        else:
            self.move(target)
            self._finish_animation()

    def _finish_animation(self):
        self.setIcon(toolbar_icon('book-open', '#0F766E', 22) if self.expanded else QIcon())

    def reveal(self):
        if self.temporarily_hidden or not self.isVisible():
            return
        self.hide_timer.stop()
        if self.expanded:
            return
        self.expanded = True
        self.setIcon(toolbar_icon('book-open', '#0F766E', 22))
        self.raise_()
        self._move(True)

    def _near_edge(self, point):
        viewport = self.view.viewport()
        return (viewport.rect().contains(point) and point.x() >= viewport.width() - 108
                and point.y() >= viewport.height() - 132)

    def _hide_if_away(self):
        if self.settings_menu.isVisible() or not self.isVisible():
            return
        point = self.view.viewport().mapFromGlobal(QCursor.pos())
        if self._near_edge(point):
            return
        self.expanded = False
        self._move(True)

    def _menu_opened(self):
        self.hide_timer.stop()
        self.expanded = True
        self.setIcon(toolbar_icon('book-open', '#0F766E', 22))
        self.setDown(True)
        self._move(False)

    def _menu_closed(self):
        self.setDown(False)
        if self.isVisible() and not self.temporarily_hidden:
            self.hide_timer.start(self.HIDE_DELAY)

    def open_settings(self):
        self.reveal()
        menu = self.settings_menu
        menu.ensurePolished()
        size = menu.sizeHint()
        viewport = self.view.viewport()
        x = max(8, viewport.width() - size.width() - 12)
        y = max(8, self.mapTo(viewport, QPoint()).y() - size.height() - 8)
        menu.popup(viewport.mapToGlobal(QPoint(x, y)))

    def hide_temporarily(self):
        self.temporarily_hidden = True
        self.settings_menu.hide()
        self.animation.stop()
        self.hide_timer.stop()
        self.hide()
        self.sync()
        self.host.statusBar().showMessage('本次运行已隐藏悬浮球；可从“视图 → 显示阅读悬浮球”恢复。', 6000)

    def restore(self):
        self.temporarily_hidden = False
        if self.host.ws.pages:
            self.host.stack.setCurrentIndex(0)
        self.sync()
        self.reveal()
        self.hide_timer.start(self.HIDE_DELAY)

    def shutdown(self):
        self.hide()
        self.animation.stop()
        self.hide_timer.stop()
        self.view.removeEventFilter(self)
        self.view.viewport().removeEventFilter(self)

    def sync(self):
        available = bool(self.host.ws.pages)
        self.restore_action.setEnabled(available)
        self.hide_action.setEnabled(available and not self.temporarily_hidden)
        layout = self.host.reading_layout_actions[self.host._reading_layout].text()
        zoom = self.host.reading_zoom_actions[self.host._zoom_mode].text()
        hint = '\n阅读区较窄，当前暂用单页。' if self.host.reading_fallback_notice.isVisible() else ''
        self.setToolTip(f'阅读设置\n{layout} · {zoom}{hint}\nCtrl+1 单页 · Ctrl+2 双页 · Ctrl+4 四页')
        visible = available and self.parentWidget().isVisible() and not self.temporarily_hidden
        if not visible:
            self.animation.stop()
            self.hide_timer.stop()
            self.hide()
        elif self.isHidden():
            self.expanded = True
            self._move(False)
            self.show()
            self.raise_()
            self.hide_timer.start(1800)

    def enterEvent(self, event):
        self.reveal()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hide_timer.start(self.HIDE_DELAY)
        super().leaveEvent(event)

    def moveEvent(self, event):
        # 保留贴边裁切，同时让滚动条仍能接收点击与拖动。
        bounds = self.view.viewport().geometry().translated(-self.pos())
        self.setMask(QRegion(self.rect().intersected(bounds)))
        super().moveEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.expanded:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor('#0F766E'), 1.6, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawLine(8, 17, 5, 21)
            painter.drawLine(5, 21, 8, 25)
            painter.end()

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Type.Resize:
            self._move(False)
        elif kind == QEvent.Type.Show:
            QTimer.singleShot(0, self.sync)
        elif kind == QEvent.Type.Hide:
            self.animation.stop()
            self.hide_timer.stop()
            self.hide()
        elif kind == QEvent.Type.MouseMove and not event.buttons():
            point = watched.mapTo(self.view.viewport(), event.position().toPoint())
            if self._near_edge(point):
                self.reveal()
            elif self.expanded and (not self.hide_timer.isActive()
                                    or self.hide_timer.remainingTime() > self.HIDE_DELAY):
                self.hide_timer.start(self.HIDE_DELAY)
        elif kind == QEvent.Type.Leave and not self.settings_menu.isVisible():
            self.hide_timer.start(self.HIDE_DELAY)
        return False
