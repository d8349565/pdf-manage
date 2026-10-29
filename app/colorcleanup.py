"""Local, conservative color cleanup. Edits replay only on private PDF pages."""
from __future__ import annotations

import base64
import colorsys
from collections import deque
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import math
import re

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat
import pymupdf as p

from app.output import check_cancel
from app.pdfedits import iter_annotations
from app.workspace import PageEdit

MAX_PIXELS = 24_000_000
ANNOT_TYPES = {3: '直线', 4: '方框', 5: '圆圈', 6: '多边形', 7: '折线',
               8: '高亮', 9: '下划线', 10: '波浪线', 11: '删除线', 15: '墨迹'}


@dataclass(frozen=True)
class CleanupSettings:
    color: tuple = (210, 25, 45)
    tolerance: int = 30
    faint: int = 45
    smooth_background: bool = False
    remove_fringe: bool = False
    erase_fill: str = 'paper'

    def validate(self):
        if (len(self.color) != 3 or any(type(c) is not int or not 0 <= c <= 255 for c in self.color)
                or max(self.color) - min(self.color) < 12):
            raise ValueError('请选择红色、蓝色等有明显色彩的笔迹；暂不支持黑色或灰色。')
        if not 0 <= self.tolerance <= 100 or not 0 <= self.faint <= 100:
            raise ValueError('颜色容差和浅色笔迹强度须在 0～100 之间。')
        if self.erase_fill not in ('paper', 'white'):
            raise ValueError('请选择白色或匹配纸面的擦除填充。')


@dataclass(frozen=True)
class CleanupZones:
    # All coordinates refer to the displayed, rotated, cropped page, in [0, 1].
    region: tuple = (0., 0., 1., 1.)
    protected: tuple = ()
    strokes: tuple = ()  # (radius / min(display width, height), ((x, y), ...))
    erased: tuple = ()  # Explicit manual rectangles, never inferred from black ink.
    erased_strokes: tuple = ()
    erased_ellipses: tuple = ()
    protected_ellipses: tuple = ()

    def validate(self):
        for rect in (self.region, *self.protected, *self.erased, *self.erased_ellipses, *self.protected_ellipses):
            if (len(rect) != 4 or not all(math.isfinite(v) and 0 <= v <= 1 for v in rect)
                    or rect[0] >= rect[2] or rect[1] >= rect[3]):
                raise ValueError('框选范围无效，请重新选择。')
        for radius, points in (*self.strokes, *self.erased_strokes):
            if not math.isfinite(radius) or not 0 < radius <= .2 or not points:
                raise ValueError('保护画笔范围无效。')
            if any(len(pt) != 2 or not all(math.isfinite(v) and 0 <= v <= 1 for v in pt) for pt in points):
                raise ValueError('保护画笔坐标无效。')


def color_mask(image, settings, cancel=None):
    """Pillow LUTs run in C; hue distance wraps around red, with no dilation."""
    settings.validate()
    check_cancel(cancel)
    rgb = image.convert('RGB')
    hue, saturation, value = rgb.convert('HSV').split()
    target = colorsys.rgb_to_hsv(*(c / 255 for c in settings.color))[0] * 255
    radius = 5 + settings.tolerance * .27
    hue = hue.point([255 if min(abs(i - target), 255 - abs(i - target)) <= radius else 0 for i in range(256)])
    saturation = saturation.point([255 if i >= max(10, 65 - settings.faint * .55) else 0 for i in range(256)])
    value = value.point([255 if i >= 48 else 0 for i in range(256)])
    r, g, b = rgb.split()
    chroma = ImageChops.subtract(ImageChops.lighter(ImageChops.lighter(r, g), b),
                                 ImageChops.darker(ImageChops.darker(r, g), b))
    chroma = chroma.point([255 if i >= max(9, 30 - settings.faint * .22) else 0 for i in range(256)])
    # Dark, color-tinted antialiasing at a black glyph is ambiguous. Leave it
    # rather than opening holes in numbers, brackets or fine printed strokes.
    readable = rgb.convert('L').point([255 if i >= 38 else 0 for i in range(256)])
    check_cancel(cancel)
    return ImageChops.multiply(ImageChops.multiply(hue, saturation),
        ImageChops.multiply(ImageChops.multiply(value, chroma), readable))


