"""Small, purpose-specific dialogs for the existing PDF tool area."""
from __future__ import annotations

from pathlib import Path

import pymupdf
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QPushButton, QRadioButton, QVBoxLayout, QWidget,
)

from app import APP_ORGANIZATION, APP_SLUG
from app.imaging import IMAGE_FILTER
from app.pdfio import EncryptionOptions
from app.workspace import parse_page_range


def hint(layout, text):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setObjectName("hint")
    layout.addWidget(label)
    return label


def buttons(dialog, layout, text):
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
    start = box.addButton(text, QDialogButtonBox.ButtonRole.AcceptRole)
    start.setObjectName("primary")
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    layout.addWidget(box)
    return start


class EncryptionDialog(QDialog):
    def __init__(self, parent=None, *, allow_plain=False):
        super().__init__(parent)
        self.setWindowTitle("选择输出保护" if allow_plain else "加密另存")
        self.setMinimumWidth(480)
        self.setting = None
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        hint(layout, "密码仅用于本次输出，不会保存。请妥善保管，本软件无法找回。")
        self.plain = QCheckBox("明确选择不加密")
        self.plain.setVisible(allow_plain)
        layout.addWidget(self.plain)
        form = QFormLayout()
        self.algorithm = QComboBox()
        self.algorithm.setAccessibleName("PDF 加密格式")
        self.algorithm.addItem("AES-256 · 常规加密", pymupdf.PDF_ENCRYPT_AES_256)
        self.algorithm.addItem("AES-128 · 兼容旧版 Foxit", pymupdf.PDF_ENCRYPT_AES_128)
        self.preferences = QSettings(APP_ORGANIZATION, APP_SLUG)
        if self.preferences.value("pdf/encryption_algorithm", "aes256") == "aes128":
            self.algorithm.setCurrentIndex(1)
        form.addRow("加密格式", self.algorithm)
        self.password = QLineEdit()
        self.confirm = QLineEdit()
        self.owner = QLineEdit()
        for edit in (self.password, self.confirm, self.owner):
            edit.setEchoMode(QLineEdit.EchoMode.Password)
            edit.setMaxLength(40)
            edit.textChanged.connect(self.validate)
        form.addRow("打开密码", self.password)
        form.addRow("再次输入", self.confirm)
        layout.addLayout(form)
        self.compatibility_hint = hint(layout, "")
        hint(layout, "会记住上次确认的加密格式，下次无需重新选择。")
        self.advanced = QCheckBox("高级选项 · 打印与复制权限")
        layout.addWidget(self.advanced)
        self.options = QWidget()
        details = QVBoxLayout(self.options)
        details.setContentsMargins(0, 0, 0, 0)
        self.printing = QCheckBox("允许打印")
        self.copying = QCheckBox("允许复制和内容导出")
        for check in (self.printing, self.copying):
            check.setChecked(True)
            check.toggled.connect(self.validate)
            details.addWidget(check)
        owner_form = QFormLayout()
        owner_form.addRow("独立管理密码", self.owner)
        details.addLayout(owner_form)
        hint(details, "限制权限时必须使用不同的管理密码。权限限制不能保证绝对防复制。")
        self.options.hide()
        self.advanced.toggled.connect(self.options.setVisible)
        layout.addWidget(self.options)
        self.feedback = hint(layout, "")
        self.start = buttons(self, layout, "确认输出保护")
        self.plain.toggled.connect(self.validate)
        self.algorithm.currentIndexChanged.connect(self.validate)
        self.validate()

    def validate(self):
        plain = self.plain.isChecked()
        for widget in (self.algorithm, self.password, self.confirm, self.advanced, self.options):
            widget.setEnabled(not plain)
        restricted = not self.printing.isChecked() or not self.copying.isChecked()
        self.owner.setEnabled(restricted and not plain)
        error = ""
        compatible = self.algorithm.currentData() == pymupdf.PDF_ENCRYPT_AES_128
        self.compatibility_hint.setText("AES-128 用于兼容旧版阅读器，强度低于 AES-256。"
            "密码限 1～32 个字符，仅支持字母、数字、英文符号和空格，中文密码请使用 AES-256。"
            if compatible else "AES-256 支持 1～40 个字符；旧版阅读器不一定支持此格式。")
        if not plain:
            if not self.password.text():
                error = f"请输入打开密码（最多 {32 if compatible else 40} 个字符）。"
            elif self.password.text() != self.confirm.text():
                error = "两次打开密码不一致。"
            elif restricted and not self.owner.text():
                error = "限制权限时请输入独立管理密码。"
            elif restricted and self.owner.text() == self.password.text():
                error = "管理密码必须不同于打开密码。"
            elif compatible and any(len(value) > 32 or any(not 32 <= ord(char) <= 126 for char in value)
                    for value in (self.password.text(), self.owner.text() if restricted else "")):
                error = "兼容模式密码须为最多 32 个半角字符；中文密码请选 AES-256。"
        self.feedback.setText(error)
        self.start.setEnabled(not error)

    def accept(self):
        if not self.start.isEnabled():
            return
        if not self.plain.isChecked():
            permissions = 4095
            if not self.printing.isChecked():
                permissions &= ~(pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_PRINT_HQ)
            if not self.copying.isChecked():
                permissions &= ~pymupdf.PDF_PERM_COPY
            self.setting = EncryptionOptions(self.password.text(),
                self.owner.text() if permissions != 4095 else "", permissions,
                algorithm=self.algorithm.currentData())
            # Persist only the explicit format choice; all passwords stay in memory.
            self.preferences.setValue("pdf/encryption_algorithm",
                "aes128" if self.setting.algorithm == pymupdf.PDF_ENCRYPT_AES_128 else "aes256")
        super().accept()

    def done(self, result):
        for edit in (self.password, self.confirm, self.owner):
            edit.clear()
        super().done(result)


class ImageImportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("图片转 PDF")
        self.resize(620, 460)
        layout = QVBoxLayout(self)
        hint(layout, "支持 PNG、JPEG、BMP、TIFF。列表顺序就是页序；多帧 TIFF 按帧生成页面。")
        row = QHBoxLayout()
        for label, slot in (("添加图片…", self.add_files), ("上移", lambda: self.move(-1)),
                            ("下移", lambda: self.move(1)), ("移除", self.remove)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            row.addWidget(button)
        layout.addLayout(row)
        self.files = QListWidget()
        self.files.setAccessibleName("图片页序，可拖动或使用上移下移按钮")
        self.files.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        layout.addWidget(self.files, 1)
        self.start = buttons(self, layout, "另存为 PDF…")
        self.start.setEnabled(False)

    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "按顺序添加图片", "", IMAGE_FILTER)
        self.files.addItems(paths)
        self.start.setEnabled(self.files.count() > 0)

    def move(self, delta):
        row = self.files.currentRow()
        target = row + delta
        if row >= 0 and 0 <= target < self.files.count():
            item = self.files.takeItem(row)
            self.files.insertItem(target, item)
            self.files.setCurrentRow(target)

    def remove(self):
        row = self.files.currentRow()
        if row >= 0:
            self.files.takeItem(row)
        self.start.setEnabled(self.files.count() > 0)

    def paths(self):
        return [self.files.item(i).text() for i in range(self.files.count())]


class ImageExportDialog(QDialog):
    def __init__(self, count, current, selected, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PDF 转图片")
        self.setMinimumWidth(440)
        self.count, self.current, self.selected = count, current, selected
        layout = QVBoxLayout(self)
        self.current_mode = QRadioButton("当前页")
        self.selected_mode = QRadioButton(f"所选页面（{len(selected)} 页）")
        self.range_mode = QRadioButton("按页码范围")
        self.current_mode.setChecked(True)
        self.selected_mode.setEnabled(bool(selected))
        for radio in (self.current_mode, self.selected_mode, self.range_mode):
            layout.addWidget(radio)
            radio.toggled.connect(self.validate)
        self.range = QLineEdit()
        self.range.setPlaceholderText("例如 1-3,5")
        self.range.setAccessibleName("图片导出页码范围")
        self.range.setVisible(False)
        self.range_mode.toggled.connect(self.range.setVisible)
        self.range.textChanged.connect(self.validate)
        layout.addWidget(self.range)
        form = QFormLayout()
        self.format = QComboBox()
        self.format.addItems(["PNG", "JPEG"])
        self.dpi = QComboBox()
        self.dpi.addItems(["150 DPI", "300 DPI"])
        form.addRow("图片格式", self.format)
        form.addRow("分辨率", self.dpi)
        layout.addLayout(form)
        self.overwrite = QCheckBox("覆盖同名图片（默认跳过）")
        layout.addWidget(self.overwrite)
        hint(layout, "每页生成一个文件，页码按当前工作区顺序编号。取消保留已经完整导出的图片。")
        self.feedback = hint(layout, "")
        self.start = buttons(self, layout, "导出到文件夹…")
        self.validate()

    def indices(self):
        if self.range_mode.isChecked():
            if not self.range.text().strip():
                raise ValueError("请输入页码范围。")
            return list(dict.fromkeys(parse_page_range(self.range.text(), self.count)))
        if self.selected_mode.isChecked():
            return self.selected
        return [self.current]

    def validate(self):
        if not hasattr(self, "start"):
            return
        try:
            self.indices()
            error = ""
        except ValueError as exc:
            error = str(exc)
        self.feedback.setText(error)
        self.start.setEnabled(not error)


def new_pdf_path(parent, store, title, suggested):
    path, _ = QFileDialog.getSaveFileName(parent, title, suggested, "PDF (*.pdf)")
    if not path:
        return None
    if not path.lower().endswith(".pdf"):
        path += ".pdf"
    if store.is_source_destination(path):
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(parent, "请另存副本", "不能覆盖已打开的来源文件，请选择新的文件名。")
        return None
    return str(Path(path).absolute())
