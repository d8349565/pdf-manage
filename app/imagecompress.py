"""Fast standalone image compression. Always write a new file beside the source."""
from dataclasses import dataclass
from io import BytesIO
from math import isfinite
from pathlib import Path
import os
import tempfile
import struct

from PIL import Image, ImageFile, ImageOps, JpegImagePlugin, PngImagePlugin

from app.output import check_cancel, commit_file, stamp


IMAGE_SUFFIXES = ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff')
MAX_PIXELS = 64_000_000


@dataclass(frozen=True)
class ImageCompressionResult:
    source: str
    path: str | None
    before: int
    after: int
    description: str = ''


def available_path(source, suffix, *, tag='', folder=None):
    source = Path(source)
    parent = Path(folder) if folder else source.parent
    target = parent / (source.stem + tag + suffix)
    number = 2
    while target.exists():
        target = parent / f'{source.stem}{tag} ({number}){suffix}'
        number += 1
    return target


def _webp_metadata(image, metadata):
    """WebP stores resolution in EXIF rather than PNG's pHYs chunk."""
    result = {key: metadata[key] for key in ('icc_profile', 'exif', 'xmp') if metadata.get(key)}
    dpi = metadata.get('dpi')
    if dpi and len(dpi) == 2 and all(isinstance(value, (int, float)) and isfinite(value) and value > 0 for value in dpi):
        exif = image.getexif()
        exif.setdefault(282, dpi[0])
        exif.setdefault(283, dpi[1])
        exif.setdefault(296, 2)
        result['exif'] = exif.tobytes()
    return result


