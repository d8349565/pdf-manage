"""Page size controls and a shared, bounded preview for page tools."""
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                             QFormLayout, QHBoxLayout, QLabel, QLineEdit, QSpinBox,
                             QStyle, QStyleOptionComboBox, QStyleOptionSpinBox,
                             QVBoxLayout, QWidget)

from app.pagegeometry import PT_PER_MM
from app.pagepreviewlist import ScrollablePagePreview, draw_fitted_page
from app.workspace import parse_page_range


class ClearComboBox(QComboBox):
    """Keep an explicit dropdown indicator with the application's stylesheet."""
    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()

    def paintEvent(self, event):
        super().paintEvent(event)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        area = self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                          QStyle.SubControl.SC_ComboBoxArrow, self)
        center = area.center()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#3B6654' if self.isEnabled() else '#9BAAA1'), 1.8))
        painter.drawPolyline(QPolygonF([QPointF(center.x() - 4, center.y() - 2),
            QPointF(center.x(), center.y() + 2), QPointF(center.x() + 4, center.y() - 2)]))


def _draw_spin_controls(widget):
    option = QStyleOptionSpinBox()
    widget.initStyleOption(option)
    painter = QPainter(widget)
    painter.setPen(QPen(QColor('#3B6654' if widget.isEnabled() else '#9BAAA1'), 1.4))
    for control, plus in ((QStyle.SubControl.SC_SpinBoxUp, True), (QStyle.SubControl.SC_SpinBoxDown, False)):
        area = widget.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option, control, widget)
        center = area.center()
        painter.drawLine(center.x() - 3, center.y(), center.x() + 3, center.y())
        if plus:
            painter.drawLine(center.x(), center.y() - 3, center.x(), center.y() + 3)


