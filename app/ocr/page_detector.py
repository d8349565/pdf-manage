"""扫描页检测器。

识别 PDF 页面是否缺少有效文字层（扫描件、纯图片页），用于自动跳过已具备文字层的电子页面。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import pymupdf


@dataclass(frozen=True)
class PageScanInfo:
    """单页扫描状态评估。"""

    page_index: int
    is_scanned: bool
    text_length: int
    has_images: bool
    image_area_ratio: float
    reason: str


def assess_page(
    page: pymupdf.Page,
    *,
    min_text_len: int = 15,
    min_img_ratio: float = 0.5,
) -> PageScanInfo:
    """评估单页是否为需要 OCR 的扫描页。

    判定标准：
    1. 页面可提取文本纯字符数 >= min_text_len：通常为已有文字层的电子页或已 OCR 页面 -> 跳过；
    2. 页面文本极少且含有图像，或图像占页面面积超过 min_img_ratio -> 判定为扫描页，需要 OCR；
    3. 空白无内容页面 -> 默认不强制 OCR。
    """
    text = page.get_text("text").strip()
    text_len = len(text)

    # 统计图片区域占比
    rect = page.rect
    page_area = rect.width * rect.height if rect.width > 0 and rect.height > 0 else 1.0

    img_info_list = page.get_images(full=True)
    has_images = len(img_info_list) > 0

    total_img_area = 0.0
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") == 1 and "bbox" in block:
            b = block["bbox"]
            total_img_area += max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])

    img_ratio = min(1.0, total_img_area / page_area) if page_area > 0 else 0.0

    if text_len >= min_text_len and img_ratio < 0.95:
        return PageScanInfo(
            page_index=page.number,
            is_scanned=False,
            text_length=text_len,
            has_images=has_images,
            image_area_ratio=img_ratio,
            reason="页面已包含有效文字层",
        )

    if has_images or img_ratio >= min_img_ratio:
        return PageScanInfo(
            page_index=page.number,
            is_scanned=True,
            text_length=text_len,
            has_images=has_images,
            image_area_ratio=img_ratio,
            reason="页面为扫描件或大图且缺乏文字层",
        )

    # 既无文字又无明显图片的特殊页
    is_scanned = text_len < min_text_len
    return PageScanInfo(
        page_index=page.number,
        is_scanned=is_scanned,
        text_length=text_len,
        has_images=has_images,
        image_area_ratio=img_ratio,
        reason="页面有效文本不足" if is_scanned else "已有文字",
    )


def filter_pages_for_ocr(
    doc: pymupdf.Document,
    target_indices: Sequence[int] | None = None,
    skip_existing: bool = True,
) -> tuple[list[int], list[int]]:
    """筛选出需要处理的页面列表与跳过的页面列表。

    返回 (to_process_indices, skipped_indices)。
    """
    indices = list(target_indices) if target_indices is not None else list(range(doc.page_count))
    to_process: list[int] = []
    skipped: list[int] = []

    for idx in indices:
        if idx < 0 or idx >= doc.page_count:
            continue
        if not skip_existing:
            to_process.append(idx)
            continue

        info = assess_page(doc[idx])
        if info.is_scanned:
            to_process.append(idx)
        else:
            skipped.append(idx)

    return to_process, skipped
