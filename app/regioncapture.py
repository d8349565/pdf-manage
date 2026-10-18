"""Copy a PDF region with its hover comments expanded below the page image."""
from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path

import pymupdf as p
from PyQt6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QColor, QFont, QFontMetricsF, QImage, QKeySequence, QPainter,
                        QPen, QPixmap, QShortcut, QTextDocument, QTextOption)
from PyQt6.QtWidgets import (QApplication, QDialog, QFileDialog, QGraphicsRectItem,
                             QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel,
                             QMessageBox, QPushButton, QVBoxLayout, QWidget)

from app.output import OperationCancelled, check_cancel, commit_file, stamp
from app.pdfio import PdfError
from app.progress import run_operation
from app.storage import export_path


def selected_comments(annotations, matrix, area):
    """Use displayed geometry so numbering follows reading order after rotation."""
    result = []
    for annot in annotations:
        content = (annot.get('content') or '').strip()
        # FreeText is already printed on the page; watermarks/images are not comments.
        if not content or annot['kind'] == 2 or annot.get('watermark') or annot.get('image_settings'):
            continue
        rect = p.Rect(annot['rect']) * matrix
        clipped = rect & area
        if not clipped.is_empty:
            result.append(dict(content=content, rect=clipped))
    return sorted(result, key=lambda item: (item['rect'].y0, item['rect'].x0))


def comment_document(text, width, font):
    document = QTextDocument()
    document.setDefaultFont(font)
    document.setDocumentMargin(0)
    option = document.defaultTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    document.setDefaultTextOption(option)
    document.setPlainText(text)
    document.setTextWidth(width)
    return document


def comment_markers(notes, origin, scale, crop_x, crop_y, width, height, radius, gap):
    """Place badges beside marks, with a leader endpoint on each mark's boundary."""
    if not notes:
        return []
    bounds = [QRectF(crop_x + (note['rect'].x0 - origin[0]) * scale,
                     crop_y + (note['rect'].y0 - origin[1]) * scale,
                     note['rect'].width * scale, note['rect'].height * scale) for note in notes]
    canvas = QRectF(2, 2, width - 4, height - 4)
    occupied, result = [], []
    step = math.ceil(radius * 2 + gap)
    # Border positions are a fallback for adjacent or overlapping PDF comments.
    border = ([QPointF(x, y) for y in range(step, height - radius, step)
               for x in (radius + gap, width - radius - gap)]
              + [QPointF(x, y) for x in range(step, width - radius, step)
                 for y in (radius + gap, height - radius - gap)])
    for rect in bounds:
        center = rect.center()
        candidates = [QPointF(rect.left() - radius - gap, center.y()),
                      QPointF(rect.right() + radius + gap, center.y()),
                      QPointF(center.x(), rect.top() - radius - gap),
                      QPointF(center.x(), rect.bottom() + radius + gap)]
        candidates += sorted(border, key=lambda point: (point.x() - center.x()) ** 2
                             + (point.y() - center.y()) ** 2)
        for point in candidates:
            badge = QRectF(point.x() - radius - 2, point.y() - radius - 2,
                           radius * 2 + 4, radius * 2 + 4)
            if (canvas.contains(badge) and not any(badge.intersects(other) for other in bounds)
                    and not any(badge.intersects(other) for other in occupied)):
                anchor = QPointF(max(rect.left(), min(rect.right(), point.x())),
                                 max(rect.top(), min(rect.bottom(), point.y())))
                result.append((point, anchor))
                occupied.append(badge)
                break
        else:
            raise PdfError('批注标记过于密集，无法清楚排放编号，请分次框选导出。')
    return result


