"""Small, purpose-specific dialogs for the existing PDF tool area."""
from __future__ import annotations
from app.i18n import tr
from app.themes import set_theme_style

from pathlib import Path

import pymupdf
from PyQt6.QtCore import QEvent, QObject, QSettings, QSignalBlocker, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QRadioButton, QSizePolicy, QVBoxLayout, QWidget,
)

from app.imaging import IMAGE_FILTER, IMAGE_SUFFIXES, plan_long_image
from app.pdfio import EncryptionOptions, PdfError
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
    box.button(QDialogButtonBox.StandardButton.Cancel).setText(tr('取消'))
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    layout.addWidget(box)
    return start


class EncryptionDialog(QDialog):
    def __init__(self, parent=None, *, allow_plain=False):
        super().__init__(parent)
        self.setWindowTitle(tr('选择输出保护') if allow_plain else tr('加密另存'))
        self.setMinimumWidth(480)
        self.setting = None
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        hint(layout, tr('密码仅用于本次输出，不会保存。请妥善保管，本软件无法找回。'))
        self.mode = QComboBox()
        self.mode.setAccessibleName(tr('输出保护方式'))
        self.mode.addItem(tr('打开密码 · 输入密码才能阅读'), "open")
        self.mode.addItem(tr('免密阅读 · 限制打印与复制'), "permissions")
        self.mode.addItem(tr('双重保护 · 打开密码与独立管理密码'), "both")
        layout.addWidget(self.mode)
        self.plain = QCheckBox(tr('明确选择不加密'))
        self.plain.setVisible(allow_plain)
        layout.addWidget(self.plain)
        form = QFormLayout()
        self.password_form = form
        self.algorithm = QComboBox()
        self.algorithm.setAccessibleName(tr('PDF 加密格式'))
        self.algorithm.addItem(tr('AES-256 · 常规加密'), pymupdf.PDF_ENCRYPT_AES_256)
        self.algorithm.addItem(tr('AES-128 · 兼容旧版 Foxit'), pymupdf.PDF_ENCRYPT_AES_128)
        from app.runtime import preferences
        self.preferences = preferences(QSettings)
        if self.preferences.value("pdf/encryption_algorithm", "aes256") == "aes128":
            self.algorithm.setCurrentIndex(1)
        form.addRow(tr('加密格式'), self.algorithm)
        self.password = QLineEdit()
        self.confirm = QLineEdit()
        self.owner = QLineEdit()
        for edit in (self.password, self.confirm, self.owner):
            edit.setEchoMode(QLineEdit.EchoMode.Password)
            edit.setMaxLength(40)
            edit.textChanged.connect(self.validate)
        form.addRow(tr('打开密码'), self.password)
        form.addRow(tr('再次输入'), self.confirm)
        layout.addLayout(form)
        self.compatibility_hint = hint(layout, "")
        hint(layout, tr('会记住上次确认的加密格式，下次无需重新选择。'))
        self.advanced = QCheckBox(tr('高级选项 · 打印与复制权限'))
        layout.addWidget(self.advanced)
        self.options = QWidget()
        details = QVBoxLayout(self.options)
        details.setContentsMargins(0, 0, 0, 0)
        self.printing = QCheckBox(tr('允许打印'))
        self.copying = QCheckBox(tr('允许复制和内容导出'))
        for check in (self.printing, self.copying):
            check.setChecked(True)
            check.toggled.connect(self.validate)
            details.addWidget(check)
        owner_form = QFormLayout()
        owner_form.addRow(tr('独立管理密码'), self.owner)
        details.addLayout(owner_form)
        hint(details, tr('限制权限时必须使用不同的管理密码。权限限制不能保证绝对防复制。'))
        self.options.hide()
        self.advanced.toggled.connect(self.options.setVisible)
        layout.addWidget(self.options)
        self.feedback = hint(layout, "")
        self.start = buttons(self, layout, tr('确认输出保护'))
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
        self.compatibility_hint.setText(tr('AES-128 用于兼容旧版阅读器，强度低于 AES-256。密码限 1～32 个字符，仅支持字母、数字、英文符号和空格，中文密码请使用 AES-256。')
            if compatible else tr('AES-256 支持 1～40 个字符；旧版阅读器不一定支持此格式。'))
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
        self.setWindowTitle(tr('图片转 PDF'))
        self.resize(680, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        hint(layout, tr('支持 PNG、JPEG、WebP、BMP、TIFF。列表顺序就是页序；多帧 TIFF 按帧生成页面。'))

        # File list card ---------------------------------------------------------
        files_card = QFrame()
        files_card.setObjectName("card")
        set_theme_style(files_card, CARD_QSS)
        files_layout = QVBoxLayout(files_card)
        files_layout.setContentsMargins(16, 14, 16, 14)
        files_layout.setSpacing(10)

        files_header = QHBoxLayout()
        files_header.setSpacing(10)
        files_title = QLabel(tr('待转换图片 · 可直接拖入'))
        set_theme_style(files_title, "font-weight: 600; color: #134E4A;")
        files_title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        files_header.addWidget(files_title)
        files_header.addStretch()

        self.count_label = QLabel(tr('0 张'))
        set_theme_style(self.count_label, "color: #4D6B67;")
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        files_header.addWidget(self.count_label)

        add = QPushButton(tr('添加图片…'))
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
        self.placeholder = QLabel(tr('把图片拖到这里\n\n或点击右上角「添加图片…」'))
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)
        set_theme_style(self.placeholder, "color: #4D6B67; background: transparent; font-size: 14px;")
        list_area.addWidget(self.placeholder, 1)

        self.files = QListWidget()
        self.files.setFrameShape(QListWidget.Shape.NoFrame)
        # 不能关掉 viewport 的 acceptDrops：InternalMove 靠它完成列表内拖动重排。
        # 文件拖放经 eventFilter 转交给对话框统一处理，两者互不影响。
        self.files.installEventFilter(self)
        self.files.viewport().installEventFilter(self)
        self.files.setAccessibleName(tr('图片页序，可拖动或使用上移下移按钮'))
        self.files.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.files.setUniformItemSizes(True)
        self.files.hide()
        list_area.addWidget(self.files, 1)

        drop_zone = QFrame()
        drop_zone.setObjectName("dropZone")
        drop_zone_layout = QVBoxLayout(drop_zone)
        drop_zone_layout.setContentsMargins(8, 8, 8, 8)
        drop_zone_layout.addLayout(list_area)
        set_theme_style(drop_zone,
            "QFrame#dropZone { background: #FAFCFB; border: 1.5px dashed #C5D5D2; border-radius: 9px; }\n"
            "QFrame#dropZone[active=\"true\"] { background: #F0FDFA; border: 1.5px dashed #0D9488; }"
        )
        files_layout.addWidget(drop_zone, 1)
        self._drop_zone = drop_zone

        reorder_row = QHBoxLayout()
        reorder_row.setSpacing(8)
        self.btn_move_up = QPushButton(tr('上移'))
        self.btn_move_down = QPushButton(tr('下移'))
        self.btn_remove = QPushButton(tr('移除'))
        self.btn_clear = QPushButton(tr('清空'))
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

        self.start = buttons(self, layout, tr('另存为 PDF…'))
        self.start.setEnabled(False)
        self._refresh_state()

    # ----- core actions -------------------------------------------------------
    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, tr('按顺序添加图片'), "", IMAGE_FILTER)
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
        self.count_label.setText(tr('{p0} 张', p0=f'{count}'))
        self.start.setEnabled(count > 0)
        self._update_button_states()
        if count == 0:
            self.placeholder.setVisible(True)
            self.files.setVisible(False)
            self.feedback.setText(tr('请拖入或选择 PNG、JPEG、WebP、BMP、TIFF 文件。重复文件自动忽略。'))
            return
        self.placeholder.setVisible(False)
        self.files.setVisible(True)
        if added:
            self.feedback.setText(tr('已添加 {p0} 张，共 {p1} 张。列表顺序就是页序。', p0=f'{added}', p1=f'{count}'))
        else:
            self.feedback.setText(tr('共 {p0} 张。列表顺序就是页序，可拖动或用上移/下移调整。', p0=f'{count}'))

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
                self.feedback.setText(tr('未发现支持的图片。请拖入 PNG、JPEG、WebP、BMP、TIFF 文件或包含图片的文件夹。'))
            event.ignore()

    def eventFilter(self, watched, event):
        # The list owns this command; text fields keep native deletion.
        if watched is self.files and event.type() == QEvent.Type.KeyPress:
            from app.shortcuts import manager_for
            if manager_for(self).matches('import_images.remove', event):
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


