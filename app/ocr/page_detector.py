"""扫描页检测 —— 决定哪些页面需要 OCR。

与 ``app.pdf2excel.analyzer.analyze_page`` 有相似意图，但 PRD §5.2 要求：

* OCR 模块独立维护扫描页判断接口，**不要** 直接复用
  ``pdf2excel.analyzer`` 的业务结果；
* 后续再考虑抽取通用检测方法供两个功能复用。

本模块对外暴露纯函数，便于 service / 测试调用。
"""
from __future__ import annotations

from dataclasses import dataclass

import pymupdf


@dataclass(frozen=True)
class ScanPageAssessment:
    """单页扫描状态评估结果。"""

    page_index: int
    needs_ocr: bool
    reason: str
    text_length: int
    image_area_ratio: float


def assess_page(page: pymupdf.Page, *, threshold_text_length: int = 10,
                threshold_image_ratio: float = 0.8) -> ScanPageAssessment:
    """判断单页是否需要 OCR。

    判定规则与现有 ``pdf2excel.analyzer`` 保持一致，避免 UI 给出互相矛盾的结论：

    * 整页图片占比 ``> threshold_image_ratio`` 且有效文本极短 ``< threshold_text_length``
      → 判定为扫描页，需要 OCR；
    * 已存在可读文字层 → 跳过 OCR；
    * 其他情况按"安全侧"处理：不强制 OCR，留给调用方按选项决定。

    返回 :class:`ScanPageAssessment`，所有数值一并附带，便于审计与回归。
    """
    text = page.get_text("text") or ""
    blocks = page.get_text("dict").get("blocks", []) or []
    image_blocks = [b for b in blocks if b.get("type") == 1]
    page_area = page.rect.width * page.rect.height
    image_area = sum(
        (b["bbox"][2] - b["bbox"][0]) * (b["bbox"][3] - b["bbox"][1])
        for b in image_blocks if "bbox" in b
    )
    image_area_ratio = image_area / page_area if page_area > 0 else 0.0
    cleaned = text.strip()

    needs_ocr = (image_area_ratio > threshold_image_ratio and len(cleaned) < threshold_text_length) \
        or (len(cleaned) == 0 and len(image_blocks) > 0)
    reason = "图片占比高且文字近乎空" if needs_ocr else "已有可用文字层"
    return ScanPageAssessment(
        page_index=page.number,
        needs_ocr=needs_ocr,
        reason=reason,
        text_length=len(cleaned),
        image_area_ratio=image_area_ratio,
    )


def filter_pages_needing_ocr(
    doc: pymupdf.Document,
    page_indices: list[int] | None = None,
) -> list[int]:
    """批量评估，返回需要 OCR 的页面索引（0-based）。

    ``page_indices`` 为 None 时评估全部页面；否则只评估指定页面。
    """
    indices = page_indices if page_indices is not None else list(range(doc.page_count))
    needs: list[int] = []
    for index in indices:
        if index < 0 or index >= doc.page_count:
            continue
        if assess_page(doc[index]).needs_ocr:
            needs.append(index)
    return needs