def render_capture(store, ref, region, progress, cancel):
    """Render the current edit draft, never the reader's overlays or popup widgets."""
    from app.pdfedits import annotation_summary

    store.require_operation([ref], 'copy')
    if not all(math.isfinite(value) for value in region):
        raise PdfError('无效的框选范围，请重新框选。')
    area = p.Rect(region) & p.Rect(0, 0, *store.page_size(ref))
    if area.is_empty or area.width < 1 or area.height < 1:
        raise PdfError('框选区域太小，请重新框选。')
    check_cancel(cancel)
    progress(0, 3, '正在读取区域内的批注')
    with store._render_page(ref, cancel) as page:
        extra = p.Matrix(1, 1).prerotate(ref.rotation)
        rotated = page.rect * extra
        extra *= p.Matrix(1, 0, 0, 1, -rotated.x0, -rotated.y0)
        notes = selected_comments(annotation_summary(page), page.rotation_matrix * extra, area)
    check_cancel(cancel)
    progress(1, 3, '正在生成原文图片')
    scale = min(200 / 72, 2400 / area.width, 3000 / area.height)
    crop, origin, scale = store.render_region(ref, tuple(area), scale)
    check_cancel(cancel)
    progress(2, 3, '正在排列批注说明')
    content_width = max(640, crop.width())
    unit = max(1, content_width / 900)
    padding, gap, radius = round(24 * unit), round(16 * unit), round(14 * unit)
    font = QFont('Microsoft YaHei UI')
    font.setPixelSize(round(20 * unit))
    radius = max(radius, math.ceil(QFontMetricsF(font).horizontalAdvance(str(max(1, len(notes)))) / 2 + 4 * unit))
    marker_gap = round(6 * unit)
    padding = max(padding, radius * 2 + marker_gap + round(8 * unit))
    width = content_width + padding * 2
    crop_x = padding + (content_width - crop.width()) // 2
    top = crop.height() + padding * 2
    height = top
    rows = []
    footer_padding = round(20 * unit)
    heading_height = math.ceil(QFontMetricsF(font).height())
    if notes:
        height += footer_padding + heading_height + gap
        for note in notes:
            check_cancel(cancel)
            document = comment_document(note['content'], content_width - radius * 2 - gap, font)
            row_height = max(radius * 2, math.ceil(document.size().height()))
            rows.append((document, height))
            height += row_height + gap
            if height > 30000 or width * height > 24_000_000:
                raise PdfError('区域内批注较多，图片过长，请缩小选区或分次导出。批注未被截断。')
        height += footer_padding - gap
    if width * height > 24_000_000:
        raise PdfError('区域内批注较多，图片过长，请缩小选区或分次导出。批注未被截断。')
    markers = comment_markers(notes, origin, scale, crop_x, padding, width, top, radius, marker_gap)
    image = QImage(width, height, QImage.Format.Format_RGB32)
    if image.isNull():
        raise PdfError('无法生成区域图片，请缩小选区后重试。')
    image.fill(QColor('white'))
    image.setDotsPerMeterX(round(200 / .0254))
    image.setDotsPerMeterY(round(200 / .0254))
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.drawImage(crop_x, padding, crop)
        painter.setFont(font)

        def badge(number, x, y):
            rect = QRectF(x - radius, y - radius, radius * 2, radius * 2)
            painter.setPen(QPen(QColor('white'), max(1, unit)))
            painter.setBrush(QColor('#DC2626'))
            painter.drawEllipse(rect)
            painter.setPen(QColor('white'))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(number))

        for number, (point, anchor) in enumerate(markers, 1):
            painter.setPen(QPen(QColor('white'), max(3, 4 * unit)))
            painter.drawLine(point, anchor)
            painter.setPen(QPen(QColor('#DC2626'), max(1, 1.2 * unit)))
            painter.drawLine(point, anchor)
            badge(number, point.x(), point.y())
        if notes:
            painter.fillRect(0, top, width, height - top, QColor('#F5F7F6'))
            painter.setPen(QPen(QColor('#DCE5DF'), 1))
            painter.drawLine(0, top, width, top)
            painter.setPen(QColor('#173C36'))
            heading = QFont(font)
            heading.setBold(True)
            painter.setFont(heading)
            painter.drawText(QRectF(padding, top + footer_padding, content_width, heading_height),
                             Qt.AlignmentFlag.AlignVCenter, f'批注说明 · {len(notes)} 条')
            painter.setFont(font)
            for number, (document, y) in enumerate(rows, 1):
                check_cancel(cancel)
                if number > 1:
                    painter.setPen(QPen(QColor('#E1E7E4'), 1))
                    painter.drawLine(QPointF(padding, y - gap / 2), QPointF(width - padding, y - gap / 2))
                badge(number, padding + radius, y + radius)
                painter.save()
                painter.translate(padding + radius * 2 + gap, y)
                document.drawContents(painter)
                painter.restore()
    finally:
        painter.end()
    check_cancel(cancel)
    progress(3, 3, '区域图片已生成')
    return image, len(notes)


