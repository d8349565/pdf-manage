"""Lazily created outline and review controls for the existing reader."""
from __future__ import annotations
from app.themes import set_theme_style, ui_color

from dataclasses import replace
from bisect import bisect_right
import math
import uuid
from time import perf_counter

from PyQt6.QtCore import QEvent, QPointF, QRect, QRectF, QSize, QTimer, Qt
from PyQt6.QtGui import QAction, QActionGroup, QColor, QFont, QFontMetrics, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFrame, QGraphicsDropShadowEffect, QGraphicsPathItem, QGraphicsView, QHeaderView, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMenu, QMessageBox, QProxyStyle, QPushButton, QSizePolicy, QStyle, QTextEdit, QToolBar, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
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
        set_theme_style(self, """
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
        self._items = []
        self._chapter_pages = []
        self._chapters = {}
        self._following_item = None
        self._expansion_mode = None
        self.expand_all_action = QAction("展开全部", self)
        self.expand_all_action.triggered.connect(lambda: self.set_all_expanded(True))
        self.collapse_all_action = QAction("折叠全部", self)
        self.collapse_all_action.triggered.connect(lambda: self.set_all_expanded(False))
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
        self.tree.viewport().installEventFilter(self)
        self.tree.installEventFilter(self)
        self.tree.verticalScrollBar().installEventFilter(self)
        self._follow_timer = QTimer(self)
        self._follow_timer.setSingleShot(True)
        self._follow_timer.setInterval(1600)
        self._follow_timer.timeout.connect(self.sync_position)
        layout.addWidget(self.tree, 1)

        self.feedback = QLabel("在当前阅读页添加书签。")
        self.feedback.setObjectName("hint")
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        self.refresh()

    def refresh(self):
        key = (id(self.host.ws.pages), id(self.host.ws.outline))
        if key == self._key:
            self.sync_position()
            return
        self._key = key
        self._expansion_mode = None
        self.entries = self.host.ws.outline
        if self.entries is None:
            try:
                self.entries = run_operation(self.host, "读取大纲", lambda progress: self.host.store.outline_entries(self.host.ws.pages))
            except PdfError as exc:
                self.feedback.setText(str(exc))
                self.entries = ()
        targets = {ref.page_id: i + 1 for i, ref in enumerate(self.host.ws.pages)}
        self.tree.clear()
        self._items = []
        self._chapters = {}
        self._following_item = None
        parents = []
        for index, entry in enumerate(self.entries):
            while len(parents) >= entry.level:
                parents.pop()
            target = targets.get(entry.target_id)
            item = QTreeWidgetItem([entry.title, str(target) if target else "—"])
            item.setToolTip(0, entry.title)
            item.setData(0, Qt.ItemDataRole.UserRole, index)
            self._items.append(item)
            if target:
                self._chapters.setdefault(target - 1, []).append(index)
            if entry.target_id is not None and target is None:
                item.setToolTip(0, "目标页已删除；撤销删除可恢复跳转。")
                item.setForeground(0, QColor(ui_color("warning")))
            if parents:
                parents[-1].addChild(item)
            else:
                self.tree.addTopLevelItem(item)
            parents.append(item)
        self.tree.expandToDepth(1)
        has_children = any(item.childCount() for item in self._items)
        self.expand_all_action.setEnabled(has_children)
        self.collapse_all_action.setEnabled(has_children)
        self._chapter_pages = sorted(self._chapters)
        self.feedback.setText("在当前阅读页添加书签。" if self.entries else "暂无大纲，可为关键页面添加书签。")
        self.sync_position()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Wheel, QEvent.Type.MouseButtonPress,
                            QEvent.Type.MouseButtonRelease, QEvent.Type.KeyPress):
            self._follow_timer.start()
        return super().eventFilter(watched, event)

    def sync_position(self, *, force=False):
        if not self.isVisible() or self._key != (id(self.host.ws.pages), id(self.host.ws.outline)):
            return
        if not force and (self._follow_timer.isActive() or QApplication.mouseButtons()
                          or QApplication.activePopupWidget() or QApplication.activeModalWidget()):
            if not self._follow_timer.isActive():
                self._follow_timer.start()
            return
        current = self.host.ws.preview_index()
        slot = bisect_right(self._chapter_pages, current) - 1 if current is not None else -1
        target = None
        if slot >= 0:
            page = self._chapter_pages[slot]
            candidates = self._chapters[page]
            # 没有可靠的页内坐标时，稳定选中该页第一个条目。
            target = candidates[0]
            view = self.host.preview_view
            summary = self.host.store.cached_annotations(self.host.ws.pages[page])
            if summary and page < len(view.page_rects):
                import pymupdf as p
                matrix = p.Matrix(summary['source_pdf_matrix'])
                positions = []
                for index in candidates:
                    point = dict(self.entries[index].destination).get('to')
                    if (isinstance(point, (tuple, list)) and len(point) == 2
                            and all(isinstance(v, (int, float)) and math.isfinite(v) for v in point)):
                        positions.append(((p.Point(point) * matrix).y, index))
                if len(positions) == len(candidates):
                    visible = view.mapToScene(view.viewport().rect()).boundingRect()
                    y = (visible.top() + min(visible.height() / 4, 100)
                         - view.page_rects[page].top())
                    eligible = [(offset, index) for offset, index in positions
                                if page < current or offset <= y]
                    target = max(eligible)[1] if eligible else (
                        self._chapters[self._chapter_pages[slot - 1]][-1] if slot > 0 else None)
        item = self._items[target] if target is not None else None
        if item is not None and self._expansion_mode is False:
            ancestor = item.parent()
            while ancestor is not None:
                if not ancestor.isExpanded():
                    item = ancestor
                ancestor = ancestor.parent()
        if not force and item is self._following_item and item is self.tree.currentItem():
            return
        self._following_item = item
        self.tree.setCurrentItem(item)
        if item is None:
            self.tree.clearSelection()
            return
        if self._expansion_mode is not False:
            ancestor = item.parent()
            while ancestor is not None:
                ancestor.setExpanded(True)
                ancestor = ancestor.parent()
        self.tree.scrollToItem(item)

    def set_all_expanded(self, expanded):
        current = self.tree.currentItem()
        following = current is self._following_item
        self._expansion_mode = expanded
        self._follow_timer.stop()
        if expanded:
            self.tree.expandAll()
        else:
            self.tree.collapseAll()
        if following:
            self.sync_position(force=True)
        else:
            if not expanded and current is not None:
                while current.parent() is not None:
                    current = current.parent()
                self.tree.setCurrentItem(current)
            self._follow_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self.sync_position(force=True)

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
        menu.addAction(self.expand_all_action)
        menu.addAction(self.collapse_all_action)
        menu.addSeparator()
        for title, callback in (("在当前页添加书签", self.add), ("重命名", self.rename),
                                ("删除（含子项）", self.delete), ("设为子项", lambda: self.shift(1)),
                                ("提升层级", lambda: self.shift(-1))):
            action = menu.addAction(title)
            action.triggered.connect(callback)
            action.setEnabled(bool(self.host.ws.pages) and not self.host.store.operation_reason(self.host.ws.pages, "outline"))
        menu.exec(position)
        menu.deleteLater()


class MoveHandleButton(QToolButton):
    def __init__(self, hover_bar):
        super().__init__(hover_bar)
        self.hover_bar = hover_bar
        self._drag_start_pos = None
        self._dragging = False
        self._drag_cancelled = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setToolTip("按住拖动以移动批注，或点击后点选新位置")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.hover_bar._hide_timer.stop()
            self._drag_start_pos = event.globalPosition().toPoint()
            self._drag_scene_start = self.hover_bar.tools.view.mapToScene(
                self.hover_bar.tools.view.viewport().mapFromGlobal(self._drag_start_pos))
            self._dragging = False
            self._drag_cancelled = False
            self._bar_start_pos = self.hover_bar.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and self._drag_start_pos:
            delta = event.globalPosition().toPoint() - self._drag_start_pos
            tools = self.hover_bar.tools
            if not self._dragging and delta.manhattanLength() >= QApplication.startDragDistance():
                self._dragging = tools.start_move_annot(self.hover_bar.annot_id,
                    page_index=self.hover_bar.page_index, anchor=self._drag_scene_start)
                if not self._dragging:
                    self._drag_cancelled = True
                    self._drag_start_pos = None
                    self.setDown(False)
                    event.accept()
                    return
            if self._dragging:
                point = tools.view.mapToScene(tools.view.viewport().mapFromGlobal(event.globalPosition().toPoint()))
                offset = tools.update_move_preview(point)
                if offset is not None:
                    self.hover_bar.move(self._bar_start_pos + offset)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._drag_cancelled:
                self._drag_start_pos = None
                self._drag_cancelled = False
                self.setDown(False)
                event.accept()
                return
            if self._dragging:
                self._dragging = False
                self._drag_start_pos = None
                tools = self.hover_bar.tools
                scene_pt = tools.view.mapToScene(tools.view.viewport().mapFromGlobal(event.globalPosition().toPoint()))
                self.setDown(False)
                tools.finish_move_annot(scene_pt)
                event.accept()
                return
            self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        tools = self.hover_bar.tools
        if event.key() == Qt.Key.Key_Escape and tools._move_committing:
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and (tools._move_state or self._drag_start_pos is not None):
            tools.cancel_move_annot()
            self._drag_cancelled = True
            self._drag_start_pos = None
            self.setDown(False)
            tools._set_feedback("已取消移动。")
            event.accept()
            return
        if self._dragging:
            event.accept()
            return
        super().keyPressEvent(event)

    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride and event.key() == Qt.Key.Key_Escape:
            tools = self.hover_bar.tools
            if self._drag_start_pos is not None or tools._move_state or tools._move_committing:
                event.accept()
                return True
        return super().event(event)


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
        set_theme_style(self, """
            QFrame#annotHoverBar {
                background: #FFFFFF;
                border: 1px solid #D7E8E4;
                border-radius: 8px;
            }
            QLabel#hoverTitle {
                font-size: 12px;
                font-weight: 600;
                color: #0F766E;
            }
            QLabel#hoverContent {
                font-size: 13px;
                color: #134E4A;
                background: transparent;
                border: none;
                padding: 2px 0;
            }
            QToolButton {
                min-width: 28px;
                min-height: 28px;
                padding: 0;
                border-radius: 5px;
                background: transparent;
                border: 1px solid transparent;
            }
            QToolButton:hover { background: #F0FDFA; }
            QToolButton#deleteBtn:hover {
                background: #FEF2F2;
            }
        """)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(12)
        shadow.setColor(QColor(0, 0, 0, 40))
        shadow.setOffset(0, 2)
        self.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        self.content_box = QWidget(self)
        set_theme_style(self.content_box, "background: transparent;")
        c_layout = QVBoxLayout(self.content_box)
        c_layout.setContentsMargins(0, 0, 0, 0)
        c_layout.setSpacing(4)

        self.title_label = QLabel("便签备注", self.content_box)
        self.title_label.setObjectName("hoverTitle")
        c_layout.addWidget(self.title_label)

        self.content_label = QLabel(self.content_box)
        self.content_label.setObjectName("hoverContent")
        self.content_label.setWordWrap(True)
        self.content_label.setTextFormat(Qt.TextFormat.PlainText)
        self.content_label.setFixedWidth(240)
        self.content_label.setMaximumHeight(180)
        c_layout.addWidget(self.content_label)

        layout.addWidget(self.content_box)

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(4)

        self.edit_btn = QToolButton(self)
        self.edit_btn.setObjectName("editBtn")
        self.edit_btn.clicked.connect(self._on_edit)
        btn_layout.addWidget(self.edit_btn)

        self.move_btn = MoveHandleButton(self)
        self.move_btn.setObjectName("moveBtn")
        self.move_btn.clicked.connect(self._on_move_click)
        btn_layout.addWidget(self.move_btn)

        self.style_btn = QToolButton(self)
        self.style_btn.setObjectName("styleBtn")
        self.style_btn.setToolTip("应用当前选择的颜色与线宽")
        self.style_btn.clicked.connect(self._on_style)
        btn_layout.addWidget(self.style_btn)

        self.delete_btn = QToolButton(self)
        self.delete_btn.setObjectName("deleteBtn")
        self.delete_btn.clicked.connect(self._on_delete)
        btn_layout.addWidget(self.delete_btn)
        btn_layout.addStretch()
        for button, icon, title in (
            (self.edit_btn, "pen", "编辑备注"), (self.move_btn, "move", "移动批注"),
            (self.style_btn, "circle", "应用当前颜色与线宽"), (self.delete_btn, "trash", "删除批注"),
        ):
            button.setFixedSize(28, 28)
            button.setIconSize(QSize(16, 16))
            button.setIcon(toolbar_icon(icon, "#B91C1C" if icon == "trash" else "#0F766E", 16))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.setToolTip(title)
            button.setAccessibleName(title)

        layout.addLayout(btn_layout)
        self.hide()

    def enterEvent(self, event):
        if self._hide_timer.isActive():
            self._hide_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.move_btn._drag_start_pos is None:
            self._hide_timer.start(250)
        super().leaveEvent(event)

    def show_for_annot(self, annot, page_index):
        import pymupdf as p
        if annot.get("watermark"):
            self.hide()
            return
        self._hide_timer.stop()
        if self.isVisible() and self.annot_id == annot["id"] and self.page_index == page_index:
            return
        self.annot = annot
        self.annot_id = annot["id"]
        self.page_index = page_index

        content = (annot.get("content") or "").strip()
        kind = annot.get("kind", -1)
        names = {0: "便签", 2: "文本", 3: "线条", 4: "矩形", 5: "椭圆", 8: "高亮", 9: "下划线", 15: "画笔"}
        kind_name = names.get(kind, "批注")
        self.edit_btn.show()
        self.style_btn.setVisible(kind != 0)

        if content or kind == 0:
            self.content_box.show()
            self.title_label.setText(f"{kind_name}备注")
            self.content_label.setText(content if content else "（暂无备注内容，点击编辑添加）")
            self.edit_btn.setToolTip("编辑备注")
        else:
            self.content_box.hide()
            self.edit_btn.setToolTip("添加备注")

        self.move_btn.setVisible(kind in (0, 2, 4))
        if kind == 2:
            self.title_label.setText("页面文本框")
            self.edit_btn.setToolTip("编辑文本框")
            self.style_btn.setToolTip("设置字体、字号与颜色")
        else:
            self.style_btn.setToolTip("应用当前选择的颜色与线宽")
        self.move_btn.setToolTip("拖动可实时预览位置；也可点击后选位置，Esc 取消")

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
        self.tools.start_move_annot(self.annot_id, page_index=self.page_index)

    def _on_style(self):
        self.hide()
        self.tools.apply_style_to(self.annot_id)

    def _on_delete(self):
        self.hide()
        self.tools.delete_annot(self.annot_id)


class _ReviewToolbarStyle(QProxyStyle):
    def pixelMetric(self, metric, option=None, widget=None):
        if metric == QStyle.PixelMetric.PM_ToolBarExtensionExtent:
            return 32
        return super().pixelMetric(metric, option, widget)


class AnnotationTools(QWidget):
    TOOL_DEFS = (
        ("browse", "mouse-pointer", "浏览／移动"),
        ("select", "annotation-select", "选择页面批注"),
        ("highlight", "highlighter", "文字高亮"),
        ("underline", "underline", "下划线"),
        ("ink", "ink-pen", "自由画笔"),
        ("note", "note-comment", "便签备注"),
        ("freetext", "text-box", "页面文本框"),
        ("rect", "square", "矩形框"),
        ("ellipse", "circle", "椭圆"),
        ("arrow", "arrow-up-right", "箭头"),
        ("replace", "text-edit", "单行文字修正"),
        ("mosaic", "mosaic", "马赛克（像素块／模糊／纯色遮盖）"),
    )

    def __init__(self, host):
        super().__init__()
        self.host = host
        self.view = host.preview_view
        self.snapshot = None
        self.snapshot_key = None
        self.snapshot_index = None
        self.stroke = []
        self.stroke_page = None
        self.overlay = None
        self._move_state = None
        self._move_preview = None
        self._move_committing = False
        self.color = QColor("#EAB308")
        from app.mosaic import DEFAULTS
        self.mosaic_settings = dict(DEFAULTS)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.bar = QFrame(self)
        self.bar.setObjectName("annotationBar")
        bar_layout = QHBoxLayout(self.bar)
        bar_layout.setContentsMargins(10, 4, 10, 4)
        bar_layout.setSpacing(6)

        # Underlying QComboBox kept for full API & test compatibility
        self.tool = QComboBox(self)
        self.tool.hide()
        for kind, icon_name, name in self.TOOL_DEFS:
            self.tool.addItem(name, kind)
        self.tool.setAccessibleName("审阅工具")
        self.tool.currentIndexChanged.connect(self._on_tool_combo_changed)

        # Native toolbar keeps one row and provides its own overflow menu.
        self.tool_bar = QToolBar(self.bar)
        self.tool_bar.setObjectName('reviewTools')
        self._toolbar_style = _ReviewToolbarStyle(QApplication.style().objectName())
        self._toolbar_style.setParent(self.tool_bar)
        self.tool_bar.setStyle(self._toolbar_style)
        self.tool_bar.setAccessibleName('审阅工具')
        self.tool_bar.setMovable(False)
        self.tool_bar.setFloatable(False)
        self.tool_bar.setIconSize(QSize(16, 16))
        self.tool_bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.tool_bar.setMinimumWidth(0)
        self.tool_bar.setFixedHeight(36)
        self.tool_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.tool_group = QActionGroup(self)
        self.tool_actions = []
        short_names = {"browse": "浏览", "select": "选批注", "highlight": "高亮", "underline": "下划线",
                       "ink": "画笔", "note": "便签", "freetext": "文本框", "rect": "矩形",
                       "ellipse": "椭圆", "arrow": "箭头", "replace": "改原文", "mosaic": "马赛克"}
        for idx, (kind, icon_name, name) in enumerate(self.TOOL_DEFS):
            action = QAction(toolbar_icon(icon_name, '#526B63', 16), short_names[kind], self)
            action.setToolTip(name)
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, i=idx: self.tool.setCurrentIndex(i))
            self.tool_group.addAction(action)
            self.tool_actions.append(action)
        self.insert_image_action = QAction(toolbar_icon('image', '#526B63', 16), '图片', self)
        self.insert_image_action.setToolTip('插入图片（PNG/JPEG，可拖动、调整尺寸和旋转）')
        self.insert_image_action.triggered.connect(lambda: self.host.image_tools.choose_image())
        actions = {kind: action for (kind, _, _), action in zip(self.TOOL_DEFS, self.tool_actions)}
        actions['image'] = self.insert_image_action
        actions['capture'] = self.host.capture_action
        groups = (('browse', 'select', 'capture'), ('highlight', 'underline', 'arrow', 'note'),
                  ('replace', 'mosaic', 'image', 'freetext'), ('ink', 'rect', 'ellipse'))
        for group_index, group in enumerate(groups):
            if group_index:
                self.tool_bar.addSeparator()
            for kind in group:
                self.tool_bar.addAction(actions[kind])
        self.tool_buttons = [self.tool_bar.widgetForAction(action) for action in self.tool_actions]
        for button, (_, _, name) in zip(self.tool_buttons, self.TOOL_DEFS):
            button.setAccessibleName(name)
        self.insert_image_button = self.tool_bar.widgetForAction(self.insert_image_action)
        self.insert_image_button.setAccessibleName("插入图片")
        self.tool_actions[0].setChecked(True)
        bar_layout.addWidget(self.tool_bar, 1)

        sep1 = self.options_separator = QFrame()
        sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setFixedSize(1, 20)
        set_theme_style(sep1, 'background: #DCE5DF; border: none;')
        bar_layout.addWidget(sep1)

        # Color picker swatch button
        self.color_button = QToolButton(self.bar)
        self.color_button.setToolTip("选择批注颜色")
        self.color_button.setAccessibleName("选择批注颜色")
        self.color_button.setFixedSize(36, 32)
        self.color_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.color_button.setIconSize(QSize(16, 16))
        self.color_button.clicked.connect(self.choose_color)
        bar_layout.addWidget(self.color_button)

        self.width = QDoubleSpinBox(self.bar)
        self.width.setRange(0.1, 30.0)
        self.width.setValue(2.0)
        self.width.setDecimals(1)
        self.width.setSuffix(" pt")
        self.width.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.width.setAccessibleName("批注线宽")
        self.width.setToolTip("批注线宽（pt）")
        self.width.setFixedSize(62, 32)
        bar_layout.addWidget(self.width)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        set_theme_style(sep2, "color: #E2EEEC; margin: 4px 2px;")
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

        self.feedback = QLabel("拖选或点击操作；Ctrl+Z 撤销", self.bar)
        self.feedback.setObjectName("hint")
        self.feedback.hide()

        close = QToolButton(self.bar)
        close.setFixedSize(32, 32)
        close.setIconSize(QSize(16, 16))
        close.setIcon(toolbar_icon("x", "#4D6B67", 16))
        close.setToolTip("收起审阅工具")
        close.setAccessibleName("收起审阅工具")
        close.clicked.connect(self.deactivate)
        bar_layout.addWidget(close)

        root_layout.addWidget(self.bar)
        self._update_color_button_style()
        self._update_options()
        self.view.viewport().installEventFilter(self)
        if isinstance(self.view, QWidget):
            self.view.setMouseTracking(True)
            self.view.viewport().setMouseTracking(True)
        self.hover_bar = AnnotationHoverBar(self.view.viewport(), self)
        if hasattr(self.view, "installEventFilter"):
            self.view.installEventFilter(self)
        if hasattr(self.view, "view_changed"):
            self.view.view_changed.connect(self.hover_bar.hide)
            self.view.view_changed.connect(self._position_inline_replace)
            self.view.view_changed.connect(self._move_view_changed)

    def showEvent(self, event):
        super().showEvent(event)
        for button in (*self.tool_buttons, self.insert_image_button):
            button.ensurePolished()
            # 先完整显示标签，再由原生工具栏把放不下的操作收进菜单。
            button.setMinimumWidth(button.sizeHint().width() + 2)
        more = self.tool_bar.findChild(QToolButton, 'qt_toolbar_ext_button')
        if more is not None:
            more.setIcon(toolbar_icon('more-horizontal', '#526B63', 16))
            more.setIconSize(QSize(16, 16))
            more.setToolTip('更多审阅工具')
            more.setAccessibleName('更多审阅工具')

    def _set_feedback(self, text):
        self.feedback.setText(text)
        if hasattr(self.host, "statusBar"):
            self.host.statusBar().showMessage(text, 6000)

    def _update_options(self):
        kind = self.tool.currentData()
        color_visible = kind in ("highlight", "underline", "ink", "note", "rect", "ellipse", "arrow")
        self.options_separator.setVisible(color_visible)
        self.color_button.setVisible(color_visible)
        self.width.setVisible(kind in ("ink", "rect", "ellipse", "arrow"))

    def _on_tool_combo_changed(self, index):
        if 0 <= index < len(self.tool_buttons):
            self.tool_actions[index].setChecked(True)
        self.choose_tool()

    def _update_color_button_style(self):
        name = self.color.name()
        self.color_button.setIcon(toolbar_icon("circle", name, 18, themed=False))

    def choose_color(self):
        color = QColorDialog.getColor(self.color, self, "选择批注颜色")
        if color.isValid():
            self.color = color
            self._update_color_button_style()

    def _ensure_filter(self):
        self.remove_view_filters()
        self.view.viewport().installEventFilter(self)
        if hasattr(self.view, "installEventFilter"):
            self.view.installEventFilter(self)

    def remove_view_filters(self):
        try:
            self.view.viewport().removeEventFilter(self)
            if hasattr(self.view, "removeEventFilter"):
                self.view.removeEventFilter(self)
        except RuntimeError:
            pass

    def _set_view_cursor(self, cursor):
        vp = self.view.viewport() if hasattr(self.view, "viewport") else None
        if vp and hasattr(vp, "setCursor"):
            vp.setCursor(cursor)

    def deactivate(self):
        if not self.finish_text_box():
            return False
        self.cancel_move_annot()
        self._set_view_cursor(Qt.CursorShape.ArrowCursor)
        if hasattr(self, "hover_bar"):
            self.hover_bar.hide()
        self._close_inline_replace()
        self.tool.setCurrentIndex(0)
        self.clear_overlay()
        self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.hide()
        if hasattr(self.host, "review_button"):
            self.host.review_button.setChecked(False)
        return True

    def closeEvent(self, event):
        if not self.finish_text_box():
            event.ignore()
            return
        self.cancel_move_annot()
        self._close_inline_replace()
        self.remove_view_filters()
        super().closeEvent(event)

    def choose_tool(self):
        try:
            if hasattr(self.host, 'image_tools'):
                self.host.image_tools.clear()
            if not self.finish_text_box():
                return
            self.cancel_move_annot()
            self._set_view_cursor(Qt.CursorShape.ArrowCursor)
            if hasattr(self, "hover_bar"):
                self.hover_bar.hide()
            self._close_inline_replace()
            kind = self.tool.currentData()
            self._update_options()
            self.clear_overlay()
            self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
            if kind == "browse":
                self._set_feedback("悬浮查看便签；双击或点击浮层中的编辑按钮修改备注。")
                return
            if not self.host._allow_operation("content_edit" if kind in ("replace", "mosaic") else "annotate"):
                self.tool.setCurrentIndex(0)
                return
            self._ensure_filter()
            self.color = QColor("#EAB308" if kind in ("highlight", "underline", "note") else "#DC2626")
            if kind == "arrow":
                self.width.setValue(1.5)
            self._update_color_button_style()
            if not self.ensure_snapshot(geometry_only=kind not in ("highlight", "underline", "replace")):
                return
            self._set_feedback("点击或框选文字即可在原处直接修改；Enter 确认，Esc 取消。" if kind == "replace" else
                                  "在一页内框选区域，松开后选择马赛克样式并预览；Esc 取消框选。" if kind == "mosaic" else
                                  "点一下添加便签。" if kind == "note" else "在当前页拖选或绘制；Esc 取消当前笔画。")
            if kind == "mosaic":
                self._set_view_cursor(Qt.CursorShape.CrossCursor)
        except Exception as exc:
            self._set_feedback(f"工具切换失败：{exc}")

    def ensure_snapshot(self, index=None, *, geometry_only=False):
        started = perf_counter()
        if index is None:
            index = self.host.ws.preview_index()
        if index is None or not 0 <= index < len(self.host.ws.pages):
            return False
        ref = self.host.ws.pages[index]
        if self.snapshot_key == ref.render_key and self.snapshot is not None and (geometry_only or "raw" in self.snapshot):
            self.snapshot_index = index
            self.host.rendering.diagnostics.record("annotation_snapshot", duration_ms=round((perf_counter() - started) * 1000, 2),
                                                  geometry_only=geometry_only, cache_hit=True)
            return True
        if geometry_only:
            summary = self.host.store.cached_annotations(ref)
            if summary is not None:
                self.host.rendering.diagnostics.record("annotation_snapshot", duration_ms=round((perf_counter() - started) * 1000, 2),
                                                      geometry_only=True, cache_hit=True)
                return self._use_snapshot(ref, index, summary)
        snapshot = None if geometry_only else self.host.store.cached_text_snapshot(ref, include_tables=False)
        if snapshot is not None:
            self.host.rendering.diagnostics.record("annotation_snapshot", duration_ms=round((perf_counter() - started) * 1000, 2),
                                                  geometry_only=False, cache_hit=True)
            return self._use_snapshot(ref, index, snapshot)
        pages, kind = self.host.ws.pages, self.tool.currentData()
        active_index = self.host.ws.preview_index()
        generation = self.view.layout_generation
        try:
            snapshot = run_operation(self.host, "准备当前页批注",
                lambda progress: self.host.store.prepare_annotations(ref) if geometry_only
                    else self.host.store.text_snapshot(ref, include_tables=False))
        except Exception as exc:
            self._set_feedback(str(exc))
            return False
        finally:
            self.host.rendering.diagnostics.record("annotation_snapshot",
                duration_ms=round((perf_counter() - started) * 1000, 2),
                geometry_only=geometry_only, cache_hit=False)
        if (self.host.ws.pages is not pages or self.host.ws.preview_index() != active_index
                or self.tool.currentData() != kind or self.view.layout_generation != generation
                or index >= len(pages) or pages[index] is not ref):
            self._set_feedback("页面或工具已改变，请重新选择。")
            return False
        return self._use_snapshot(ref, index, snapshot)

    def ensure_annotation_summary(self, index):
        """Hover only consumes metadata prepared by rendering, never PDF parsing."""
        if not 0 <= index < len(self.host.ws.pages):
            return False
        ref = self.host.ws.pages[index]
        if self.snapshot_key == ref.render_key and self.snapshot is not None:
            self.snapshot_index = index
            return True
        summary = self.host.store.cached_annotations(ref)
        if summary is None:
            self.hover_bar.hide()
            return False
        return self._use_snapshot(ref, index, summary)

    def _use_snapshot(self, ref, index, snapshot):
        self.snapshot, self.snapshot_key = snapshot, ref.render_key
        self.snapshot_index = index
        self.annotations.blockSignals(True)
        self.annotations.clear()
        annots = [item for item in snapshot.get("annotations", ())
                  if not item.get('image_settings') and not item.get("watermark")]
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
            if item.get('image_settings') or item.get("watermark"):
                continue
            rect = p.Rect(item["rect"])
            padding = max(6, 12 / max(self.view.transform().m11(), .001)) if item['kind'] == 0 else 6
            expanded = p.Rect(rect.x0 - padding, rect.y0 - padding,
                              rect.x1 + padding, rect.y1 + padding)
            if expanded.contains(local):
                return item
        return None

    def open_note_dialog(self, identifier: str):
        if not self.snapshot or not self.snapshot.get("annotations"):
            return
        annot = next((a for a in self.snapshot["annotations"] if a["id"] == identifier), None)
        if not annot:
            return
        index = self.snapshot_index
        ref = self.host.ws.pages[index]
        content = annot.get("content", "")
        if annot.get("watermark"):
            self._set_feedback("水印请在「组织 → 水印」中选择后调整或删除。")
            return
        if annot.get("kind") == 2:
            self.edit_text_box(annot, index, ref)
            return
        can_edit = self.host._allow_operation("annotate")
        dialog = NoteViewerDialog(self, content, can_edit=can_edit)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if dialog.action == "save" and dialog.new_text != content:
                self.commit(PageEdit("note_text", annotation_id=identifier, text=dialog.new_text), page_index=index, expected_ref=ref)
            elif dialog.action == "delete":
                self.commit(PageEdit("delete", annotation_id=identifier), page_index=index, expected_ref=ref)

    def _stroke_is_current(self, state):
        index, ref, _, generation = state
        pages = self.host.ws.pages
        return (0 <= index < len(pages) and pages[index] is ref
                and self.view.layout_generation == generation)

    def sync_page(self):
        stroke = self.stroke_page
        if stroke and self.stroke and not self._stroke_is_current(stroke):
            self.clear_overlay()
            self._set_feedback("所选页面已改变，已取消本次绘制。")
        box = getattr(self, "_inline_text_box", None)
        if box and not box.committing and (box.index >= len(self.host.ws.pages)
                                          or self.host.ws.pages[box.index] is not box.ref):
            box.close()
            self._set_feedback("页面已改变，未完成的文本框输入已退出。")
        move = self._move_state
        if move and (move["index"] >= len(self.host.ws.pages)
                     or self.host.ws.pages[move["index"]] is not move["ref"]
                     or self.host.ws.preview_index() != move["active_index"]):
            self.cancel_move_annot()
            if not self._move_committing:
                self._set_feedback("页面已改变，移动已取消。")
        state = getattr(self, "_inline_replace", None)
        if state and self.host._scroll_navigation:
            # 滚动只更新阅读位置；主动跳页仍按原有行为退出输入。
            state["active_index"] = self.host.ws.preview_index()
        if state and (state["active_index"] != self.host.ws.preview_index()
                      or state["index"] >= len(self.host.ws.pages)
                      or self.host.ws.pages[state["index"]] is not state["ref"]):
            self._close_inline_replace()
        if self.snapshot_index is not None and (self.snapshot_index >= len(self.host.ws.pages)
                or self.host.ws.pages[self.snapshot_index].render_key != self.snapshot_key):
            self.hover_bar.hide()
            self.snapshot_key = None
        if not self.host.ws.pages:
            self.deactivate()

    def edit_text_box(self, annot, index, ref):
        self.start_text_box(annot["rect"], index, ref, annot.get("content", ""),
                             annot.get("text_settings"), annot["id"])

    def start_text_box(self, area, index, ref, text="", settings=None, identifier=None):
        from app.textboxui import InlineTextBox
        if not self.host._allow_operation("annotate"):
            return
        if not self.finish_text_box():
            return
        self._close_inline_replace()
        self.hover_bar.hide()
        self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self._inline_text_box = InlineTextBox(self, area, index, ref, text, settings, identifier)
        self._set_feedback("直接输入文字，拖动边框控制点调整宽高；右下角设置字体、字号与颜色。Ctrl+Enter 完成，Esc 取消。")

    def apply_text_box(self, box, text, settings):
        settings = dict(settings)
        settings["rotation"] = (self.host.store._source(box.ref.source_id).page_rotation(box.ref.source_page) + box.ref.rotation) % 360
        edit = PageEdit("freetext_update" if box.identifier else "freetext", tuple(box.area),
                       text=text, color=settings.pop("color"),
                       annotation_id=box.identifier or "zhixu-text-" + uuid.uuid4().hex,
                       details=tuple(settings.items()))
        return self.commit(edit, page_index=box.index, expected_ref=box.ref, on_error=box.show_error)

    def finish_text_box(self):
        box = getattr(self, "_inline_text_box", None)
        return box is None or box.apply()

    def text_box_closed(self, box):
        if getattr(self, "_inline_text_box", None) is box:
            self._inline_text_box = None
        if self.tool.currentData() == "freetext":
            self.tool.blockSignals(True)
            self.tool.setCurrentIndex(0)
            self.tool.blockSignals(False)
            self.tool_actions[0].setChecked(True)
            self._update_options()
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.view.setFocus()

    def clear_overlay(self):
        self.cancel_move_annot()
        if hasattr(self, "hover_bar"):
            self.hover_bar.hide()
        if self.overlay is not None:
            self.view._scene.removeItem(self.overlay)
        self.overlay = None
        self.stroke = []
        self.stroke_page = None

    def eventFilter(self, watched, event):
        if getattr(self, "view", None) is None:
            return False
        if (getattr(self.host, "_task_dialog", None) is not None
                and watched in (self.view, self.view.viewport())
                and event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                                     QEvent.Type.MouseButtonDblClick, QEvent.Type.MouseMove)):
            return True
        if watched in (self.view, self.view.viewport()) and event.type() == QEvent.Type.ShortcutOverride:
            if event.key() == Qt.Key.Key_Escape and (self._move_state or self._move_committing or self.stroke):
                event.accept()
                return True
        if watched in (self.view, self.view.viewport()) and self.stroke and self.tool.currentData() == "mosaic":
            if (event.type() == QEvent.Type.Hide
                    or event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape
                    or event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.RightButton):
                self.clear_overlay()
                self._set_feedback("已取消马赛克框选。")
                return event.type() != QEvent.Type.Hide
            self.sync_page()
        if watched is self.view and event.type() == QEvent.Type.Hide and self._move_state:
            self.cancel_move_annot()
        state = getattr(self, "_inline_replace", None)
        if state and watched is state["input"]:
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self._close_inline_replace()
                self.view.setFocus()
                return True
            return False
        if watched is self.view and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape and self._move_committing:
                return True
            if event.key() == Qt.Key.Key_Escape and self._move_state:
                self.cancel_move_annot()
                self._set_feedback("已取消移动。")
                return True
            if event.key() == Qt.Key.Key_Escape and self.stroke:
                self.clear_overlay()
                self._set_feedback("已取消本次绘制。")
                return True
            return False
        if watched is not self.view.viewport():
            return False
        box = getattr(self, "_inline_text_box", None)
        if box and not box.closed and event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove,
                                                       QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick):
            return True
        if event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self._position_inline_replace)
        if event.type() == QEvent.Type.Leave and not self._move_state:
            self.hover_bar._hide_timer.start(250)
        if not self.host.ws.pages or not self.view.isVisible():
            return False
        kind = self.tool.currentData() if self.isVisible() else "browse"
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            if self._move_committing:
                return True
            if self._move_state:
                self.cancel_move_annot()
                self._set_feedback("已取消移动。")
                return True
            self._close_inline_replace()
            self.clear_overlay()
            return True

        if self._move_committing:
            return event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease, QEvent.Type.MouseMove)
        if self._move_state:
            if event.type() == QEvent.Type.MouseMove:
                point = self.view.mapToScene(event.position().toPoint())
                self.update_move_preview(point)
                return True
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self.sync_page()
                if not self._move_state:
                    return True
                point = self.view.mapToScene(event.position().toPoint())
                bounds = self.view.page_rects[self._move_state["index"]]
                if not bounds.contains(point):
                    self._set_feedback("请在原页面内放置批注；Esc 取消。")
                    return True
                self.finish_move_annot(point)
                return True
            return False

        inline_rep = getattr(self, "_inline_replace", None)
        if inline_rep:
            w = inline_rep.get("widget")
            if w and w.isVisible() and hasattr(event, "position"):
                pos = event.position().toPoint()
                if w.geometry().contains(pos):
                    return False
                if event.type() == QEvent.Type.MouseButtonPress:
                    self._close_inline_replace()
                else:
                    return False

        hover_bar = getattr(self, "hover_bar", None)
        if hover_bar and hover_bar.isVisible() and hasattr(event, "position"):
            pos = event.position().toPoint()
            if hover_bar.geometry().contains(pos):
                hover_bar._hide_timer.stop()
                return False

        # 便签与批注悬停操作条
        if (event.type() == QEvent.Type.MouseMove and not self.stroke
                and kind in ("browse", "select") and not self.host._scrolling):
            try:
                pos = event.position().toPoint()
                if hover_bar and hover_bar.isVisible() and hover_bar.geometry().contains(pos):
                    hover_bar._hide_timer.stop()
                else:
                    point = self.view.mapToScene(pos)
                    index = self.view.page_at_point(point)
                    if 0 <= index < len(self.view.page_rects) and self.view.page_rects[index].contains(point):
                        if self.ensure_annotation_summary(index):
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

        # Single click keeps one popover; editing is an explicit action or double click.
        if kind in ("browse", "select") and event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            if event.button() == Qt.MouseButton.LeftButton and self.host.ws.pages and not self._move_state:
                point = self.view.mapToScene(event.position().toPoint())
                index = self.view.page_at_point(point)
                if 0 <= index < len(self.view.page_rects) and self.view.page_rects[index].contains(point):
                    if self.ensure_annotation_summary(index):
                        hit_annot = self.find_annotation_at(point)
                        if hit_annot and (hit_annot.get("kind") == 0 or hit_annot.get("content")):
                            self.annotations.setCurrentIndex(self.annotations.findData(hit_annot["id"]))
                            if event.type() == QEvent.Type.MouseButtonDblClick:
                                self.hover_bar.hide()
                                self.open_note_dialog(hit_annot["id"])
                            else:
                                self.hover_bar.show_for_annot(hit_annot, index)
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
                index = self.view.page_at_point(point)
                if not 0 <= index < len(self.view.page_rects) or not self.view.page_rects[index].contains(point):
                    return True
                # 落笔页就是这次批注的归属页。连续阅读时上一页／下一页的可见部分
                # 也能直接标注，所以不再为了落笔去切换当前页（切页会打断阅读位置）。
                pressed = bool(QApplication.mouseButtons() & Qt.MouseButton.LeftButton)
                if not self.ensure_snapshot(index, geometry_only=kind not in ("highlight", "underline", "replace")):
                    return True
                if pressed and not QApplication.mouseButtons() & Qt.MouseButton.LeftButton:
                    self._set_feedback("本页批注已准备好，请重新点击或拖动以添加批注。")
                    return True
                self.stroke = [point]
                self.stroke_page = (index, self.host.ws.pages[index], self.snapshot, self.view.layout_generation)
                if kind == "mosaic":
                    self.view.setFocus(Qt.FocusReason.MouseFocusReason)
                if kind == "select":
                    self.select_at(point)
                    self.stroke = []
                    self.stroke_page = None
                    return True
                self.overlay = QGraphicsPathItem()
                self.overlay.setZValue(4)
                self.overlay.setPen(QPen(self.color, self.width.value(), Qt.PenStyle.SolidLine,
                                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                if kind == "mosaic":
                    pen = QPen(QColor("#0F766E"), 1.5, Qt.PenStyle.DashLine)
                    pen.setCosmetic(True)
                    self.overlay.setPen(pen)
                    self.overlay.setBrush(QColor(15, 118, 110, 35))
                if kind == "arrow":
                    self.overlay.setBrush(self.color)
                self.view._scene.addItem(self.overlay)
                return True
            if not self.stroke:
                return False
            point = self.view.mapToScene(event.position().toPoint())
            owner = self.stroke_page[0] if self.stroke_page else self.host.ws.preview_index()
            if (self.stroke_page and not self._stroke_is_current(self.stroke_page)) \
                    or not 0 <= owner < len(self.view.page_rects):
                self.clear_overlay()
                self._set_feedback("所选页面已改变，已取消本次绘制。")
                return True
            bounds = self.view.page_rects[owner]
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
                    from app.pdfedits import arrow_geometry
                    if point == self.stroke[0]:
                        return True
                    left, right, shaft_end = arrow_geometry(
                        (self.stroke[0].x(), self.stroke[0].y()), (point.x(), point.y()), self.width.value())
                    path.lineTo(QPointF(*shaft_end))
                    path.moveTo(point)
                    path.lineTo(QPointF(*left))
                    path.lineTo(QPointF(*right))
                    path.closeSubpath()
                else:
                    from PyQt6.QtCore import QRectF
                    rectangle = QRectF(self.stroke[0], point).normalized()
                    path.addEllipse(rectangle) if kind == "ellipse" else path.addRect(rectangle)
                self.overlay.setPath(path)
                return True
            if event.button() != Qt.MouseButton.LeftButton:
                return False
            points = [*self.stroke, point]
            state = self.stroke_page
            self.clear_overlay()
            try:
                self.finish(kind, points, state)
            except (ValueError, PdfError) as exc:
                QMessageBox.information(self, "本次操作未应用", str(exc))
            except Exception as exc:
                self._set_feedback(f"操作未完成：{exc}")
            return True
        except Exception as exc:
            self.clear_overlay()
            self._set_feedback(f"操作异常：{exc}")
            return False

    def canonical(self, point, index=None, matrix=None):
        import pymupdf as p
        state = self.stroke_page
        if index is None:
            index = state[0] if state else self.snapshot_index
        if matrix is None:
            snapshot = state[2] if state and state[0] == index else self.snapshot
            matrix = snapshot["matrix"]
        top = self.view.page_rects[index].topLeft()
        return p.Point(point.x() - top.x(), point.y() - top.y()) * ~p.Matrix(matrix)

    def select_at(self, point):
        import pymupdf as p
        local = self.canonical(point)
        for item in reversed(self.snapshot["annotations"]):
            if item.get('image_settings') or item.get("watermark"):
                continue
            if p.Rect(item["rect"]).contains(local):
                self.annotations.setCurrentIndex(self.annotations.findData(item["id"]))
                return
        self.annotations.setCurrentIndex(0)

    def _close_inline_replace(self):
        replace_state = getattr(self, "_inline_replace", None)
        if replace_state:
            for widget in (replace_state["widget"], replace_state["actions"]):
                widget.hide()
                widget.deleteLater()
            self._inline_replace = None

    def _position_inline_replace(self):
        import pymupdf as p
        state = getattr(self, "_inline_replace", None)
        if not state:
            return
        self.sync_page()
        if getattr(self, "_inline_replace", None) is not state:
            return
        index = state["index"]
        if index >= len(self.view.page_rects):
            return
        matrix = p.Matrix(state["matrix"])
        local_rect = state["area"] * matrix
        page_top = self.view.page_rects[index].topLeft()
        scene_rect = QRectF(page_top.x() + local_rect.x0, page_top.y() + local_rect.y0,
                            local_rect.width, local_rect.height)
        rect = self.view.mapFromScene(scene_rect).boundingRect()
        viewport = self.view.viewport()
        if not viewport.rect().intersects(rect):
            state["widget"].hide()
            state["actions"].hide()
            return

        span = state["span"]
        pixels = max(8, round(span.get("size", 12) * math.hypot(matrix.a, matrix.b) * self.view.transform().m11()))
        font = QFont(self.view.font())
        font.setPixelSize(pixels)
        font.setBold(bool(span.get("flags", 0) & 16))
        font.setItalic(bool(span.get("flags", 0) & 2))
        edit_input = state["input"]
        edit_input.setFont(font)
        color = QColor.fromRgb(span.get("color", 0)).name()
        edit_input.setStyleSheet(
            f"QLineEdit {{ min-height: 0; padding: 0; border: none; border-radius: 0; background: #FFFFFF; "
            f"font-size: {pixels}px; font-weight: {700 if font.bold() else 400}; "
            f"font-style: {'italic' if font.italic() else 'normal'}; color: {color}; "
            "selection-background-color: #CCFBF1; selection-color: #134E4A; }"
        )
        metrics = QFontMetrics(font)
        text_width = metrics.horizontalAdvance(edit_input.text()) + 14
        page_right = self.view.mapFromScene(self.view.page_rects[index].topRight()).x()
        width = max(rect.width() + 10, min(text_width, max(72, page_right - rect.left())))
        height = max(28, metrics.height() + 6)
        widget, actions = state["widget"], state["actions"]
        widget.setGeometry(rect.left() - 5, round(rect.center().y() - height / 2), width, height)
        action_x = max(4, min(widget.geometry().right() - actions.width(), viewport.width() - actions.width() - 4))
        action_y = widget.geometry().bottom() + 4
        if action_y + actions.height() > viewport.height() - 4:
            action_y = widget.geometry().top() - actions.height() - 4
        actions.move(action_x, max(4, action_y))
        widget.show()
        actions.show()
        widget.raise_()
        actions.raise_()

    def _start_inline_replace(self, ref, area, original, span=None, index=None, matrix=None):
        self._close_inline_replace()
        self.hover_bar.hide()
        if index is None:
            index = self.host.ws.preview_index()
        if matrix is None:
            matrix = self.snapshot["matrix"]
        if index is None or index >= len(self.view.page_rects):
            return
        container = QFrame(self.view.viewport())
        container.setObjectName("inlineReplace")
        container.setStyleSheet(
            "QFrame#inlineReplace { background: #FFFFFF; border: 1px solid #0F766E; border-radius: 3px; }"
        )
        layout = QHBoxLayout(container)
        layout.setContentsMargins(5, 1, 5, 1)
        layout.setSpacing(0)

        edit_input = QLineEdit(original, container)
        edit_input.setAccessibleName("原处文字修改")
        edit_input.setToolTip("Enter 应用修改 · Esc 取消")
        edit_input.installEventFilter(self)
        layout.addWidget(edit_input, 1)

        actions = QFrame(self.view.viewport())
        actions.setObjectName("inlineReplaceActions")
        set_theme_style(actions,
            "QFrame#inlineReplaceActions { background: #FFFFFF; border: 1px solid #D7E8E4; border-radius: 6px; } "
            "QToolButton { min-width: 24px; min-height: 24px; padding: 0; border-radius: 4px; } "
            "QToolButton#apply { background: #0F766E; border: none; } "
            "QToolButton#apply:hover { background: #0D9488; }"
        )
        actions.setFixedSize(60, 32)
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(3, 3, 3, 3)
        action_layout.setSpacing(2)
        apply_btn = QToolButton(actions)
        apply_btn.setObjectName("apply")
        apply_btn.setFixedSize(26, 26)
        apply_btn.setIcon(toolbar_icon("check", "#FFFFFF", 16))
        apply_btn.setIconSize(QSize(16, 16))
        apply_btn.setAccessibleName("应用文字修改")
        apply_btn.setToolTip("应用修改 (Enter)")
        action_layout.addWidget(apply_btn)

        cancel_btn = QToolButton(actions)
        cancel_btn.setObjectName("cancel")
        cancel_btn.setFixedSize(26, 26)
        cancel_btn.setIcon(toolbar_icon("x", "#4D6B67", 16))
        cancel_btn.setIconSize(QSize(16, 16))
        cancel_btn.setAccessibleName("取消文字修改")
        cancel_btn.setToolTip("取消 (Esc)")
        action_layout.addWidget(cancel_btn)

        self._inline_replace = {
            "widget": container,
            "actions": actions,
            "input": edit_input,
            "ref": ref,
            "index": index,
            "active_index": self.host.ws.preview_index(),
            "matrix": matrix,
            "span": span or {},
            "area": area,
            "original": original,
        }
        self._position_inline_replace()
        edit_input.setFocus()
        edit_input.selectAll()
        edit_input.textChanged.connect(self._position_inline_replace)

        def commit():
            text = edit_input.text()
            if text == original:
                self._close_inline_replace()
                return
            if not text.strip():
                self._set_feedback("请输入一行非空文字；Esc 可取消修改。")
                edit_input.setFocus()
                return
            if index >= len(self.host.ws.pages) or self.host.ws.pages[index] is not ref:
                self._close_inline_replace()
                self._set_feedback("页面已改变，请重新选择需要修改的文字。")
                return
            try:
                replacement_edit = run_operation(
                    self.host, "检查文字修正",
                    lambda progress: self.host.store.prepare_replacement(ref, area, text)
                )
                if self.commit(replacement_edit, page_index=index, expected_ref=ref):
                    self._close_inline_replace()
                    if dict(replacement_edit.details).get("font_fallback"):
                        self._set_feedback("文字已修改；未找到可用的完整原字体，已使用内置替代字体。Ctrl+Z 撤销，Ctrl+S 保存。")
                    else:
                        self._set_feedback("文字修改已应用；Ctrl+Z 撤销，Ctrl+S 保存。")
            except (ValueError, PdfError) as exc:
                QMessageBox.information(self, "修改未应用", str(exc))
                edit_input.setFocus()
            except Exception as exc:
                self._set_feedback(f"修改未应用：{exc}")

        def cancel():
            self._close_inline_replace()

        edit_input.returnPressed.connect(commit)
        apply_btn.clicked.connect(commit)
        cancel_btn.clicked.connect(cancel)

    def finish(self, kind, points, state=None):
        import pymupdf as p
        from app.pdfedits import selection_quads
        pages = self.host.ws.pages
        if state is None:
            state = self.stroke_page
        if state is not None:
            # 以落笔页为归属页：拖拽过程中「当前页」怎么变都不影响本次批注。
            index, ref, snapshot, _ = state
            if not self._stroke_is_current(state):
                raise ValueError("所选页面已改变，请重新标注。")
        else:
            index = self.host.ws.preview_index()
            ref = pages[index]
            snapshot = self.snapshot
            if ref.render_key != self.snapshot_key:
                raise ValueError("当前页已改变，请重新操作。")
        matrix = snapshot["matrix"]
        canonical = [self.canonical(pos, index, matrix) for pos in points]
        area = p.Rect(canonical[0], canonical[-1]).normalize()
        color = (self.color.redF(), self.color.greenF(), self.color.blueF())
        identifier = "zhixu-" + uuid.uuid4().hex
        if kind == "mosaic":
            if area.width < 2 or area.height < 2:
                raise ValueError("请拖出一个马赛克区域，不能只点击页面。")
            if not self.host._allow_operation("content_edit"):
                return
            from app.mosaicui import MosaicDialog
            dialog = MosaicDialog(self, index, ref, area, self.mosaic_settings)
            dialog.exec()
            dialog.deleteLater()
            return
        if kind == "freetext":
            visible = self.view.page_rects[index]
            if area.width < 16 or area.height < 8:
                start = points[0] - visible.topLeft()
                area = p.Rect(start.x(), start.y(), min(start.x() + 220, visible.width()),
                              min(start.y() + 48, visible.height())) * ~p.Matrix(matrix)
            if area.width < 16 or area.height < 8:
                raise ValueError("请在页面内留出文本框空间。")
            self.start_text_box(area, index, ref)
            return
        if kind == "note":
            text, accepted = QInputDialog.getMultiLineText(self, "添加备注", "备注内容")
            if not accepted or not text.strip():
                return
            edit = PageEdit("note", (tuple(canonical[0]),), text=text, color=color, annotation_id=identifier)
        elif kind == "replace":
            from app.pdfedits import selected_lines
            raw_data = snapshot.get("raw") if isinstance(snapshot, dict) else {}
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
            if len(selected) != 1:
                raise ValueError("一次只能修改一行文字，请缩小选择范围。")
            chars = selected[0][1]
            original = "".join(char["c"] for char, _ in chars)
            if not original:
                raise ValueError("选区没有可修改的文字。")
            line_area = p.Rect(chars[0][0]["bbox"])
            for char_item, _ in chars[1:]:
                line_area |= p.Rect(char_item["bbox"])
            self._start_inline_replace(ref, line_area, original, chars[0][1], index=index, matrix=matrix)
            return
        elif kind in ("highlight", "underline"):
            quads = selection_quads(snapshot["raw"], area)
            if not quads:
                raise ValueError("选区没有文字层；扫描图片可以用画笔或便签标注。")
            edit = PageEdit(kind, quads, color=color, width=self.width.value(), annotation_id=identifier)
        elif kind in ("rect", "ellipse"):
            edit = PageEdit(kind, tuple(area), color=color, width=self.width.value(), annotation_id=identifier)
        else:
            chosen = canonical if kind == "ink" else [canonical[0], canonical[-1]]
            edit = PageEdit(kind, tuple(tuple(pos) for pos in chosen), color=color, width=self.width.value(), annotation_id=identifier)
        self.commit(edit, confirm=kind == "replace", page_index=index, expected_ref=ref)

    def commit(self, edit, *, confirm=False, page_index=None, expected_ref=None, on_error=None, prepared_preview=None):
        if getattr(self.host, "_task_dialog", None) is not None:
            self._set_feedback("正在处理本次操作，请稍候。")
            return False
        index = self.host.ws.preview_index() if page_index is None else page_index
        if index is None or not 0 <= index < len(self.host.ws.pages) or not self.host._allow_operation(edit.operation):
            return False
        ref = self.host.ws.pages[index]
        if expected_ref is not None and ref is not expected_ref:
            message = "页面已改变，请关闭预览并重新选择操作区域。"
            self._set_feedback(message)
            if on_error:
                on_error(message)
            return False
        candidate = ref.edited(edit) if prepared_preview is None else prepared_preview[0]
        started = perf_counter()
        try:
            if prepared_preview is None:
                width = self.host._preview_render_width()
                image = run_operation(self.host, "预览本次修改", lambda progress: self.host.store.render_qimage(candidate, width))
            else:
                image = prepared_preview[1]
            self.host.rendering.diagnostics.record("annotation_preview",
                duration_ms=round((perf_counter() - started) * 1000, 2), prepared=prepared_preview is not None)
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
                    return False
            if index >= len(self.host.ws.pages) or self.host.ws.pages[index] is not ref:
                raise ValueError("工作区已改变，请重新应用修改。")
            cached = perf_counter()
            self.host._cache_page_image(candidate.render_key, image)
            self.host.rendering.diagnostics.record("annotation_cache",
                duration_ms=round((perf_counter() - cached) * 1000, 2))
            applied = perf_counter()
            self.host.ws.edit_page(index, edit, "马赛克" if edit.kind == "mosaic" else "文字修正" if edit.kind == "replace" else "修改批注", prepared=candidate)
            self.host.rendering.diagnostics.record("annotation_apply",
                duration_ms=round((perf_counter() - applied) * 1000, 2))
            self.snapshot_key = None
            refreshed = perf_counter()
            self.host.refresh()
            self.host.rendering.diagnostics.record("annotation_refresh",
                duration_ms=round((perf_counter() - refreshed) * 1000, 2))
            self.ensure_snapshot(geometry_only=True)
            where = f"第 {index + 1} 页" if index != self.host.ws.preview_index() else ""
            self._set_feedback(f"{where}修改已应用；Ctrl+Z 撤销，Ctrl+S 保存。")
            return True
        except (ValueError, PdfError) as exc:
            if on_error:
                on_error(str(exc))
            else:
                QMessageBox.information(self, "本次操作未应用", str(exc))
            return False

    def delete_annot(self, identifier: str):
        if identifier:
            self.commit(PageEdit("delete", annotation_id=identifier), page_index=self.snapshot_index)

    def apply_style_to(self, identifier: str):
        annot = next((a for a in self.snapshot.get("annotations", ()) if a["id"] == identifier), None) if self.snapshot else None
        if annot and annot.get("kind") == 2:
            self.open_note_dialog(identifier)
            return
        if annot and annot.get("watermark"):
            self._set_feedback("水印请在「组织 → 水印」中调整。")
            return
        if identifier:
            self.commit(PageEdit("style", annotation_id=identifier,
                                 color=(self.color.redF(), self.color.greenF(), self.color.blueF()),
                                 width=self.width.value()), page_index=self.snapshot_index)

    def move_annot_to(self, identifier: str, canonical_pt, *, page_index=None, expected_ref=None):
        if identifier:
            return self.commit(PageEdit("move", points=(tuple(canonical_pt),), annotation_id=identifier),
                               page_index=self.snapshot_index if page_index is None else page_index,
                               expected_ref=expected_ref)

    def start_move_annot(self, identifier: str, *, page_index=None, anchor=None):
        import pymupdf as p
        if self._move_committing:
            return False
        index = self.snapshot_index if page_index is None else page_index
        if index is None:
            index = self.host.ws.preview_index()
        if not self.host._allow_operation("annotate") or not self.ensure_snapshot(index, geometry_only=True):
            return False
        annot = next((a for a in self.snapshot["annotations"] if a["id"] == identifier), None)
        if not annot or annot["kind"] not in (0, 2, 4):
            self._set_feedback("当前批注不支持移动。")
            return False
        self.cancel_move_annot()
        matrix = p.Matrix(self.snapshot["matrix"])
        rect = p.Rect(annot["rect"])
        page = self.view.page_rects[index]
        bounds = p.Rect(0, 0, page.width(), page.height()) * ~matrix
        if rect.width > bounds.width or rect.height > bounds.height:
            self._set_feedback("批注大于页面，无法在页内移动。")
            return False
        self._move_state = dict(id=identifier, index=index, active_index=self.host.ws.preview_index(),
            ref=self.host.ws.pages[index], rect=rect, matrix=matrix, bounds=bounds,
            anchor=None if anchor is None else p.Point(anchor.x() - page.x(), anchor.y() - page.y()) * ~matrix,
            target=p.Point(rect.top_left), drag_mode=self.view.dragMode())
        self._move_preview = QGraphicsPathItem()
        self._move_preview.setZValue(5)
        components = annot.get("color", ())
        color = QColor.fromRgbF(*components) if len(components) == 3 else QColor("#0F766E")
        self._move_preview.setPen(QPen(color, max(.5, annot.get("width") or 1), Qt.PenStyle.DashLine))
        self._move_preview.setOpacity(.85)
        if annot["kind"] == 0:
            fill = QColor(color)
            fill.setAlpha(45)
            self._move_preview.setBrush(fill)
        self.view._scene.addItem(self._move_preview)
        self.hover_bar._hide_timer.stop()
        self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self._set_view_cursor(Qt.CursorShape.CrossCursor)
        self._set_feedback("拖动预览位置，松开鼠标放置；Esc 取消。" if anchor is not None else
                           "移动鼠标预览位置，点击放置；Esc 取消。")
        origin = rect.top_left * matrix
        self.update_move_preview(anchor if anchor is not None else QPointF(origin.x + page.x(), origin.y + page.y()))
        if anchor is None:
            self.view.setFocus()
        return self._move_state is not None

    def update_move_preview(self, point):
        import pymupdf as p
        self.sync_page()
        state = self._move_state
        if not state:
            return None
        page = self.view.page_rects[state["index"]]
        local = p.Point(point.x() - page.x(), point.y() - page.y()) * ~state["matrix"]
        rect = state["rect"]
        target = local if state["anchor"] is None else rect.top_left + local - state["anchor"]
        bounds = state["bounds"]
        target = p.Point(max(bounds.x0, min(bounds.x1 - rect.width, target.x)),
                         max(bounds.y0, min(bounds.y1 - rect.height, target.y)))
        state["target"] = target
        moved = p.Rect(target.x, target.y, target.x + rect.width, target.y + rect.height) * state["matrix"]
        path = QPainterPath()
        path.addRect(QRectF(moved.x0 + page.x(), moved.y0 + page.y(), moved.width, moved.height))
        self._move_preview.setPath(path)
        original = rect.top_left * state["matrix"]
        destination = target * state["matrix"]
        return self.view.mapFromScene(QPointF(destination.x + page.x(), destination.y + page.y())) - self.view.mapFromScene(
            QPointF(original.x + page.x(), original.y + page.y()))

    def finish_move_annot(self, point):
        self.update_move_preview(point)
        state = self._move_state
        if not state:
            return
        self._move_committing = True
        self.hover_bar.hide()
        try:
            if abs(state["target"] - state["rect"].top_left) < .01:
                self._set_feedback("位置未改变。")
            elif self.move_annot_to(state["id"], state["target"], page_index=state["index"], expected_ref=state["ref"]):
                self._set_feedback("批注已移动；Ctrl+Z 撤销，Ctrl+S 保存。")
        except Exception as exc:
            self._set_feedback(f"移动未完成，请检查当前页面：{exc}")
        finally:
            self._move_committing = False
            self.cancel_move_annot()

    def cancel_move_annot(self):
        state = self._move_state
        self._move_state = None
        if self._move_preview is not None:
            self.view._scene.removeItem(self._move_preview)
            self._move_preview = None
        if state:
            self.view.setDragMode(state["drag_mode"])
            self._set_view_cursor(Qt.CursorShape.ArrowCursor)
            button = self.hover_bar.move_btn
            if button._dragging:
                button._drag_cancelled = True
            button._dragging = False
            button._drag_start_pos = None
            button.setDown(False)
            self.hover_bar.hide()

    def _move_view_changed(self):
        if self._move_state and self._move_state["anchor"] is not None and not self._move_committing:
            self.cancel_move_annot()
            self._set_feedback("视图已改变，移动已取消。")

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