class ClearSpinBox(QSpinBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        _draw_spin_controls(self)


class ClearDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()

    def paintEvent(self, event):
        super().paintEvent(event)
        _draw_spin_controls(self)


class PageScope(QWidget):
    def __init__(self, pages, selected, parent=None):
        super().__init__(parent)
        self.pages, self.selected = pages, selected
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.mode = ClearComboBox()
        if selected:
            self.mode.addItem(f'所选页面（{len(selected)} 页）', 'selected')
        self.mode.addItem(f'全部页面（{len(pages)} 页）', 'all')
        self.mode.addItem('指定页码', 'range')
        self.mode.setAccessibleName('处理页面范围')
        self.range = QLineEdit()
        self.range.setPlaceholderText('例如 1-3，5')
        self.range.setAccessibleName('指定页码')
        layout.addWidget(self.mode)
        layout.addWidget(self.range)
        self.mode.currentIndexChanged.connect(self.sync)
        self.sync()

    def sync(self):
        self.range.setEnabled(self.mode.currentData() == 'range')
        self.range.setVisible(self.mode.currentData() == 'range')

    def indices(self):
        mode = self.mode.currentData()
        if mode == 'selected':
            return list(self.selected)
        if mode == 'all':
            return list(range(len(self.pages)))
        return sorted(set(parse_page_range(self.range.text(), len(self.pages))))


def spin_mm(value=210):
    spin = ClearDoubleSpinBox()
    spin.setRange(3.53, 5080)
    spin.setDecimals(2)
    spin.setSuffix(' mm')
    spin.setValue(value)
    return spin


class PageSizeDialog(QDialog):
    def __init__(self, host, image, selected=None):
        super().__init__(host)
        self.host, self.pages = host, tuple(host.ws.pages)
        self.setWindowTitle('调整页面大小')
        layout = QVBoxLayout(self)
        selected = host.ws.target_indices() if selected is None else selected
        self.scope = PageScope(self.pages, selected)
        form = QFormLayout()
        form.addRow('处理范围', self.scope)
        self.preset = ClearComboBox()
        for text, value in (('匹配参考页', 'reference'), ('A4', 'a4'), ('A3', 'a3'), ('自定义尺寸', 'custom')):
            self.preset.addItem(text, value)
        form.addRow('目标尺寸', self.preset)
        self.reference = ClearSpinBox()
        self.reference.setRange(0, len(self.pages))
        self.reference.setSpecialValueText('请选择参考页')
        self.reference.setPrefix('第 ')
        self.reference.setSuffix(' 页')
        chosen = selected
        self.reference.setValue(min(chosen) if chosen and min(chosen) > 0 else 0)
        form.addRow('参考页面', self.reference)
        self.orientation = ClearComboBox()
        self.orientation.addItem('纵向', False)
        self.orientation.addItem('横向', True)
        form.addRow('纸张方向', self.orientation)
        sizes = QHBoxLayout()
        self.width_input, self.height_input = spin_mm(), spin_mm(297)
        self.width_input.setAccessibleName('目标页面宽度')
        self.height_input.setAccessibleName('目标页面高度')
        sizes.addWidget(QLabel('宽'))
        sizes.addWidget(self.width_input)
        sizes.addWidget(QLabel('高'))
        sizes.addWidget(self.height_input)
        form.addRow('实际宽高', sizes)
        self.form = form
        body = QHBoxLayout()
        form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        body.addLayout(form)
        self.preview = ScrollablePagePreview(host.store, self.pages, paper_size=self.preview_size,
                                             draw_content=self.draw_preview)
        body.addWidget(self.preview, 1)
        layout.addLayout(body)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        hint = QLabel('滚轮查看上下页。只处理所选范围，内容等比居中，可能留白；可撤销，保存时另存副本。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('应用到页面')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        for control in (self.preset, self.orientation, self.scope.mode):
            control.currentIndexChanged.connect(self.sync)
        for control in (self.reference, self.width_input, self.height_input):
            control.valueChanged.connect(self.sync)
        self.scope.range.textChanged.connect(self.sync)
        self.sync()

    def target_size(self):
        preset = self.preset.currentData()
        if preset == 'reference':
            if not self.reference.value():
                raise ValueError('请选择用来匹配尺寸的参考页。')
            return self.host.store.page_size(self.pages[self.reference.value() - 1])
        if preset in ('a4', 'a3'):
            size = (210, 297) if preset == 'a4' else (297, 420)
            if self.orientation.currentData():
                size = size[::-1]
            return tuple(value * PT_PER_MM for value in size)
        return self.width_input.value() * PT_PER_MM, self.height_input.value() * PT_PER_MM

    def sync(self, *_):
        preset = self.preset.currentData()
        self.form.setRowVisible(2, preset == 'reference')
        self.form.setRowVisible(3, preset in ('a4', 'a3'))
        self.form.setRowVisible(4, preset == 'custom')
        self.reference.setEnabled(preset == 'reference')
        self.orientation.setEnabled(preset in ('a4', 'a3'))
        self.width_input.setEnabled(preset == 'custom')
        self.height_input.setEnabled(preset == 'custom')
        try:
            indices = self.scope.indices()
            self.preview.set_rows(indices)
            size = self.target_size()
            if not indices:
                raise ValueError('请至少选择一页。')
            if preset != 'custom':
                for spin, value in zip((self.width_input, self.height_input), size):
                    spin.blockSignals(True)
                    spin.setValue(value / PT_PER_MM)
                    spin.blockSignals(False)
            self.status.setText(f'将调整 {len(indices)} 页 · {size[0] / PT_PER_MM:.2f} × {size[1] / PT_PER_MM:.2f} mm')
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
        except ValueError as exc:
            self.status.setText(str(exc))
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.preview.refresh_pages(layout=True)

    def preview_size(self, row):
        try:
            return self.target_size()
        except ValueError:
            return self.host.store.page_size(self.pages[row])

    def draw_preview(self, painter, rectangle, row, image):
        draw_fitted_page(painter, rectangle, image)

    def done(self, result):
        self.preview.shutdown()
        super().done(result)


def resize_pages(host):
    from app.output import OperationCancelled, check_cancel
    from app.pagegeometry import geometry_edit
    from app.pdfio import PdfError
    from app.progress import run_operation
    from PyQt6.QtWidgets import QMessageBox
    if not host.ws.pages or not host._allow_operation('content_edit'):
        return
    before = tuple(host.ws.pages)
    selected = host.ws.target_indices()
    try:
        dialog = PageSizeDialog(host, None, selected)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            dialog.deleteLater()
            return
        indices, size = dialog.scope.indices(), dialog.target_size()
        dialog.deleteLater()
        def prepare(progress, cancel):
            changes = {}
            for step, row in enumerate(indices):
                check_cancel(cancel)
                ref = before[row]
                candidate = ref.edited(geometry_edit(host.store, ref, size))
                with host.store._render_page(candidate, cancel):
                    pass
                changes[row] = candidate
                progress(step + 1, len(indices), f'已准备 {step + 1} / {len(indices)} 页')
            check_cancel(cancel)
            return changes
        changes = run_operation(host, '调整页面大小', prepare, cancellable=True)
        if tuple(host.ws.pages) != before:
            raise ValueError('工作区已改变，请重新选择页面。')
        host.ws.edit_pages(changes, '调整页面大小')
        host.refresh()
        host.statusBar().showMessage(f'已调整 {len(changes)} 页；Ctrl+Z 可撤销，保存时另存副本。', 6500)
    except OperationCancelled:
        host.statusBar().showMessage('已取消，页面未修改。', 5000)
    except (ValueError, PdfError) as exc:
        QMessageBox.warning(host, '无法调整页面大小', str(exc))
