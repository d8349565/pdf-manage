"""Image exports using the Qt codecs already shipped with the application."""
from __future__ import annotations

import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PyQt6.QtCore import QBuffer, QIODevice
from PyQt6.QtGui import QImageReader

from app.i18n import tr
from app.output import OperationCancelled, available_path, cancellable_lock, check_cancel, commit_file, stamp
from app.pdfio import PdfError, PdfStore
from app.storage import TemporaryWorkspace

IMAGE_FILTER = "图片 (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)"
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")

_MIB = 1024 ** 2
LONG_IMAGE_MEMORY_LIMIT = 256 * _MIB
_LONG_IMAGE_MEMORY_RESERVE = 64 * _MIB
_LONG_IMAGE_SIDE_LIMITS = {"png": 100_000, "jpeg": 65_500}


@dataclass(frozen=True)
class LongImageLayout:
    width: int
    height: int
    page_renders: tuple[tuple[pymupdf.Matrix, int], ...]
    peak_bytes: int
    image_format: str

    def summary(self):
        return tr('{p0} × {p1} 像素 · {p2} 边长上限 {p3} 像素 · 预计额外峰值 {p4} MiB',
                  p0=self.width, p1=self.height, p2=self.image_format.upper(),
                  p3=_LONG_IMAGE_SIDE_LIMITS[self.image_format], p4=math.ceil(self.peak_bytes / _MIB))


def _image_matrix(ref, scale):
    return pymupdf.Matrix(scale, scale).prerotate(ref.rotation)


def _image_pixels(rect, matrix):
    bounds = rect * matrix
    if not all(math.isfinite(v) for v in bounds) or bounds.is_empty or bounds.is_infinite:
        raise PdfError(tr('所选页面尺寸异常，请缩小导出范围后重试。'))
    pixels = bounds.irect
    if pixels.is_empty:
        raise PdfError(tr('所选页面尺寸异常，请缩小导出范围后重试。'))
    return pixels