def save_capture(image, target, expected):
    """Only replace a confirmed destination, after the whole PNG is encoded."""
    fd, temp = tempfile.mkstemp(prefix='.zhixu-capture-', suffix='.png', dir=Path(target).parent)
    os.close(fd)
    try:
        if not image.save(temp, 'PNG'):
            raise PdfError('图片保存失败，请检查保存位置和可用空间。')
        commit_file(temp, target, expected)
    finally:
        Path(temp).unlink(missing_ok=True)


class CapturePreview(QGraphicsView):
    zoom_changed = pyqtSignal(int)

    def __init__(self, image, parent=None):
        super().__init__(parent)
        self.mode = 'width'
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self.refit)
        scene = QGraphicsScene(self)
        self.setScene(scene)
        self.item = scene.addPixmap(QPixmap.fromImage(image))
        self.item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.setSceneRect(self.item.boundingRect().adjusted(-12, -12, 12, 12))
        self.setBackgroundBrush(QColor('#E9EEEC'))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setAccessibleName('原文与批注说明图片预览')
        self.setToolTip('Ctrl+滚轮缩放，拖动图片查看；复制和保存始终使用完整图片')

    def set_mode(self, mode):
        self.mode = mode
        self.refit(top=True)

    def refit(self, *, top=False):
        if self.mode is None:
            return
        anchor = self.mapToScene(self.viewport().rect().center())
        if self.mode == 'actual':
            factor = 1 / self.devicePixelRatioF()
        else:
            rect = self.sceneRect()
            factor = max(1, self.viewport().width() - 8) / rect.width()
            if self.mode == 'whole':
                factor = min(factor, max(1, self.viewport().height() - 8) / rect.height())
        self.resetTransform()
        self.scale(factor, factor)
        self.centerOn(self.item if top else anchor)
        if top and self.mode == 'width':
            self.verticalScrollBar().setValue(self.verticalScrollBar().minimum())
        self.zoom_changed.emit(round(factor * self.devicePixelRatioF() * 100))

    def zoom_by(self, factor):
        self.mode = None
        current = self.transform().m11()
        target = max(.05, min(4, current * self.devicePixelRatioF() * factor)) / self.devicePixelRatioF()
        self.scale(target / current, target / current)
        self.zoom_changed.emit(round(target * self.devicePixelRatioF() * 100))

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.angleDelta().y():
                self.zoom_by(1.2 if event.angleDelta().y() > 0 else 1 / 1.2)
            event.accept()
            return
        super().wheelEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_timer.start(0)

    def showEvent(self, event):
        super().showEvent(event)
        self.refit(top=True)


