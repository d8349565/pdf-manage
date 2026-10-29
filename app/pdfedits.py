"""Replay small immutable edits on private pages; never mutate input documents."""
from __future__ import annotations

from difflib import SequenceMatcher
from functools import lru_cache
from itertools import groupby
import math

from app.workspace import PageEdit


@lru_cache(maxsize=1)
def cjk_font_buffer():
    import pymupdf
    return pymupdf.Font("cjk").buffer


def annotation_token(annot, ordinal):
    return annot.info.get("id") or f"zhixu-original-{ordinal}"


def iter_annotations(page):
    """Walk once; Page.annots() reloads each xref by searching from the start."""
    import pymupdf as p
    annot = page.first_annot
    while annot is not None:
        following = annot.next
        if annot.type[0] not in (p.PDF_ANNOT_LINK, p.PDF_ANNOT_POPUP, p.PDF_ANNOT_WIDGET):
            yield annot
        annot = following


def annotation_summary(page):
    from app.pdfmarks import text_settings, is_watermark
    from app.pdfimages import image_settings
    annotations = []
    for ordinal, annot in enumerate(iter_annotations(page)):
        annotations.append(dict(id=annotation_token(annot, ordinal), kind=annot.type[0],
            rect=tuple(annot.rect), content=annot.info.get("content", ""),
            color=annot.colors.get("stroke") or (1, .8, 0), width=annot.border.get("width", 2),
            text_settings=text_settings(page, annot) if annot.type[0] == 2 else None,
            watermark=is_watermark(page, annot) or page.parent.xref_get_key(annot.xref, 'ZhiXuSeam')[0] != 'null',
            image_settings=image_settings(page, annot)))
    return annotations


def snapshot_page(page):
    import pymupdf as p
    raw = page.get_text("rawdict", flags=p.TEXTFLAGS_RAWDICT & ~p.TEXT_PRESERVE_IMAGES)
    return dict(raw=raw, annotations=annotation_summary(page))


def selected_lines(raw, rectangle):
    import pymupdf as p
    area = p.Rect(rectangle)
    result = []
    for block in raw.get("blocks", ()):
        if block.get("bbox") is not None and not area.intersects(p.Rect(block["bbox"])):
            continue
        for line in block.get("lines", ()):
            if line.get("bbox") is not None and not area.intersects(p.Rect(line["bbox"])):
                continue
            chars = []
            for span in line.get("spans", ()):
                for char in span.get("chars", ()):
                    bbox = p.Rect(char["bbox"])
                    if area.contains((bbox.tl + bbox.br) / 2):
                        chars.append((char, span))
            if chars:
                result.append((line, chars))
    return result


def selection_range(raw, start, end):
    """Keep characters between two carets, including complete intermediate lines."""
    import pymupdf as p
    entries = [(line, [(char, span) for span in line.get('spans', ()) for char in span.get('chars', ())])
               for block in raw.get('blocks', ()) for line in block.get('lines', ())]
    entries = sorted((item for item in entries if item[1]),
                     key=lambda item: (item[0]['bbox'][1], item[0]['bbox'][0]))
    if not entries:
        return dict(raw, blocks=[]), p.Rect()

    def caret(point):
        point = p.Point(point)
        def distance(item):
            box = p.Rect(item[0]['bbox'])
            return (max(box.y0 - point.y, 0, point.y - box.y1),
                    max(box.x0 - point.x, 0, point.x - box.x1))
        index = min(range(len(entries)), key=lambda slot: distance(entries[slot]))
        line, chars = entries[index]
        dx, dy = line.get('dir', (1, 0))
        offset = 0
        for char, _ in chars:
            box = p.Rect(char['bbox'])
            center = (box.tl + box.br) / 2
            offset += (center.x - point.x) * dx + (center.y - point.y) * dy < 0
        return index, offset

    first, last = sorted((caret(start), caret(end)))
    lines, area = [], p.Rect()
    for index in range(first[0], last[0] + 1):
        line, chars = entries[index]
        chars = chars[first[1] if index == first[0] else 0:last[1] if index == last[0] else len(chars)]
        if not chars:
            continue
        spans = []
        for _, pairs in groupby(chars, key=lambda item: id(item[1])):
            group = list(pairs)
            spans.append(dict(group[0][1], chars=[char for char, _ in group]))
        lines.append(dict(line, spans=spans))
        for char, _ in chars:
            area |= p.Rect(char['bbox'])
    return dict(raw, blocks=[dict(type=0, lines=lines)]), area


