"""Compact watermark controls with asynchronous page preview."""
import base64
import uuid
from collections import OrderedDict
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QThreadPool
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
                            QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel,
                            QLineEdit, QListWidget, QListWidgetItem, QScrollArea, QSlider, QSpinBox, QTabBar,
                            QToolButton, QVBoxLayout, QWidget)

from app.output import OperationCancelled, check_cancel
from app.progress import run_operation
from app.rendering import RenderSignals
from app.workspace import PageEdit, parse_page_range
from app.watermarkpreview import PagePreview, AssetJob, AssetSignals

STYLE = """
QDialog#watermarkDialog { background: #F5F7FA; color: #1E293B; }
QDialog#watermarkDialog QWidget { font-family: "Segoe UI", "Microsoft YaHei UI"; font-size: 13px; }
QDialog#watermarkDialog QLabel { color: #334155; background: transparent; border: none; }
QDialog#watermarkDialog QLabel#watermarkTitle { font-size: 20px; font-weight: 600; color: #134E4A; }
QDialog#watermarkDialog QLabel#watermarkHint { font-size: 12px; color: #64748B; }
QDialog#watermarkDialog QLabel#watermarkStatus { font-size: 12px; color: #64748B; }
QDialog#watermarkDialog QLabel#watermarkStatus[error="true"] { color: #B91C1C; }
QDialog#watermarkDialog QGroupBox { background: white; border: 1px solid #E2E8F0; border-radius: 8px; margin-top: 10px; padding: 14px 10px 10px; font-weight: 600; }
QDialog#watermarkDialog QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; color: #334155; }
QDialog#watermarkDialog QLineEdit, QDialog#watermarkDialog QComboBox, QDialog#watermarkDialog QSpinBox { min-height: 26px; max-height: 30px; padding: 2px 7px; border: 1px solid #CBD5E1; border-radius: 5px; background: white; color: #1E293B; font-weight: 400; }
QDialog#watermarkDialog QLineEdit:focus, QDialog#watermarkDialog QComboBox:focus, QDialog#watermarkDialog QSpinBox:focus { border: 1px solid #0F766E; }
QDialog#watermarkDialog QListWidget { border: 1px solid #CBD5E1; border-radius: 5px; padding: 4px; background: white; font-weight: 400; }
QDialog#watermarkDialog QListWidget::item { min-height: 28px; padding: 4px 6px; border-radius: 3px; }
QDialog#watermarkDialog QListWidget::item:selected { background: #DDF1EA; color: #134E4A; }
QDialog#watermarkDialog QListWidget::item:hover { background: #F0F7F5; }
QDialog#watermarkDialog QPushButton, QDialog#watermarkDialog QToolButton { min-height: 28px; max-height: 32px; padding: 3px 10px; border: 1px solid #CBD5E1; border-radius: 5px; background: white; color: #334155; font-weight: 400; }
QDialog#watermarkDialog QPushButton:hover, QDialog#watermarkDialog QToolButton:hover { border-color: #94A3B8; background: #F0F7F5; }
QDialog#watermarkDialog QPushButton#watermarkApply { background: #0F766E; border-color: #0F766E; color: white; font-weight: 600; }
QDialog#watermarkDialog QPushButton:disabled { background: #E2E8F0; border-color: #E2E8F0; color: #94A3B8; }
QDialog#watermarkDialog QTabBar::tab { background: #E9EEF2; color: #475569; border: none; padding: 8px 20px; min-width: 45px; }
QDialog#watermarkDialog QTabBar::tab:selected { background: #0F766E; color: white; }
QDialog#watermarkDialog QScrollArea { border: none; background: transparent; }
QDialog#watermarkDialog QWidget#watermarkSettings { background: transparent; }
QDialog#watermarkDialog QFrame#watermarkCanvas { background: #E8EDF2; border: 1px solid #D8E0E8; border-radius: 8px; }
QDialog#watermarkDialog QCheckBox { spacing: 7px; color: #334155; font-weight: 400; }
QDialog#watermarkDialog QCheckBox::indicator { width: 15px; height: 15px; }
QDialog#watermarkDialog QSlider::groove:horizontal { height: 4px; background: #DDE5E9; border-radius: 2px; }
QDialog#watermarkDialog QSlider::handle:horizontal { width: 12px; margin: -5px 0; background: #0F766E; border: 1px solid #0F766E; border-radius: 6px; }
"""


