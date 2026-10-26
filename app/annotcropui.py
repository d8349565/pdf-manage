"""批注裁剪对话框：收集裁剪来源、排版方式与页码范围。"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QDialog, QLineEdit, QRadioButton, QVBoxLayout,
)

from app.exportdialogs import buttons, hint
from app.workspace import parse_page_range


class AnnotationCropDialog(QDialog):
    """选项对话框，出参交给 app.annotcrop.export_annotation_crop。"""

    def __init__(self, count, selected, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批注裁剪")
        self.setMinimumWidth(460)
        self.count = count
        self.selected = sorted(i for i in (selected or ()) if 0 <= i < count)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        hint(layout, "用「矩形框」批注在页面上框出要保留的区域，这里按框批量裁剪并导出为新 PDF。"
                     "标记批注不会出现在结果里，隐藏的批注会被跳过；原件不变。")

        self.all_mode = QRadioButton(f"全部页面（{count} 页）")
        self.selected_mode = QRadioButton(f"所选页面（{len(self.selected)} 页）")
        self.range_mode = QRadioButton("按页码范围")
        self.all_mode.setChecked(True)
        self.selected_mode.setEnabled(bool(self.selected))
        self.range_group = QButtonGroup(self)
        for radio in (self.all_mode, self.selected_mode, self.range_mode):
            layout.addWidget(radio)
            self.range_group.addButton(radio)
            radio.toggled.connect(self.validate)
        self.range = QLineEdit()
        self.range.setPlaceholderText("例如 1-3,5")
        self.range.setAccessibleName("批注裁剪页码范围")
        self.range.setVisible(False)
        self.range_mode.toggled.connect(self.range.setVisible)
        self.range.textChanged.connect(self.validate)
        layout.addWidget(self.range)

        hint(layout, "裁剪来源")
        self.source_annot = QRadioButton("矩形批注（Square）")
        self.source_margin = QRadioButton("页边距（每边内缩 36 磅）")
        self.source_annot.setChecked(True)
        self.source_group = QButtonGroup(self)
        for radio in (self.source_annot, self.source_margin):
            layout.addWidget(radio)
            self.source_group.addButton(radio)
        self.expand = QCheckBox("同页多个批注合并为一个区域")
        layout.addWidget(self.expand)

        hint(layout, "排版方式")
        self.layout_flow = QRadioButton("拼版排版（A4 流式排列，便于打印）")
        self.layout_per = QRadioButton("独立分页（区域多大，页面就多大）")
        self.layout_flow.setChecked(True)
        self.layout_group = QButtonGroup(self)
        for radio in (self.layout_flow, self.layout_per):
            layout.addWidget(radio)
            self.layout_group.addButton(radio)

        self.feedback = hint(layout, "")
        self.start = buttons(self, layout, "选择输出 PDF…")
        self.validate()

    def indices(self):
        if self.range_mode.isChecked():
            return list(dict.fromkeys(parse_page_range(self.range.text(), self.count)))
        if self.selected_mode.isChecked():
            return self.selected
        return list(range(self.count))

    def options(self):
        if self.all_mode.isChecked():
            # 留空表示全部页面，由核心模块处理
            range_text = ""
        elif self.range_mode.isChecked():
            range_text = self.range.text().strip()
        else:
            range_text = ",".join(str(index + 1) for index in self.selected)
        return dict(
            source="annot" if self.source_annot.isChecked() else "margin",
            layout="flow" if self.layout_flow.isChecked() else "per",
            expand=self.expand.isChecked(),
            range=range_text,
        )

    def validate(self):
        if not hasattr(self, "start"):
            return
        try:
            error = "" if self.indices() else "页码范围没有匹配到任何页面。"
        except ValueError as exc:
            error = str(exc)
        self.feedback.setText(error)
        self.start.setEnabled(not error)