class _ComboEnabledBridge(QObject):
    def __init__(self, combo: QComboBox, target: QWidget):
        super().__init__(target)
        self._combo = combo
        self._target = target
        combo.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self._combo and event.type() == QEvent.Type.EnabledChange:
            self._target.setEnabled(self._combo.isEnabled())
        return False


def _segmented_for_combo(combo: QComboBox, label: str = "") -> QFrame:
    """Create a one-click segmented control bidirectionally synced to a QComboBox."""
    track = QFrame()
    track.setObjectName("segmentedTrack")
    track.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
    if label:
        track.setAccessibleName(label)
        if not combo.accessibleName():
            combo.setAccessibleName(label)
    lay = QHBoxLayout(track)
    lay.setContentsMargins(3, 3, 3, 3)
    lay.setSpacing(3)
    group = QButtonGroup(track)
    group.setExclusive(True)
    buttons_list: list[QPushButton] = []

    def sync_from_combo(index: int) -> None:
        if 0 <= index < len(buttons_list):
            btn = buttons_list[index]
            group.blockSignals(True)
            btn.blockSignals(True)
            btn.setChecked(True)
            btn.blockSignals(False)
            group.blockSignals(False)

    for idx in range(combo.count()):
        text = combo.itemText(idx)
        btn = QPushButton(text, track)
        btn.setCheckable(True)
        btn.setAutoDefault(False)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if label:
            btn.setAccessibleName(f"{label} - {text}")
        group.addButton(btn, idx)
        lay.addWidget(btn, 1)
        buttons_list.append(btn)

    group.idClicked.connect(lambda i: combo.setCurrentIndex(i) if combo.currentIndex() != i else None)
    combo.currentIndexChanged.connect(sync_from_combo)
    sync_from_combo(combo.currentIndex())
    track.setEnabled(combo.isEnabled())
    _ComboEnabledBridge(combo, track)
    combo.hide()
    return track


