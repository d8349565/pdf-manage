"""测试扫描页识别与已有文字层跳过。"""
from __future__ import annotations

import pymupdf

from app.ocr.page_detector import assess_page, filter_pages_for_ocr


def test_assess_scanned_vs_text_page(tmp_path):
    doc = pymupdf.open()

    # 第 1 页：丰富文字电子页（非扫描）
    p1 = doc.new_page(width=300, height=400)
    p1.insert_text((50, 100), "这是一份包含大量正文的电子合同页面，具有完整文字层。", fontname="china-s", fontsize=12)

    # 第 2 页：空白/少量字符页（判定为需要 OCR / 扫描）
    p2 = doc.new_page(width=300, height=400)
    # 模拟扫描图片：插入一个大图
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 300), 0)
    p2.insert_image(pymupdf.Rect(20, 20, 280, 380), pixmap=pix)

    info1 = assess_page(doc[0])
    assert not info1.is_scanned
    assert "有效文字层" in info1.reason

    info2 = assess_page(doc[1])
    assert info2.is_scanned

    to_proc, skipped = filter_pages_for_ocr(doc, skip_existing=True)
    assert to_proc == [1]
    assert skipped == [0]

    # 不跳过时全部处理
    to_proc_all, skipped_all = filter_pages_for_ocr(doc, skip_existing=False)
    assert to_proc_all == [0, 1]
    assert skipped_all == []
