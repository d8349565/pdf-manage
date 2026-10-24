"""Small standalone image actions for Explorer; no PDF reader window is created."""
from pathlib import Path
from tempfile import TemporaryDirectory
from dataclasses import replace

from PyQt6.QtCore import QTimer, Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout

from app.output import OperationCancelled, commit_file, stamp
from app.progress import OperationThread


STYLE = '''
QDialog { background: #F3F6F5; }
QWidget { font-family: "Segoe UI", "Microsoft YaHei UI"; font-size: 14px; color: #134E4A; }
QLabel { background: transparent; }
QLabel#heading { font-size: 22px; font-weight: 600; }
QLabel#detail { color: #526B65; }
QPushButton { background: white; border: 1px solid #C5D5D2; border-radius: 7px; padding: 9px 18px; }
QPushButton:hover { background: #EAF4EF; border-color: #0F766E; }
QPushButton:focus { border: 2px solid #0F766E; }
QPushButton:disabled { color: #84958F; background: #EFF3F0; }
QPushButton#primary { background: #0F766E; color: white; border-color: #0F766E; }
QProgressBar { border: 1px solid #C5D5D2; border-radius: 5px; background: white; height: 10px; }
QProgressBar::chunk { background: #0F766E; border-radius: 4px; }
'''


def size_text(size):
    return f'{size / 1024 / 1024:.2f} MB' if size >= 1024 * 1024 else f'{size / 1024:.1f} KB'


class ImageQuickDialog(QDialog):
    def __init__(self, paths, mode, parent=None):
        super().__init__(parent)
        self.paths = [str(Path(path).resolve()) for path in paths]
        self.mode, self.worker, self.output, self.closing = mode, None, None, False
        title = '图片转 PDF' if mode == 'pdf' else '压缩图片'
        self.setWindowTitle(f'{title} - 纸序 PDF')
        self.setStyleSheet(STYLE)
        self.resize(520, 260)
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(14)
        heading = QLabel(title)
        heading.setObjectName('heading')
        layout.addWidget(heading)
        self.filename = QLabel()
        self.filename.setTextFormat(Qt.TextFormat.PlainText)
        self.filename.setText(Path(self.paths[0]).name if self.paths else '未收到图片')
        self.filename.setWordWrap(True)
        layout.addWidget(self.filename)
        self.status = QLabel('正在准备…')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.detail = QLabel('结果保存到原图旁边；同名文件会自动避让。' if mode == 'pdf' else
                             '保留尺寸，另存副本。JPG/WebP 适度压缩；PNG、BMP、TIFF 无损优化。')
        self.detail.setObjectName('detail')
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.open_folder = QPushButton('打开文件夹')
        self.open_folder.setEnabled(False)
        self.open_folder.clicked.connect(self.show_output)
        buttons.addWidget(self.open_folder)
        self.finish = QPushButton('取消')
        self.finish.setObjectName('primary')
        self.finish.clicked.connect(self.reject)
        buttons.addWidget(self.finish)
        layout.addLayout(buttons)
        QTimer.singleShot(0, self.start)

    def work(self, report, cancel):
        from app.imagecompress import IMAGE_SUFFIXES, available_path, compress_image
        if not self.paths:
            raise ValueError('没有收到图片，请重新右键选择图片。')
        if any(not Path(path).is_file() or Path(path).suffix.lower() not in IMAGE_SUFFIXES for path in self.paths):
            raise ValueError('请选择 JPG、PNG、WebP、BMP 或 TIFF 图片。')
        if self.mode == 'pdf':
            from app.imaging import images_to_pdf
            from app.pdfio import PdfStore
            target = available_path(self.paths[0], '.pdf', tag='_合并' if len(self.paths) > 1 else '')
            store = PdfStore()
            try:
                original = [stamp(path) for path in self.paths]
                # Stage on the output volume, then commit exclusively. A file
                # created by another application during conversion must survive.
                with TemporaryDirectory(prefix='.zhixu-image-pdf-', dir=target.parent) as folder:
                    result = images_to_pdf(self.paths, str(Path(folder) / 'prepared.pdf'), store, report, cancel)
                    if any(stamp(path) != before for path, before in zip(self.paths, original)):
                        raise OSError('处理期间原图已被其他程序修改，请重新尝试。')
                    commit_file(result.path, target, None, cancel=cancel)
                    return replace(result, path=str(target))
            finally:
                store.close_all()
        if self.mode == 'compress':
            if len(self.paths) != 1:
                raise ValueError('请一次选择一张图片进行快捷压缩。')
            return compress_image(self.paths[0], progress=report, cancel=cancel)
        raise ValueError('未知图片操作。')

    def start(self):
        if self.worker is not None or self.closing:
            return
        self.worker = OperationThread(self.work, self, cancellable=True)
        self.worker.progress.connect(self.update_progress)
        self.worker.finished.connect(self.completed)
        self.worker.start()

    def update_progress(self, current, total, message):
        if not self.closing:
            self.status.setText(message)
            self.progress.setRange(0, total)
            self.progress.setValue(current)

    def completed(self):
        self.worker.wait()
        self.progress.hide()
        self.finish.setText('完成')
        self.finish.setEnabled(True)
        if self.closing:
            super().done(QDialog.DialogCode.Rejected)
            return
        error = self.worker.error
        if error:
            self.status.setText('操作已取消，原图保留。' if isinstance(error, OperationCancelled) else f'未完成：{error}')
            if not isinstance(error, OperationCancelled):
                self.detail.setText('请确认图片有效，且所在文件夹允许写入。原图未被覆盖。')
            return
        result = self.worker.result
        self.output = result.path
        if self.mode == 'pdf':
            self.status.setText(f'PDF 已生成：\n{result.path}')
        elif result.path:
            self.status.setText(f'{size_text(result.before)} → {size_text(result.after)}，减少 {(1-result.after/result.before)*100:.1f}%\n{result.path}')
        else:
            self.status.setText('图片已经比较紧凑，本次没有找到更小的结果，原图保留。')
        self.open_folder.setEnabled(bool(self.output))

    def show_output(self):
        if self.output:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.output).parent)))

    def done(self, result):
        if self.worker is not None and self.worker.isRunning():
            self.closing = True
            self.worker.cancel_event.set()
            self.status.setText('正在停止，原图保留…')
            self.finish.setEnabled(False)
            return
        self.closing = True
        super().done(result)
