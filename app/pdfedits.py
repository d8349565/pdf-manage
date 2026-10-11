"""Replay small immutable edits on private pages; never mutate input documents."""
from __future__ import annotations

from functools import lru_cache
import math

from app.workspace import PageEdit


@lru_cache(maxsize=1)
def cjk_font_buffer():
    import pymupdf
    return pymupdf.Font("cjk").buffer


def annotation_token(annot, ordinal):
    return annot.info.get("id") or f"zhixu-original-{ordinal}"


def snapshot_page(page):
    import pymupdf as p
    raw = page.get_text("rawdict", flags=p.TEXTFLAGS_RAWDICT & ~p.TEXT_PRESERVE_IMAGES)
    annotations = []
    for ordinal, annot in enumerate(page.annots() or ()):
        annotations.append(dict(id=annotation_token(annot, ordinal), kind=annot.type[0],
            rect=tuple(annot.rect), content=annot.info.get("content", ""),
            color=annot.colors.get("stroke") or (1, .8, 0), width=annot.border.get("width", 2)))
    return dict(raw=raw, annotations=annotations)


def selected_lines(raw, rectangle):
    import pymupdf as p
    area = p.Rect(rectangle)
    result = []
    for block in raw.get("blocks", ()):
        for line in block.get("lines", ()):
            chars = []
            for span in line.get("spans", ()):
                for char in span.get("chars", ()):
                    bbox = p.Rect(char["bbox"])
                    if area.contains((bbox.tl + bbox.br) / 2):
                        chars.append((char, span))
            if chars:
                result.append((line, chars))
    return result


def selection_quads(raw, rectangle):
    import pymupdf as p
    quads = []
    for line, chars in selected_lines(raw, rectangle):
        # recover_char_quad preserves slanted baselines and rotated glyphs.
        for char, span in chars:
            quad = p.recover_char_quad(line["dir"], span, char)
            quads.append(tuple(tuple(point) for point in quad))
    return tuple(quads)


def replacement_font(page, text, preferred=""):
    import pymupdf as p
    for xref, _, _, name, _, *_ in page.get_fonts():
        original_name = name.split("+")[-1]
        preferred_name = preferred.split("+")[-1]
        if original_name != preferred_name and not original_name.startswith(preferred_name):
            continue
        _, _, _, buffer = page.parent.extract_font(xref)
        if buffer:
            try:
                font = p.Font(fontbuffer=buffer)
                if all(font.has_glyph(ord(c)) for c in text):
                    return font, f"ZhiXuReplace{xref}", buffer
            except Exception:
                pass  # An unsupported source font may still use the built-in fallback.
    alias = p.Base14_fontdict.get(preferred.lower(), preferred) if preferred else "Helvetica"
    if alias in p.Base14_fontdict.values():
        font = p.Font(fontname=alias)
        if all(font.has_glyph(ord(c)) for c in text):
            return font, alias, None
    font = p.Font("cjk")
    if not all(font.has_glyph(ord(c)) for c in text):
        raise ValueError("内置字体缺少所需字形，请改用 Word 编辑。")
    return font, "ZhiXuCJK", cjk_font_buffer()


def prepare_replacement(page, rectangle, text, raw=None):
    import pymupdf as p
    if not text.strip() or "\n" in text or "\r" in text:
        raise ValueError("请输入一行非空文字。")
    raw = raw or snapshot_page(page)["raw"]
    lines = selected_lines(raw, rectangle)
    if len(lines) != 1 or tuple(lines[0][0]["dir"]) != (1.0, 0.0):
        raise ValueError("仅支持一行横排可见文字，请缩小选择范围；复杂内容可转 Word 编辑。")
    chars = lines[0][1]
    first = chars[0][1]
    if any((span["font"], span["size"], span["color"]) != (first["font"], first["size"], first["color"]) for _, span in chars):
        raise ValueError("这段文字包含不同样式，请分段修改。")
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
    if any(a.type[0] == p.PDF_ANNOT_REDACT for a in (page.annots() or ())):
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
    font, _, _ = replacement_font(page, text, first["font"])
    if font.text_length(text, fontsize=first["size"]) > area.width + .1:
        raise ValueError("新文字超出原文字宽度，请缩短文字或转 Word 编辑。")
    origin = tuple(chars[0][0]["origin"])
    if origin[1] - font.ascender * first["size"] < area.y0 - 1 or origin[1] - font.descender * first["size"] > area.y1 + 1:
        raise ValueError("替换字体超出原文字高度，请转 Word 编辑。")
    color = first["color"]
    rgb = tuple(((color >> shift) & 255) / 255 for shift in (16, 8, 0))
    return PageEdit("replace", tuple(area), text=text, color=rgb, details=(
        ("font", first["font"]), ("font_size", first["size"]), ("origin", origin),
        ("old_text", "".join(c["c"] for c, _ in chars)),
    ))


def apply_edits(page, edits):
    import pymupdf as p
    subset = False
    identifiers = set()
    for ordinal, annot in enumerate(page.annots() or ()):
        token = annotation_token(annot, ordinal)
        if token in identifiers:
            token = f"zhixu-original-{ordinal}"
        identifiers.add(token)
        page.parent.xref_set_key(annot.xref, "NM", p.get_pdf_str(token))
    for edit in edits:
        if not all(math.isfinite(float(v)) for v in (*edit.color, edit.width)) or len(edit.color) != 3 or not all(0 <= c <= 1 for c in edit.color) or not .1 <= edit.width <= 30:
            raise ValueError("批注颜色或线宽无效。")
        if edit.kind == "replace":
            details = dict(edit.details)
            # Recheck the private output before removing any text.
            prepared = prepare_replacement(page, edit.points, edit.text)
            if dict(prepared.details)["old_text"] != details["old_text"]:
                raise ValueError("选区文字已发生变化，未应用修正。")
            font, name, buffer = replacement_font(page, edit.text, details["font"])
            subset |= buffer is not None
            page.add_redact_annot(p.Rect(edit.points), fill=False, cross_out=False)
            page.apply_redactions(images=p.PDF_REDACT_IMAGE_NONE, graphics=p.PDF_REDACT_LINE_ART_NONE, text=p.PDF_REDACT_TEXT_REMOVE)
            page.insert_font(fontname=name, fontbuffer=buffer)
            page.insert_text(details["origin"], edit.text, fontname=name,
                             fontsize=details["font_size"], color=edit.color)
            continue
        if edit.kind in ("delete", "style", "note_text"):
            annot = next((a for a in (page.annots() or ()) if a.info.get("id") == edit.annotation_id), None)
            if annot is None:
                raise ValueError("要修改的批注已不存在。")
            if edit.kind == "delete":
                page.delete_annot(annot)
                continue
            if edit.kind == "note_text":
                annot.set_info(content=edit.text)
            else:
                annot.set_colors(stroke=edit.color)
                annot.set_border(width=edit.width)
            annot.update()
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
            annot.set_line_ends(p.PDF_ANNOT_LE_NONE, p.PDF_ANNOT_LE_OPEN_ARROW)
        else:
            raise ValueError("不支持的编辑类型。")
        annot.set_colors(stroke=edit.color)
        annot.set_border(width=edit.width)
        annot.set_opacity(.35 if edit.kind == "highlight" else 1)
        if edit.annotation_id:
            page.parent.xref_set_key(annot.xref, "NM", p.get_pdf_str(edit.annotation_id))
        annot.update()
    return subset


def apply_outline(doc, pages, entries, warnings):
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
