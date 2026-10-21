"""Vector appearances for editable text annotations and removable watermarks."""
import json
import math
import base64
import io
from functools import lru_cache

TEXT_SETTINGS = "ZhiXuText"
WATERMARK_PREFIX = "zhixu-watermark-"
WATERMARK_DATA = "ZhiXuWatermarkData"


def set_appearance(page, annot, document, opacity=1):
    """Import the form without leaving a second copy in page content."""
    import pymupdf as p
    parent = page.parent
    rectangle = parent.xref_get_key(annot.xref, "Rect")[1]
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
    # Updating a standard stamp fits its Rect to the built-in stamp's ratio.
    # Our appearance already has the intended ratio; keep its original bounds.
    parent.xref_set_key(annot.xref, "Rect", rectangle)


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


def watermark_id(annot):
    identifier = annot.info.get("id", "")
    prefix, _, ordinal = identifier.rpartition("-")
    return prefix if ordinal.isdigit() else identifier


def remove_watermarks(page, identifier=""):
    targets = [annot for annot in (page.annots() or ()) if is_watermark(page, annot)
               and (not identifier or watermark_id(annot) == identifier)]
    for annot in targets:
        page.delete_annot(annot)
    return len(targets)


def watermark_settings(page, annot):
    """Read one shared settings stream, including the original image if present."""
    kind, value = page.parent.xref_get_key(annot.xref, WATERMARK_DATA)
    if kind != "xref":
        return None
    try:
        data = page.parent.xref_stream(int(value.split()[0]))
        if len(data) > 15 * 1024 * 1024:
            return None
        settings = json.loads(data)
        options = settings["options"]
        color = settings["color"]
        if (settings.get("version") != 1 or not isinstance(settings["text"], str)
                or len(settings["text"]) > 1000 or len(color) != 3
                or not all(isinstance(c, (int, float)) and 0 <= c <= 1 for c in color)
                or not isinstance(options.get("image", ""), str)
                or len(options.get("image", "")) > 14 * 1024 * 1024
                or not .05 <= float(options.get("opacity", .2)) <= 1):
            return None
        options.update(size=float(options.get("size", 35)), angle=float(options.get("angle", 45)),
                       opacity=float(options.get("opacity", .2)))
        for key in ("offset", "tile_offset"):
            offset = options.get(key, (.5, .5))
            if len(offset) != 2 or not all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in offset):
                return None
        if options.get("rotation", 0) not in (0, 90, 180, 270):
            return None
        watermark_layout(600, 800, 200, 72, options)
        return settings
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError):
        return None


