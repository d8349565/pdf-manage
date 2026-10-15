"""Editable image stamps with one shared PDF image object per insertion."""
import base64
import io
import json
import math

IMAGE_SETTINGS = 'ZhiXuImage'
IMAGE_SOURCE = 'ZhiXuImageSource'
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000


def validate_image(data):
    from PIL import Image, UnidentifiedImageError
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError('请选择 10 MB 内的 PNG 或 JPEG 图片。')
    try:
        with Image.open(io.BytesIO(data)) as picture:
            if picture.format not in ('PNG', 'JPEG'):
                raise ValueError('仅支持 PNG 和 JPEG 图片。')
            width, height = picture.size
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                raise ValueError('图片超过 2000 万像素，请先缩小图片。')
            picture.verify()
        return width, height
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError('图片损坏或像素过多，请换一张图片。') from exc


def image_bounds(settings):
    angle = math.radians(settings['angle'])
    width, height = settings['width'], settings['height']
    w = abs(width * math.cos(angle)) + abs(height * math.sin(angle))
    h = abs(width * math.sin(angle)) + abs(height * math.cos(angle))
    return (settings['cx'] - w / 2, settings['cy'] - h / 2,
            settings['cx'] + w / 2, settings['cy'] + h / 2)


def fit_image(settings, bounds):
    """Keep the entire rotated image on the page, preserving its chosen ratio."""
    result = dict(settings)
    if not all(math.isfinite(float(result[k])) for k in ('cx', 'cy', 'width', 'height', 'angle')):
        raise ValueError('图片位置、尺寸或角度无效。')
    result['width'], result['height'] = max(2, result['width']), max(2, result['height'])
    result['angle'] = (result['angle'] + 180) % 360 - 180
    x0, y0, x1, y1 = bounds
    if x1 <= x0 or y1 <= y0:
        raise ValueError('页面没有可放置图片的空间。')
    left, top, right, bottom = image_bounds(result)
    scale = min(1, (x1 - x0) / (right - left), (y1 - y0) / (bottom - top))
    result['width'] *= scale
    result['height'] *= scale
    left, top, right, bottom = image_bounds(result)
    half_w, half_h = (right - left) / 2, (bottom - top) / 2
    result['cx'] = max(x0 + half_w, min(x1 - half_w, result['cx']))
    result['cy'] = max(y0 + half_h, min(y1 - half_h, result['cy']))
    return result


def image_settings(page, annot):
    if not annot.info.get('id', '').startswith('zhixu-image-'):
        return None
    try:
        kind, value = page.parent.xref_get_key(annot.xref, IMAGE_SETTINGS)
        if kind != 'string':
            return None
        settings = json.loads(value)
        if (not isinstance(settings, dict) or settings.get('version') != 1
                or not all(isinstance(settings.get(k), (int, float)) and math.isfinite(settings[k])
                           for k in ('cx', 'cy', 'width', 'height', 'angle'))
                or not 0 < settings['width'] <= 100000 or not 0 < settings['height'] <= 100000):
            return None
        source_kind, source = page.parent.xref_get_key(annot.xref, IMAGE_SOURCE)
        if source_kind != 'xref' or page.parent.xref_get_key(int(source.split()[0]), 'Subtype') != ('name', '/Image'):
            return None
        return settings
    except (ValueError, TypeError, RuntimeError, KeyError):
        return None


def detach_image_resource(page, source, previous_names):
    """The stamp owns this image; don't retain a second unused page-resource root."""
    doc = page.parent
    names = [item[7] for item in page.get_images() if item[0] == source and item[7] not in previous_names]
    kind, value = doc.xref_get_key(page.xref, 'Resources')
    owner, prefix = (int(value.split()[0]), '') if kind == 'xref' else (page.xref, 'Resources/')
    kind, value = doc.xref_get_key(owner, prefix + 'XObject')
    if kind == 'xref':
        owner, prefix = int(value.split()[0]), ''
    else:
        prefix += 'XObject/'
    for name in names:
        doc.xref_set_key(owner, prefix + name, 'null')


def apply_image(page, edit):
    import pymupdf as p
    document = page.parent
    previous = next((a for a in (page.annots() or ()) if a.info.get('id') == edit.annotation_id), None)
    if edit.kind != 'image' and (previous is None or image_settings(page, previous) is None):
        raise ValueError('要修改的图片已不存在。')
    if edit.kind == 'image_delete':
        page.delete_annot(previous)
        return
    options = dict(edit.details)
    settings = {key: options[key] for key in ('cx', 'cy', 'width', 'height', 'angle')}
    settings.update(version=1, locked=bool(options.get('locked', True)))
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for k, v in settings.items() if k != 'locked'):
        raise ValueError('图片位置、尺寸或角度无效。')
    bounds = page.rect * page.derotation_matrix
    area = p.Rect(image_bounds(settings))
    if (settings['width'] <= 0 or settings['height'] <= 0 or area.is_empty
            or not (bounds + (-.01, -.01, .01, .01)).contains(area)):
        raise ValueError('图片须完整放在页面内。')
    rotation = page.rotation
    try:
        page.set_rotation(0)
        if edit.kind == 'image':
            if previous is not None:
                raise ValueError('图片标识重复，请重新插入。')
            data = base64.b64decode(options.get('image', ''), validate=True)
            validate_image(data)
            contents = document.xref_get_key(page.xref, 'Contents')[1]
            previous_names = {item[7] for item in page.get_images()}
            try:
                source = page.insert_image(area, stream=data, keep_proportion=False)
            finally:
                document.xref_set_key(page.xref, 'Contents', contents)
            detach_image_resource(page, source, previous_names)
            annot = page.add_stamp_annot(area)
        else:
            source = int(document.xref_get_key(previous.xref, IMAGE_SOURCE)[1].split()[0])
            annot = previous
        if annot.set_rect(area) is False:
            raise ValueError('图片位置无法应用。')
        annot.set_info(title='纸序 PDF 图片', content='插入的图片')
        annot.set_flags(annot.flags | p.PDF_ANNOT_IS_PRINT)
        annot.update()
        document.xref_set_key(annot.xref, 'NM', p.get_pdf_str(edit.annotation_id))
        document.xref_set_key(annot.xref, IMAGE_SETTINGS, p.get_pdf_str(json.dumps(settings)))
        # An indirect reference survives save-time garbage collection and renumbering.
        document.xref_set_key(annot.xref, IMAGE_SOURCE, f'{source} 0 R')
        appearance = document.get_new_xref()
        w, h = area.width, area.height
        document.update_object(appearance,
            f'<< /Type /XObject /Subtype /Form /BBox [0 0 {w:.8f} {h:.8f}] '
            f'/Resources << /XObject << /Im {source} 0 R >> >> >>')
        angle = math.radians(settings['angle'])
        a, b = settings['width'] * math.cos(angle), -settings['width'] * math.sin(angle)
        c, d = settings['height'] * math.sin(angle), settings['height'] * math.cos(angle)
        x, y = (w - a - c) / 2, (h - b - d) / 2
        document.update_stream(appearance,
            f'q {a:.8f} {b:.8f} {c:.8f} {d:.8f} {x:.8f} {y:.8f} cm /Im Do Q'.encode('ascii'))
        document.xref_set_key(annot.xref, 'AP/N', f'{appearance} 0 R')
    finally:
        page.set_rotation(rotation)
