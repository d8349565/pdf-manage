"""OCR 业务服务端到端测试。"""
from __future__ import annotations

import os
from pathlib import Path
import pymupdf
import pytest

from app.ocr.contracts import OCRRunOptions
from app.ocr.service import OCRError, resolve_default_output_path, run_ocr


def _create_synthetic_scanned_pdf(path: Path, text: str = "纸序PDF离线文字识别") -> str:
    """创建一个将文字光栅化为图片的合成扫描 PDF。"""
    # 1. 先用一个临时文档把文字渲染为图像
    temp_doc = pymupdf.open()
    temp_page = temp_doc.new_page(width=300, height=200)
    temp_page.insert_text((40, 80), text, fontname="china-s", fontsize=20)
    pix = temp_page.get_pixmap(dpi=150)
    temp_doc.close()

    # 2. 将位图写入新的纯图片 PDF（无文字层）
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=200)
    page.insert_image(page.rect, pixmap=pix)
    doc.save(str(path))
    doc.close()

    # 验证原文档确实无文字
    with pymupdf.open(str(path)) as check_doc:
        assert check_doc[0].get_text().strip() == ""

    return str(path)


def test_run_ocr_end_to_end_on_scanned_page(tmp_path):
    source_pdf = tmp_path / "invoice_scan.pdf"
    _create_synthetic_scanned_pdf(source_pdf, text="发票代码12345678")

    out_pdf = tmp_path / "invoice_scan_custom.pdf"
    res = run_ocr(
        str(source_pdf),
        destination_path=str(out_pdf),
        options=OCRRunOptions(skip_existing_text_layer=True),
    )

    assert res.output_path == str(out_pdf)
    assert 0 in res.processed_pages
    assert len(res.failed_pages) == 0

    # 验证源文件保持未变
    with pymupdf.open(str(source_pdf)) as src:
        assert src[0].get_text().strip() == ""

    # 验证目标 PDF 可以搜索到识别文本并高亮定位
    with pymupdf.open(str(out_pdf)) as out:
        assert len(out) == 1
        page_text = out[0].get_text()
        assert "发票" in page_text
        hits = out[0].search_for("发票")
        assert len(hits) >= 1
        # 命中框在合理范围内
        assert hits[0].width > 5


def test_run_ocr_skips_clean_text_page(tmp_path):
    # 创建带有电子文字的 PDF
    clean_pdf = tmp_path / "digital.pdf"
    doc = pymupdf.open()
    p = doc.new_page(width=300, height=200)
    p.insert_text((40, 60), "这是标准电子文本不需要再次重复识别文字。", fontname="china-s", fontsize=14)
    doc.save(str(clean_pdf))
    doc.close()

    res = run_ocr(
        str(clean_pdf),
        options=OCRRunOptions(skip_existing_text_layer=True),
    )

    # 应该被跳过
    assert 0 in res.skipped_pages
    assert len(res.processed_pages) == 0


def test_run_ocr_cancellation_cleans_up(tmp_path):
    source_pdf = tmp_path / "cancel_test.pdf"
    _create_synthetic_scanned_pdf(source_pdf, text="测试取消流程")

    cancelled = True
    dest = tmp_path / "should_not_exist.pdf"

    from app.output import OperationCancelled
    with pytest.raises(OperationCancelled):
        run_ocr(
            str(source_pdf),
            destination_path=str(dest),
            cancel_check=lambda: cancelled,
        )

    # 目标文件不应生成
    assert not dest.exists()
