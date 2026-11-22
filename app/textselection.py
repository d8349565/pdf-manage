"""Read-only text-flow selection for visible and invisible PDF text layers."""
from app.i18n import tr
from time import perf_counter

from PyQt6.QtCore import QEvent, QObject, QPointF, Qt
from PyQt6.QtGui import QColor, QPainterPath, QPen, QShortcut
from PyQt6.QtWidgets import QApplication, QGraphicsPathItem, QMenu

from app.rendering import RenderSignals


class TextSelection(QObject):
    def __init__(self, host):
        super().__init__(host)
        self.host, self.view = host, host.preview_view
        self.snapshot = None
        self.page_index = None
        self.key = None
        self.generation = None
        self.start = None
        self._dragging = False
        self.selecting_translation = False
        self.text = ''
        self.area = None
        self.endpoints = None
        self.overlay = None
        self._pending = None
        self._selection_serial = 0
        self.signals = RenderSignals(self)
        self.signals.done.connect(self._snapshot_ready, Qt.ConnectionType.QueuedConnection)
        self.shortcut = QShortcut(self.view)
        self.shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.shortcut.activated.connect(self.copy)
        self.shortcut.setEnabled(False)
        host.shortcut_manager.bind_shortcut('reader.copy_text', self.shortcut)
        self.translate_shortcut = QShortcut(self.view)
        self.translate_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.translate_shortcut.activated.connect(self.translate)
        self.translate_shortcut.setEnabled(False)
        host.shortcut_manager.bind_shortcut('reader.translate_selection', self.translate_shortcut)
        self.view.viewport().installEventFilter(self)
        self.view.installEventFilter(self)
        self.view.viewport().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.viewport().customContextMenuRequested.connect(self.menu)
        self.sync_shortcut_hint()

    def sync_shortcut_hint(self):
        manager = self.host.shortcut_manager
        drag = tr('Alt＋拖动页面') if manager.uses_reader_space() else tr('按住空格拖动页面（也可 Alt＋拖动）')
        self.view.setAccessibleDescription(tr('点击链接跳转；拖动选择文字；') + manager.hint('reader.copy_text', tr('复制文字')) + '；' + drag)

    def clear(self):
        self._pending = None
        self._selection_serial += 1
        if self.overlay is not None:
            self.view.scene().removeItem(self.overlay)
        self.overlay = None
        self.text, self.start, self.area, self.endpoints = '', None, None, None
        self._dragging = False
        self.selecting_translation = False
        self.snapshot, self.page_index, self.key = None, None, None
        self.shortcut.setEnabled(False)
        self.translate_shortcut.setEnabled(False)
        on_cleared = getattr(self.host, '_on_text_selection_cleared', None)
        if on_cleared is not None:
            on_cleared()

    def sync(self):
        if self._pending is not None and not self._pending_current(self._pending):
            self.clear()
        if self.page_index is not None and (
                self.page_index >= len(self.host.ws.pages)
                or self.host.ws.pages[self.page_index].render_key != self.key
                or self.view.layout_generation != self.generation
                or (self.selecting_translation and not self.host._translation.inline_layer.showing_translation(self.page_index))):
            self.clear()

    def active(self):
        tools = getattr(self.host, 'annotation_tools', None)
        translation = getattr(self.host, '_translation', None)
        return (getattr(self.host, '_task_dialog', None) is None
                and not (translation and translation.region_active)
                and getattr(self.host, '_reading_tool', 'select') == 'select'
                and not (tools and tools.isVisible() and tools.tool.currentData() != 'browse'))

    def eventFilter(self, watched, event):
        self.sync()
        if not self.active():
            if self._pending is not None:
                self.clear()
            return False
        if (event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape
                and (self.text or self.start is not None or self._pending is not None)):
            self.clear()
            return True
        if watched is not self.view.viewport() or not self.host.ws.pages:
            return False
        word = event.type() == QEvent.Type.MouseButtonDblClick
        if (event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick)
                and event.button() == Qt.MouseButton.LeftButton):
            if word and self.host.reader_interaction.panning:
                return False
            self.clear()
            if event.modifiers() & Qt.KeyboardModifier.AltModifier:
                return False
            point = self.view.mapToScene(event.position().toPoint())
            index = self.view.page_at_point(point)
            if not 0 <= index < len(self.view.page_rects) or not self.view.page_rects[index].contains(point):
                return False
            ref = self.host.ws.pages[index]
            reason = self.host.store.operation_reason([ref], 'copy')
            if reason:
                self.host.statusBar().showMessage(reason, 5000)
                return False
            if index != self.host.ws.preview_index():
                self.host.go_to_page(index + 1, reveal=False)
            generation = self.view.layout_generation
            snapshot = (self.host.store.cached_text_snapshot(ref, include_tables=True)
                        or self.host.store.cached_text_snapshot(ref, include_tables=False))
            translation = getattr(self.host, '_translation', None)
            displayed = (translation is not None and not translation.panel.selection_btn.isChecked()
                         and translation.inline_layer.showing_translation(index))
            if translation and (snapshot is None or not any(b.get('lines') for b in snapshot['raw'].get('blocks', ()))):
                snapshot = translation.ocr_selection_snapshot(ref) or snapshot
            if snapshot is not None:
                return self._begin(snapshot, ref, index, point, generation, displayed, word=word)
            key = ('text_selection', self._selection_serial, *ref.render_key)
            pending = dict(key=key, pages=self.host.ws.pages, ref=ref, index=index,
                           generation=generation, start=point, end=point, released=word,
                           started=perf_counter(), displayed=displayed, word=word)
            self._pending = pending
            self.view.setFocus(Qt.FocusReason.MouseFocusReason)
            self.host.statusBar().showMessage(tr('正在准备当前页文字…'))
            self.host.rendering.request(ref, None, key, self.signals,
                lambda: self._pending is not pending or not self._pending_current(pending), 50, kind='text')
            return True
        if self._pending is not None and event.type() in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease):
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() != Qt.MouseButton.LeftButton:
                return False
            if not self._pending['released']:
                self._pending['end'] = self.view.mapToScene(event.position().toPoint())
                self._pending['released'] = event.type() == QEvent.Type.MouseButtonRelease
            return True
        if self.start is not None and event.type() in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease):
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() != Qt.MouseButton.LeftButton:
                return False
            self.select(self.view.mapToScene(event.position().toPoint()))
            if event.type() == QEvent.Type.MouseButtonRelease:
                self._finish()
            return True
        return False

    def _pending_current(self, pending):
        index, pages = pending['index'], self.host.ws.pages
        return (pages is pending['pages'] and index < len(pages) and pages[index] is pending['ref']
                and self.host.ws.preview_index() == index
                and self.view.layout_generation == pending['generation'] and self.active())

    def _begin(self, snapshot, ref, index, point, generation, displayed, *, word=False):
        if displayed and not self.host._translation.inline_layer.showing_translation(index):
            return False
        if not displayed and not any(block.get('lines') for block in snapshot['raw'].get('blocks', ())):
            return False
        self.selecting_translation = displayed
        self.snapshot, self.page_index, self.key = snapshot, index, ref.render_key
        self.generation, self.start = generation, point
        self.view.setFocus(Qt.FocusReason.MouseFocusReason)
        if word:
            self.select(point, word=True)
            self._finish()
        return True

    def _snapshot_ready(self, key, result):
        pending = self._pending
        if pending is None or pending['key'] != key:
            return
        self._pending = None
        if (not self._pending_current(pending) or result.cancelled
                or result.generation != self.host.rendering.generation):
            return
        self.host.rendering.diagnostics.record('text_selection_snapshot',
            duration_ms=round((perf_counter() - pending['started']) * 1000, 2), failed=bool(result.error))
        if result.error or result.value is None:
            self.host.statusBar().showMessage(tr('当前页文字暂时无法读取，请重试。'), 5000)
            return
        if not self._begin(result.value, pending['ref'], pending['index'], pending['start'],
                           pending['generation'], pending['displayed'], word=pending['word']):
            self.host.statusBar().showMessage(tr('当前页没有可选择的文字层。'), 5000)
            return
        if pending['word']:
            return
        self.select(pending['end'])
        if pending['released']:
            self._finish()
        else:
            self.host.statusBar().showMessage(tr('当前页文字已准备好，可继续拖动选择。'), 3500)

    def _finish(self):
        self.start = None
        if not self.text:
            self.clear()
            return
        if self.selecting_translation:
            self.host.statusBar().showMessage(self.host.shortcut_manager.hint('reader.copy_text', tr('复制译文')), 5000)
            return
        self.host.statusBar().showMessage(self.host.shortcut_manager.hint('reader.copy_text',
            tr('已选取 {p0} 字 · 可右键复制或翻译', p0=f'{len(self.text)}')), 5000)
        on_selected = getattr(self.host, '_on_text_selected', None)
        if on_selected is not None:
            on_selected(self.text, self.page_index, self.area)

    def select(self, point, *, word=False):
        import pymupdf as p
        from app.pdfedits import selection_range, selection_quads, selection_word
        if not self._dragging:
            if not word:
                distance = (self.view.mapFromScene(point) - self.view.mapFromScene(self.start)).manhattanLength()
                if distance < QApplication.startDragDistance():
                    return
                self._dragging = True
            translation = getattr(self.host, '_translation', None)
            if self.selecting_translation:
                self.snapshot = translation.inline_layer.selection_snapshot(self.page_index, self.snapshot)
            elif translation:
                translation.inline_layer.set_selecting_source(True)
        bounds = self.view.page_rects[self.page_index]
        point = QPointF(max(bounds.left(), min(bounds.right(), point.x())),
                        max(bounds.top(), min(bounds.bottom(), point.y())))
        matrix = p.Matrix(self.snapshot['matrix'])
        end = p.Point(point.x() - bounds.left(), point.y() - bounds.top()) * ~matrix
        tables = () if word else self.snapshot.get('tables', ())
        if word:
            raw, self.area = selection_word(self.snapshot['raw'], end)
        else:
            start = p.Point(self.start.x() - bounds.left(), self.start.y() - bounds.top()) * ~matrix
            self.endpoints = (start, end)
            raw, self.area = selection_range(self.snapshot['raw'], *self.endpoints, tables)
        from app.documentlayout import selection_text
        # Partial overlap with an inferred table must not reorder surrounding prose.
        tables = [table for table in tables if p.Rect(table.bbox).contains(self.area)]
        if self.selecting_translation:
            from app.translation.visibletext import excerpt
            self.text = excerpt(raw, self.area, tables)
        else:
            self.text = selection_text(raw, self.area, tables)
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.WindingFill)
        for quad in selection_quads(raw, self.area):
            points = [p.Point(quad[i]) * matrix for i in (0, 1, 3, 2)]
            path.moveTo(bounds.topLeft() + QPointF(*points[0]))
            for mapped in points[1:]:
                path.lineTo(bounds.topLeft() + QPointF(*mapped))
            path.closeSubpath()
        if self.overlay is None:
            self.overlay = QGraphicsPathItem()
            self.overlay.setZValue(6)
            self.overlay.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.overlay.setPen(QPen(Qt.PenStyle.NoPen))
            self.overlay.setBrush(QColor(20, 149, 135, 70))
            self.view.scene().addItem(self.overlay)
        self.overlay.setPath(path)
        self.shortcut.setEnabled(bool(self.text))
        self.translate_shortcut.setEnabled(bool(self.text) and not self.selecting_translation)

    def copy(self):
        self.sync()
        if not self.text or self.page_index is None:
            return
        if not self.host._allow_operation('copy'):
            return
        plain_fallback = False
        if self.endpoints is not None and ('tables' not in self.snapshot or
                (not self.snapshot['tables'] and self.snapshot.get('aligned_tables_checked') is False)):
            from app.progress import run_operation
            ref = self.host.ws.pages[self.page_index]
            try:
                snapshot = run_operation(self.host, '整理所选文字',
                    lambda progress: self.host.store.text_snapshot(ref, infer_aligned_tables=True))
            except Exception:
                plain_fallback = True
            else:
                self.sync()
                if not self.text or self.page_index is None:
                    return
                from app.documentlayout import selection_text
                from app.pdfedits import selection_range
                import pymupdf as p
                self.snapshot = snapshot
                raw, self.area = selection_range(snapshot['raw'], *self.endpoints, snapshot.get('tables', ()))
                tables = [table for table in snapshot.get('tables', ()) if p.Rect(table.bbox).contains(self.area)]
                self.text = selection_text(raw, self.area, tables)
        self.sync()
        if not self.text or self.page_index is None:
            return
        QApplication.clipboard().setText(self.text)
        self.host.statusBar().showMessage(
            tr('表格排版无法读取，已按普通文字复制。') if plain_fallback else tr('已复制 {p0} 字', p0=f'{len(self.text)}'), 5000 if plain_fallback else 3500)

    def translate(self):
        self.sync()
        if not self.text or self.page_index is None or self.selecting_translation:
            return
        if not self.host._allow_operation('copy'):
            return
        handler = getattr(self.host, 'translate_selected_text', None)
        if handler is not None:
            handler()

    def menu(self, position):
        self.sync()
        if not self.text or not self.active():
            return
        menu = QMenu(self.view)
        binding = self.host.shortcut_manager.label('reader.copy_text')
        copy = menu.addAction(tr('复制文字') + ('\t' + binding if binding else ''))
        copy.triggered.connect(self.copy)
        if hasattr(self.host, 'translate_selected_text') and not self.selecting_translation:
            t_binding = self.host.shortcut_manager.label('reader.translate_selection')
            translate = menu.addAction(tr('翻译所选文字') + ('\t' + t_binding if t_binding else ''))
            translate.triggered.connect(self.translate)
        clear = menu.addAction(tr('取消选择'))
        clear.triggered.connect(self.clear)
        menu.exec(self.view.viewport().mapToGlobal(position))