def selection_quads(raw, rectangle):
    import pymupdf as p
    quads = []
    for line, chars in selected_lines(raw, rectangle):
        # One continuous band per line avoids overlapping round caps per glyph.
        spans = []
        for _, group in groupby(chars, key=lambda item: id(item[1])):
            selected = list(group)
            span = selected[0][1]
            quad = p.recover_span_quad(line["dir"], span, [item[0] for item in selected])
            spans.append(dict(span, bbox=tuple(quad.rect)))
        quad = p.recover_line_quad(line, spans=spans)
        quads.append(tuple(tuple(point) for point in quad))
    return tuple(quads)


def arrow_geometry(start, end, width):
    """Return the compact tip and shaft end in PDF points for preview and output."""
    import pymupdf as p
    start, end = p.Point(start), p.Point(end)
    length = abs(end - start)
    if length < .01:
        raise ValueError("请拖动绘制箭头。")
    direction = (end - start) / length
    normal = p.Point(-direction.y, direction.x)
    head = min(max(5, width * 3), length * .35)
    base = end - direction * head
    return base + normal * (head * .38), base - normal * (head * .38), end - direction * (head * .6)


def update_arrow_appearance(page, annot):
    """Keep a native line annotation, with a smaller, narrower filled tip."""
    import pymupdf as p
    if annot.type[0] != p.PDF_ANNOT_LINE or annot.line_ends not in (
            (p.PDF_ANNOT_LE_NONE, p.PDF_ANNOT_LE_OPEN_ARROW),
            (p.PDF_ANNOT_LE_NONE, p.PDF_ANNOT_LE_CLOSED_ARROW)):
        return
    start, end = annot.vertices
    width = annot.border["width"] if annot.border["width"] > 0 else 1
    left, right, shaft_end = arrow_geometry(start, end, width)
    points = [p.Point(point) * ~page.transformation_matrix for point in (start, end, left, right, shaft_end)]
    start, end, left, right, shaft_end = [f"{point.x:.4f} {point.y:.4f}" for point in points]
    color = " ".join(f"{value:.4f}" for value in (annot.colors["stroke"] or (0, 0, 0)))
    xref = int(page.parent.xref_get_key(annot.xref, "AP/N")[1].split()[0])
    opacity = "/H gs\n" if page.parent.xref_get_key(xref, "Resources/ExtGState/H")[0] != "null" else ""
    appearance = (f"q\n{opacity}1 J 1 j\n{width:.4f} w\n{color} RG\n{color} rg\n"
                  f"{start} m\n{shaft_end} l\nS\n{end} m\n{left} l\n{right} l\nh B\nQ\n")
    page.parent.update_stream(xref, appearance.encode("ascii"))


def replacement_font(page, text, preferred="", flags=0):
    import pymupdf as p
    from app.systemfonts import installed_font_buffer, font_face
    _, wanted_bold, wanted_italic = font_face(preferred, flags)
    # insert_font reuses an existing resource name even when its font buffer
    # differs. A saved subset may contain none of the replacement's glyphs.
    resources = {item[4] for item in page.get_fonts()}

    def unused_name(base):
        name, suffix = base, 1
        while name in resources:
            name = f"{base}_{suffix}"
            suffix += 1
        return name

    for xref, _, _, name, _, *_ in page.get_fonts():
        original_name = name.split("+")[-1]
        preferred_name = preferred.split("+")[-1]
        if original_name != preferred_name and not original_name.startswith(preferred_name):
            continue
        _, _, _, buffer = page.parent.extract_font(xref)
        if buffer:
            try:
                font = p.Font(fontbuffer=buffer)
                if (bool(font.is_bold) == wanted_bold and bool(font.is_italic) == wanted_italic
                        and all(font.has_glyph(ord(c)) for c in text)):
                    return font, unused_name(f"ZhiXuReplace{xref}"), buffer
            except Exception:
                pass  # An unsupported source font may still use the built-in fallback.
    alias = p.Base14_fontdict.get(preferred.lower(), preferred) if preferred else "Helvetica"
    if alias in p.Base14_fontdict.values():
        font = p.Font(fontname=alias)
        if all(font.has_glyph(ord(c)) for c in text):
            return font, alias, None
    # Saved subsets often omit the new characters. Prefer the same full installed
    # face before falling back to the built-in Chinese font.
    buffer = installed_font_buffer(preferred, flags)
    if buffer:
        try:
            font = p.Font(fontbuffer=buffer)
            _, bold, italic = font_face(preferred, flags)
            if (bool(font.is_bold) == bold and bool(font.is_italic) == italic
                    and all(font.has_glyph(ord(c)) for c in text)):
                return font, unused_name("ZhiXuSystem"), buffer
        except Exception:
            pass  # Unreadable or unsupported installed faces still use the safe fallback.
    font = p.Font("cjk")
    if not all(font.has_glyph(ord(c)) for c in text):
        raise ValueError("内置字体缺少所需字形，请改用 Word 编辑。")
    return font, unused_name("ZhiXuCJK"), cjk_font_buffer()


