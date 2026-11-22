"""Full-page reading view for translations which exceed the original text boxes."""
from html import escape
from itertools import groupby

from PyQt6.QtCore import QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPalette, QTextCursor, QTextOption
from PyQt6.QtWidgets import QApplication, QFrame, QRubberBand, QTextBrowser

from app.i18n import tr


def _page_html(cached):
    def content(seg):
        value = escape(cached.translations[seg.segment_id]).replace('\n', '<br>')
        return f'<a name="{escape(seg.segment_id, quote=True)}">{value}</a>'

    parts = []
    for _, group in groupby(cached.extraction.segments, key=lambda s: s.table_id or s.segment_id):
        entries = list(group)
        first = entries[0]
        if not first.cell:
            tag = {'heading': 'h2', 'code': 'pre'}.get(first.role, 'p')
            parts.append(f'<{tag}>{content(first)}</{tag}>')
            continue
        parts.append('<table width="100%" border="1" cellspacing="0" cellpadding="4">')
        occupied = set()
        columns = max(seg.cell.col + seg.cell.col_span for seg in entries)
        cells = {(seg.cell.row, seg.cell.col): seg for seg in entries}
        for row in range(max(seg.cell.row + seg.cell.row_span for seg in entries)):
            parts.append('<tr>')
            for col in range(columns):
                if (row, col) in occupied:
                    continue
                seg = cells.get((row, col))
                if seg is None:
                    parts.append('<td></td>')
                    continue
                cell = seg.cell
                occupied.update((y, x) for y in range(row, row + cell.row_span)
                                for x in range(col, col + cell.col_span))
                tag = 'th' if cell.style.bold else 'td'
                parts.append(f'<{tag} rowspan="{cell.row_span}" colspan="{cell.col_span}">{content(seg)}</{tag}>')
            parts.append('</tr>')
        parts.append('</table>')
    return ''.join(parts)


class ReflowedPage(QTextBrowser):
    page_step = pyqtSignal(int)
    segment_clicked = pyqtSignal(str)
    region_selected = pyqtSignal(str)

    def __init__(self, view):
        super().__init__(view.viewport())
        self._view = view
        self.page = None
        self._anchors = []
        self._press = None
        self._region = None
        self.setReadOnly(True)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAccessibleName(tr('页内译文'))
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.document().setDocumentMargin(24)
        self.hide()

    def set_page(self, page):
        if self.page is page:
            return
        self.cancel_region()
        self.page = page
        self._anchors.clear()
        if page is None:
            self.clear()
            self.hide()
            return
        self.document().setDefaultStyleSheet('p {margin: 0 0 10px} pre {white-space: pre-wrap} h2 {margin: 16px 0 6px}')
        self.setHtml(_page_html(page))
        block = self.document().begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                names = fragment.charFormat().anchorNames()
                if names:
                    self._anchors.append((fragment.position(), names[0]))
                it += 1
            block = block.next()
        self.verticalScrollBar().setValue(0)

    def sync_geometry(self):
        self.setGeometry(self._view.viewport().rect())
        colors = getattr(self._view, 'reading_colors', None)
        paper, ink = (colors.paper, colors.ink) if colors and colors.enabled else ('#FFFFFF', '#1E293B')
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Base, QColor(paper))
        palette.setColor(QPalette.ColorRole.Text, QColor(ink))
        self.setPalette(palette)
        font = QFont('Microsoft YaHei UI')
        font.setPixelSize(max(9, round(12 * self._view.transform().m11())))
        if self.document().defaultFont() != font:
            self.document().setDefaultFont(font)

    @property
    def region_active(self):
        return self._region is not None

    def highlight_segments(self, ids, *, reveal=False):
        selections = []
        end = self.document().characterCount() - 1
        for index, (position, sid) in enumerate(self._anchors):
            if sid not in ids:
                continue
            selection = self.ExtraSelection()
            selection.cursor = QTextCursor(self.document())
            selection.cursor.setPosition(position)
            stop = self._anchors[index+1][0] if index+1 < len(self._anchors) else end
            selection.cursor.setPosition(stop, QTextCursor.MoveMode.KeepAnchor)
            tint = self.palette().color(QPalette.ColorRole.Highlight)
            tint.setAlpha(48)
            selection.format.setBackground(tint)
            selections.append(selection)
        self.setExtraSelections(selections)
        if reveal and ids:
            self.scrollToAnchor(ids[0])

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            QApplication.sendEvent(self._view.viewport(), event)
            return
        delta = event.angleDelta().y() or event.pixelDelta().y()
        bar = self.verticalScrollBar()
        if (delta < 0 and bar.value() == bar.maximum()) or (delta > 0 and bar.value() == bar.minimum()):
            self.page_step.emit(1 if delta < 0 else -1)
            event.accept()
        else:
            super().wheelEvent(event)

    def begin_region(self):
        self.cancel_region()
        self._region = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
        self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        self.setFocus()

    def cancel_region(self):
        if self._region is not None:
            self._region.hide()
            self._region.deleteLater()
            self._region = None
            self.viewport().unsetCursor()
        self._press = None

    def keyPressEvent(self, event):
        if self._region is not None and event.key() == Qt.Key.Key_Escape:
            self.cancel_region()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        self._press = event.position().toPoint() if event.button() == Qt.MouseButton.LeftButton else None
        if self._region is None:
            super().mousePressEvent(event)
        elif self._press is None:
            self.cancel_region()
        else:
            self._region.setGeometry(QRect(self._press, self._press))
            self._region.show()

    def mouseMoveEvent(self, event):
        if self._region is not None and self._press is not None:
            self._region.setGeometry(QRect(self._press, event.position().toPoint()).normalized())
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        end = event.position().toPoint()
        start = self._press
        if self._region is not None and start is not None:
            self.cancel_region()
            self.region_selected.emit(self._region_text(QRect(start, end).normalized()))
            return
        super().mouseReleaseEvent(event)
        self._press = None
        if start is not None and (end - start).manhattanLength() < QApplication.startDragDistance():
            cursor = self.cursorForPosition(end)
            for position, sid in reversed(self._anchors):
                if position <= cursor.position():
                    self.segment_clicked.emit(sid)
                    break

    def _region_text(self, rect):
        area = QRectF(rect.translated(self.horizontalScrollBar().value(), self.verticalScrollBar().value()))
        doc = self.document()
        texts = []
        block = doc.begin()
        while block.isValid():
            bounds = doc.documentLayout().blockBoundingRect(block)
            if bounds.intersects(area):
                pieces = []
                layout = block.layout()
                for i in range(layout.lineCount()):
                    line = layout.lineAt(i)
                    ink = line.naturalTextRect().translated(bounds.topLeft())
                    if ink.intersects(area):
                        start = line.xToCursor(area.left() - bounds.left())
                        end = line.xToCursor(area.right() - bounds.left())
                        cursor = QTextCursor(doc)
                        cursor.setPosition(block.position() + start)
                        cursor.setPosition(block.position() + end, QTextCursor.MoveMode.KeepAnchor)
                        pieces.append(cursor.selectedText())
                if pieces:
                    texts.append(''.join(pieces))
            block = block.next()
        return '\n'.join(texts).strip()