def _row_regions(seed, margin, cancel):
    mask = Image.new('L', seed.size)
    if not seed.getbbox():
        return mask
    bands, start, last = [], None, 0
    for y, occupied in enumerate(seed.getprojection()[1]):
        if occupied:
            if start is None:
                start = y
            elif y - last > 2 * margin:
                bands.append((start, last + 1))
                start = y
            last = y
    bands.append((start, last + 1))
    draw = ImageDraw.Draw(mask)
    for y0, y1 in bands:
        check_cancel(cancel)
        box = seed.crop((0, y0, seed.width, y1)).getbbox()
        draw.rectangle((max(0, box[0] - margin), max(0, y0 - margin),
            min(seed.width - 1, box[2] + margin), min(seed.height - 1, y1 + margin)), fill=255)
    return mask


def _fringe_regions(image, settings, allowed, cancel):
    """White-paper cleanup: include codec fringes, retain connected printed ink.

    This is an explicit stronger mode, not text reconstruction. An opaque color
    stroke can already have destroyed a black line in the supplied image.
    """
    hue, saturation, _ = image.convert('HSV').split()
    r, g, b = image.split()
    maximum = ImageChops.lighter(ImageChops.lighter(r, g), b)
    minimum = ImageChops.darker(ImageChops.darker(r, g), b)
    chroma = ImageChops.subtract(maximum, minimum)
    gray = image.convert('L')
    target = colorsys.rgb_to_hsv(*(c / 255 for c in settings.color))[0] * 255
    radius = 5 + settings.tolerance * .45
    distances = [min(abs(i - target), 255 - abs(i - target)) for i in range(256)]
    seed = ImageChops.multiply(hue.point([255 if d <= radius else 0 for d in distances]),
        chroma.point([255 if i >= max(3, 15 - settings.faint * .11) else 0 for i in range(256)]))
    if allowed is not None:
        seed = ImageChops.multiply(seed, allowed)
    if not seed.getbbox():
        return seed, seed, seed
    check_cancel(cancel)
    # Include the paper between strokes so low-chroma gray/green ringing cannot
    # retain the outline of an otherwise removed word. No PDF content is removed.
    # ponytail: row bands suit worksheets; photos need another cleanup method.
    margin = 8
    mask = _row_regions(seed, margin, cancel)
    other_colors = ImageChops.multiply(hue.point([255 if d > radius + 20 else 0 for d in distances]),
        saturation.point([255 if i >= 80 else 0 for i in range(256)]))
    other_colors = ImageChops.multiply(other_colors, ImageChops.multiply(
        maximum.point([255 if i >= 100 else 0 for i in range(256)]),
        chroma.point([255 if i >= 40 else 0 for i in range(256)])))
    # Chroma subsampling can give a blue letter a reddish fringe. Its whole
    # footprint must remain protected when the user selected red ink.
    other_colors = _row_regions(other_colors, margin, cancel)
    mask = ImageChops.subtract(mask, other_colors)
    if allowed is not None:
        mask = ImageChops.multiply(mask, allowed)

    # Preserve neutral black and its connected anti-aliased strokes. Near-black
    # pen tips can be indistinguishable from printed dots / CJK radicals; these
    # require an explicit eraser selection rather than a character-shape guess.
    neutral = chroma.point([255 if i <= 6 else 0 for i in range(256)])
    anchors = ImageChops.multiply(neutral, gray.point([255 if i < 200 else 0 for i in range(256)]))
    candidate = ImageChops.lighter(maximum.point([255 if i < 100 else 0 for i in range(256)]),
        ImageChops.multiply(neutral, gray.point([255 if i < 245 else 0 for i in range(256)])))
    front = ImageChops.subtract(ImageChops.multiply(anchors.filter(ImageFilter.MaxFilter(3)), candidate), anchors)
    queue = deque(match.start() for match in re.finditer(b'\xff', front.tobytes()))
    kept, available = bytearray(anchors.tobytes()), candidate.tobytes()
    width, length = image.width, image.width * image.height
    for pos in queue:
        kept[pos] = 255
    steps = 0
    while queue:
        if steps % 4096 == 0:
            check_cancel(cancel)
        pos = queue.popleft()
        x = pos % width
        for next_pos in ((pos - 1 if x else -1), (pos + 1 if x < width - 1 else -1), pos - width, pos + width):
            if 0 <= next_pos < length and available[next_pos] and not kept[next_pos]:
                kept[next_pos] = 255
                queue.append(next_pos)
        steps += 1
    return mask, Image.frombytes('L', image.size, bytes(kept)), seed


