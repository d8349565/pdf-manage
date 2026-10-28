"""Independent region extraction; selections never become document edits."""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QPen, QShortcut
from PyQt6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget)

from app.annotcrop import MARGIN, collect_markers, layout_flow, visible_matrix
from app.output import OperationCancelled, check_cancel
from app.pagepreviewlist import ScrollablePagePreview, draw_fitted_page
from app.pagetoolsui import ClearComboBox, PageScope
from app.pdfio import PdfError
from app.progress import run_operation
from app.themes import ui_color


class RegionPreview(ScrollablePagePreview):
    changed = pyqtSignal()

    def __init__(self, store, pages, parent=None):
        super().__init__(store, pages, parent, draw_content=self.draw_regions)
        self.regions, self.reference_regions, self.history = {}, {}, []
        self.selected = self.drag = self.draft = None
        self.editable = True
        self.click_selection = False
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        self.setAccessibleName('区域摘录预览，拖动或点击两个角画框；拖动框内移动，拖动角点调整大小')
        self.verticalScrollBar().valueChanged.connect(self.cancel_drag)

    def shown_regions(self):
        return self.regions if self.editable else self.reference_regions

    def draw_regions(self, painter, paper, row, image):
        draw_fitted_page(painter, paper, image)
        width, height = self.store.page_size(self.pages[row])
        painter.save()
        painter.setClipRect(paper)
        for index, area in enumerate(self.shown_regions().get(row, ())):
            if self.drag and self.drag[:2] == (row, index):
                continue
            rectangle = QRectF(paper.x() + area.x() * paper.width() / width,
                paper.y() + area.y() * paper.height() / height,
                area.width() * paper.width() / width, area.height() * paper.height() / height)
            painter.setPen(QPen(QColor(ui_color('#0F766E')), 2))
            painter.fillRect(rectangle, QColor(15, 118, 110, 26))
            painter.drawRect(rectangle)
            painter.drawText(rectangle.adjusted(5, 3, -3, -3), Qt.AlignmentFlag.AlignTop, str(index + 1))
            if self.editable and self.selected == (row, index):
                for corner in (rectangle.topLeft(), rectangle.topRight(), rectangle.bottomLeft(), rectangle.bottomRight()):
                    painter.fillRect(QRectF(corner.x() - 4, corner.y() - 4, 8, 8), QColor(ui_color('#0F766E')))
        if self.drag and self.drag[0] == row and self.draft is not None:
            area = self.draft
            painter.setPen(QPen(QColor(ui_color('#0F766E')), 2, Qt.PenStyle.DashLine))
            painter.drawRect(QRectF(paper.x() + area.x() * paper.width() / width,
                paper.y() + area.y() * paper.height() / height,
                area.width() * paper.width() / width, area.height() * paper.height() / height))
        painter.restore()

    def cancel_drag(self, *_):
        active = self.drag is not None
        self.drag = self.draft = None
        self.click_selection = False
        self.viewport().update()
        if active:
            self.changed.emit()

    def set_rows(self, rows, remove_group=''):
        self.cancel_drag()
        super().set_rows(rows, remove_group)

    def local_point(self, row, position):
        index = self.page_model.index(self.rows.index(row))
        paper = self.paper_rect(row, self.visualRect(index))
        width, height = self.store.page_size(self.pages[row])
        return QPointF(max(0, min(width, (position.x() - paper.x()) * width / paper.width())),
                       max(0, min(height, (position.y() - paper.y()) * height / paper.height())))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.cancel_drag()
            return
        if not self.editable or event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        if self.drag and self.click_selection:
            self.update_draft(event.position())
            return
        index = self.indexAt(event.position().toPoint())
        if not index.isValid():
            return
        row = self.rows[index.row()]
        paper = self.paper_rect(row, self.visualRect(index))
        if not paper.contains(event.position()) or self.image_key(row) not in self.images:
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        point = self.local_point(row, event.position())
        radius = 9 * self.store.page_size(self.pages[row])[0] / paper.width()
        for number in reversed(range(len(self.regions.get(row, ())))):
            area = self.regions[row][number]
            corners = ((area.topLeft(), area.bottomRight()), (area.topRight(), area.bottomLeft()),
                       (area.bottomLeft(), area.topRight()), (area.bottomRight(), area.topLeft()))
            for corner, opposite in corners:
                if (point - corner).manhattanLength() <= radius:
                    self.drag = (row, number, opposite, QRectF(area), 'resize')
                    break
            if self.drag:
                break
            if area.contains(point):
                self.drag = (row, number, point, QRectF(area), 'move')
                break
        if self.drag is None:
            self.drag = (row, None, point, QRectF(), 'new')
        self.selected = (row, self.drag[1]) if self.drag[1] is not None else None
        self.update_draft(event.position())
        self.changed.emit()

    def update_draft(self, position):
        row, _, anchor, original, mode = self.drag
        point = self.local_point(row, position)
        if mode == 'move':
            area = original.translated(point - anchor)
            width, height = self.store.page_size(self.pages[row])
            area.moveLeft(max(0, min(width - area.width(), area.left())))
            area.moveTop(max(0, min(height - area.height(), area.top())))
        else:
            area = QRectF(anchor, point).normalized()
        self.draft = area
        self.viewport().update()

    def mouseMoveEvent(self, event):
        if self.drag:
            self.update_draft(event.position())
        else:
            super().mouseMoveEvent(event)

    def remember(self):
        self.history.append({row: [QRectF(area) for area in areas] for row, areas in self.regions.items()})

    def mouseReleaseEvent(self, event):
        if not self.drag or event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        self.update_draft(event.position())
        row, number, _, original, mode = self.drag
        area = QRectF(self.draft)
        if (area.width() < 2 or area.height() < 2) and mode == 'new':
            self.click_selection = True
            return
        self.cancel_drag()
        if area.width() < 2 or area.height() < 2 or area == original:
            return
        self.remember()
        areas = self.regions.setdefault(row, [])
        if number is None:
            number = len(areas)
            areas.append(area)
        else:
            areas[number] = area
        self.selected = row, number
        self.changed.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.drag:
            self.cancel_drag()
            event.accept()
        else:
            super().keyPressEvent(event)

    def delete_selected(self):
        self.cancel_drag()
        if self.editable and self.selected:
            row, number = self.selected
            self.remember()
            self.regions[row].pop(number)
            self.selected = None
            self.changed.emit()

    def undo(self):
        if self.editable and self.history:
            self.cancel_drag()
            self.regions = self.history.pop()
            self.selected = None
            self.changed.emit()


