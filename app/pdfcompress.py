"""Choose smaller PDF-standard image encodings, retaining the original on a tie."""
from io import BytesIO
import math
import re
import struct

from PIL import Image, ImageFile, features

from app.output import check_cancel


MAX_PIXELS = 20_000_000


def _png(image):
    """Use Pillow's PNG row predictors directly in a PDF Flate image stream."""
    output = BytesIO()
    image.save(output, 'PNG', optimize=True)
    data = output.getvalue()
    chunks, palette, position = [], None, 8
    while position < len(data):
        size = struct.unpack_from('>I', data, position)[0]
        kind = data[position + 4:position + 8]
        value = data[position + 8:position + 8 + size]
        if kind == b'IHDR':
            width, height, bits, color, *_ = struct.unpack('>IIBBBBB', value)
        elif kind == b'IDAT':
            chunks.append(value)
        elif kind == b'PLTE':
            palette = value
        position += size + 12
    colors = 3 if color == 2 else 1
    space = '/DeviceRGB' if color == 2 else '/DeviceGray'
    if color == 3:
        space = f'[/Indexed /DeviceRGB {len(palette) // 3 - 1} <{palette.hex()}>]'
    return dict(Filter='/FlateDecode', ColorSpace=space, BitsPerComponent=str(bits),
                DecodeParms=f'<< /Predictor 15 /Colors {colors} /Columns {width} /BitsPerComponent {bits} >>',
                Width=str(width), Height=str(height)), b''.join(chunks)


def _fax(image):
    output = BytesIO()
    image.save(output, 'TIFF', compression='group4')
    data = output.getvalue()
    with Image.open(BytesIO(data)) as tiff:
        offsets, counts = tiff.tag_v2[273], tiff.tag_v2[279]
        if len(offsets) != 1:
            return None
        # TIFF WhiteIsZero corresponds to PDF BlackIs1; preserve the pixel polarity.
        black = 'true' if tiff.tag_v2[262] == 0 else 'false'
    return dict(Filter='/CCITTFaxDecode', ColorSpace='/DeviceGray', BitsPerComponent='1',
                Width=str(image.width), Height=str(image.height),
                DecodeParms=f'<< /K -1 /Columns {image.width} /Rows {image.height} /BlackIs1 {black} >>'), data[offsets[0]:offsets[0] + counts[0]]


def _cost(candidate):
    keys, stream = candidate
    return len(stream) + sum(len(key) + len(value) + 2 for key, value in keys.items())


def _lossless_candidates(image, *, allow_jpx):
    yield _png(image)
    colors = image.getcolors(256)
    if image.mode == 'L' and colors and all(color in (0, 255) for _, color in colors):
        mono = image.convert('1', dither=Image.Dither.NONE)
        yield _png(mono)
        try:
            fax = _fax(mono)
        except OSError:
            fax = None
        if fax:
            yield fax
    elif image.mode == 'RGB' and colors:
        indexed = image.quantize(colors=len(colors), dither=Image.Dither.NONE)
        # An exact palette is allowed; approximate color quantization is never lossless.
        if indexed.convert('RGB').tobytes() == image.tobytes():
            yield _png(indexed)
    elif allow_jpx and min(image.size) >= 32 and features.check_codec('jpg_2000'):
        output = BytesIO()
        try:
            image.save(output, 'JPEG2000', irreversible=False, no_jp2=True)
        except OSError:
            return  # The already-yielded Flate candidate and original remain usable.
        yield dict(Filter='/JPXDecode', DecodeParms='null', BitsPerComponent='8',
                   ColorSpace='/DeviceRGB' if image.mode == 'RGB' else '/DeviceGray',
                   Width=str(image.width), Height=str(image.height)), output.getvalue()


