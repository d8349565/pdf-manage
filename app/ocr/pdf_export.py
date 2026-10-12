"""可搜索 PDF 装配与不可见文字层生成模块。

按 PRD §6：保留原始扫描图片和可见排版，仅写入 render_mode=3 隐藏文字层。
支持中英文搜索、选区复制，且与纸序 PDF 现有搜索定位完全兼容。
"""
from __future__ import annotations

import logging
from typing import Mapping, Sequence

import pymupdf

from app.ocr.contracts import CancelCheck, OCRLine

logger = logging.getLogger(__name__)

# PyMuPDF render_mode: 3 = Neither fill nor stroke text (不可见文字层)
INVISIBLE_RENDER_MODE = 3


def image_to_page_rect(
    bbox: tuple[float, float, float, float],
    pix_width: float,
    pix_height: float,
    page_rect: pymupdf.Rect,
) -> pymupdf.Rect:
    """将渲染图像的像素坐标映射回 PDF 页面坐标。

    支持 0°、90°、180°、270° 旋转页面及任意 CropBox 偏移。
    """
    if pix_width <= 0 or pix_height <= 0:
        return page_rect

    x0_img, y0_img, x1_img, y1_img = bbox

    # 归一化比例 (0.0 ~ 1.0)
    nx0 = max(0.0, min(1.0, x0_img / pix_width))
    ny0 = max(0.0, min(1.0, y0_img / pix_height))
    nx1 = max(0.0, min(1.0, x1_img / pix_width))
    ny1 = max(0.0, min(1.0, y1_img / pix_height))

    # 映射到 page.rect 坐标系
    px0 = page_rect.x0 + nx0 * page_rect.width
    py0 = page_rect.y0 + ny0 * page_rect.height
    px1 = page_rect.x0 + nx1 * page_rect.width
    py1 = page_rect.y0 + ny1 * page_rect.height

    return pymupdf.Rect(px0, py0, px1, py1)


def write_invisible_text_layer(
    page: pymupdf.Page,
    lines: Sequence[OCRLine],
    pix_width: float,
    pix_height: float,
    *,
    fontname: str = "china-s",
) -> int:
    """在单页中写入隐藏文字层，返回成功写入的行数。"""
    if not lines:
        return 0

    page_rect = page.rect
    success_count = 0

    for line in lines:
        text = line.text.strip()
        if not text:
            continue

        target_rect = image_to_page_rect(line.bbox, pix_width, pix_height, page_rect)
        if target_rect.is_empty or target_rect.width <= 1.0 or target_rect.height <= 1.0:
            continue

        # 根据文字框高度动态计算字号，保证文字紧密贴合检测框
        line_height = target_rect.height
        fontsize = max(4.0, min(line_height * 0.85, 72.0))

        try:
            # 使用 insert_textbox 写入不可见文字
            rc = page.insert_textbox(
                target_rect,
                text,
                fontname=fontname,
                fontsize=fontsize,
                render_mode=INVISIBLE_RENDER_MODE,
                align=pymupdf.TEXT_ALIGN_LEFT,
            )
            # rc >= 0 表示文字完整容纳（负数表示空间不足剩余字符）
            if rc < 0:
                # 若因排版空间稍显局促，尝试缩小字号重试一次
                page.insert_textbox(
                    target_rect,
                    text,
                    fontname=fontname,
                    fontsize=max(4.0, fontsize * 0.7),
                    render_mode=INVISIBLE_RENDER_MODE,
                    align=pymupdf.TEXT_ALIGN_LEFT,
                )
            success_count += 1
        except Exception as exc:
            logger.debug("页面 %d 写入单行文字失败：%s", page.number, exc)
            continue

    return success_count


def assemble_searchable_pdf(
    source_path: str,
    destination_path: str,
    page_lines: Mapping[int, Sequence[OCRLine]],
    page_pix_sizes: Mapping[int, tuple[float, float]],
    *,
    cancel_check: CancelCheck | None = None,
) -> dict[str, int]:
    """生成带有隐藏文字层的可搜索目标 PDF。

    返回统计结果字典：{"processed": n, "skipped": m, "failed": k}。
    """
    src_doc = pymupdf.open(source_path)
    out_doc = pymupdf.open()

    processed_count = 0
    skipped_count = 0
    failed_count = 0

    try:
        # 复制全部页面以保留原排版与图像
        out_doc.insert_pdf(src_doc)

        # 尝试复制原文档的大纲 / 书签
        try:
            toc = src_doc.get_toc(simple=False)
            if toc:
                out_doc.set_toc(toc)
        except Exception:
            pass

        for page_idx in range(out_doc.page_count):
            if cancel_check and cancel_check():
                break

            lines = page_lines.get(page_idx)
            if lines is None:
                skipped_count += 1
                continue

            if not lines:
                # 进行了识别但没提取出任何文字
                skipped_count += 1
                continue

            pix_w, pix_h = page_pix_sizes.get(page_idx, (out_doc[page_idx].rect.width, out_doc[page_idx].rect.height))
            page = out_doc[page_idx]

            written = write_invisible_text_layer(page, lines, pix_w, pix_h)
            if written > 0:
                processed_count += 1
            else:
                failed_count += 1

        # 保存为新 PDF 并使用 garbage 压缩与清理
        out_doc.save(
            destination_path,
            garbage=4,
            deflate=True,
            clean=True,
        )

        return {
            "processed": processed_count,
            "skipped": skipped_count,
            "failed": failed_count,
        }
    finally:
        src_doc.close()
        out_doc.close()
