"""Replace a selected PDF region with processed pixels on private draft pages."""
from __future__ import annotations

import io
import math

from app.workspace import PageEdit


STYLES = (("pixelate", "像素块"), ("blur", "柔化模糊"), ("solid", "纯色遮盖"))
SHAPES = (("rect", "矩形"), ("ellipse", "椭圆"))
DEFAULTS = dict(style="pixelate", shape="rect", strength=12, color=(0., 0., 0.))


def mosaic_edit(rectangle, *, style="pixelate", shape="rect", strength=12, color=(0., 0., 0.)):
    if style not in dict(STYLES) or shape not in dict(SHAPES):
        raise ValueError("请选择有效的马赛克样式与形状。")
    if not isinstance(strength, (int, float)) or not math.isfinite(strength) or not 2 <= strength <= 40:
        raise ValueError("马赛克强度须在 2–40 之间。")
    if (len(color) != 3 or not all(isinstance(c, (int, float)) and math.isfinite(c) and 0 <= c <= 1 for c in color)):
        raise ValueError("马赛克颜色无效。")
    if len(rectangle) != 4 or not all(math.isfinite(v) for v in rectangle):
        raise ValueError("马赛克范围无效，请重新框选。")
    return PageEdit("mosaic", tuple(rectangle), color=tuple(color), details=(
        ("style", style), ("shape", shape), ("strength", strength)))


def mosaic_picture(picture, scale, edit):
    """Share the exact effect between draft preview and final PDF replay."""
    from PIL import Image, ImageDraw, ImageFilter

    options = dict(edit.details)
    style, shape, strength = options["style"], options["shape"], options["strength"]
    if style == "solid":
        result = Image.new("RGB", picture.size, tuple(round(c * 255) for c in edit.color))
    elif style == "blur":
        result = picture.filter(ImageFilter.GaussianBlur(radius=max(1, strength * scale)))
    else:
        block = max(2, strength * scale)
        result = picture.resize((max(1, math.ceil(picture.width / block)),
                                 max(1, math.ceil(picture.height / block))), Image.Resampling.BOX)
        result = result.resize(picture.size, Image.Resampling.NEAREST)
    if shape == "ellipse":
        mask = Image.new("L", picture.size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0, picture.width - 1, picture.height - 1), fill=255)
        result = Image.composite(result, picture, mask)
    return result


def apply_mosaic(page, edit):
    import pymupdf as p
    from PIL import Image

    # Validate again when immutable edits arrive from the PDF worker.
    edit = mosaic_edit(edit.points, color=edit.color, **dict(edit.details))
    rotation = page.rotation
    try:
        page.set_rotation(0)
        area = p.Rect(edit.points).normalize() & page.rect
        if area.is_empty or area.width < 2 or area.height < 2:
            raise ValueError("请拖出宽、高至少 2 pt 的马赛克范围。")
        annotations = list(page.annots() or ())
        if any(a.type[0] == p.PDF_ANNOT_REDACT for a in annotations):
            raise ValueError("该页有待应用的涂黑批注，请先处理后再添加马赛克。")
        if any(area.intersects(w.rect) for w in (page.widgets() or ())):
            raise ValueError("马赛克范围与交互表单重叠，请避开表单字段。")

        # Bound work for unusually large pages, independent of the reader zoom.
        scale = min(2., math.sqrt(4_000_000 / area.get_area()), 4096 / max(area.width, area.height))
        pix = page.get_pixmap(matrix=p.Matrix(scale, scale), clip=area,
                              colorspace=p.csRGB, alpha=False, annots=True)
        if pix.width < 1 or pix.height < 1:
            raise ValueError("马赛克范围无法生成图片，请重新框选。")
        picture = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        # Fit the patch to the actual raster grid, avoiding fractional-pixel shifts.
        area = p.Rect(pix.irect) / scale
        if any(area.intersects(w.rect) for w in (page.widgets() or ())):
            raise ValueError("马赛克边缘靠近交互表单，请缩小范围后重试。")
        picture = mosaic_picture(picture, scale, edit)
        data = io.BytesIO()
        picture.save(data, format="PNG")

        # Remove editable annotation payloads too, including image stamp sources.
        for annot in annotations:
            if area.intersects(annot.rect):
                page.delete_annot(annot)
        page.add_redact_annot(area, fill=False, cross_out=False)
        page.apply_redactions(images=p.PDF_REDACT_IMAGE_PIXELS,
                              graphics=p.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
                              text=p.PDF_REDACT_TEXT_REMOVE)
        # Drop unused original image resources before garbage-collected saving.
        page.clean_contents()
        page.insert_image(area, stream=data.getvalue(), keep_proportion=False)
    finally:
        page.set_rotation(rotation)
