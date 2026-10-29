"""An edge-docked, session-only shortcut to the reader's shared settings menu."""
from app.themes import set_theme_style, theme_manager, ui_color
from PyQt6.QtCore import QEvent, QPoint, QPropertyAnimation, QEasingCurve, QRect, QSize, QTimer, Qt
from PyQt6.QtGui import QAction, QColor, QCursor, QIcon, QPainter, QPen, QRegion
from PyQt6.QtWidgets import (
    QApplication, QGraphicsDropShadowEffect, QStyle, QStyleOptionToolButton, QStylePainter, QToolButton,
)

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
        self._reading_icon = toolbar_icon('reading-settings', '#0F766E', 24)
        self.setIconSize(QSize(24, 24))
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName('阅读设置')
        set_theme_style(self, '''
            QToolButton#readingSettingsOrb {
                min-width: 40px; min-height: 40px; padding: 0; border-radius: 14px;
                background: #FCFFFD; border: 1px solid #D5E5DD;
            }
            QToolButton#readingSettingsOrb:hover { background: #F0FAF5; border-color: #99C7B2; }
            QToolButton#readingSettingsOrb:pressed { background: #E2F2E9; border-color: #80B69E; }
        ''')
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(14)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(19, 78, 74, 22))
        self.setGraphicsEffect(shadow)
        self.animation = QPropertyAnimation(self, b'pos', self)
        duration = QApplication.style().styleHint(QStyle.StyleHint.SH_Widget_Animation_Duration)
        self._motion_duration = min(200, duration) if duration > 0 else 0
        self.animation.valueChanged.connect(lambda _: self.update())
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
        self.restore_action = QAction(self._reading_icon, '显示阅读悬浮球', host)
        self.restore_action.triggered.connect(self.restore)
        theme_manager().changed.connect(self._refresh_theme)
        self._refresh_theme()
        menu.addSeparator()
        menu.addAction(self.hide_action)
        self.view.setMouseTracking(True)
        self.view.installEventFilter(self)
        self.view.viewport().setMouseTracking(True)
        self.view.viewport().installEventFilter(self)
        self.hide()

    def _refresh_theme(self, *_args):
        self._reading_icon = toolbar_icon('reading-settings', '#0F766E', 24)
        self.restore_action.setIcon(self._reading_icon)
        shadow = QColor(ui_color('text'))
        shadow.setAlpha(22)
        self.graphicsEffect().setColor(shadow)
        self.update()

    def _target(self, expanded=None):
        viewport = self.view.viewport()
        expanded = self.expanded if expanded is None else expanded
        x = viewport.width() - (self.SIZE + 12 if expanded else self.PEEK)
        return viewport.pos() + QPoint(max(4, x), max(8, viewport.height() - self.SIZE - 20))

    def _move(self, animate):
        self.animation.stop()
        target = self._target()
        if animate and self.isVisible() and target != self.pos():
            curve = QEasingCurve(QEasingCurve.Type.OutBack if self.expanded else QEasingCurve.Type.InCubic)
            if self.expanded:
                curve.setOvershoot(0.8)
            self.animation.setEasingCurve(curve)
            self.animation.setDuration(self._motion_duration if self.expanded else self._motion_duration * 2 // 3)
            self.animation.setStartValue(self.pos())
            self.animation.setEndValue(target)
            self.animation.start()
        else:
            self.move(target)
            self._finish_animation()

    def _finish_animation(self):
        self.setIcon(self._reading_icon if self.expanded else QIcon())
        self.update()

    def reveal(self):
        if self.temporarily_hidden or not self.isVisible():
            return
        self.hide_timer.stop()
        if self.expanded:
            return
        self.expanded = True
        self.setIcon(self._reading_icon)
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
        self.setIcon(self._reading_icon)
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
        viewport = self.view.viewport()
        bounds = QRect(viewport.mapToGlobal(QPoint()), viewport.size()).adjusted(8, 8, -12, -12)
        bottom = min(self.view.mapToGlobal(self._target(True)).y() - 12, bounds.bottom() + 1)
        size = menu.sizeHint()
        menu.popup(QPoint(max(bounds.left(), bounds.right() + 1 - size.width()),
                          max(bounds.top(), bottom - size.height())))
        # 原生菜单显示后的尺寸可能与预估不同，按实际外框再次贴齐按钮上方。
        size = menu.frameGeometry().size()
        menu.move(max(bounds.left(), bounds.right() + 1 - size.width()),
                  max(bounds.top(), bottom - size.height()))

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
        self.host.default_reading_view_action.setEnabled(available and self.host._zoom_mode in ('page', 'width'))
        self.host.default_reading_view_action.setToolTip(
            '保存当前页面布局和适配方式，新打开的文档使用此设置'
            if self.host._zoom_mode in ('page', 'width') else '先选择“适合页面”或“适合宽度”，再设为默认')
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
        option = QStyleOptionToolButton()
        self.initStyleOption(option)
        option.icon = QIcon()
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ToolButton, option)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 图标跟随实际位置变化，收起途中再次靠近也能连续衔接。
        closed, opened = self._target(False), self._target(True)
        progress = (closed.x() - self.x()) / max(1, closed.x() - opened.x())
        opacity = max(0.0, min(1.0, progress))
        if opacity < 1:
            painter.setOpacity(1 - opacity)
            painter.setPen(QPen(QColor(ui_color('#0F766E')), 1.6, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawLine(8, 17, 5, 21)
            painter.drawLine(5, 21, 8, 25)
        if opacity > 0:
            painter.setOpacity(opacity)
            painter.translate(self.SIZE / 2, self.SIZE / 2)
            painter.rotate(-12 * (1 - progress))
            scale = (0.88 + 0.12 * progress) * (0.94 if self.isDown() else 1)
            painter.scale(scale, scale)
            self._reading_icon.paint(painter, QRect(-12, -12, 24, 24))
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
