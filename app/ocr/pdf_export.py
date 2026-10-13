"""Fit invisible CJK text to OCR quadrilaterals, in unrotated page coordinates."""
import math
import pymupdf as p

from app.ocr.contracts import OCRError


def add_text_layer(page, lines, pixmap, dpi=200, *, protect_existing=True):
    font = p.Font('cjk')
    page.insert_font(fontname='ZhiXuOCR', fontbuffer=font.buffer)
    shape = page.new_shape()
    inverse = p.Matrix(72 / dpi, 72 / dpi) * page.derotation_matrix
    existing = [p.Rect(w[:4]) for w in page.get_text('words')] if protect_existing else []
    written = []
    for line in lines:
        if not line.text.strip():
            continue
        if (len(line.polygon) != 8 or not all(math.isfinite(v) for v in line.polygon)
                or '\n' in line.text or '\r' in line.text
                or type(line.word_box) is not bool
                or not math.isfinite(line.confidence) or not 0 <= line.confidence <= 1):
            raise OCRError('识别结果含无效文字或坐标，该页未写入。')
        points = [p.Point(line.polygon[i] + pixmap.x, line.polygon[i + 1] + pixmap.y) * inverse
                  for i in range(0, 8, 2)]
        tl, tr, br, bl = points
        # RapidOCR rotates tall text crops 90 degrees counterclockwise before recognition.
        # Restore that reading direction for scans whose pixels, rather than /Rotate, are rotated.
        # Word boxes already follow the reading direction; a narrow digit is not a rotated crop.
        if not line.word_box and abs(bl - tl) >= abs(tr - tl) * 1.5:
            tl, tr, br, bl = tr, br, bl, tl
        area = p.Quad(tl, tr, bl, br).rect
        bounds = page.rect * page.derotation_matrix
        if area.is_empty or not bounds.contains(area):
            raise OCRError('识别框超出页面，该页未写入。')
        if protect_existing and any((area & word).get_area() >= min(area.get_area(), word.get_area()) * .6
                                    for word in existing):
            continue
        width = font.text_length(line.text, fontsize=1)
        if width <= 0 or any(not font.has_glyph(ord(c)) for c in line.text if not c.isspace()):
            raise OCRError('识别文字包含当前字体不支持的字符，该页未写入。')
        horizontal = (tr - tl) / width
        vertical = (bl - tl) / (font.ascender - font.descender)
        # morph uses PDF's upward Y axis; page/OCR points use downward Y.
        matrix = p.Matrix(horizontal.x, -horizontal.y, -vertical.x, vertical.y, 0, 0)
        if abs(matrix.a * matrix.d - matrix.b * matrix.c) < 1e-6:
            raise OCRError('识别框无法映射到文字层，该页未写入。')
        origin = tl + vertical * font.ascender
        shape.insert_text(origin, line.text, fontname='ZhiXuOCR', fontsize=1,
                          morph=(origin, matrix), render_mode=3)
        written.append((line.text, area))
    if written:
        shape.commit()
    return written