class ImageExportDialog(QDialog):
    def __init__(self, store, pages, current, selected, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('PDF 转图片'))
        self.setMinimumWidth(460)
        self.store, self.pages = store, pages
        self.count, self.current, self.selected = len(pages), current, list(selected)
        selected_count = len(self.selected)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        # Card 1: 导出范围 --------------------------------------------------------
        scope_card = QFrame()
        scope_card.setObjectName("dialogCard")
        scope_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        scope_layout = QVBoxLayout(scope_card)
        scope_layout.setContentsMargins(16, 12, 16, 14)
        scope_layout.setSpacing(8)

        scope_title = QLabel(tr('导出范围'))
        scope_title.setObjectName("sectionTitle")
        scope_title.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        scope_layout.addWidget(scope_title)

        self.current_mode = QRadioButton(tr('当前页'))
        self.all_mode = QRadioButton(tr('全部页面（{p0} 页）', p0=self.count))
        self.selected_mode = QRadioButton(tr('所选页面（{p0} 页）', p0=selected_count))
        self.range_mode = QRadioButton(tr('按页码范围'))
        self.current_mode.setChecked(True)
        for radio in (self.current_mode, self.all_mode, self.selected_mode, self.range_mode):
            scope_layout.addWidget(radio)
            radio.toggled.connect(self.validate)
        self.all_mode.setVisible(self.count > 1)
        self.selected_mode.setVisible(0 < selected_count < self.count and self.selected != [current])
        self.range_mode.setVisible(self.count > 1)

        self._range_wrap = QWidget()
        self._range_wrap.setStyleSheet("background: transparent;")
        self._range_wrap.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        range_row = QHBoxLayout(self._range_wrap)
        range_row.setContentsMargins(24, 2, 0, 0)
        range_row.setSpacing(0)
        self.range = QLineEdit()
        self.range.setPlaceholderText(tr('例如 1-3,5'))
        self.range.setAccessibleName(tr('图片导出页码范围'))
        self.range.setVisible(False)
        self._range_wrap.setVisible(False)
        self.range_mode.toggled.connect(self._toggle_range)
        self.range.textChanged.connect(self.validate)
        range_row.addWidget(self.range)
        scope_layout.addWidget(self._range_wrap)
        layout.addWidget(scope_card)

        # Card 2: 输出设置 --------------------------------------------------------
        output_card = QFrame()
        output_card.setObjectName("dialogCard")
        output_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        output_layout = QVBoxLayout(output_card)
        output_layout.setContentsMargins(16, 12, 16, 14)
        output_layout.setSpacing(10)

        output_title = QLabel(tr('输出设置'))
        output_title.setObjectName("sectionTitle")
        output_title.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        output_layout.addWidget(output_title)

        mode_block = QVBoxLayout()
        mode_block.setSpacing(5)
        mode_text = tr('导出方式')
        mode_label = QLabel(mode_text)
        self.mode = QComboBox(self)
        self.mode.addItem(tr('每页一张'), False)
        self.mode.addItem(tr('合成一张长图'), True)
        self._mode_seg = _segmented_for_combo(self.mode, mode_text)
        mode_block.addWidget(mode_label)
        mode_block.addWidget(self._mode_seg)
        self.mode_hint = hint(mode_block, "")
        output_layout.addLayout(mode_block)

        params_row = QHBoxLayout()
        params_row.setSpacing(12)

        fmt_col = QVBoxLayout()
        fmt_col.setSpacing(5)
        fmt_text = tr('图片格式')
        fmt_col.addWidget(QLabel(fmt_text))
        self.format = QComboBox(self)
        self.format.addItem("PNG", "png")
        self.format.addItem("JPEG", "jpeg")
        self._format_seg = _segmented_for_combo(self.format, fmt_text)
        fmt_col.addWidget(self._format_seg)
        params_row.addLayout(fmt_col, 1)

        dpi_col = QVBoxLayout()
        dpi_col.setSpacing(5)
        dpi_text = tr('分辨率')
        dpi_col.addWidget(QLabel(dpi_text))
        self.dpi = QComboBox(self)
        self.dpi.addItem("150 DPI", 150)
        self.dpi.addItem("300 DPI", 300)
        self._dpi_seg = _segmented_for_combo(self.dpi, dpi_text)
        dpi_col.addWidget(self._dpi_seg)
        params_row.addLayout(dpi_col, 1)
        output_layout.addLayout(params_row)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFixedHeight(1)
        set_theme_style(divider, "background: #E5EEEC; border: none;")
        output_layout.addWidget(divider)

        self.overwrite = QCheckBox(tr('覆盖同名图片（默认跳过）'))
        output_layout.addWidget(self.overwrite)
        layout.addWidget(output_card)

        for field in (self.mode, self.format, self.dpi):
            field.currentIndexChanged.connect(self.validate)

        # Dynamic feedback banner (hidden when empty to avoid dead whitespace) ---
        self.feedback = QLabel("")
        self.feedback.setWordWrap(True)
        self.feedback.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.feedback.setObjectName("exportFeedbackBanner")
        set_theme_style(self.feedback, """
            QLabel#exportFeedbackBanner {
                background: #EEF3F0;
                color: #526B65;
                border: 1px solid #D5E2DE;
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 12px;
            }
            QLabel#exportFeedbackBanner[error="true"] {
                background: #FEF2F2;
                color: #B91C1C;
                border: 1px solid #FECACA;
            }
        """)
        self.feedback.hide()
        layout.addWidget(self.feedback)
        layout.addStretch(1)

        self.start = buttons(self, layout, tr('导出到文件夹…'))
        self.validate()

    def _fit_height(self):
        if not self.isVisible():
            return
        self.layout().activate()
        target_h = self.sizeHint().height()
        if target_h > 0:
            self.resize(self.width(), target_h)

    def _toggle_range(self, checked):
        self._range_wrap.setVisible(checked)
        self.range.setVisible(checked)
        if checked:
            self.range.setFocus()
        QTimer.singleShot(0, self._fit_height)

    def indices(self):
        if self.all_mode.isChecked():
            return list(range(self.count))
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
        long_image = self.mode.currentData()
        self.mode_hint.setText(
            tr('页面按所选顺序从上到下拼接，各页等比铺满最宽页的宽度。') if long_image else
            tr('每页生成一个文件，页码按当前工作区顺序编号。取消保留已经完整导出的图片。'))
        message = ""
        valid = True
        try:
            indices = self.indices()
            if long_image:
                plan = plan_long_image(self.store, (self.pages[i] for i in indices),
                                       self.format.currentData(), self.dpi.currentData())
                message = plan.summary()
        except (ValueError, PdfError) as exc:
            message = str(exc)
            valid = False
        was_visible = self.feedback.isVisible()
        self.feedback.setText(message)
        self.feedback.setProperty("error", "true" if (message and not valid) else "false")
        self.feedback.style().unpolish(self.feedback)
        self.feedback.style().polish(self.feedback)
        self.feedback.setVisible(bool(message))
        self.start.setEnabled(valid)
        if self.isVisible() and (was_visible != bool(message) or bool(message)):
            QTimer.singleShot(0, self._fit_height)

    def accept(self):
        if self.start.isEnabled():
            super().accept()


