"""Small standalone image actions for Explorer; no PDF reader window is created."""
from app.themes import set_theme_style
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from dataclasses import replace

from PyQt6.QtCore import QTimer, Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QLabel, QListWidget,
                            QListWidgetItem, QProgressBar, QPushButton, QVBoxLayout)

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
        if mode == 'pdf' and len(self.paths) > 1:
            self.paths.sort(key=lambda path: ([int(part) if part.isdigit() else part.casefold()
                for part in re.split(r'(\d+)', Path(path).name)], path.casefold()))
        self.mode, self.worker, self.output, self.closing = mode, None, None, False
        self.compact, self.best_result = False, None
        title = '图片转 PDF' if mode == 'pdf' else '压缩图片'
        if mode == 'pdf' and len(self.paths) > 1:
            title = '合并图片为 PDF'
        self.setWindowTitle(f'{title} - 纸序 PDF')
        set_theme_style(self, STYLE)
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
        self.order_list = None
        self.order_buttons = []
        if mode == 'pdf' and len(self.paths) > 1:
            self.filename.setText(f'已选择 {len(self.paths)} 张图片，按以下顺序合并：')
            self.order_list = QListWidget()
            self.order_list.setAccessibleName('合并图片顺序')
            self.order_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
            self.order_list.setDefaultDropAction(Qt.DropAction.MoveAction)
            for path in self.paths:
                item = QListWidgetItem(Path(path).name)
                item.setToolTip(path)
                item.setData(Qt.ItemDataRole.UserRole, path)
                self.order_list.addItem(item)
            self.order_list.setCurrentRow(0)
            layout.addWidget(self.order_list)
            order_buttons = QHBoxLayout()
            for title, delta in (('上移', -1), ('下移', 1)):
                button = QPushButton(title)
                button.clicked.connect(lambda checked=False, step=delta: self.move_image(step))
                self.order_buttons.append(button)
                order_buttons.addWidget(button)
            layout.addLayout(order_buttons)
            self.resize(520, 480)
        self.status = QLabel('按文件名自然排序，可拖动或用上移、下移调整。' if self.order_list is not None else '正在准备…')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.detail = QLabel('结果保存到原图旁边；同名文件会自动避让。' if mode == 'pdf' else
                             '保留尺寸，另存副本。PNG/BMP 自动比较无损 PNG 与 WebP；JPG/WebP 适度有损压缩。')
        self.detail.setObjectName('detail')
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0)
        self.progress.setVisible(self.order_list is None)
        layout.addWidget(self.progress)
        buttons = QHBoxLayout()
        self.smaller = QPushButton('更小体积（有损）')
        self.smaller.setVisible(mode == 'compress')
        self.smaller.setEnabled(False)
        self.smaller.setToolTip('从原图另存有损 WebP，尺寸不变；可能改变小字、颜色和纹理细节。')
        self.smaller.clicked.connect(self.make_smaller)
        buttons.addWidget(self.smaller)
        buttons.addStretch()
        self.open_pdf = QPushButton('打开 PDF')
        self.open_pdf.setVisible(False)
        self.open_pdf.clicked.connect(self.show_pdf)
        buttons.addWidget(self.open_pdf)
        self.open_folder = QPushButton('打开文件夹')
        self.open_folder.setEnabled(False)
        self.open_folder.clicked.connect(self.show_output)
        buttons.addWidget(self.open_folder)
        self.finish = QPushButton('合并并打开' if self.order_list is not None else '取消')
        self.finish.setDefault(self.order_list is not None)
        self.finish.setObjectName('primary')
        self.finish.clicked.connect(self.finish_clicked)
        buttons.addWidget(self.finish)
        layout.addLayout(buttons)
        if self.order_list is None:
            QTimer.singleShot(0, self.start)

    def move_image(self, delta):
        if self.worker is not None or self.order_list is None:
            return
        row = self.order_list.currentRow()
        if 0 <= row + delta < self.order_list.count():
            item = self.order_list.takeItem(row)
            self.order_list.insertItem(row + delta, item)
            self.order_list.setCurrentRow(row + delta)

    def finish_clicked(self):
        if self.order_list is not None and self.worker is None and not self.closing:
            self.start()
        else:
            self.reject()

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
            return compress_image(self.paths[0], progress=report, cancel=cancel, compact=self.compact,
                                  smaller_than=self.best_result.after if self.compact and self.best_result else None)
        raise ValueError('未知图片操作。')

    def start(self):
        if self.worker is not None or self.closing:
            return
        if self.order_list is not None:
            self.paths = [self.order_list.item(i).data(Qt.ItemDataRole.UserRole)
                          for i in range(self.order_list.count())]
            self.order_list.setEnabled(False)
            for button in self.order_buttons:
                button.setEnabled(False)
            self.finish.setText('取消')
            self.progress.show()
        self.worker = OperationThread(self.work, self, cancellable=True)
        self.worker.progress.connect(self.update_progress)
        self.worker.finished.connect(self.completed)
        self.worker.start()

    def make_smaller(self):
        if self.closing or self.worker is None or self.worker.isRunning():
            return
        self.compact = True
        self.worker.deleteLater()
        self.worker = None
        self.smaller.setEnabled(False)
        self.open_folder.setEnabled(False)
        self.finish.setText('取消')
        self.progress.setRange(0, 0)
        self.progress.show()
        self.status.setText('正在从原图生成更小的 WebP…')
        self.detail.setText('本次使用有损压缩，可能改变细节；保留尺寸、原图和上一份结果。')
        self.start()

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
            self.open_folder.setEnabled(bool(self.output))
            self.smaller.setEnabled(self.mode == 'compress' and self.compact and not isinstance(error, OperationCancelled))
            return
        result = self.worker.result
        if self.mode == 'pdf':
            self.output = result.path
            self.status.setText(f'PDF 已生成：\n{result.path}')
        elif result.path:
            self.output, self.best_result = result.path, result
            self.status.setText(f'{size_text(result.before)} → {size_text(result.after)}，减少 {(1-result.after/result.before)*100:.1f}%\n{Path(result.path).name}')
            self.status.setToolTip(result.path)
            self.detail.setText(result.description + ('。原图和上一份结果均保留。' if self.compact else
                                '。还想更小，可选“更小体积（有损）”另存 WebP。'))
        elif self.compact and self.best_result:
            self.status.setText(f'本次没有比已保存结果更小的版本，保留已有文件：\n{Path(self.output).name}')
            self.detail.setText(self.best_result.description + '。原图保留。')
        else:
            self.status.setText('图片已经比较紧凑，本次没有找到更小的结果，原图保留。')
        self.smaller.setEnabled(self.mode == 'compress' and not self.compact)
        self.open_folder.setEnabled(bool(self.output))
        if self.mode == 'pdf':
            self.open_pdf.setVisible(True)
            self.show_pdf()

    def show_pdf(self):
        if self.output and not QDesktopServices.openUrl(QUrl.fromLocalFile(self.output)):
            self.detail.setText('PDF 已保存，但暂时无法自动打开。可点“打开 PDF”重试，或打开文件夹查看。')

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
