"""Simple stamp setup with continuous, lazy page previews."""
import base64
import uuid

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (QAbstractSpinBox, QDialog, QDialogButtonBox,
                             QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
                             QMessageBox, QPushButton, QSlider, QToolButton,
                             QVBoxLayout, QWidget)

from app.imageui import load_image_asset
from app.output import OperationCancelled
from app.pagegeometry import PT_PER_MM
from app.pagetoolsui import ClearComboBox, PageScope, spin_mm
from app.pagepreviewlist import ScrollablePagePreview, draw_fitted_page
from app.pdfio import PdfError
from app.progress import run_operation
from app.seamstamp import list_groups, prepare_group, slice_rect


class SeamStampDialog(QDialog):
    def __init__(self, host, groups, selected=None):
        super().__init__(host)
        self.host, self.pages, self.groups = host, tuple(host.ws.pages), groups
        self.image, self.asset, self.preview_options = '', QImage(), None
        self.remove_requested, self.loading = False, True
        self.setWindowTitle('骑缝章')
        self.setObjectName('seamStampDialog')
        self.setMinimumSize(780, 540)
        self.resize(1000, 620)
        self.setStyleSheet("""
            QDialog#seamStampDialog QComboBox { min-height: 28px; padding: 4px 28px 4px 9px; border-radius: 5px; }
            QDialog#seamStampDialog QComboBox::drop-down { width: 25px; border-left: 1px solid #D7E8E4; }
            QDialog#seamStampDialog QDoubleSpinBox { min-height: 28px; padding: 3px 24px 3px 9px; }
            QDialog#seamStampDialog QDoubleSpinBox::up-button,
            QDialog#seamStampDialog QDoubleSpinBox::down-button { width: 23px; background: #EDF5F0; border-left: 1px solid #D7E8E4; }
            QDialog#seamStampDialog QPushButton { min-height: 28px; padding: 5px 12px; border-radius: 5px; }
            QDialog#seamStampDialog QPushButton#primary { background: #0F766E; color: white; border: 1px solid #0F766E; padding: 6px 22px; }
            QDialog#seamStampDialog QPushButton#primary:disabled { background: #D8E4DE; color: #5E7266; border-color: #D8E4DE; }
            QDialog#seamStampDialog QPushButton#removeGroup { color: #A53930; background: transparent; border: none; }
            QDialog#seamStampDialog QToolButton { border: none; background: transparent; padding: 6px 0; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        body = QHBoxLayout()
        body.setSpacing(22)
        settings = QWidget()
        settings.setFixedWidth(268)
        controls = QVBoxLayout(settings)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(12)
        self.group = ClearComboBox()
        self.group.addItem('＋ 新增骑缝章', '')
        for number, (identifier, value) in enumerate(groups.items(), 1):
            self.group.addItem(f'骑缝章 {number} · {len(value["members"])} 页', identifier)
        self.group.setVisible(bool(groups))
        self.group.setAccessibleName('已有骑缝章')
        controls.addWidget(self.group)
        title = QLabel('选择印章图片')
        title.setStyleSheet('font-size: 15px; font-weight: 600;')
        controls.addWidget(title)
        self.seal_preview = QLabel('支持透明 PNG 或 JPEG')
        self.seal_preview.setMinimumHeight(122)
        self.seal_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.seal_preview.setStyleSheet('background: white; border: 1px dashed #C3D4C9; border-radius: 7px; color: #60756A;')
        self.seal_preview.setAccessibleName('完整印章及分片线预览')
        controls.addWidget(self.seal_preview)
        self.choose = QPushButton('选择图片…')
        self.choose.clicked.connect(self.choose_image)
        controls.addWidget(self.choose)
        selected = host.ws.target_indices() if selected is None else selected
        self.scope = PageScope(self.pages, selected if len(selected) >= 2 else [])
        form = QFormLayout()
        form.setVerticalSpacing(12)
        form.addRow('盖章页面', self.scope)
        self.width_input, self.height_input = spin_mm(42), spin_mm(42)
        self.height_input.setParent(self)
        self.height_input.hide()
        self.width_input.setRange(1, 1000)
        self.height_input.setRange(.01, 1000)
        self.width_input.setDecimals(1)
        self.width_input.setSingleStep(1)
        self.width_input.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
        self.width_input.setAccessibleName('完整印章宽度，保持原图比例')
        form.addRow('印章大小', self.width_input)
        controls.addLayout(form)
        self.dimensions = QLabel('宽高自动保持比例')
        self.dimensions.setStyleSheet('color: #60756A; font-size: 11px;')
        controls.addWidget(self.dimensions)
        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText('更多设置 · 默认右侧居中')
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.toggled.connect(self.show_advanced)
        controls.addWidget(self.advanced_toggle)
        self.advanced = QFrame()
        advanced_form = QFormLayout(self.advanced)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        self.edge = ClearComboBox()
        for title, value in (('右侧', 'right'), ('左侧', 'left'), ('顶部', 'top'), ('底部', 'bottom')):
            self.edge.addItem(title, value)
        advanced_form.addRow('盖章边', self.edge)
        self.position = QSlider(Qt.Orientation.Horizontal)
        self.position.setRange(0, 100)
        self.position.setValue(50)
        self.position.setAccessibleName('印章沿页面边缘的位置')
        advanced_form.addRow('沿边位置', self.position)
        self.advanced.hide()
        controls.addWidget(self.advanced)
        controls.addStretch()
        hint = QLabel('同一枚章按页数分片。支持撤销；保存时另存副本。')
        hint.setWordWrap(True)
        hint.setStyleSheet('color: #60756A; font-size: 11px;')
        controls.addWidget(hint)
        body.addWidget(settings)
        right = QVBoxLayout()
        self.preview_caption = QLabel('连续预览 · 滚轮查看上下页')
        self.preview_caption.setStyleSheet('color: #53685B; font-size: 12px;')
        right.addWidget(self.preview_caption)
        self.preview = ScrollablePagePreview(host.store, self.pages, draw_content=self.draw_preview)
        self.preview.page_changed.connect(lambda row: self.preview_caption.setText(f'第 {row + 1} 页 · 滚轮查看上下页'))
        right.addWidget(self.preview, 1)
        body.addLayout(right, 1)
        layout.addLayout(body, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet('color: #53685B; font-size: 12px;')
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.apply_button.setText('添加骑缝章')
        self.apply_button.setObjectName('primary')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.remove = self.buttons.addButton('删除这组', QDialogButtonBox.ButtonRole.DestructiveRole)
        self.remove.setObjectName('removeGroup')
        self.remove.clicked.connect(self.remove_group)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.group.currentIndexChanged.connect(self.select_group)
        self.edge.currentIndexChanged.connect(self.sync)
        self.scope.mode.currentIndexChanged.connect(self.sync)
        self.scope.range.textChanged.connect(self.sync)
        self.position.valueChanged.connect(self.sync)
        self.width_input.valueChanged.connect(lambda: self.resize_asset(True))
        current = selected[0] if selected else host.ws.preview_index()
        initial = next((i for i, value in enumerate(groups.values(), 1) if current in value['members']), 0)
        self.group.setCurrentIndex(initial)
        self.select_group()

    def show_advanced(self, checked):
        self.advanced.setVisible(checked)
        self.advanced_toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)

    def select_group(self, *_):
        self.loading = True
        value = self.groups.get(self.group.currentData())
        self.remove.setVisible(value is not None)
        self.apply_button.setText('保存修改' if value else '添加骑缝章')
        if value:
            self.image = value['image']
            self.asset = QImage.fromData(base64.b64decode(self.image))
            self.scope.mode.setCurrentIndex(self.scope.mode.findData('range'))
            self.scope.range.setText(','.join(str(row + 1) for row in value['members']))
            self.edge.setCurrentIndex(self.edge.findData(value['edge']))
            self.width_input.setValue(value['width'] / PT_PER_MM)
            self.height_input.setValue(value['height'] / PT_PER_MM)
            self.position.setValue(round(value['position'] * 100))
        self.loading = False
        self.sync()

    def choose_image(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择印章图片', '', '图片 (*.png *.jpg *.jpeg)')
        if not path:
            return
        try:
            data, _, _, image = load_image_asset(path)
            self.image, self.asset = base64.b64encode(data).decode('ascii'), image
            self.resize_asset(True)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, '无法使用图片', str(exc))

    def resize_asset(self, width_changed=True):
        if self.loading:
            return
        if not self.asset.isNull():
            self.height_input.setValue(self.width_input.value() * self.asset.height() / self.asset.width())
        self.sync()

    def options(self):
        if not self.image:
            raise ValueError('先选择印章图片，再确认盖章范围。')
        return dict(image=self.image, width=self.width_input.value() * PT_PER_MM,
                    height=self.height_input.value() * PT_PER_MM, edge=self.edge.currentData(),
                    position=self.position.value() / 100)

    def sync(self, *_):
        if self.loading:
            return
        place = '居中' if self.position.value() == 50 else f'{self.position.value()}%'
        self.advanced_toggle.setText(f'更多设置 · {self.edge.currentText()}{place}')
        try:
            indices = self.scope.indices()
            self.preview.set_rows(indices, self.group.currentData() or '')
            if len(indices) < 2:
                raise ValueError('至少选择两页；单页请使用插入图片。')
            options = self.options()
            for ordinal, row in enumerate(indices):
                slice_rect(options['width'], options['height'], len(indices), ordinal,
                           options['edge'], options['position'], *self.host.store.page_size(self.pages[row]))
            self.preview_options = options
            thickness = (options['width'] if options['edge'] in ('left', 'right') else options['height']) / len(indices) / PT_PER_MM
            warning = ' · 分片较窄，请核对预览' if thickness < 1 else ''
            self.status.setText(f'{len(indices)} 页 · 每片约 {thickness:.1f} mm{warning} · 图片盖章，不提供数字签名或验签')
            self.dimensions.setText(f'{self.width_input.value():g} × {self.height_input.value():g} mm · 保持原图比例')
            self.choose.setText('更换图片…')
            self.update_seal_preview(len(indices))
            enabled = True
        except (ValueError, PdfError) as exc:
            self.preview_options = None
            self.status.setText(str(exc))
            enabled = False
        self.preview.refresh_pages()
        self.apply_button.setEnabled(enabled)

    def draw_preview(self, painter, rectangle, row, image):
        draw_fitted_page(painter, rectangle, image)
        options = self.preview_options
        if not options or self.asset.isNull():
            return
        indices = self.preview.rows
        page_width, page_height = self.host.store.page_size(self.pages[row])
        area, fraction = slice_rect(options['width'], options['height'], len(indices), indices.index(row),
                                   options['edge'], options['position'], page_width, page_height)
        target = QRectF(rectangle.x() + area.x0 / page_width * rectangle.width(),
                        rectangle.y() + area.y0 / page_height * rectangle.height(),
                        area.width / page_width * rectangle.width(), area.height / page_height * rectangle.height())
        source = QRectF(fraction[0] * self.asset.width(), fraction[1] * self.asset.height(),
                        (fraction[2] - fraction[0]) * self.asset.width(), (fraction[3] - fraction[1]) * self.asset.height())
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(target, self.asset, source)
        painter.setPen(QPen(QColor('#0F766E'), 1, Qt.PenStyle.DashLine))
        painter.drawRect(target)
        painter.restore()

    def update_seal_preview(self, count):
        seal = self.asset.scaled(240, 110, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        painter = QPainter(seal)
        painter.setPen(QPen(QColor('#6C8B7C'), 1))
        for index in range(1, count, max(1, (count + 79) // 80)):
            if self.edge.currentData() in ('left', 'right'):
                x = round(seal.width() * index / count)
                painter.drawLine(x, 0, x, seal.height())
            else:
                y = round(seal.height() * index / count)
                painter.drawLine(0, y, seal.width(), y)
        painter.end()
        self.seal_preview.setPixmap(QPixmap.fromImage(seal))

    def done(self, result):
        self.preview.shutdown()
        super().done(result)

    def remove_group(self):
        self.remove_requested = True
        self.accept()


def open_seam_stamps(host):
    if not host.ws.pages or not host._allow_operation('content_edit'):
        return
    before = tuple(host.ws.pages)
    selected = host.ws.target_indices()
    try:
        groups = run_operation(host, '读取骑缝章', lambda progress, cancel:
                               list_groups(host.store, before, progress, cancel), cancellable=True)
        dialog = SeamStampDialog(host, groups, selected)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            dialog.deleteLater()
            return
        group = dialog.group.currentData() or f'zhixu-seam-{uuid.uuid4().hex}'
        previous = groups.get(group, {}).get('members', [])
        remove = dialog.remove_requested
        indices, options = ([], {}) if remove else (dialog.scope.indices(), dialog.options())
        dialog.deleteLater()
        changes = run_operation(host, '删除骑缝章' if remove else '应用骑缝章', lambda progress, cancel:
            prepare_group(host.store, before, indices, options, group, previous=previous,
                          remove=remove, progress=progress, cancel=cancel), cancellable=True)
        if tuple(host.ws.pages) != before:
            raise ValueError('工作区已改变，请重新操作。')
        host.ws.edit_pages(changes, '删除骑缝章' if remove else '应用骑缝章')
        host.refresh()
        host.statusBar().showMessage('骑缝章已删除；Ctrl+Z 可撤销。' if remove else
                                    f'已在 {len(indices)} 页分片盖章；Ctrl+Z 可撤销。', 6500)
    except OperationCancelled:
        host.statusBar().showMessage('已取消，页面未修改。', 5000)
    except (ValueError, PdfError) as exc:
        QMessageBox.warning(host, '无法处理骑缝章', str(exc))
