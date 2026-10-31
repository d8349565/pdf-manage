"""Reading-only colour mapping; source pixmaps and PDF output stay untouched."""
import json
from dataclasses import dataclass, replace
from functools import cached_property

from PyQt6.QtCore import QRunnable, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QColorDialog, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QGraphicsPixmapItem, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from app.themes import set_theme_style, ui_color


@dataclass(frozen=True)
class ReadingColors:
    enabled: bool = False
    paper: str = '#F5F0E5'
    ink: str = '#252A25'
    canvas: str = '#D9DEDA'

    @classmethod
    def load(cls, settings):
        try:
            values = json.loads(settings.value('reading/colors', '{}', type=str))
            if not isinstance(values, dict):
                return cls()
            colors = cls(**values)
            if type(colors.enabled) is not bool or any(
                    not isinstance(value, str) or len(value) != 7 or not value.startswith('#')
                    or not QColor(value).isValid() for value in (colors.paper, colors.ink, colors.canvas)):
                return cls()
            return colors if not colors.enabled or colors.contrast >= 4.5 else cls()
        except (TypeError, ValueError):
            return cls()

    def save(self, settings):
        settings.setValue('reading/colors', json.dumps(dict(
            enabled=self.enabled, paper=self.paper, ink=self.ink, canvas=self.canvas)))
        settings.sync()

    @property
    def contrast(self):
        def luminance(value):
            components = QColor(value).getRgbF()[:3]
            linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in components]
            return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))
        first, second = sorted((luminance(self.paper), luminance(self.ink)))
        return (second + .05) / (first + .05)

    @cached_property
    def color_table(self):
        ink, paper = QColor(self.ink).getRgb()[:3], QColor(self.paper).getRgb()[:3]
        return [QColor(*(round(a + (b - a) * shade / 255) for a, b in zip(ink, paper))).rgb()
                for shade in range(256)]

    def map_image(self, image):
        if not self.enabled or image.isNull():
            return image
        gray = image.convertToFormat(QImage.Format.Format_Grayscale8)
        # An explicit grayscale palette keeps the sample values as palette indices.
        mapped = QImage(gray.constBits(), gray.width(), gray.height(), gray.bytesPerLine(),
                        QImage.Format.Format_Indexed8)
        mapped.setColorTable(self.color_table)
        return mapped.copy()


class ReadingColorJob(QRunnable):
    def __init__(self, colors, image, key, signals):
        super().__init__()
        self.colors, self.image, self.key, self.signals = colors, image, key, signals

    def run(self):
        try:
            image = self.colors.map_image(self.image)
            image.setDevicePixelRatio(self.image.devicePixelRatio())
        except (MemoryError, RuntimeError):
            image = self.image
        try:
            self.signals.done.emit(self.key, image)
        except RuntimeError:
            pass  # The view may have been destroyed while mapping.


class ReadingPixmapItem(QGraphicsPixmapItem):
    def __init__(self, view):
        super().__init__()
        self.view = view

    def paint(self, painter, option, widget=None):
        if not self.view.reading_colors.enabled:
            return super().paint(painter, option, widget)
        painter.drawImage(0, 0, self.view.reading_image(self.pixmap()))


class ReadingColorPreview(QLabel):
    image = None

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.image is not None and not self.image.isNull():
            size = self.image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(QRectF((self.width() - size.width()) / 2,
                                    (self.height() - size.height()) / 2, size.width(), size.height()), self.image)


