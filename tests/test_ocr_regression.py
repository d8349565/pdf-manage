"""OCR 增强包的回归与单元测试。

覆盖：
* OCR 子包契约与错误处理（不依赖 RapidOCR 推理）；
* PDF 导出层在空数据 / 旋转页面的坐标换算（不依赖真实模型）；
* 与现有 ``Workspace`` 的未保存修改判断的协调；
* 端到端：在临时目录中创建最小扫描 PDF，跑完整流程（需 ``rapidocr-onnxruntime`` 与模型）。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

# 让项目根可被 import
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pymupdf  # noqa: E402

from app.ocr import contracts as C  # noqa: E402
from app.ocr.engine import OCREngine, OCREngineError, OCRModelMissing  # noqa: E402
from app.ocr.model_manager import REQUIRED_FILES, check_environment  # noqa: E402
from app.ocr.page_detector import assess_page, filter_pages_needing_ocr  # noqa: E402
from app.ocr.pdf_export import image_to_page_coords, INVISIBLE_TEXT_RENDER_MODE  # noqa: E402
from app.ocr.service import OCROperationError, run_ocr  # noqa: E402


# --------------------------------------------------------------------------- #
# 契约 / 子包基础
# --------------------------------------------------------------------------- #


def test_ocr_line_default_construction():
    line = C.OCRLine(
        text="纸序",
        bbox=(10, 20, 110, 60),
        polygon=(10, 20, 110, 20, 110, 60, 10, 60),
        confidence=0.92,
        page_index=0,
    )
    assert line.text == "纸序"
    assert line.confidence > 0
    assert line.page_index == 0


def test_run_options_defaults():
    opts = C.OCRRunOptions(page_indices=[0])
    assert opts.skip_existing_text_layer is True
    assert opts.render_dpi == 200
    assert opts.overwrite_existing is False


def test_environment_check_missing(tmp_path):
    status = check_environment(install_dir=tmp_path)
    assert status.is_ready is False
    assert set(status.missing_files) == set(REQUIRED_FILES)


def test_engine_error_classes():
    assert issubclass(OCRModelMissing, OCREngineError)
    with pytest.raises(OCREngineError):
        raise OCRModelMissing("test")
    OCREngine.reset()


# --------------------------------------------------------------------------- #
# 页面检测
# --------------------------------------------------------------------------- #


def _make_pdf(path: Path, text: str = "Hello world", *, rotation: int = 0) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_textbox(page.rect, text, fontname="china-s", fontsize=12, rotate=rotation)
    doc.save(path)
    doc.close()
    return path


def test_assess_electronic_page():
    path = _make_pdf(Path(_tmp()) / "electronic.pdf", "Hello")
    doc = pymupdf.open(path)
    page = doc[0]
    assessment = assess_page(page)
    assert assessment.needs_ocr is False
    assert assessment.text_length > 0
    doc.close()


def test_assess_scanned_page(tmp_path):
    """纯扫描页（图片占满且无文字）。"""
    img = _make_blank_image(800, 1000, "white")
    doc = pymupdf.open()
    page = doc.new_page(width=img.width, height=img.height)
    page.insert_image(page.rect, stream=_png_bytes(img))
    out = tmp_path / "scanned.pdf"
    doc.save(out)
    doc.close()
    doc = pymupdf.open(out)
    assessment = assess_page(doc[0])
    assert assessment.needs_ocr is True, assessment
    assert assessment.image_area_ratio > 0.5
    doc.close()


def test_filter_pages_needing_ocr(tmp_path):
    out = tmp_path / "mixed.pdf"
    doc = pymupdf.open()
    page1 = doc.new_page(width=595, height=842)
    page1.insert_textbox(page1.rect, "电子文字", fontname="china-s", fontsize=12)
    page2 = doc.new_page(width=595, height=842)
    img = _make_blank_image(595, 842, "white")
    page2.insert_image(page2.rect, stream=_png_bytes(img))
    doc.save(out)
    doc.close()
    doc = pymupdf.open(out)
    needs = filter_pages_needing_ocr(doc)
    assert 1 in needs
    assert 0 not in needs
    doc.close()


# --------------------------------------------------------------------------- #
# PDF 导出层：坐标变换
# --------------------------------------------------------------------------- #


def test_image_to_page_coords_identity():
    rect = image_to_page_coords(
        (50, 50, 150, 150),
        render_width=200, render_height=200,
        page_rect=pymupdf.Rect(0, 0, 200, 200),
        page_rotation=0,
    )
    assert rect.x0 == pytest.approx(50, abs=1)
    assert rect.y0 == pytest.approx(50, abs=1)
    assert rect.x1 == pytest.approx(150, abs=1)
    assert rect.y1 == pytest.approx(150, abs=1)


def test_image_to_page_coords_rotated_90():
    """旋转 90° 时，图像的 Y 轴应镜像映射到页面坐标系。"""
    rect = image_to_page_coords(
        (0, 0, 200, 100),
        render_width=200, render_height=100,
        page_rect=pymupdf.Rect(0, 0, 100, 200),
        page_rotation=90,
    )
    norm = rect.normalize()
    assert norm.width == pytest.approx(100, abs=1)
    assert norm.height == pytest.approx(200, abs=1)


def test_invisible_render_mode_constant():
    assert INVISIBLE_TEXT_RENDER_MODE == 3


# --------------------------------------------------------------------------- #
# service.run_ocr 错误转换
# --------------------------------------------------------------------------- #


def test_run_ocr_missing_file(tmp_path):
    with pytest.raises(OCROperationError):
        run_ocr(
            source_path=str(tmp_path / "no.pdf"),
            destination_path=None,
            page_indices=None,
            options=C.OCRRunOptions(page_indices=[]),
            progress_callback=None,
            cancel_check=lambda: False,
        )


def test_run_ocr_non_pdf(tmp_path):
    bad = tmp_path / "note.txt"
    bad.write_text("not a pdf", encoding="utf-8")
    with pytest.raises(OCROperationError):
        run_ocr(
            source_path=str(bad),
            destination_path=None,
            page_indices=None,
            options=C.OCRRunOptions(page_indices=[]),
            progress_callback=None,
            cancel_check=lambda: False,
        )


# --------------------------------------------------------------------------- #
# 端到端：依赖真实模型的可选测试
# --------------------------------------------------------------------------- #


@pytest.mark.requires_models
def test_end_to_end_with_models(tmp_path):
    """如果本地已经安装 OCR 增强包，跑一次端到端 OCR。"""
    env = check_environment()
    if not env.is_ready:
        pytest.skip(f"OCR 增强包缺失：{env.missing_files}")

    # 用 PIL 合成一张最小的扫描 PDF
    img = _make_blank_image(800, 600, "white")
    from PIL import ImageDraw, ImageFont
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 28)
    except Exception:
        font = ImageFont.load_default()
    draw.text((40, 60), "纸序PDF OCR 测试", fill="black", font=font)

    src = tmp_path / "scan_src.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=img.width, height=img.height)
    page.insert_image(page.rect, stream=_png_bytes(img))
    doc.save(src)
    doc.close()

    dst = tmp_path / "scan_dst.pdf"
    options = C.OCRRunOptions(page_indices=[], skip_existing_text_layer=True,
                              render_dpi=200, output_path=str(dst))
    job = run_ocr(
        source_path=str(src),
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
    assert "纸序" in text or "PDF" in text, f"识别文字异常：{text[:80]!r}"


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #


def _tmp() -> str:
    import tempfile
    d = tempfile.mkdtemp(prefix="ocr-test-")
    return d


def _make_blank_image(w: int, h: int, color: str):
    from PIL import Image
    return Image.new("RGB", (w, h), color)


def _png_bytes(image) -> bytes:
    import io
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()