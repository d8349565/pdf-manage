"""Vector appearances for editable text annotations and removable watermarks."""
import json
import math
import base64
import io
from functools import lru_cache

TEXT_SETTINGS = "ZhiXuText"
WATERMARK_PREFIX = "zhixu-watermark-"


def set_appearance(page, annot, document, opacity=1):
    """Import the form without leaving a second copy in page content."""
    import pymupdf as p
    parent = page.parent
    annot.set_flags(annot.flags | p.PDF_ANNOT_IS_PRINT)
    annot.update()
    contents = parent.xref_get_key(page.xref, "Contents")[1]
    try:
        xref = page.show_pdf_page(p.Rect(annot.rect), document, 0)
    finally:
        parent.xref_set_key(page.xref, "Contents", contents)
    if opacity < 1 and parent.xref_get_key(xref, "Resources/ExtGState/ZhiXuOpacity")[0] == "null":
        # Imported form dictionaries can be indirect. Clone each dictionary and
        # update its own xref rather than writing through an indirect key path.
        kind, value = parent.xref_get_key(xref, "Resources")
        resources = parent.get_new_xref()
        parent.update_object(resources, parent.xref_object(int(value.split()[0]))
                             if kind == "xref" else value if kind == "dict" else "<<>>")
        kind, value = parent.xref_get_key(resources, "ExtGState")
        states = parent.get_new_xref()
        parent.update_object(states, parent.xref_object(int(value.split()[0]))
                             if kind == "xref" else value if kind == "dict" else "<<>>")
        parent.xref_set_key(states, "ZhiXuOpacity", f"<< /Type /ExtGState /CA {opacity} /ca {opacity} >>")
        parent.xref_set_key(resources, "ExtGState", f"{states} 0 R")
        parent.xref_set_key(xref, "Resources", f"{resources} 0 R")
        stream = parent.xref_stream(xref)
        parent.update_stream(xref, b"q /ZhiXuOpacity gs\n" + stream + b"\nQ")
    parent.xref_set_key(annot.xref, "AP/N", f"{xref} 0 R")


def text_settings(page, annot):
    kind, value = page.parent.xref_get_key(annot.xref, TEXT_SETTINGS)
    if kind == "string":
        try:
            settings = json.loads(value)
            if (isinstance(settings, dict) and isinstance(settings.get("font"), str)
                    and len(settings["font"]) <= 128
                    and isinstance(settings.get("fontsize"), (int, float))
                    and 6 <= settings["fontsize"] <= 144
                    and isinstance(settings.get("color"), list) and len(settings["color"]) == 3
                    and all(isinstance(c, (int, float)) and 0 <= c <= 1 for c in settings["color"])):
                return settings
        except (ValueError, TypeError):
            pass
    return dict(font="Microsoft YaHei", fontsize=12, color=annot.colors.get("stroke") or (0, 0, 0))


def add_text_box(page, edit):
    rotation = page.rotation
    try:
        page.set_rotation(int(dict(edit.details).get("rotation", rotation)))
        return _add_text_box(page, edit)
    finally:
        page.set_rotation(rotation)