def legacy_watermark_settings(page, members):
    """Recover the source and layout written by versions without edit metadata."""
    import pymupdf as p
    annot = members[0]
    doc = page.parent
    appearance = int(doc.xref_get_key(annot.xref, "AP/N")[1].split()[0])
    wrapper = int(doc.xref_get_key(appearance, "Resources/XObject/fzFrm0")[1].split()[0])
    transform = [float(v) for v in doc.xref_get_key(wrapper, "Matrix")[1].strip("[]").split()]
    angle = round((math.degrees(math.atan2(transform[1], transform[0])) - page.rotation + 180) % 360 - 180)
    text = annot.info.get("content", "")
    color = (.4, .4, .4)
    for block in annot.get_text("dict").get("blocks", ()):
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                color = tuple(((span["color"] >> shift) & 255) / 255 for shift in (16, 8, 0))
    image = ""
    source = int(doc.xref_get_key(wrapper, "Resources/XObject/fullpage")[1].split()[0])
    kind, value = doc.xref_get_key(source, "Resources/XObject/fzImg0")
    if kind == "xref":
        source = int(value.split()[0])
        width = int(doc.xref_get_key(source, "Width")[1])
        height = int(doc.xref_get_key(source, "Height")[1])
        if width <= 0 or height <= 0 or width * height > 20_000_000:
            raise ValueError("旧版水印图片像素过多。")
        kind, mask = doc.xref_get_key(source, "SMask")
        if kind == "xref":
            pixmap = p.Pixmap(doc, source)
            pixmap = p.Pixmap(pixmap, p.Pixmap(doc, int(mask.split()[0])))
            if pixmap.colorspace and pixmap.colorspace.n not in (1, 3):
                pixmap = p.Pixmap(p.csRGB, pixmap)
            data = pixmap.tobytes("png")
        else:
            data = doc.extract_image(source)["image"]
        image = base64.b64encode(data).decode("ascii")
    area = p.Rect(annot.rect) * page.rotation_matrix
    bbox = p.Rect([float(v) for v in doc.xref_get_key(appearance, "BBox")[1].strip("[]").split()])
    width, height = (bbox.height, bbox.width) if page.rotation % 180 else (bbox.width, bbox.height)
    factor = max(area.width / width, area.height / height)
    center = (area.tl + area.br) / 2
    area = p.Rect(center.x - width * factor / 2, center.y - height * factor / 2,
                  center.x + width * factor / 2, center.y + height * factor / 2)
    bounds = [p.Rect(member.rect) * page.rotation_matrix for member in members]
    kind, opacity = doc.xref_get_key(appearance, "Resources/ExtGState/ZhiXuOpacity/ca")
    options = dict(size=max(10, min(90, round(area.width / page.rect.width * 100))),
                   opacity=float(opacity) if kind in ("float", "int") else 1,
                   angle=angle, position="custom", tile=len(members) > 1, image=image,
                   rotation=page.rotation, tile_offset=(.5, .5))
    if options["tile"]:
        pitch_x = page.rect.width / max(1, int(page.rect.width / (area.width * 1.3)))
        centers = [((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2) for rect in bounds]
        # Infer the original row pitch before the stamp compressed each tile.
        rows = sorted({round(y, 3) for _, y in centers})
        pitch_y = min((b - a for a, b in zip(rows, rows[1:]) if b - a > 1), default=page.rect.height)
        options["tile_offset"] = ((centers[0][0] / pitch_x) % 1, (centers[0][1] / pitch_y) % 1)
    else:
        # The old stamp update kept the center while shrinking one dimension.
        _, width, height = watermark_base(text, color, image)
        mw, mh, _ = watermark_layout(page.rect.width, page.rect.height, width, height, options)
        options["offset"] = tuple(max(0, min(1, value)) for value in (
            ((area.x0 + area.x1) / 2 - mw / 2) / max(1, page.rect.width - mw),
            ((area.y0 + area.y1) / 2 - mh / 2) / max(1, page.rect.height - mh)))
    return dict(version=1, text=text, color=color, options=options)


def list_watermarks(page, rotation=None):
    original_rotation = page.rotation
    try:
        if rotation is not None:
            page.set_rotation(rotation)
        groups = {}
        for annot in page.annots() or ():
            if is_watermark(page, annot):
                groups.setdefault(watermark_id(annot), []).append(annot)
        result = []
        for identifier, members in groups.items():
            settings = watermark_settings(page, members[0])
            if settings is None:
                try:
                    settings = legacy_watermark_settings(page, members)
                except (ValueError, TypeError, KeyError, RuntimeError, IndexError, ZeroDivisionError):
                    settings = None
            result.append(dict(id=identifier, settings=settings,
                               text=members[0].info.get("content", "水印")))
        return result
    finally:
        page.set_rotation(original_rotation)


def add_watermark(page, edit):
    rotation = page.rotation
    try:
        page.set_rotation(int(dict(edit.details).get("rotation", rotation)))
        target = dict(edit.details).get("target", edit.annotation_id)
        if edit.kind == "watermark_update" and not remove_watermarks(page, target):
            raise ValueError("要修改的水印已不存在。")
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
            # 用实际字形边界定位，避免文本框下方的空白让旋转水印看起来偏心。
            ink = p.Rect()
            for kind, bounds in original.get_bboxlog():
                if kind in ("fill-text", "stroke-text"):
                    ink |= p.Rect(bounds)
            if ink.is_empty or not all(math.isfinite(value) for value in ink):
                raise ValueError("水印文字没有可显示的内容。")
            original.set_cropbox((ink + (-3, -3, 3, 3)) & original.rect)
            width, height = original.rect.width, original.rect.height
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
        metadata = page.parent.get_new_xref()
        page.parent.update_object(metadata, "<<>>")
        saved_options = {key: value for key, value in options.items() if key != "target"}
        page.parent.update_stream(metadata, json.dumps(dict(version=1, text=edit.text,
            color=edit.color, options=saved_options), ensure_ascii=False).encode("utf-8"))
        for ordinal, (x, y) in enumerate(locations):
            area = p.Rect(x, y, x + mark_width, y + mark_height) * page.derotation_matrix
            annot = page.add_stamp_annot(area)
            if annot.set_rect(area) is False:
                raise ValueError("水印位置无法应用。")
            annot.set_info(title="纸序 PDF 水印", content=edit.text if not options.get("image") else "图片水印")
            page.parent.xref_set_key(annot.xref, "NM", p.get_pdf_str(f"{edit.annotation_id}-{ordinal}"))
            page.parent.xref_set_key(annot.xref, "ZhiXuWatermark", p.get_pdf_str("1"))
            page.parent.xref_set_key(annot.xref, WATERMARK_DATA, f"{metadata} 0 R")
            set_appearance(page, annot, appearance, opacity)