class AnnotationCropDialog(QDialog):
    def __init__(self, host):
        super().__init__(host)
        self.host, self.pages = host, tuple(host.ws.pages)
        self.loaded, self.annotation_regions = None, {}
        self.setWindowTitle('区域摘录')
        self.setMinimumSize(820, 540)
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1120, round(screen.width() * .9)), min(800, round(screen.height() * .9)))
        outer = QVBoxLayout(self)
        instructions = QLabel('在右侧逐页画框，摘出需要的内容。拖动或点击两个角画框；拖动框内可移动，拖动角点可调整大小。')
        instructions.setWordWrap(True)
        outer.addWidget(instructions)
        body = QHBoxLayout()
        controls = QWidget()
        controls.setMaximumWidth(340)
        sidebar = QVBoxLayout(controls)
        sidebar.setContentsMargins(0, 0, 8, 0)
        self.scope = PageScope(self.pages, sorted(host.ws.selected), self)
        self.scope.mode.setCurrentIndex(self.scope.mode.findData('all'))
        form = QFormLayout()
        form.addRow('页面范围', self.scope)
        self.source = ClearComboBox()
        for label, value in (('手动画框', 'regions'), ('已有矩形批注', 'annot'), ('页边距（每边 12.7 mm）', 'margin')):
            self.source.addItem(label, value)
        self.source.setAccessibleName('摘录选区来源')
        form.addRow('选区来源', self.source)
        self.layout_mode = ClearComboBox()
        self.layout_mode.addItem('拼到 A4 上，便于打印', 'flow')
        self.layout_mode.addItem('每个区域单独一页', 'per')
        self.layout_mode.setAccessibleName('新 PDF 的排版方式')
        form.addRow('输出排版', self.layout_mode)
        sidebar.addLayout(form)
        self.expand = QCheckBox('合并同页选区')
        self.expand.setToolTip('同页的多个框合并为一个区域，会包含框之间的内容。')
        sidebar.addWidget(self.expand)
        sidebar.addWidget(QLabel('已选区域'))
        self.region_list = QListWidget()
        self.region_list.setAccessibleName('已选区域，选择后可删除或调整')
        sidebar.addWidget(self.region_list, 1)
        actions = QHBoxLayout()
        self.delete = QPushButton('删除所选')
        self.undo_button = QPushButton('撤销选区操作')
        actions.addWidget(self.delete)
        actions.addWidget(self.undo_button)
        sidebar.addLayout(actions)
        note = QLabel('选区仅用于本次导出；没画框的页面会跳过，原 PDF 保留。')
        note.setWordWrap(True)
        sidebar.addWidget(note)
        self.preview = RegionPreview(host.store, self.pages, self)
        body.addWidget(controls)
        body.addWidget(self.preview, 1)
        outer.addLayout(body, 1)
        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        outer.addWidget(self.feedback)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.start = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.start.setText('导出新 PDF…')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.scope.mode.currentIndexChanged.connect(self.sync_range)
        self.scope.range.textChanged.connect(self.sync_range)
        self.source.currentIndexChanged.connect(self.sync_range)
        self.layout_mode.currentIndexChanged.connect(self.refresh)
        self.expand.toggled.connect(self.refresh)
        self.preview.changed.connect(self.refresh)
        self.region_list.currentItemChanged.connect(self.select_region)
        self.delete.clicked.connect(self.preview.delete_selected)
        self.undo_button.clicked.connect(self.preview.undo)
        self.shortcuts = []
        for key, callback in (('Ctrl+Z', self.preview.undo), ('Delete', self.preview.delete_selected)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(callback)
            self.shortcuts.append(shortcut)
        self.sync_range()

    def indices(self):
        return self.scope.indices()

    def sync_range(self, *_):
        try:
            indices = self.indices()
            self.preview.set_rows(indices)
            self.preview.selected = None
            source = self.source.currentData()
            self.preview.editable = source == 'regions'
            if source == 'annot':
                if self.loaded != tuple(indices):
                    def read(progress, cancel):
                        import pymupdf as p
                        regions = {}
                        for number, row in enumerate(indices):
                            check_cancel(cancel)
                            bounds = p.Rect(0, 0, *self.host.store.page_size(self.pages[row]))
                            with self.host.store._render_page(self.pages[row], cancel) as page:
                                matrix = visible_matrix(page, self.pages[row])
                                areas = [(marker * matrix) & bounds for marker in collect_markers(page)]
                                regions[row] = [QRectF(area.x0, area.y0, area.width, area.height)
                                                for area in areas if area.width >= 1 and area.height >= 1]
                            progress(number + 1, len(indices), '正在读取已有矩形批注…')
                        return regions
                    self.annotation_regions = run_operation(self, '读取矩形批注', read, cancellable=True)
                    self.loaded = tuple(indices)
                self.preview.reference_regions = self.annotation_regions
            elif source == 'margin':
                self.preview.reference_regions = {}
                for row in indices:
                    width, height = self.host.store.page_size(self.pages[row])
                    if min(width, height) > 2 * MARGIN:
                        self.preview.reference_regions[row] = [QRectF(MARGIN, MARGIN, width - 2 * MARGIN, height - 2 * MARGIN)]
            self.preview.viewport().setCursor(Qt.CursorShape.CrossCursor if self.preview.editable else Qt.CursorShape.ArrowCursor)
            self.refresh()
        except (ValueError, PdfError, RuntimeError, OperationCancelled) as exc:
            self.preview.set_rows([])
            self.feedback.setText(str(exc))
            self.start.setEnabled(False)

    def refresh(self, *_):
        try:
            self.indices()
        except ValueError as exc:
            self.feedback.setText(str(exc))
            self.start.setEnabled(False)
            return
        selected = self.preview.selected
        self.region_list.blockSignals(True)
        self.region_list.clear()
        all_regions = []
        shown = self.preview.shown_regions()
        for row in self.preview.rows:
            areas = shown.get(row, ())
            for number, area in enumerate(areas):
                item = QListWidgetItem(f'第 {row + 1} 页 · 区域 {number + 1}')
                item.setData(Qt.ItemDataRole.UserRole, (row, number))
                self.region_list.addItem(item)
                if selected == (row, number):
                    self.region_list.setCurrentItem(item)
            if self.expand.isChecked() and areas:
                merged = QRectF(areas[0])
                for area in areas[1:]:
                    merged = merged.united(area)
                all_regions.append(merged)
            else:
                all_regions.extend(areas)
        self.region_list.blockSignals(False)
        self.delete.setEnabled(self.preview.editable and self.region_list.currentItem() is not None)
        self.undo_button.setEnabled(self.preview.editable and bool(self.preview.history))
        count = len(all_regions)
        if count:
            import pymupdf as p
            output_pages = len(layout_flow([p.Rect(area.getCoords()) for area in all_regions])) if self.layout_mode.currentData() == 'flow' else count
            self.feedback.setText(f'已选 {count} 个区域，预计导出 {output_pages} 页。原 PDF 保留。')
        else:
            self.feedback.setText('请在右侧需要的页面上画框；每个页面分别选择区域。' if self.preview.editable else '所选范围内没有可摘录区域。')
        if self.preview.drag:
            self.feedback.setText('请完成当前选区后导出；按 Esc 可取消当前绘制。')
        self.start.setEnabled(bool(count) and self.preview.drag is None)
        self.preview.viewport().update()

    def select_region(self, item, _):
        if item is not None:
            self.preview.selected = item.data(Qt.ItemDataRole.UserRole)
            self.preview.scroll_to_page(self.preview.selected[0])
            self.preview.viewport().update()
        self.delete.setEnabled(self.preview.editable and item is not None)

    def options(self):
        return dict(source=self.source.currentData(), layout=self.layout_mode.currentData(),
            expand=self.expand.isChecked(), range=','.join(str(index + 1) for index in self.indices()),
            regions={row: [area.getCoords() for area in areas] for row, areas in self.preview.regions.items()})

    def done(self, result):
        if result == QDialog.DialogCode.Accepted and (not self.start.isEnabled() or self.preview.drag):
            return
        self.preview.shutdown()
        super().done(result)


def open_annotation_crop(host):
    from pathlib import Path
    from PyQt6.QtWidgets import QMessageBox
    from app.annotcrop import AnnotationCropError, export_annotation_crop, needs_output_protection
    from app.exportdialogs import EncryptionDialog, new_pdf_path
    if not host.ws.pages or not host._allow_operation('copy'):
        return
    dialog = AnnotationCropDialog(host)
    try:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        pages, options = dialog.pages, dialog.options()
    finally:
        dialog.deleteLater()
    if len(host.ws.pages) != len(pages) or any(current is not before for current, before in zip(host.ws.pages, pages)):
        QMessageBox.information(host, '区域摘录未完成', '文档已改变，请重新选择区域。')
        return
    if needs_output_protection(host.store, pages):
        protection = EncryptionDialog(host, allow_plain=True)
        try:
            if protection.exec() != QDialog.DialogCode.Accepted:
                return
            options.update(encryption=protection.setting, unencrypted_confirmed=protection.setting is None)
        finally:
            protection.setting = None
            protection.deleteLater()
    stem = Path(host.ws.path or '文档').stem
    path = new_pdf_path(host, host.store, '区域摘录导出', f'{stem}_区域摘录.pdf')
    if not path:
        return
    try:
        result = run_operation(host, '区域摘录', lambda progress, cancel:
            export_annotation_crop(host.store, pages, options, path, progress, cancel), cancellable=True)
        QMessageBox.information(host, '区域摘录完成',
            f'已导出 {result["regions"]} 个区域，共 {result["out_pages"]} 页：\n{result["path"]}')
    except OperationCancelled:
        QMessageBox.information(host, '已取消', '已取消，未提交新 PDF，原文件保留。')
    except (AnnotationCropError, PdfError, OSError) as exc:
        QMessageBox.warning(host, '区域摘录未完成', str(exc))