def _add_text_box(page, edit):
    import pymupdf as p
    from app.pdfedits import replacement_font
    settings = dict(edit.details)
    fontsize = float(settings.get("fontsize", 12))
    if not 6 <= fontsize <= 144 or not edit.text.strip() or len(edit.text) > 20000:
        raise ValueError("文本框请输入 1～20000 个字符，字号须为 6～144 pt。")
    area = p.Rect(edit.points)
    bounds = page.rect * page.derotation_matrix
    if area.is_empty or not all(math.isfinite(v) for v in area) or not bounds.contains(area):
        raise ValueError("文本框须放在页面内。")
    visible = area * page.rotation_matrix
    with p.open() as document:
        available = page.rect.y1 - visible.y0
        scratch = document.new_page(width=visible.width, height=available)
        glyphs = "".join(c for c in edit.text if c not in "\r\n\t")
        font, name, buffer = replacement_font(scratch, glyphs, settings.get("font", "Microsoft YaHei"))
        scratch.insert_font(fontname=name, fontbuffer=buffer)
        box = p.Rect(0, 0, visible.width, min(max(visible.height, fontsize * 1.6), available))
        result = scratch.insert_textbox(box, edit.text, fontname=name, fontsize=fontsize, color=edit.color)
        if result < 0 and box.height < available:
            box.y1 = min(available, box.height - result + fontsize * .25)
            result = scratch.insert_textbox(box, edit.text, fontname=name, fontsize=fontsize, color=edit.color)
        if result < 0:
            raise ValueError("文字超出页面，请减小字号、增大框宽或减少内容。")
        # The appearance's MediaBox must match its annotation rectangle.
        final = document.new_page(width=visible.width, height=box.height)
        final.insert_font(fontname=name, fontbuffer=buffer)
        if final.insert_textbox(final.rect, edit.text, fontname=name, fontsize=fontsize, color=edit.color) < 0:
            raise ValueError("文字放不下，请调整字号或文本框。")
        document.delete_page(0)
        document.subset_fonts()
        visible.y1 = visible.y0 + box.height
        area = visible * page.derotation_matrix
        previous = next((a for a in (page.annots() or ()) if a.info.get("id") == edit.annotation_id), None)
        if edit.kind == "freetext_update" and previous is None:
            raise ValueError("要修改的文本框已不存在。")
        annot = page.add_freetext_annot(area, edit.text, fontsize=fontsize, text_color=edit.color)
        page.parent.xref_set_key(annot.xref, "NM", p.get_pdf_str(edit.annotation_id))
        settings.update(fontsize=fontsize, color=list(edit.color))
        page.parent.xref_set_key(annot.xref, TEXT_SETTINGS, p.get_pdf_str(json.dumps(settings, ensure_ascii=False)))
        if page.rotation:
            with p.open() as canonical:
                target = canonical.new_page(width=area.width, height=area.height)
                target.show_pdf_page(target.rect, document, 0, rotate=page.rotation)
                set_appearance(page, annot, canonical)
        else:
            set_appearance(page, annot, document)
        if previous is not None:
            page.delete_annot(previous)
    return buffer is not None


def is_watermark(page, annot):
    return (annot.info.get("id", "").startswith(WATERMARK_PREFIX)
            and page.parent.xref_get_key(annot.xref, "ZhiXuWatermark") == ("string", "1"))


def remove_watermarks(page):
    targets = [annot for annot in (page.annots() or ()) if is_watermark(page, annot)]
    for annot in targets:
        page.delete_annot(annot)
    return len(targets)


def add_watermark(page, edit):
    rotation = page.rotation
    try:
        page.set_rotation(int(dict(edit.details).get("rotation", rotation)))
        _add_watermark(page, edit)
    finally:
        page.set_rotation(rotation)


@lru_cache(maxsize=2)
def watermark_base(text, color, image=""):
    """Shared unrotated vector source for preview and saved annotations."""
    import pymupdf as p
    from app.pdfedits import replacement_font
    with p.open() as base:
        if image:
            from PIL import Image
            data = base64.b64decode(image, validate=True)
            if len(data) > 10 * 1024 * 1024:
                raise ValueError("水印图片请控制在 10 MB 内。")
            with Image.open(io.BytesIO(data)) as picture:
                width, height = picture.size
                if width * height > 20_000_000:
                    raise ValueError("水印图片像素过多，请先缩小图片。")
                picture.verify()
            original = base.new_page(width=width, height=height)
            original.insert_image(original.rect, stream=data)
        else:
            if not text.strip() or len(text) > 1000:
                raise ValueError("文字水印请输入 1～1000 个字符。")
            original = base.new_page()
            font, name, buffer = replacement_font(original, "".join(c for c in text if c not in "\r\n\t"), "Microsoft YaHei")
            lines = text.splitlines() or [text]
            width = max(font.text_length(line, fontsize=36) for line in lines) + 24
            height = max(72, len(lines) * 60)
            original = base.new_page(width=width, height=height)
            original.insert_font(fontname=name, fontbuffer=buffer)
            if original.insert_textbox(original.rect, text, fontname=name, fontsize=36, color=color, align=1) < 0:
                raise ValueError("文字水印放不下，请减少内容。")
            base.delete_page(0)
            base.subset_fonts()
        return base.tobytes(), float(width), float(height)