class WatermarkDialog(QDialog):
    def __init__(self, host):
        super().__init__(host)
        self.host, self.image = host, ""
        self._watermarks = {}
        self._new_identifier = "zhixu-watermark-" + uuid.uuid4().hex
        self._loading_selection = False
        self._document_pages = host.ws.pages
        self.color = QColor("#666666")
        self._closed = self._applying = False
        self._version = 0
        self._ready_version = None
        self._mode = None
        self._angles = {"text": 30, "image": 0}
        self.custom_position = (.5, .5)
        self.tile_offset = (.5, .5)
        self._background_key = None
        self._background_token = 0
        self._background_ready = False
        self._asset_key = self._wanted_asset_key = None
        self._asset_job = self._pending_asset = None
        self._asset_cache = OrderedDict()
        self._content_pending = False
        self._settings_valid = False
        self._asset_error = ""
        self._background_size = (1, 1)
        self.setObjectName("watermarkDialog")
        self.setWindowTitle("水印 · 纸序 PDF")
        self.setStyleSheet(STYLE)
        self.setMinimumSize(700, 500)
        available = host.screen().availableGeometry()
        self.resize(min(940, available.width() - 60), min(650, available.height() - 80))
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 14)
        root.setSpacing(14)
        header = QHBoxLayout()
        title = QLabel("水印")
        title.setObjectName("watermarkTitle")
        header.addWidget(title)
        header.addStretch()
        self.tabs = QTabBar()
        for label in ("文字水印", "图片水印"):
            self.tabs.addTab(label)
        self.tabs.setExpanding(False)
        header.addWidget(self.tabs)
        root.addLayout(header)
        body = QHBoxLayout()
        body.setSpacing(18)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setMinimumWidth(290)
        self.scroll.setMaximumWidth(330)
        self.settings_panel = QWidget()
        self.settings_panel.setObjectName("watermarkSettings")
        left = QVBoxLayout(self.settings_panel)
        left.setContentsMargins(0, 0, 5, 0)
        left.setSpacing(10)
        self.kind = QComboBox()
        for label, value in (("文字水印", "text"), ("图片水印", "image")):
            self.kind.addItem(label, value)
        self.kind.hide()
        _, selection = self.group("选择水印", left)
        self.watermark = QListWidget()
        self.watermark.addItem("＋ 新增水印")
        self.watermark.setCurrentRow(0)
        self.watermark.setAccessibleName("选择已有水印或新增水印")
        self.watermark.setMinimumHeight(90)
        self.watermark.setMaximumHeight(150)
        self.watermark.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        selection.addRow(self.watermark)
        self.delete_button = QToolButton()
        self.delete_button.setText("删除所选水印")
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(lambda: self.apply(remove=True))
        selection.addRow(self.delete_button)
        self.selection_hint = QLabel("选择已有水印后调整或删除；新增水印直接设置后添加。")
        self.selection_hint.setObjectName("watermarkHint")
        self.selection_hint.setWordWrap(True)
        selection.addRow(self.selection_hint)
        content, form = self.group("水印内容", left)
        self.text = QLineEdit("仅供参考")
        self.text.setMaxLength(1000)
        self.text.setAccessibleName("水印文字")
        form.addRow("文字", self.text)
        self.image_button = QToolButton()
        self.image_button.setText("选择 PNG / JPG 图片…")
        self.image_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.image_button.clicked.connect(self.choose_image)
        form.addRow("图片", self.image_button)
        self.content_form = form
        self.appearance_group, appearance = self.group("外观", left)
        self.size = QSpinBox()
        self.size.setRange(10, 90)
        self.size.setValue(50)
        self.size.setSuffix("%")
        self.size.setToolTip("水印宽度占页面宽度的比例")
        appearance.addRow("大小", self.slider_row(self.size))
        self.opacity = QSpinBox()
        self.opacity.setRange(5, 100)
        self.opacity.setValue(15)
        self.opacity.setSuffix("%")
        self.opacity.setToolTip("数值越小，水印越淡")
        appearance.addRow("不透明度", self.slider_row(self.opacity))
        self.color_button = QToolButton()
        self.color_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.color_button.clicked.connect(self.choose_color)
        appearance.addRow("颜色", self.color_button)
        self.appearance_form = appearance
        self.layout_group, placement = self.group("布局", left)
        self.position = QComboBox()
        names = ("左上", "顶部居中", "右上", "左侧居中", "正中", "右侧居中", "左下", "底部居中", "右下")
        for v in ("top", "center", "bottom"):
            for h in ("left", "center", "right"):
                self.position.addItem(names[self.position.count()], f"{v}-{h}")
        self.position.setCurrentIndex(4)
        self.position.addItem("自定义（拖动）", "custom")
        position_row = QWidget()
        position_layout = QHBoxLayout(position_row)
        position_layout.setContentsMargins(0, 0, 0, 0)
        position_layout.addWidget(self.position, 1)
        self.reset_button = QToolButton()
        self.reset_button.setText("复位")
        self.reset_button.setToolTip("恢复居中位置与平铺起点")
        self.reset_button.clicked.connect(self.reset_position)
        position_layout.addWidget(self.reset_button)
        placement.addRow("位置", position_row)
        self.angle = QSpinBox()
        self.angle.setRange(-180, 180)
        self.angle.setValue(self._angles["text"])
        self.angle.setSuffix("°")
        placement.addRow("旋转", self.angle)
        self.tile = QCheckBox("整页平铺（重复多个水印）")
        placement.addRow(self.tile)
        self.tile_rows = QSpinBox()
        self.tile_columns = QSpinBox()
        for spin, label in ((self.tile_rows, "平铺行数"), (self.tile_columns, "平铺每行个数")):
            spin.setRange(0, 200)
            spin.setSpecialValueText("自动")
            spin.setAccessibleName(label)
            spin.setToolTip("自动随水印大小调整；手动设置只改变数量，不改变水印大小。")
        placement.addRow("行数", self.tile_rows)
        placement.addRow("每行个数", self.tile_columns)
        self.layout_hint = QLabel()
        self.layout_hint.setObjectName("watermarkHint")
        self.layout_hint.setWordWrap(True)
        placement.addRow(self.layout_hint)
        self.placement_form = placement
        _, ranges = self.group("应用范围", left)
        self.range_mode = QComboBox()
        self.range_mode.addItem("所选水印所在页面", "watermark")
        for label, value in (("全部页面", "all"), ("所选页面", "selected"), ("当前页面", "current"), ("指定页码", "custom")):
            self.range_mode.addItem(label, value)
        ranges.addRow("范围", self.range_mode)
        self.range_text = QLineEdit()
        self.range_text.setPlaceholderText("例如 1-3,5")
        self.range_text.setAccessibleName("水印页码范围")
        ranges.addRow("页码", self.range_text)
        self.range_form = ranges
        left.addStretch()
        self.scroll.setWidget(self.settings_panel)
        body.addWidget(self.scroll)
        right = QVBoxLayout()
        right.setSpacing(8)
        preview_header = QHBoxLayout()
        self.preview_caption = QLabel("页面预览")
        preview_header.addWidget(self.preview_caption)
        preview_header.addStretch()
        self.preview_button = QToolButton()
        self.preview_button.setText("刷新预览")
        self.preview_button.clicked.connect(self.force_preview)
        preview_header.addWidget(self.preview_button)
        right.addLayout(preview_header)
        canvas = QFrame()
        canvas.setObjectName("watermarkCanvas")
        canvas_layout = QVBoxLayout(canvas)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        self.preview_label = PagePreview()
        self.preview_label.moved.connect(self.move_watermark)
        self.preview_label.drag_started.connect(self.remember_drag_position)
        self.preview_label.drag_cancelled.connect(self.restore_drag_position)
        canvas_layout.addWidget(self.preview_label)
        right.addWidget(canvas, 1)
        self.status = QLabel("设置变化后自动更新预览")
        self.status.setObjectName("watermarkStatus")
        self.status.setWordWrap(True)
        right.addWidget(self.status)
        body.addLayout(right, 1)
        root.addLayout(body, 1)
        footer = QHBoxLayout()
        hint = QLabel("应用后可 Ctrl+Z 撤销，保存后写入文件。\n移除范围仅限纸序添加的水印。")
        hint.setObjectName("watermarkHint")
        hint.setWordWrap(True)
        footer.addWidget(hint, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = self.buttons.button(QDialogButtonBox.StandardButton.Apply)
        self.apply_button.setObjectName("watermarkApply")
        self.apply_button.setText("应用水印")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.apply)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("关闭")
        self.buttons.rejected.connect(self.reject)
        footer.addWidget(self.buttons)
        root.addLayout(footer)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.preview)
        self.content_timer = QTimer(self)
        self.content_timer.setSingleShot(True)
        self.content_timer.setInterval(280)
        self.content_timer.timeout.connect(self.content_settled)
        self.signals = RenderSignals()
        self.signals.done.connect(self.preview_ready)
        self.asset_signals = AssetSignals()
        self.asset_signals.ready.connect(self.asset_ready, Qt.ConnectionType.QueuedConnection)
        self.asset_signals.finished.connect(self.asset_finished, Qt.ConnectionType.QueuedConnection)
        self.tabs.currentChanged.connect(self.kind.setCurrentIndex)
        self.kind.currentIndexChanged.connect(self.sync)
        self.watermark.currentRowChanged.connect(self.select_watermark)
        self.range_mode.currentIndexChanged.connect(self.sync)
        self.tile.toggled.connect(self.sync)
        for widget in (self.size, self.opacity, self.angle, self.tile_rows, self.tile_columns):
            widget.valueChanged.connect(self.invalidate_preview)
        self.text.textChanged.connect(self.content_changed)
        self.position.currentIndexChanged.connect(self.invalidate_preview)
        self.range_text.textChanged.connect(self.invalidate_preview)
        self.update_color()
        self.load_watermarks()
        self.sync()

    def load_watermarks(self):
        pages = self._document_pages
        def work(progress, cancelled):
            from app.pdfmarks import list_watermarks
            groups = {}
            for index, ref in enumerate(pages):
                check_cancel(cancelled)
                with self.host.store._render_page(ref, cancel=cancelled) as page:
                    entries = list_watermarks(page, (page.rotation + ref.rotation) % 360)
                for entry in entries:
                    record = groups.setdefault(entry["id"], dict(entry, indices=[]))
                    record["indices"].append(index)
                progress(index + 1, len(pages), "正在读取已有水印")
            check_cancel(cancelled)
            return groups
        try:
            groups = run_operation(self.host, "读取水印", work, cancellable=True)
            if self.host.ws.pages is not pages:
                raise ValueError("文档已改变，请关闭后重新打开水印设置。")
            self._watermarks = groups
            for identifier, entry in self._watermarks.items():
                settings = entry["settings"]
                kind = "图片水印" if settings and settings["options"].get("image") else "文字水印" if settings else "水印"
                name = entry["text"].replace("\n", " ")[:16]
                indices = entry["indices"]
                location = f"第 {indices[0] + 1} 页" if len(indices) == 1 else f"共 {len(indices)} 页"
                item = QListWidgetItem(f"{kind} · {name} · {location}")
                item.setData(Qt.ItemDataRole.UserRole, identifier)
                item.setToolTip(f"{kind} · {entry['text']}\n{location}；点击选择后可修改或删除。")
                self.watermark.addItem(item)
            if groups:
                self.selection_hint.setText(f"找到 {len(groups)} 组水印。点击列表中的已有水印，再修改或删除。")
            else:
                self.selection_hint.setText("未找到可管理的水印。已合并进正文或图片的水印无法在此单独删除。")
        except Exception as exc:
            self.selection_hint.setText("已有水印未能读取，可关闭后重新打开设置重试。")
            self.selection_hint.setToolTip(str(exc))
        self.range_mode.setCurrentIndex(self.range_mode.findData("all"))
        self.range_mode.model().item(0).setEnabled(False)
        self.delete_button.setVisible(bool(self._watermarks))
        if self._watermarks:
            current = self.host.ws.preview_index()
            identifiers = list(self._watermarks)
            identifier = next((key for key in identifiers if current in self._watermarks[key]["indices"]), identifiers[0])
            self.watermark.setCurrentRow(identifiers.index(identifier) + 1)

    def selected_watermark_id(self):
        item = self.watermark.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def select_watermark(self, *_):
        selected = self._watermarks.get(self.selected_watermark_id())
        settings = selected["settings"] if selected else None
        self._loading_selection = True
        try:
            self.range_mode.model().item(0).setEnabled(selected is not None)
            self.range_mode.setCurrentIndex(self.range_mode.findData("watermark" if selected else "all"))
            self.delete_button.setEnabled(selected is not None)
            editable = not selected or settings is not None
            for group in (self.appearance_group, self.layout_group):
                group.setEnabled(editable)
            self.text.setEnabled(editable)
            self.image_button.setEnabled(editable)
            if settings:
                options = settings["options"]
                self.image = options.get("image", "")
                mode = "image" if self.image else "text"
                self.tabs.setCurrentIndex(self.kind.findData(mode))
                self.text.setText(settings["text"])
                self.color = QColor.fromRgbF(*settings["color"])
                self.size.setValue(round(options.get("size", 50)))
                self.opacity.setValue(round(options.get("opacity", .15) * 100))
                self.angle.setValue(round(options.get("angle", 30)))
                self.position.setCurrentIndex(self.position.findData(options.get("position", "center-center")))
                self.tile.setChecked(bool(options.get("tile")))
                self.tile_rows.setValue(options.get("tile_rows", 0))
                self.tile_columns.setValue(options.get("tile_columns", 0))
                self.custom_position = tuple(options.get("offset", (.5, .5)))
                self.tile_offset = tuple(options.get("tile_offset", (.5, .5)))
                self.image_button.setText("更换水印图片…" if self.image else "选择 PNG / JPG 图片…")
                self.image_button.setToolTip("")
                self.update_color()
        finally:
            self._loading_selection = False
        self._content_pending = False
        self.content_timer.stop()
        self._background_key = None
        self._background_token += 1
        self._background_ready = self._settings_valid = False
        self._wanted_asset_key = self._pending_asset = None
        if self._asset_job is not None:
            self._asset_job.cancel_event.set()
        self.preview_label.asset = None
        self._asset_key = None
        self.preview_label.set_options({})
        self.sync()

    def group(self, title, layout):
        box = QGroupBox(title)
        form = QFormLayout(box)
        form.setContentsMargins(10, 12, 10, 8)
        form.setSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.addWidget(box)
        return box, form

    def slider_row(self, spin):
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(spin.minimum(), spin.maximum())
        slider.setValue(spin.value())
        slider.setAccessibleName(spin.toolTip())
        slider.valueChanged.connect(spin.setValue)
        spin.valueChanged.connect(slider.setValue)
        spin.setFixedWidth(70)
        row.addWidget(slider, 1)
        row.addWidget(spin)
        return widget

    def update_color(self):
        from app.icons import toolbar_icon
        self.color_button.setText(self.color.name().upper())
        self.color_button.setIcon(toolbar_icon("circle", self.color.name(), 16))

    def set_status(self, text, error=False):
        if self.status.text() != text:
            self.status.setText(text)
        if self.status.property("error") != error:
            self.status.setProperty("error", error)
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)

    def invalidate_preview(self, *_):
        if self._closed or self._applying or self._loading_selection:
            return
        self._version += 1
        self._ready_version = None
        self.apply_button.setEnabled(False)
        self.timer.start()

    def sync(self, *_):
        kind = self.kind.currentData()
        if kind != self._mode:
            if self._mode in self._angles:
                self._angles[self._mode] = self.angle.value()
            self._mode = kind
            if kind in self._angles:
                self.angle.setValue(self._angles[kind])
            self._content_pending = False
            self.content_timer.stop()
            self.preview_label.asset = None
            self._asset_key = None
            self.preview_label.set_options({})
        self.content_form.setRowVisible(self.text, kind == "text")
        self.content_form.setRowVisible(self.image_button, kind == "image")
        self.appearance_form.setRowVisible(self.color_button, kind == "text")
        self.position.setEnabled(not self.tile.isChecked())
        for widget in (self.tile_rows, self.tile_columns):
            self.placement_form.setRowVisible(widget, self.tile.isChecked())
        self.layout_hint.setText("可调整行数和每行个数；单页最多 200 个，数量多时可能重叠。"
                                 if self.tile.isChecked() else "当前为单个水印；勾选整页平铺可重复多个。")
        self.range_form.setRowVisible(self.range_text, self.range_mode.currentData() == "custom")
        self.apply_button.setText("保存修改" if self.selected_watermark_id() else "添加水印")
        self.invalidate_preview()

    def content_changed(self, *_):
        if self._loading_selection:
            return
        self._content_pending = True
        self._wanted_asset_key = None
        self._pending_asset = None
        if self._asset_job is not None:
            self._asset_job.cancel_event.set()
        self.apply_button.setEnabled(False)
        self.content_timer.start()
        self.set_status("正在更新水印内容…")

    def content_settled(self):
        self._content_pending = False
        self.preview()

    def reset_position(self):
        self.custom_position = self.tile_offset = (.5, .5)
        self.position.setCurrentIndex(4)
        self.invalidate_preview()

    def remember_drag_position(self):
        self._drag_position = (self.position.currentIndex(), self.custom_position, self.tile_offset)

    def restore_drag_position(self):
        index, self.custom_position, self.tile_offset = self._drag_position
        self.position.blockSignals(True)
        self.position.setCurrentIndex(index)
        self.position.blockSignals(False)
        self._version += 1
        self.refresh_layer()

    def move_watermark(self, x, y):
        if self._closed or self._applying:
            return
        if self.tile.isChecked():
            self.tile_offset = (x, y)
        else:
            self.custom_position = (x, y)
        self.position.blockSignals(True)
        self.position.setCurrentIndex(self.position.findData("custom"))
        self.position.blockSignals(False)
        self._version += 1
        self.refresh_layer()

    def preview_options(self):
        return dict(size=self.size.value(), opacity=self.opacity.value() / 100,
                    angle=self.angle.value(), position=self.position.currentData(), tile=self.tile.isChecked(),
                    offset=self.custom_position, tile_offset=self.tile_offset,
                    tile_rows=self.tile_rows.value(), tile_columns=self.tile_columns.value())

    def choose_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择水印图片", "", "图片 (*.png *.jpg *.jpeg)")
        if not path:
            return
        try:
            with open(path, "rb") as stream:
                data = stream.read(10 * 1024 * 1024 + 1)
            if len(data) > 10 * 1024 * 1024:
                raise ValueError("水印图片请控制在 10 MB 内。")
            self.image = base64.b64encode(data).decode("ascii")
            self.image_button.setText(Path(path).name)
            self.image_button.setToolTip(Path(path).name)
            self.invalidate_preview()
        except (ValueError, OSError) as exc:
            self.set_status(str(exc), error=True)

    def choose_color(self):
        color = QColorDialog.getColor(self.color, self, "水印颜色")
        if color.isValid():
            self.color = color
            self.update_color()
            self.invalidate_preview()

    def indices(self):
        mode = self.range_mode.currentData()
        selected = self._watermarks.get(self.selected_watermark_id())
        if mode == "watermark":
            indices = selected["indices"] if selected else []
        elif mode == "all":
            indices = list(range(len(self.host.ws.pages)))
        elif mode == "selected":
            indices = self.host.ws.target_indices()
        elif mode == "current":
            index = self.host.ws.preview_index()
            indices = [] if index is None else [index]
        else:
            indices = parse_page_range(self.range_text.text(), len(self.host.ws.pages))
        if selected:
            available = set(selected["indices"])
            indices = [i for i in indices if i in available]
        if not indices:
            raise ValueError("所选范围没有此水印。" if selected else "请选择需要处理的页面。")
        return list(dict.fromkeys(indices))

    def edit(self, ref, *, remove=False):
        identifier = self.selected_watermark_id()
        if remove:
            if not identifier:
                raise ValueError("请先选择要删除的水印。")
            return PageEdit("watermark_remove", annotation_id=identifier)
        if self.kind.currentData() == "image" and not self.image:
            raise ValueError("请先选择水印图片。")
        if self.kind.currentData() == "text" and not self.text.text().strip():
            raise ValueError("请输入水印文字。")
        settings = self.preview_options()
        settings.update(image=self.image if self.kind.currentData() == "image" else "",
                        rotation=(self.host.store._source(ref.source_id).page_rotation(ref.source_page) + ref.rotation) % 360)
        if identifier:
            settings["target"] = identifier
        return PageEdit("watermark_update" if identifier else "watermark", text=self.text.text(),
                        color=(self.color.redF(), self.color.greenF(), self.color.blueF()),
                        annotation_id=self._new_identifier, details=tuple(settings.items()))

    def preview(self, *_):
        if self._closed or self._applying:
            return
        self.timer.stop()
        self._version += 1
        self.apply_button.setEnabled(False)
        try:
            index = self.indices()[0]
            pages = self.host.ws.pages
            ref = pages[index]
            selected = self._watermarks.get(self.selected_watermark_id())
            editable = not selected or selected["settings"] is not None
            if editable:
                self.edit(ref)
            self._settings_valid = True
            generation = self.host.rendering.generation
            identifier = self.selected_watermark_id() if editable else None
            background_key = (ref.render_key, identifier, generation)
            if background_key != self._background_key:
                self._background_key = background_key
                self._background_token += 1
                self._background_ready = False
                self._background_size = self.host.store.page_size(ref)
                self.preview_label.image = None
                self.preview_label.update_geometry()
                self.preview_caption.setText(f"页面预览 · 第 {index + 1} 页")
                cached = None if identifier else getattr(self.host, "_page_images", {}).get(ref.render_key)
                if cached is not None:
                    self.preview_label.set_image(cached, self.host.store.page_size(ref))
                    self._background_ready = True
                if cached is None or cached.width() < 1100:
                    key = ("background", self._background_token)
                    stale = lambda: (self._closed or key != ("background", self._background_token)
                                     or self.host.ws.pages is not pages or self.host.rendering.generation != generation)
                    candidate = ref.edited(PageEdit("watermark_remove", annotation_id=identifier)) if identifier else ref
                    self.host.rendering.request(candidate, 1100, key, self.signals, stale, 60)
            if not self._content_pending and editable:
                text = self.text.text() if self.kind.currentData() == "text" else ""
                color = (self.color.redF(), self.color.greenF(), self.color.blueF()) if text else (0, 0, 0)
                image = self.image if self.kind.currentData() == "image" else ""
                request = (text, color, image)
                self._wanted_asset_key = request
                self.request_asset(request, request)
            self._settings_valid = editable
            self.refresh_layer()
        except Exception as exc:
            self._settings_valid = False
            self._wanted_asset_key = None
            self.preview_label.set_options({})
            self.set_status(str(exc), error=True)

    def force_preview(self, *_):
        self._background_key = None
        self._content_pending = False
        self.content_timer.stop()
        self.preview()

    def preview_ready(self, key, result):
        if self._closed or self._applying or key != ("background", self._background_token):
            return
        if result.cancelled:
            return
        if result.error or result.value is None:
            self.set_status("此页无法生成预览，请尝试其他页或稍后重试。", error=True)
            return
        self.preview_label.set_image(result.value, self._background_size)
        self._background_ready = True
        self.refresh_layer()

    def request_asset(self, key, request):
        if key == self._asset_key and self.preview_label.asset is not None:
            self._asset_error = ""
            self._pending_asset = None
            if self._asset_job is not None and self._asset_job.key != key:
                self._asset_job.cancel_event.set()
            return
        value = self._asset_cache.get(key)
        if value is not None:
            self._asset_error = ""
            self._asset_cache.move_to_end(key)
            self.preview_label.set_asset(value)
            self._asset_key = key
            self._pending_asset = None
            if self._asset_job is not None and self._asset_job.key != key:
                self._asset_job.cancel_event.set()
            return
        if self._asset_job is not None:
            if self._asset_job.key == key and not self._asset_job.cancel_event.is_set():
                return
            self._pending_asset = (key, request)
            self._asset_job.cancel_event.set()
            return
        self._asset_job = AssetJob(key, request, self.asset_signals)
        self._asset_error = ""
        QThreadPool.globalInstance().start(self._asset_job)

    def asset_ready(self, key, value, error):
        if self._closed:
            return
        if value is not None:
            self._asset_cache[key] = value
            self._asset_cache.move_to_end(key)
            while len(self._asset_cache) > 1 and (len(self._asset_cache) > 3
                    or sum(item[0].sizeInBytes() for item in self._asset_cache.values()) > 24 * 1024 * 1024):
                self._asset_cache.popitem(last=False)
        if key != self._wanted_asset_key or self._content_pending or self._applying:
            return
        if error:
            self._asset_error = error
            self.set_status(error, error=True)
            self.apply_button.setEnabled(False)
            return
        if value is not None:
            self._asset_error = ""
            self.preview_label.set_asset(value)
            self._asset_key = key
            self.refresh_layer()

    def asset_finished(self, key):
        if self._asset_job is not None and self._asset_job.key == key:
            self._asset_job = None
        pending, self._pending_asset = self._pending_asset, None
        if not self._closed and not self._content_pending and pending is not None and pending[0] == self._wanted_asset_key:
            self.request_asset(*pending)
            self.refresh_layer()

    def refresh_layer(self):
        if self._closed or self._applying:
            return
        selected = self._watermarks.get(self.selected_watermark_id())
        if selected and selected["settings"] is None:
            self.preview_label.set_options({})
            self.apply_button.setEnabled(False)
            self.set_status("此水印的原始参数无法读取，可删除后重新添加。", error=True)
            return
        if not self._settings_valid:
            self.preview_label.set_options({})
            self.apply_button.setEnabled(False)
            return
        try:
            self.preview_label.set_options(self.preview_options())
        except ValueError as exc:
            self.preview_label.set_options({})
            self.apply_button.setEnabled(False)
            self.set_status(str(exc), error=True)
            return
        ready = self._background_ready and (not self._content_pending
                and self._asset_key == self._wanted_asset_key and self.preview_label.asset is not None)
        self._ready_version = self._version if ready else None
        self.apply_button.setEnabled(ready)
        if ready:
            count = f"本页 {len(self.preview_label.marks)} 个水印（含边缘部分） · " if self.tile.isChecked() else ""
            self.set_status(f"预览已更新 · {count}可拖动定位，方向键微调")
        elif not self._background_ready:
            self.set_status("正在准备页面底图…")
        elif self._asset_error:
            self.set_status(self._asset_error, error=True)
        else:
            self.set_status("正在更新水印内容…")

    def apply(self, *, remove=False):
        if self._applying:
            return
        try:
            if self.host.ws.pages is not self._document_pages:
                self.set_status("文档已改变，请关闭后重新打开水印设置。", error=True)
                return
            indices = self.indices()
            before = self.host.ws.pages
            edits = {i: self.edit(before[i], remove=remove) for i in indices}
            action = "删除水印" if remove else "调整水印" if self.selected_watermark_id() else "添加水印"
            self._applying = True
            self.timer.stop()
            self.content_timer.stop()
            self.scroll.setEnabled(False)
            self.preview_label.setEnabled(False)
            self.tabs.setEnabled(False)
            self.apply_button.setEnabled(False)
            self.delete_button.setEnabled(False)
            def work(progress, cancelled):
                changes = {}
                for current, index in enumerate(indices):
                    check_cancel(cancelled)
                    if remove:
                        from app.pdfmarks import is_watermark, watermark_id
                        with self.host.store._render_page(before[index], cancel=cancelled) as original:
                            found = any(is_watermark(original, a) and watermark_id(a) == edits[index].annotation_id
                                        for a in (original.annots() or ()))
                        if not found:
                            progress(current + 1, len(indices), "正在检查水印")
                            continue
                    candidate = before[index].edited(edits[index])
                    with self.host.store._render_page(candidate, cancel=cancelled):
                        pass
                    changes[index] = candidate
                    progress(current + 1, len(indices), "正在准备水印修改")
                check_cancel(cancelled)
                return changes
            changes = run_operation(self.host, "水印处理", work, cancellable=True)
            if self.host.ws.pages is not before:
                raise ValueError("工作区已改变，请重新应用。")
            if not changes:
                self.set_status("所选范围未找到纸序添加的水印。")
                return
            self.host.ws.edit_pages(changes, action)
            self.host.refresh()
            self.host.statusBar().showMessage(f"{action}已完成；Ctrl+Z 撤销，Ctrl+S 保存。", 8000)
            self._applying = False
            self.accept()
        except OperationCancelled as exc:
            self.set_status(str(exc))
        except Exception as exc:
            self.set_status(str(exc), error=True)
        finally:
            self._applying = False
            if not self._closed:
                self.scroll.setEnabled(True)
                self.preview_label.setEnabled(True)
                self.tabs.setEnabled(True)
                self.apply_button.setEnabled(self._ready_version == self._version)
                self.delete_button.setEnabled(self.selected_watermark_id() is not None)

    def reject(self):
        if self._applying:
            progress = getattr(self.host, "_task_dialog", None)
            if progress is not None:
                progress.request_cancel()
            return
        super().reject()

    def done(self, result):
        self._closed = True
        self._version += 1
        self.timer.stop()
        self.content_timer.stop()
        self._pending_asset = None
        if self._asset_job is not None:
            self._asset_job.cancel_event.set()
        self.host.rendering.prune()
        super().done(result)
