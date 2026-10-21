"""Mosaic settings with asynchronous previews of the actual edited PDF."""
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import (QColorDialog, QComboBox, QDialog, QDialogButtonBox,
                             QHBoxLayout, QLabel, QPushButton, QSlider, QSpinBox,
                             QTabWidget, QVBoxLayout)

from app.mosaic import SHAPES, STYLES, mosaic_edit
from app.icons import toolbar_icon
from app.output import check_cancel
from app.progress import OperationThread
from app.regioncapture import CapturePreview


class MosaicDialog(QDialog):
    def __init__(self, tools, index, ref, area, settings):
        super().__init__(tools.host)
        self.tools, self.index, self.ref, self.area = tools, index, ref, tuple(area)
        self.matrix = tuple(tools.snapshot["matrix"])
        self.color = QColor.fromRgbF(*settings["color"])
        self.worker = self.ready = None
        self.generation = 0
        self.closed = False
        self.setWindowTitle("马赛克 · 预览与样式")
        self.setMinimumSize(640, 480)
        available = tools.host.screen().availableGeometry()
        self.resize(min(940, available.width() - 48), min(760, available.height() - 48))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        row = QHBoxLayout()
        self.style, self.shape = QComboBox(), QComboBox()
        for kind, title in STYLES:
            self.style.addItem(title, kind)
        for kind, title in SHAPES:
            self.shape.addItem(title, kind)
        for title, widget in (("样式", self.style), ("形状", self.shape)):
            label = QLabel(title)
            label.setBuddy(widget)
            widget.setAccessibleName("马赛克" + title)
            row.addWidget(label)
            row.addWidget(widget, 1)
        self.style.setCurrentIndex(self.style.findData(settings["style"]))
        self.shape.setCurrentIndex(self.shape.findData(settings["shape"]))
        layout.addLayout(row)

        row = QHBoxLayout()
        self.strength_label = QLabel()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.strength = QSpinBox()
        self.strength.setRange(2, 40)
        self.strength.setValue(settings["strength"])
        self.strength.setKeyboardTracking(False)
        self.slider.setRange(2, 40)
        self.slider.setValue(self.strength.value())
        self.slider.setAccessibleName("马赛克强度")
        self.strength.setAccessibleName("马赛克强度数值")
        self.strength_label.setBuddy(self.strength)
        self.slider.valueChanged.connect(self.strength.setValue)
        self.strength.valueChanged.connect(self.slider.setValue)
        row.addWidget(self.strength_label)
        row.addWidget(self.slider, 1)
        row.addWidget(self.strength)
        self.color_button = QPushButton()
        self.color_button.setAutoDefault(False)
        self.color_button.setAccessibleName("马赛克遮盖颜色")
        self.color_button.clicked.connect(self.choose_color)
        row.addWidget(self.color_button)
        layout.addLayout(row)

        self.tabs = QTabWidget()
        self.previews = []
        placeholder = QImage(8, 8, QImage.Format.Format_RGB32)
        placeholder.fill(QColor("white"))
        for title in ("选区效果", "整页检查"):
            preview = CapturePreview(placeholder, self)
            preview.setAccessibleName("马赛克" + title)
            preview.setToolTip("Ctrl+滚轮缩放，拖动查看")
            self.previews.append(preview)
            self.tabs.addTab(preview, title)
        layout.addWidget(self.tabs, 1)
        hint = QLabel("Ctrl+滚轮缩放，拖动查看。像素块与模糊可能仍可辨认，敏感信息建议使用纯色遮盖。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        notice = QLabel("框选矩形内的原文字与底图会被替换，椭圆外围保留图片外观；相交批注和跨区图形可能整体移除。请在「整页检查」核对；应用后可 Ctrl+Z 撤销，Ctrl+S 保存。")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.feedback = QLabel("正在准备预览…")
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        self.apply_button.setText("应用马赛克")
        self.apply_button.setProperty("primary", True)
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.apply)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.start_preview)
        self.style.currentIndexChanged.connect(self.changed)
        self.shape.currentIndexChanged.connect(self.changed)
        self.strength.valueChanged.connect(self.changed)
        self.changed()

    def settings(self):
        return dict(style=self.style.currentData(), shape=self.shape.currentData(),
                    strength=self.strength.value(),
                    color=(self.color.redF(), self.color.greenF(), self.color.blueF()))

    def choose_color(self):
        color = QColorDialog.getColor(self.color, self, "选择马赛克遮盖颜色")
        if color.isValid():
            self.color = color
            self.changed()

    def changed(self):
        solid = self.style.currentData() == "solid"
        self.strength_label.setVisible(not solid)
        self.slider.setVisible(not solid)
        self.strength.setVisible(not solid)
        self.color_button.setVisible(solid)
        self.color_button.setText("选择颜色 · " + self.color.name().upper())
        self.color_button.setIcon(toolbar_icon("square", self.color.name(), 16))
        self.strength_label.setText("像素块大小" if self.style.currentData() == "pixelate" else "模糊强度")
        self.slider.setToolTip("数值越大，遮挡越强；效果按页面尺寸计算，不受阅读缩放影响。")
        self.generation += 1
        self.ready = None
        self.apply_button.setEnabled(False)
        self.feedback.setText("正在更新预览…")
        self.timer.start(150)

    def start_preview(self):
        if self.closed or self.worker is not None:
            return
        generation, settings = self.generation, self.settings()
        edit = mosaic_edit(self.area, **settings)
        candidate = self.ref.edited(edit)
        store = self.tools.host.store
        matrix = self.matrix
        def render(progress, cancel):
            import pymupdf as p
            check_cancel(cancel)
            store.require_operation([candidate], "content_edit")
            w, h = store.page_size(candidate)
            width = max(1, int(min(1400, (8_000_000 * w / h) ** .5, 8190 * w / max(w, h))))
            whole = store.render_qimage(candidate, width)
            check_cancel(cancel)
            crop, _, _ = store.render_region(candidate, tuple(p.Rect(edit.points) * p.Matrix(matrix)), 2.)
            check_cancel(cancel)
            return whole, crop
        worker = self.worker = OperationThread(render, self, cancellable=True)
        self.job = (generation, edit, candidate, settings)
        worker.finished.connect(self.preview_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def preview_finished(self):
        worker = self.worker
        generation, edit, candidate, settings = self.job
        self.worker = None
        if self.closed:
            super().done(self.pending_result)
            return
        if generation != self.generation:
            self.timer.start(0)
            return
        if worker.error is not None:
            self.feedback.setText(f"预览未完成：{worker.error}")
            return
        whole, crop = worker.result
        for preview, image in zip(self.previews, (crop, whole)):
            preview.item.setPixmap(QPixmap.fromImage(image))
            preview.setSceneRect(preview.item.boundingRect().adjusted(-12, -12, 12, 12))
            preview.set_mode("whole")
        self.ready = (edit, candidate, whole, settings)
        self.feedback.setText("预览已更新。可继续调整样式，或应用到页面。")
        self.apply_button.setEnabled(True)

    def apply(self):
        if self.ready is None:
            return
        edit, candidate, image, settings = self.ready
        if self.tools.commit(edit, page_index=self.index, expected_ref=self.ref,
                             prepared_preview=(candidate, image), on_error=self.feedback.setText):
            self.tools.mosaic_settings = settings
            self.accept()

    def done(self, result):
        if self.closed:
            return
        self.closed = True
        self.timer.stop()
        if self.worker is not None:
            # Stay responsive and keep the modal owner alive until native rendering ends.
            self.pending_result = result
            self.worker.cancel_event.set()
            self.apply_button.setEnabled(False)
            for widget in (self.style, self.shape, self.slider, self.strength, self.color_button):
                widget.setEnabled(False)
            self.feedback.setText("正在结束预览，请稍候…")
            return
        super().done(result)
