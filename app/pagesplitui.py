"""Small preview dialog for dividing one PDF page into two or three pages."""
from app.i18n import tr

from PyQt6.QtCore import QEvent, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QGridLayout,
                             QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget)

from app.pagegeometry import PT_PER_MM
from app.pagetoolsui import ClearComboBox, PAPER_SIZES, spin_mm


class PageSplitDialog(QDialog):
    def __init__(self, parent, image, page_size=None):
        super().__init__(parent)
        self.setWindowTitle(tr('分割单页'))
        self.setMinimumWidth(560)
        self.page_size = page_size
        self.base = QPixmap.fromImage(image).scaled(
            520, 440, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self._drag_cut = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr('将选中的一页切成新页面；拖动橙色分割线调整位置。')))
        if page_size is not None:
            layout.addWidget(QLabel(tr('原页：{p0}', p0=f'{self.size_text(page_size)}')))
        controls = QHBoxLayout()
        self.direction = QComboBox()
        self.direction.addItem(tr('左右分割'), "vertical")
        self.direction.addItem(tr('上下分割'), "horizontal")
        width, height = page_size or (image.width(), image.height())
        self.direction.setCurrentIndex(0 if width >= height else 1)
        self.direction.setAccessibleName(tr('分割方向'))
        self.parts = QComboBox()
        self.parts.addItem(tr('分成 2 页'), 2)
        self.parts.addItem(tr('分成 3 页'), 3)
        self.parts.setAccessibleName(tr('分割后页数'))
        controls.addWidget(self.direction)
        controls.addWidget(self.parts)
        layout.addLayout(controls)
        cuts = QHBoxLayout()
        self.cut1 = QSpinBox()
        self.cut1.setRange(5, 95)
        self.cut1.setValue(50)
        self.cut1.setSuffix("%")
        self.cut1.setAccessibleName(tr('第一条分割线的位置'))
        self.cut2 = QSpinBox()
        self.cut2.setRange(10, 95)
        self.cut2.setValue(67)
        self.cut2.setSuffix("%")
        self.cut2.setAccessibleName(tr('第二条分割线的位置'))
        cuts.addWidget(QLabel(tr('分割线位置')))
        cuts.addWidget(self.cut1)
        self.second_label = QLabel(tr('第二条'))
        cuts.addWidget(self.second_label)
        cuts.addWidget(self.cut2)
        cuts.addStretch()
        layout.addLayout(cuts)
        self.paper = ClearComboBox()
        self.paper.setAccessibleName(tr('分割后的纸张大小'))
        self.paper.setMaxVisibleItems(10)
        self.paper.addItem(tr('保持切后实际尺寸（不缩放）'), "original")
        for key, (name, width, height) in PAPER_SIZES.items():
            self.paper.addItem(f"{name} · {width:g} × {height:g} mm", key)
        self.paper.insertSeparator(self.paper.count())
        self.paper.addItem(tr('自定义尺寸'), "custom")
        self.orientation = ClearComboBox()
        self.orientation.setAccessibleName(tr('分割后的纸张方向'))
        self.orientation.addItem(tr('自动（按切后形状）'), "auto")
        self.orientation.addItem(tr('纵向'), "portrait")
        self.orientation.addItem(tr('横向'), "landscape")
        self.width_input, self.height_input = spin_mm(), spin_mm(297)
        self.width_input.setAccessibleName(tr('分割后页面宽度'))
        self.height_input.setAccessibleName(tr('分割后页面高度'))
        self.dimensions = QWidget()
        dimensions = QHBoxLayout(self.dimensions)
        dimensions.setContentsMargins(0, 0, 0, 0)
        dimensions.addWidget(QLabel(tr('宽')))
        dimensions.addWidget(self.width_input)
        dimensions.addWidget(QLabel(tr('高')))
        dimensions.addWidget(self.height_input)
        self.size_form = QGridLayout()
        self.size_form.setColumnStretch(1, 1)
        self.size_labels = tuple(QLabel(text) for text in ("分割后尺寸", "纸张方向", "实际宽高"))
        self.size_fields = (self.paper, self.orientation, self.dimensions)
        for row, (label, field) in enumerate(zip(self.size_labels, self.size_fields)):
            self.size_form.addWidget(label, row, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.size_form.addWidget(field, row, 1)
        layout.addLayout(self.size_form)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(self.base.height() + 12)
        self.preview.setAccessibleName(tr('页面分割线预览'))
        self.preview.setToolTip(tr('拖动橙色分割线调整位置，也可输入百分比。'))
        self.preview.setMouseTracking(True)
        self.preview.installEventFilter(self)
        layout.addWidget(self.preview)
        self.result_summary = QLabel()
        self.result_summary.setWordWrap(True)
        self.result_summary.setAccessibleName(tr('分割后的页面尺寸'))
        layout.addWidget(self.result_summary)
        self.size_hint = QLabel()
        self.size_hint.setWordWrap(True)
        layout.addWidget(self.size_hint)
        hint = QLabel(tr('可撤销。原有批注和表单保留可见外观，链接与表单不再可点击或填写；原 PDF 在保存前不变。'))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr('分割页面'))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr('取消'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.direction.currentIndexChanged.connect(self.update_preview)
        self.parts.currentIndexChanged.connect(self._parts_changed)
        self.cut1.valueChanged.connect(self.update_preview)
        self.cut2.valueChanged.connect(self.update_preview)
        self.paper.currentIndexChanged.connect(self.update_preview)
        self.orientation.currentIndexChanged.connect(self.update_preview)
        self.width_input.valueChanged.connect(self.update_preview)
        self.height_input.valueChanged.connect(self.update_preview)
        self._parts_changed()

    @property
    def cuts(self):
        return ((self.cut1.value(), self.cut2.value()) if self.parts.currentData() == 3
                else (self.cut1.value(),))

    @staticmethod
    def size_text(size):
        width, height = (value / PT_PER_MM for value in size)
        ordered = sorted((width, height))
        match = next(((name, w, h) for name, w, h in PAPER_SIZES.values()
                      if abs(ordered[0] - w) <= .75 and abs(ordered[1] - h) <= .75), None)
        name = match[0] if match else ""
        if match and max(abs(ordered[0] - match[1]), abs(ordered[1] - match[2])) > .1:
            name = f"约 {name}"
        shape = "横向" if width > height else "纵向"
        prefix = f"{name}（{shape}）· " if name else ""
        return f"{prefix}{width:.1f} × {height:.1f} mm"

    def target_size(self):
        preset = self.paper.currentData()
        if preset == "original":
            return None
        if preset == "custom":
            return self.width_input.value() * PT_PER_MM, self.height_input.value() * PT_PER_MM
        _, width, height = PAPER_SIZES[preset]
        if self.orientation.currentData() == "landscape":
            width, height = height, width
        return width * PT_PER_MM, height * PT_PER_MM

    @property
    def auto_orient(self):
        return self.paper.currentData() in PAPER_SIZES and self.orientation.currentData() == "auto"

    def eventFilter(self, watched, event):
        if watched is not self.preview:
            return super().eventFilter(watched, event)
        kind = event.type()
        if kind == QEvent.Type.Leave and self._drag_cut is None:
            self.preview.unsetCursor()
        if kind not in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove,
                        QEvent.Type.MouseButtonRelease):
            return super().eventFilter(watched, event)

        size = self.base.deviceIndependentSize()
        page = QRectF(0, 0, size.width(), size.height())
        page.moveCenter(QRectF(self.preview.contentsRect()).center())
        vertical = self.direction.currentData() == "vertical"
        origin = page.left() if vertical else page.top()
        pixels = self.base.width() - 1 if vertical else self.base.height() - 1
        scale = self.base.devicePixelRatioF()
        span = pixels / scale
        if span <= 0:
            return super().eventFilter(watched, event)
        coordinate = event.position().x() if vertical else event.position().y()
        handled = False
        if self._drag_cut is not None and (
                kind == QEvent.Type.MouseMove or
                kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton):
            control, offset = self._drag_cut
            control.setValue(round((coordinate - origin - offset) / span * 100))
            handled = True
            if kind == QEvent.Type.MouseButtonRelease:
                self._drag_cut = None

        positions = [origin + round(pixels * value / 100) / scale for value in self.cuts]
        nearest = min(range(len(positions)), key=lambda i: abs(coordinate - positions[i]))
        hit = page.contains(event.position()) and abs(coordinate - positions[nearest]) <= 10
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton and hit:
            control = (self.cut1, self.cut2)[nearest]
            self._drag_cut = (control, coordinate - positions[nearest])
            control.setFocus(Qt.FocusReason.MouseFocusReason)
            handled = True
        if self._drag_cut is not None or hit:
            self.preview.setCursor(Qt.CursorShape.SizeHorCursor if vertical else Qt.CursorShape.SizeVerCursor)
        else:
            self.preview.unsetCursor()
        return handled or super().eventFilter(watched, event)

    def _parts_changed(self):
        triple = self.parts.currentData() == 3
        self.second_label.setVisible(triple)
        self.cut2.setVisible(triple)
        self.cut1.blockSignals(True)
        self.cut2.blockSignals(True)
        self.cut1.setRange(5, 95)
        self.cut2.setRange(10, 95)
        self.cut1.setValue(33 if triple else 50)
        self.cut2.setValue(67)
        self.cut1.blockSignals(False)
        self.cut2.blockSignals(False)
        self.update_preview()

    def update_preview(self):
        triple = self.parts.currentData() == 3
        if triple:
            self.cut1.setMaximum(self.cut2.value() - 5)
            self.cut2.setMinimum(self.cut1.value() + 5)
        else:
            self.cut1.setMaximum(95)
        pixmap = QPixmap(self.base)
        painter = QPainter(pixmap)
        painter.setPen(QPen(QColor("#D97706"), 3))
        for value in self.cuts:
            ratio = value / 100
            if self.direction.currentData() == "vertical":
                x = round((pixmap.width() - 1) * ratio)
                painter.drawLine(x, 0, x, pixmap.height() - 1)
            else:
                y = round((pixmap.height() - 1) * ratio)
                painter.drawLine(0, y, pixmap.width() - 1, y)
        painter.end()
        self.preview.setPixmap(pixmap)
        preset = self.paper.currentData()
        for row, visible in ((1, preset in PAPER_SIZES), (2, preset == "custom")):
            self.size_labels[row].setVisible(visible)
            self.size_fields[row].setVisible(visible)
        target_size = self.target_size()
        self.size_hint.setText(
            tr('保持内容实际大小，不缩放；打印时选择“实际大小”或“100%”。') if target_size is None
            else tr('每部分内容等比缩放、居中到所选纸张，可能留白。'))
        self.result_summary.setVisible(self.page_size is not None)
        if self.page_size is not None:
            width, height = self.page_size
            vertical = self.direction.currentData() == "vertical"
            stops = (0, *self.cuts, 100)
            sizes = []
            for start, end in zip(stops, stops[1:]):
                fraction = (end - start) / 100
                content = (width * fraction, height) if vertical else (width, height * fraction)
                size = target_size or content
                if self.auto_orient and (size[0] > size[1]) != (content[0] > content[1]):
                    size = size[::-1]
                sizes.append(self.size_text(size))
            text = (f"分割后：{len(sizes)} 张 · 每张 {sizes[0]}" if len(set(sizes)) == 1
                    else "分割后：" + "；".join(f"第 {i} 张 {size}" for i, size in enumerate(sizes, 1)))
            self.result_summary.setText(text)
