"""Temporary reader panning and cache-only text hit testing."""
from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import QApplication


class ReaderInteraction(QObject):
    def __init__(self, host):
        super().__init__(host)
        self.host, self.view = host, host.preview_view
        self.space = False
        self.origin = None
        self.previous_cursor = None
        self.view.viewport().setMouseTracking(True)
        QApplication.instance().installEventFilter(self)

    @property
    def panning(self):
        return self.space or self.origin is not None

    def reset(self):
        self.space = False
        self.origin = None
        if self.previous_cursor is not None:
            self.view.viewport().setCursor(self.previous_cursor)
        self.previous_cursor = None

    def busy_gesture(self, *, buttons=True):
        tools = getattr(self.host, 'annotation_tools', None)
        images = getattr(self.host, 'image_tools', None)
        return (bool(buttons and QApplication.mouseButtons()) or self.host.text_selection.start is not None
                or bool(tools and (tools.stroke or tools._move_state or tools._move_committing))
                or bool(images and images.drag))

    def text_cursor(self, position):
        tools = getattr(self.host, 'annotation_tools', None)
        images = getattr(self.host, 'image_tools', None)
        if ((tools and tools.isVisible() and tools.tool.currentData() != 'browse')
                or (images and images.engaged)):
            return
        point = self.view.mapToScene(position)
        index = self.view.page_at_point(point)
        cursor = Qt.CursorShape.ArrowCursor
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
        if kind == QEvent.Type.MouseMove and not event.buttons():
            self.text_cursor(event.position().toPoint())
        return False