def _region_mask(size, rectangles=(), ellipses=(), strokes=(), transform=None, display_size=None):
    """One geometry path for preview, protection, brush and final PDF pixels."""
    w, h = size
    dw, dh = display_size or size
    def point(x, y):
        if transform is None:
            return x * w, y * h
        pt = p.Point(x * dw, y * dh) * transform
        return pt.x, pt.y
    def polygon(draw, rect, fill):
        x0, y0, x1, y1 = rect
        draw.polygon([point(x0, y0), point(x1, y0), point(x1, y1), point(x0, y1)], fill=fill)
    mask = Image.new('L', size)
    draw = ImageDraw.Draw(mask)
    for rect in rectangles:
        polygon(draw, rect, 255)
    for x0, y0, x1, y1 in ellipses:
        cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
        draw.polygon([point(cx + rx * math.cos(a * math.tau / 128), cy + ry * math.sin(a * math.tau / 128))
                      for a in range(128)], fill=255)
    for radius, points in strokes:
        rx, ry = radius * min(dw, dh) / dw, radius * min(dw, dh) / dh
        previous = None
        for x, y in points:
            ring = [point(x + rx * math.cos(a * math.tau / 24), y + ry * math.sin(a * math.tau / 24)) for a in range(24)]
            draw.polygon(ring, fill=255)
            if previous is not None:
                # Convex hull of the two circles covers the complete brush segment.
                from app.scanwhite import _hull
                draw.polygon(_hull(previous + ring), fill=255)
            previous = ring
    return mask


def zone_masks(size, zones=CleanupZones(), transform=None, display_size=None):
    """Map displayed selections into source-image pixels, including crop/rotation."""
    zones.validate()
    allowed = _region_mask(size, (zones.region,), transform=transform, display_size=display_size)
    protected = _region_mask(size, zones.protected, zones.protected_ellipses, zones.strokes, transform, display_size)
    # Protect source-pixel footprints at selection boundaries, too.
    protected = protected.filter(ImageFilter.MaxFilter(3))
    return ImageChops.subtract(allowed, protected), protected


def erasure_mask(size, zones, transform=None, display_size=None):
    """Only explicit manual marks can override foreground protection."""
    allowed, _ = zone_masks(size, zones, transform, display_size)
    erased = _region_mask(size, zones.erased, zones.erased_ellipses, zones.erased_strokes, transform, display_size)
    return ImageChops.multiply(erased, allowed)


