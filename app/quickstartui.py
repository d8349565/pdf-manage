"""A short, replayable guide illustrated with the current application's UI."""
from pathlib import Path

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout,
)

from app import APP_NAME
from app.themes import set_theme_style


class _GuideImage(QLabel):
    def __init__(self):
        super().__init__()
        self.source = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.setAccessibleName('当前版本的实际界面示例')

    def set_image(self, name, crop):
        path = Path(__file__).resolve().parent / 'assets' / 'quickstart' / name
        self.source = QPixmap(str(path)).copy(QRect(*crop))
        self._fit()

    def _fit(self):
        self.setPixmap(self.source.scaled(self.contentsRect().size(), Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()


class QuickStartDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName('quickStartDialog')
        self.setWindowTitle(f'{APP_NAME} · 操作指南')
        self.resize(800, 640)
        set_theme_style(self, '''
            QDialog#quickStartDialog { background: #FBFCFA; color: #173C36; }
            QDialog#quickStartDialog QLabel { background: transparent; font-size: 14px; }
            QDialog#quickStartDialog QLabel#quickStartTitle { font-size: 22px; font-weight: 600; }
            QDialog#quickStartDialog QLabel#quickStartHint { color: #65796E; font-size: 12px; }
        ''')
        drag = ('按住 Alt，再按住鼠标左键拖动' if parent.shortcut_manager.uses_reader_space()
                else '按住空格或 Alt，再按住鼠标左键拖动')
        # These captures come from this version's MainWindow with a generated example.
        # Refresh them whenever the illustrated controls move or change appearance.
        self.steps = (
            ('拖入文件，开始阅读', 'home.png', (100, 85, 960, 450),
             '把 PDF、OFD 或图片拖进窗口即可打开，也可点击首页右上角的“打开文件”。'),
            ('放大或缩小，看清细节', 'reading.png', (570, 0, 590, 220),
             '按住 Ctrl 滚动鼠标滚轮：向上放大，向下缩小。也可点击顶部的放大、缩小按钮，或点击它们旁边的“适合宽度”。'),
            ('放大后，上下左右移动页面', 'reading.png', (310, 80, 840, 615),
             f'{drag}，页面就会跟着移动。也可在“视图 → 阅读设置”选择“手形拖动”，直接拖动页面。'),
            ('拖动缩略图，调整页序', 'arrange.png', (0, 35, 1160, 535),
             '点击顶部的“整理页面”图标，再拖动页面调整顺序；左侧缩略图也支持拖动。操作有误时，可从“编辑”菜单撤销。'),
            ('固定还是自动收起，由你选择', 'reading.png', (0, 75, 400, 320),
             '鼠标靠近左侧箭头可展开侧栏。点击顶部的“固定”保持展开，再点一次恢复自动收起；下次打开软件会沿用你的选择。'),
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
        hint = QLabel('关闭后不再自动弹出，随时可点顶部“操作指南”重新查看。')
        hint.setObjectName('quickStartHint')
        hint.setWordWrap(True)
        root.addWidget(hint)
        footer = QHBoxLayout()
        self.progress = QLabel()
        self.progress.setObjectName('quickStartHint')
        footer.addWidget(self.progress)
        footer.addStretch(1)
        self.skip_button = QPushButton('直接使用')
        self.skip_button.clicked.connect(self.reject)
        footer.addWidget(self.skip_button)
        self.back_button = QPushButton('上一步')
        self.back_button.clicked.connect(lambda: self._show_step(self.step - 1))
        footer.addWidget(self.back_button)
        self.next_button = QPushButton('下一步')
        self.next_button.setObjectName('primary')
        self.next_button.setDefault(True)
        self.next_button.clicked.connect(self._next)
        footer.addWidget(self.next_button)
        root.addLayout(footer)
        self._show_step(0)
        self.next_button.setFocus()

    def _show_step(self, index):
        self.step = index
        heading, image, crop, description = self.steps[index]
        self.heading.setText(heading)
        self.preview.set_image(image, crop)
        self.description.setText(description)
        self.progress.setText(f'{index + 1} / {len(self.steps)}')
        self.back_button.setEnabled(index > 0)
        self.skip_button.setVisible(index < len(self.steps) - 1)
        self.next_button.setText('开始使用' if index == len(self.steps) - 1 else '下一步')

    def _next(self):
        if self.step == len(self.steps) - 1:
            self.accept()
        else:
            self._show_step(self.step + 1)
