"""Small, purpose-specific dialogs for the existing PDF tool area."""
from __future__ import annotations

from pathlib import Path

import pymupdf
from PyQt6.QtCore import QEvent, QSettings, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QRadioButton, QVBoxLayout, QWidget,
)

from app import APP_ORGANIZATION, APP_SLUG
from app.imaging import IMAGE_FILTER, IMAGE_SUFFIXES
from app.pdfio import EncryptionOptions
from app.workspace import parse_page_range
from app.storage import export_path

# The global sheet forces `min-height: 30px` + `padding: 6px` on every QPushButton,
# which makes a 28px-tall button taller than its neighbours. Compact buttons inside
# this dialog re-declare a smaller box so the header row and the reorder row align.
COMPACT_QSS = """
QPushButton[compact="true"] {
    min-height: 0px;
    max-height: none;
    padding: 5px 16px;
    font-size: 13px;
    border-radius: 7px;
}
QPushButton[compact="true"]:hover { background: #F0FDFA; border-color: #0D9488; }
QPushButton[compact="true"]:pressed { background: #CCFBF1; }
QPushButton[compact="true"]:focus { border: 1px solid #0D9488; }
QPushButton[compact="true"]:disabled {
    color: #94A3B8; background: #FFFFFF; border: 1px solid #E5EEEC;
}
"""
CARD_QSS = """
QFrame#card {
    background: #FFFFFF;
    border: 1px solid #E2EEEC;
    border-radius: 12px;
}
""" + COMPACT_QSS


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
    box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
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
        self.mode = QComboBox()
        self.mode.setAccessibleName("输出保护方式")
        self.mode.addItem("打开密码 · 输入密码才能阅读", "open")
        self.mode.addItem("免密阅读 · 限制打印与复制", "permissions")
        self.mode.addItem("双重保护 · 打开密码与独立管理密码", "both")
        layout.addWidget(self.mode)
        self.plain = QCheckBox("明确选择不加密")
        self.plain.setVisible(allow_plain)
        layout.addWidget(self.plain)
        form = QFormLayout()
        self.password_form = form
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
        self.mode.currentIndexChanged.connect(self.protection_changed)
        self.protection_changed()
        self.validate()

    def protection_changed(self):
        mode = self.mode.currentData()
        restricted = mode != "open"
        self.password_form.setRowVisible(self.password, mode != "permissions")
        self.password_form.setRowVisible(self.confirm, mode != "permissions")
        self.advanced.setVisible(False)
        self.options.setVisible(restricted)
        self.printing.setChecked(not restricted)
        self.copying.setChecked(not restricted)
        self.validate()

    def validate(self):
        plain = self.plain.isChecked()
        for widget in (self.mode, self.algorithm, self.password, self.confirm, self.advanced, self.options):
            widget.setEnabled(not plain)
        restricted = not self.printing.isChecked() or not self.copying.isChecked()
        self.owner.setEnabled(restricted and not plain)
        error = ""
        compatible = self.algorithm.currentData() == pymupdf.PDF_ENCRYPT_AES_128
        self.compatibility_hint.setText("AES-128 用于兼容旧版阅读器，强度低于 AES-256。"
            "密码限 1～32 个字符，仅支持字母、数字、英文符号和空格，中文密码请使用 AES-256。"
            if compatible else "AES-256 支持 1～40 个字符；旧版阅读器不一定支持此格式。")
        if not plain:
            permission_only = self.mode.currentData() == "permissions"
            user_password = "" if permission_only else self.password.text()
            if permission_only and not restricted:
                error = "请至少限制打印或复制，或改选打开密码保护。"
            elif not permission_only and not user_password:
                error = f"请输入打开密码（最多 {32 if compatible else 40} 个字符）。"
            elif not permission_only and user_password != self.confirm.text():
                error = "两次打开密码不一致。"
            elif restricted and not self.owner.text():
                error = "限制权限时请输入独立管理密码。"
            elif restricted and self.owner.text() == user_password:
                error = "管理密码必须不同于打开密码。"
            elif compatible and any(len(value) > 32 or any(not 32 <= ord(char) <= 126 for char in value)
                    for value in (user_password, self.owner.text() if restricted else "")):
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
            self.setting = EncryptionOptions("" if self.mode.currentData() == "permissions" else self.password.text(),
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
        self.setAcceptDrops(True)
        self.setWindowTitle("图片转 PDF")
        self.resize(680, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        hint(layout, "支持 PNG、JPEG、WebP、BMP、TIFF。列表顺序就是页序；多帧 TIFF 按帧生成页面。")

        # File list card ---------------------------------------------------------
        files_card = QFrame()
        files_card.setObjectName("card")
        files_card.setStyleSheet(CARD_QSS)
        files_layout = QVBoxLayout(files_card)
        files_layout.setContentsMargins(16, 14, 16, 14)
        files_layout.setSpacing(10)

        files_header = QHBoxLayout()
        files_header.setSpacing(10)
        files_title = QLabel("待转换图片 · 可直接拖入")
        files_title.setStyleSheet("font-weight: 600; color: #134E4A;")
        files_title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        files_header.addWidget(files_title)
        files_header.addStretch()

        self.count_label = QLabel("0 张")
        self.count_label.setStyleSheet("color: #4D6B67;")
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        files_header.addWidget(self.count_label)

        add = QPushButton("添加图片…")
        add.setProperty("compact", True)
        add.setCursor(Qt.CursorShape.PointingHandCursor)
        add.clicked.connect(self.add_files)
        files_header.addWidget(add)
        files_layout.addLayout(files_header)

        list_area = QVBoxLayout()
        list_area.setContentsMargins(0, 0, 0, 0)
        list_area.setSpacing(0)

        # Empty-state hint occupies the same slot as the list so the layout never
        # jumps between the two states.
        self.placeholder = QLabel("把图片拖到这里\n\n或点击右上角「添加图片…」")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.placeholder.setStyleSheet("color: #4D6B67; background: transparent; font-size: 14px;")
        list_area.addWidget(self.placeholder, 1)

        self.files = QListWidget()
        self.files.setFrameShape(QListWidget.Shape.NoFrame)
        # 不能关掉 viewport 的 acceptDrops：InternalMove 靠它完成列表内拖动重排。
        # 文件拖放经 eventFilter 转交给对话框统一处理，两者互不影响。
        self.files.installEventFilter(self)
        self.files.viewport().installEventFilter(self)
        self.files.setAccessibleName("图片页序，可拖动或使用上移下移按钮")
        self.files.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.files.setUniformItemSizes(True)
        self.files.hide()
        list_area.addWidget(self.files, 1)

        drop_zone = QFrame()
        drop_zone.setObjectName("dropZone")
        drop_zone_layout = QVBoxLayout(drop_zone)
        drop_zone_layout.setContentsMargins(8, 8, 8, 8)
        drop_zone_layout.addLayout(list_area)
        drop_zone.setStyleSheet(
            "QFrame#dropZone { background: #FAFCFB; border: 1.5px dashed #C5D5D2; border-radius: 9px; }\n"
            "QFrame#dropZone[active=\"true\"] { background: #F0FDFA; border: 1.5px dashed #0D9488; }"
        )
        files_layout.addWidget(drop_zone, 1)
        self._drop_zone = drop_zone

        reorder_row = QHBoxLayout()
        reorder_row.setSpacing(8)
        self.btn_move_up = QPushButton("上移")
        self.btn_move_down = QPushButton("下移")
        self.btn_remove = QPushButton("移除")
        self.btn_clear = QPushButton("清空")
        for btn, slot in (
            (self.btn_move_up, lambda: self.move(-1)),
            (self.btn_move_down, lambda: self.move(1)),
            (self.btn_remove, self.remove_selected),
            (self.btn_clear, self.clear_files),
        ):
            btn.setProperty("compact", True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(slot)
            reorder_row.addWidget(btn)
        reorder_row.addStretch()
        files_layout.addLayout(reorder_row)
        self.reorder_row = reorder_row

        self.files.currentRowChanged.connect(lambda _: self._update_button_states())

        layout.addWidget(files_card, 1)

        self.feedback = hint(layout, "")

        self.start = buttons(self, layout, "另存为 PDF…")
        self.start.setEnabled(False)
        self._refresh_state()

    # ----- core actions -------------------------------------------------------
    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "按顺序添加图片", "", IMAGE_FILTER)
        if paths:
            self.add_paths(paths)

    def add_paths(self, paths):
        seen = {self.files.item(i).text().casefold() for i in range(self.files.count())}
        added = 0
        for value in paths:
            path = Path(value)
            if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
                continue
            resolved = str(path.resolve())
            if resolved.casefold() in seen:
                continue
            seen.add(resolved.casefold())
            item = QListWidgetItem(resolved)
            item.setToolTip(resolved)
            self.files.addItem(item)
            added += 1
        self.files.setVisible(bool(self.files.count()))
        self._refresh_state(added=added)

    def move(self, delta):
        row = self.files.currentRow()
        target = row + delta
        if row >= 0 and 0 <= target < self.files.count():
            item = self.files.takeItem(row)
            self.files.insertItem(target, item)
            self.files.setCurrentRow(target)
            self._refresh_state()

    def remove_selected(self):
        row = self.files.currentRow()
        if row >= 0:
            self.files.takeItem(row)
        self.files.setVisible(bool(self.files.count()))
        self._refresh_state()

    def clear_files(self):
        if self.files.count() == 0:
            return
        self.files.clear()
        self.files.setVisible(False)
        self._refresh_state()

    def paths(self):
        return [self.files.item(i).text() for i in range(self.files.count())]

    # ----- internal helpers ---------------------------------------------------
    def _update_button_states(self):
        count = self.files.count()
        row = self.files.currentRow()
        self.btn_move_up.setEnabled(row > 0)
        self.btn_move_down.setEnabled(0 <= row < count - 1)
        self.btn_remove.setEnabled(row >= 0)
        self.btn_clear.setEnabled(count > 0)

    def _refresh_state(self, added=0):
        count = self.files.count()
        self.count_label.setText(f"{count} 张")
        self.start.setEnabled(count > 0)
        self._update_button_states()
        if count == 0:
            self.placeholder.setVisible(True)
            self.files.setVisible(False)
            self.feedback.setText("请拖入或选择 PNG、JPEG、WebP、BMP、TIFF 文件。重复文件自动忽略。")
            return
        self.placeholder.setVisible(False)
        self.files.setVisible(True)
        if added:
            self.feedback.setText(f"已添加 {added} 张，共 {count} 张。列表顺序就是页序。")
        else:
            self.feedback.setText(f"共 {count} 张。列表顺序就是页序，可拖动或用上移/下移调整。")

    # ----- drag & drop --------------------------------------------------------
    def _set_drop_active(self, active):
        self._drop_zone.setProperty("active", "true" if active else "false")
        self._drop_zone.style().unpolish(self._drop_zone)
        self._drop_zone.style().polish(self._drop_zone)

    def _accept_drag(self, event):
        if self._drop_paths(event.mimeData()):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            return True
        event.ignore()
        return False

    def _drop_paths(self, mime_data):
        if not mime_data or not mime_data.hasUrls():
            return []
        paths = []
        for url in mime_data.urls():
            if not url.isLocalFile():
                continue
            candidate = Path(url.toLocalFile())
            if candidate.is_file() and candidate.suffix.lower() in IMAGE_SUFFIXES:
                paths.append(str(candidate.resolve()))
            elif candidate.is_dir():
                try:
                    for entry in sorted(candidate.iterdir()):
                        if entry.is_file() and entry.suffix.lower() in IMAGE_SUFFIXES:
                            paths.append(str(entry.resolve()))
                except OSError:
                    continue
        return paths

    def dragEnterEvent(self, event):
        if self._accept_drag(event):
            self._set_drop_active(True)

    def dragMoveEvent(self, event):
        # 拖动过程中必须持续响应，否则系统会判定为禁止拖放。
        self._accept_drag(event)

    def dragLeaveEvent(self, event):
        self._set_drop_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        paths = self._drop_paths(event.mimeData())
        self._set_drop_active(False)
        if paths:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self.add_paths(paths)
        else:
            if event.mimeData().hasUrls():
                self.feedback.setText("未发现支持的图片。请拖入 PNG、JPEG、WebP、BMP、TIFF 文件或包含图片的文件夹。")
            event.ignore()

    def eventFilter(self, watched, event):
        # 键盘快捷键：Delete / Backspace 移除选中项
        if watched is self.files and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                if self.files.currentRow() >= 0:
                    self.remove_selected()
                    return True
        # 文件拖到列表上时转交给对话框处理；列表自身的拖动重排照常走默认逻辑。
        if watched in (self.files, self.files.viewport()):
            if event.type() == QEvent.Type.Drop:
                if self._drop_paths(event.mimeData()):
                    self.dropEvent(event)
                    return True
            elif event.type() == QEvent.Type.DragEnter:
                if self._accept_drag(event):
                    self._set_drop_active(True)
                    return True
            elif event.type() == QEvent.Type.DragMove:
                return self._accept_drag(event)
            elif event.type() == QEvent.Type.DragLeave:
                self._set_drop_active(False)
        return super().eventFilter(watched, event)


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
    path, _ = QFileDialog.getSaveFileName(parent, title, export_path(suggested), "PDF (*.pdf)")
    if not path:
        return None
    if not path.lower().endswith(".pdf"):
        path += ".pdf"
    if store.is_source_destination(path):
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(parent, "请另存副本", "不能覆盖已打开的来源文件，请选择新的文件名。")
        return None
    return str(Path(path).absolute())