def clean_image(image, settings=CleanupSettings(), *, allowed=None, erase=None, cancel=None):
    """Return result and final binary mask. Outside it, RGB bytes are identical."""
    image = image.convert('RGB')
    if image.width * image.height > MAX_PIXELS:
        raise ValueError('单张图片超过 2400 万像素，请先缩小图片或选择转图片处理。')
    if allowed is not None:
        if allowed.size != image.size:
            raise ValueError('处理范围与图片尺寸不一致。')
    settings.validate()
    check_cancel(cancel)
    foreground = None
    if settings.remove_fringe:
        mask, foreground, seed = _fringe_regions(image, settings, allowed, cancel)
    else:
        mask = color_mask(image, settings, cancel)
        if allowed is not None:
            mask = ImageChops.multiply(mask, allowed)
        seed = mask
    if erase is not None:
        if erase.size != image.size:
            raise ValueError('擦除范围与图片尺寸不一致。')
        erase = erase.convert('L').point(lambda v: 255 if v else 0)
        if allowed is not None:
            erase = ImageChops.multiply(erase, allowed)
        mask = ImageChops.lighter(mask, erase)
        if foreground is not None:
            foreground = ImageChops.subtract(foreground, erase)
    if not mask.getbbox():
        return image.copy(), mask
    # Sample neutral, bright paper near each tile; never sample black text as paper.
    hsv = image.convert('HSV')
    _, saturation, value = hsv.split()
    paper = ImageChops.multiply(saturation.point([255 if i < 42 else 0 for i in range(256)]),
                                value.point([255 if i >= 125 else 0 for i in range(256)]))
    paper = ImageChops.subtract(paper, seed)
    fallback = tuple(ImageStat.Stat(image, paper).median) if paper.getbbox() else (255, 255, 255)
    tile = max(32, round(max(image.size) / 40))
    cols, rows = math.ceil(image.width / tile), math.ceil(image.height / tile)
    colors = Image.new('RGB', (cols, rows), 'white')
    for y in range(rows):
        check_cancel(cancel)
        for x in range(cols):
            box = (max(0, (x - 1) * tile), max(0, (y - 1) * tile),
                   min(image.width, (x + 2) * tile), min(image.height, (y + 2) * tile))
            sample = ImageStat.Stat(image.crop(box), paper.crop(box))
            color = tuple(sample.median) if sample.count[0] else fallback
            if settings.remove_fringe and min(color) >= 250 and max(color) - min(color) <= 4:
                color = (255, 255, 255)
            colors.putpixel((x, y), color)
    fill = colors.resize((cols * tile, rows * tile),
        Image.Resampling.BILINEAR if settings.smooth_background else Image.Resampling.NEAREST).crop((0, 0, *image.size))
    if foreground is not None:
        fill.paste(image.convert('L').convert('RGB'), mask=foreground)
    if erase is not None and settings.erase_fill == 'white':
        fill.paste((255, 255, 255), mask=erase)
    check_cancel(cancel)
    result = Image.composite(fill, image, mask)
    difference = ImageChops.difference(image, result)
    r, g, b = difference.split()
    # Export/replay and highlights report the effective processing mask, including
    # the fringes and excluding pixels which already had their final value.
    final_mask = ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda v: 255 if v else 0)
    return result, final_mask


def _picture(page, xref, *, allow_gray=False):
    doc = page.parent
    width = int(doc.xref_get_key(xref, 'Width')[1])
    height = int(doc.xref_get_key(xref, 'Height')[1])
    if width * height > MAX_PIXELS:
        raise ValueError('单张图片超过 2400 万像素。')
    pix = p.Pixmap(doc, xref)
    if pix.width * pix.height > MAX_PIXELS:
        raise ValueError('单张图片超过 2400 万像素。')
    gray = allow_gray and pix.colorspace is not None and pix.colorspace.n == 1
    bits = doc.xref_get_key(xref, 'BitsPerComponent')[1]
    if (pix.alpha or pix.colorspace is None or pix.colorspace.n != 3 and not gray
            or bits not in (('1', '8') if gray else ('8',))
            or any(doc.xref_get_key(xref, key)[0] != 'null' for key in ('SMask', 'Mask', 'Decode'))):
        raise ValueError('该页图片含透明蒙版、特殊色彩空间或解码设置，暂不能安全替换。')
    return Image.frombytes('L' if gray else 'RGB', (pix.width, pix.height), pix.samples)


def _png(image):
    stream = BytesIO()
    image.save(stream, format='PNG')
    return base64.b64encode(stream.getvalue()).decode('ascii')


