"""测试坐标映射与不可见文字层导出。"""
from __future__ import annotations

import pymupdf
import pytest

from app.ocr.contracts import OCRLine
from app.ocr.pdf_export import (
    assemble_searchable_pdf,
    image_to_page_rect,
    write_invisible_text_layer,
)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_image_to_page_rect_rotations(rotation):
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=500)
    if rotation:
        page.set_rotation(rotation)

    # 模拟在页面中间的矩形
    true_rect = pymupdf.Rect(50, 100, 250, 200)

    # 模拟 200 DPI 渲染
    pix = page.get_pixmap(dpi=200)
    scale_x = pix.width / page.rect.width
    scale_y = pix.height / page.rect.height

    bbox = (
        (true_rect.x0 - page.rect.x0) * scale_x,
        (true_rect.y0 - page.rect.y0) * scale_y,
        (true_rect.x1 - page.rect.x0) * scale_x,
        (true_rect.y1 - page.rect.y0) * scale_y,
    )

    mapped = image_to_page_rect(bbox, pix.width, pix.height, page.rect)
    assert abs(mapped.x0 - true_rect.x0) < 0.01
    assert abs(mapped.y0 - true_rect.y0) < 0.01
    assert abs(mapped.x1 - true_rect.x1) < 0.01
    assert abs(mapped.y1 - true_rect.y1) < 0.01


def test_write_invisible_text_layer_and_search(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=600)

    pix = page.get_pixmap(dpi=200)
    line = OCRLine(
        text="增值税普通发票",
        bbox=(100.0, 100.0, 500.0, 160.0),
        polygon=(100.0, 100.0, 500.0, 100.0, 500.0, 160.0, 100.0, 160.0),
        confidence=0.99,
        page_index=0,
    )

    count = write_invisible_text_layer(page, [line], pix.width, pix.height)
    assert count == 1

    # 提取文字与检索
    text = page.get_text()
    assert "增值税普通发票" in text

    hits = page.search_for("增值税")
    assert len(hits) == 1
    # 命中框在合理位置
    assert hits[0].x0 >= 30.0 and hits[0].y0 >= 30.0

    target = tmp_path / "searchable.pdf"
    doc.save(str(target))
    doc.close()

    # 重新打开验证
    with pymupdf.open(str(target)) as reopened:
        assert len(reopened) == 1
        assert "增值税" in reopened[0].get_text()
        assert len(reopened[0].search_for("发票")) == 1
