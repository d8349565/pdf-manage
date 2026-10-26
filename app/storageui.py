"""Storage settings reuse the application's existing dialogs and task progress."""
from app.themes import set_theme_style
from pathlib import Path
import tempfile

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox,
                            QFileDialog, QGridLayout, QFrame, QHBoxLayout, QLabel,
                            QInputDialog, QLineEdit, QMessageBox, QPushButton, QScrollArea,
                            QVBoxLayout, QWidget)

from app.exportdialogs import CARD_QSS, hint
from app.ocr.contracts import OCRError
from app.ocr.model_manager import relocate_runtime
from app.output import OperationCancelled
from app.progress import run_operation
from app import storage

SETTINGS_QSS = CARD_QSS + '''
QDialog#storageSettings QLineEdit, QDialog#ocrInstaller QLineEdit, QDialog#ocrSettings QLineEdit,
QDialog#storageSettings QComboBox, QDialog#ocrSettings QComboBox {
    min-height: 24px; padding: 6px 10px; border-width: 2px;
}
QDialog#storageSettings QPushButton, QDialog#ocrInstaller QPushButton, QDialog#ocrSettings QPushButton {
    min-height: 24px; padding: 6px 14px; border-width: 2px;
}
'''


class SettingsForm(QGridLayout):
    """Keep label and field centers equal, including fields with taller styles."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHorizontalSpacing(12)
        self.setVerticalSpacing(10)
        self.setColumnStretch(1, 1)
        self._next_row = 0

    def addRow(self, label, field=None):
        row = self._next_row
        self._next_row += 1
        if field is None:
            self.addWidget(label, row, 0, 1, 2)
            return
        caption = QLabel(label)
        caption.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.addWidget(caption, row, 0)
        if isinstance(field, QWidget):
            self.addWidget(field, row, 1)
            caption.setBuddy(field)
        else:
            self.addLayout(field, row, 1)
            if field.itemAt(0) and field.itemAt(0).widget():
                caption.setBuddy(field.itemAt(0).widget())


def settings_form(parent=None):
    return SettingsForm(parent)


def _path_field(path, name):
    field = QLineEdit(str(path))
    field.setReadOnly(True)
    field.setCursorPosition(0)
    field.setAccessibleName(name)
    field.setToolTip(str(path))
    return field


def _pick_component_folder(parent, kind, current):
    name = 'OCR 组件' if kind == 'ocr' else '任务缓存'
    path = QFileDialog.getExistingDirectory(
        parent, f'选择{name}所在文件夹（自动建立 ZhiXuPDF/{kind}）', str(current.parent))
    return Path(path) / 'ZhiXuPDF' / kind if path else None


def _open_folder(parent, path):
    path = Path(path)
    if not path.is_dir():
        QMessageBox.information(parent, '文件夹无法访问', f'此位置尚未创建、已清理或暂时不可访问：\n{path}')
    elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
        QMessageBox.warning(parent, '无法打开文件夹', f'请在文件资源管理器中打开：\n{path}')


class RuntimeInstallDialog(QDialog):
    def __init__(self, archive, parent=None):
        super().__init__(parent)
        self.setWindowTitle('安装 OCR 增强包')
        self.setObjectName('ocrInstaller')
        set_theme_style(self, SETTINGS_QSS)
        self.setMinimumWidth(620)
        self.directory = storage.directory('ocr')
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        hint(layout, '完整校验后安装到以下位置；原 ZIP 保留，已有组件会保留一份备份。')
        form = settings_form()
        form.addRow('本地 ZIP', _path_field(archive, 'OCR 增强包 ZIP 路径'))
        self.target = _path_field(self.directory, 'OCR 组件安装目录')
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.target, 1)
        choose = QPushButton('选择位置…')
        choose.clicked.connect(self.choose_directory)
        row.addWidget(choose)
        form.addRow('安装位置', row)
        layout.addLayout(form)
        hint(layout, '旧组件备份保存在安装目录的上一级，可在“文件 → 存储管理”中查看和清理。')
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        start = box.addButton('安装到此位置', QDialogButtonBox.ButtonRole.AcceptRole)
        start.setObjectName('primary')
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def choose_directory(self):
        path = _pick_component_folder(self, 'ocr', self.directory)
        if path:
            self.directory = path
            self.target.setText(str(path))
            self.target.setCursorPosition(0)
            self.target.setToolTip(str(path))


class StorageDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('存储管理')
        self.setObjectName('storageSettings')
        available = self.screen().availableGeometry()
        width = min(780, available.width() - 48)
        self.setMinimumWidth(min(650, width))
        self.resize(width, min(680, available.height() - 64))
        set_theme_style(self, SETTINGS_QSS)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)
        hint(outer, '查看文件位置和占用空间。位置更改立即保存，新任务使用新位置。')
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(12)
        self.paths = {}
        for kind, title, explanation in (
            ('ocr', 'OCR 组件', '模型、字典与推理程序。迁移会复制并校验，原位置保留。'),
            ('cache', '任务缓存', '识别图像、压缩预览和转换中间文件；任务结束或取消后自动清理。'),
            ('output', '默认输出', '新导出任务使用此位置；每次导出仍可另选文件夹，已有文件按原有规则保护。'),
        ):
            card = QFrame()
            card.setObjectName('card')
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 12)
            card_layout.setSpacing(10)
            heading = QLabel(title)
            font = heading.font()
            font.setBold(True)
            heading.setFont(font)
            card_layout.addWidget(heading)
            hint(card_layout, explanation)
            field = _path_field('', title + '位置')
            self.paths[kind] = field
            card_layout.addWidget(field)
            row = QHBoxLayout()
            row.setSpacing(8)
            for text, callback in (
                ('选择位置…', lambda checked=False, k=kind: self.choose_directory(k)),
                ('恢复默认', lambda checked=False, k=kind: self.restore_default(k)),
                ('打开目录', lambda checked=False, k=kind: self.open_directory(k)),
            ):
                button = QPushButton(text)
                button.setAutoDefault(False)
                button.setAccessibleName(title + text)
                button.clicked.connect(callback)
                row.addWidget(button)
            row.addStretch()
            if kind == 'ocr':
                install = QPushButton('安装增强包…')
                install.setAutoDefault(False)
                install.clicked.connect(self.install_package)
                row.addWidget(install)
            if kind == 'cache':
                clean = QPushButton('清理缓存…')
                clean.setAutoDefault(False)
                clean.clicked.connect(lambda: self.clean('cache'))
                row.addWidget(clean)
            card_layout.addLayout(row)
            layout.addWidget(card)
        self.usage = hint(layout, '点击“统计用量”查看当前组件、可清理缓存和旧备份的占用空间。')
        self.usage.setTextFormat(Qt.TextFormat.PlainText)
        usage_row = QHBoxLayout()
        usage_row.setSpacing(8)
        count = QPushButton('统计用量')
        count.clicked.connect(self.refresh_usage)
        usage_row.addWidget(count)
        backups = QPushButton('查看旧备份')
        backups.clicked.connect(self.open_backups)
        usage_row.addWidget(backups)
        clean = QPushButton('清理旧备份…')
        clean.clicked.connect(lambda: self.clean('backup'))
        usage_row.addWidget(clean)
        usage_row.addStretch()
        layout.addLayout(usage_row)
        details = QPushButton('其他文件位置 ▸')
        details.setCheckable(True)
        details.setAutoDefault(False)
        layout.addWidget(details)
        self.details = QWidget()
        form = settings_form(self.details)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow('程序目录', _path_field(storage.program_directory(), '程序所在目录'))
        form.addRow('偏好设置', _path_field(storage.settings_location(), '偏好设置保存位置'))
        self.install_parent = _path_field('', '安装暂存与备份所在目录')
        form.addRow('安装与备份', self.install_parent)
        form.addRow('系统临时', _path_field(tempfile.gettempdir(), '系统临时文件夹'))
        self.previous = QComboBox()
        self.previous.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.previous.setMinimumContentsLength(24)
        self.previous.setAccessibleName('保留的原组件及缓存位置')
        history = QHBoxLayout()
        history.setSpacing(8)
        history.addWidget(self.previous, 1)
        open_old = QPushButton('打开')
        open_old.clicked.connect(lambda: _open_folder(self, self.previous.currentData())
                                 if self.previous.currentData() else None)
        history.addWidget(open_old)
        form.addRow('以前位置', history)
        clean_previous = QPushButton('清理以前组件…')
        clean_previous.setAutoDefault(False)
        clean_previous.clicked.connect(lambda: self.clean('previous-ocr'))
        form.addRow('', clean_previous)
        note = QLabel('原 ZIP 保存在您选择文件时的位置，不另存一份。\n'
                      'PDF、Word、Excel、TXT 和图片结果保存在各导出窗口显示的位置。\n'
                      '输出校验临时文件跟随输出目录，正常结束会删除。页面预览和搜索缓存位于内存。\n'
                      '安装暂存在安装目录上一级的 .ocr-install-*；正常结束会删除。\n'
                      'Word 等外部程序可能使用系统临时文件夹，其临时文件按各自设置管理。\n'
                      '迁移后原组件目录保留以便恢复；确认新组件可用后，可单独清理以前组件。')
        note.setWordWrap(True)
        form.addRow(note)
        self.details.hide()
        details.toggled.connect(self.details.setVisible)
        details.toggled.connect(lambda checked: details.setText('其他文件位置 ▾' if checked else '其他文件位置 ▸'))
        layout.addWidget(self.details)
        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        self.feedback = hint(outer, '')
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.button(QDialogButtonBox.StandardButton.Close).setText('关闭')
        box.rejected.connect(self.reject)
        outer.addWidget(box)
        self.refresh_paths()

    def refresh_paths(self):
        for kind, field in self.paths.items():
            path = storage.output_directory() if kind == 'output' else storage.directory(kind)
            text = str(path) if path else '按各功能原有默认位置（通常与源文件相同）'
            field.setText(text)
            field.setCursorPosition(0)
            field.setToolTip(text)
        parent = str(storage.directory('ocr').parent)
        self.install_parent.setText(parent)
        self.install_parent.setCursorPosition(0)
        self.install_parent.setToolTip(parent)
        self.previous.clear()
        for kind, label in (('ocr', '原 OCR'), ('cache', '原缓存')):
            for path in storage.previous_directories(kind):
                self.previous.addItem(f'{label}：{path}', str(path))

    def _busy(self):
        if any(getattr(widget, '_task_dialog', None) is not None
               or (getattr(widget, '_conv_thread', None) is not None
                   and widget._conv_thread.isRunning()) for widget in QApplication.topLevelWidgets()):
            self.feedback.setText('请等待当前文件任务结束，再更改位置或清理文件。')
            return True
        return False

    def choose_directory(self, kind):
        if self._busy():
            return
        if kind == 'output':
            selected = QFileDialog.getExistingDirectory(self, '选择默认输出目录', str(storage.output_directory() or ''))
            path = Path(selected) if selected else None
        else:
            path = _pick_component_folder(self, kind, storage.directory(kind))
        if path:
            self.change_directory(kind, path)

    def restore_default(self, kind):
        if not self._busy():
            self.change_directory(kind, None if kind == 'output' else storage.data_directory() / kind)

    def change_directory(self, kind, path):
        try:
            storage.check_directory(kind, path)
            if kind == 'ocr':
                run_operation(self, '迁移 OCR 组件', lambda progress, cancel:
                              relocate_runtime(storage.directory('ocr'), path, progress, cancel), cancellable=True)
            elif path:
                storage.writable_directory(path)
            storage.save_directory(kind, path)
            self.refresh_paths()
            self.feedback.setText('位置已保存。原组件目录保留；新任务将使用新位置。' if kind == 'ocr'
                                  else '位置已保存，新任务将使用新位置。')
        except OperationCancelled:
            self.feedback.setText('迁移已取消，仍使用原位置。')
        except (OSError, OCRError) as exc:
            self.feedback.setText(str(exc))

    def open_directory(self, kind):
        path = storage.output_directory() if kind == 'output' else storage.directory(kind)
        if path:
            _open_folder(self, path)
        else:
            self.feedback.setText('尚未设置统一输出目录，可在每个导出窗口中查看并选择位置。')

    def open_backups(self):
        parents = list(dict.fromkeys(str(path.parent) for path in
                       [storage.directory('ocr'), *storage.previous_directories('ocr')]))
        if len(parents) == 1:
            _open_folder(self, parents[0])
            return
        selected, accepted = QInputDialog.getItem(self, '查看旧备份', '选择安装或旧位置的上一级文件夹：',
                                                  parents, 0, False)
        if accepted:
            _open_folder(self, selected)

    def install_package(self):
        if self._busy():
            return
        from app.ocrui import install_local_runtime
        if install_local_runtime(self):
            self.refresh_paths()
            self.feedback.setText('OCR 增强包已安装。')

    def refresh_usage(self):
        try:
            result = run_operation(self, '统计存储用量', storage.inspect_storage, cancellable=True)
            self.usage.setText(f"OCR 组件 {result['ocr_bytes'] / 1024 ** 2:.2f} MiB · "
                               f"可清理缓存 {result['cache_bytes'] / 1024 ** 2:.2f} MiB（{result['cache_count']} 项）\n"
                               f"可清理旧备份 {result['backup_bytes'] / 1024 ** 2:.2f} MiB（{result['backup_count']} 份） · "
                               f"正在使用的缓存 {result['active']} 项，清理时跳过\n"
                               f"以前位置的组件 {result['previous_bytes'] / 1024 ** 2:.2f} MiB（{result['previous_count']} 份）")
            self.feedback.setText('\n'.join(result['notes']))
        except OperationCancelled:
            self.feedback.setText('用量统计已取消。')
        except (OSError, OCRError) as exc:
            self.feedback.setText(str(exc))

    def clean(self, kind):
        if self._busy():
            return
        description = ('删除已结束任务留下的缓存，正在使用的缓存会跳过。' if kind == 'cache'
                       else '删除经过校验的旧 OCR 组件备份。请先确认当前组件可以正常识别。')
        if kind == 'previous-ocr':
            paths = storage.previous_directories('ocr')
            if not paths:
                self.feedback.setText('没有保留的以前组件位置。')
                return
            description = ('清理迁移前保留的所有旧组件目录。请先确认新位置能够正常识别。\n'
                           '仅删除完整且未使用的组件，包含其他文件的目录会跳过：\n' + '\n'.join(str(p) for p in paths))
        if QMessageBox.question(self, '确认清理', description + '\n原文件、导出结果和当前 OCR 组件保留。',
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            removed, skipped = run_operation(self, '清理缓存' if kind == 'cache' else '清理旧备份',
                                             lambda progress, cancel: storage.clean_storage(kind, progress, cancel),
                                             cancellable=True)
            self.feedback.setText(f'已清理 {removed} 项。' + ('\n' + '\n'.join(skipped) if skipped else ''))
            self.usage.setText('清理已完成，可重新统计用量。')
        except OperationCancelled:
            self.feedback.setText('清理已停止，尚未处理的文件保留，可重新统计用量。')
        except (OSError, OCRError) as exc:
            self.feedback.setText(str(exc))