def _render(page, width=1100):
    scale = min(width / page.rect.width, max(2000, width * 1.8) / page.rect.height,
                math.sqrt(12_000_000 / page.rect.get_area()))
    pix = page.get_pixmap(matrix=p.Matrix(scale, scale), colorspace=p.csRGB, alpha=False)
    return Image.frombytes('RGB', (pix.width, pix.height), pix.samples)


def _annotation_candidates(page, settings, zones):
    mask, _ = zone_masks((1000, max(1, round(1000 * page.rect.height / page.rect.width))), zones)
    result, seen = [], set()
    for ordinal, annot in enumerate(iter_annotations(page)):
        token = annot.info.get('id') or f'zhixu-original-{ordinal}'
        if token in seen:
            token = f'zhixu-original-{ordinal}'
        seen.add(token)
        color = annot.colors.get('stroke') or annot.colors.get('fill') or ()
        if annot.type[0] not in ANNOT_TYPES or len(color) != 3:
            continue
        if page.parent.xref_get_key(annot.xref, 'ZhiXuSeam')[0] != 'null':
            continue
        rgb = tuple(max(0, min(255, round(c * 255))) for c in color)
        if not color_mask(Image.new('RGB', (1, 1), rgb), settings).getbbox():
            continue
        rect = annot.rect * page.rotation_matrix
        normal = (rect.x0 / page.rect.width, rect.y0 / page.rect.height,
                  rect.x1 / page.rect.width, rect.y1 / page.rect.height)
        box = (math.floor(normal[0] * mask.width), math.floor(normal[1] * mask.height),
               math.ceil(normal[2] * mask.width), math.ceil(normal[3] * mask.height))
        # An annotation is indivisible: require its entire box inside the allowed area.
        if box[0] < 0 or box[1] < 0 or box[2] > mask.width or box[3] > mask.height or not mask.crop(box).getbbox():
            continue
        if ImageStat.Stat(mask.crop(box)).extrema[0] != (255, 255):
            continue
        result.append(dict(id=token, kind=ANNOT_TYPES[annot.type[0]], rect=normal,
                           content=annot.info.get('content', '')[:100]))
    return result


def _hidden_spans(page):
    traces = page.get_texttrace()
    images = [(seq, p.Rect(box)) for seq, (kind, box) in enumerate(page.get_bboxlog()) if kind == 'fill-image']
    def hidden(span):
        return (span['type'] == 3 or span.get('opacity', 1) == 0
                or span.get('font', '').split('+')[-1] == 'GlyphLessFont'
                or any(seq > span['seqno'] and box.contains(p.Rect(span['bbox'])) for seq, box in images))
    return ([span for span in traces if hidden(span)], [span for span in traces if not hidden(span)])


def _remove_hidden(page):
    hidden, visible = _hidden_spans(page)
    if not hidden:
        return
    if any(a.type[0] == p.PDF_ANNOT_REDACT for a in iter_annotations(page)):
        raise ValueError('该页有待应用的涂黑批注，不能同时移除文字层；请选择保留旧文字层。')
    boxes = [p.Rect(span['bbox']) for span in hidden]
    if any(box.intersects(p.Rect(span['bbox'])) for box in boxes for span in visible):
        raise ValueError('隐藏文字与可见正文交叠，无法安全单独移除；请选择保留旧文字层，或明确转图片。')
    if any(box.intersects(widget.rect) for box in boxes for widget in (page.widgets() or ())):
        raise ValueError('隐藏文字与表单交叠，不能安全移除；请选择保留旧文字层。')
    original = _render(page)
    links = {link['xref']: page.parent.xref_object(link['xref']) for link in page.get_links()}
    for box in boxes:
        if not box.is_empty:
            page.add_redact_annot(box, fill=False, cross_out=False)
    page.apply_redactions(images=p.PDF_REDACT_IMAGE_NONE, graphics=p.PDF_REDACT_LINE_ART_NONE, text=p.PDF_REDACT_TEXT_REMOVE)
    # MuPDF also drops links intersecting redactions. Restore the raw dictionaries
    # to retain rotated rectangles, named actions and cross-page destinations.
    remaining = {link['xref'] for link in page.get_links()}
    missing = [data for xref, data in links.items() if xref not in remaining]
    if missing:
        doc = page.parent
        kind, value = doc.xref_get_key(page.xref, 'Annots')
        value = doc.xref_object(int(value.split()[0])) if kind == 'xref' else value if kind == 'array' else '[]'
        references = value.strip()[1:-1]
        for data in missing:
            xref = doc.get_new_xref()
            doc.update_object(xref, data)
            references += f' {xref} 0 R'
        doc.xref_set_key(page.xref, 'Annots', f'[{references}]')
    if _hidden_spans(page)[0] or ImageChops.difference(original, _render(page)).getbbox():
        raise ValueError('该页文字层无法在保留画面的前提下完整移除；请选择保留旧文字层。')