class CaptureDialog(QDialog):
    def __init__(self, host, image, count, page_index, source_path):
        super().__init__(host)
        self.host, self.image = host, image
        self.setWindowTitle('区域图片预览')
        available = host.screen().availableGeometry()
        self.setMinimumSize(640, 480)
        self.resize(min(1100, available.width() - 64), min(820, available.height() - 64))
        layout = QVBoxLayout(self)
        hint = QLabel(f'已整理 {count} 条有备注的批注，图片中的编号与下方说明对应。' if count else
                      '选区内没有带备注的批注，将导出原文区域。', self)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.preview = CapturePreview(image, self)
        zoom = QHBoxLayout()
        self.zoom_label = QLabel(self)
        self.zoom_label.setMinimumWidth(48)
        self.preview.zoom_changed.connect(lambda value: self.zoom_label.setText(f'{value}%'))
        for title, name, slot in (
                ('−', '缩小预览', lambda: self.preview.zoom_by(1 / 1.2)),
                ('+', '放大预览', lambda: self.preview.zoom_by(1.2)),
                ('100%', '按实际像素查看', lambda: self.preview.set_mode('actual')),
                ('适合宽度', '按窗口宽度查看', lambda: self.preview.set_mode('width')),
                ('完整图片', '查看完整图片', lambda: self.preview.set_mode('whole'))):
            button = QPushButton(title, self)
            button.setAutoDefault(False)
            button.setAccessibleName(name)
            button.setToolTip(name)
            button.clicked.connect(slot)
            zoom.addWidget(button)
        zoom.addWidget(self.zoom_label)
        zoom.addStretch()
        layout.addLayout(zoom)
        layout.addWidget(self.preview, 1)
        detail = QLabel(f'{image.width()} × {image.height()} 像素 · Ctrl+滚轮缩放，拖动查看', self)
        detail.setObjectName('hint')
        layout.addWidget(detail)
        self.feedback = QLabel('可复制后粘贴到聊天窗口，或保存为 PNG 图片。', self)
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        buttons = QHBoxLayout()
        copy = QPushButton('复制图片', self)
        copy.setDefault(True)
        copy.setToolTip('复制整张图片，包括下方所有批注说明（Ctrl+C）')
        copy.clicked.connect(self.copy)
        buttons.addWidget(copy)
        save = QPushButton('保存图片…', self)
        save.clicked.connect(self.save)
        buttons.addWidget(save)
        buttons.addStretch()
        reselect = QPushButton('重新框选', self)
        reselect.setAutoDefault(False)
        reselect.clicked.connect(self.accept)
        buttons.addWidget(reselect)
        close = QPushButton('关闭', self)
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.shortcut = QShortcut(QKeySequence.StandardKey.Copy, self)
        self.shortcut.activated.connect(self.copy)
        self.default_path = export_path(Path(source_path).with_name(
            f'{Path(source_path).stem}_第{page_index + 1}页_区域批注.png'))

    def copy(self):
        QApplication.clipboard().setImage(self.image)
        self.feedback.setText('已复制整张图片，可直接粘贴到聊天窗口。')

    def save(self):
        path, _ = QFileDialog.getSaveFileName(self, '保存区域图片', str(self.default_path),
                                            'PNG 图片 (*.png)',
                                            options=QFileDialog.Option.DontConfirmOverwrite)
        if not path:
            return
        if not path.lower().endswith('.png'):
            path += '.png'
        try:
            if self.host.store.is_source_destination(path):
                raise PdfError('不能覆盖已打开的原文件，请另选文件名。')
            expected = stamp(path)
            if expected is not None and QMessageBox.question(
                    self, '替换图片？', '保存位置已有同名图片，是否替换？',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            run_operation(self, '保存区域图片', lambda progress: save_capture(self.image, path, expected))
            self.default_path = path
            self.feedback.setText(f'已保存：{path}')
        except (PdfError, OSError) as exc:
            QMessageBox.warning(self, '图片未保存', str(exc))


class RegionCapture(QObject):
    def __init__(self, host):
        super().__init__(host)
        self.host, self.view = host, host.preview_view
        self.active = False
        self.start = self.overlay = self.ref = self.index = None
        self.workspace = self.generation = None

    def begin(self):
        if not self.host.ws.pages or not self.host._allow_operation('copy'):
            return
        tools = getattr(self.host, 'annotation_tools', None)
        if tools and tools.deactivate() is False:
            return
        if hasattr(self.host, 'image_tools'):
            self.host.image_tools.clear()
        self.host.reader_interaction.reset()
        self.host.text_selection.clear()
        self.cancel()
        self.host.stack.setCurrentIndex(0)
        self.active = True
        self.workspace = self.host.ws
        self.view.setFocus(Qt.FocusReason.OtherFocusReason)
        self.view.viewport().setCursor(Qt.CursorShape.CrossCursor)
        # Installed only during capture, ahead of text/image/hover event handlers.
        QApplication.instance().installEventFilter(self)
        self.host.statusBar().showMessage('在一页内拖动框选区域，松开后预览并复制或保存图片；Esc 取消。')

    def cancel(self):
        if self.overlay is not None and self.overlay.scene() is not None:
            self.overlay.scene().removeItem(self.overlay)
        self.start = self.overlay = self.ref = self.index = None
        self.workspace = self.generation = None
        if self.active:
            QApplication.instance().removeEventFilter(self)
            self.view.viewport().unsetCursor()
            self.host.statusBar().clearMessage()
        self.active = False

    def sync(self):
        if self.active and (self.workspace is not self.host.ws or not self.host.ws.pages or not self.view.isVisible()
                or (self.ref is not None and (self.index >= len(self.host.ws.pages)
                    or self.host.ws.pages[self.index].render_key != self.ref.render_key
                    or self.view.layout_generation != self.generation))):
            self.cancel()

    def eventFilter(self, watched, event):
        if not self.active:
            return False
        self.sync()
        if not self.active:
            return False
        kind = event.type()
        # Native clicks first reach QWidgetWindow (a QWindow), then the viewport.
        # Only a click delivered to another QWidget means the user left the canvas.
        if ((kind == QEvent.Type.MouseButtonPress and isinstance(watched, QWidget)
                and watched not in (self.view, self.view.viewport()))
                or (kind == QEvent.Type.WindowDeactivate and watched is self.host)):
            self.cancel()
            return False
        if watched in (self.view, self.view.viewport()):
            if kind == QEvent.Type.ShortcutOverride and (self.start is not None
                    or event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Space)):
                event.accept()
                return True
            if kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) and event.key() == Qt.Key.Key_Space:
                return True
            if kind == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self.cancel()
                self.host.statusBar().showMessage('已取消框选。', 3000)
                return True
            if kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) and self.start is not None:
                return True
            if kind == QEvent.Type.Hide:
                self.cancel()
                return False
        if watched is not self.view.viewport():
            return False
        if kind == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.RightButton:
                self.cancel()
            elif event.button() == Qt.MouseButton.LeftButton:
                point = self.view.mapToScene(event.position().toPoint())
                index = self.view.page_at_point(point)
                if 0 <= index < len(self.view.page_rects) and self.view.page_rects[index].contains(point):
                    self.host.go_to_page(index + 1, reveal=False)
                    self.index, self.ref = index, self.host.ws.pages[index]
                    self.generation = self.view.layout_generation
                    self.start = point
                    self.overlay = QGraphicsRectItem()
                    pen = QPen(QColor('#0F766E'), 1.5, Qt.PenStyle.DashLine)
                    pen.setCosmetic(True)
                    self.overlay.setPen(pen)
                    self.overlay.setBrush(QColor(15, 118, 110, 25))
                    self.overlay.setZValue(10)
                    self.overlay.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                    self.view.scene().addItem(self.overlay)
            return True
        if kind in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease):
            if self.start is not None:
                bounds = self.view.page_rects[self.index]
                point = self.view.mapToScene(event.position().toPoint())
                point = QPointF(max(bounds.left(), min(bounds.right(), point.x())),
                                max(bounds.top(), min(bounds.bottom(), point.y())))
                rect = QRectF(self.start, point).normalized()
                self.overlay.setRect(rect)
                if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                    ref, index = self.ref, self.index
                    local = rect.translated(-bounds.topLeft())
                    self.cancel()
                    if local.width() < 2 or local.height() < 2:
                        self.begin()
                        return True
                    region = (local.left(), local.top(), local.right(), local.bottom())
                    QTimer.singleShot(0, lambda: self.export(ref, index, region))
            return True
        return kind in (QEvent.Type.MouseButtonDblClick, QEvent.Type.ContextMenu, QEvent.Type.Wheel)

    def export(self, ref, index, region):
        try:
            if index >= len(self.host.ws.pages) or self.host.ws.pages[index].render_key != ref.render_key:
                raise PdfError('页面已改变，请重新框选。')
            image, count = run_operation(self.host, '生成区域图片', lambda progress, cancel:
                render_capture(self.host.store, ref, region, progress, cancel), cancellable=True)
            dialog = CaptureDialog(self.host, image, count, index, ref.source_path)
            try:
                reselect = dialog.exec() == QDialog.DialogCode.Accepted
            finally:
                dialog.deleteLater()
            if reselect:
                QTimer.singleShot(0, self.begin)
        except OperationCancelled:
            self.host.statusBar().showMessage('已取消生成区域图片。', 3500)
        except Exception as exc:
            QMessageBox.warning(self.host, '区域图片未生成', str(exc))