class ReadingColorsDialog(QDialog):
    def __init__(self, parent, colors, preview):
        from app.exportdialogs import COMPACT_QSS
        super().__init__(parent)
        self.setWindowTitle('阅读配色')
        self.setObjectName('readingColors')
        self.setMinimumSize(680, 500)
        self.resize(740, 540)
        self.colors = colors
        self.preview = preview
        original = getattr(parent, '_preview_image', None)
        self.preview_source = (original.scaled(256, 338, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation) if original is not None else QImage())
        set_theme_style(self, COMPACT_QSS + '''
            QDialog#readingColors { background: #FFFFFF; }
            QWidget#customReadingColors { background: transparent; }
            QListWidget#readingSchemes { background: #FFFFFF; border: none; padding: 4px; }
            QListWidget#readingSchemes::item { height: 36px; padding: 4px 8px;
                border-radius: 6px; }
            QListWidget#readingSchemes::item:selected { background: #E1F3EF; }
            QPushButton[readingColor="true"] { min-height: 26px; max-height: 26px;
                padding: 4px 12px; }
        ''')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)
        title = QLabel('阅读配色')
        title.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        set_theme_style(title, 'font-size: 20px; font-weight: 600; color: #173C36;')
        layout.addWidget(title)
        subtitle = QLabel('选一个方案，直接看当前页面的变化。')
        subtitle.setWordWrap(True)
        subtitle.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        subtitle.setObjectName('hint')
        layout.addWidget(subtitle)
        body = QHBoxLayout()
        body.setSpacing(24)
        controls = QVBoxLayout()
        controls.setSpacing(12)
        self.scheme_label = QLabel('配色方案')
        controls.addWidget(self.scheme_label)
        self.presets = QListWidget()
        self.presets.setObjectName('readingSchemes')
        self.presets.setAccessibleName('阅读配色方案')
        self.presets.setIconSize(QSize(36, 26))
        self.presets.setFixedHeight(232)
        schemes = [('文档原色', None), *[(name, (paper, ink, canvas)) for name, paper, ink, canvas in (
                ('米色纸面', '#F5F0E5', '#252A25', '#D9DEDA'),
                ('浅绿纸面', '#E2EFDF', '#213C2D', '#CBD8CC'),
                ('深色阅读', '#24282E', '#E2E4E7', '#171A1F'))], ('自定义颜色', None)]
        for name, values in schemes:
            paper, ink = values[:2] if values else ('#FFFFFF', '#25282B')
            swatch = QPixmap(36, 26)
            swatch.fill(QColor(paper))
            painter = QPainter(swatch)
            painter.setPen(QColor(ink))
            painter.drawText(swatch.rect(), Qt.AlignmentFlag.AlignCenter, 'Aa')
            painter.end()
            item = QListWidgetItem(QIcon(swatch), name)
            item.setData(Qt.ItemDataRole.UserRole, values)
            self.presets.addItem(item)
        selected = 0 if not colors.enabled else next((index for index in range(1, 4)
            if self.presets.item(index).data(Qt.ItemDataRole.UserRole) == (colors.paper, colors.ink, colors.canvas)), 4)
        self.presets.setCurrentRow(selected)
        controls.addWidget(self.presets)
        self.custom_button = QPushButton('返回配色方案')
        self.custom_button.setProperty('compact', True)
        self.custom_button.clicked.connect(self.show_schemes)
        controls.addWidget(self.custom_button)
        self.custom_area = QWidget()
        self.custom_area.setObjectName('customReadingColors')
        form = QFormLayout(self.custom_area)
        form.setContentsMargins(0, 0, 0, 0)
        form.setVerticalSpacing(8)
        self.color_buttons = {}
        for key, label in (('paper', '纸面底色'), ('ink', '文字颜色'), ('canvas', '页面外背景')):
            button = QPushButton()
            button.setAccessibleName(label)
            button.setProperty('compact', True)
            button.setProperty('readingColor', True)
            button.setIconSize(QSize(24, 18))
            button.clicked.connect(lambda checked=False, k=key: self.choose_color(k))
            self.color_buttons[key] = button
            form.addRow(label, button)
        controls.addWidget(self.custom_area)
        controls.addStretch(1)
        scope = QLabel('所有阅读标签共用此配色。\n点击应用后记住选择，下次继续使用。')
        scope.setObjectName('hint')
        scope.setWordWrap(True)
        controls.addWidget(scope)
        body.addLayout(controls, 1)
        self.preview_frame = QFrame()
        self.preview_frame.setObjectName('readingColorPreview')
        preview_layout = QVBoxLayout(self.preview_frame)
        preview_layout.setContentsMargins(16, 12, 16, 12)
        self.preview_caption = QLabel('当前页面预览' if not self.preview_source.isNull() else '配色预览')
        preview_layout.addWidget(self.preview_caption)
        self.sample = ReadingColorPreview('正文显示效果\n纸序 PDF · Aa 0123456789')
        self.sample.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sample.setMinimumSize(210, 250)
        preview_layout.addWidget(self.sample, 1)
        body.addWidget(self.preview_frame, 1)
        layout.addLayout(body, 1)
        note = QLabel('仅改变阅读显示，保存、导出和打印仍用原色。'
                      '\n图片、扫描页和批注也会按明暗换色，不单独识别文字。')
        note.setWordWrap(True)
        note.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        note.setObjectName('hint')
        layout.addWidget(note)
        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        row = QHBoxLayout()
        self.restore = QPushButton('恢复原色')
        self.restore.clicked.connect(lambda: self.presets.setCurrentRow(0))
        row.addWidget(self.restore)
        row.addStretch()
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = self.buttons.addButton('应用', QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply_button.setObjectName('primary')
        self.apply_button.setDefault(True)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        row.addWidget(self.buttons)
        layout.addLayout(row)
        self.presets.currentRowChanged.connect(self.use_preset)
        self.presets.itemClicked.connect(lambda item: self.use_preset() if self.presets.row(item) == 4 else None)
        self.custom_area.setVisible(selected == 4)
        self.custom_button.setVisible(selected == 4)
        self.presets.setVisible(selected != 4)
        self.refresh()

    def choose_color(self, key):
        chosen = QColorDialog.getColor(QColor(getattr(self.colors, key)), self,
                                       self.color_buttons[key].accessibleName())
        if chosen.isValid():
            self.colors = replace(self.colors, **{key: chosen.name()})
            self.presets.setCurrentRow(4)
            self.refresh()

    def use_preset(self):
        values = self.presets.currentItem().data(Qt.ItemDataRole.UserRole)
        if self.presets.currentRow() == 0:
            self.colors = replace(self.colors, enabled=False)
        elif values is not None:
            self.colors = ReadingColors(True, *values)
        else:
            self.colors = replace(self.colors, enabled=True)
        custom = self.presets.currentRow() == 4
        self.custom_area.setVisible(custom)
        self.custom_button.setVisible(custom)
        self.presets.setVisible(not custom)
        self.scheme_label.setText('自定义颜色' if custom else '配色方案')
        self.refresh()
        if self.isVisible():
            self.layout().activate()
            self.resize(self.width(), max(self.height(), self.minimumSizeHint().height()))

    def show_schemes(self):
        self.presets.show()
        self.custom_area.hide()
        self.custom_button.hide()
        self.scheme_label.setText('配色方案')

    def refresh(self):
        for key, button in self.color_buttons.items():
            value = getattr(self.colors, key)
            swatch = QPixmap(24, 18)
            swatch.fill(QColor(value))
            button.setIcon(QIcon(swatch))
            button.setText(value.upper())
            button.setEnabled(self.colors.enabled)
        valid = not self.colors.enabled or self.colors.contrast >= 4.5
        self.apply_button.setEnabled(valid)
        self.feedback.setText('文字与纸面的颜色太接近，请调整后应用。' if not valid else '')
        self.feedback.setVisible(not valid)
        paper, ink = (self.colors.paper, self.colors.ink) if self.colors.enabled else ('#FFFFFF', '#25282B')
        canvas = self.colors.canvas if self.colors.enabled else ui_color('#F4F7F6')
        self.preview_frame.setStyleSheet(f'QFrame#readingColorPreview {{ background: {canvas}; '
            f'border: 1px solid {ui_color("#D7E8E4")}; border-radius: 8px; }}')
        caption_ink = '#000000' if ReadingColors(paper=canvas, ink='#000000').contrast >= 4.5 else '#FFFFFF'
        self.preview_caption.setStyleSheet(f'color: {caption_ink}; background: transparent; font-size: 13px;')
        if not self.preview_source.isNull():
            self.sample.image = self.colors.map_image(self.preview_source)
            self.sample.setText('')
            self.sample.setStyleSheet('background: transparent;')
            self.sample.update()
        else:
            self.sample.setStyleSheet(f'background: {paper}; color: {ink}; padding: 16px; '
                                     'border-radius: 4px; font-size: 14px;')
        if valid:
            self.preview(self.colors)