def available_right_boundary(page, raw, area, selected):
    import pymupdf as p
    max_right = page.rect.x1 - 12
    y_min = area.y0 - 2
    y_max = area.y1 + 2

    # 1. 检查右侧其他文字
    for block in raw.get("blocks", ()):
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                for char in span.get("chars", ()):
                    bbox = p.Rect(char["bbox"])
                    if bbox.y1 > y_min and bbox.y0 < y_max:
                        if (tuple(char["origin"]), char["c"]) not in selected:
                            if bbox.x0 >= area.x1 - 1 and bbox.x0 < max_right:
                                max_right = bbox.x0 - 3

    # 2. 检查右侧图片
    for info in page.get_image_info():
        ibox = p.Rect(info["bbox"])
        if ibox.y1 > y_min and ibox.y0 < y_max and ibox.x0 >= area.x1 - 1:
            if ibox.x0 < max_right:
                max_right = ibox.x0 - 3

    # 3. 检查右侧垂直边框或表格线
    for drawing in page.get_drawings():
        d_rect = p.Rect(drawing.get("rect", (0, 0, 0, 0)))
        if d_rect.y1 > y_min and d_rect.y0 < y_max:
            for item in drawing.get("items", ()):
                kind = item[0]
                if kind == "l":
                    p1, p2 = item[1], item[2]
                    if abs(p1.x - p2.x) < 2 and min(p1.y, p2.y) <= y_max and max(p1.y, p2.y) >= y_min:
                        line_x = (p1.x + p2.x) / 2
                        if line_x >= area.x1 - 0.5 and line_x < max_right:
                            max_right = line_x - 3
                elif kind == "re":
                    r = item[1]
                    if r.y1 >= y_min and r.y0 <= y_max:
                        if r.x1 >= area.x1 - 0.5 and r.x1 < max_right:
                            max_right = r.x1 - 3
                        if r.x0 >= area.x1 - 0.5 and r.x0 < max_right:
                            max_right = r.x0 - 3

    return max(area.x1, max_right)


