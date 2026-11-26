"""Page-local text entry with controls anchored to the box."""
from app.i18n import tr
from app.themes import set_theme_style
import math
import pymupdf as p
from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, QRectF, QSize, QTimer, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QGuiApplication
from PyQt6.QtWidgets import (QAbstractButton, QApplication, QColorDialog, QComboBox, QFontComboBox,
                            QFrame, QGraphicsView, QHBoxLayout, QLabel, QTextEdit, QToolButton,
                            QVBoxLayout, QWidget)
from app.arrowstyleui import add_ink_swatches, ink_icon, update_ink_swatches
from app.icons import toolbar_icon


class InlineTextBox(QObject):
    initial_width = 144
    initial_height = 36

    def __init__(self, tools, area, index, ref, text="", settings=None, identifier=None):
        super().__init__(tools)
        settings = settings or {}
        self.tools, self.view = tools, tools.view
        self.index, self.ref, self.identifier = index, ref, identifier
        self.group = settings.get("group") if isinstance(settings.get("group"), str) else None
        self.matrix = p.Matrix(tools.snapshot["matrix"])
        self.base_area = self.area = self.original_area = p.Rect(area)
        self.manual_size = bool(settings.get('manual_size'))
        self._measure_doc = None
        self._measure_key = None
        self.closed = self.committing = self.positioning = self.preedit = self._queued = False
        self._resize_state = None
        self._move_state = None
        self._saved_update_mode = None
        self.frame = QFrame(self.view.viewport())
        self.frame.setObjectName("pageTextBox")
        self.frame.setStyleSheet("QFrame#pageTextBox { background: white; border: 1px dashed #0F766E; border-radius: 0; }")
        layout = QVBoxLayout(self.frame)
        layout.setContentsMargins(1, 1, 1, 1)
        self.editor = QTextEdit(self.frame)
        self.editor.setFrameShape(QFrame.Shape.NoFrame)
        self.editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.editor.setAcceptRichText(False)
        self.editor.setPlainText(text)
        self.editor.setPlaceholderText(tr('输入文字…'))
        self.editor.setAccessibleName(tr('页面文本框内容'))
        self.editor.document().setDocumentMargin(0)
        layout.addWidget(self.editor)
        self.borders = {}
        for side in ('top', 'bottom', 'left', 'right'):
            border = QWidget(self.view.viewport())
            border.setObjectName('textBoxMoveBorder')
            border.setCursor(Qt.CursorShape.SizeAllCursor)
            border.setToolTip(tr('按住拖动移动文本框位置'))
            border.setAccessibleName(tr('移动文本框'))
            self.borders[side] = border
        self.handles = {}
        cursors = dict(nw=Qt.CursorShape.SizeFDiagCursor, n=Qt.CursorShape.SizeVerCursor,
                       ne=Qt.CursorShape.SizeBDiagCursor, e=Qt.CursorShape.SizeHorCursor,
                       se=Qt.CursorShape.SizeFDiagCursor, s=Qt.CursorShape.SizeVerCursor,
                       sw=Qt.CursorShape.SizeBDiagCursor, w=Qt.CursorShape.SizeHorCursor)
        for edge, cursor in cursors.items():
            handle = QFrame(self.view.viewport())
            handle.setObjectName('textBoxResizeHandle')
            set_theme_style(handle, 'QFrame#textBoxResizeHandle { background: #0F766E; border: 1px solid white; border-radius: 2px; }')
            handle.setFixedSize(10, 10)
            handle.setCursor(cursor)
            handle.setToolTip(tr('拖动调整文本框宽高；文字放不下时高度会自动增长'))
            handle.setAccessibleName(tr('调整文本框尺寸'))
            self.handles[edge] = handle
        self.toolbar = QFrame(self.view.viewport())
        self.toolbar.setObjectName("textBoxControls")
        set_theme_style(self.toolbar, """
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
        self.move_button = self.button("", "move", row)
        self.move_button.setObjectName("moveText")
        self.move_button.setCursor(Qt.CursorShape.SizeAllCursor)
        self.move_button.setToolTip(tr('按住拖动移动文本框位置'))
        self.move_button.setAccessibleName(tr('移动文本框'))
        self._move_targets = (*self.borders.values(), self.move_button)
        self.font = QFontComboBox()
        self.font.setEditable(False)
        self.font.setFixedWidth(140)
        self.font.setCurrentFont(QFont(settings.get("font", "Microsoft YaHei")))
        self.font.setAccessibleName(tr('文本框字体'))
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
        self.size.setToolTip(tr('选择字号'))
        self.size.setAccessibleName(tr('文本框字号'))
        row.addWidget(self.size)
        self.color = QColor.fromRgbF(*settings.get("color", (0, 0, 0)))
        self.color_button = self.button(tr('颜色'), "circle", row)
        self.color_button.setToolTip(tr('悬停选择常用颜色，点击自定义颜色'))
        self.color_button.clicked.connect(self.choose_color)
        self.color_palette = QFrame(self.view.viewport())
        self.color_palette.setObjectName('textColorPalette')
        self.color_palette.setAttribute(Qt.WidgetAttribute.WA_NoMousePropagation)
        set_theme_style(self.color_palette, '''
            QFrame#textColorPalette { background: #FFFFFF; border: 1px solid #CBD5E1; border-radius: 6px; }
            QToolButton { min-width: 0; min-height: 0; padding: 0; border-radius: 4px; }
            QToolButton:hover { background: #E2EEEC; }
        ''')
        palette_row = QHBoxLayout(self.color_palette)
        palette_row.setContentsMargins(4, 4, 4, 4)
        palette_row.setSpacing(2)
        self.color_swatches = add_ink_swatches(palette_row, self.set_color)
        self.color_palette.adjustSize()
        self.color_palette.hide()
        self.palette_hide_timer = QTimer(self)
        self.palette_hide_timer.setSingleShot(True)
        self.palette_hide_timer.setInterval(200)
        self.palette_hide_timer.timeout.connect(self.color_palette.hide)
        self.scope = QComboBox()
        self.scope.setEditable(False)
        for label, value in ((tr('仅当前页'), 'current'), (tr('全部页面'), 'all'), (tr('当前页及之后'), 'from_current')):
            self.scope.addItem(label, value)
        self.scope.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.scope.setToolTip(tr('选择将此文本框插入哪些页面'))
        self.scope.setAccessibleName(tr('文本框插入范围'))
        self.scope.activated.connect(self._defer_focus_editor)
        row.addWidget(self.scope)
        if len(tools.host.ws.pages) <= 1:
            self.scope.hide()
        apply_button = self.button("", "check", row)
        apply_button.setObjectName("applyText")
        apply_button.setIcon(toolbar_icon("check", "#FFFFFF", 16))
        self.tools.host.shortcut_manager.watch_hint('textbox.apply', apply_button, '完成文本框')
        apply_button.setAccessibleName(tr('完成文本框'))
        apply_button.clicked.connect(self._defer_apply)
        cancel_button = self.button("", "x", row)
        cancel_button.setToolTip(tr('取消本次输入（Esc）'))
        cancel_button.setAccessibleName(tr('取消文本框输入'))
        cancel_button.clicked.connect(self._defer_close)
        controls.addLayout(row)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setMaximumWidth(300)
        self.error.hide()
        controls.addWidget(self.error)
        self.original_text, self.original_settings = text, self.settings()
        self.layout_timer = QTimer(self)
        self.layout_timer.setSingleShot(True)
        self.layout_timer.setInterval(16)
        self.layout_timer.timeout.connect(self.position)
        self.font.currentFontChanged.connect(self.style_changed)
        self.size.currentIndexChanged.connect(self.style_changed)
        self.editor.textChanged.connect(self.changed)
        self.editor.document().documentLayout().documentSizeChanged.connect(self.schedule_position)
        self.view.view_changed.connect(self.position)
        QApplication.instance().installEventFilter(self)
        self.style_changed()
        self.editor.setFocus()

    def button(self, text, icon, layout):
        button = QToolButton(self.toolbar)
        button.setText(text)
        button.setIcon(toolbar_icon(icon, "#334155", 16))
        button.setIconSize(QSize(16, 16))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setAccessibleName(text)
        layout.addWidget(button)
        return button

    def _defer_focus_editor(self, *_):
        QTimer.singleShot(0, lambda: None if (self.closed or self.committing) else self.editor.setFocus())

    def _defer_apply(self, *_):
        if self.closed or self.committing or self._queued:
            return
        self._queued = True
        QTimer.singleShot(0, self._run_deferred_apply)

    def _run_deferred_apply(self):
        self._queued = False
        if not self.closed and not self.committing:
            self.apply()

    def _defer_close(self, *_):
        if self.closed or self.committing:
            return
        self._queued = True
        QTimer.singleShot(0, self.close)

    def scope_mode(self) -> str:
        if self.scope.isHidden():
            return 'current'
        return self.scope.currentData() or 'current'

    def settings(self):
        result = dict(font=self.font.currentFont().family(), fontsize=self.size.currentData(), manual_size=self.manual_size,
                      color=(self.color.redF(), self.color.greenF(), self.color.blueF()))
        if self.group:
            result["group"] = self.group
        return result

    def choose_color(self):
        self.color_palette.hide()
        self.set_color(QColorDialog.getColor(self.color, self.frame, tr('文字颜色')))

    def set_color(self, color):
        self.color_palette.hide()
        if color.isValid():
            self.color = color
            self.style_changed()
        self.editor.setFocus()

    def position_color_palette(self):
        viewport = self.view.viewport()
        trigger = self.color_button.geometry().translated(self.toolbar.pos())
        width, height = self.color_palette.width(), self.color_palette.height()
        x = max(0, min(trigger.center().x() - width // 2, viewport.width() - width))
        y = trigger.bottom() + 1
        if trigger.top() >= height and (self.toolbar.y() < self.frame.y() or y + height > viewport.height()):
            y = trigger.top() - height
        self.color_palette.move(x, y)
        self.color_palette.raise_()

    def style_changed(self, *_):
        self.color_button.setIcon(ink_icon(self.color, self.color_button.devicePixelRatioF(), size=16))
        update_ink_swatches(self.color_swatches, self.color)
        self.font.setToolTip(tr('选择字体（当前：{p0}）', p0=f'{self.font.currentFont().family()}'))
        self.error.hide()
        self.position()

    def changed(self):
        self.error.hide()
        self.schedule_position()

    def schedule_position(self, *_):
        if not (self.closed or self.committing or self.positioning or self._move_state or self.layout_timer.isActive()):
            self.layout_timer.start()

    def fitted_size(self, visible, page, font, scale, *, wait=False):
        """Measure with the same font and line breaker as PDF output, in page points."""
        from app.pdfedits import replacement_font
        text = self.editor.toPlainText()
        if not text and not self.manual_size and self.identifier is None:
            metrics = QFontMetricsF(font, self.editor)
            width = max(self.initial_width, visible.width, (metrics.horizontalAdvance(self.editor.placeholderText()) + 20) / scale)
            height = max(self.initial_height, visible.height, (metrics.height() + 12) / scale)
            return min(width, page.width()), min(height, page.height())
        if not text.strip() or len(text) > 20000:
            return visible.width, visible.height
        if (self.identifier and text == self.original_text and self.settings() == self.original_settings
                and abs(self.base_area.width - self.original_area.width) < .01
                and abs(self.base_area.height - self.original_area.height) < .01):
            return visible.width, visible.height
        family, size = self.font.currentFont().family(), self.size.currentData()
        key = (text, family, size, self.manual_size, tuple(visible), page.width(), page.height())
        if key == self._measure_key:
            return self._measured_size
        glyphs = set(text) - {'\r', '\n', '\t'}
        lock = self.tools.host.store._native_lock
        if not lock.acquire(blocking=wait):
            # Painting/thumbnail work holds the same lock. Keep accepting input and
            # measure the latest text on a later frame. Apply always requests an exact fit.
            self.layout_timer.start()
            return getattr(self, '_measured_size', (visible.width, visible.height))
        try:
            if (self._measure_doc is None or family != self._measure_family
                    or self._font_name == 'ZhiXuCJK' and not self._measure_chars.issubset(glyphs)
                    or any(not self._pdf_font.has_glyph(ord(c)) for c in glyphs - self._measure_chars)):
                if self._measure_doc is not None:
                    self._measure_doc.close()
                self._measure_doc = p.open()
                self._measure_page = self._measure_doc.new_page()
                self._measure_family = None
                self._pdf_font, self._font_name, buffer = replacement_font(self._measure_page, glyphs, family)
                self._measure_page.insert_font(fontname=self._font_name, fontbuffer=buffer)
                self._measure_family, self._measure_chars = family, set()
            self._measure_chars.update(glyphs)
            available = page.height() - visible.y0
            if self.manual_size:
                width = visible.width
            else:
                longest = max(self._pdf_font.text_length(line.expandtabs(1), fontsize=size) for line in text.splitlines())
                width = min(page.width() - visible.x0, max(24, longest + 8))
            spare = self._measure_page.new_shape().insert_textbox(
                p.Rect(0, 0, width, available), text, fontname=self._font_name, fontsize=size)
            too_wide = any(self._pdf_font.glyph_advance(ord(c)) * size > width + .001 for c in glyphs)
        finally:
            lock.release()
        warning = tr('文字超出页面，请减小字号、增大框宽或减少内容。')
        if spare < -.001 or too_wide:
            self.error.setText(warning)
            self.error.show()
        elif self.error.text() == warning:
            self.error.hide()
        height = max(size * 1.6, available - spare + 2, visible.height if self.manual_size else 0)
        self._measure_key = key
        self._measured_size = width, min(height, available)
        return self._measured_size

    def position(self, *_, wait=False):
        if self.closed or self.committing or self.positioning or self.index >= len(self.view.page_rects):
            return
        self.positioning = True
        try:
            self.layout_timer.stop()
            visible = self.base_area * self.matrix
            page = self.view.page_rects[self.index]
            scale = max(.01, self.view.transform().m11())
            pixels = max(1, round(self.size.currentData() * math.hypot(self.matrix.a, self.matrix.b) * scale))
            font = self.font.currentFont()
            font.setPixelSize(pixels)
            try:
                width, height = self.fitted_size(visible, page, font, scale, wait=wait)
                if self.editor.document().isEmpty() and not self.manual_size and not self.identifier:
                    dx = min(0, page.width() - visible.x0 - width)
                    dy = min(0, page.height() - visible.y0 - height)
                    if dx or dy:
                        visible += (dx, dy, dx, dy)
                        self.base_area = visible * ~self.matrix
                visible.x1, visible.y1 = visible.x0 + width, visible.y0 + height
            except (ValueError, RuntimeError) as exc:
                self.error.setText(str(exc))
                self.error.show()
            scene = QRectF(page.x() + visible.x0, page.y() + visible.y0, visible.width, visible.height)
            rect = self.view.mapFromScene(scene).boundingRect()
            viewport = self.view.viewport()
            self.area = visible * ~self.matrix
            if not self.view.isVisible() or not viewport.rect().intersects(rect):
                self.frame.hide()
                for border in self.borders.values():
                    border.hide()
                for handle in self.handles.values():
                    handle.hide()
                self.toolbar.hide()
                self.color_palette.hide()
                return
            if self.editor.font() != font:
                self.editor.setFont(font)
            if self.editor.document().defaultFont() != font:
                self.editor.document().setDefaultFont(font)
            family = font.family().replace("\\", "\\\\").replace('"', '\\"')
            style = f'QTextEdit {{ background: white; color: {self.color.name()}; border: none; padding: 0; font-family: "{family}"; font-size: {pixels}px; }}'
            if self.editor.styleSheet() != style:
                self.editor.setStyleSheet(style)
            self.frame.setGeometry(rect)
            self.frame.show()
            self.frame.raise_()
            self._place_handles()
            self.toolbar.adjustSize()
            self._position_toolbar(rect)
            self.toolbar.show()
            self.toolbar.raise_()
            if self.color_palette.isVisible():
                self.position_color_palette()
        finally:
            self.positioning = False

    def _position_toolbar(self, rect):
        viewport = self.view.viewport()
        width, height = self.toolbar.width(), self.toolbar.height()
        x = max(2, min(rect.right() - width + 1, viewport.width() - width - 2))
        y = rect.bottom() + 12
        if y + height > viewport.height() - 2:
            y = max(2, rect.top() - height - 12)
        self.toolbar.move(x, y)

    def _place_handles(self):
        # Keep the grips outside the text, even for a box containing one character.
        rect = self.frame.geometry()
        pad = 6
        self.borders['top'].setGeometry(rect.left(), rect.top() - pad, rect.width(), pad + 1)
        self.borders['bottom'].setGeometry(rect.left(), rect.bottom(), rect.width(), pad + 1)
        self.borders['left'].setGeometry(rect.left() - pad, rect.top(), pad + 1, rect.height())
        self.borders['right'].setGeometry(rect.right(), rect.top(), pad + 1, rect.height())
        for border in self.borders.values():
            border.show()
            border.raise_()
        for edge, handle in self.handles.items():
            x = rect.center().x() - 5 if edge in ('n', 's') else rect.right() if 'e' in edge else rect.left() - 9
            y = rect.center().y() - 5 if edge in ('e', 'w') else rect.bottom() if 's' in edge else rect.top() - 9
            handle.move(x, y)
            handle.show()
            handle.raise_()

    def _begin_drag_update_mode(self):
        if self._saved_update_mode is None and hasattr(self.view, 'viewportUpdateMode'):
            self._saved_update_mode = self.view.viewportUpdateMode()
            self.view.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate)

    def _end_drag_update_mode(self):
        if self._saved_update_mode is not None and hasattr(self.view, 'setViewportUpdateMode'):
            self.view.setViewportUpdateMode(self._saved_update_mode)
            self._saved_update_mode = None

    def _move_drag(self, event):
        start, original, _ = self._move_state
        scale = max(.01, self.view.transform().m11())
        dx = (event.globalPosition().x() - start.x()) / scale
        dy = (event.globalPosition().y() - start.y()) / scale
        page = self.view.page_rects[self.index]
        nx0 = max(0.0, min(original.x0 + dx, max(0.0, page.width() - original.width)))
        ny0 = max(0.0, min(original.y0 + dy, max(0.0, page.height() - original.height)))
        rect = p.Rect(nx0, ny0, nx0 + original.width, ny0 + original.height)
        self.base_area = self.area = rect * ~self.matrix
        scene = QRectF(page.x() + nx0, page.y() + ny0, rect.width, rect.height)
        top_left = self.view.mapFromScene(scene).boundingRect().topLeft()
        delta = top_left - self.frame.pos()
        if not delta.isNull():
            self.frame.move(top_left)
            for widget in (*self.borders.values(), *self.handles.values()):
                widget.move(widget.pos() + delta)
            self._position_toolbar(self.frame.geometry())

    def _resize_drag(self, event):
        edge, start, original, _, _ = self._resize_state
        scale = max(.01, self.view.transform().m11())
        dx = (event.globalPosition().x() - start.x()) / scale
        dy = (event.globalPosition().y() - start.y()) / scale
        rect = p.Rect(original)
        page = self.view.page_rects[self.index]
        min_width = 16
        min_height = max(16, self.size.currentData() * 1.6)
        if 'w' in edge:
            rect.x0 = max(0, min(rect.x0 + dx, rect.x1 - min_width))
        if 'e' in edge:
            rect.x1 = min(page.width(), max(rect.x1 + dx, rect.x0 + min_width))
        if 'n' in edge:
            rect.y0 = max(0, min(rect.y0 + dy, rect.y1 - min_height))
        if 's' in edge:
            rect.y1 = min(page.height(), max(rect.y1 + dy, rect.y0 + min_height))
        self.base_area = rect * ~self.matrix
        self.manual_size = True
        self.position()

    def show_error(self, text):
        self.error.setText(tr(text))
        self.error.show()
        self.position()
        self.editor.setFocus()

    def apply(self):
        if self.closed:
            return True
        if self.committing:
            return False
        if self.preedit or self.editor.hasFocus():
            QGuiApplication.inputMethod().commit()
        self.position(wait=True)
        text = self.editor.toPlainText()
        if not text.strip():
            if self.identifier is None:
                self.close()
                return True
            self.show_error(tr('文字不能为空。要删除，请先取消编辑，再单击文本框并点右上角的删除按钮。'))
            return False
        if len(text) > 20000:
            self.show_error(tr('文本框最多输入 20000 个字符。'))
            return False
        if (self.identifier and self.scope_mode() == 'current'
                and text == self.original_text and self.settings() == self.original_settings
                and all(abs(a - b) < .01 for a, b in zip(self.base_area, self.original_area))):
            self.close()
            return True
        self.committing = True
        committed = False
        try:
            committed = bool(self.tools.apply_text_box(self, text, self.settings()))
        except Exception as exc:
            self.show_error(str(exc))
            return False
        finally:
            self.committing = False
        if committed:
            self.close()
            return True
        return False

    def close(self, *_):
        if self.closed:
            return
        self.closed = True
        self._queued = False
        self.palette_hide_timer.stop()
        self.layout_timer.stop()
        QApplication.instance().removeEventFilter(self)
        for signal, slot in (
            (self.view.view_changed, self.position),
            (self.editor.textChanged, self.changed),
            (self.editor.document().documentLayout().documentSizeChanged, self.schedule_position),
            (self.font.currentFontChanged, self.style_changed),
            (self.size.currentIndexChanged, self.style_changed),
        ):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        if self._measure_doc is not None:
            with self.tools.host.store._native_lock:
                self._measure_page = self._pdf_font = None
                self._measure_doc.close()
                self._measure_doc = None
        if self._resize_state:
            for handle in self.handles.values():
                handle.releaseMouse()
            self._resize_state = None
        if self._move_state:
            for target in self._move_targets:
                target.releaseMouse()
            self._move_state = None
        self._end_drag_update_mode()
        self.frame.hide()
        for border in self.borders.values():
            border.hide()
            border.deleteLater()
        for handle in self.handles.values():
            handle.hide()
            handle.deleteLater()
        self.toolbar.hide()
        self.color_palette.hide()
        self.color_palette.deleteLater()
        for combo in (self.font, self.size, self.scope):
            if combo.view().isVisible():
                combo.hidePopup()
        self.frame.deleteLater()
        self.toolbar.deleteLater()
        self.tools.text_box_closed(self)
        self.deleteLater()

    def eventFilter(self, watched, event):
        if self.closed or self.committing:
            return False
        kind = event.type()
        if self._queued and kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                                     QEvent.Type.MouseButtonDblClick, QEvent.Type.KeyPress):
            return True
        if kind == QEvent.Type.InputMethod and watched in (self.editor, self.editor.viewport()):
            self.preedit = bool(event.preeditString())
            return False
        if QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None:
            return False
        if watched is self.color_button or watched is self.color_palette:
            if kind == QEvent.Type.Enter:
                self.palette_hide_timer.stop()
                if watched is self.color_button:
                    self.position_color_palette()
                    self.color_palette.show()
            elif kind == QEvent.Type.Leave:
                self.palette_hide_timer.start()
        if watched in self._move_targets:
            if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self.color_palette.hide()
                self.layout_timer.stop()
                self._move_state = (event.globalPosition(), self.area * self.matrix, p.Rect(self.base_area))
                self._begin_drag_update_mode()
                watched.grabMouse()
                return True
            if self._move_state and kind == QEvent.Type.MouseMove:
                self._move_drag(event)
                return True
            if self._move_state and kind == QEvent.Type.MouseButtonRelease:
                self._move_drag(event)
                self._move_state = None
                watched.releaseMouse()
                self._end_drag_update_mode()
                self.position()
                self.editor.setFocus()
                return True
        if watched in self.handles.values():
            if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                edge = next(name for name, handle in self.handles.items() if handle is watched)
                self._resize_state = (edge, event.globalPosition(),
                                      self.area * self.matrix, p.Rect(self.base_area), self.manual_size)
                self._begin_drag_update_mode()
                watched.grabMouse()
                return True
            if self._resize_state and kind == QEvent.Type.MouseMove:
                self._resize_drag(event)
                return True
            if self._resize_state and kind == QEvent.Type.MouseButtonRelease:
                self._resize_drag(event)
                self._resize_state = None
                watched.releaseMouse()
                self._end_drag_update_mode()
                self.editor.setFocus()
                return True
        if kind in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress) and event.key() != Qt.Key.Key_Escape:
            from app.shortcuts import keep_native_text_input
            if keep_native_text_input(event):
                return False
        if kind == QEvent.Type.ShortcutOverride:
            shortcuts = self.tools.host.shortcut_manager
            if ((event.key() == Qt.Key.Key_Escape and not self.preedit)
                    or any(shortcuts.matches(key, event) for key in
                           ('textbox.apply', 'file.save', 'file.save_as'))):
                event.accept()
                return True
        if kind == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape and not self.preedit:
                if self.color_palette.isVisible():
                    self.color_palette.hide()
                    self.editor.setFocus()
                    return True
                if self._move_state:
                    self.base_area = self._move_state[2]
                    self._move_state = None
                    for target in self._move_targets:
                        target.releaseMouse()
                    self._end_drag_update_mode()
                    self.position()
                    return True
                if self._resize_state:
                    self.base_area = self._resize_state[3]
                    self.manual_size = self._resize_state[4]
                    self._resize_state = None
                    for handle in self.handles.values():
                        handle.releaseMouse()
                    self._end_drag_update_mode()
                    self.position()
                    return True
                self.close()
                return True
            shortcuts = self.tools.host.shortcut_manager
            if shortcuts.matches('textbox.apply', event):
                self.apply()
                return True
            for key, save_as in (('file.save', False), ('file.save_as', True)):
                if shortcuts.matches(key, event):
                    if self.apply():
                        self.tools.host.save(save_as)
                    return True
        if kind == QEvent.Type.Resize and watched is self.view.viewport():
            self.schedule_position()
        if kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease) and isinstance(watched, QWidget):
            if watched is self.color_palette or self.color_palette.isAncestorOf(watched):
                return False
            self.color_palette.hide()
            if (watched is self.frame or self.frame.isAncestorOf(watched)
                    or watched is self.toolbar or self.toolbar.isAncestorOf(watched)):
                return False
            if watched is self.view.viewport():
                if kind == QEvent.Type.MouseButtonPress:
                    self._defer_apply()
                return True
            if isinstance(watched, QAbstractButton) and event.button() == Qt.MouseButton.LeftButton:
                # Deliver the native press/release pair. Commit before the release
                # triggers the action, since committing can run a nested event loop.
                if kind == QEvent.Type.MouseButtonPress:
                    return False
                applied = self.apply()
                if sip.isdeleted(watched):
                    return True
                if not applied:
                    watched.setDown(False)
                    return True
            elif kind == QEvent.Type.MouseButtonPress:
                return not self.apply() or sip.isdeleted(watched)
        return False
