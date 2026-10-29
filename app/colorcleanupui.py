"""A focused cleanup workbench; draft marks never change the source workspace."""
from dataclasses import replace
import math
from pathlib import Path
from threading import Event

from PIL import Image, ImageChops, ImageStat
from PyQt6.QtCore import (QEvent, QObject, QPoint, QPointF, QRectF, QRunnable,
                         Qt, QThreadPool, QTimer, pyqtSignal)
from PyQt6.QtGui import QColor, QImage, QKeySequence, QPainter, QPen, QPolygonF, QShortcut
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QDialog,
    QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QSlider, QVBoxLayout, QWidget)

from app.colorcleanup import (ANNOT_TYPES, CleanupSettings, CleanupZones, plan_page,
                             prepare_cleanup, resolve_cleanup_mode, zone_masks)
from app.icons import toolbar_icon
from app.output import OperationCancelled, check_cancel
from app.pagetoolsui import ClearComboBox, PageScope
from app.pdfio import PdfError, WriteOptions
from app.progress import run_operation
from app.themes import set_theme_style, ui_color


def _qimage(image):
    image = image.convert('RGB')
    return QImage(image.tobytes(), image.width, image.height, image.width * 3,
                  QImage.Format.Format_RGB888).copy()


def _pil(image):
    image = image.convertToFormat(QImage.Format.Format_RGB888)
    return Image.frombytes('RGB', (image.width(), image.height()),
        image.constBits().asstring(image.sizeInBytes()), 'raw', 'RGB', image.bytesPerLine(), 1)