def _long_image_peak_bytes(width, height, page_bytes, fmt):
    canvas_bytes = 3 * width * height
    if fmt == "png":
        # MuPDF writes the whole pixmap as one band with two full-sized zlib buffers.
        filtered_bytes = canvas_bytes + height
        encoder_bytes = 2 * filtered_bytes + filtered_bytes // 100
    else:
        # Progressive 4:4:4 JPEG stores 16-bit coefficients in padded 8×8 blocks.
        encoder_bytes = 6 * ((width + 7) // 8 * 8) * ((height + 7) // 8 * 8)
    return canvas_bytes + max(page_bytes, encoder_bytes) + _LONG_IMAGE_MEMORY_RESERVE


def require_saved_image_pages(pages):
    if any(ref.edits for ref in pages):
        raise PdfError(tr('所选页面有未保存的内容修改。请先保存或另存 PDF，再导出图片。'))


def plan_long_image(store, pages, fmt, dpi, cancel=None):
    """Plan full-width renders from each source CropBox before allocating any pixels."""
    pages = tuple(pages)
    require_saved_image_pages(pages)
    source_pages = []
    for ref in pages:
        check_cancel(cancel)
        rect = store._source(ref.source_id).sizes[ref.source_page]
        matrix = _image_matrix(ref, dpi / 72)
        source_pages.append((ref, rect, matrix, _image_pixels(rect, matrix)))
    if not source_pages:
        raise PdfError(tr('没有要导出的页面。'))

    width = max(pixels.width for _, _, _, pixels in source_pages)
    page_renders = []
    for ref, rect, matrix, pixels in source_pages:
        check_cancel(cancel)
        if pixels.width < width:
            visible_width = rect.height if ref.rotation % 180 else rect.width
            scale = width / visible_width
            matrix = _image_matrix(ref, scale)
            pixels = _image_pixels(rect, matrix)
            if pixels.width > width:
                # Float32 rounding can put the page edge just beyond the integer width.
                # Move it inside by a fraction of a pixel while keeping the same X/Y scale.
                matrix = _image_matrix(ref, scale * (1 - 2 ** -22))
                pixels = _image_pixels(rect, matrix)
            if width <= _LONG_IMAGE_SIDE_LIMITS["png"] and pixels.width != width:
                raise PdfError(tr('所选页面尺寸异常，请缩小导出范围后重试。'))
        page_renders.append((matrix, pixels.height))

    height = sum(h for _, h in page_renders)
    page_bytes = 3 * width * max(h for _, h in page_renders)
    peak_bytes = _long_image_peak_bytes(width, height, page_bytes, fmt)
    layout = LongImageLayout(width, height, tuple(page_renders), peak_bytes, fmt)

    longest_side = max(width, height)
    reasons = []
    if longest_side > _LONG_IMAGE_SIDE_LIMITS[fmt]:
        reasons.append(tr('宽度或高度超出 {p0} 边长上限。', p0=fmt.upper()))
    if peak_bytes > LONG_IMAGE_MEMORY_LIMIT:
        reasons.append(tr('预计内存超出 {p0} MiB 预算。', p0=LONG_IMAGE_MEMORY_LIMIT // _MIB))
    if reasons:
        if (fmt == "jpeg" and _LONG_IMAGE_SIDE_LIMITS["jpeg"] < longest_side <= _LONG_IMAGE_SIDE_LIMITS["png"]
                and _long_image_peak_bytes(width, height, page_bytes, "png") <= LONG_IMAGE_MEMORY_LIMIT):
            suggestion = tr('请选择 PNG、降低 DPI 或缩小导出范围。')
        else:
            suggestion = tr('请降低 DPI、缩小导出范围或改用逐页导出。')
        raise PdfError('\n'.join((layout.summary(), *reasons, suggestion)))
    return layout


def image_pdf_data(paths, progress=None, cancel=None):
    """Create image pages in memory, also used by mixed document import."""
    with pymupdf.open() as doc:
        for index, path in enumerate(paths):
            check_cancel(cancel)
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            frame_count = reader.imageCount()
            is_tiff = Path(path).suffix.lower() in (".tif", ".tiff")
            if is_tiff and frame_count < 1:
                raise PdfError(f"无法读取 TIFF 帧目录：{Path(path).name}")
            for frame in range(max(1, frame_count)):
                if frame and not reader.jumpToImage(frame):
                    raise PdfError(f"无法跳转到 {Path(path).name} 第 {frame + 1} 帧")
                check_cancel(cancel)
                image = reader.read()
                if image.isNull():
                    raise PdfError(f"无法读取 {Path(path).name} 第 {frame + 1} 帧：{reader.errorString()}")
                buffer = QBuffer()
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                if not image.save(buffer, "PNG"):
                    raise PdfError(f"无法编码图片：{Path(path).name}")
                dpi_x = image.dotsPerMeterX() * .0254
                dpi_y = image.dotsPerMeterY() * .0254
                dpi_x = dpi_x if 36 <= dpi_x <= 1200 else 150
                dpi_y = dpi_y if 36 <= dpi_y <= 1200 else 150
                page = doc.new_page(width=image.width() * 72 / dpi_x, height=image.height() * 72 / dpi_y)
                page.insert_image(page.rect, stream=bytes(buffer.data()), keep_proportion=False)
                if progress:
                    progress(index, len(paths), f"正在导入 {Path(path).name} · 第 {frame + 1} 帧")
            if progress:
                progress(index + 1, len(paths), f"已导入 {Path(path).name} · {frame + 1} 页")
        check_cancel(cancel)
        return doc.tobytes(garbage=4, deflate=True)


def images_to_pdf(paths, dest, safety_store, progress, cancel):
    if safety_store.is_source_destination(dest):
        raise PdfError("图片转 PDF 请另存新文件，不能覆盖已打开的 PDF。")
    safety_store._check_destination(dest)
    with TemporaryWorkspace('images') as folder:
        intermediate = os.path.join(folder, "images.pdf")
        Path(intermediate).write_bytes(image_pdf_data(paths, progress, cancel))
        store = PdfStore()
        try:
            pages = store.load(intermediate)
            return store.write(pages, dest, progress_callback=progress, cancel=cancel)
        finally:
            store.close_all()


def _render_pdf_image(store, ref, matrix, cancel):
    source = store._source(ref.source_id)
    with cancellable_lock(store._native_lock, cancel), cancellable_lock(source.lock, cancel):
        page = source.doc_locked()[ref.source_page]
        return page.get_pixmap(matrix=matrix, colorspace=pymupdf.csRGB, alpha=False)


def _save_pdf_image(pix, target, expected, dpi, cancel, *, overwrite, verify_size=False):
    check_cancel(cancel)
    pix.set_dpi(dpi, dpi)
    target_path = Path(target)
    fd, temp = tempfile.mkstemp(prefix=".pageark-image-", suffix=target_path.suffix, dir=target_path.parent)
    try:
        os.close(fd)
        check_cancel(cancel)
        pix.save(temp, jpg_quality=95)
        check_cancel(cancel)
        if not Path(temp).stat().st_size:
            raise PdfError(tr('图片输出为空'))
        if verify_size:
            size = QImageReader(temp, target_path.suffix[1:].encode("ascii")).size()
            if (size.width(), size.height()) != (pix.width, pix.height):
                raise PdfError(tr('无法核对长图尺寸，请重新导出。'))
        if overwrite:
            commit_file(temp, target, expected, cancel=cancel)
            return target
        while True:
            candidate = available_path(target_path, target_path.suffix, cancel=cancel)
            try:
                commit_file(temp, candidate, None, cancel=cancel)
                return str(candidate)
            except FileExistsError:
                # Another export took this name. Reuse the already encoded image.
                check_cancel(cancel)
    finally:
        Path(temp).unlink(missing_ok=True)


def pdf_to_images(store, pages, indices, folder, prefix, fmt, dpi, overwrite, progress, cancel):
    store.require_operation(pages, "copy")
    require_saved_image_pages(pages)
    completed, renamed, failed = [], [], []
    cancelled = False
    for number, (ref, index) in enumerate(zip(pages, indices), 1):
        if cancel():
            cancelled = True
            break
        target = str(Path(folder) / f"{prefix}_{index + 1:04d}.{fmt.lower()}")
        try:
            expected = stamp(target) if overwrite else None
            if overwrite and store.is_source_destination(target):
                raise PdfError(tr('不能覆盖已打开的来源文件，请选择新的文件名。'))
            progress(number - 1, len(pages), f"正在渲染第 {index + 1} 页 · {dpi} DPI")
            pix = _render_pdf_image(store, ref, _image_matrix(ref, dpi / 72), cancel)
            saved = _save_pdf_image(pix, target, expected, dpi, cancel, overwrite=overwrite)
            completed.append(saved)
            if saved != target:
                renamed.append(saved)
            progress(number, len(pages), f"已导出第 {index + 1} 页")
        except OperationCancelled:
            cancelled = True
            break
        except Exception as exc:
            failed.append(f"第 {index + 1} 页：{exc}")
    return completed, renamed, failed, cancelled


def pdf_to_long_image(store, pages, indices, folder, prefix, fmt, dpi, overwrite, progress, cancel):
    store.require_operation(pages, "copy")
    target = str(Path(folder) / (prefix + tr('_长图') + '.' + fmt))
    try:
        check_cancel(cancel)
        expected = stamp(target) if overwrite else None
        if overwrite and store.is_source_destination(target):
            raise PdfError(tr('不能覆盖已打开的来源文件，请选择新的文件名。'))

        progress(0, 0, tr('正在检查长图尺寸…'))
        layout = plan_long_image(store, pages, fmt, dpi, cancel)
        total = len(layout.page_renders) + 1
        progress(0, total, layout.summary())
        check_cancel(cancel)
        canvas = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, layout.width, layout.height), 0)
        canvas.clear_with(255)
        y = 0
        renders = zip(pages, indices, layout.page_renders, strict=True)
        for number, (ref, index, (matrix, height)) in enumerate(renders, 1):
            check_cancel(cancel)
            pix = _render_pdf_image(store, ref, matrix, cancel)
            check_cancel(cancel)
            if (pix.width, pix.height) != (layout.width, height):
                raise PdfError(tr('第 {p0} 页的渲染尺寸与预检不符，请重新打开文档后再试。', p0=index + 1))
            pix.set_origin(0, y)
            canvas.copy(pix, pix.irect)
            del pix
            y += height
            progress(number, total, tr('已合成 {p0} / {p1} 页', p0=number, p1=total - 1))

        progress(0, 0, tr('正在编码并保存长图…'))
        saved = _save_pdf_image(canvas, target, expected, dpi, cancel, overwrite=overwrite, verify_size=True)
        progress(total, total, tr('长图已导出'))
        return [saved], [saved] if saved != target else [], [], False
    except OperationCancelled:
        return [], [], [], True
    except MemoryError as exc:
        raise PdfError(tr('长图内存不足，请降低 DPI 或缩小导出范围。')) from exc
    except (OSError, RuntimeError, ValueError, pymupdf.mupdf.FzErrorBase) as exc:
        raise PdfError(str(exc)) from exc
