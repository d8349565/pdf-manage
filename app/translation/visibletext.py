"""Read what the translated page actually paints using its Qt text layout."""
from dataclasses import replace
from itertools import groupby

import pymupdf as pdf
from PyQt6.QtCore import QRectF, QTextBoundaryFinder
from PyQt6.QtGui import QTextLine

from app.documentlayout import selection_text
from app.pdfedits import selected_lines


def _translated_lines(item, page_origin):
    doc = item._doc
    origin = item.text_origin() - page_origin
    clip = item._text_rect.translated(-page_origin)
    block = doc.begin()
    while block.isValid():
        layout = block.layout()
        base = origin + layout.position()
        text = block.text()
        encoded = text.encode('utf-16-le')
        # Qt cursor positions use UTF-16 offsets. Keep combined characters together.
        boundaries = QTextBoundaryFinder(QTextBoundaryFinder.BoundaryType.Grapheme, text)
        for index in range(layout.lineCount()):
            line = layout.lineAt(index)
            start = line.textStart()
            end = start + line.textLength()
            chars = []
            bounds = QRectF()
            boundaries.setPosition(start)
            while start < end:
                stop = boundaries.toNextBoundary()
                if stop <= start:
                    break
                x0 = base.x() + line.cursorToX(start)[0]
                x1 = base.x() + line.cursorToX(start, QTextLine.Edge.Trailing)[0]
                box = QRectF(min(x0, x1), base.y() + line.y(), abs(x0 - x1), line.height()).intersected(clip)
                if not box.isEmpty():
                    chars.append(dict(c=encoded[start*2:stop*2].decode('utf-16-le'),
                                      bbox=box.getCoords(), carets=(x0, x1), offsets=(start, stop)))
                    bounds = bounds.united(box)
                start = stop
            if chars:
                yield dict(bbox=bounds.getCoords(), dir=(1., 0.), inline_id=item.segment.segment_id,
                           paragraph=block.blockNumber(), utf16=encoded,
                           spans=[dict(size=doc.defaultFont().pixelSize(), chars=chars)])
        block = block.next()


def visible_snapshot(source, items, page_origin, unchanged):
    """Combine actual translated line boxes with only the still-visible PDF/OCR glyphs."""
    matrix = pdf.Matrix(source['matrix'])
    identity = matrix == pdf.Identity
    masks = [pdf.Rect(item._rect.translated(-page_origin).getCoords()) for item in items]
    lines = []
    for block in source['raw'].get('blocks', ()):
        for line in block.get('lines', ()):
            box = pdf.Rect(line['bbox']) * matrix
            nearby = [mask for mask in masks if mask.intersects(box)]
            if any(mask.contains(box) for mask in nearby):
                continue
            if identity and not nearby:
                lines.append(line)
                continue
            spans = []
            remaining = pdf.Rect()
            for span in line['spans']:
                chars = []
                span_box = pdf.Rect()
                for char in span['chars']:
                    rect = pdf.Rect(char['bbox']) * matrix
                    center = (rect.tl + rect.br) / 2
                    if not any(mask.contains(center) for mask in nearby):
                        chars.append(dict(c=char['c'], bbox=tuple(rect)))
                        span_box |= rect
                if chars:
                    spans.append(dict(span, bbox=tuple(span_box), chars=chars))
                    remaining |= span_box
            if spans:
                direction = pdf.Point(line['dir']) * matrix - pdf.Point(0, 0) * matrix
                lines.append(dict(line, bbox=tuple(remaining), dir=tuple(direction), spans=spans))
    for item in items:
        lines.extend(_translated_lines(item, page_origin))

    tables = source.get('tables', ())
    if not identity:
        tables = tuple(replace(table, bbox=tuple(pdf.Rect(table.bbox) * matrix),
                               cells=[replace(cell, bbox=tuple(pdf.Rect(cell.bbox) * matrix)) for cell in table.cells if cell.bbox])
                       for table in tables)
    return dict(raw=dict(blocks=[dict(type=0, lines=lines)], inline_view=True), tables=tables,
                matrix=(1, 0, 0, 1, 0, 0), unchanged=tuple(pdf.Rect(box) for box in unchanged))


def excerpt(raw, area, tables, *, cached_only=False, unchanged=()):
    lines = selected_lines(raw, area)
    if not lines:
        return ''
    if cached_only:
        for line, chars in lines:
            if 'inline_id' not in line:
                for char, _ in chars:
                    box = pdf.Rect(char['bbox'])
                    if not any(rect.contains((box.tl + box.br) / 2) for rect in unchanged):
                        return ''
    first_id = lines[0][0].get('inline_id')
    if first_id and all(line.get('inline_id') == first_id for line, _ in lines):
        paragraphs = []
        for _, group in groupby(lines, key=lambda entry: entry[0]['paragraph']):
            pieces = []
            previous = None
            for line, chars in group:
                for char, _ in chars:
                    start, stop = char['offsets']
                    if previous is not None and start > previous:
                        gap = line['utf16'][previous*2:start*2].decode('utf-16-le')
                        # Preserve soft-wrap spaces. A marquee can omit an actual word.
                        pieces.append(gap if gap.isspace() else '\n')
                    pieces.append(char['c'])
                    previous = stop
            paragraphs.append(''.join(pieces))
        return '\n'.join(paragraphs)
    return selection_text(raw, area, [table for table in tables if pdf.Rect(table.bbox).contains(area)])