def _private_resources(page):
    """Detach inherited/shared dictionaries before binding new image objects."""
    doc, owner = page.parent, page.xref
    seen = set()
    while owner not in seen:
        seen.add(owner)
        kind, value = doc.xref_get_key(owner, 'Resources')
        if kind != 'null':
            break
        kind, parent = doc.xref_get_key(owner, 'Parent')
        if kind != 'xref':
            value = '<<>>'
            break
        owner = int(parent.split()[0])
    resources = doc.get_new_xref()
    doc.update_object(resources, doc.xref_object(int(value.split()[0])) if kind == 'xref' and value != '<<>>' else value)
    doc.xref_set_key(page.xref, 'Resources', f'{resources} 0 R')
    kind, value = doc.xref_get_key(resources, 'XObject')
    objects = doc.get_new_xref()
    doc.update_object(objects, doc.xref_object(int(value.split()[0])) if kind == 'xref' else value if kind == 'dict' else '<<>>')
    doc.xref_set_key(resources, 'XObject', f'{objects} 0 R')
    return objects


def apply_cleanup(page, edit):
    """Called by the existing preview and subprocess save replay."""
    options = dict(edit.details)
    doc = page.parent
    mode = options['mode']
    if mode == 'annotations':
        wanted = set(options['annotations'])
        found = [a for a in iter_annotations(page) if a.info.get('id') in wanted]
        if len(found) != len(wanted) or any(a.type[0] not in ANNOT_TYPES for a in found):
            raise ValueError('所选批注已发生变化，请重新预览。')
        for annot in found:
            page.delete_annot(annot)
        return
    if mode not in ('images', 'raster'):
        raise ValueError('未知的彩色笔迹清理方式。')
    if mode == 'images':
        replacements = options['replacements']
        objects = _private_resources(page) if replacements else None
        for name, digest, data in replacements:
            candidates = [item for item in page.get_images(full=True) if item[7] == name and item[9] == 0]
            if len(candidates) != 1:
                raise ValueError('页面图片结构已改变，请重新预览。')
            old = candidates[0][0]
            original = _picture(page, old, allow_gray=True)
            if sha256(original.tobytes()).hexdigest() != digest:
                raise ValueError('页面图片已改变，请重新预览。')
            # Never replace_image(old): its replacement would affect every shared page.
            clone = doc.get_new_xref()
            doc.update_object(clone, doc.xref_object(old))
            with Image.open(BytesIO(base64.b64decode(data, validate=True))) as replacement:
                if replacement.mode != original.mode or replacement.size != original.size:
                    raise ValueError('替换图片的尺寸或颜色格式无效。')
                pixels = replacement.tobytes()
            if original.mode == 'L': doc.xref_set_key(clone, 'BitsPerComponent', '8')
            # Retain the original color profile, interpolation and placement. Only
            # the losslessly decoded pixel data changes; JPEG predictors do not carry over.
            doc.xref_set_key(clone, 'Filter', 'null')
            doc.xref_set_key(clone, 'DecodeParms', 'null')
            doc.update_stream(clone, pixels, compress=True)
            doc.xref_set_key(objects, name, f'{clone} 0 R')
        if options['ocr'] == 'remove':
            _remove_hidden(page)
    else:
        _private_resources(page)
        contents = doc.get_new_xref()
        doc.update_object(contents, '<<>>')
        doc.update_stream(contents, b'')
        doc.xref_set_key(page.xref, 'Contents', f'{contents} 0 R')
        rotation = page.rotation
        try:
            page.set_rotation(0)
            page.insert_image(page.rect, stream=base64.b64decode(options['image'], validate=True), keep_proportion=False)
        finally:
            page.set_rotation(rotation)
    page._image_info = None


