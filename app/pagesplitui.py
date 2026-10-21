"""Small preview dialog for dividing one PDF page into two or three pages."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QHBoxLayout, QLabel, QSpinBox, QVBoxLayout)


class PageSplitDialog(QDialog):
    def __init__(self, parent, image):
        super().__init__(parent)
        self.setWindowTitle("分割单页")
        self.setMinimumWidth(560)
        self.base = QPixmap.fromImage(image).scaled(
            520, 440, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("将选中的一页切成新页面；拖动排序后保存。"))
        controls = QHBoxLayout()
        self.direction = QComboBox()
        self.direction.addItem("左右分割", "vertical")
        self.direction.addItem("上下分割", "horizontal")
        self.direction.setAccessibleName("分割方向")
        self.parts = QComboBox()
        self.parts.addItem("分成 2 页", 2)
        self.parts.addItem("分成 3 页", 3)
        self.parts.setAccessibleName("分割后页数")
        controls.addWidget(self.direction)
        controls.addWidget(self.parts)
        layout.addLayout(controls)
        cuts = QHBoxLayout()
        self.cut1 = QSpinBox()
        self.cut1.setRange(5, 95)
        self.cut1.setValue(50)
        self.cut1.setSuffix("%")
        self.cut1.setAccessibleName("第一条分割线的位置")
        self.cut2 = QSpinBox()
        self.cut2.setRange(10, 95)
        self.cut2.setValue(67)
        self.cut2.setSuffix("%")
        self.cut2.setAccessibleName("第二条分割线的位置")
        cuts.addWidget(QLabel("分割线位置"))
        cuts.addWidget(self.cut1)
        self.second_label = QLabel("第二条")
        cuts.addWidget(self.second_label)
        cuts.addWidget(self.cut2)
        cuts.addStretch()
        layout.addLayout(cuts)
        self.fit_a4 = QCheckBox("适配 A4（等比缩放，可能留白）")
        layout.addWidget(self.fit_a4)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(self.base.height() + 12)
        self.preview.setAccessibleName("页面分割线预览")
        layout.addWidget(self.preview)
        hint = QLabel("可撤销。原有批注和表单保留可见外观，链接与表单不再可点击或填写；原 PDF 在保存前不变。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("分割页面")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.direction.currentIndexChanged.connect(self.update_preview)
        self.parts.currentIndexChanged.connect(self._parts_changed)
        self.cut1.valueChanged.connect(self.update_preview)
        self.cut2.valueChanged.connect(self.update_preview)
        self._parts_changed()

    @property
    def cuts(self):
        return ((self.cut1.value(), self.cut2.value()) if self.parts.currentData() == 3
                else (self.cut1.value(),))

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
