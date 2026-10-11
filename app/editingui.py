"""Lazily created outline and review controls for the existing reader."""
from __future__ import annotations

from dataclasses import replace
import math
import uuid

from PyQt6.QtCore import QEvent, QPointF, QRect, QRectF, QSize, QTimer, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFrame, QGraphicsDropShadowEffect, QGraphicsPathItem, QGraphicsView, QHeaderView, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMenu, QMessageBox, QPushButton, QTextEdit, QToolButton, QToolTip, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from app.icons import toolbar_icon
from app.pdfio import PdfError
from app.progress import run_operation
from app.workspace import OutlineEntry, PageEdit


class NoteViewerDialog(QDialog):
    def __init__(self, parent, content: str, can_edit: bool = True):
        super().__init__(parent)
        self.setWindowTitle("便签备注")
        self.resize(380, 260)
        self.setStyleSheet("""
            QDialog { background: #FFFFFF; }
            QLabel#noteHeader { color: #0F766E; font-size: 14px; font-weight: 600; }
            QTextEdit { border: 1.5px solid #FDE047; border-radius: 8px; padding: 10px; font-size: 13px; color: #713F12; background: #FEFCE8; }
            QPushButton { min-height: 28px; padding: 0 12px; font-size: 12px; border-radius: 6px; }
            QPushButton#saveBtn { background: #0F766E; color: #FFFFFF; font-weight: 600; border: none; }
            QPushButton#saveBtn:hover { background: #0D9488; }
            QPushButton#deleteBtn { background: #FEF2F2; color: #DC2626; border: 1px solid #FECACA; }
            QPushButton#deleteBtn:hover { background: #FEE2E2; }
            QPushButton#closeBtn { background: #F3F4F6; color: #4B5563; border: 1px solid #E5E7EB; }
            QPushButton#closeBtn:hover { background: #E5E7EB; }
        """)
        self.action = None
        self.new_text = content
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        header = QLabel("📌 便签备注内容")
        header.setObjectName("noteHeader")
        layout.addWidget(header)

        self.editor = QTextEdit(content)
        self.editor.setReadOnly(not can_edit)
        self.editor.setPlaceholderText("请输入便签内容…")
        layout.addWidget(self.editor, 1)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        if can_edit:
            self.delete_btn = QPushButton("删除便签")
            self.delete_btn.setObjectName("deleteBtn")
            self.delete_btn.clicked.connect(self._on_delete)
            btn_layout.addWidget(self.delete_btn)

            btn_layout.addStretch()

            self.save_btn = QPushButton("保存修改")
            self.save_btn.setObjectName("saveBtn")
            self.save_btn.clicked.connect(self._on_save)
            btn_layout.addWidget(self.save_btn)
        else:
            btn_layout.addStretch()

        close_btn = QPushButton("关闭")
        close_btn.setObjectName("closeBtn")
        close_btn.clicked.connect(self.reject)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

    def _on_save(self):
        self.action = "save"
        self.new_text = self.editor.toPlainText().strip()
        self.accept()

    def _on_delete(self):
        self.action = "delete"
        self.accept()


