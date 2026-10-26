"""Lightweight, reversible placement and transforms for paper images."""
from app.themes import set_theme_style
import base64
import math
import uuid

from PyQt6.QtCore import QEvent, QObject, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QTransform
from PyQt6.QtWidgets import (QApplication, QCheckBox, QDoubleSpinBox, QFileDialog, QFrame,
                            QGraphicsDropShadowEffect, QGraphicsObject, QGraphicsPixmapItem,
                            QGridLayout, QLabel, QMessageBox, QPushButton)

from app.output import OperationCancelled
from app.pdfimages import fit_image, validate_image, MAX_IMAGE_BYTES
from app.pdfio import PdfError
from app.progress import run_operation
from app.workspace import PageEdit


def load_image_asset(path):
    with open(path, 'rb') as stream:
        data = stream.read(MAX_IMAGE_BYTES + 1)
    width, height = validate_image(data)
    image = QImage.fromData(data)
    if image.isNull():
        raise ValueError('图片无法解码，请换一张图片。')
    image = image.scaled(1024, 1024, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    return data, width, height, image


class ImageOverlay(QGraphicsObject):
    def __init__(self, tools):
        super().__init__()
        self.tools = tools
        self.bounds = QRectF()
        self.setZValue(6)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def boundingRect(self):
        return self.bounds

    def update_geometry(self):
        self.prepareGeometryChange()
        settings = self.tools.settings
        if not settings:
            self.bounds = QRectF()
            return
        pad = 32 / max(.01, self.tools.view.transform().m11())
        self.bounds = QRectF(-settings['width'] / 2 - pad, -settings['height'] / 2 - pad,
                             settings['width'] + 2 * pad, settings['height'] + 2 * pad)

    def handles(self):
        s = self.tools.settings
        w, h = s['width'] / 2, s['height'] / 2
        points = {(x, y): QPointF(x * w, y * h)
                  for x, y in ((-1, -1), (0, -1), (1, -1), (1, 0),
                               (1, 1), (0, 1), (-1, 1), (-1, 0))}
        points['rotate'] = QPointF(0, -h - 24 / max(.01, self.tools.view.transform().m11()))
        return points

    def paint(self, painter, option, widget=None):
        s = self.tools.settings
        if not s:
            return
        rect = QRectF(-s['width'] / 2, -s['height'] / 2, s['width'], s['height'])
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(rect, self.tools.image)
        pen = QPen(QColor('#0F766E'), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)
        if self.tools.pending:
            return
        painter.drawLine(QPointF(0, rect.top()), self.handles()['rotate'])
        painter.setBrush(QColor('white'))
        radius = 4 / max(.01, self.tools.view.transform().m11())
        for key, point in self.handles().items():
            if key == 'rotate':
                painter.drawEllipse(point, radius, radius)
            else:
                painter.drawRect(QRectF(point.x() - radius, point.y() - radius, 2 * radius, 2 * radius))


class ImageTools(QObject):
    def __init__(self, host):
        super().__init__(host)
        self.host, self.view = host, host.preview_view
        self.settings = self.ref = self.index = self.identifier = None
        self.pending = self.drag = None
        self.image = QImage()
        self.overlay = self.backdrop = None
        self.committing = False
        self.shown_values = {}
        self.bar = QFrame(self.view.viewport())
        self.bar.setObjectName('imageBar')
        set_theme_style(self.bar, """
            QFrame#imageBar {
                background: #FFFFFF;
                border: 1px solid #D7E8E4;
                border-radius: 8px;
            }
            QLabel {
                font-size: 12px;
                color: #0F766E;
                font-weight: 500;
            }
            QDoubleSpinBox {
                min-height: 24px;
                padding: 0 16px 0 4px;
                font-size: 12px;
                border-radius: 6px;
                border: 1px solid #D7E8E4;
                background: #F8FAFA;
            }
            QDoubleSpinBox:focus {
                border: 1px solid #0F766E;
                background: #FFFFFF;
            }
            QCheckBox {
                font-size: 12px;
                color: #0F766E;
            }
            QPushButton#deleteBtn {
                min-height: 24px;
                padding: 2px 10px;
                font-size: 12px;
                border-radius: 6px;
                border: 1px solid #FECACA;
                color: #DC2626;
                background: #FEF2F2;
            }
            QPushButton#deleteBtn:hover {
                background: #FEE2E2;
                border-color: #FCA5A5;
            }
        """)
        shadow = QGraphicsDropShadowEffect(self.bar)
        shadow.setBlurRadius(12)
        shadow.setColor(QColor(0, 0, 0, 40))
        shadow.setOffset(0, 2)
        self.bar.setGraphicsEffect(shadow)
        layout = QGridLayout(self.bar)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(4)
        self.width, self.height, self.angle = QDoubleSpinBox(), QDoubleSpinBox(), QDoubleSpinBox()
        for column, (label, spin, (lo, hi), sfx) in enumerate((('宽', self.width, (.1, 40000), ' mm'),
                                                               ('高', self.height, (.1, 40000), ' mm'),
                                                               ('角度', self.angle, (-180, 180), '°'))):
            layout.addWidget(QLabel(label), 0, column * 2)
            spin.setAccessibleName('图片' + label)
            spin.setKeyboardTracking(False)
            spin.setDecimals(1)
            spin.setRange(lo, hi)
            spin.setSuffix(sfx)
            spin.setMaximumWidth(86)
            layout.addWidget(spin, 0, column * 2 + 1)
        self.lock = QCheckBox('锁定比例')
        self.lock.setChecked(True)
        layout.addWidget(self.lock, 1, 0, 1, 4)
        remove = QPushButton('删除')
        remove.setObjectName('deleteBtn')
        remove.setToolTip('删除图片（Delete）')
        remove.clicked.connect(self.delete)
        layout.addWidget(remove, 1, 4, 1, 2)
        self.width.editingFinished.connect(lambda: self.apply_values('width'))
        self.height.editingFinished.connect(lambda: self.apply_values('height'))
        self.angle.editingFinished.connect(lambda: self.apply_values('angle'))
        self.lock.toggled.connect(lambda: self.apply_values('locked'))
        self.bar.hide()
        self.view.view_changed.connect(self.position_bar)
        QApplication.instance().installEventFilter(self)

    @property
    def engaged(self):
        return self.settings is not None

    def clear(self):
        for item in (self.overlay, self.backdrop):
            if item is not None and item.scene() is not None:
                item.scene().removeItem(item)
        self.overlay = self.backdrop = None
        self.settings = self.ref = self.index = self.identifier = None
        self.pending = self.drag = None
        self.image = QImage()
        self.bar.hide()
        self.view.viewport().unsetCursor()

    def sync(self):
        if self.committing or not self.engaged:
            return
        if (not self.view.isVisible() or self.index != self.host.ws.preview_index()
                or self.index >= len(self.host.ws.pages) or self.host.ws.pages[self.index] is not self.ref):
            self.clear()

    def matrix(self):
        import pymupdf as p
        summary = self.host.store.cached_annotations(self.ref)
        if summary is None:
            raise ValueError('页面尚未准备好，请稍候重试。')
        return p.Matrix(summary['matrix'])

    def canonical(self, point):
        import pymupdf as p
        local = point - self.view.page_rects[self.index].topLeft()
        result = p.Point(local.x(), local.y()) * ~self.matrix()
        return QPointF(result.x, result.y)

    def bounds(self):
        import pymupdf as p
        rect = self.view.page_rects[self.index]
        return tuple(p.Rect(0, 0, rect.width(), rect.height()) * ~self.matrix())

    def display_rotation(self):
        matrix = self.matrix()
        return math.degrees(math.atan2(matrix.b, matrix.a))

    def update_overlay(self):
        import pymupdf as p
        if self.overlay is None:
            self.overlay = ImageOverlay(self)
            self.view.scene().addItem(self.overlay)
        self.overlay.update_geometry()
        s = self.settings
        center = p.Point(s['cx'], s['cy']) * self.matrix()
        self.overlay.setPos(self.view.page_rects[self.index].topLeft() + QPointF(center.x, center.y))
        self.overlay.setRotation(s['angle'] + self.display_rotation())
        self.overlay.update()
        self.position_bar()

    def position_bar(self, force=False):
        if not self.engaged or self.overlay is None:
            return
        self.overlay.update_geometry()
        if self.pending:
            self.bar.hide()
            return
        s = self.settings
        for field, spin, value in (('width', self.width, s['width'] * 25.4 / 72),
                                   ('height', self.height, s['height'] * 25.4 / 72),
                                   ('angle', self.angle, (s['angle'] + self.display_rotation() + 180) % 360 - 180)):
            if force or (not spin.hasFocus() and not spin.lineEdit().hasFocus()):
                spin.setValue(value)
                self.shown_values[field] = spin.value()
        self.lock.blockSignals(True)
        self.lock.setChecked(bool(s.get('locked', True)))
        self.lock.blockSignals(False)
        self.bar.adjustSize()
        point = self.view.mapFromScene(self.overlay.sceneBoundingRect().topLeft())
        self.bar.move(max(0, min(self.view.viewport().width() - self.bar.width(), point.x())),
                      max(0, min(self.view.viewport().height() - self.bar.height(), point.y() - self.bar.height() - 4)))
        self.bar.show()
        self.bar.raise_()

    def choose_image(self):
        tools = self.host.annotation_tools
        if not tools.finish_text_box() or not self.host._allow_operation('content_edit'):
            return
        tools.tool.setCurrentIndex(0)
        tools.cancel_move_annot()
        tools._close_inline_replace()
        tools.clear_overlay()
        tools.hover_bar.hide()
        path, _ = QFileDialog.getOpenFileName(self.host, '插入图片', '', '图片 (*.png *.jpg *.jpeg)')
        if not path:
            return
        self.clear()
        try:
            data, width, height, image = run_operation(self.host, '读取图片', lambda progress: load_image_asset(path))
            index = self.host.ws.preview_index()
            if index is None:
                return
            self.index, self.ref = index, self.host.ws.pages[index]
            bounds = self.bounds()
            factor = min((bounds[2] - bounds[0]) * .5 / width, (bounds[3] - bounds[1]) * .5 / height)
            self.settings = dict(version=1, cx=(bounds[0] + bounds[2]) / 2,
                                 cy=(bounds[1] + bounds[3]) / 2, width=width * factor,
                                 height=height * factor, angle=-self.display_rotation(), locked=True)
            self.pending = base64.b64encode(data).decode('ascii')
            self.identifier = 'zhixu-image-' + uuid.uuid4().hex
            self.image = image
            self.update_overlay()
            self.view.setFocus()
            self.view.viewport().setCursor(Qt.CursorShape.CrossCursor)
            self.host.statusBar().showMessage('在当前页点击放置图片；Esc 取消。', 6000)
        except (OSError, ValueError, PdfError, OperationCancelled) as exc:
            self.clear()
            if not isinstance(exc, OperationCancelled):
                QMessageBox.information(self.host, '图片未插入', str(exc))

    def select_image(self, index, item):
        if not self.host._allow_operation('content_edit'):
            return False
        self.clear()
        if self.host.ws.preview_index() != index:
            self.host.go_to_page(index + 1, reveal=False)
        ref = self.host.ws.pages[index]
        try:
            image, background = run_operation(self.host, '准备图片编辑', lambda progress:
                self.host.store.image_editor_preview(ref, item['id'], self.host._preview_render_width()))
            if index >= len(self.host.ws.pages) or self.host.ws.pages[index] is not ref:
                raise ValueError('页面已改变，请重新选择图片。')
            self.index, self.ref, self.identifier = index, ref, item['id']
            self.settings, self.image = dict(item['image_settings']), image
            rect = self.view.page_rects[index]
            self.backdrop = QGraphicsPixmapItem(QPixmap.fromImage(background))
            self.backdrop.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.backdrop.setZValue(4)
            self.backdrop.setPos(rect.topLeft())
            self.backdrop.setTransform(QTransform.fromScale(rect.width() / background.width(),
                                                            rect.height() / background.height()))
            self.view.scene().addItem(self.backdrop)
            self.host.text_selection.clear()
            self.host.annotation_tools.hover_bar.hide()
            self.update_overlay()
            self.view.setFocus()
            return True
        except (ValueError, PdfError, OperationCancelled) as exc:
            self.clear()
            if not isinstance(exc, OperationCancelled):
                QMessageBox.information(self.host, '图片未选中', str(exc))
            return False

    def hit(self, point):
        if self.overlay is None:
            return None
        local = self.overlay.mapFromScene(point)
        radius = 8 / max(.01, self.view.transform().m11())
        for key, position in self.overlay.handles().items():
            if (local - position).manhattanLength() <= radius:
                return key
        s = self.settings
        return 'move' if abs(local.x()) <= s['width'] / 2 and abs(local.y()) <= s['height'] / 2 else None

    def apply_values(self, field):
        if not self.engaged or self.pending or self.committing:
            return
        if field != 'locked' and getattr(self, field).value() == self.shown_values.get(field):
            return
        previous = dict(self.settings)
        settings = dict(previous, locked=self.lock.isChecked())
        if field in ('width', 'height'):
            settings[field] = getattr(self, field).value() * 72 / 25.4
            if settings['locked']:
                other = 'height' if field == 'width' else 'width'
                settings[other] *= settings[field] / previous[field]
        elif field == 'angle':
            settings['angle'] = self.angle.value() - self.display_rotation()
        settings = fit_image(settings, self.bounds())
        if settings != previous:
            self.settings = settings
            if not self.commit():
                self.settings = previous
            if self.engaged:
                self.update_overlay()
                self.position_bar(force=True)

    def commit(self, delete=False):
        if not self.engaged or not self.host._allow_operation('content_edit'):
            return False
        index, ref, identifier = self.index, self.ref, self.identifier
        if index >= len(self.host.ws.pages) or self.host.ws.pages[index] is not ref:
            self.clear()
            return False
        options = dict(self.settings)
        was_pending = self.pending is not None
        if was_pending:
            options['image'] = self.pending
        kind = 'image_delete' if delete else 'image' if was_pending else 'image_update'
        edit = PageEdit(kind, annotation_id=identifier, details=tuple(options.items()))
        candidate = ref.edited(edit)
        self.committing = True
        try:
            image = run_operation(self.host, '应用图片修改', lambda progress:
                self.host.store.render_qimage(candidate, self.host._preview_render_width()))
            if index >= len(self.host.ws.pages) or self.host.ws.pages[index] is not ref:
                raise ValueError('页面已改变，图片修改未应用。')
            self.host._cache_page_image(candidate.render_key, image)
            self.host.ws.edit_page(index, edit, '删除图片' if delete else '插入图片' if was_pending else '调整图片',
                                   prepared=candidate)
            self.ref = candidate
            self.pending = None
            self.host.refresh()
            if delete:
                self.clear()
            elif was_pending:
                # Selection prepares its image-free backdrop only once, never during dragging.
                item = dict(id=identifier, image_settings=dict(self.settings))
                self.committing = False
                self.select_image(index, item)
            self.host.statusBar().showMessage('图片修改已应用；Ctrl+Z 撤销，Ctrl+S 保存。', 5000)
            return True
        except (ValueError, PdfError, OperationCancelled) as exc:
            if not isinstance(exc, OperationCancelled):
                QMessageBox.information(self.host, '图片修改未应用', str(exc))
            return False
        finally:
            self.committing = False

    def delete(self):
        self.commit(delete=True)

    def update_drag(self, point):
        state = self.drag
        settings = dict(state['settings'])
        current = self.canonical(point)
        if state['kind'] == 'move':
            delta = current - state['point']
            settings['cx'] += delta.x()
            settings['cy'] += delta.y()
        elif state['kind'] == 'rotate':
            center = QPointF(settings['cx'], settings['cy'])
            start, end = state['point'] - center, current - center
            settings['angle'] += math.degrees(math.atan2(end.y(), end.x()) - math.atan2(start.y(), start.x()))
        else:
            local = self.overlay.mapFromScene(point)
            x, y = state['kind']
            if x:
                settings['width'] = max(2, abs(local.x()) * 2)
            if y:
                settings['height'] = max(2, abs(local.y()) * 2)
            if settings.get('locked', True):
                rx = settings['width'] / state['settings']['width']
                ry = settings['height'] / state['settings']['height']
                ratio = (rx if abs(rx - 1) >= abs(ry - 1) else ry) if x and y else rx if x else ry
                settings['width'] = state['settings']['width'] * ratio
                settings['height'] = state['settings']['height'] * ratio
        self.settings = fit_image(settings, self.bounds())
        self.update_overlay()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.WindowDeactivate and watched is self.host:
            if self.drag:
                self.settings, self.drag = self.drag['settings'], None
                self.update_overlay()
        if watched not in (self.view, self.view.viewport()):
            return False
        if self.committing:
            return False
        kind = event.type()
        if kind == QEvent.Type.Hide:
            self.clear()
            return False
        self.sync()
        if (kind == QEvent.Type.KeyPress and self.engaged
                and QApplication.focusWidget() in (self.view, self.view.viewport())):
            if event.key() == Qt.Key.Key_Escape:
                if self.drag:
                    self.settings, self.drag = self.drag['settings'], None
                    self.update_overlay()
                else:
                    self.clear()
                return True
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and not self.pending:
                self.delete()
                return True
        if (watched is not self.view.viewport() or kind not in
                (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress,
                 QEvent.Type.MouseButtonRelease, QEvent.Type.Wheel)):
            return False
        if (self.host.reader_interaction.panning
                or event.modifiers() & Qt.KeyboardModifier.AltModifier):
            return False
        tools = self.host.annotation_tools
        if tools.isVisible() and tools.tool.currentData() != 'browse':
            return False
        point = self.view.mapToScene(event.position().toPoint())
        if self.pending and kind in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress):
            rect = self.view.page_rects[self.index]
            if rect.contains(point):
                local = self.canonical(point)
                self.settings = fit_image(dict(self.settings, cx=local.x(), cy=local.y()), self.bounds())
                self.update_overlay()
                if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                    self.commit()
            return True
        if self.drag:
            if kind == QEvent.Type.Wheel:
                return True
            if kind == QEvent.Type.MouseMove:
                self.update_drag(point)
                return True
            if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                self.update_drag(point)
                previous = self.drag['settings']
                self.drag = None
                if self.settings != previous and not self.commit():
                    self.settings = previous
                if self.engaged:
                    self.update_overlay()
                return True
        hit = self.hit(point) if self.engaged else None
        if kind == QEvent.Type.MouseMove and hit:
            cursor = (Qt.CursorShape.SizeAllCursor if hit == 'move' else Qt.CursorShape.CrossCursor
                      if hit == 'rotate' else Qt.CursorShape.SizeFDiagCursor)
            self.view.viewport().setCursor(cursor)
            return True
        if kind == QEvent.Type.MouseMove and self.engaged:
            self.view.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        if kind != QEvent.Type.MouseButtonPress or event.button() != Qt.MouseButton.LeftButton:
            return False
        if hit:
            self.drag = dict(kind=hit, settings=dict(self.settings), point=self.canonical(point))
            self.view.setFocus()
            return True
        if self.engaged:
            self.clear()
        index = self.view.page_at_point(point)
        if not 0 <= index < len(self.host.ws.pages) or not self.view.page_rects[index].contains(point):
            return False
        ref = self.host.ws.pages[index]
        summary = self.host.store.cached_annotations(ref)
        if summary is None:
            return False
        local = point - self.view.page_rects[index].topLeft()
        import pymupdf as p
        canonical = p.Point(local.x(), local.y()) * ~p.Matrix(summary['matrix'])
        for item in reversed(summary['annotations']):
            s = item.get('image_settings')
            if s:
                relative = canonical - p.Point(s['cx'], s['cy'])
                angle = math.radians(-s['angle'])
                x = relative.x * math.cos(angle) - relative.y * math.sin(angle)
                y = relative.x * math.sin(angle) + relative.y * math.cos(angle)
                if abs(x) <= s['width'] / 2 and abs(y) <= s['height'] / 2:
                    if self.select_image(index, item):
                        current = self.view.mapToScene(event.position().toPoint())
                        self.drag = dict(kind='move', settings=dict(self.settings), point=self.canonical(current))
                    return True
        return False
