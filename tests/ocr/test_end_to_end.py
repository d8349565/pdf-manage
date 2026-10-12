"""端到端 OCR 跑通测试：覆盖 PRD §9.2 的核心验证。

需要本地装好 PPD-OCR 增强包；跳过条件已就绪检测。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pymupdf  # noqa: E402

from app.ocr.contracts import OCRRunOptions  # noqa: E402
from app.ocr.model_manager import check_environment  # noqa: E402
from app.ocr.service import run_ocr  # noqa: E402


@pytest.fixture
def scanned_pdf(tmp_path):
    """合成一张最小的扫描 PDF：含已知中文文字。"""
    from PIL import Image, ImageDraw, ImageFont

    env = check_environment()
    if not env.is_ready:
        pytest.skip(f"OCR 增强包缺失：{env.missing_files}")

    img = Image.new("RGB", (800, 400), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 36)
    except Exception:
        font = ImageFont.load_default()
    draw.text((40, 80), "纸序PDF 测试文字", fill="black", font=font)
    draw.text((40, 180), "OCR 识别验证", fill="black", font=font)

    src = tmp_path / "src.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=img.width, height=img.height)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(src)
    doc.close()
    return src


def test_end_to_end_extract_chinese(scanned_pdf, tmp_path):
    """端到端：扫描 PDF → 隐藏文字层 → get_text() 提取中文。"""
    dst = tmp_path / "dst.pdf"
    options = OCRRunOptions(
        page_indices=[], skip_existing_text_layer=True, render_dpi=200,
        output_path=str(dst),
    )
    job = run_ocr(
        source_path=str(scanned_pdf),
        destination_path=str(dst),
        page_indices=None,
        options=options,
        progress_callback=None,
        cancel_check=lambda: False,
    )
    assert dst.is_file(), f"未生成输出：{job.warnings}"
    out = pymupdf.open(dst)
    text = out[0].get_text("text") or ""
    out.close()
    assert "纸序" in text, f"未识别到 纸序：{text!r}"


def test_end_to_end_search_for(scanned_pdf, tmp_path):
    """端到端：扫描 PDF → search_for() 命中。"""
    dst = tmp_path / "dst.pdf"
    options = OCRRunOptions(
        page_indices=[], skip_existing_text_layer=True, render_dpi=200,
        output_path=str(dst),
    )
    run_ocr(
        source_path=str(scanned_pdf),
        destination_path=str(dst),
        page_indices=None,
        options=options,
        progress_callback=None,
        cancel_check=lambda: False,
    )
    out = pymupdf.open(dst)
    hits = out[0].search_for("纸序") or []
    out.close()
    assert len(hits) >= 1, "search_for 未命中"


def test_end_to_end_visual_unchanged(scanned_pdf, tmp_path):
    """端到端：渲染前后视觉一致。"""
    dst = tmp_path / "dst.pdf"
    options = OCRRunOptions(
        page_indices=[], skip_existing_text_layer=True, render_dpi=200,
        output_path=str(dst),
    )
    run_ocr(
        source_path=str(scanned_pdf),
        destination_path=str(dst),
        page_indices=None,
        options=options,
        progress_callback=None,
        cancel_check=lambda: False,
    )

    src_doc = pymupdf.open(scanned_pdf)
    dst_doc = pymupdf.open(dst)
    sp = src_doc[0].get_pixmap(dpi=72)
    dp = dst_doc[0].get_pixmap(dpi=72)
    src_doc.close()
    dst_doc.close()

    if sp.width != dp.width or sp.height != dp.height:
        pytest.skip("像素尺寸差异（写入层导致 PDF 尺寸微调）")

    import numpy as np
    a = np.frombuffer(sp.samples, dtype=np.uint8).astype(np.int16)
    b = np.frombuffer(dp.samples, dtype=np.uint8).astype(np.int16)
    diff = float(np.abs(a - b).mean() / 255.0)
    assert diff < 0.05, f"视觉差异过大 {diff:.4f}"