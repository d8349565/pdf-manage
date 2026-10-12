"""OCR 子包 smoke test：仅验证模块可被导入与基础契约成立。"""
from __future__ import annotations

from app.ocr.contracts import (
    OCRJobResult,
    OCRLine,
    OCRPageFailure,
    OCRRunOptions,
)
from app.ocr.engine import OCREngineError, OCRModelMissing
from app.ocr.model_manager import REQUIRED_FILES, check_environment
from app.ocr.page_detector import assess_page


def test_contracts_importable():
    assert OCRLine.__doc__
    assert OCRJobResult.__doc__
    assert OCRRunOptions.__doc__
    assert OCRPageFailure.__doc__


def test_run_options_defaults():
    opts = OCRRunOptions(page_indices=[0, 1])
    assert opts.skip_existing_text_layer is True
    assert opts.render_dpi == 200
    assert opts.overwrite_existing is False


def test_environment_check_missing_dir(tmp_path):
    status = check_environment(install_dir=tmp_path)
    assert status.is_ready is False
    assert "det.onnx" in status.missing_files
    assert REQUIRED_FILES  # 模型清单非空


def test_engine_error_classes_are_distinct():
    assert issubclass(OCRModelMissing, OCREngineError)


def test_page_detector_signature():
    """确认 assess_page 不依赖 PyMuPDF 实例即可拿到签名（实现需真实页面）。"""
    import inspect

    sig = inspect.signature(assess_page)
    assert "page" in sig.parameters
    assert sig.parameters["page"].annotation is not inspect.Parameter.empty