def new_pdf_path(parent, store, title, suggested):
    path, _ = QFileDialog.getSaveFileName(parent, title, export_path(suggested), "PDF (*.pdf)")
    if not path:
        return None
    if not path.lower().endswith(".pdf"):
        path += ".pdf"
    if store.is_source_destination(path):
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(parent, tr('请另存副本'), tr('不能覆盖已打开的来源文件，请选择新的文件名。'))
        return None
    return str(Path(path).absolute())


class ExcelExportOptions(QWidget):
    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        from app.pagetoolsui import ClearComboBox

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        content_label = QLabel(tr('导出内容'))
        self.content = ClearComboBox()
        self.content.addItem(tr('全部内容（按页导出）'), True)
        self.content.addItem(tr('仅表格'), False)
        self.content.setAccessibleName(tr('Excel 导出内容'))
        content_label.setBuddy(self.content)
        layout.addWidget(content_label)
        layout.addWidget(self.content)

        self.all = QRadioButton(tr('全部页面'))
        self.selected = QRadioButton(tr('所选页面'))
        self.range_mode = QRadioButton(tr('按页码范围'))
        self.all.setChecked(True)
        for radio in (self.all, self.selected, self.range_mode):
            layout.addWidget(radio)
            radio.toggled.connect(self.changed)
        self.range = QLineEdit()
        self.range.setPlaceholderText(tr('如 1-3,5；默认全部'))
        self.range.setAccessibleName(tr('转换页码范围'))
        layout.addWidget(self.range)
        self.range.hide()
        self.range_mode.toggled.connect(self.range.setVisible)
        self.range.textChanged.connect(self.changed)
        hint(layout, tr('全部内容按页保留文字、表格和图片。扫描文字需先完成 OCR；金额与行列请核对原件。'))
        self.feedback = hint(layout, '')
        self.feedback.setObjectName('exportFeedback')
        self.feedback.hide()

    def copy_from(self, other):
        with QSignalBlocker(self):
            self.content.setCurrentIndex(other.content.currentIndex())
            self.range.setText(other.range.text())
            for name in ('all', 'selected', 'range_mode'):
                if getattr(other, name).isChecked():
                    getattr(self, name).setChecked(True)
                    break
        self.changed.emit()

    def indices(self, workspace):
        if self.selected.isChecked():
            selected = workspace.target_indices()
            if not selected:
                raise ValueError(tr('请先在页面列表中选择需要转换的页面。'))
            return selected
        if self.range_mode.isChecked():
            text = self.range.text().strip()
            if not text:
                raise ValueError(tr('请输入要转换的页码范围。'))
            return parse_page_range(text, len(workspace.pages))
        return range(len(workspace.pages))

    def validate(self, button, workspace, reason=''):
        error = reason if workspace.pages else tr('请先打开 PDF')
        if not error:
            try:
                self.indices(workspace)
            except ValueError as exc:
                error = str(exc)
        self.feedback.setText(error)
        self.feedback.setVisible(bool(error))
        button.setEnabled(not error)
        button.setToolTip(error)


class ExcelExportDialog(QDialog):
    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('PDF 转 Excel'))
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        self.options = ExcelExportOptions()
        layout.addWidget(self.options)
        self.start = buttons(self, layout, tr('转换为 Excel…'))
        self.options.changed.connect(lambda: self.options.validate(self.start, workspace))
        self.options.validate(self.start, workspace)

    def accept(self):
        if self.start.isEnabled():
            super().accept()