def resolve_cleanup_mode(store, ref, mode='auto', cancel=None):
    if mode != 'auto':
        return mode
    with store._render_page(ref, cancel) as page:
        if page.get_image_info():
            return 'images'
        return 'annotations' if any(a.type[0] in ANNOT_TYPES for a in iter_annotations(page)) else 'images'


def plan_page(store, ref, settings=CleanupSettings(), zones=CleanupZones(), *, mode='images',
              selected=(), ocr='ask', preview=False, preview_width=1100, cancel=None):
    """Build a self-contained immutable edit, without changing the source or workspace."""
    settings.validate()
    zones.validate()
    mode = resolve_cleanup_mode(store, ref, mode, cancel)
    store.require_operation([ref], 'copy')
    store.require_operation([ref], 'annotate' if mode == 'annotations' else 'content_edit')
    if ocr not in ('ask', 'keep', 'remove'):
        raise ValueError('请选择旧文字层的处理方式。')
    with store._render_page(ref, cancel) as source, p.open() as draft:
        draft.insert_pdf(source.parent, from_page=source.number, to_page=source.number)
        page = draft[0]
        page.set_rotation((source.rotation + ref.rotation) % 360)
        check_cancel(cancel)
        before = _render(page, preview_width) if preview else None
        candidates = _annotation_candidates(page, settings, zones) if mode == 'annotations' else []
        hidden = bool(_hidden_spans(page)[0])
        replacements, changed_pixels = [], 0
        options = dict(mode=mode, ocr=ocr)
        message = ''
        if mode == 'annotations':
            allowed = {a['id'] for a in candidates}
            if set(selected) - allowed:
                raise ValueError('所选批注不在当前颜色、局部范围或保护区之外，请重新筛选。')
            options['annotations'] = tuple(selected)
            changed_pixels = len(selected)
            message = f'可选批注 {len(candidates)} 项，已选 {len(selected)} 项；正文、链接与表单保留。'
        elif mode == 'images':
            infos = page.get_image_info(xrefs=True)
            images = page.get_images(full=True)
            if not infos:
                raise ValueError('该页没有可独立替换的扫描图片。压平矢量笔迹请明确选择“选中页转图片”。')
            if any(not info['xref'] for info in infos) or any(item[9] != 0 for item in images):
                raise ValueError('该页含内联图片或嵌套图形，暂不能安全替换；可明确选择“选中页转图片”。')
            for info in infos:
                check_cancel(cancel)
                manual = bool(zones.erased or zones.erased_strokes or zones.erased_ellipses)
                if info['colorspace'] == 1 and not manual:
                    # A grayscale image cannot contain the selected chromatic ink.
                    continue
                xref = info['xref']
                names = [item[7] for item in images if item[0] == xref and item[9] == 0]
                if len(names) != 1 or sum(i['xref'] == xref for i in infos) != 1:
                    raise ValueError('同页重复使用了同一图片，暂不能单独映射清理范围；可明确选择转图片。')
                if not re.fullmatch(r'[A-Za-z0-9_.-]+', names[0]):
                    raise ValueError('该页使用了特殊图片资源名称，暂不能安全替换；可明确选择转图片。')
                image = _picture(page, xref, allow_gray=manual)
                placement = p.Matrix(info['transform'])
                if not all(math.isfinite(v) for v in placement) or abs(placement.a * placement.d - placement.b * placement.c) < 1e-8:
                    raise ValueError('图片放置坐标无效，不能安全映射清理范围。')
                transform = page.derotation_matrix * ~placement * p.Matrix(image.width, image.height)
                allowed, _ = zone_masks(image.size, zones, transform, (page.rect.width, page.rect.height))
                erased = erasure_mask(image.size, zones, transform, (page.rect.width, page.rect.height)) if (zones.erased or zones.erased_strokes or zones.erased_ellipses) else None
                cleaned, mask = clean_image(image, settings, allowed=allowed, erase=erased, cancel=cancel)
                if mask.getbbox():
                    if image.mode == 'L': cleaned = cleaned.convert('L')
                    replacements.append((names[0], sha256(image.tobytes()).hexdigest(), _png(cleaned)))
                    changed_pixels += mask.histogram()[255]
            options['replacements'] = tuple(replacements)
            if hidden and changed_pixels and ocr == 'ask' and not preview:
                raise ValueError('检测到 OCR / 隐藏文字层，请明确选择保留或移除；保留时旧答案仍可能被搜索和复制。')
            message = f'识别到 {changed_pixels:,} 个图片像素；原生文字与矢量内容保留。'
            if hidden:
                message += ' 检测到隐藏文字层：' + ('将移除处理页的整页隐藏文字。' if ocr == 'remove' else '旧答案可能仍可搜索或复制，请选择文字层处理方式。')
        elif mode == 'raster':
            if page.first_widget:
                raise ValueError('含表单的页暂不支持转图片清理；请使用原生批注或图片清理方式。')
            scale = min(300 / 72, 4200 / max(page.rect.width, page.rect.height))
            pix = page.get_pixmap(matrix=p.Matrix(scale, scale), colorspace=p.csRGB, alpha=False, annots=False)
            image = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
            allowed, _ = zone_masks(image.size, zones)
            erased = erasure_mask(image.size, zones) if (zones.erased or zones.erased_strokes or zones.erased_ellipses) else None
            cleaned, mask = clean_image(image, settings, allowed=allowed, erase=erased, cancel=cancel)
            changed_pixels = mask.histogram()[255]
            if page.rotation:
                cleaned = cleaned.rotate(page.rotation, expand=True)
            options['image'] = _png(cleaned)
            message = '仅选中页转为图片，原生文字、矢量及 OCR 文字层不保留；链接与独立批注保留。'
        else:
            raise ValueError('请选择清理方式。')
        edit = PageEdit('color_cleanup', details=tuple(options.items())) if changed_pixels else None
        after = before
        if preview and edit:
            from app.pdfedits import apply_edits
            apply_edits(page, (edit,))
            after = _render(page, preview_width)
        check_cancel(cancel)
        mask = None
        if preview:
            diff = ImageChops.difference(before, after)
            r, g, b = diff.split()
            mask = ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda v: 255 if v else 0)
        return dict(edit=edit, before=before, after=after, mask=mask, candidates=candidates,
                    hidden=hidden, changed=changed_pixels, message=message, mode=mode)


def prepare_cleanup(store, pages, indices, settings=CleanupSettings(), *, zones=None, mode='images',
                    selected=None, ocr='ask', progress=None, cancel=None):
    indices = list(dict.fromkeys(indices))
    if not indices or any(type(i) is not int or not 0 <= i < len(pages) for i in indices):
        raise ValueError('请选择有效的处理页码。')
    changes = {}
    for step, row in enumerate(indices):
        check_cancel(cancel)
        if progress:
            progress(step, len(indices), f'正在清理第 {row + 1} 页…')
        try:
            result = plan_page(store, pages[row], settings, (zones or {}).get(row, CleanupZones()),
                mode=mode, selected=(selected or {}).get(row, ()), ocr=ocr, cancel=cancel)
        except ValueError as exc:
            raise ValueError(f'第 {row + 1} 页：{exc}') from exc
        if result['edit']:
            # Rehearse every edit before returning any workspace changes.
            prepared = pages[row].edited(result['edit'])
            with store._render_page(prepared, cancel):
                pass
            changes[row] = prepared
    check_cancel(cancel)
    if progress:
        progress(len(indices), len(indices), '清理页面已准备好')
    return changes