def compress_image(source, *, folder=None, progress=None, cancel=None, compact=False, smaller_than=None):
    """Compare lossless PNG/WebP, or explicitly request a smaller lossy WebP.

    PNG-specific color/text metadata stays in PNG in the automatic path.
    ``smaller_than`` avoids an extra output larger than an earlier result.
    """
    source = Path(source).resolve()
    if source.suffix.lower() not in IMAGE_SUFFIXES or not source.is_file():
        raise ValueError('请选择 JPG、PNG、WebP、BMP 或 TIFF 图片。')
    original = stamp(source)
    check_cancel(cancel)
    if progress:
        progress(0, 0, '正在读取图片…')
    with Image.open(source) as opened:
        if opened.format not in ('JPEG', 'PNG', 'WEBP', 'BMP', 'TIFF'):
            raise ValueError('这张图片的实际格式暂不支持压缩。')
        if getattr(opened, 'n_frames', 1) > 1:
            raise ValueError('多帧或动态图片暂不直接压缩，原图保留。')
        if opened.width * opened.height > MAX_PIXELS:
            raise ValueError('图片超过 6400 万像素，请先缩小尺寸后再压缩。')
        kind = opened.format
        sampling = JpegImagePlugin.get_sampling(opened) if kind == 'JPEG' else 0
        image = ImageOps.exif_transpose(opened)
        image.load()
        info = dict(image.info)
        png_only_info = kind == 'PNG' and bool(getattr(opened, 'text', {}) or
                        any(key in info for key in ('gamma', 'chromaticity', 'srgb')))
        metadata = {key: info[key] for key in ('icc_profile', 'exif', 'dpi', 'xmp') if info.get(key)}
        if kind == 'PNG' and 'transparency' in info:
            metadata['transparency'] = info['transparency']
        if kind == 'PNG':
            png_info = PngImagePlugin.PngInfo()
            for key in getattr(opened, 'text', {}):
                if isinstance(info.get(key), str):
                    png_info.add_text(key, info[key])
            if 'gamma' in info:
                png_info.add(b'gAMA', struct.pack('>I', round(info['gamma'] * 100000)))
            if 'chromaticity' in info:
                png_info.add(b'cHRM', struct.pack('>8I', *(round(value * 100000) for value in info['chromaticity'])))
            if 'srgb' in info:
                png_info.add(b'sRGB', bytes([info['srgb']]))
            metadata['pnginfo'] = png_info
    check_cancel(cancel)
    if progress:
        progress(0, 0, '正在压缩图片，保留原尺寸…')
    check_cancel(cancel)
    encoded = BytesIO()
    if compact:
        if image.mode not in ('RGB', 'RGBA', 'L', 'LA', 'P', '1') or (image.mode not in ('RGB', 'RGBA') and metadata.get('icc_profile')):
            raise ValueError('这张图片的颜色模式不适合转为 WebP，请使用默认压缩。')
        if max(image.size) > 16383:
            raise ValueError('图片单边超过 WebP 的 16383 像素限制，请使用默认压缩。')
        if png_only_info and not metadata.get('icc_profile') and any(key in info for key in ('gamma', 'chromaticity', 'srgb')):
            raise ValueError('这张 PNG 包含独立颜色信息，请使用默认压缩保留颜色。')
        image.save(encoded, 'WEBP', quality=95, method=3, exact=True,
                   **_webp_metadata(image, metadata))
        suffix, description = '.webp', 'WebP · 有损压缩，尺寸不变'
    elif kind == 'JPEG':
        # Preserve source chroma sampling; the optimized RGB buffer can be
        # larger than Pillow's estimate for noisy 4:4:4 images.
        old_block = ImageFile.MAXBLOCK
        try:
            ImageFile.MAXBLOCK = max(old_block, 4 * image.width * image.height + 65536)
            image.save(encoded, 'JPEG', quality=85, optimize=True, progressive=True,
                       subsampling=sampling if sampling in (0, 1, 2) else 0, **metadata)
        finally:
            ImageFile.MAXBLOCK = old_block
        suffix = '.jpg' if source.suffix.lower() != '.jpeg' else '.jpeg'
        description = 'JPG · 有损压缩，尺寸不变'
    elif kind == 'WEBP':
        metadata.pop('dpi', None)
        image.save(encoded, 'WEBP', quality=85, method=3, **metadata)
        suffix = '.webp'
        description = 'WebP · 有损压缩，尺寸不变'
    elif kind == 'TIFF':
        image.save(encoded, 'TIFF', compression='tiff_adobe_deflate', **metadata)
        suffix = '.tiff' if source.suffix.lower() == '.tiff' else '.tif'
        description = 'TIFF · 无损优化，尺寸不变'
    else:
        if image.mode not in ('1', 'L', 'LA', 'P', 'RGB', 'RGBA', 'I', 'I;16'):
            raise ValueError('这张图片的颜色模式暂不支持直接压缩，原图保留。')
        image.save(encoded, 'PNG', optimize=True, compress_level=9, **metadata)
        if image.mode == 'RGB' and 'transparency' not in metadata:
            colors = image.getcolors(256)
            if colors:
                indexed = image.quantize(colors=len(colors), dither=Image.Dither.NONE)
                if indexed.convert('RGB').tobytes() == image.tobytes():
                    palette = BytesIO()
                    indexed.save(palette, 'PNG', optimize=True, compress_level=9, **metadata)
                    if palette.tell() < encoded.tell():
                        encoded = palette
        suffix = '.png'
        description = 'PNG · 无损优化，像素保留'
        if image.mode in ('RGB', 'RGBA') and max(image.size) <= 16383 and not png_only_info:
            check_cancel(cancel)
            if progress:
                progress(0, 0, '正在比较 PNG 与无损 WebP…')
            check_cancel(cancel)
            webp = BytesIO()
            try:
                image.save(webp, 'WEBP', lossless=True, quality=75, method=3, exact=True,
                           **_webp_metadata(image, metadata))
            except (OSError, ValueError):
                pass  # The successful PNG candidate remains usable.
            else:
                if webp.tell() < encoded.tell():
                    encoded, suffix = webp, '.webp'
                    description = 'WebP · 无损压缩，像素保留'
    check_cancel(cancel)
    if stamp(source) != original:
        raise OSError('处理期间原图已被其他程序修改，请重新尝试。')
    data = encoded.getvalue()
    if len(data) >= min(original[2], smaller_than if smaller_than is not None else original[2]):
        return ImageCompressionResult(str(source), None, original[2], original[2])
    target = available_path(source, suffix, tag='_更小' if compact else '_压缩', folder=folder)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix='.zhixu-image-', suffix=suffix, dir=target.parent)
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
        check_cancel(cancel)
        if stamp(source) != original:
            raise OSError('处理期间原图已被其他程序修改，已停止输出。')
        commit_file(temporary, target, None, cancel=cancel)
        temporary = None
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
    return ImageCompressionResult(str(source), str(target), original[2], len(data), description)
