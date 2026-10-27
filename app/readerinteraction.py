"""Reader panning, link activation and cache-only hover hit testing."""
import math

from PyQt6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QApplication

from app.rendering import RenderSignals


class ReaderInteraction(QObject):
    def __init__(self, host):
        super().__init__(host)
        self.host, self.view = host, host.preview_view
        self.space = False
        self.origin = None
        self.previous_cursor = None
        self.link_press = self.link_jump = None
        self._link_serial = 0
        self.signals = RenderSignals(self)
        self.signals.done.connect(self._metadata_ready, Qt.ConnectionType.QueuedConnection)
        self.view.viewport().setMouseTracking(True)
        QApplication.instance().installEventFilter(self)

    @property
    def panning(self):
        return self.space or self.origin is not None

    def reset(self):
        self.space = False
        self.origin = None
        self.link_press = self.link_jump = None
        if self.view.viewport().toolTip():
            self.view.viewport().setToolTip('')
        if self.previous_cursor is not None:
            self.view.viewport().setCursor(self.previous_cursor)
        self.previous_cursor = None

    def busy_gesture(self, *, buttons=True):
        tools = getattr(self.host, 'annotation_tools', None)
        images = getattr(self.host, 'image_tools', None)
        return (bool(buttons and QApplication.mouseButtons()) or self.host.text_selection.start is not None
                or bool(tools and (tools.stroke or tools._move_state or tools._move_committing))
                or bool(images and images.drag))

    def links_enabled(self):
        tools = getattr(self.host, 'annotation_tools', None)
        images = getattr(self.host, 'image_tools', None)
        capture = getattr(self.host, 'region_capture', None)
        return (not self.panning and not getattr(self.host, '_task_dialog', None)
                and not (tools and (tools.stroke or tools._move_state or tools._move_committing
                                    or (tools.isVisible() and tools.tool.currentData() != 'browse')))
                and not (images and images.engaged) and not (capture and capture.active))

    def link_at(self, position):
        point = self.view.mapToScene(position)
        index = self.view.page_at_point(point)
        if not 0 <= index < len(self.host.ws.pages):
            return None
        summary = self.host.store.cached_annotations(self.host.ws.pages[index])
        if summary:
            local = point - self.view.page_rects[index].topLeft()
            for link in summary.get('links', ()):
                x0, y0, x1, y1 = link['from']
                if x0 <= local.x() <= x1 and y0 <= local.y() <= y1:
                    return index, link
        return None

    def _current(self, pending):
        index, pages = pending['index'], self.host.ws.pages
        return (pages is pending['pages'] and index < len(pages) and pages[index] is pending['ref']
                and self.view.layout_generation == pending['generation'] and self.view.isVisible()
                and self.links_enabled()
                and (pending is self.link_press or (pending is self.link_jump
                     and self.host.ws.preview_index() == index)))

    def _request_metadata(self, pending):
        self._link_serial += 1
        pending['key'] = ('reader_link', self._link_serial, *pending['ref'].render_key)
        self.host.rendering.request(pending['ref'], None, pending['key'], self.signals,
                                    lambda: not self._current(pending), 60, kind='metadata')

    def _metadata_ready(self, key, result):
        for pending in (self.link_press, self.link_jump):
            if pending is None or pending.get('key') != key or not self._current(pending):
                continue
            if result.cancelled or result.generation != self.host.rendering.generation:
                return
            if result.error:
                self.link_press = self.link_jump = None
                self.host.statusBar().showMessage('链接暂时无法读取，请重试。', 5000)
            elif pending is self.link_jump:
                self._finish_jump(pending)
            elif pending['released']:
                self._finish_click(pending)
            return

    def _finish_click(self, pending):
        if not self._current(pending) or pending['dragged']:
            self.link_press = None
            return False
        if self.host.store.cached_annotations(pending['ref']) is None:
            return False
        hit = self.link_at(pending['position'])
        self.link_press = None
        if hit is None or hit != self.link_at(pending['end']):
            return False
        self.host.text_selection.clear()
        self._follow_link(pending['ref'], hit[1])
        return True

    def _follow_link(self, ref, link):
        import pymupdf as p
        if link.get('kind') == p.LINK_URI:
            url = QUrl(str(link.get('uri', '')).strip())
            allowed = (url.scheme().lower() in ('http', 'https') and bool(url.host())
                       or url.scheme().lower() == 'mailto' and bool(url.path()))
            if not url.isValid() or not allowed:
                self.host.statusBar().showMessage('此链接类型暂不支持打开。', 5000)
                return
            opened = QDesktopServices.openUrl(url)
            self.host.statusBar().showMessage('已交给默认应用打开链接。' if opened
                                             else '链接未能打开，请检查系统默认浏览器或邮件应用。', 5000)
        elif link.get('kind') == p.LINK_GOTO:
            matches = [i for i, target in enumerate(self.host.ws.pages)
                       if target.source_id == ref.source_id and target.source_page == link.get('page')]
            if not matches:
                self.host.statusBar().showMessage('链接指向的页面已不在当前文档中。', 5000)
                return
            index = next((i for i in matches if self.host.ws.pages[i].copy_group == ref.copy_group), matches[0])
            self.host.go_to_page(index + 1)
            pending = dict(index=index, ref=self.host.ws.pages[index], pages=self.host.ws.pages,
                           generation=self.view.layout_generation, point=link.get('to', (0, 0)))
            self.link_jump = pending
            if self.host.store.cached_annotations(pending['ref']) is None:
                self._request_metadata(pending)
            else:
                self._finish_jump(pending)
        else:
            self.host.statusBar().showMessage('此链接类型暂不支持打开。', 5000)

    def _finish_jump(self, pending):
        if not self._current(pending):
            return
        import pymupdf as p
        summary = self.host.store.cached_annotations(pending['ref'])
        self.link_jump = None
        point = pending['point']
        if (not isinstance(point, (tuple, list)) or len(point) != 2
                or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in point)):
            return
        mapped = p.Point(point) * p.Matrix(summary['source_pdf_matrix'])
        bounds = self.view.page_rects[pending['index']]
        position = bounds.topLeft() + QPointF(max(0, min(bounds.width(), mapped.x)),
                                             max(0, min(bounds.height(), mapped.y)))
        if not getattr(self.view, 'fit_page', False):
            self.view.ensureVisible(QRectF(position.x(), position.y(), 1, 1), 48, 72)

    def text_cursor(self, position):
        tools = getattr(self.host, 'annotation_tools', None)
        images = getattr(self.host, 'image_tools', None)
        if ((tools and tools.isVisible() and tools.tool.currentData() != 'browse')
                or (images and images.engaged)):
            return
        point = self.view.mapToScene(position)
        index = self.view.page_at_point(point)
        cursor = Qt.CursorShape.ArrowCursor
        hit = self.link_at(position) if self.links_enabled() else None
        self.view.viewport().setToolTip('' if hit is None else
            hit[1].get('uri') or ('跳转到文档内页面' if 'page' in hit[1] else '此链接类型暂不支持打开'))
        if hit is not None:
            self.view.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            return
        if 0 <= index < len(self.host.ws.pages) and index < len(self.view.page_rects):
            bounds = self.view.page_rects[index]
            ref = self.host.ws.pages[index]
            summary = self.host.store.cached_annotations(ref)
            if summary and bounds.contains(point) and not self.host.store.operation_reason([ref], 'copy'):
                local = point - bounds.topLeft()
                if any(x0 <= local.x() <= x1 and y0 <= local.y() <= y1
                       for x0, y0, x1, y1 in summary.get('text_boxes', ())):
                    cursor = Qt.CursorShape.IBeamCursor
        self.view.viewport().setCursor(cursor)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Type.WindowDeactivate and watched is self.host:
            self.reset()
        if watched not in (self.view, self.view.viewport()):
            return False
        if kind in (QEvent.Type.Hide, QEvent.Type.FocusOut):
            self.reset()
            return False
        if not self.view.isVisible() or not self.host.ws.pages or getattr(self.host, '_task_dialog', None):
            self.reset()
            return False
        if kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.ShortcutOverride):
            if kind == QEvent.Type.KeyPress:
                self.link_press = self.link_jump = None
            if event.key() != Qt.Key.Key_Space:
                return False
            # Only the canvas owns this shortcut; editors keep their normal spaces.
            if QApplication.focusWidget() not in (self.view, self.view.viewport()):
                return False
            if kind == QEvent.Type.ShortcutOverride:
                event.accept()
                return True
            if event.isAutoRepeat():
                return True
            if kind == QEvent.Type.KeyPress:
                if not self.panning and not self.busy_gesture():
                    self.previous_cursor = self.view.viewport().cursor()
                    self.space = True
                    self.view.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.space = False
                if self.origin is None:
                    self.reset()
            return True
        if watched is not self.view.viewport():
            return False
        if (kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton
                and (self.space or event.modifiers() & Qt.KeyboardModifier.AltModifier)
                and not self.busy_gesture(buttons=False)):
            if self.previous_cursor is None:
                self.previous_cursor = self.view.viewport().cursor()
            self.origin = (event.globalPosition(), self.view.horizontalScrollBar().value(),
                           self.view.verticalScrollBar().value())
            self.view.setFocus(Qt.FocusReason.MouseFocusReason)
            self.view.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            return True
        if self.origin is not None:
            if kind == QEvent.Type.MouseMove:
                delta = event.globalPosition() - self.origin[0]
                self.view.horizontalScrollBar().setValue(self.origin[1] - round(delta.x()))
                self.view.verticalScrollBar().setValue(self.origin[2] - round(delta.y()))
                return True
            if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                self.origin = None
                if self.space:
                    self.view.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
                else:
                    self.reset()
                return True
        if kind == QEvent.Type.MouseMove and self.space:
            self.view.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            return True
        if kind == QEvent.Type.Wheel:
            self.link_press = self.link_jump = None
        if kind == QEvent.Type.MouseButtonPress:
            self.link_press = self.link_jump = None
            if (event.button() == Qt.MouseButton.LeftButton and not event.modifiers()
                    and self.links_enabled()):
                position = event.position().toPoint()
                index = self.view.page_at_point(self.view.mapToScene(position))
                if 0 <= index < len(self.host.ws.pages):
                    ref = self.host.ws.pages[index]
                    if self.host.store.cached_annotations(ref) is None or self.link_at(position):
                        pending = dict(index=index, ref=ref, pages=self.host.ws.pages,
                                       generation=self.view.layout_generation, position=position,
                                       end=position, released=False, dragged=False)
                        self.link_press = pending
                        if self.host.store.cached_annotations(ref) is None:
                            self._request_metadata(pending)
        pending = self.link_press
        if (pending is not None and not pending['released']
                and kind in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease)):
            pending['end'] = event.position().toPoint()
            pending['dragged'] |= ((pending['end'] - pending['position']).manhattanLength()
                                   >= QApplication.startDragDistance())
            if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                pending['released'] = True
                if self._finish_click(pending):
                    return True
        if kind == QEvent.Type.MouseMove and not event.buttons():
            self.text_cursor(event.position().toPoint())
        return False