class CleanupCanvas(QWidget):
    zone_added = pyqtSignal(str, object)
    picked = pyqtSignal(object)
    pan_delta = pyqtSignal(object)
    gesture_changed = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self.image, self.source = QImage(), QImage()
        self.zones = CleanupZones()
        self.page_size, self.scale = (595., 842.), 1.
        self.role, self.shape, self.radius = 'move', 'brush', .01
        self.drag, self.pan_position, self.hover = [], None, None
        self.live, self.clip, self.clip_key = QImage(), QImage(), None
        self.live_color = QColor('white')
        self.editable, self.show_live = False, True
        self._scaled_key, self._scaled = None, QImage()
        self.setMinimumSize(1, 1)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName('清理画布，可移动、擦除或标记保留；Ctrl 加滚轮缩放')

    def area(self):
        width, height = (value * self.scale for value in self.page_size)
        return QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)

    def point(self, position):
        area = self.area()
        return (max(0., min(1., (position.x() - area.x()) / area.width())),
                max(0., min(1., (position.y() - area.y()) / area.height())))

    def clear_live(self):
        self.live = QImage()
        self.update()

    def cancel_gesture(self):
        active = bool(self.drag)
        self.drag, self.pan_position = [], None
        self.clear_live()
        if active:
            self.gesture_changed.emit(False)
        return active

    def _image_point(self, point):
        return QPointF(point[0] * self.image.width(), point[1] * self.image.height())

    def _prepare_live(self):
        if self.live.size() != self.image.size():
            self.live = QImage(self.image.size(), QImage.Format.Format_ARGB32_Premultiplied)
            self.live.fill(Qt.GlobalColor.transparent)
        key = (self.image.width(), self.image.height(), self.role, self.zones.region,
               self.zones.protected, self.zones.protected_ellipses, self.zones.strokes)
        if key != self.clip_key:
            zones = self.zones if self.role == 'erase' else CleanupZones(region=self.zones.region)
            allowed, _ = zone_masks((self.image.width(), self.image.height()), zones)
            alpha = Image.new('RGBA', allowed.size, (255, 255, 255, 0))
            alpha.putalpha(allowed)
            self.clip = QImage(alpha.tobytes(), alpha.width, alpha.height, alpha.width * 4,
                               QImage.Format.Format_RGBA8888).copy()
            self.clip_key = key

    def _paint_live(self, previous=None, rectangle=None):
        self._prepare_live()
        painter = QPainter(self.live)
        color = self.live_color if self.role == 'erase' else QColor(20, 101, 192, 75)
        if rectangle is not None:
            x0, y0, x1, y1 = rectangle
            box = QRectF(x0 * self.image.width(), y0 * self.image.height(),
                         (x1 - x0) * self.image.width(), (y1 - y0) * self.image.height())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(box) if self.shape == 'ellipse' else painter.drawRect(box)
        else:
            pen = QPen(color, max(1., 2 * self.radius * min(self.image.width(), self.image.height())))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            end = self._image_point(self.drag[-1])
            painter.drawLine(self._image_point(previous), end) if previous else painter.drawPoint(end)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        painter.drawImage(0, 0, self.clip)
        painter.end()
        self.update()

    def drag_rect(self):
        start, end = self.drag[0], self.drag[-1]
        if self.shape == 'ellipse' and self.role != 'region':
            width, height = self.page_size
            side = min(abs(end[0] - start[0]) * width, abs(end[1] - start[1]) * height)
            end = (start[0] + math.copysign(side / width, end[0] - start[0]),
                   start[1] + math.copysign(side / height, end[1] - start[1]))
        return min(start[0], end[0]), min(start[1], end[1]), max(start[0], end[0]), max(start[1], end[1])

    @staticmethod
    def draw_stroke(painter, area, radius, points):
        pen = QPen(QColor(20, 101, 192, 85), radius * min(area.width(), area.height()) * 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        line = QPolygonF([QPointF(area.x() + x * area.width(), area.y() + y * area.height()) for x, y in points])
        painter.drawPoint(line[0]) if len(points) == 1 else painter.drawPolyline(line)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(ui_color('canvas')))
        area = self.area()
        painter.fillRect(area, QColor('white'))
        if self.image.isNull():
            painter.setPen(QColor(ui_color('muted')))
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, '正在生成清理预览…')
            return
        dpr = painter.device().devicePixelRatioF()
        key = (self.image.cacheKey(), round(area.width() * dpr), round(area.height() * dpr), dpr)
        if key[1] * key[2] > 8_000_000:
            self._scaled, self._scaled_key = QImage(), None
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(area, self.image)
        elif key != self._scaled_key:
            self._scaled = self.image.scaled(max(1, key[1]), max(1, key[2]),
                Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self._scaled.setDevicePixelRatio(dpr)
            self._scaled_key = key
        if not self._scaled.isNull(): painter.drawImage(area, self._scaled)
        if self.show_live and not self.live.isNull():
            painter.drawImage(area, self.live)
        painter.setPen(QPen(QColor(ui_color('border')), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(area)
        def box(values):
            x0, y0, x1, y1 = values
            return QRectF(area.x() + x0 * area.width(), area.y() + y0 * area.height(),
                          (x1 - x0) * area.width(), (y1 - y0) * area.height())
        if self.zones.region != (0., 0., 1., 1.):
            painter.setPen(QPen(QColor('#0F766E'), 1, Qt.PenStyle.DashLine))
            painter.drawRect(box(self.zones.region))
        if self.role == 'keep':
            painter.setPen(QPen(QColor('#1465c0'), 1.5))
            painter.setBrush(QColor(20, 101, 192, 35))
            for rect in self.zones.protected:
                painter.drawRect(box(rect))
            for rect in self.zones.protected_ellipses:
                painter.drawEllipse(box(rect))
            for radius, points in self.zones.strokes:
                self.draw_stroke(painter, area, radius, points)
        if self.drag and (self.shape != 'brush' or self.role == 'region'):
            painter.setPen(QPen(QColor('#0F766E'), 1.5, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            selection = box(self.drag_rect())
            painter.drawEllipse(selection) if self.shape == 'ellipse' and self.role != 'region' else painter.drawRect(selection)
        if self.editable and self.hover is not None and area.contains(self.hover) and self.role in ('erase', 'keep') and self.shape == 'brush':
            radius = max(1., self.radius * min(area.width(), area.height()))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            for color, width in ((QColor('white'), 3), (QColor('#0F766E'), 1)):
                painter.setPen(QPen(color, width))
                painter.drawEllipse(self.hover, radius, radius)

    def mousePressEvent(self, event):
        if self.image.isNull() or not self.area().contains(event.position()):
            return super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.MiddleButton or event.button() == Qt.MouseButton.LeftButton and self.role == 'move':
            self.pan_position = event.globalPosition()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if event.button() != Qt.MouseButton.LeftButton or not self.editable:
            return super().mousePressEvent(event)
        point = self.point(event.position())
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if self.role == 'pick' and not self.source.isNull():
            color = self.source.pixelColor(min(self.source.width() - 1, int(point[0] * self.source.width())),
                min(self.source.height() - 1, int(point[1] * self.source.height())))
            self.picked.emit((color.red(), color.green(), color.blue()))
        elif self.role in ('erase', 'keep', 'region'):
            self.drag = [point]
            self.gesture_changed.emit(True)
            if self.shape == 'brush' and self.role != 'region':
                self._paint_live()
            self.update()

    def mouseMoveEvent(self, event):
        self.hover = event.position()
        if self.pan_position is not None:
            self.pan_delta.emit(event.globalPosition() - self.pan_position)
            self.pan_position = event.globalPosition()
            return
        if self.drag:
            previous = self.drag[-1]
            point = self.point(event.position())
            if point != previous:
                self.drag.append(point)
                if self.shape == 'brush' and self.role != 'region':
                    self._paint_live(previous)
        self.setCursor(Qt.CursorShape.BlankCursor if self.editable and self.role in ('erase', 'keep') and self.shape == 'brush' and self.area().contains(event.position())
                       else Qt.CursorShape.OpenHandCursor if self.role == 'move' else Qt.CursorShape.CrossCursor)
        self.update()

    def mouseReleaseEvent(self, event):
        if self.pan_position is not None:
            self.pan_position = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            return
        if event.button() != Qt.MouseButton.LeftButton or not self.drag:
            return super().mouseReleaseEvent(event)
        self.drag.append(self.point(event.position()))
        brush = self.shape == 'brush' and self.role != 'region'
        value = (self.radius, tuple(self.drag)) if brush else self.drag_rect()
        valid = brush or value[2] - value[0] > .0001 and value[3] - value[1] > .0001
        if valid:
            if self.role == 'erase' and not brush:
                self._paint_live(rectangle=value)
            self.zone_added.emit(self.role, (self.shape if self.role != 'region' else 'rect', value))
        self.drag = []
        if self.role != 'erase':
            self.clear_live()
        self.gesture_changed.emit(False)
        self.update()

    def leaveEvent(self, event):
        self.hover = None
        self.unsetCursor()
        self.update()
        super().leaveEvent(event)


class _Scope(PageScope):
    def __init__(self, pages, selected, current):
        super().__init__(pages, selected)
        self.current = current
        self.mode.insertItem(0, f'当前页（第 {current + 1} 页）', 'current')
        self.mode.setCurrentIndex(self.mode.findData('all'))
        self.range.setMaximumWidth(120)

    def indices(self):
        return [self.current] if self.mode.currentData() == 'current' else super().indices()


class _Signals(QObject):
    ready = pyqtSignal(int, object, str)


class _Preview(QRunnable):
    def __init__(self, store, ref, settings, zones, options, width, token, signals, cancel, cached=None):
        super().__init__()
        self.store, self.ref, self.settings, self.zones = store, ref, settings, zones
        self.options, self.width, self.token = options, width, token
        self.signals, self.cancel, self.cached = signals, cancel, cached

    def run(self):
        result, error = {}, ''
        mode = self.options['mode']
        try:
            check_cancel(self.cancel.is_set)
            if self.cached:
                result = dict(self.cached)
                result['original'] = self.store.render_qimage(self.ref, self.width, prepare_annotations=False)
                check_cancel(self.cancel.is_set)
                result['cleaned'] = self.store.render_qimage(result['prepared'], self.width, prepare_annotations=False)
                before, after = _pil(result['original']), _pil(result['cleaned'])
            else:
                mode = resolve_cleanup_mode(self.store, self.ref, mode, self.cancel.is_set)
                selected = self.options['selected']
                if mode == 'annotations':
                    scan = plan_page(self.store, self.ref, self.settings, self.zones, mode=mode,
                        ocr=self.options['ocr'], cancel=self.cancel.is_set)
                    selected = set(selected) & {a['id'] for a in scan['candidates']}
                result = plan_page(self.store, self.ref, self.settings, self.zones, mode=mode,
                    selected=selected, ocr=self.options['ocr'], preview=True, preview_width=self.width,
                    cancel=self.cancel.is_set)
                before, after = result.pop('before'), result.pop('after')
                result['original'], result['cleaned'] = _qimage(before), _qimage(after)
                result['selected'] = selected
                result['prepared'] = self.ref.edited(result['edit']) if result['edit'] else self.ref
            diff = ImageChops.difference(before, after)
            result['paper_color'] = tuple(ImageStat.Stat(before.resize((96, 96))).median)
            r, g, b = diff.split()
            mask = ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda v: 255 if v else 0)
            result['mask'] = mask
            result['highlight'] = _qimage(Image.composite(Image.new('RGB', before.size, '#e6379d'), before, mask))
            check_cancel(self.cancel.is_set)
        except OperationCancelled:
            result = {}
        except Exception as exc:
            error = str(exc)
            result = dict(mode=mode)
            if not self.cancel.is_set():
                try:
                    result['original'] = self.store.render_qimage(self.ref, min(self.width, 1100), prepare_annotations=False)
                except Exception:
                    pass
        self.signals.ready.emit(self.token, result, error)


class ColorCleanupDialog(QDialog):
    def __init__(self, host):
        super().__init__(host)
        self.host, self.workspace, self.pages = host, host.ws, tuple(host.ws.pages)
        self.outline = host.ws.outline
        self.current = host.ws.preview_index() or 0
        self.zones, self.selected, self.history, self.redo_history = {}, {}, [], []
        self.result, self.busy, self.closed, self.saving = {}, False, False, False
        self.generation, self.sample_ready, self.force_ocr = 0, False, False
        self.cancel, self.saved_result = Event(), None
        self.color = (210, 25, 45)
        self.current_tool, self.current_shape, self.current_view = 'move', 'brush', 'cleaned'
        self.zoom_mode, self.zoom_percent, self.render_width = 'page', 100., 1100
        self._zoom_only = False
        self.setObjectName('colorCleanup')
        self.setWindowTitle('去除彩色笔迹 - 纸序 PDF')
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        screen = host.screen().availableGeometry()
        self.resize(min(1480, round(screen.width() * .94)), round(screen.height() * .92))
        set_theme_style(self, '''
            QDialog#colorCleanup QPushButton { min-height: 0px; padding: 7px 12px; border-radius: 6px; font-size: 13px; }
            QDialog#colorCleanup QPushButton:checked { background: #CCFBF1; border: 1px solid #99B2AC; }
            QDialog#colorCleanup QComboBox { min-height: 0px; padding: 6px 10px; border-radius: 6px; font-size: 13px; }
            QFrame#cleanupBar { background: #FFFFFF; border: 1px solid #D7E8E4; border-radius: 7px; }
            QLabel#cleanupTitle { font-size: 23px; font-weight: 600; }
            QLabel#cleanupHint { color: #4D6B67; font-size: 12px; }
        ''')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)
        heading = QHBoxLayout()
        text = QVBoxLayout()
        title = QLabel('去除彩色笔迹'); title.setObjectName('cleanupTitle')
        subtitle = QLabel('先自动清理，再用橡皮擦补一补'); subtitle.setObjectName('cleanupHint')
        text.addWidget(title); text.addWidget(subtitle)
        heading.addLayout(text); heading.addStretch()
        self.previous = self.button('上一页', 'chevron-left', lambda: self.navigate(-1))
        self.next = self.button('下一页', 'chevron-right', lambda: self.navigate(1))
        for button in (self.previous, self.next):
            button.setToolTip(button.text()); button.setText(''); button.setFixedWidth(34)
        self.page_label = QLabel()
        for widget in (self.previous, self.page_label, self.next): heading.addWidget(widget)
        heading.addSpacing(14); heading.addWidget(QLabel('处理页码'))
        self.scope = _Scope(self.pages, host.ws.target_indices(), self.current)
        heading.addWidget(self.scope)
        layout.addLayout(heading)
        bar = self.toolbar = QFrame(); bar.setObjectName('cleanupBar')
        bar_layout = QVBoxLayout(bar); bar_layout.setContentsMargins(10, 8, 10, 8); bar_layout.setSpacing(7)
        choices = QHBoxLayout()
        choices.addWidget(QLabel('笔迹颜色'))
        self.red = self.button('红色', '', lambda: self.choose_color((210, 25, 45)), checkable=True)
        self.blue = self.button('蓝色', '', lambda: self.choose_color((30, 65, 210)), checkable=True)
        for button, color in ((self.red, '#d2192d'), (self.blue, '#1e41d2')):
            button.setIcon(toolbar_icon('circle', color, themed=False)); choices.addWidget(button)
        self.colors = QButtonGroup(self); self.colors.addButton(self.red); self.colors.addButton(self.blue)
        self.red.setChecked(True)
        self.pick = self.button('取色', 'eyedropper', lambda: self.set_tool('pick'), checkable=True)
        choices.addWidget(self.pick)
        self.advanced = self.button('调整效果', '', self.show_adjustments)
        choices.addWidget(self.advanced); choices.addStretch()
        self.view_buttons = {}
        group = QButtonGroup(self)
        for key, label in (('original', '原图'), ('cleaned', '清理后')):
            button = self.button(label, '', lambda checked=False, key=key: self.set_view(key), checkable=True)
            self.view_buttons[key] = button; group.addButton(button); choices.addWidget(button)
        self.view_buttons['cleaned'].setChecked(True)
        self.fit = ClearComboBox()
        self.fit.addItem('适合页面', 'page'); self.fit.addItem('适合宽度', 'width'); self.fit.addItem('实际大小', 'actual')
        self.fit.setAccessibleName('页面适配方式')
        self.zoom_out = self.button('缩小', 'zoom-out', lambda: self.set_zoom(self.zoom_percent / 1.25))
        self.zoom_label = QLabel('100%'); self.zoom_label.setMinimumWidth(46); self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_in = self.button('放大', 'zoom-in', lambda: self.set_zoom(self.zoom_percent * 1.25))
        for button in (self.zoom_out, self.zoom_in):
            button.setToolTip(button.text()); button.setText(''); button.setFixedWidth(34)
        bar_layout.addLayout(choices)
        tools = QHBoxLayout()
        self.tool_buttons = {}; self.tool_group = QButtonGroup(self)
        for key, label, icon in (('move', '移动', 'hand'), ('erase', '擦除', 'eraser'), ('keep', '保留内容', 'pen')):
            button = self.button(label, icon, lambda checked=False, key=key: self.set_tool(key), checkable=True)
            self.tool_buttons[key] = button; self.tool_group.addButton(button); tools.addWidget(button)
        self.tool_buttons['move'].setChecked(True)
        tools.addSpacing(14)
        self.undo_zone = self.button('撤销', 'undo', self.undo_zones)
        self.redo_zone = self.button('重做', 'redo', self.redo_zones)
        tools.addWidget(self.undo_zone); tools.addWidget(self.redo_zone); tools.addStretch()
        for widget in (self.fit, self.zoom_out, self.zoom_label, self.zoom_in): tools.addWidget(widget)
        self.tool_hint = QLabel('可放大后检查细节'); self.tool_hint.setObjectName('cleanupHint')
        self.tool_hint.setMinimumWidth(0)
        self.tool_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        bar_layout.addLayout(tools)
        self.context = QWidget(); self.context.setObjectName('cleanupContext')
        set_theme_style(self.context, 'QWidget#cleanupContext {background: transparent; border: none;}')
        contextual = QHBoxLayout(self.context); contextual.setContentsMargins(0, 0, 0, 0)
        self.shape_label = QLabel('擦除方式'); contextual.addWidget(self.shape_label)
        self.shape_buttons = {}; shapes = QButtonGroup(self)
        for key, label, icon in (('brush', '画笔', 'pen'), ('rect', '矩形', 'square'), ('ellipse', '圆形', 'circle')):
            button = self.button(label, icon, lambda checked=False, key=key: self.set_shape(key), checkable=True)
            self.shape_buttons[key] = button; shapes.addButton(button); contextual.addWidget(button)
        self.shape_buttons['brush'].setChecked(True)
        self.size_label = QLabel('大小'); contextual.addWidget(self.size_label)
        self.brush = QSlider(Qt.Orientation.Horizontal); self.brush.setRange(3, 200); self.brush.setValue(20)
        self.brush.setMaximumWidth(150); self.brush.setAccessibleName('笔刷直径，单位十分之一毫米')
        self.brush_value = QLabel('2 mm'); self.brush_value.setMinimumWidth(48)
        contextual.addWidget(self.brush); contextual.addWidget(self.brush_value)
        self.fill = ClearComboBox(); self.fill.addItem('白色', 'white'); self.fill.addItem('匹配纸面', 'paper')
        self.fill.setAccessibleName('手工擦除填充'); contextual.addWidget(self.fill); contextual.addStretch()
        contextual.addWidget(self.tool_hint)
        bar_layout.addWidget(self.context); self.context.hide()
        layout.addWidget(bar)
        self.adjustments = QDialog(self, Qt.WindowType.Popup)
        self.adjustments.setWindowTitle('调整清理效果')
        panel_layout = QVBoxLayout(self.adjustments)
        advanced_scroll = QScrollArea(); advanced_scroll.setWidgetResizable(True)
        advanced_scroll.setFrameShape(QFrame.Shape.NoFrame)
        panel = QWidget(); advanced = QVBoxLayout(panel)
        self.method = ClearComboBox()
        for name, key in (('自动选择（推荐）', 'auto'), ('只清理扫描图片', 'images'), ('只清理彩色批注', 'annotations'),
                          ('转图片处理（原生文字将转为图像）', 'raster')): self.method.addItem(name, key)
        advanced.addWidget(QLabel('处理内容')); advanced.addWidget(self.method)
        self.tolerance, self.faint = QSlider(Qt.Orientation.Horizontal), QSlider(Qt.Orientation.Horizontal)
        for slider, name, value in ((self.tolerance, '颜色容差', 30), (self.faint, '浅色笔迹识别强度', 45)):
            slider.setRange(0, 100); slider.setValue(value); slider.setAccessibleName(name)
            label = QLabel(f'{name}：{value}')
            slider.valueChanged.connect(lambda v, label=label, name=name: label.setText(f'{name}：{v}'))
            advanced.addWidget(label); advanced.addWidget(slider)
        self.fringe = QCheckBox('清除边缘残影（白纸打印）'); self.fringe.setChecked(True)
        self.smooth = QCheckBox('平滑估计邻近背景')
        self.show_mask = QCheckBox('显示清除范围（粉色高亮）')
        for widget in (self.fringe, self.smooth, self.show_mask): advanced.addWidget(widget)
        self.region_button = self.button('只清理框选区域', 'square', lambda: self.set_tool('region'))
        self.whole = self.button('恢复整页清理范围', '', lambda: self.change_zones(replace(self.page_zones(), region=(0., 0., 1., 1.))))
        self.copy_zones = self.button('范围与保护区用于所选页', '', self.copy_page_zones)
        self.clear_manual = self.button('清空本页手工修改', '', self.clear_page_marks)
        self.reset = self.button('恢复默认清理参数', '', self.reset_parameters)
        self.help = self.button('处理说明', '', self.show_help)
        for button in (self.region_button, self.whole, self.copy_zones, self.clear_manual, self.reset, self.help): advanced.addWidget(button)
        advanced_scroll.setWidget(panel); panel_layout.addWidget(advanced_scroll)
        self.adjustments.resize(380, min(600, round(screen.height() * .72)))
        self.annotation_panel = QWidget(); annotation_layout = QVBoxLayout(self.annotation_panel)
        annotation_layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout(); row.addWidget(QLabel('本页可删除的彩色批注：勾选后预览'))
        self.annotation_type = ClearComboBox(); self.annotation_type.addItem('全部类型', '')
        for kind in dict.fromkeys(ANNOT_TYPES.values()): self.annotation_type.addItem(kind, kind)
        row.addWidget(self.annotation_type)
        self.select_annotations = self.button('勾选筛选结果', '', lambda: self.check_annotations(True))
        self.clear_annotations = self.button('取消本页勾选', '', lambda: self.check_annotations(False))
        row.addWidget(self.select_annotations); row.addWidget(self.clear_annotations); row.addStretch()
        annotation_layout.addLayout(row)
        self.annotations = QListWidget(); self.annotations.setMaximumHeight(100)
        self.annotations.setAccessibleName('本页待删除的彩色批注，默认不勾选')
        annotation_layout.addWidget(self.annotations); layout.addWidget(self.annotation_panel); self.annotation_panel.hide()
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(False); self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.canvas = CleanupCanvas(); self.scroll.setWidget(self.canvas)
        self.scroll.setMinimumSize(280, 220)
        self.scroll.viewport().installEventFilter(self); self.canvas.installEventFilter(self)
        layout.addWidget(self.scroll, 1)
        self.ocr_box = QWidget(); ocr_row = QHBoxLayout(self.ocr_box); ocr_row.setContentsMargins(0, 0, 0, 0)
        ocr_label = QLabel('检测到旧识别文字；清理后，旧答案仍可能被搜索。'); ocr_label.setWordWrap(True)
        ocr_row.addWidget(ocr_label, 1)
        self.ocr = ClearComboBox(); self.ocr.addItem('请选择文字层处理方式', 'ask')
        self.ocr.addItem('移除旧识别文字（保留可见正文）', 'remove')
        self.ocr.addItem('保留旧识别文字（旧答案可能仍可搜索）', 'keep')
        self.ocr.setToolTip('移除后原 OCR 不能再搜索；此处不会自动重做识别。与可见正文重叠时会停止处理。')
        ocr_row.addWidget(self.ocr); layout.addWidget(self.ocr_box); self.ocr_box.hide()
        self.risk = QLabel(); self.risk.setWordWrap(True); self.risk.setObjectName('cleanupHint')
        layout.addWidget(self.risk); self.risk.hide()
        bottom = QHBoxLayout()
        self.status = QLabel('正在生成清理预览…'); self.status.setWordWrap(True); self.status.setObjectName('cleanupHint')
        bottom.addWidget(self.status, 1)
        self.fallback = self.button('选择转图片处理', '', self.choose_raster)
        bottom.addWidget(self.fallback); self.fallback.hide()
        reassurance = QLabel('原文件保留，另存为新的 PDF'); reassurance.setObjectName('cleanupHint')
        bottom.addWidget(reassurance)
        self.cancel_button = self.button('取消', '', self.reject)
        self.apply = self.button('另存清理副本', '', self.accept)
        self.apply.setObjectName('primary'); self.apply.setEnabled(False); self.apply.setDefault(True)
        bottom.addWidget(self.cancel_button); bottom.addWidget(self.apply); layout.addLayout(bottom)
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.timeout.connect(self.start_preview)
        self.zoom_timer = QTimer(self); self.zoom_timer.setSingleShot(True); self.zoom_timer.timeout.connect(self.refine_preview)
        self.signals = _Signals(); self.signals.ready.connect(self.preview_ready)
        self.scope.mode.currentIndexChanged.connect(self.scope_changed); self.scope.range.textChanged.connect(self.scope_changed)
        self.method.currentIndexChanged.connect(self.method_changed)
        for slider in (self.tolerance, self.faint): slider.valueChanged.connect(self.invalidate)
        for checkbox in (self.fringe, self.smooth): checkbox.toggled.connect(self.invalidate)
        self.ocr.currentIndexChanged.connect(self.invalidate); self.fill.currentIndexChanged.connect(self.invalidate)
        self.show_mask.toggled.connect(self.show_result)
        self.fit.currentIndexChanged.connect(self.fit_changed)
        self.brush.valueChanged.connect(self.update_brush)
        self.canvas.picked.connect(self.pick_color); self.canvas.zone_added.connect(self.add_zone)
        self.canvas.pan_delta.connect(self.pan); self.canvas.gesture_changed.connect(self.gesture_changed)
        self.annotation_type.currentIndexChanged.connect(self.fill_annotations)
        self.annotations.itemChanged.connect(self.annotation_changed)
        self.shortcuts = []
        for sequence, callback in ((QKeySequence.StandardKey.Undo, self.undo_zones),
                (QKeySequence.StandardKey.Redo, self.redo_zones), ('Ctrl+0', lambda: self.fit.setCurrentIndex(0)),
                ('Ctrl++', lambda: self.set_zoom(self.zoom_percent * 1.25)), ('Ctrl+-', lambda: self.set_zoom(self.zoom_percent / 1.25))):
            shortcut = QShortcut(QKeySequence(sequence), self); shortcut.activated.connect(callback); self.shortcuts.append(shortcut)
        self.update_history_buttons()
        self.invalidate()
        QTimer.singleShot(0, self.fit_zoom)

    def button(self, text, icon='', callback=None, *, checkable=False):
        button = QPushButton(text)
        button.setAccessibleName(text)
        if icon: button.setIcon(toolbar_icon(icon, size=19))
        button.setCheckable(checkable)
        if callback: button.clicked.connect(callback)
        return button

    def settings(self):
        return CleanupSettings(self.color, self.tolerance.value(), self.faint.value(),
            self.smooth.isChecked(), self.fringe.isChecked(), self.fill.currentData())

    def page_zones(self):
        return self.zones.get(self.current, CleanupZones())

    def show_adjustments(self):
        point = self.advanced.mapToGlobal(QPoint(0, self.advanced.height() + 3))
        available = self.screen().availableGeometry()
        self.adjustments.move(min(point.x(), available.right() - self.adjustments.width()),
            min(point.y(), available.bottom() - self.adjustments.height()))
        self.adjustments.show()

    def choose_color(self, color):
        self.color = color
        (self.red if color == (210, 25, 45) else self.blue).setChecked(True)
        if self.current_tool == 'pick': self.set_tool('move')
        self.pick.setText('取色')
        self.invalidate()

    def pick_color(self, color):
        try:
            replace(self.settings(), color=color).validate()
        except ValueError as exc:
            self.status.setText(str(exc)); return
        self.color = tuple(color)
        self.colors.setExclusive(False); self.red.setChecked(False); self.blue.setChecked(False); self.colors.setExclusive(True)
        self.pick.setText('取色 #' + ''.join(f'{c:02X}' for c in color))
        self.set_tool('move')
        self.invalidate()

    def method_changed(self, *_):
        self.selected.clear()
        self.invalidate()

    def scope_changed(self, *_):
        try:
            rows = self.scope.indices()
        except ValueError as exc:
            self.invalidate(); self.status.setText(str(exc)); return
        if self.current not in rows: self.go_to(rows[0])
        else: self.invalidate()

    def go_to(self, row):
        self.canvas.cancel_gesture()
        self.current = row
        self.result = {}
        self.canvas.image, self.canvas.source = QImage(), QImage()
        self.canvas.editable = False
        self.canvas.page_size = self.host.store.page_size(self.pages[row])
        self.fit_zoom()
        self.invalidate()

    def navigate(self, delta):
        try:
            rows = self.scope.indices(); position = rows.index(self.current) + delta
            if 0 <= position < len(rows): self.go_to(rows[position])
        except ValueError:
            pass

    def update_brush(self, *_):
        width, height = self.canvas.page_size
        radius = self.brush.value() / 10 * 72 / 25.4 / 2 / min(width, height)
        self.canvas.radius = min(.2, radius)
        self.brush_value.setText(f'{self.canvas.radius * min(width, height) * 2 * 25.4 / 72:g} mm')
        self.canvas.update()

    def set_tool(self, tool):
        if self.saving: return
        self.canvas.cancel_gesture()
        self.current_tool = self.canvas.role = tool
        self.adjustments.hide()
        self.tool_group.setExclusive(False)
        for key, button in self.tool_buttons.items(): button.setChecked(key == tool)
        self.tool_group.setExclusive(True)
        self.pick.setChecked(tool == 'pick')
        self.context.setVisible(tool in ('erase', 'keep'))
        self.shape_label.setText('保留方式' if tool == 'keep' else '擦除方式')
        self.fill.setVisible(tool == 'erase')
        hints = {'move': '拖动查看，Ctrl 加滚轮缩放', 'erase': '涂到的内容会被擦除，每一笔都可撤销',
                 'keep': '在原图上标记要保留的内容，保护区优先', 'pick': '点击原图中的彩色笔迹取色',
                 'region': '框选自动清理范围，其他区域保留'}
        self.tool_hint.setText(hints[tool])
        self.tool_hint.setToolTip(hints[tool])
        self.tool_hint.setVisible(self.width() >= 1100)
        self.status.setText(hints[tool])
        self.set_view('original' if tool in ('keep', 'pick', 'region') else 'cleaned')
        if tool == 'erase': self.show_mask.setChecked(False)
        self.canvas.setCursor(Qt.CursorShape.OpenHandCursor if tool == 'move' else Qt.CursorShape.CrossCursor)
        self.update_brush()

    def set_shape(self, shape):
        self.canvas.cancel_gesture()
        self.current_shape = self.canvas.shape = shape
        self.shape_buttons[shape].setChecked(True)
        for widget in (self.brush, self.brush_value, self.size_label): widget.setVisible(shape == 'brush')
        self.canvas.update()

    def set_view(self, view):
        self.current_view = view
        self.view_buttons[view].setChecked(True)
        if view == 'original': self.show_mask.setChecked(False)
        self.show_result()

    def show_result(self, *_):
        self.canvas.source = self.result.get('original', QImage())
        key = 'highlight' if self.show_mask.isChecked() and self.current_view == 'cleaned' else self.current_view
        self.canvas.image = self.result.get(key, self.canvas.source)
        self.canvas.zones = self.page_zones()
        self.canvas.show_live = self.current_view == 'cleaned' or self.current_tool == 'keep'
        if self.fill.currentData() == 'white':
            self.canvas.live_color = QColor('white')
        else:
            self.canvas.live_color = QColor(*self.result.get('paper_color', (255, 255, 255)))
        self.canvas.update()

    def gesture_changed(self, active):
        if active:
            self.generation += 1; self.cancel.set(); self.timer.stop(); self.zoom_timer.stop()
            self.sample_ready = False; self.apply.setEnabled(False)
        elif not self.closed:
            self.timer.start(180)

    def invalidate(self, *_, keep_live=False):
        if self.closed: return
        self.generation += 1; self.cancel.set(); self._zoom_only = False
        self.sample_ready = False; self.apply.setEnabled(False)
        if not keep_live: self.canvas.clear_live()
        self.canvas.zones = self.page_zones()
        self.canvas.page_size = self.host.store.page_size(self.pages[self.current])
        self.update_brush()
        self.status.setText('正在更新清理预览…')
        self.update_navigation()
        self.timer.start(180)

    def update_navigation(self):
        self.scope.current = self.current
        self.scope.mode.setItemText(self.scope.mode.findData('current'), f'当前页（第 {self.current + 1} 页）')
        try:
            rows = self.scope.indices()
            index = rows.index(self.current)
            self.page_label.setText(f'第 {self.current + 1} / {len(self.pages)} 页')
            self.previous.setEnabled(index > 0 and not self.saving)
            self.next.setEnabled(index < len(rows) - 1 and not self.saving)
        except ValueError:
            self.previous.setEnabled(False); self.next.setEnabled(False)

    def requested_width(self):
        width, height = self.canvas.page_size
        requested = max(1100, math.ceil(self.canvas.area().width() * self.devicePixelRatioF()))
        return max(64, round(min(3600, requested, math.sqrt(12_000_000 * width / height), 6000 * width / height)))

    def start_preview(self):
        if self.closed or self.saving or self.busy or self.canvas.drag: return
        try:
            self.scope.indices(); self.settings().validate()
        except ValueError as exc:
            self.status.setText(str(exc)); return
        self.cancel = Event(); self.busy = True
        self.render_width = self.requested_width()
        cached = self.result if self._zoom_only and self.result.get('prepared') else None
        self._zoom_only = False
        self.task = _Preview(self.host.store, self.pages[self.current], self.settings(), self.page_zones(),
            dict(mode=self.method.currentData(), selected=tuple(self.selected.get(self.current, ())), ocr=self.ocr.currentData()),
            self.render_width, self.generation, self.signals, self.cancel, cached)
        QThreadPool.globalInstance().start(self.task)

    def preview_ready(self, token, result, error):
        self.busy = False
        if self.closed or self.saving: return
        if token != self.generation:
            if not self.canvas.drag: self.timer.start(1)
            return
        self.result = result
        self.sample_ready = 'cleaned' in result and not error
        self.canvas.editable = self.sample_ready
        self.canvas.clear_live()
        native = result.get('mode') == 'annotations'
        self.annotation_panel.setVisible(native)
        self.tool_buttons['erase'].setEnabled(not native)
        self.fringe.setEnabled(not native)
        if native and self.current_tool == 'erase': self.set_tool('move')
        if native and 'selected' in result: self.selected[self.current] = set(result['selected'])
        self.ocr_box.setVisible(not native and (result.get('hidden', False) or self.force_ocr))
        raster = self.method.currentData() == 'raster'
        self.risk.setVisible(raster)
        self.risk.setText('所选页将转为图片，原生文字和矢量不保留；请核对细线与符号。其他页保持原样。')
        self.fallback.setVisible(bool(error) and not raster and ('图片' in error or '矢量' in error))
        self.fill_annotations()
        self.show_result()
        if error: self.status.setText(error)
        elif native: self.status.setText(f"本页可选 {len(result.get('candidates', ()))} 条批注，已勾选 {len(self.selected.get(self.current, ()))} 条")
        else: self.status.setText('本页预览已更新' if result.get('changed') else '未发现所选颜色的笔迹，可手工补擦或直接另存')
        self.update_apply()

    def update_apply(self):
        unresolved = (self.force_ocr or self.result.get('hidden', False) and self.result.get('changed', 0)) and self.result.get('mode') != 'annotations' and self.ocr.currentData() == 'ask'
        self.apply.setEnabled(self.sample_ready and not self.saving and not unresolved and not self.canvas.drag)

    def choose_raster(self):
        self.method.setCurrentIndex(self.method.findData('raster'))

    def push_history(self):
        self.history.append((dict(self.zones), {row: set(values) for row, values in self.selected.items()}, self.current))
        self.redo_history.clear(); self.update_history_buttons()

    def change_zones(self, zones, *, keep_live=False):
        self.push_history(); self.zones[self.current] = zones
        self.selected.pop(self.current, None)
        self.invalidate(keep_live=keep_live)

    def add_zone(self, role, mark):
        shape, value = mark
        zones = self.page_zones()
        field = ('region' if role == 'region' else
                 'erased_strokes' if role == 'erase' and shape == 'brush' else
                 'erased_ellipses' if role == 'erase' and shape == 'ellipse' else
                 'erased' if role == 'erase' else
                 'strokes' if shape == 'brush' else 'protected_ellipses' if shape == 'ellipse' else 'protected')
        update = value if field == 'region' else (*getattr(zones, field), value)
        self.change_zones(replace(zones, **{field: update}), keep_live=role == 'erase')

    def restore_history(self, source, destination):
        if self.saving: return
        if self.canvas.cancel_gesture(): return
        if not source: return
        destination.append((dict(self.zones), {row: set(values) for row, values in self.selected.items()}, self.current))
        self.zones, self.selected, row = source.pop()
        try:
            if row in self.scope.indices() and row != self.current:
                self.current = row; self.result = {}; self.canvas.image, self.canvas.source = QImage(), QImage()
        except ValueError:
            pass
        self.update_history_buttons(); self.fit_zoom(); self.invalidate()

    def undo_zones(self):
        self.restore_history(self.history, self.redo_history)

    def redo_zones(self):
        self.restore_history(self.redo_history, self.history)

    def update_history_buttons(self):
        self.undo_zone.setEnabled(bool(self.history) and not self.saving)
        self.redo_zone.setEnabled(bool(self.redo_history) and not self.saving)

    def clear_page_marks(self):
        self.change_zones(CleanupZones(region=self.page_zones().region))

    def copy_page_zones(self):
        try:
            rows = self.scope.indices()
        except ValueError as exc:
            self.status.setText(str(exc)); return
        self.push_history(); zones = self.page_zones()
        for row in rows:
            local = self.zones.get(row, CleanupZones())
            self.zones[row] = replace(zones, erased=local.erased, erased_strokes=local.erased_strokes,
                                       erased_ellipses=local.erased_ellipses)
            self.selected.pop(row, None)
        self.invalidate()

    def reset_parameters(self):
        self.tolerance.setValue(30); self.faint.setValue(45)
        self.smooth.setChecked(False); self.fringe.setChecked(True)
        self.invalidate()

    def fill_annotations(self, *_):
        self.annotations.blockSignals(True); self.annotations.clear()
        for annot in self.result.get('candidates', ()):
            if self.annotation_type.currentData() and annot['kind'] != self.annotation_type.currentData(): continue
            item = QListWidgetItem(f"{annot['kind']} · {annot['content'] or annot['id']}")
            item.setData(Qt.ItemDataRole.UserRole, annot['id'])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if annot['id'] in self.selected.get(self.current, ()) else Qt.CheckState.Unchecked)
            self.annotations.addItem(item)
        self.annotations.blockSignals(False)

    def annotation_changed(self, item):
        self.push_history(); chosen = self.selected.setdefault(self.current, set())
        token = item.data(Qt.ItemDataRole.UserRole)
        chosen.add(token) if item.checkState() == Qt.CheckState.Checked else chosen.discard(token)
        self.invalidate()

    def check_annotations(self, checked):
        self.push_history(); chosen = self.selected.setdefault(self.current, set())
        chosen.update(self.annotations.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.annotations.count())) if checked else chosen.clear()
        self.invalidate()

    def fit_changed(self, *_):
        mode = self.fit.currentData()
        if mode == 'actual': self.set_zoom(100.)
        else:
            self.zoom_mode = mode; self.fit_zoom(); self.zoom_timer.start(250)

    def fit_zoom(self):
        if self.closed or not hasattr(self, 'scroll'): return
        width, height = self.host.store.page_size(self.pages[self.current])
        self.canvas.page_size = (width, height)
        available_w, available_h = max(80, self.scroll.viewport().width() - 48), max(80, self.scroll.viewport().height() - 48)
        if self.zoom_mode != 'manual':
            scale = available_w / width
            if self.zoom_mode == 'page': scale = min(scale, available_h / height)
            self.zoom_percent = scale / (96 / 72) * 100
        self.canvas.scale = self.zoom_percent / 100 * 96 / 72
        canvas_width = self.scroll.viewport().width() if self.zoom_mode in ('page', 'width') else max(self.scroll.viewport().width(), math.ceil(width * self.canvas.scale + 48))
        canvas_height = self.scroll.viewport().height() if self.zoom_mode == 'page' else max(self.scroll.viewport().height(), math.ceil(height * self.canvas.scale + 48))
        self.canvas.resize(canvas_width, canvas_height)
        self.zoom_label.setText(f'{self.zoom_percent:.0f}%')
        self.update_brush(); self.canvas.update()
        if self.sample_ready and not self.saving and hasattr(self, 'zoom_timer'):
            self.zoom_timer.start(250)

    def set_zoom(self, percent, anchor=None):
        if self.canvas.drag or self.saving: return
        anchor = anchor or self.scroll.viewport().rect().center()
        previous = self.canvas.mapFrom(self.scroll.viewport(), anchor)
        normal = self.canvas.point(QPointF(previous))
        self.zoom_mode, self.zoom_percent = 'manual', max(10., min(600., percent))
        self.fit_zoom()
        area = self.canvas.area()
        self.scroll.horizontalScrollBar().setValue(round(area.x() + normal[0] * area.width() - anchor.x()))
        self.scroll.verticalScrollBar().setValue(round(area.y() + normal[1] * area.height() - anchor.y()))
        self.zoom_timer.start(250)

    def refine_preview(self):
        image = self.result.get('original', QImage())
        if not self.sample_ready or image.isNull() or self.requested_width() <= image.width() * 1.2: return
        self.generation += 1; self.cancel.set(); self._zoom_only = True
        self.timer.start(1)

    def pan(self, delta):
        self.scroll.horizontalScrollBar().setValue(self.scroll.horizontalScrollBar().value() - round(delta.x()))
        self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().value() - round(delta.y()))

    def eventFilter(self, watched, event):
        if not self.closed and hasattr(self, 'scroll') and watched in (self.scroll.viewport(), self.canvas):
            if event.type() == QEvent.Type.Resize and watched is self.scroll.viewport():
                QTimer.singleShot(0, self.fit_zoom)
            elif event.type() == QEvent.Type.Wheel and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                point = event.position().toPoint()
                if watched is self.canvas: point = self.canvas.mapTo(self.scroll.viewport(), point)
                self.set_zoom(self.zoom_percent * 1.25 ** (event.angleDelta().y() / 120), point)
                return True
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        if hasattr(self, 'tool_hint'): self.tool_hint.setVisible(self.width() >= 1100)
        super().resizeEvent(event)

    def show_help(self):
        QMessageBox.information(self, '清理与补擦',
            '先选红色、蓝色或取色，程序会生成清理预览。可对比原图，放大后检查细节。\n\n'
            '擦除：画笔可直接涂抹，矩形或圆形拖出范围后填补形状内部；大小按纸面毫米计算。'
            '默认涂白，也可匹配纸面。保留内容：在原图上标记不允许改动的内容。保护区始终优先。'
            '每一笔可撤销、重做，换页保留修改。手工擦除只作用于当前页，不复制到其他页。\n\n'
            '调整效果中可改颜色容差、浅色识别、清除残影和局部范围。原生批注须逐项勾选，链接和表单保留。'
            '复杂图片/矢量页可能不支持，转图片须明确选择，会失去处理页的原生正文和矢量，其他页保留。\n\n'
            '有旧 OCR 文字时必须选择保留或移除；保留可能仍能搜索旧答案，移除不会自动重做 OCR。'
            '近黑笔尾与正文无法可靠区分，可手工补擦。被完全遮挡的内容无法恢复，同色印刷字和印章需保护。\n\n'
            '全程本机处理，另存新的 PDF。取消或保存失败时修改仍留在窗口，原件不变。此功能不是安全脱敏工具。')

    def snapshot_valid(self):
        return self.host.ws is self.workspace and tuple(self.workspace.pages) == self.pages and self.workspace.outline == self.outline

    def accept(self):
        if not self.sample_ready or not self.apply.isEnabled() or self.saving: return
        if not self.snapshot_valid():
            self.status.setText('原工作区已改变，请关闭后重新打开清理工具。'); return
        from app.exportdialogs import new_pdf_path
        self.adjustments.hide()
        suggested = Path(self.workspace.path or self.pages[0].source_path or '文档.pdf').stem + '_清理副本.pdf'
        path = new_pdf_path(self, self.host.store, '另存清理副本', suggested)
        if not path: return
        self.saving = True; self.cancel.set(); self.timer.stop(); self.zoom_timer.stop(); self.generation += 1
        self.apply.setEnabled(False); self.canvas.editable = False
        self.scope.setEnabled(False); self.annotation_panel.setEnabled(False); self.ocr_box.setEnabled(False)
        self.toolbar.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.update_history_buttons(); self.update_navigation()
        try:
            settings, rows = self.settings(), self.scope.indices()
            zones = dict(self.zones); selected = {row: tuple(values) for row, values in self.selected.items()}
            mode, ocr = self.method.currentData(), self.ocr.currentData()
            def prepare(progress, cancel):
                changes = prepare_cleanup(self.host.store, self.pages, rows, settings, zones=zones,
                    selected=selected, mode=mode, ocr=ocr, progress=progress, cancel=cancel)
                return changes, cancel
            changes, cancelled = run_operation(self, '准备清理副本', prepare, cancellable=True)
            check_cancel(cancelled)
            if not self.snapshot_valid(): raise ValueError('原工作区已改变，未保存，请重新打开清理工具。')
            pages = [changes.get(row, ref) for row, ref in enumerate(self.pages)]
            result = self.host._write_pdf(pages, path, '另存清理副本', options=WriteOptions(outline=self.outline))
            if result is None:
                self.status.setText('保存已取消，修改仍保留，可继续编辑。'); return
            self.saved_result = result
            super().accept()
        except OperationCancelled:
            self.status.setText('已取消保存，修改仍保留，可继续编辑。')
        except (ValueError, PdfError, OSError, RuntimeError) as exc:
            if '隐藏文字层' in str(exc):
                self.force_ocr = True; self.ocr_box.show()
            self.status.setText(str(exc))
            QMessageBox.warning(self, '清理副本未保存', str(exc) + '\n修改仍保留在当前窗口，原件未改动。')
        finally:
            self.saving = False
            self.canvas.editable = self.sample_ready
            self.scope.setEnabled(True); self.annotation_panel.setEnabled(True); self.ocr_box.setEnabled(True)
            self.toolbar.setEnabled(True)
            self.cancel_button.setEnabled(True)
            self.update_history_buttons(); self.update_navigation(); self.update_apply()

    def done(self, result):
        if self.saving and result != QDialog.DialogCode.Accepted: return
        self.closed = True; self.cancel.set(); self.timer.stop(); self.zoom_timer.stop()
        self.adjustments.hide(); self.canvas.cancel_gesture()
        super().done(result)


def open_color_cleanup(host):
    if not host.ws.pages or not host._allow_operation('copy'): return
    dialog = ColorCleanupDialog(host)
    try:
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.saved_result is None: return
        result = dialog.saved_result
        host._show_write_notes((result,))
        host.open_files_in_tabs([result.path])
        host.statusBar().showMessage('清理副本已保存，原文件与原工作区保留。', 7000)
    finally:
        dialog.deleteLater()
