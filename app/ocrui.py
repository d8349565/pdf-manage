"""Small OCR setup/result dialogs using the existing task runner."""
from pathlib import Path

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                            QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                            QPushButton, QVBoxLayout)

from app.exportdialogs import hint
from app.ocr.contracts import OCROptions, OCRError
from app.ocr.model_manager import default_runtime_dir, install_runtime
from app.output import OperationCancelled, stamp
from app.progress import run_operation
from app.storage import check_directory, export_path, save_directory
from app.workspace import parse_page_range


def unused_output(source):
    source = Path(source)
    candidate = Path(export_path(source.with_name(source.stem + '_OCR.pdf')))
    number = 2
    while candidate.exists():
        candidate = candidate.with_name(f'{source.stem}_OCR_{number}.pdf')
        number += 1
    return str(candidate)


def install_local_runtime(parent):
    archive,_=QFileDialog.getOpenFileName(parent,'选择可信来源的离线 OCR 增强包','','OCR 增强包 (*.zip)')
    if not archive:
        return False
    from app.storageui import RuntimeInstallDialog
    dialog = RuntimeInstallDialog(archive, parent)
    try:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        target = dialog.directory
        check_directory('ocr', target)
        installed = run_operation(parent,'安装 OCR 增强包',
                      lambda progress,cancel:install_runtime(archive,directory=target,progress=progress,cancel=cancel),cancellable=True)
        save_directory('ocr', target)
        QMessageBox.information(parent, 'OCR 增强包已安装', f'组件保存到：\n{installed}\n\n原 ZIP 保留；旧组件备份可在存储管理中查看。')
        return True
    except OperationCancelled:
        QMessageBox.information(parent,'安装已取消','原有 OCR 组件保留。')
    except (OCRError,OSError) as exc:
        QMessageBox.warning(parent,'OCR 增强包未安装',str(exc))
    finally:
        dialog.deleteLater()
    return False


class OCRDialog(QDialog):
    def __init__(self, source, count, current, parent=None):
        super().__init__(parent)
        self.count, self.current = count, current
        self.setWindowTitle('OCR 文字识别')
        from app.storageui import SETTINGS_QSS, settings_form
        self.setObjectName('ocrSettings')
        self.setStyleSheet(SETTINGS_QSS)
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        hint(layout, '识别扫描件中的中英文文字，生成可搜索、可复制的 PDF 副本。')
        self.environment = QLabel()
        self.environment.setWordWrap(True)
        self.environment.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.environment)
        self.install_button = QPushButton('安装本地 OCR 增强包…')
        self.install_button.clicked.connect(self.install_package)
        row = QHBoxLayout()
        row.addWidget(self.install_button, 1)
        locations = QPushButton('存储位置…')
        locations.clicked.connect(self.manage_storage)
        row.addWidget(locations)
        layout.addLayout(row)
        form = settings_form()
        self.scope = QComboBox()
        self.scope.addItems(['当前页', '全部页面', '指定页码'])
        self.scope.setCurrentIndex(1)
        self.scope.setAccessibleName('识别范围')
        form.addRow('识别范围', self.scope)
        self.range = QLineEdit()
        self.range.setPlaceholderText('例如 1-3,5,8')
        self.range.setAccessibleName('指定识别页码')
        form.addRow('指定页码', self.range)
        layout.addLayout(form)
        self.skip = QCheckBox('跳过已有文字的页面')
        self.skip.setChecked(True)
        layout.addWidget(self.skip)
        output_row = QHBoxLayout()
        self.output = QLineEdit(unused_output(source))
        self.output.setAccessibleName('OCR 输出 PDF 路径')
        output_row.addWidget(self.output, 1)
        choose = QPushButton('选择位置…')
        choose.clicked.connect(self.choose_output)
        output_row.addWidget(choose)
        layout.addLayout(output_row)
        self.feedback = hint(layout, '')
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        hint(layout, '全部处理在本机完成。原 PDF 保留；金额和编号请核对原件。')
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.start = box.addButton('开始识别', QDialogButtonBox.ButtonRole.AcceptRole)
        self.start.setObjectName('primary')
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        self.scope.currentIndexChanged.connect(self.validate)
        self.range.textChanged.connect(self.validate)
        self.output.textChanged.connect(self.validate)
        self.source = Path(source).resolve()
        self.refresh_environment()
        self.validate()

    def indices(self):
        if self.scope.currentIndex() == 0:
            return [self.current]
        if self.scope.currentIndex() == 1:
            return list(range(self.count))
        return list(dict.fromkeys(parse_page_range(self.range.text(), self.count)))

    def refresh_environment(self):
        try:
            root = default_runtime_dir()
            self.runtime_ready = (root / 'manifest.json').is_file()
            location = f'\n组件位置：{root}'
        except OCRError:
            self.runtime_ready = False
            location = ''
        self.environment.setText(('OCR 增强包已安装 · 开始前自动检查完整性' if self.runtime_ready
                                 else '尚未安装 OCR 增强包，请先安装本地离线 ZIP。') + location)
        self.install_button.setText('重新安装本地 OCR 增强包…' if self.runtime_ready else '安装本地 OCR 增强包…')

    def validate(self):
        specified = self.scope.currentIndex() == 2
        self.range.setEnabled(specified)
        try:
            self.indices()
            output = Path(self.output.text().strip()).resolve()
            if output == self.source:
                raise ValueError('请选择新的输出文件，保留原 PDF。')
            if output.suffix.lower() != '.pdf' or not output.parent.is_dir():
                raise ValueError('请选择已有文件夹中的 PDF 输出路径。')
            if output.exists():
                raise ValueError('该文件已存在，请选择新的文件名。')
            self.feedback.setText('')
            self.start.setEnabled(self.runtime_ready)
        except (ValueError, OSError) as exc:
            self.feedback.setText(str(exc))
            self.start.setEnabled(False)

    def choose_output(self):
        path, _ = QFileDialog.getSaveFileName(self, '保存 OCR 副本', self.output.text(), 'PDF 文件 (*.pdf)',
                                             options=QFileDialog.Option.DontConfirmOverwrite)
        if path:
            self.output.setText(path if Path(path).suffix.lower() == '.pdf' else path + '.pdf')

    def install_package(self):
        if install_local_runtime(self):
            self.refresh_environment()
            self.validate()

    def manage_storage(self):
        from app.storageui import StorageDialog
        dialog = StorageDialog(self)
        dialog.exec()
        dialog.deleteLater()
        self.refresh_environment()
        self.validate()

    def accept(self):
        self.validate()
        if self.start.isEnabled():
            super().accept()


