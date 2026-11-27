"""A short, replayable guide illustrated with the current application's UI."""
from app.i18n import tr
from pathlib import Path

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout,
)

from app import APP_NAME, APP_PROJECT_URL
from app.themes import set_theme_style

_OCR_INSTALL_STEP = 5
_OCR_RECOGNITION_STEP = 6


class _GuideImage(QLabel):
    def __init__(self):
        super().__init__()
        self.source = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.setAccessibleName(tr('当前版本的实际界面示例'))

    def set_image(self, name):
        path = Path(__file__).resolve().parent / 'assets' / 'quickstart' / name
        self.source = QPixmap(str(path))
        self._fit()

    def _fit(self):
        if self.source.isNull():
            self.clear()
            return
        ratio = self.devicePixelRatioF()
        bounds = (self.contentsRect().size() * ratio).boundedTo(self.source.size())
        preview = self.source.scaled(bounds, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
        preview.setDevicePixelRatio(ratio)
        self.setPixmap(preview)

    def event(self, event):
        handled = super().event(event)
        if event.type() == QEvent.Type.DevicePixelRatioChange:
            self._fit()
        return handled

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()


class QuickStartDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName('quickStartDialog')
        self.setWindowTitle(tr('{p0} · 操作指南', p0=tr(APP_NAME)))
        self.resize(800, 640)
        set_theme_style(self, '''
            QDialog#quickStartDialog { background: #FBFCFA; color: #173C36; }
            QDialog#quickStartDialog QLabel { background: transparent; font-size: 14px; }
            QDialog#quickStartDialog QLabel#quickStartTitle { font-size: 22px; font-weight: 600; }
            QDialog#quickStartDialog QLabel#quickStartHint { color: #65796E; font-size: 12px; }
        ''')
        drag = (tr('按住 Alt，再按住鼠标左键拖动') if parent.shortcut_manager.uses_reader_space()
                else tr('按住空格或 Alt，再按住鼠标左键拖动'))
        # Each image is a 3× capture of the illustrated controls using a generated document.
        self.steps = (
            (tr('拖入文件，开始阅读'), 'home.png',
             tr('把 PDF、OFD 或图片拖进窗口即可打开，也可点击首页右上角的“打开文件”。')),
            (tr('放大或缩小，看清细节'), 'zoom.png',
             tr('按住 Ctrl 滚动鼠标滚轮：向上放大，向下缩小。也可点击顶部的放大、缩小按钮，或点击它们旁边的“适合宽度”。')),
            (tr('放大后，上下左右移动页面'), 'reading.png',
             tr('{p0}，页面就会跟着移动。也可在“视图 → 阅读布局与适配”选择“手形拖动”，直接拖动页面。', p0=f'{drag}')),
            (tr('拖动缩略图，调整页序'), 'arrange.png',
             tr('点击顶部的“整理页面”图标，再拖动页面调整顺序；左侧缩略图也支持拖动。操作有误时，可从“编辑”菜单撤销。')),
            (tr('固定还是自动收起，由你选择'), 'sidebar.png',
             tr('鼠标靠近左侧箭头可展开侧栏。点击顶部的“固定”保持展开，再点一次恢复自动收起；下次打开软件会沿用你的选择。')),
            (tr('首次使用，安装 OCR 增强包'), 'ocr-install.png',
             tr('点下方“导入 OCR 增强包…”，直接选择下载好的 ZIP，无需解压，再点“安装到此位置”。打开 PDF 后，也可从“工具 → 文字识别（OCR）”点“安装本地 OCR 增强包…”。')),
            (tr('识别扫描件，让文字可以搜索和复制'), 'ocr-run.png',
             tr('打开扫描 PDF，点“工具 → 文字识别（OCR）”。选好“识别范围”和副本的保存位置，点“开始识别”。完成后点“打开识别后的 PDF”，即可搜索和复制文字，原 PDF 会保留。')),
        )
        self.step = 0
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        self.heading = QLabel()
        self.heading.setObjectName('quickStartTitle')
        self.heading.setWordWrap(True)
        root.addWidget(self.heading)
        self.preview = _GuideImage()
        root.addWidget(self.preview, 1)
        self.description = QLabel()
        self.description.setWordWrap(True)
        self.description.setMinimumHeight(64)
        root.addWidget(self.description)
        package = 'ZhiXuPDF-OCR-win-x64.zip'
        download_url = f'{APP_PROJECT_URL}/releases/latest/download/{package}'
        self.ocr_download = QLabel(f'<a href="{download_url}">{tr("下载 OCR 增强包")} ({package})</a>')
        self.ocr_download.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.ocr_download.setOpenExternalLinks(True)
        self.ocr_download.setWordWrap(True)
        root.addWidget(self.ocr_download)
        hint = QLabel(tr('关闭后，可从右上角“帮助与关于 → 操作指南”重新查看。'))
        hint.setObjectName('quickStartHint')
        hint.setWordWrap(True)
        root.addWidget(hint)
        footer = QHBoxLayout()
        self.progress = QLabel()
        self.progress.setObjectName('quickStartHint')
        footer.addWidget(self.progress)
        self.import_ocr_button = QPushButton(tr('导入 OCR 增强包…'))
        self.import_ocr_button.clicked.connect(self._import_ocr)
        footer.addWidget(self.import_ocr_button)
        footer.addStretch(1)
        self.skip_button = QPushButton(tr('直接使用'))
        self.skip_button.clicked.connect(self.reject)
        footer.addWidget(self.skip_button)
        self.back_button = QPushButton(tr('上一步'))
        self.back_button.clicked.connect(lambda: self._show_step(self.step - 1))
        footer.addWidget(self.back_button)
        self.next_button = QPushButton(tr('下一步'))
        self.next_button.setObjectName('primary')
        self.next_button.setDefault(True)
        self.next_button.clicked.connect(self._next)
        footer.addWidget(self.next_button)
        root.addLayout(footer)
        self._show_step(0)
        self.next_button.setFocus()

    def _show_step(self, index):
        self.step = index
        heading, image, description = self.steps[index]
        self.heading.setText(heading)
        self.preview.set_image(image)
        self.description.setText(description)
        self.ocr_download.setVisible(index == _OCR_INSTALL_STEP)
        self.import_ocr_button.setVisible(index in (0, _OCR_INSTALL_STEP))
        self.progress.setText(f'{index + 1} / {len(self.steps)}')
        self.back_button.setEnabled(index > 0)
        self.skip_button.setVisible(index < len(self.steps) - 1)
        self.next_button.setText(tr('开始使用') if index == len(self.steps) - 1 else tr('下一步'))

    def _import_ocr(self):
        from app.ocrui import install_local_runtime
        if install_local_runtime(self):
            self._show_step(_OCR_RECOGNITION_STEP)

    def _next(self):
        if self.step == len(self.steps) - 1:
            self.accept()
        else:
            self._show_step(self.step + 1)
