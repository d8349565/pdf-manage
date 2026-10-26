"""Image-only PDF settings and a recovery route for uncertain signatures."""
from app.themes import set_theme_style
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                            QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout)

from app.exportdialogs import EncryptionDialog, hint
from app.output import OperationCancelled
from app.pdfio import PdfError
from app.progress import run_operation
from app.rasterpdf import RasterOptions, rasterize_pdf
from app.storage import export_path
from app.storageui import SETTINGS_QSS, settings_form
from app.workspace import parse_page_range


def unused_path(source):
    source = Path(source or '文档.pdf')
    root = Path(export_path(source.with_name(source.stem + '_影像副本.pdf')))
    candidate = root
    number = 2
    while candidate.exists():
        candidate = root.with_name(f'{root.stem}_{number}.pdf')
        number += 1
    return str(candidate)


class RasterDialog(QDialog):
    def __init__(self, count, current, selected, source, parent=None, *, recovery=False):
        super().__init__(parent)
        self.count, self.current, self.selected = count, current, tuple(selected)
        self.setWindowTitle('制作影像副本' if recovery else 'PDF 转图片版')
        self.setObjectName('ocrSettings')
        set_theme_style(self, SETTINGS_QSS)
        self.setMinimumWidth(580)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        hint(layout, '将当前页面另存为图片版 PDF，保留页面显示效果。原文件保持不变。')
        if recovery:
            hint(layout, '原件签名结构无法确认。副本只保存页面影像，不保留数字签名效力。')
        form = settings_form()
        self.scope = QComboBox()
        self.scope.addItems(['全部页面', '当前页', '所选页面', '指定页码'])
        self.scope.setAccessibleName('影像副本页面范围')
        form.addRow('转换范围', self.scope)
        self.range = QLineEdit()
        self.range.setPlaceholderText('例如 1-3,5,8')
        self.range.setAccessibleName('影像副本指定页码')
        form.addRow('指定页码', self.range)
        self.quality = QComboBox()
        self.quality.addItems(['清晰 · 200 DPI，JPEG（推荐）', '更清晰 · 300 DPI，无损图片（文件较大）',
                               '较小文件 · 150 DPI，JPEG', '无损图片 · 200 DPI（文件较大）'])
        self.quality.setAccessibleName('影像副本清晰度')
        form.addRow('清晰程度', self.quality)
        layout.addLayout(form)
        hint(layout, '推荐档位适度压缩图片以兼顾清晰度与体积；无损图片可能使文件明显变大。')
        self.output = QLineEdit(unused_path(source))
        self.output.setAccessibleName('影像副本 PDF 保存路径')
        self.output.setCursorPosition(0)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.output, 1)
        choose = QPushButton('选择位置…')
        choose.clicked.connect(self.choose_output)
        row.addWidget(choose)
        layout.addLayout(row)
        hint(layout, '文字、批注和表单外观固化；链接、附件与可填写表单不保留。需要搜索和复制文字时，可对副本使用 OCR。')
        self.feedback = hint(layout, '')
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.start = box.addButton('生成影像副本', QDialogButtonBox.ButtonRole.AcceptRole)
        self.start.setObjectName('primary')
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        self.scope.currentIndexChanged.connect(self.validate)
        self.range.textChanged.connect(self.validate)
        self.output.textChanged.connect(self.validate)
        self.source = Path(source).resolve() if source else None
        self.validate()

    def indices(self):
        mode = self.scope.currentIndex()
        if mode == 0:
            return list(range(self.count))
        if mode == 1:
            return [self.current]
        if mode == 2:
            if not self.selected:
                raise ValueError('请先在工作区选择页面，或改用其他范围。')
            return list(self.selected)
        if not self.range.text().strip():
            raise ValueError('请输入需要转换的页码。')
        return list(dict.fromkeys(parse_page_range(self.range.text(), self.count)))

    def options(self, encryption=None, unencrypted_confirmed=False):
        dpi, image_format = ((200, 'jpeg'), (300, 'png'), (150, 'jpeg'), (200, 'png'))[self.quality.currentIndex()]
        return RasterOptions(dpi, image_format, encryption, unencrypted_confirmed)

    def validate(self):
        self.range.setEnabled(self.scope.currentIndex() == 3)
        try:
            self.indices()
            path = Path(self.output.text().strip()).resolve()
            if path == self.source:
                raise ValueError('请选择新的文件名，保留原件。')
            if path.suffix.lower() != '.pdf' or not path.parent.is_dir():
                raise ValueError('请选择已有文件夹中的 PDF 保存位置。')
            if path.exists():
                raise ValueError('该文件已存在，请选择新的文件名。')
            self.feedback.setText('')
            self.start.setEnabled(True)
        except (ValueError, OSError) as exc:
            self.feedback.setText(str(exc))
            self.start.setEnabled(False)

    def choose_output(self):
        path, _ = QFileDialog.getSaveFileName(self, '保存影像副本', self.output.text(), 'PDF (*.pdf)',
                                             options=QFileDialog.Option.DontConfirmOverwrite)
        if path:
            self.output.setText(path if path.lower().endswith('.pdf') else path + '.pdf')
            self.output.setCursorPosition(0)

    def accept(self):
        self.validate()
        if self.start.isEnabled():
            super().accept()


def open_raster(host, *, recovery=False):
    if not host.ws.pages:
        QMessageBox.information(host, '影像副本', '请先打开 PDF。')
        return
    if not host._allow_operation('raster_copy'):
        return
    dialog = RasterDialog(len(host.ws.pages), host.ws.preview_index() or 0,
                          sorted(host.ws.selected), host.ws.path, host, recovery=recovery)
    try:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        pages = [host.ws.pages[index] for index in dialog.indices()]
        if host.store.is_source_destination(dialog.output.text().strip()):
            raise PdfError('影像副本不能覆盖任何已打开的来源文件，请另选文件名。')
        encryption = None
        unencrypted_confirmed = False
        if any(host.store._source(ref.source_id).safety.encrypted for ref in pages):
            protected = EncryptionDialog(host, allow_plain=True)
            try:
                if protected.exec() != QDialog.DialogCode.Accepted:
                    return
                encryption = protected.setting
                unencrypted_confirmed = protected.plain.isChecked()
            finally:
                protected.deleteLater()
        options = dialog.options(encryption, unencrypted_confirmed)
        output, outline = dialog.output.text().strip(), host.ws.outline
        result = run_operation(host, '生成影像副本', lambda progress, cancel:
                               rasterize_pdf(host.store, pages, output, options,
                                             progress, cancel, outline=outline), cancellable=True)
        show_result(host, result)
    except OperationCancelled:
        host.statusBar().showMessage('影像副本已取消，原件保留。', 6000)
    except (PdfError, OSError, RuntimeError, ValueError) as exc:
        QMessageBox.warning(host, '影像副本未生成', str(exc))
    finally:
        dialog.deleteLater()


def show_result(host, result):
    box = QMessageBox(host)
    box.setWindowTitle('影像副本已生成')
    box.setText(f'文件已保存到：\n{result.path}')
    box.setInformativeText('\n'.join(result.warnings))
    open_pdf = box.addButton('打开影像副本', QMessageBox.ButtonRole.AcceptRole)
    folder = box.addButton('打开文件夹', QMessageBox.ButtonRole.ActionRole)
    box.addButton('关闭', QMessageBox.ButtonRole.RejectRole)
    box.exec()
    if box.clickedButton() is open_pdf:
        host._ingest([result.path], replace=True)
    elif box.clickedButton() is folder:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(result.path).parent)))