def show_result(host, result):
    dialog = QDialog(host)
    dialog.setWindowTitle('OCR 识别完成')
    dialog.setMinimumWidth(520)
    layout = QVBoxLayout(dialog)
    total = len(result.processed_pages) + len(result.skipped_pages) + len(result.failed_pages)
    text = f'处理 {total} 页 · 识别 {len(result.processed_pages)} 页 · 跳过 {len(result.skipped_pages)} 页 · 失败 {len(result.failed_pages)} 页'
    label = QLabel(text + '\n\n' + result.output_path)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    layout.addWidget(label)
    details = [f'第 {i + 1} 页：{why}' for i, why in result.failed_pages]
    details.extend(result.warnings)
    if details:
        detail = QLabel('\n'.join(details))
        detail.setWordWrap(True)
        detail.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(detail)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    open_pdf = buttons.addButton('打开识别后的 PDF', QDialogButtonBox.ButtonRole.ActionRole)
    folder = buttons.addButton('打开文件夹', QDialogButtonBox.ButtonRole.ActionRole)
    def open_output():
        dialog.accept()
        host._ingest([result.output_path], replace=True)
    open_pdf.clicked.connect(open_output)
    folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(result.output_path).parent))))
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()
    dialog.deleteLater()


def open_ocr(host):
    if not host.ws.pages:
        QMessageBox.information(host, 'OCR 文字识别', '请先打开要识别的 PDF。')
        return
    if host.ws.path and host.ws.path.lower().endswith('.ofd') and not host.save(True):
        return
    if host.ws.dirty or not host.ws.path:
        QMessageBox.information(host, '先保存当前文档', '当前工作区有未保存的修改，请先保存或另存 PDF，再开始 OCR。')
        return
    if not host._allow_operation('content_edit') or not host._allow_operation('copy'):
        return
    # Only immutable metadata is read here; the worker opens its own Document.
    source = host.store._source(host.ws.pages[0].source_id)
    if stamp(source.path) != source.disk_stamp:
        QMessageBox.warning(host, '原文件已变化', '磁盘文件与当前工作区不一致，请重新打开或另存当前文档后再识别。')
        return
    dialog = OCRDialog(host.ws.path, len(host.ws.pages), host.ws.preview_index() or 0, host)
    try:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        from app.ocr.service import run_ocr
        indices, output = dialog.indices(), dialog.output.text().strip()
        options = OCROptions(skip_existing_text=dialog.skip.isChecked(), password=source.password)
        result = run_operation(host, 'OCR 文字识别',
                               lambda progress, cancel: run_ocr(host.ws.path, output, indices, options, progress, cancel),
                               cancellable=True)
        show_result(host, result)
    except OperationCancelled:
        host.statusBar().showMessage('OCR 已取消，原 PDF 保留，临时文件已清理。', 6000)
    except OCRError as exc:
        QMessageBox.warning(host, 'OCR 未完成', str(exc))
    finally:
        dialog.deleteLater()
