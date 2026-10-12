"""Image exports using the Qt codecs already shipped with the application."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pymupdf
from PyQt6.QtCore import QBuffer, QIODevice
from PyQt6.QtGui import QImageReader

from app.output import OperationCancelled, check_cancel, commit_file, stamp
from app.pdfio import PdfError, PdfStore

IMAGE_FILTER = "图片 (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)"
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")


def images_to_pdf(paths, dest, safety_store, progress, cancel):
    if safety_store.is_source_destination(dest):
        raise PdfError("图片转 PDF 请另存新文件，不能覆盖已打开的 PDF。")
    safety_store._check_destination(dest)
    with tempfile.TemporaryDirectory(prefix="pageark-images-") as folder:
        intermediate = os.path.join(folder, "images.pdf")
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
                    progress(index, len(paths), f"正在导入 {Path(path).name} · 第 {frame + 1} 帧")
                progress(index + 1, len(paths), f"已导入 {Path(path).name} · {frame + 1} 页")
            check_cancel(cancel)
            doc.save(intermediate, garbage=4, deflate=True)
        store = PdfStore()
        try:
            pages = store.load(intermediate)
            return store.write(pages, dest, progress_callback=progress, cancel=cancel)
        finally:
            store.close_all()


def pdf_to_images(store, pages, indices, folder, prefix, fmt, dpi, overwrite, progress, cancel):
    store.require_operation(pages, "copy")
    completed, skipped, failed = [], [], []
    cancelled = False
    for number, (ref, index) in enumerate(zip(pages, indices), 1):
        if cancel():
            cancelled = True
            break
        target = str(Path(folder) / f"{prefix}_{index + 1:04d}.{fmt.lower()}")
        expected = stamp(target)
        if expected is not None and not overwrite:
            skipped.append(target)
            progress(number, len(pages), f"已跳过同名图片 · {Path(target).name}")
            continue
        temp = None
        try:
            progress(number - 1, len(pages), f"正在渲染第 {index + 1} 页 · {dpi} DPI")
            source = store._source(ref.source_id)
            with source.lock:
                page = source.doc_locked()[ref.source_page]
                pix = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72).prerotate(ref.rotation),
                                      colorspace=pymupdf.csRGB, alpha=False)
            pix.set_dpi(dpi, dpi)
            check_cancel(cancel)
            fd, temp = tempfile.mkstemp(prefix=".pageark-image-", suffix="." + fmt.lower(), dir=folder)
            os.close(fd)
            pix.save(temp, jpg_quality=95)
            if stamp(temp)[2] == 0:
                raise PdfError("图片输出为空")
            commit_file(temp, target, expected, cancel=cancel)
            temp = None
            completed.append(target)
            progress(number, len(pages), f"已导出第 {index + 1} 页")
        except OperationCancelled:
            cancelled = True
            break
        except Exception as exc:
            failed.append(f"第 {index + 1} 页：{exc}")
        finally:
            if temp:
                Path(temp).unlink(missing_ok=True)
    return completed, skipped, failed, cancelled
