"""Page-local text entry with controls anchored to the box."""
import math
import pymupdf as p
from PyQt6.QtCore import QEvent, QObject, QRectF, QSize, QTimer, Qt
from PyQt6.QtGui import QColor, QFont, QGuiApplication
from PyQt6.QtWidgets import (QApplication, QColorDialog, QComboBox, QFontComboBox,
                            QFrame, QHBoxLayout, QLabel, QTextEdit, QToolButton,
                            QVBoxLayout, QWidget)
from app.icons import toolbar_icon


class InlineTextBox(QObject):
    def __init__(self, tools, area, index, ref, text="", settings=None, identifier=None):
        super().__init__(tools)
        settings = settings or {}
        self.tools, self.view = tools, tools.view
        self.index, self.ref, self.identifier = index, ref, identifier
        self.matrix = p.Matrix(tools.snapshot["matrix"])
        self.base_area = self.area = self.original_area = p.Rect(area)
        self.closed = self.committing = self.positioning = self.preedit = False
        self._resize_state = None
        self.frame = QFrame(self.view.viewport())
        self.frame.setObjectName("pageTextBox")
        self.frame.setStyleSheet("QFrame#pageTextBox { background: white; border: 1px dashed #0F766E; border-radius: 0; }")
        layout = QVBoxLayout(self.frame)
        layout.setContentsMargins(2, 2, 2, 2)
        self.editor = QTextEdit(self.frame)
        self.editor.setFrameShape(QFrame.Shape.NoFrame)
        self.editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.editor.setAcceptRichText(False)
        self.editor.setPlainText(text)
        self.editor.setPlaceholderText("输入文字…")
        self.editor.setAccessibleName("页面文本框内容")
        self.editor.document().setDocumentMargin(2)
        layout.addWidget(self.editor)
        self.handles = {}
        cursors = dict(nw=Qt.CursorShape.SizeFDiagCursor, n=Qt.CursorShape.SizeVerCursor,
                       ne=Qt.CursorShape.SizeBDiagCursor, e=Qt.CursorShape.SizeHorCursor,
                       se=Qt.CursorShape.SizeFDiagCursor, s=Qt.CursorShape.SizeVerCursor,
                       sw=Qt.CursorShape.SizeBDiagCursor, w=Qt.CursorShape.SizeHorCursor)
        for edge, cursor in cursors.items():
            handle = QFrame(self.frame)
            handle.setObjectName('textBoxResizeHandle')
            handle.setStyleSheet('QFrame#textBoxResizeHandle { background: #0F766E; border: 1px solid white; border-radius: 2px; }')
            handle.setFixedSize(10, 10)
            handle.setCursor(cursor)
            handle.setToolTip('拖动调整文本框宽高；文字放不下时高度会自动增长')
            handle.setAccessibleName('调整文本框尺寸')
            self.handles[edge] = handle
        self.toolbar = QFrame(self.view.viewport())
        self.toolbar.setObjectName("textBoxControls")
        self.toolbar.setStyleSheet("""
            QFrame#textBoxControls { background: #FFFFFF; border: 1px solid #CBD5E1; border-radius: 6px; }
            QToolButton { min-height: 26px; min-width: 24px; padding: 2px 5px; border: none; border-radius: 4px; font-size: 12px; color: #334155; }
            QToolButton:hover { background: #E2EEEC; }
            QToolButton#applyText { background: #0F766E; }
            QComboBox { min-height: 24px; max-height: 26px; padding: 1px 5px; border: 1px solid #CBD5E1; border-radius: 4px; background: white; font-size: 12px; color: #334155; }
            QComboBox:focus { border-color: #0F766E; }
            QLabel { border: none; color: #B91C1C; font-size: 12px; }
        """)
        controls = QVBoxLayout(self.toolbar)
        controls.setContentsMargins(4, 4, 4, 4)
        controls.setSpacing(3)
        row = QHBoxLayout()
        row.setSpacing(3)
        self.font = QFontComboBox()
        self.font.setEditable(False)
        self.font.setFixedWidth(140)
        self.font.setCurrentFont(QFont(settings.get("font", "Microsoft YaHei")))
        self.font.setAccessibleName("文本框字体")
        row.addWidget(self.font)
        self.size = QComboBox()
        self.size.setEditable(False)
        current_size = float(settings.get("fontsize", 12))
        sizes = {6, 7, 8, 9, 10, 10.5, 11, 12, 14, 16, 18, 20, 22, 24, 26, 28,
                 32, 36, 40, 44, 48, 54, 60, 66, 72, 80, 88, 96, 110, 120, 144, current_size}
        for size in sorted(sizes):
            self.size.addItem(f"{size:g} pt", float(size))
        self.size.setCurrentIndex(self.size.findData(current_size))
        self.size.setFixedWidth(80)
        self.size.setToolTip("选择字号")
        self.size.setAccessibleName("文本框字号")
        row.addWidget(self.size)
        self.color = QColor.fromRgbF(*settings.get("color", (0, 0, 0)))
        self.color_button = self.button("颜色", "circle", row)
        self.color_button.clicked.connect(self.choose_color)
        apply_button = self.button("", "check", row)
        apply_button.setObjectName("applyText")
        apply_button.setIcon(toolbar_icon("check", "#FFFFFF", 16))
        apply_button.setToolTip("完成文本框（Ctrl+Enter）")
        apply_button.setAccessibleName("完成文本框")
        apply_button.clicked.connect(self.apply)
        cancel_button = self.button("", "x", row)
        cancel_button.setToolTip("取消本次输入（Esc）")
        cancel_button.setAccessibleName("取消文本框输入")
        cancel_button.clicked.connect(self.close)
        controls.addLayout(row)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setMaximumWidth(300)
        self.error.hide()
        controls.addWidget(self.error)
        self.original_text, self.original_settings = text, self.settings()
        self.font.currentFontChanged.connect(self.style_changed)
        self.size.currentIndexChanged.connect(self.style_changed)
        self.editor.textChanged.connect(self.changed)
        self.editor.document().documentLayout().documentSizeChanged.connect(self.position)
        self.view.view_changed.connect(self.position)
        QApplication.instance().installEventFilter(self)
        self.style_changed()
        self.position()
        self.editor.setFocus()

    def button(self, text, icon, layout):
        button = QToolButton(self.toolbar)
        button.setText(text)
        button.setIcon(toolbar_icon(icon, "#334155", 16))
        button.setIconSize(QSize(16, 16))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setAccessibleName(text)
        layout.addWidget(button)
        return button

    def settings(self):
        return dict(font=self.font.currentFont().family(), fontsize=self.size.currentData(),
                    color=(self.color.redF(), self.color.greenF(), self.color.blueF()))

    def choose_color(self):
        color = QColorDialog.getColor(self.color, self.frame, "文本颜色")
        if color.isValid():
            self.color = color
            self.style_changed()
        self.editor.setFocus()

    def style_changed(self, *_):
        self.color_button.setIcon(toolbar_icon("circle", self.color.name(), 16))
        self.font.setToolTip(f"选择字体（当前：{self.font.currentFont().family()}）")
        self.error.hide()
        self.position()

    def changed(self):
        self.error.hide()
        QTimer.singleShot(0, self.position)

    def position(self, *_):
        if self.closed or self.positioning or self.index >= len(self.view.page_rects):
            return
        self.positioning = True
        try:
            visible = self.base_area * self.matrix
            page = self.view.page_rects[self.index]
            scene = QRectF(page.x() + visible.x0, page.y() + visible.y0, visible.width, visible.height)
            rect = self.view.mapFromScene(scene).boundingRect()
            viewport = self.view.viewport()
            active = self.area * self.matrix
            active_scene = QRectF(page.x() + active.x0, page.y() + active.y0, active.width, active.height)
            if not self.view.isVisible() or not viewport.rect().intersects(self.view.mapFromScene(active_scene).boundingRect()):
                self.frame.hide()
                self.toolbar.hide()
                return
            scale = max(.01, self.view.transform().m11())
            pixels = max(1, round(self.size.currentData() * math.hypot(self.matrix.a, self.matrix.b) * scale))
            font = self.font.currentFont()
            font.setPixelSize(pixels)
            if self.editor.font() != font:
                self.editor.setFont(font)
            if self.editor.document().defaultFont() != font:
                self.editor.document().setDefaultFont(font)
            family = font.family().replace("\\", "\\\\").replace('"', '\\"')
            style = f'QTextEdit {{ background: white; color: {self.color.name()}; border: none; padding: 0; font-family: "{family}"; font-size: {pixels}px; }}'
            if self.editor.styleSheet() != style:
                self.editor.setStyleSheet(style)
            self.frame.setGeometry(rect)
            self.frame.layout().activate()
            wanted = max(visible.height, (self.editor.document().size().height() + 8) / scale)
            visible.y1 = min(page.height(), visible.y0 + wanted)
            self.area = visible * ~self.matrix
            scene.setHeight(visible.height)
            rect = self.view.mapFromScene(scene).boundingRect()
            self.frame.setGeometry(rect)
            self._place_handles()
            self.frame.show()
            self.frame.raise_()
            self.toolbar.adjustSize()
            width, height = self.toolbar.width(), self.toolbar.height()
            x = max(2, min(rect.right() - width + 1, viewport.width() - width - 2))
            y = rect.bottom() + 5
            if y + height > viewport.height() - 2:
                y = max(2, rect.top() - height - 5)
            self.toolbar.move(x, y)
            self.toolbar.show()
            self.toolbar.raise_()
        finally:
            self.positioning = False

    def _place_handles(self):
        width, height = self.frame.width() - 10, self.frame.height() - 10
        for edge, handle in self.handles.items():
            x = width / 2 if edge in ('n', 's') else width if 'e' in edge else 0
            y = height / 2 if edge in ('e', 'w') else height if 's' in edge else 0
            handle.move(round(x), round(y))
            handle.raise_()

    def _resize_drag(self, event):
        edge, start, original, _ = self._resize_state
        scale = max(.01, self.view.transform().m11())
        dx = (event.globalPosition().x() - start.x()) / scale
        dy = (event.globalPosition().y() - start.y()) / scale
        rect = p.Rect(original)
        page = self.view.page_rects[self.index]
        min_width = 16
        min_height = max(16, (self.editor.document().size().height() + 8) / scale)
        if 'w' in edge:
            rect.x0 = max(0, min(rect.x0 + dx, rect.x1 - min_width))
        if 'e' in edge:
            rect.x1 = min(page.width(), max(rect.x1 + dx, rect.x0 + min_width))
        if 'n' in edge:
            rect.y0 = max(0, min(rect.y0 + dy, rect.y1 - min_height))
        if 's' in edge:
            rect.y1 = min(page.height(), max(rect.y1 + dy, rect.y0 + min_height))
        self.base_area = rect * ~self.matrix
        self.position()

    def show_error(self, text):
        self.error.setText(text)
        self.error.show()
        self.position()
        self.editor.setFocus()

    def apply(self):
        if self.closed:
            return True
        if self.committing:
            return False
        QGuiApplication.inputMethod().commit()
        self.position()
        text = self.editor.toPlainText()
        if not text.strip():
            if self.identifier is None:
                self.close()
                return True
            self.show_error("文字不能为空；删除文本框请使用批注删除按钮。")
            return False
        if len(text) > 20000:
            self.show_error("文本框最多输入 20000 个字符。")
            return False
        if (self.identifier and text == self.original_text and self.settings() == self.original_settings
                and all(abs(a - b) < .01 for a, b in zip(self.base_area, self.original_area))):
            self.close()
            return True
        self.committing = True
        try:
            if self.tools.apply_text_box(self, text, self.settings()):
                self.close()
                return True
            return False
        except Exception as exc:
            self.show_error(str(exc))
            return False
        finally:
            self.committing = False

    def close(self, *_):
        if self.closed:
            return
        self.closed = True
        if self._resize_state:
            for handle in self.handles.values():
                handle.releaseMouse()
            self._resize_state = None
        QApplication.instance().removeEventFilter(self)
        self.view.view_changed.disconnect(self.position)
        self.frame.hide()
        self.toolbar.hide()
        self.font.hidePopup()
        self.size.hidePopup()
        self.frame.deleteLater()
        self.toolbar.deleteLater()
        self.tools.text_box_closed(self)
        self.deleteLater()

    def eventFilter(self, watched, event):
        if self.closed or self.committing:
            return False
        kind = event.type()
        if kind == QEvent.Type.InputMethod and watched in (self.editor, self.editor.viewport()):
            self.preedit = bool(event.preeditString())
            return False
        if QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None:
            return False
        if watched in self.handles.values():
            if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                edge = next(name for name, handle in self.handles.items() if handle is watched)
                self._resize_state = (edge, event.globalPosition(),
                                      self.area * self.matrix, p.Rect(self.base_area))
                watched.grabMouse()
                return True
            if self._resize_state and kind == QEvent.Type.MouseMove:
                self._resize_drag(event)
                return True
            if self._resize_state and kind == QEvent.Type.MouseButtonRelease:
                self._resize_drag(event)
                self._resize_state = None
                watched.releaseMouse()
                self.editor.setFocus()
                return True
        if kind == QEvent.Type.ShortcutOverride:
            if ((event.key() == Qt.Key.Key_Escape and not self.preedit)
                    or (event.modifiers() & Qt.KeyboardModifier.ControlModifier
                        and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_S))):
                event.accept()
                return True
        if kind == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape and not self.preedit:
                if self._resize_state:
                    self.base_area = self._resize_state[3]
                    self._resize_state = None
                    for handle in self.handles.values():
                        handle.releaseMouse()
                    self.position()
                    return True
                self.close()
                return True
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    self.apply()
                    return True
                if event.key() == Qt.Key.Key_S:
                    if self.apply():
                        self.tools.host.save()
                    return True
        if kind == QEvent.Type.Resize and watched is self.view.viewport():
            QTimer.singleShot(0, self.position)
        if kind == QEvent.Type.MouseButtonPress and isinstance(watched, QWidget):
            if (watched is self.frame or self.frame.isAncestorOf(watched)
                    or watched is self.toolbar or self.toolbar.isAncestorOf(watched)):
                return False
            return not self.apply()
        return False