class OutlinePanel(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self._key = None
        self.entries = ()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(6)
        add = QPushButton("添加书签")
        add.setProperty("primary", True)
        add.setFixedHeight(28)
        add.clicked.connect(self.add)
        row.addWidget(add)

        more = QPushButton("管理…")
        more.setFixedHeight(28)
        more.clicked.connect(lambda: self.menu(more.mapToGlobal(more.rect().bottomLeft())))
        row.addWidget(more)
        layout.addLayout(row)

        self.tree = QTreeWidget()
        self.tree.setFrameShape(QTreeWidget.Shape.NoFrame)
        self.tree.setHeaderLabels(["目录", "页"])
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(1, 44)
        self.tree.setAccessibleName("文档大纲书签")
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(lambda pos: self.menu(self.tree.viewport().mapToGlobal(pos)))
        self.tree.itemClicked.connect(self.jump)
        layout.addWidget(self.tree, 1)

        self.feedback = QLabel("在当前阅读页添加书签。")
        self.feedback.setObjectName("hint")
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        self.refresh()

    def refresh(self):
        key = (id(self.host.ws.pages), id(self.host.ws.outline))
        if key == self._key:
            return
        self._key = key
        self.entries = self.host.ws.outline
        if self.entries is None:
            try:
                self.entries = run_operation(self.host, "读取大纲", lambda progress: self.host.store.outline_entries(self.host.ws.pages))
            except PdfError as exc:
                self.feedback.setText(str(exc))
                self.entries = ()
        targets = {ref.page_id: i + 1 for i, ref in enumerate(self.host.ws.pages)}
        self.tree.clear()
        parents = []
        for index, entry in enumerate(self.entries):
            while len(parents) >= entry.level:
                parents.pop()
            target = targets.get(entry.target_id)
            item = QTreeWidgetItem([entry.title, str(target) if target else "—"])
            item.setToolTip(0, entry.title)
            item.setData(0, Qt.ItemDataRole.UserRole, index)
            if entry.target_id is not None and target is None:
                item.setToolTip(0, "目标页已删除；撤销删除可恢复跳转。")
                item.setForeground(0, QColor("#D97706"))
            if parents:
                parents[-1].addChild(item)
            else:
                self.tree.addTopLevelItem(item)
            parents.append(item)
        self.tree.expandToDepth(1)
        self.feedback.setText("在当前阅读页添加书签。" if self.entries else "暂无大纲，可为关键页面添加书签。")

    def selected(self):
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def jump(self, item, column):
        entry = self.entries[item.data(0, Qt.ItemDataRole.UserRole)]
        for index, page in enumerate(self.host.ws.pages):
            if page.page_id == entry.target_id:
                self.host.go_to_page(index + 1)
                return
        self.feedback.setText("此书签目标已删除，可修改或撤销页面删除。")

    def commit(self, entries):
        if not self.host._allow_operation("outline"):
            return
        try:
            self.host.ws.set_outline(entries)
            self.host.refresh()
            self.refresh()
        except ValueError as exc:
            QMessageBox.information(self, "大纲未修改", str(exc))

    def add(self):
        index = self.host.ws.preview_index()
        if index is None or not self.host._allow_operation("outline"):
            return
        title, accepted = QInputDialog.getText(self, "添加书签", "书签名称", text=f"第 {index + 1} 页")
        if accepted and title.strip():
            self.commit((*self.entries, OutlineEntry(1, title.strip(), self.host.ws.pages[index].page_id)))

    def subtree_end(self, index):
        end = index + 1
        while end < len(self.entries) and self.entries[end].level > self.entries[index].level:
            end += 1
        return end

    def rename(self):
        index = self.selected()
        if index is None:
            return
        title, accepted = QInputDialog.getText(self, "重命名书签", "书签名称", text=self.entries[index].title)
        if accepted and title.strip():
            entries = list(self.entries)
            entries[index] = replace(entries[index], title=title.strip())
            self.commit(entries)

    def delete(self):
        index = self.selected()
        if index is not None:
            self.commit((*self.entries[:index], *self.entries[self.subtree_end(index):]))

    def shift(self, delta):
        index = self.selected()
        if index is None:
            return
        level = self.entries[index].level + delta
        if level < 1 or (delta > 0 and (index == 0 or level > self.entries[index - 1].level + 1)):
            self.feedback.setText("请选择可调整层级的书签。")
            return
        end = self.subtree_end(index)
        entries = [replace(entry, level=entry.level + delta) if index <= i < end else entry for i, entry in enumerate(self.entries)]
        self.commit(entries)

    def menu(self, position):
        menu = QMenu(self)
        for title, callback in (("在当前页添加书签", self.add), ("重命名", self.rename),
                                ("删除（含子项）", self.delete), ("设为子项", lambda: self.shift(1)),
                                ("提升层级", lambda: self.shift(-1))):
            action = menu.addAction(title)
            action.triggered.connect(callback)
            action.setEnabled(bool(self.host.ws.pages) and not self.host.store.operation_reason(self.host.ws.pages, "outline"))
        menu.exec(position)


class MoveHandleButton(QPushButton):
    def __init__(self, text, hover_bar):
        super().__init__(text, hover_bar)
        self.hover_bar = hover_bar
        self._drag_start_pos = None
        self._dragging = False
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setToolTip("按住拖动以移动批注，或点击后点选新位置")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.globalPosition().toPoint()
            self._dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and self._drag_start_pos:
            delta = event.globalPosition().toPoint() - self._drag_start_pos
            if not self._dragging and delta.manhattanLength() > 6:
                self._dragging = True
            if self._dragging:
                delta_move = event.globalPosition().toPoint() - self._drag_start_pos
                self._drag_start_pos = event.globalPosition().toPoint()
                self.hover_bar.move(self.hover_bar.pos() + delta_move)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._dragging:
                self._dragging = False
                self._drag_start_pos = None
                v_pt = self.hover_bar.pos() + event.position().toPoint()
                scene_pt = self.hover_bar.tools.view.mapToScene(v_pt)
                canonical_pt = self.hover_bar.tools.canonical(scene_pt)
                self.hover_bar.tools.move_annot_to(self.hover_bar.annot_id, canonical_pt)
                self.hover_bar.hide()
                return
            self._drag_start_pos = None
        super().mouseReleaseEvent(event)


class AnnotationHoverBar(QFrame):
    def __init__(self, parent_viewport, tools):
        vp = parent_viewport if isinstance(parent_viewport, QWidget) else None
        super().__init__(vp)
        self.tools = tools
        self.annot = None
        self.annot_id = None
        self.page_index = None

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

        self.setObjectName("annotHoverBar")
        self.setStyleSheet("""
            QFrame#annotHoverBar {
                background: #FFFFFF;
                border: 1px solid #CBD5E1;
                border-radius: 8px;
            }
            QLabel#hoverTitle {
                font-size: 11px;
                font-weight: 600;
                color: #854D0E;
            }
            QLabel#hoverContent {
                font-size: 12px;
                line-height: 1.4;
                color: #1F2937;
                background: #FEFCE8;
                border: 1px solid #FEF08A;
                border-radius: 6px;
                padding: 6px 8px;
            }
            QPushButton {
                min-height: 24px;
                max-height: 24px;
                font-size: 12px;
                border-radius: 5px;
                padding: 0 8px;
            }
            QPushButton#editBtn {
                background: #0F766E;
                color: #FFFFFF;
                font-weight: 600;
                border: none;
            }
            QPushButton#editBtn:hover {
                background: #0D9488;
            }
            QPushButton#moveBtn {
                background: #F3F4F6;
                color: #374151;
                border: 1px solid #D1D5DB;
            }
            QPushButton#moveBtn:hover {
                background: #E5E7EB;
            }
            QPushButton#styleBtn {
                background: #F3F4F6;
                color: #374151;
                border: 1px solid #D1D5DB;
            }
            QPushButton#styleBtn:hover {
                background: #E5E7EB;
            }
            QPushButton#deleteBtn {
                background: #FEF2F2;
                color: #DC2626;
                border: 1px solid #FECACA;
            }
            QPushButton#deleteBtn:hover {
                background: #FEE2E2;
            }
        """)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(12)
        shadow.setColor(QColor(0, 0, 0, 40))
        shadow.setOffset(0, 2)
        self.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.content_box = QWidget(self)
        c_layout = QVBoxLayout(self.content_box)
        c_layout.setContentsMargins(0, 0, 0, 0)
        c_layout.setSpacing(4)

        self.title_label = QLabel("📌 便签备注", self.content_box)
        self.title_label.setObjectName("hoverTitle")
        c_layout.addWidget(self.title_label)

        self.content_label = QLabel(self.content_box)
        self.content_label.setObjectName("hoverContent")
        self.content_label.setWordWrap(True)
        self.content_label.setMaximumWidth(280)
        c_layout.addWidget(self.content_label)

        layout.addWidget(self.content_box)

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(4)

        self.edit_btn = QPushButton("✏️ 编辑", self)
        self.edit_btn.setObjectName("editBtn")
        self.edit_btn.clicked.connect(self._on_edit)
        btn_layout.addWidget(self.edit_btn)

        self.move_btn = MoveHandleButton("⠿ 拖动", self)
        self.move_btn.setObjectName("moveBtn")
        self.move_btn.clicked.connect(self._on_move_click)
        btn_layout.addWidget(self.move_btn)

        self.style_btn = QPushButton("🎨 样式", self)
        self.style_btn.setObjectName("styleBtn")
        self.style_btn.setToolTip("应用当前选择的颜色与线宽")
        self.style_btn.clicked.connect(self._on_style)
        btn_layout.addWidget(self.style_btn)

        self.delete_btn = QPushButton("🗑️ 删除", self)
        self.delete_btn.setObjectName("deleteBtn")
        self.delete_btn.clicked.connect(self._on_delete)
        btn_layout.addWidget(self.delete_btn)

        layout.addLayout(btn_layout)
        self.hide()

    def enterEvent(self, event):
        if self._hide_timer.isActive():
            self._hide_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if not getattr(self.move_btn, "_dragging", False):
            self._hide_timer.start(250)
        super().leaveEvent(event)

    def show_for_annot(self, annot, page_index):
        import pymupdf as p
        self._hide_timer.stop()
        self.annot = annot
        self.annot_id = annot["id"]
        self.page_index = page_index

        content = (annot.get("content") or "").strip()
        kind = annot.get("kind", -1)
        names = {0: "便签", 2: "文本", 3: "线条", 4: "矩形", 5: "椭圆", 8: "高亮", 9: "下划线", 15: "画笔"}
        kind_name = names.get(kind, "批注")

        if content or kind == 0:
            self.content_box.show()
            self.title_label.setText(f"📌 {kind_name}备注")
            self.content_label.setText(content if content else "（暂无备注内容，点击编辑添加）")
            self.edit_btn.setText("✏️ 编辑")
        else:
            self.content_box.hide()
            self.edit_btn.setText("✏️ 添加备注")

        self.move_btn.setVisible(kind in (0, 4))

        self.adjustSize()
        self.layout().activate()

        matrix = p.Matrix(self.tools.snapshot["matrix"])
        local_rect = p.Rect(annot["rect"]) * matrix
        page_top = self.tools.view.page_rects[page_index].topLeft()
        scene_rect = QRectF(page_top.x() + local_rect.x0, page_top.y() + local_rect.y0,
                            max(local_rect.width, 16), max(local_rect.height, 16))
        v_tl = self.tools.view.mapFromScene(scene_rect.topLeft())
        v_br = self.tools.view.mapFromScene(scene_rect.bottomRight())
        annot_vp_rect = QRect(v_tl, v_br).normalized()

        w = self.sizeHint().width()
        h = self.sizeHint().height()
        bar_x = annot_vp_rect.center().x() - w // 2
        bar_y = annot_vp_rect.top() - h - 6
        if bar_y < 8:
            bar_y = annot_vp_rect.bottom() + 6

        vp_width = self.tools.view.viewport().width()
        vp_height = self.tools.view.viewport().height()
        bar_x = max(8, min(bar_x, vp_width - w - 8))
        bar_y = max(8, min(bar_y, vp_height - h - 8))

        self.move(bar_x, bar_y)
        self.show()
        self.raise_()

    def _on_edit(self):
        self.hide()
        self.tools.open_note_dialog(self.annot_id)

    def _on_move_click(self):
        self.hide()
        self.tools.start_move_annot(self.annot_id)

    def _on_style(self):
        self.hide()
        self.tools.apply_style_to(self.annot_id)

    def _on_delete(self):
        self.hide()
        self.tools.delete_annot(self.annot_id)


class AnnotationTools(QWidget):
    TOOL_DEFS = (
        ("browse", "mouse-pointer", "浏览／移动"),
        ("select", "select-all", "选择页面批注"),
        ("highlight", "highlighter", "文字高亮"),
        ("underline", "underline", "下划线"),
        ("ink", "pen", "自由画笔"),
        ("note", "sticky-note", "便签备注"),
        ("rect", "square", "矩形框"),
        ("ellipse", "circle", "椭圆"),
        ("arrow", "arrow-up-right", "箭头"),
        ("replace", "type", "单行文字修正"),
    )

    def __init__(self, host):
        super().__init__()
        self.host = host
        self.view = host.preview_view
        self.snapshot = None
        self.snapshot_key = None
        self.stroke = []
        self.overlay = None
        self.color = QColor("#EAB308")

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(8, 4, 8, 4)
        root_layout.setSpacing(4)

        self.bar = QFrame(self)
        self.bar.setObjectName("annotationBar")
        bar_layout = QHBoxLayout(self.bar)
        bar_layout.setContentsMargins(8, 4, 8, 4)
        bar_layout.setSpacing(6)

        # Underlying QComboBox kept for full API & test compatibility
        self.tool = QComboBox()
        self.tool.hide()
        for kind, icon_name, name in self.TOOL_DEFS:
            self.tool.addItem(name, kind)
        self.tool.setAccessibleName("审阅工具")
        self.tool.currentIndexChanged.connect(self._on_tool_combo_changed)

        # Visual tool button group with crisp SVG icons
        self.tool_group = QButtonGroup(self)
        self.tool_buttons = []
        for idx, (kind, icon_name, name) in enumerate(self.TOOL_DEFS):
            btn = QToolButton(self.bar)
            btn.setFixedSize(30, 30)
            btn.setIconSize(QSize(18, 18))
            btn.setIcon(toolbar_icon(icon_name, "#0F766E", 18))
            btn.setToolTip(name)
            btn.setCheckable(True)
            btn.setAutoRaise(False)
            btn.clicked.connect(lambda checked=False, i=idx: self.tool.setCurrentIndex(i))
            self.tool_group.addButton(btn, idx)
            self.tool_buttons.append(btn)
            bar_layout.addWidget(btn)
        self.tool_buttons[0].setChecked(True)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setStyleSheet("color: #E2EEEC; margin: 4px 2px;")
        bar_layout.addWidget(sep1)

        # Color picker swatch button
        self.color_button = QPushButton("颜色", self.bar)
        self.color_button.setToolTip("选择批注颜色")
        self.color_button.setFixedSize(54, 28)
        self.color_button.clicked.connect(self.choose_color)
        bar_layout.addWidget(self.color_button)

        self.width = QDoubleSpinBox(self.bar)
        self.width.setRange(0.1, 30.0)
        self.width.setValue(2.0)
        self.width.setSuffix(" pt")
        self.width.setAccessibleName("批注线宽")
        self.width.setFixedWidth(78)
        self.width.setFixedHeight(28)
        bar_layout.addWidget(self.width)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet("color: #E2EEEC; margin: 4px 2px;")
        sep2.hide()
        bar_layout.addWidget(sep2)

        # Annotation picker & actions (hidden to keep toolbar minimal; actions shown on hover)
        self.annotations = QComboBox(self.bar)
        self.annotations.addItem("当前页批注", None)
        self.annotations.setAccessibleName("当前页面批注")
        self.annotations.setMinimumWidth(140)
        self.annotations.setFixedHeight(30)
        self.annotations.currentIndexChanged.connect(self.sync_annotation_actions)
        self.annotations.hide()
        bar_layout.addWidget(self.annotations)

        self.annotation_actions = []
        for title, callback in (("应用样式", self.style), ("查看/改备注", self.note_text), ("删除", self.delete)):
            button = QPushButton(title, self.bar)
            button.setFixedHeight(30)
            button.clicked.connect(callback)
            button.setEnabled(False)
            button.setToolTip("先在页面或下拉框中选中批注")
            button.hide()
            self.annotation_actions.append(button)
            bar_layout.addWidget(button)

        bar_layout.addStretch()

        self.feedback = QLabel("拖选或点击操作；Ctrl+Z 撤销", self.bar)
        self.feedback.setObjectName("hint")
        bar_layout.addWidget(self.feedback)

        close = QToolButton(self.bar)
        close.setFixedSize(28, 28)
        close.setIconSize(QSize(16, 16))
        close.setIcon(toolbar_icon("x", "#4D6B67", 16))
        close.setToolTip("收起审阅工具")
        close.clicked.connect(self.deactivate)
        bar_layout.addWidget(close)

        root_layout.addWidget(self.bar)
        self._update_color_button_style()
        self.view.viewport().installEventFilter(self)
        self.hover_bar = AnnotationHoverBar(self.view.viewport(), self)
        self._moving_annot_id = None
        if hasattr(self.view, "view_changed"):
            self.view.view_changed.connect(self.hover_bar.hide)

    def _on_tool_combo_changed(self, index):
        if 0 <= index < len(self.tool_buttons):
            self.tool_buttons[index].setChecked(True)
        self.choose_tool()

    def _update_color_button_style(self):
        # High-taste color preview pill
        name = self.color.name()
        self.color_button.setStyleSheet(
            f"QPushButton {{ background: #FFFFFF; border: 2px solid {name}; border-radius: 7px; "
            f"color: #134E4A; font-weight: 600; padding: 2px 6px; }} "
            f"QPushButton:hover {{ background: {name}15; }}"
        )

    def choose_color(self):
        color = QColorDialog.getColor(self.color, self, "选择批注颜色")
        if color.isValid():
            self.color = color
            self._update_color_button_style()

    def _ensure_filter(self):
        try:
            self.view.viewport().removeEventFilter(self)
        except Exception:
            pass
        self.view.viewport().installEventFilter(self)

    def _set_view_cursor(self, cursor):
        vp = self.view.viewport() if hasattr(self.view, "viewport") else None
        if vp and hasattr(vp, "setCursor"):
            vp.setCursor(cursor)

    def deactivate(self):
        self._moving_annot_id = None
        self._set_view_cursor(Qt.CursorShape.ArrowCursor)
        if hasattr(self, "hover_bar"):
            self.hover_bar.hide()
        self._close_inline_replace()
        self.tool.setCurrentIndex(0)
        self.clear_overlay()
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        try:
            self.view.viewport().removeEventFilter(self)
        except Exception:
            pass
        self.hide()

    def closeEvent(self, event):
        self._close_inline_replace()
        try:
            self.view.viewport().removeEventFilter(self)
        except Exception:
            pass
        super().closeEvent(event)

    def choose_tool(self):
        try:
            self._moving_annot_id = None
            self._set_view_cursor(Qt.CursorShape.ArrowCursor)
            if hasattr(self, "hover_bar"):
                self.hover_bar.hide()
            self._close_inline_replace()
            kind = self.tool.currentData()
            self.clear_overlay()
            self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag if kind == "browse" else QGraphicsView.DragMode.NoDrag)
            if kind == "browse":
                return
            if not self.host._allow_operation("content_edit" if kind == "replace" else "annotate"):
                self.tool.setCurrentIndex(0)
                return
            self._ensure_filter()
            self.color = QColor("#EAB308" if kind in ("highlight", "underline", "note") else "#DC2626")
            self._update_color_button_style()
            if not self.ensure_snapshot():
                return
            self.feedback.setText("点击或框选文字即可在原处直接修改；Enter 确认，Esc 取消。" if kind == "replace" else
                                  "点一下添加便签。" if kind == "note" else "在当前页拖选或绘制；Esc 取消当前笔画。")
        except Exception as exc:
            self.feedback.setText(f"工具切换失败：{exc}")

    def ensure_snapshot(self):
        index = self.host.ws.preview_index()
        if index is None:
            return False
        ref = self.host.ws.pages[index]
        if self.snapshot_key == ref.render_key:
            return True
        try:
            snapshot = self.host.store.text_snapshot(ref)
        except Exception as exc:
            self.feedback.setText(str(exc))
            return False
        self.snapshot, self.snapshot_key = snapshot, ref.render_key
        self.annotations.blockSignals(True)
        self.annotations.clear()
        annots = snapshot.get("annotations", ())
        if annots:
            self.annotations.addItem("选择当前页批注…", None)
            self.annotations.setEnabled(True)
        else:
            self.annotations.addItem("当前页暂无批注", None)
            self.annotations.setEnabled(False)
        for item in annots:
            names = {0: "便签", 2: "文本", 3: "线条／箭头", 4: "矩形", 5: "椭圆", 8: "高亮", 9: "下划线", 15: "画笔"}
            title = names.get(item["kind"], "批注")
            content = item.get("content") or ""
            if content:
                title += " · " + content[:20]
            self.annotations.addItem(title, item["id"])
        self.annotations.blockSignals(False)
        self.sync_annotation_actions()
        return True

    def sync_annotation_actions(self):
        selected_id = self.annotations.currentData()
        selected = selected_id is not None
        for button in getattr(self, "annotation_actions", ()):
            button.setEnabled(selected)
            button.setToolTip("" if selected else "先在页面中选中批注")

    def find_annotation_at(self, point):
        import pymupdf as p
        if not self.snapshot or not self.snapshot.get("annotations"):
            return None
        local = self.canonical(point)
        for item in reversed(self.snapshot["annotations"]):
            rect = p.Rect(item["rect"])
            expanded = p.Rect(rect.x0 - 6, rect.y0 - 6, rect.x1 + 6, rect.y1 + 6)
            if expanded.contains(local):
                return item
        return None

    def open_note_dialog(self, identifier: str):
        if not self.snapshot or not self.snapshot.get("annotations"):
            return
        annot = next((a for a in self.snapshot["annotations"] if a["id"] == identifier), None)
        if not annot:
            return
        content = annot.get("content", "")
        can_edit = self.host._allow_operation("annotate")
        dialog = NoteViewerDialog(self, content, can_edit=can_edit)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if dialog.action == "save" and dialog.new_text != content:
                self.commit(PageEdit("note_text", annotation_id=identifier, text=dialog.new_text))
            elif dialog.action == "delete":
                self.commit(PageEdit("delete", annotation_id=identifier))

    def clear_overlay(self):
        if hasattr(self, "hover_bar"):
            self.hover_bar.hide()
        if self.overlay is not None:
            self.view._scene.removeItem(self.overlay)
        self.overlay = None
        self.stroke = []

    def eventFilter(self, watched, event):
        if watched is not self.view.viewport() or not self.isVisible():
            return False
        kind = self.tool.currentData()
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            if getattr(self, "_moving_annot_id", None):
                self._moving_annot_id = None
                self.feedback.setText("已取消移动。")
                self._set_view_cursor(Qt.CursorShape.ArrowCursor)
                return True
            self._close_inline_replace()
            self.clear_overlay()
            return True

        if getattr(self, "_moving_annot_id", None):
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                point = self.view.mapToScene(event.position().toPoint())
                index = self.view.page_at_y(point.y())
                if 0 <= index < len(self.view.page_rects) and self.view.page_rects[index].contains(point):
                    canonical_pt = self.canonical(point)
                    annot_id = self._moving_annot_id
                    self._moving_annot_id = None
                    self._set_view_cursor(Qt.CursorShape.ArrowCursor)
                    self.move_annot_to(annot_id, canonical_pt)
                    self.feedback.setText("批注已移动；Ctrl+Z 撤销。")
                    return True

        inline_rep = getattr(self, "_inline_replace", None)
        if inline_rep:
            w = inline_rep.get("widget")
            if w and w.isVisible() and hasattr(event, "position"):
                pos = event.position().toPoint()
                if w.geometry().contains(pos):
                    return False
                if event.type() == QEvent.Type.MouseButtonPress:
                    self._close_inline_replace()

        hover_bar = getattr(self, "hover_bar", None)
        if hover_bar and hover_bar.isVisible() and hasattr(event, "position"):
            pos = event.position().toPoint()
            if hover_bar.geometry().contains(pos):
                hover_bar._hide_timer.stop()
                return False

        # 便签与批注悬停操作条
        if event.type() == QEvent.Type.MouseMove and not self.stroke:
            try:
                pos = event.position().toPoint()
                if hover_bar and hover_bar.isVisible() and hover_bar.geometry().contains(pos):
                    hover_bar._hide_timer.stop()
                else:
                    point = self.view.mapToScene(pos)
                    index = self.view.page_at_y(point.y())
                    if 0 <= index < len(self.view.page_rects) and self.view.page_rects[index].contains(point):
                        if self.ensure_snapshot():
                            hit_annot = self.find_annotation_at(point)
                            if hit_annot:
                                if hover_bar:
                                    hover_bar.show_for_annot(hit_annot, index)
                            else:
                                if hover_bar and hover_bar.isVisible() and not hover_bar._hide_timer.isActive():
                                    hover_bar._hide_timer.start(250)
                    else:
                        if hover_bar and hover_bar.isVisible() and not hover_bar._hide_timer.isActive():
                            hover_bar._hide_timer.start(250)
            except Exception:
                pass

        # 便签点击查看/编辑
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            if event.button() == Qt.MouseButton.LeftButton and self.host.ws.pages and not getattr(self, "_moving_annot_id", None):
                point = self.view.mapToScene(event.position().toPoint())
                index = self.view.page_at_y(point.y())
                if 0 <= index < len(self.view.page_rects) and self.view.page_rects[index].contains(point):
                    if self.ensure_snapshot():
                        hit_annot = self.find_annotation_at(point)
                        if hit_annot and (hit_annot.get("kind") == 0 or hit_annot.get("content")):
                            self.annotations.setCurrentIndex(self.annotations.findData(hit_annot["id"]))
                            self.open_note_dialog(hit_annot["id"])
                            return True

        if kind == "browse":
            return False

        if event.type() not in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease):
            return False
        try:
            if event.type() == QEvent.Type.MouseButtonPress:
                if event.button() != Qt.MouseButton.LeftButton or not self.host.ws.pages:
                    return False
                point = self.view.mapToScene(event.position().toPoint())
                index = self.view.page_at_y(point.y())
                if not 0 <= index < len(self.view.page_rects) or not self.view.page_rects[index].contains(point):
                    return True
                if index != self.host.ws.preview_index():
                    self.host.go_to_page(index + 1)
                    self.snapshot_key = None
                if not self.ensure_snapshot():
                    return True
                self.stroke = [point]
                if kind == "select":
                    self.select_at(point)
                    self.stroke = []
                    return True
                self.overlay = QGraphicsPathItem()
                self.overlay.setZValue(4)
                self.overlay.setPen(QPen(self.color, self.width.value()))
                self.view._scene.addItem(self.overlay)
                return True
            if not self.stroke:
                return False
            point = self.view.mapToScene(event.position().toPoint())
            bounds = self.view.page_rects[self.host.ws.preview_index()]
            point = QPointF(max(bounds.left(), min(bounds.right(), point.x())), max(bounds.top(), min(bounds.bottom(), point.y())))
            if event.type() == QEvent.Type.MouseMove:
                if kind == "ink":
                    last = self.stroke[-1]
                    distance = math.hypot(point.x() - last.x(), point.y() - last.y()) * self.view.transform().m11() * self.view.devicePixelRatioF()
                    if distance >= 1:
                        self.stroke.append(point)
                path = QPainterPath(self.stroke[0])
                if kind == "ink":
                    for pos in self.stroke[1:]:
                        path.lineTo(pos)
                elif kind == "arrow":
                    path.lineTo(point)
                else:
                    from PyQt6.QtCore import QRectF
                    rectangle = QRectF(self.stroke[0], point).normalized()
                    path.addEllipse(rectangle) if kind == "ellipse" else path.addRect(rectangle)
                self.overlay.setPath(path)
                return True
            if event.button() != Qt.MouseButton.LeftButton:
                return False
            points = [*self.stroke, point]
            self.clear_overlay()
            try:
                self.finish(kind, points)
            except (ValueError, PdfError) as exc:
                QMessageBox.information(self, "本次操作未应用", str(exc))
            except Exception as exc:
                self.feedback.setText(f"操作未完成：{exc}")
            return True
        except Exception as exc:
            self.clear_overlay()
            self.feedback.setText(f"操作异常：{exc}")
            return False

    def canonical(self, point):
        import pymupdf as p
        index = self.host.ws.preview_index()
        top = self.view.page_rects[index].topLeft()
        return p.Point(point.x() - top.x(), point.y() - top.y()) * ~p.Matrix(self.snapshot["matrix"])

    def select_at(self, point):
        import pymupdf as p
        local = self.canonical(point)
        for item in reversed(self.snapshot["annotations"]):
            if p.Rect(item["rect"]).contains(local):
                self.annotations.setCurrentIndex(self.annotations.findData(item["id"]))
                return
        self.annotations.setCurrentIndex(0)

    def _close_inline_replace(self):
        replace_state = getattr(self, "_inline_replace", None)
        if replace_state:
            widget = replace_state.get("widget")
            if widget:
                widget.deleteLater()
            self._inline_replace = None

    def _start_inline_replace(self, ref, area, original):
        import pymupdf as p
        self._close_inline_replace()
        index = self.host.ws.preview_index()
        if index is None or index >= len(self.view.page_rects):
            return

        matrix = p.Matrix(self.snapshot["matrix"])
        local_rect = area * matrix
        page_top = self.view.page_rects[index].topLeft()

        scene_tl = page_top + QPointF(local_rect.x0, local_rect.y0)
        scene_br = page_top + QPointF(local_rect.x1, local_rect.y1)

        v_tl = self.view.mapFromScene(scene_tl)
        v_br = self.view.mapFromScene(scene_br)

        vx = min(v_tl.x(), v_br.x())
        vy = min(v_tl.y(), v_br.y())
        vw = max(abs(v_br.x() - v_tl.x()), 60)
        vh = max(abs(v_br.y() - v_tl.y()), 22)

        container = QWidget(self.view.viewport())
        container.setObjectName("inlineReplace")
        container.setStyleSheet(
            "QWidget#inlineReplace { background: #FFFFFF; border: 2px solid #0F766E; border-radius: 8px; } "
            "QLineEdit { border: none; background: transparent; font-size: 13px; color: #134E4A; padding: 2px 6px; } "
            "QPushButton { min-height: 24px; max-height: 24px; font-size: 12px; border-radius: 5px; } "
            "QPushButton#apply { background: #0F766E; color: #FFFFFF; font-weight: 600; padding: 0 10px; border: none; } "
            "QPushButton#apply:hover { background: #0D9488; } "
            "QPushButton#cancel { background: #F3F4F6; color: #4B5563; border: 1px solid #D1D5DB; padding: 0 6px; } "
            "QPushButton#cancel:hover { background: #E5E7EB; }"
        )
        layout = QHBoxLayout(container)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        edit_input = QLineEdit(original, container)
        edit_input.setAccessibleName("原处文字修改")
        edit_input.selectAll()
        layout.addWidget(edit_input, 1)

        apply_btn = QPushButton("确定", container)
        apply_btn.setObjectName("apply")
        apply_btn.setToolTip("应用修改 (Enter)")
        layout.addWidget(apply_btn)

        cancel_btn = QPushButton("✕", container)
        cancel_btn.setObjectName("cancel")
        cancel_btn.setToolTip("取消 (Esc)")
        layout.addWidget(cancel_btn)

        total_w = max(vw + 120, 240)
        total_h = max(vh + 12, 34)
        vp_width = self.view.viewport().width()
        if total_w > vp_width - 16:
            total_w = max(vp_width - 16, 200)
        vx_pos = max(8, min(int(vx - 6), vp_width - total_w - 8))
        vy_pos = max(8, int(vy + (vh - total_h) / 2))

        container.setGeometry(vx_pos, vy_pos, total_w, total_h)
        container.raise_()
        container.show()
        edit_input.setFocus()

        self._inline_replace = {
            "widget": container,
            "ref": ref,
            "area": area,
            "original": original,
        }

        def commit():
            text = edit_input.text()
            self._close_inline_replace()
            if not text or text == original:
                return
            try:
                replacement_edit = run_operation(
                    self.host, "检查文字修正",
                    lambda progress: self.host.store.prepare_replacement(ref, area, text)
                )
                self.commit(replacement_edit, confirm=False)
                self.feedback.setText(f"已在原处修改为「{text}」；Ctrl+Z 可撤销，Ctrl+S 保存。")
            except (ValueError, PdfError) as exc:
                QMessageBox.information(self, "修改未应用", str(exc))
            except Exception as exc:
                self.feedback.setText(f"修改未应用：{exc}")

        def cancel():
            self._close_inline_replace()

        edit_input.returnPressed.connect(commit)
        apply_btn.clicked.connect(commit)
        cancel_btn.clicked.connect(cancel)

    def finish(self, kind, points):
        import pymupdf as p
        from app.pdfedits import selection_quads
        index = self.host.ws.preview_index()
        ref = self.host.ws.pages[index]
        if ref.render_key != self.snapshot_key:
            raise ValueError("当前页已改变，请重新操作。")
        canonical = [self.canonical(pos) for pos in points]
        area = p.Rect(canonical[0], canonical[-1]).normalize()
        color = (self.color.redF(), self.color.greenF(), self.color.blueF())
        identifier = "zhixu-" + uuid.uuid4().hex
        if kind == "note":
            text, accepted = QInputDialog.getMultiLineText(self, "添加备注", "备注内容")
            if not accepted or not text.strip():
                return
            edit = PageEdit("note", (tuple(canonical[0]),), text=text, color=color, annotation_id=identifier)
        elif kind == "replace":
            from app.pdfedits import selected_lines
            raw_data = self.snapshot.get("raw") if isinstance(self.snapshot, dict) else {}
            selected = selected_lines(raw_data, area)
            if not selected:
                # User clicked at a single point; look for intersecting or closest line
                click_pt = canonical[0]
                for block in raw_data.get("blocks", ()):
                    for line in block.get("lines", ()):
                        box = p.Rect(line["bbox"])
                        expanded = p.Rect(box.x0 - 8, box.y0 - 8, box.x1 + 8, box.y1 + 8)
                        if expanded.contains(click_pt):
                            area = box
                            selected = selected_lines(raw_data, area)
                            break
                    if selected:
                        break
            if not selected:
                raise ValueError("请点击或框选一行需要修改的文字。")
            chars = selected[0][1]
            original = "".join(char["c"] for char, _ in chars)
            if not original:
                raise ValueError("选区没有可修改的文字。")
            line_area = p.Rect(chars[0][0]["bbox"])
            for char_item, _ in chars[1:]:
                line_area |= p.Rect(char_item["bbox"])
            self._start_inline_replace(ref, line_area, original)
            return
        elif kind in ("highlight", "underline"):
            quads = selection_quads(self.snapshot["raw"], area)
            if not quads:
                raise ValueError("选区没有文字层；扫描图片可以用画笔或便签标注。")
            edit = PageEdit(kind, quads, color=color, width=self.width.value(), annotation_id=identifier)
        elif kind in ("rect", "ellipse"):
            edit = PageEdit(kind, tuple(area), color=color, width=self.width.value(), annotation_id=identifier)
        else:
            chosen = canonical if kind == "ink" else [canonical[0], canonical[-1]]
            edit = PageEdit(kind, tuple(tuple(pos) for pos in chosen), color=color, width=self.width.value(), annotation_id=identifier)
        self.commit(edit, confirm=kind == "replace")

    def commit(self, edit, *, confirm=False):
        index = self.host.ws.preview_index()
        if index is None or not self.host._allow_operation("content_edit" if edit.kind == "replace" else "annotate"):
            return
        ref = self.host.ws.pages[index]
        candidate = ref.edited(edit)
        try:
            image = run_operation(self.host, "预览本次修改", lambda progress: self.host.store.render_qimage(candidate, self.host._preview_render_width()))
            if confirm:
                dialog = QDialog(self)
                dialog.setWindowTitle("文字修正预览")
                dialog.resize(640, 520)
                layout = QVBoxLayout(dialog)
                layout.setContentsMargins(20, 20, 20, 20)
                layout.setSpacing(12)
                label = QLabel("保存时会移除所选旧文字并写入新文字。请核对字体和位置。")
                label.setObjectName("hint")
                label.setWordWrap(True)
                layout.addWidget(label)

                card = QFrame()
                card.setObjectName("card")
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(12, 12, 12, 12)
                preview = QLabel()
                preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
                preview.setPixmap(QPixmap.fromImage(image).scaled(580, 500, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                card_layout.addWidget(preview)
                layout.addWidget(card, 1)

                box = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
                apply_btn = box.button(QDialogButtonBox.StandardButton.Apply)
                apply_btn.setText("应用修改")
                apply_btn.setProperty("primary", True)
                box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
                apply_btn.clicked.connect(dialog.accept)
                box.rejected.connect(dialog.reject)
                layout.addWidget(box)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
            if self.host.ws.pages[index] is not ref:
                raise ValueError("工作区已改变，请重新应用修改。")
            self.host._cache_page_image(candidate.render_key, image)
            self.host.ws.edit_page(index, edit, "文字修正" if edit.kind == "replace" else "修改批注", prepared=candidate)
            self.snapshot_key = None
            self.host.refresh()
            self.ensure_snapshot()
            self.feedback.setText("修改已应用；Ctrl+Z 撤销，Ctrl+S 保存。")
        except (ValueError, PdfError) as exc:
            QMessageBox.information(self, "本次操作未应用", str(exc))

    def delete_annot(self, identifier: str):
        if identifier:
            self.commit(PageEdit("delete", annotation_id=identifier))

    def apply_style_to(self, identifier: str):
        if identifier:
            self.commit(PageEdit("style", annotation_id=identifier,
                                 color=(self.color.redF(), self.color.greenF(), self.color.blueF()),
                                 width=self.width.value()))

    def move_annot_to(self, identifier: str, canonical_pt):
        if identifier:
            self.commit(PageEdit("move", points=(tuple(canonical_pt),), annotation_id=identifier))

    def start_move_annot(self, identifier: str):
        self._moving_annot_id = identifier
        self.feedback.setText("请在页面点击新位置以放置批注；Esc 取消。")
        self._set_view_cursor(Qt.CursorShape.CrossCursor)

    def delete(self):
        identifier = self.annotations.currentData()
        if identifier:
            self.delete_annot(identifier)

    def style(self):
        identifier = self.annotations.currentData()
        if identifier:
            self.apply_style_to(identifier)

    def note_text(self):
        identifier = self.annotations.currentData()
        if not identifier:
            return
        self.open_note_dialog(identifier)
