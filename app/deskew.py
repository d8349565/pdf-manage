"""Small-angle scan correction using Pillow; no OCR runtime is required."""
from dataclasses import dataclass
import math

from PIL import Image
import pymupdf as p

from app.output import check_cancel
from app.pagegeometry import geometry_edit


@dataclass(frozen=True)
class DeskewEstimate:
    angle: float = 0
    confidence: float = 0
    status: str = 'review'
    message: str = '方向参考不足，请手动确认。'


def estimate_angle(image, cancel=None):
    """Return clockwise correction degrees, not the scan's measured tilt."""
    check_cancel(cancel)
    gray = image.convert('L')
    gray.thumbnail((900, 900))
    width, height = gray.size
    if min(width, height) < 40:
        return DeskewEstimate(message='页面过小，请手动确认。')
    # Ignore scanner borders, which can be stronger than the printed text.
    margin_x, margin_y = max(1, width // 30), max(1, height // 30)
    gray = gray.crop((margin_x, margin_y, width - margin_x, height - margin_y))
    hist = gray.histogram()
    total, weighted = gray.width * gray.height, sum(i * n for i, n in enumerate(hist))
    if gray.getextrema()[1] - gray.getextrema()[0] < 20:
        return DeskewEstimate(status='blank', message='空白或低对比页面，已跳过。')
    count, moment, variance, threshold = 0, 0, -1, 128
    for value, number in enumerate(hist[:-1]):
        count += number
        moment += value * number
        if not count or count == total:
            continue
        score = (moment * total - weighted * count) ** 2 / (count * (total - count))
        if score > variance:
            variance, threshold = score, value
    ink = gray.point(lambda value: 255 if value <= threshold else 0)
    ratio = sum(ink.histogram()[1:]) / total
    if ratio < .0008:
        return DeskewEstimate(status='blank', message='空白或文字过少，已跳过。')
    if ratio > .45:
        return DeskewEstimate(message='页面图像较复杂，请手动确认角度。')

    def profile(angle, source=ink):
        check_cancel(cancel)
        rotated = source.rotate(-angle, resample=Image.Resampling.NEAREST, fillcolor=0)
        rows = list(rotated.resize((1, rotated.height), Image.Resampling.BOX).get_flattened_data())
        columns = list(rotated.resize((rotated.width, 1), Image.Resampling.BOX).get_flattened_data())
        return max(((sum((b - a) ** 2 for a, b in zip(values, values[1:])) / len(values), values)
                    for values in (rows, columns)), key=lambda item: item[0])

    coarse = [(angle / 2, profile(angle / 2)[0]) for angle in range(-20, 21)]
    best = max(coarse, key=lambda item: item[1])[0]
    fine = [(round(best + step / 10, 1), profile(round(best + step / 10, 1)))
            for step in range(-5, 6) if -10 <= best + step / 10 <= 10]
    angle, (score, rows) = max(fine, key=lambda item: item[1][0])
    rival = max((value for candidate, value in coarse if abs(candidate - angle) >= 1), default=score)
    contrast = score / max(1, rival)
    confidence = max(0, min(1, (contrast - 1) / .6))
    # Several distinct bands and a stable peak reduce false positives from
    # isolated graphics, photographs, or nearly uniform pages.
    peak_threshold = max(rows) * .25
    peaks = sum(a < b and b >= c and b > peak_threshold for a, b, c in zip(rows, rows[1:], rows[2:]))
    if abs(angle) >= 9.8 or contrast < 1.12 or peaks < 3:
        return DeskewEstimate(angle, confidence, 'review', '角度判断不够可靠，请查看预览并手动确认。')
    if abs(angle) < .3:
        return DeskewEstimate(0, confidence, 'ready', '页面基本水平，无需调整。')
    return DeskewEstimate(angle, confidence, 'ready', f'建议旋转 {angle:+.1f}°')


def analyze_pages(store, pages, indices, progress=None, cancel=None):
    result = {}
    for step, row in enumerate(indices):
        check_cancel(cancel)
        ref = pages[row]
        store.require_operation([ref], 'content_edit')
        limitations = store._source(ref.source_id).geometry_limitations
        if limitations:
            result[row] = DeskewEstimate(status='unsupported',
                message='含' + '、'.join(limitations) + '，暂不支持纠偏。')
            if progress:
                progress(step + 1, len(indices), f'已分析 {step + 1} / {len(indices)} 页')
            continue
        estimate = None
        with store._render_page(ref, cancel) as page:
            if any(kind != p.PDF_ANNOT_LINK for _, kind, _ in page.annot_xrefs()):
                estimate = DeskewEstimate(status='unsupported', message='含批注、印章或表单；请先纠偏再添加。')
            else:
                width, height = store.page_size(ref)
                scale = min(4, 1000 / max(width, height))
                pix = page.get_pixmap(matrix=p.Matrix(scale, scale).prerotate(ref.rotation),
                                      colorspace=p.csGRAY, alpha=False, annots=False)
                image = Image.frombytes('L', (pix.width, pix.height), pix.samples)
        if estimate is None:
            estimate = estimate_angle(image, cancel)
        result[row] = estimate
        if progress:
            progress(step + 1, len(indices), f'已分析 {step + 1} / {len(indices)} 页')
    check_cancel(cancel)
    return result


def prepare_deskew(store, pages, angles, progress=None, cancel=None):
    changes = {}
    for step, (row, angle) in enumerate(angles.items()):
        check_cancel(cancel)
        if not math.isfinite(angle) or abs(angle) > 10:
            raise ValueError('纠偏角度须在 ±10° 内。')
        if abs(angle) >= .05:
            ref = pages[row]
            candidate = ref.edited(geometry_edit(store, ref, angle=angle))
            with store._render_page(candidate, cancel):
                pass
            changes[row] = candidate
        if progress:
            progress(step + 1, len(angles), f'已准备 {step + 1} / {len(angles)} 页')
    check_cancel(cancel)
    return changes