def watermark_layout(width, height, base_width, base_height, options):
    """PDF-point geometry shared by the live canvas and final PDF."""
    size = float(options.get("size", 35))
    angle = float(options.get("angle", 45))
    if (not all(math.isfinite(v) and v > 0 for v in (width, height, base_width, base_height))
            or not 10 <= size <= 90 or not -180 <= angle <= 180):
        raise ValueError("水印大小或角度无效。")
    radians = math.radians(angle)
    rotated_width = abs(base_width * math.cos(radians)) + abs(base_height * math.sin(radians))
    rotated_height = abs(base_width * math.sin(radians)) + abs(base_height * math.cos(radians))
    factor = min(width * size / 100 / rotated_width, height * .8 / rotated_height)
    mark_width, mark_height = rotated_width * factor, rotated_height * factor
    if options.get("tile", False):
        columns = max(1, int(width / (mark_width * 1.3)))
        rows = max(1, int(height / (mark_height * 1.8)))
        if rows * columns > 200:
            raise ValueError("平铺水印过多，请增加水印大小。")
        offset = options.get("tile_offset", (.5, .5))
        if len(offset) != 2 or not all(math.isfinite(v) and 0 <= v <= 1 for v in offset):
            raise ValueError("平铺位置无效。")
        pitch_x, pitch_y = width / columns, height / rows
        dx, dy = (offset[0] - .5) * pitch_x, (offset[1] - .5) * pitch_y
        locations = [(pitch_x * (column + .5) - mark_width / 2 + dx,
                      pitch_y * (row + .5) - mark_height / 2 + dy)
                     for row in range(-1, rows + 1) for column in range(-1, columns + 1)]
        locations = [(x, y) for x, y in locations if x < width and x + mark_width > 0
                     and y < height and y + mark_height > 0]
    else:
        position = options.get("position", "center-center")
        if position == "custom":
            offset = options.get("offset", (.5, .5))
            if len(offset) != 2 or not all(math.isfinite(v) and 0 <= v <= 1 for v in offset):
                raise ValueError("水印位置无效。")
            locations = [(offset[0] * (width - mark_width), offset[1] * (height - mark_height))]
        else:
            if position not in {f"{v}-{h}" for v in ("top", "center", "bottom") for h in ("left", "center", "right")}:
                raise ValueError("水印位置无效。")
            vertical, horizontal = position.split("-")
            margin = min(width, height) * .03
            x = {"left": margin, "center": (width - mark_width) / 2, "right": width - mark_width - margin}[horizontal]
            y = {"top": margin, "center": (height - mark_height) / 2, "bottom": height - mark_height - margin}[vertical]
            locations = [(x, y)]
    return mark_width, mark_height, locations


def _add_watermark(page, edit):
    import pymupdf as p
    options = dict(edit.details)
    opacity = float(options.get("opacity", .2))
    if not .05 <= opacity <= 1:
        raise ValueError("水印透明度无效。")
    data, width, height = watermark_base(edit.text, tuple(edit.color), options.get("image", ""))
    mark_width, mark_height, locations = watermark_layout(page.rect.width, page.rect.height, width, height, options)
    angle = float(options.get("angle", 45))
    radians = math.radians(angle)
    rotated_width = abs(width * math.cos(radians)) + abs(height * math.sin(radians))
    rotated_height = abs(width * math.sin(radians)) + abs(height * math.cos(radians))
    with p.open(stream=data, filetype="pdf") as base, p.open() as appearance:
        canonical_width, canonical_height = ((rotated_height, rotated_width) if page.rotation % 180
                                             else (rotated_width, rotated_height))
        rotated = appearance.new_page(width=canonical_width, height=canonical_height)
        rotated.show_pdf_page(rotated.rect, base, 0, rotate=angle + page.rotation)
        for ordinal, (x, y) in enumerate(locations):
            area = p.Rect(x, y, x + mark_width, y + mark_height) * page.derotation_matrix
            annot = page.add_stamp_annot(area)
            if annot.set_rect(area) is False:
                raise ValueError("水印位置无法应用。")
            annot.set_info(title="纸序 PDF 水印", content=edit.text if not options.get("image") else "图片水印")
            page.parent.xref_set_key(annot.xref, "NM", p.get_pdf_str(f"{edit.annotation_id}-{ordinal}"))
            page.parent.xref_set_key(annot.xref, "ZhiXuWatermark", p.get_pdf_str("1"))
            set_appearance(page, annot, appearance, opacity)
