"""Read-only, bounded PDF visual comparison. No Qt, OCR or reading-colour transforms.

CLI: python -m app.pdfcompare old.pdf new.pdf --output comparison --preview 1
Coordinates are raster pixels at the recorded scale, not PDF text coordinates.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import re
import time

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat
import pymupdf

from app.output import check_cancel, stamp


@dataclass(frozen=True)
class CompareOptions:
    dpi: int = 110
    threshold: int = 24
    blur: float = .5
    min_pixels: int = 8
    align_pixels: int = 0  # opt-in; a genuine whole-page move can be suppressed
    annotations: bool = True
    pairing: str = 'auto'
    max_pages: int = 200
    max_pixels: int = 2_000_000

    def validate(self):
        if not (36 <= self.dpi <= 200 and 0 <= self.threshold <= 254
                and 0 <= self.blur <= 1.5 and 1 <= self.min_pixels <= 100
                and 0 <= self.align_pixels <= 4 and self.pairing in ('auto', 'index')
                and 1 <= self.max_pages <= 300 and 10_000 <= self.max_pixels <= 4_000_000):
            raise ValueError('比较参数超出原型安全范围')


COLORS = {'old': (224, 55, 85), 'new': (0, 153, 145), 'changed': (232, 139, 20)}


@contextmanager
def _open_pdf(path, password=''):
    doc = pymupdf.open(path)
    try:
        if not doc.is_pdf or not doc.page_count:
            raise ValueError('请选择至少包含一页的 PDF')
        authenticated = doc.authenticate(password or '')
        if doc.is_encrypted:
            raise ValueError('PDF 需要正确的打开密码')
        # Fingerprinting extracts text and raster data: follow the existing copy policy.
        if not authenticated & 4 and not doc.permissions & pymupdf.PDF_PERM_COPY:
            raise ValueError('当前密码不允许复制内容，请使用有权限的密码')
        yield doc
    finally:
        doc.close()


def _render(page, scale, annotations):
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                          colorspace=pymupdf.csRGB, alpha=False, annots=annotations)
    return Image.frombytes('RGB', (pix.width, pix.height), pix.samples)


def _signature(page, options):
    size = (float(page.rect.width), float(page.rect.height))
    image = _render(page, min(64 / max(size), 1), options.annotations)
    image.thumbnail((64, 64), Image.Resampling.LANCZOS)
    thumb = Image.new('RGB', (64, 64), 'white')
    thumb.paste(image, (0, 0))
    text = page.get_text('text')[:8000].casefold()
    tokens = Counter(re.findall(r'\w+', text))
    heading = next((line for line in text.splitlines() if line.strip()), '')[:300]
    ink = sum(255 * 64 * 64 - value for value in ImageStat.Stat(thumb).sum)
    return dict(size=size, image=thumb, tokens=tokens, token_total=sum(tokens.values()),
                heading=Counter(re.findall(r'\w+', heading)), ink=ink)


def _pair_cost(a, b):
    difference = sum(ImageStat.Stat(ImageChops.difference(a['image'], b['image'])).sum)
    ink = a['ink'] + b['ink']
    visual = min(1., difference / max(1., ink))
    ta, tb = a['tokens'], b['tokens']
    if ta and tb:
        common = sum(min(count, tb.get(token, 0)) for token, count in ta.items())
        similarity = common / max(1, a['token_total'] + b['token_total'] - common)
        ha, hb = a['heading'], b['heading']
        # Shared boilerplate must not outweigh a distinctive page title when a
        # genuinely modified page sits next to a newly inserted, similar template.
        heading_similarity = sum((ha & hb).values()) / max(1, sum((ha | hb).values()))
        text_cost = .6 * (1 - heading_similarity) + .4 * (1 - similarity)
        cost = .7 * text_cost + .3 * visual
    else:
        cost = visual
    size_error = sum(abs(x - y) / max(x, y, 1) for x, y in zip(a['size'], b['size'])) / 2
    return min(1., cost + .3 * size_error)


def pair_pages(old, new, *, mode='auto', cancel=None, manual_pairs=()):
    """Monotone sequence alignment; bounded O(N*M), no claim of semantic alignment."""
    n, m = len(old), len(new)
    if manual_pairs:
        anchors = sorted(tuple(pair) for pair in manual_pairs)
        previous_old = previous_new = 0
        pairs = []
        for old_page, new_page in anchors:
            if (type(old_page) is not int or type(new_page) is not int
                    or not previous_old < old_page <= n
                    or not previous_new < new_page <= m):
                raise ValueError('手工配对须在页数范围内，且两版页码都按顺序递增，不能重复或交叉')
            segment = pair_pages(old[previous_old:old_page - 1], new[previous_new:new_page - 1],
                                 mode=mode, cancel=cancel)
            pairs.extend((a + previous_old if a is not None else None,
                          b + previous_new if b is not None else None, cost) for a, b, cost in segment)
            pairs.append((old_page - 1, new_page - 1, None))
            previous_old, previous_new = old_page, new_page
        segment = pair_pages(old[previous_old:], new[previous_new:], mode=mode, cancel=cancel)
        pairs.extend((a + previous_old if a is not None else None,
                      b + previous_new if b is not None else None, cost) for a, b, cost in segment)
        return pairs
    if mode == 'index':
        return [(i if i < n else None, i if i < m else None, None)
                for i in range(max(n, m))]
    gap = .32
    previous = [j * gap for j in range(m + 1)]
    trace = [bytearray(m + 1) for _ in range(n + 1)]
    trace[0][:] = bytes([2]) * (m + 1)
    for i in range(1, n + 1):
        check_cancel(cancel)
        current = [i * gap] + [0.] * m
        trace[i][0] = 1
        for j in range(1, m + 1):
            # On ties prefer a pair, then a deletion, then an insertion.
            choices = (previous[j - 1] + _pair_cost(old[i - 1], new[j - 1]),
                       previous[j] + gap, current[j - 1] + gap)
            direction = min(range(3), key=choices.__getitem__)
            current[j], trace[i][j] = choices[direction], direction
        previous = current
    pairs = []
    i, j = n, m
    while i or j:
        direction = trace[i][j]
        if direction == 0:
            i -= 1; j -= 1
            pairs.append((i, j, round(_pair_cost(old[i], new[j]), 4)))
        elif direction == 1:
            i -= 1; pairs.append((i, None, None))
        else:
            j -= 1; pairs.append((None, j, None))
    return pairs[::-1]


def _components(mask, cancel=None, *, keep_mask=None, minimum=1):
    """Run-length connected components, without numpy/OpenCV or per-pixel objects."""
    data = mask.tobytes()
    width, height = mask.size
    parents, bounds, counts = [], [], []

    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    previous = []
    for y in range(height):
        if y % 32 == 0:
            check_cancel(cancel)
        row = data[y * width:(y + 1) * width]
        runs, x, cursor = [], 0, 0
        while x < width:
            x = row.find(b'\xff', x)
            if x < 0:
                break
            end = row.find(b'\0', x)
            if end < 0:
                end = width
            label = len(parents)
            if label >= 100_000:
                raise ValueError('差异过于碎片化，已停止以限制内存；请开启噪声过滤或降低比较分辨率')
            parents.append(label); bounds.append([x, y, end, y + 1]); counts.append(end - x)
            while cursor < len(previous) and previous[cursor][1] < x:
                cursor += 1
            k = cursor
            while k < len(previous) and previous[k][0] <= end:
                other = root(previous[k][2]); current = root(label)
                if current != other:
                    parents[other] = current
                k += 1
            runs.append((x, end, label)); x = end
        previous = runs
    merged = {}
    for i, box in enumerate(bounds):
        key = root(i)
        if key not in merged:
            merged[key] = [box[:], counts[i]]
        else:
            b, count = merged[key]
            merged[key] = [[min(b[0], box[0]), min(b[1], box[1]),
                            max(b[2], box[2]), max(b[3], box[3])], count + counts[i]]
    if keep_mask is not None:
        draw = ImageDraw.Draw(keep_mask)
        for i, box in enumerate(bounds):
            if merged[root(i)][1] >= minimum:
                draw.line((box[0], box[1], box[2] - 1, box[1]), fill=255)
    return [value for value in merged.values() if value[1] >= minimum]


def _translate(image, dx, dy):
    result = Image.new(image.mode, image.size, 'white')
    result.paste(image, (dx, dy))
    return result


def _align(old, new, limit, cancel):
    if not limit:
        return new, (0, 0)
    # Bound the sample to ~20k points. No scale/rotation correction or page warp.
    step = max(1, math.ceil(max(old.size) / 160))
    sample = (max(1, old.width // step), max(1, old.height // step))
    a = old.convert('L').resize(sample)
    def score(dx, dy):
        check_cancel(cancel)
        b = _translate(new, dx, dy).convert('L').resize(sample)
        return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]
    baseline = score(0, 0)
    best = (baseline, 0, 0)
    for dy in range(-limit, limit + 1):
        for dx in range(-limit, limit + 1):
            if dx or dy:
                candidate = (score(dx, dy), dx, dy)
                if candidate[0] < best[0] - 1e-8:
                    best = candidate
    # Require a substantial improvement; don't nudge a page to chase one changed word.
    if best[0] < baseline * .7:
        return _translate(new, best[1], best[2]), best[1:]
    return new, (0, 0)


def compare_images(old, new, options, cancel=None):
    options.validate()
    if old.size != new.size:
        raise ValueError('比较图像必须位于同一画布')
    new, shift = _align(old, new, options.align_pixels, cancel)
    a = old.filter(ImageFilter.GaussianBlur(options.blur)) if options.blur else old
    b = new.filter(ImageFilter.GaussianBlur(options.blur)) if options.blur else new
    channels = ImageChops.difference(a, b).split()
    maximum = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    raw = maximum.point([255 if v > options.threshold else 0 for v in range(256)])
    mask = Image.new('L', old.size)
    # Keep exact component pixels; rectangles would colour unchanged holes as differences.
    _components(raw, cancel, keep_mask=mask, minimum=options.min_pixels)
    check_cancel(cancel)
    boxes = [box for box, _ in _components(mask.filter(ImageFilter.MaxFilter(9)), cancel)]
    boxes.sort(key=lambda b: (b[1], b[0]))
    if len(boxes) > 200:
        boxes = boxes[:199] + [[min(b[0] for b in boxes[199:]), min(b[1] for b in boxes[199:]),
                               max(b[2] for b in boxes[199:]), max(b[3] for b in boxes[199:])]]
    return dict(mask=mask, new=new, shift=shift, regions=boxes,
                raw_pixels=raw.histogram()[255], changed_pixels=mask.histogram()[255])


def render_pair(old_doc, new_doc, old_index, new_index, options):
    pages = [old_doc[old_index] if old_index is not None else None,
             new_doc[new_index] if new_index is not None else None]
    width = max(page.rect.width for page in pages if page is not None)
    height = max(page.rect.height for page in pages if page is not None)
    # Preserve physical coordinates; never stretch one page to the other's dimensions.
    scale = min(options.dpi / 72, math.sqrt(options.max_pixels / (width * height)),
                4096 / width, 4096 / height)
    while math.ceil(width * scale) * math.ceil(height * scale) > options.max_pixels:
        scale *= .999
    size = (max(1, math.ceil(width * scale)), max(1, math.ceil(height * scale)))
    images = []
    for page in pages:
        canvas = Image.new('RGB', size, 'white')
        if page is not None:
            canvas.paste(_render(page, scale, options.annotations), (0, 0))
        images.append(canvas)
    return images, scale


def _row(old_doc, new_doc, pair, options, cancel, with_images=False):
    oi, ni, cost = pair
    images, scale = render_pair(old_doc, new_doc, oi, ni, options)
    difference = compare_images(*images, options, cancel)
    size_changed = (oi is not None and ni is not None and
                    any(abs(a - b) > .1 for a, b in
                        zip(old_doc[oi].rect[2:], new_doc[ni].rect[2:])))
    unpaired = oi is None or ni is None
    warnings = []
    if cost is not None and cost > .3:
        warnings.append('页面配对置信度较低，请核对页码或改用按页号配对')
    if difference['shift'] != (0, 0):
        warnings.append(f"已补偿新版平移 {difference['shift']} 像素；整页位移可能被忽略")
    if size_changed:
        warnings.append('页面尺寸或旋转后的外形发生变化，未拉伸归一化')
    regions = difference['regions']
    if (unpaired or size_changed) and not regions:
        regions = [[0, 0, images[0].width, images[0].height]]
    status = ('inserted' if oi is None else 'deleted' if ni is None else
              'changed' if difference['changed_pixels'] or size_changed else 'equal')
    row = dict(old_page=oi + 1 if oi is not None else None,
               new_page=ni + 1 if ni is not None else None, status=status,
               pairing_cost=cost, regions=regions, scale=scale, canvas=list(images[0].size),
               changed_pixels=difference['changed_pixels'], raw_pixels=difference['raw_pixels'],
               shift=list(difference['shift']), warnings=warnings)
    if with_images:
        return row, images, difference
    return row


def _paths_and_stamps(old_path, new_path):
    paths = [str(Path(p).resolve(strict=True)) for p in (old_path, new_path)]
    stamps = [stamp(p) for p in paths]
    if any(s[2] > 512 * 1024 * 1024 for s in stamps):
        raise ValueError('原型每份文件限 512 MiB')
    return paths, stamps


def compare_files(old_path, new_path, options=None, *, old_password='', new_password='',
                  progress=None, cancel=None, manual_pairs=()):
    options = options or CompareOptions()
    options.validate(); check_cancel(cancel)
    paths, stamps = _paths_and_stamps(old_path, new_path)
    started = time.perf_counter()
    with _open_pdf(paths[0], old_password) as old, _open_pdf(paths[1], new_password) as new:
        if max(len(old), len(new)) > options.max_pages:
            raise ValueError(f'原型每份 PDF 最多 {options.max_pages} 页，请先提取需要核对的部分')
        signatures = [[None] * len(old), [None] * len(new)]
        if options.pairing == 'auto':
            done, total = 0, len(old) + len(new)
            for group, doc in zip(signatures, (old, new)):
                for index, page in enumerate(doc):
                    check_cancel(cancel)
                    group[index] = _signature(page, options); done += 1
                    if progress:
                        progress(done, total, '正在生成页面配对指纹')
        pairs = pair_pages(*signatures, mode=options.pairing, cancel=cancel, manual_pairs=manual_pairs)
        del signatures
        rows = []
        for i, pair in enumerate(pairs):
            check_cancel(cancel)
            rows.append(_row(old, new, pair, options, cancel))
            if progress:
                progress(i + 1, len(pairs), f'正在比较第 {i + 1} / {len(pairs)} 组页面')
    check_cancel(cancel)
    if stamps != [stamp(p) for p in paths]:
        raise ValueError('来源文件在比较期间发生变化，请重新比较')
    return dict(schema=1, paths=paths, stamps=stamps, options=asdict(options), rows=rows,
                manual_pairs=sorted([list(pair) for pair in manual_pairs]),
                counts=dict(Counter(row['status'] for row in rows)),
                seconds=round(time.perf_counter() - started, 3),
                note='页面视觉差异，不代表语义差异；自动配对为顺序启发式，请人工核对。')


def check_sources(summary):
    """Also used before reusing a preview; callers perform filesystem IO off the GUI thread."""
    paths, stamps = _paths_and_stamps(*summary['paths'])
    if [list(s) for s in stamps] != [list(s) for s in summary['stamps']]:
        raise ValueError('来源文件已经变化，请重新比较')
    return paths, stamps


def preview_pair(summary, row_index, *, old_password='', new_password='', cancel=None):
    check_cancel(cancel)
    paths, stamps = check_sources(summary)
    options = CompareOptions(**summary['options'])
    options.validate()
    item = summary['rows'][row_index]
    pair = (item['old_page'] - 1 if item['old_page'] else None,
            item['new_page'] - 1 if item['new_page'] else None, item['pairing_cost'])
    with _open_pdf(paths[0], old_password) as old, _open_pdf(paths[1], new_password) as new:
        row, images, difference = _row(old, new, pair, options, cancel, with_images=True)
    if stamps != [stamp(p) for p in paths]:
        raise ValueError('来源文件在预览期间发生变化，请重新比较')
    old, new = images
    mask = difference['mask']
    # Background-distance classification only: colours are visual evidence, not semantics.
    old_ink = old.convert('L').point([255 if v < 235 else 0 for v in range(256)])
    new_ink = difference['new'].convert('L').point([255 if v < 235 else 0 for v in range(256)])
    old_only = ImageChops.multiply(mask, ImageChops.subtract(old_ink, new_ink))
    new_only = ImageChops.multiply(mask, ImageChops.subtract(new_ink, old_ink))
    changed = ImageChops.subtract(mask, ImageChops.lighter(old_only, new_only))
    overlay = Image.new('RGB', old.size, 'white')
    for kind, layer in (('old', old_only), ('new', new_only), ('changed', changed)):
        overlay.paste(COLORS[kind], mask=layer)
    composite = Image.blend(old, difference['new'], .5)
    composite.paste(overlay, mask=mask)
    marked = []
    # Unpaired blank pages still receive a border and a navigable full-page region.
    for source, color, shift in ((old, COLORS['old'], (0, 0)),
                                  (new, COLORS['new'], difference['shift'])):
        image = source.copy(); draw = ImageDraw.Draw(image)
        for box in row['regions']:
            draw.rectangle((box[0] - shift[0], box[1] - shift[1],
                            box[2] - 1 - shift[0], box[3] - 1 - shift[1]), outline=color, width=2)
        marked.append(image)
    return row, dict(old=old, new=new, old_marked=marked[0], new_marked=marked[1],
                     overlay=composite)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('old'); parser.add_argument('new')
    parser.add_argument('--output', required=True, help='新建结果目录，拒绝覆盖已有目录')
    parser.add_argument('--preview', type=int, help='保存第几组页面的原图和标记图（从 1 开始）')
    parser.add_argument('--strict', action='store_true', help='阈值 0，无模糊，不过滤小连通域')
    parser.add_argument('--align-pixels', type=int, default=0, choices=range(5))
    parser.add_argument('--pairing', choices=['auto', 'index'], default='auto')
    parser.add_argument('--no-annotations', action='store_true')
    parser.add_argument('--ask-passwords', action='store_true')
    args = parser.parse_args(argv)
    passwords = ('', '')
    if args.ask_passwords:
        from getpass import getpass
        passwords = (getpass('旧版密码: '), getpass('新版密码: '))
    options = CompareOptions(threshold=0 if args.strict else 24, blur=0 if args.strict else .5,
                             min_pixels=1 if args.strict else 8, align_pixels=args.align_pixels,
                             pairing=args.pairing, annotations=not args.no_annotations)
    summary = compare_files(args.old, args.new, options,
                            old_password=passwords[0], new_password=passwords[1])
    if args.preview is not None and not 1 <= args.preview <= len(summary['rows']):
        parser.error('--preview 必须在页面配对组范围内')
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.preview is not None:
        _, images = preview_pair(summary, args.preview - 1,
                                 old_password=passwords[0], new_password=passwords[1])
        for name, image in images.items():
            image.save(output / f'{name}.png')
    print(json.dumps(dict(counts=summary['counts'], seconds=summary['seconds'],
                         output=str(output.resolve())), ensure_ascii=False))


if __name__ == '__main__':
    main()