def _replacement_runs(chars, text):
    """Match unchanged characters; new characters inherit a nearby source style."""
    original = ''.join(char['c'] for char, _ in chars)
    indices = []
    for tag, i, j, start, end in SequenceMatcher(None, original, text, autojunk=False).get_opcodes():
        if tag == 'equal':
            indices.extend(range(i, j))
        elif tag == 'insert':
            indices.extend([max(0, i - 1)] * (end - start))
        elif tag == 'replace':
            indices.extend(i + offset * (j - i) // (end - start) for offset in range(end - start))
    previous = None
    for _, group in groupby(zip(text, indices), key=lambda item: (
            id(chars[item[1]][1]), chars[item[1]][0]['origin'][1])):
        items = list(group)
        first, last = items[0][1], items[-1][1]
        char, span = chars[first]
        gap = 0
        if previous is not None and first == previous + 1:
            gap = max(0, char['origin'][0] - chars[previous][0]['bbox'][2])
        yield ''.join(item[0] for item in items), span, char['origin'][1], gap
        previous = last


def _styled_replacement_span(span, style):
    from app.systemfonts import font_face
    result = dict(span)
    if any(key in style for key in ('font', 'bold', 'italic')):
        family, bold, italic = font_face(span['font'], span.get('flags', 0))
        family = style.get('font', family)
        bold, italic = style.get('bold', bold), style.get('italic', italic)
        # PDF's standard families have their own italic face names.
        family = 'Times' if family == 'Times-Roman' else family
        suffix = ('Bold' if bold else '') + (('Oblique' if family in ('Helvetica', 'Courier') else 'Italic') if italic else '')
        result['font'] = family + ('-' + suffix if suffix else '-Roman' if family == 'Times' else '')
        result['flags'] = (span.get('flags', 0) & ~18) | (16 if bold else 0) | (2 if italic else 0)
    result['size'] = style.get('fontsize', span['size'])
    return result


def prepare_replacement(page, rectangle, text, raw=None, *, style=None):
    import pymupdf as p
    if not text.strip() or "\n" in text or "\r" in text:
        raise ValueError("请输入一行非空文字。")
    style = dict(style or {})
    if (set(style) - {'font', 'fontsize', 'bold', 'italic', 'color'}
            or 'font' in style and (not isinstance(style['font'], str) or not style['font'].strip() or len(style['font']) > 128)
            or 'fontsize' in style and (not isinstance(style['fontsize'], (int, float)) or not 6 <= style['fontsize'] <= 144)
            or any(key in style and not isinstance(style[key], bool) for key in ('bold', 'italic'))
            or 'color' in style and (not isinstance(style['color'], (tuple, list)) or len(style['color']) != 3
                or not all(isinstance(c, (int, float)) and 0 <= c <= 1 for c in style['color']))):
        raise ValueError('文字样式无效：请选择字体，字号须为 6～144 pt。')
    if 'color' in style:
        style['color'] = tuple(style['color'])
    raw = raw or snapshot_page(page)["raw"]
    lines = selected_lines(raw, rectangle)
    if len(lines) != 1 or tuple(lines[0][0]["dir"]) != (1.0, 0.0):
        raise ValueError("仅支持一行横排可见文字，请缩小选择范围；复杂内容可转 Word 编辑。")
    chars = lines[0][1]
    first = chars[0][1]
    mixed = any((span['font'], span['size'], span['color'], span.get('flags', 0), char['origin'][1]) !=
                (first['font'], first['size'], first['color'], first.get('flags', 0), chars[0][0]['origin'][1])
                for char, span in chars)
    area = p.Rect(chars[0][0]["bbox"])
    for char, _ in chars[1:]:
        area |= p.Rect(char["bbox"])
    selected = {(tuple(char["origin"]), char["c"]) for char, _ in chars}
    for block in raw.get("blocks", ()):
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                for char in span.get("chars", ()):
                    overlap = p.Rect(char["bbox"]) & area
                    if overlap.get_area() > .01 and (tuple(char["origin"]), char["c"]) not in selected:
                        raise ValueError("选区与其他文字重叠，不能安全修改。")
    if any(a.type[0] == p.PDF_ANNOT_REDACT for a in iter_annotations(page)):
        raise ValueError("该页存在待应用的涂黑批注，不能自动执行文字修正。")
    if any(area.intersects(p.Rect(info["bbox"])) for info in page.get_image_info()):
        raise ValueError("选区与图片重叠，扫描图片或复杂图文区域请转 Word 编辑。")
    if any(area.intersects(widget.rect) for widget in (page.widgets() or ())):
        raise ValueError("选区与表单重叠，不能安全修改。")
    if any(area.intersects(p.Rect(link["from"]) * page.derotation_matrix) or area.intersects(p.Rect(link["from"])) for link in page.get_links()):
        raise ValueError("选区与链接重叠，不能安全修改。")
    for span in page.get_texttrace():
        if span.get("type") == 3 or span.get("opacity", 1) == 0:
            if any(area.intersects(p.Rect(char[3])) for char in span.get("chars", ())):
                raise ValueError("选区含隐藏文字层，不能作为原文修正。")
    from app.systemfonts import font_face
    max_right = available_right_boundary(page, raw, area, selected)
    available_width = max(area.width, max_right - area.x0)
    segments = list(_replacement_runs(chars, text)) if mixed else [(text, first, chars[0][0]['origin'][1], 0)]
    if style:
        segments = [(value, _styled_replacement_span(span, style), baseline, gap)
                    for value, span, baseline, gap in segments]
    fonts = [replacement_font(page, value, span['font'], span.get('flags', 0)) for value, span, _, _ in segments]
    needed_width = sum(gap + font.text_length(value, fontsize=span['size'])
                       for (value, span, _, gap), (font, _, _) in zip(segments, fonts))
    scale = 1
    if needed_width > available_width + 0.5:
        scale = available_width / needed_width
        if style or scale < 0.85:
            raise ValueError(f"新文字超出可用空间宽度（可用约 {int(available_width)} pt，需要约 {int(needed_width)} pt），请缩短文字或转 Word 编辑。")
    runs, fallback = [], False
    x = chars[0][0]['origin'][0]
    for (value, span, baseline, gap), (font, font_name, _) in zip(segments, fonts):
        original_family = font_face(span['font'])[0].replace(' ', '').casefold()
        replacement_family = font_face(font.name)[0].replace(' ', '').casefold()
        fallback |= font_name.startswith('ZhiXuCJK') and original_family != replacement_family
        band = p.Rect(span['bbox']) if mixed else area
        target_size = span['size'] if style else min(
            span['size'] * scale,
            (baseline - band.y0 + 2) / font.ascender if font.ascender > 0 else span['size'],
            (band.y1 + 2 - baseline) / -font.descender if font.descender < 0 else span['size'],
        )
        if target_size < span['size'] * 0.85:
            raise ValueError("替换字体超出原文字高度，请转 Word 编辑。")
        x += gap * scale
        rgb = tuple(((span['color'] >> shift) & 255) / 255 for shift in (16, 8, 0))
        rgb = style.get('color', rgb)
        runs.append(tuple(dict(text=value, font=span['font'], font_size=target_size,
                               font_flags=span.get('flags', 0), origin=(x, baseline), color=rgb).items()))
        x += font.text_length(value, fontsize=target_size)
    head = dict(runs[0])
    details = (
        ('font', head['font']), ('font_size', head['font_size']), ('origin', head['origin']),
        ("old_text", "".join(c["c"] for c, _ in chars)),
        ('font_flags', head['font_flags']), ('font_fallback', fallback),
    )
    if style:
        # A user-selected larger face may extend beyond the old line's band.
        # Check its actual bounds before removing the old text.
        bounds = page.rect * page.derotation_matrix
        for run, (font, _, _) in zip(runs, fonts):
            run = dict(run)
            x, y = run['origin']
            box = p.Rect(x, y - font.ascender * run['font_size'],
                         x + font.text_length(run['text'], fontsize=run['font_size']),
                         y - font.descender * run['font_size'])
            if not bounds.contains(box):
                raise ValueError('所选字体或字号超出页面范围，请缩小字号。')
            for block in raw.get('blocks', ()):
                for line in block.get('lines', ()):
                    for span in line.get('spans', ()):
                        for char in span.get('chars', ()):
                            if ((tuple(char['origin']), char['c']) not in selected
                                    and (box & p.Rect(char['bbox'])).get_area() > .01):
                                raise ValueError('所选字体或字号与其他文字重叠，请缩小字号。')
            obstacles = [p.Rect(info['bbox']) for info in page.get_image_info()]
            obstacles += [widget.rect for widget in (page.widgets() or ())]
            obstacles += [p.Rect(link['from']) * page.derotation_matrix for link in page.get_links()]
            if any(box.intersects(obstacle) for obstacle in obstacles):
                raise ValueError('所选字体或字号与图片、表单或链接重叠，请缩小字号。')
        details += (('style', tuple(style.items())),)
    if mixed or style:
        details += (('runs', tuple(runs)),)
    return PageEdit('replace', tuple(area), text=text, color=head['color'], details=details)


def _write_replacement(page, edit, details):
    """Remove the old text and write the replacement; raise unless the write verifies."""
    import pymupdf as p
    runs = [dict(run) for run in details['runs']] if 'runs' in details else [dict(details, text=edit.text, color=edit.color)]
    resources, subset = [], False
    for run in runs:
        _, name, buffer = replacement_font(page, run['text'], run['font'], run.get('font_flags', 0))
        page.insert_font(fontname=name, fontbuffer=buffer)
        resources.append((name, buffer))
        subset |= buffer is not None
    page.add_redact_annot(p.Rect(edit.points), fill=False, cross_out=False)
    page.apply_redactions(images=p.PDF_REDACT_IMAGE_NONE, graphics=p.PDF_REDACT_LINE_ART_NONE, text=p.PDF_REDACT_TEXT_REMOVE)
    for run, (name, buffer) in zip(runs, resources):
        # Redaction drops unused font resources, including those reserved above.
        page.insert_font(fontname=name, fontbuffer=buffer)
        written = page.insert_text(run['origin'], run['text'], fontname=name,
                                   fontsize=run['font_size'], color=run['color'])
        traces = page.get_texttrace()
        # Some embedded fonts map the shared space glyph to U+00A0 on extraction.
        actual = ''.join(chr(char[0]) for char in traces[-1]['chars']) if traces else ''
        if (written != 1 or not traces or traces[-1]['type'] != 0
                or traces[-1]['opacity'] <= 0
                or actual.replace('\u00a0', ' ') != run['text'].replace('\u00a0', ' ')):
            raise ValueError("新文字未完整写入，本次修改未应用，原文已保留。请重新输入或转 Word 编辑。")
    return subset


def apply_edits(page, edits, *, transforms=None):
    import pymupdf as p
    subset = False
    identifiers = set()
    for ordinal, annot in enumerate(iter_annotations(page)):
        identifier = annot.info.get("id", "")
        token = identifier or f"zhixu-original-{ordinal}"
        if token in identifiers:
            token = f"zhixu-original-{ordinal}"
        identifiers.add(token)
        if token != identifier:
            page.parent.xref_set_key(annot.xref, "NM", p.get_pdf_str(token))
    for edit in edits:
        if edit.kind == 'color_cleanup':
            from app.colorcleanup import apply_cleanup
            apply_cleanup(page, edit)
            continue
        if edit.kind in ('seam_stamp', 'seam_remove'):
            from app.seamstamp import apply_seam
            apply_seam(page, edit)
            continue
        if edit.kind == 'page_geometry':
            from app.pagegeometry import apply_geometry
            matrix = apply_geometry(page, edit)
            if transforms is not None:
                transforms.append(matrix)
            continue
        if edit.kind == "mosaic":
            from app.mosaic import apply_mosaic
            apply_mosaic(page, edit)
            continue
        if edit.kind in ('image', 'image_update', 'image_delete'):
            from app.pdfimages import apply_image
            apply_image(page, edit)
            continue
        if not all(math.isfinite(float(v)) for v in (*edit.color, edit.width)) or len(edit.color) != 3 or not all(0 <= c <= 1 for c in edit.color) or not .1 <= edit.width <= 30:
            raise ValueError("批注颜色或线宽无效。")
        if edit.kind in ("freetext", "freetext_update"):
            from app.pdfmarks import add_text_box
            subset |= add_text_box(page, edit)
            continue
        if edit.kind in ("watermark", "watermark_update"):
            from app.pdfmarks import add_watermark
            add_watermark(page, edit)
            continue
        if edit.kind == "watermark_remove":
            from app.pdfmarks import remove_watermarks
            remove_watermarks(page, edit.annotation_id)
            continue
        if edit.kind == "replace":
            details = dict(edit.details)
            # Recheck the private output before removing any text.
            prepared = prepare_replacement(page, edit.points, edit.text, style=dict(details.get('style', ())))
            if dict(prepared.details)["old_text"] != details["old_text"]:
                raise ValueError("选区文字已发生变化，未应用修正。")
            # 先在草稿页完整演练一遍：写入校验失败绝不能以原文被删为代价。
            rehearsal = p.open()
            try:
                rehearsal.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
                _write_replacement(rehearsal[0], edit, details)
            finally:
                rehearsal.close()
            subset |= _write_replacement(page, edit, details)
            continue
        if edit.kind in ("delete", "style", "note_text", "move"):
            annot = next((a for a in iter_annotations(page) if a.info.get("id") == edit.annotation_id), None)
            if annot is None:
                raise ValueError("要修改的批注已不存在。")
            from app.pdfmarks import is_watermark
            from app.pdfimages import image_settings
            if image_settings(page, annot) is not None:
                raise ValueError('插入的图片请使用图片工具调整或删除。')
            if is_watermark(page, annot):
                raise ValueError("水印请在「组织 → 水印」中选择后调整或删除。")
            if edit.kind == "delete":
                page.delete_annot(annot)
                continue
            appearance = None
            if edit.kind == "move" and page.parent.xref_get_key(annot.xref, "ZhiXuText")[0] != "null":
                xref = int(page.parent.xref_get_key(annot.xref, "AP/N")[1].split()[0])
                appearance = (xref, page.parent.xref_object(xref), page.parent.xref_stream(xref))
            if edit.kind == "note_text":
                annot.set_info(content=edit.text)
            elif edit.kind == "move":
                new_pt = p.Point(edit.points[0])
                if not all(math.isfinite(v) for v in new_pt):
                    raise ValueError("批注位置无效。")
                r = annot.rect
                if annot.set_rect(p.Rect(new_pt.x, new_pt.y, new_pt.x + r.width, new_pt.y + r.height)) is False:
                    raise ValueError("当前批注无法移动，本次修改未应用。")
            else:
                annot.set_colors(stroke=edit.color)
                annot.set_border(width=edit.width)
                if annot.type[0] == p.PDF_ANNOT_LINE and annot.line_ends == (p.PDF_ANNOT_LE_NONE, p.PDF_ANNOT_LE_CLOSED_ARROW):
                    annot.set_colors(fill=edit.color)
            annot.update()
            if appearance is not None:
                xref, definition, stream = appearance
                page.parent.update_object(xref, definition)
                page.parent.update_stream(xref, stream)
                page.parent.xref_set_key(annot.xref, "AP/N", f"{xref} 0 R")
            update_arrow_appearance(page, annot)
            continue
        if edit.kind in ("highlight", "underline"):
            quads = [p.Quad(quad) for quad in edit.points]
            if not quads:
                raise ValueError("选区没有可高亮的文字。")
            annot = page.add_highlight_annot(quads) if edit.kind == "highlight" else page.add_underline_annot(quads)
        elif edit.kind == "note":
            annot = page.add_text_annot(edit.points[0], edit.text)
        elif edit.kind == "ink":
            if len(edit.points) < 2:
                raise ValueError("画笔至少需要两个不同的点。")
            annot = page.add_ink_annot([list(edit.points)])
        elif edit.kind in ("rect", "ellipse"):
            area = p.Rect(edit.points)
            if area.is_empty:
                raise ValueError("请拖出一个有效的标注范围。")
            annot = page.add_rect_annot(area) if edit.kind == "rect" else page.add_circle_annot(area)
        elif edit.kind == "arrow":
            annot = page.add_line_annot(*edit.points)
            annot.set_line_ends(p.PDF_ANNOT_LE_NONE, p.PDF_ANNOT_LE_CLOSED_ARROW)
            annot.set_colors(fill=edit.color)
        else:
            raise ValueError("不支持的编辑类型。")
        annot.set_colors(stroke=edit.color)
        if edit.kind not in ("highlight", "underline"):
            annot.set_border(width=edit.width)
        annot.set_opacity(.22 if edit.kind == "highlight" else 1)
        if edit.annotation_id:
            page.parent.xref_set_key(annot.xref, "NM", p.get_pdf_str(edit.annotation_id))
        annot.update()
        if edit.kind == "arrow":
            update_arrow_appearance(page, annot)
    return subset


def apply_outline(doc, pages, entries, warnings, *, transforms=None):
    import pymupdf as p
    targets = {ref.page_id: index for index, ref in reversed(list(enumerate(pages)))}
    rows, actions = [], []
    for entry in entries:
        index = targets.get(entry.target_id)
        destination = dict(entry.destination)
        kind = destination.get("kind", p.LINK_GOTO)
        if kind == p.LINK_GOTO and index is None:
            warnings.append(f"书签「{entry.title}」目标页已删除，保留标题并取消跳转。")
            kind = p.LINK_NONE
        if not destination and index is not None:
            rows.append([entry.level, entry.title, index + 1])
            actions.append(None)
        else:
            destination["kind"] = kind
            destination["page"] = index if index is not None else -1
            if "to" in destination:
                destination["to"] = p.Point(destination["to"])
                if transforms and index in transforms:
                    destination['to'] *= transforms[index]
            style = dict(destination)
            style.pop("to", None)
            style["kind"] = p.LINK_NONE
            rows.append([entry.level, entry.title, index + 1 if index is not None else -1, style])
            actions.append((index, destination))
    doc.set_toc(rows, collapse=0)
    for row, action in zip(doc.get_toc(False), actions):
        if action is None:
            continue
        index, destination = action
        serialized = p.utils.getDestStr(doc.page_xref(index) if index is not None else 0, destination)
        if serialized.startswith("/A"):
            doc.xref_set_key(row[3]["xref"], "A", serialized[2:])