def optimize_images(doc, *, dpi=None, quality=92, preserve_lossless=False, progress=None, cancel=None):
    """Optimize ordinary gray/RGB XObjects without flattening pages or changing masks."""
    import pymupdf
    used = {}
    for page in doc:
        check_cancel(cancel)
        for info in page.get_image_info(xrefs=True):
            xref = info.get('xref', 0)
            if not xref:
                continue  # Inline images keep their original representation.
            transform = info['transform']
            resolution = min(72 * info['width'] / max(.001, math.hypot(*transform[:2])),
                             72 * info['height'] / max(.001, math.hypot(*transform[2:4])))
            # Shared images must remain sharp at their largest displayed size.
            used[xref] = min(used.get(xref, math.inf), resolution)
    changed = 0
    for index, (xref, resolution) in enumerate(used.items()):
        check_cancel(cancel)
        if progress:
            progress(index, len(used), f'逐张优化图片 · {index + 1} / {len(used)}')
        keys = {key: doc.xref_get_key(xref, key)[1] for key in doc.xref_get_keys(xref)}
        space = keys.get('ColorSpace')
        resolved_space = space or ''
        if re.fullmatch(r'\d+ 0 R', resolved_space):
            resolved_space = doc.xref_object(int(resolved_space.split()[0]))
        icc = re.fullmatch(r'\[\s*/ICCBased\s+(\d+)\s+0\s+R\s*\]', resolved_space)
        ordinary_space = resolved_space in ('/DeviceRGB', '/DeviceGray') or (
            icc and doc.xref_get_key(int(icc[1]), 'N')[1] in ('1', '3'))
        if (not ordinary_space or keys.get('BitsPerComponent') not in ('1', '8')
                or any(keys.get(key, 'null') not in ('null', 'false', '0')
                       for key in ('SMask', 'Mask', 'ImageMask', 'SMaskInData', 'Decode'))):
            continue
        width, height = int(keys['Width']), int(keys['Height'])
        if not 0 < width * height <= 100_000_000:
            continue
        large = width * height > MAX_PIXELS
        ratio = dpi / resolution if dpi and resolution else 1
        if large and (keys['BitsPerComponent'] == '1' or ratio >= 1 / 1.2
                      or max(1, round(width * ratio)) * max(1, round(height * ratio)) > MAX_PIXELS):
            continue
        raw = doc.xref_stream_raw(xref)
        if not raw or len(raw) < 1024:
            continue
        pixmap = pymupdf.Pixmap(doc, xref)
        if pixmap.alpha or pixmap.n not in (1, 3):
            continue
        mode = 'RGB' if pixmap.n == 3 else 'L'
        # Large scans go straight through native downsampling before any Pillow copy.
        # In lossless mode, keep them unchanged rather than allocate an unbounded buffer.
        if large and pixmap.is_monochrome:
            continue
        image = None if large else Image.frombytes(mode, (width, height), pixmap.samples)
        original_filter = keys.get('Filter', '')
        encoded_lossy = 'DCTDecode' in original_filter or 'JPXDecode' in original_filter
        exact_colors = image.getcolors(2) if image is not None else None
        bitonal = keys['BitsPerComponent'] == '1' or (exact_colors is not None and all(
            color in (0, 255, (0, 0, 0), (255, 255, 255)) for _, color in exact_colors))
        resize = dpi is not None and not bitonal and resolution > dpi * 1.2
        working = image
        if resize:
            ratio = dpi / resolution
            # Use the same native resampling as the previous compressor. Encoding
            # changes must not secretly trade chroma detail or sharpness for bytes.
            scaled = pymupdf.Pixmap(pixmap, max(1, round(width * ratio)), max(1, round(height * ratio)))
            if scaled.alpha:
                scaled = pymupdf.Pixmap(scaled, 0)
            working = Image.frombytes(mode, (scaled.width, scaled.height), scaled.samples)
            del scaled
        del pixmap
        best = None
        # Preserve a lossless alternative even when the selected DPI could reduce detail.
        for source in (image, working) if resize and image is not None else (working,):
            for candidate in _lossless_candidates(source, allow_jpx=not encoded_lossy and len(raw) >= 64_000):
                check_cancel(cancel)
                # Preserve the original ICC profile, including the base of an exact palette.
                if candidate[0]['ColorSpace'].startswith('[/Indexed'):
                    candidate[0]['ColorSpace'] = candidate[0]['ColorSpace'].replace('/DeviceRGB', space, 1)
                else:
                    candidate[0]['ColorSpace'] = space
                if best is None or _cost(candidate) < _cost(best):
                    best = candidate
        if dpi is not None and not bitonal and (encoded_lossy or not preserve_lossless):
            # Pillow underestimates optimized RGB 4:4:4 output for noisy images.
            # Compression runs in one PDF worker; restore the library setting afterwards.
            old_block = ImageFile.MAXBLOCK
            try:
                ImageFile.MAXBLOCK = max(old_block, 4 * working.width * working.height + 65536)
                for progressive in (False, True):
                    output = BytesIO()
                    working.save(output, 'JPEG', quality=quality, optimize=True, progressive=progressive,
                                 subsampling=0)
                    candidate = (dict(Filter='/DCTDecode', DecodeParms='null', BitsPerComponent='8',
                                      ColorSpace=space, Width=str(working.width), Height=str(working.height)), output.getvalue())
                    if _cost(candidate) < _cost(best):
                        best = candidate
            finally:
                ImageFile.MAXBLOCK = old_block
        old_cost = len(raw) + sum(len(key) + len(keys.get(key, 'null')) + 2 for key in best[0])
        if _cost(best) >= old_cost:
            continue
        doc.update_stream(xref, best[1], compress=False)
        for key, value in best[0].items():
            doc.xref_set_key(xref, key, value)
        changed += 1
    return changed
