"""把识别结果以"隐藏文字层"的形式写入 PDF。

这是 PRD §6 强调的核心：必须 **保留原始扫描图片与可见排版**，只增加不可见文字层，
保证 PyMuPDF 的 ``get_text`` / ``search_for`` 与纸序 PDF 现有搜索 UI 能识别。

PoC 阶段实现目标：

* 把 ``OCRLine`` 列表按页面写回，使用 ``render_mode=3`` 实现不可见；
* 不替换 / 删除原有页面内容；
* 不依赖在线字体：使用 PyMuPDF 内置 CJK 字体兜底，D2 阶段验证中文覆盖度。
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pymupdf

from app.ocr.contracts import OCRLine
from app.ocr.page_detector import assess_page


# PyMuPDF 的文字渲染模式：3 表示仅写入文字层、不可见、不影响打印与复制。
INVISIBLE_TEXT_RENDER_MODE = 3


def image_to_page_coords(
    bbox_image: tuple[float, float, float, float],
    *,
    render_width: float,
    render_height: float,
    page_rect: pymupdf.Rect,
    page_rotation: int,
) -> pymupdf.Rect:
    """把渲染图像像素坐标映射到 PDF 页面坐标。

    关键点：

    * 渲染时通常以 0° 视角输出，旋转通过 PyMuPDF ``set_rotation`` 在写入阶段处理；
    * 必须处理 CropBox / MediaBox 偏移；
    * 旋转 90°/270° 时，图像坐标系的 Y 轴方向与 PDF 坐标系相反，需要镜像。

    本函数在 D2 阶段做实测验证（DPI、旋转、CropBox），PoC 先实现坐标通式。
    """
    x0_img, y0_img, x1_img, y1_img = bbox_image
    width = render_width or 1
    height = render_height or 1
    # 像素 → 比例（[0, 1]）
    nx0 = x0_img / width
    ny0 = y0_img / height
    nx1 = x1_img / width
    ny1 = y1_img / height
    page_w = page_rect.width
    page_h = page_rect.height

    if page_rotation in (90, 270):
        # 渲染时图像相对页面旋转：把图像坐标系旋转到页面坐标系。
        nx0, ny0 = ny0, 1 - nx0
        nx1, ny1 = ny1, 1 - nx1
    if page_rotation in (180,):
        nx0, ny0 = 1 - nx0, 1 - ny0
        nx1, ny1 = 1 - nx1, 1 - ny1

    rect = pymupdf.Rect(
        page_rect.x0 + nx0 * page_w,
        page_rect.y0 + ny0 * page_h,
        page_rect.x0 + nx1 * page_w,
        page_rect.y0 + ny1 * page_h,
    )
    return rect


def write_invisible_text_layer(
    page: pymupdf.Page,
    lines: Sequence[OCRLine],
    *,
    font_name: str = "china-s",
    font_file: str | None = None,
    render_width: float = 0.0,
    render_height: float = 0.0,
) -> int:
    """在单个 PDF 页面上写入隐藏文字层。

    返回成功写入的行数。任何写不进去的行都会被丢弃，但页面整体不会被破坏。

    参数说明：

    * ``font_name`` / ``font_file`` —— PyMuPDF 的内置 CJK 字体名，D2 阶段验证；
    * ``render_width`` / ``render_height`` —— 渲染时的图像宽高，用于坐标映射。
    """
    if not lines:
        return 0
    page_rect = page.rect
    page_rotation = page.rotation or 0
    success = 0
    for line in lines:
        if not line.text.strip():
            continue
        rect = image_to_page_coords(
            line.bbox,
            render_width=render_width,
            render_height=render_height,
            page_rect=page_rect,
            page_rotation=page_rotation,
        )
        try:
            page.insert_textbox(
                rect,
                line.text,
                fontname=font_name,
                fontfile=font_file,
                render_mode=INVISIBLE_TEXT_RENDER_MODE,
                color=(0, 0, 0),
                align=pymupdf.TEXT_ALIGN_LEFT,
            )
            success += 1
        except Exception:
            # 单行失败不影响整页；service 会汇总到 failed_pages。
            continue
    return success


def assemble_searchable_pdf(
    source_path: str,
    destination_path: str,
    page_results: dict[int, Sequence[OCRLine]],
    *,
    render_meta: dict[int, tuple[float, float]] | None = None,
) -> dict[str, int]:
    """把 OCR 结果装配成新的可搜索 PDF。

    返回写入统计：``{"processed": n, "skipped": m, "failed": k}``。

    ``page_results`` 的键是页面索引（0-based），值为该页识别出的 :class:`OCRLine`。
    ``render_meta`` 提供每页渲染时的宽高，用于坐标映射。
    """
    src = pymupdf.open(source_path)
    dst = pymupdf.open() if False else None  # 占位：实际使用 Document.insert_pdf 复制
    try:
        dst = pymupdf.open()
        dst.insert_pdf(src)  # 复制全部页面，包括图片与排版
        render_meta = render_meta or {}
        processed = skipped = failed = 0
        for page_index in range(dst.page_count):
            page = dst[page_index]
            lines = page_results.get(page_index) or []
            assessment = assess_page(page)
            if not lines and not assessment.needs_ocr:
                skipped += 1
                continue
            if not lines:
                # 没有识别结果但页面本应扫描 —— 视作失败留记录。
                failed += 1
                continue
            render_w, render_h = render_meta.get(page_index, (page.rect.width, page.rect.height))
            wrote = write_invisible_text_layer(
                page,
                lines,
                render_width=render_w,
                render_height=render_h,
            )
            if wrote:
                processed += 1
            else:
                failed += 1
        dst.save(destination_path, garbage=4, deflate=True, clean=True)
        return {"processed": processed, "skipped": skipped, "failed": failed}
    finally:
        src.close()
        if dst is not None:
            dst.close()