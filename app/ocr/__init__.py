"""纸序 PDF 轻量离线 OCR 模块。

基于 PP-OCRv6-small + RapidOCR + ONNX Runtime CPU。
仅在用户主动发起识别时按需加载推理模型，不影响主应用基础启动性能。
"""
from __future__ import annotations

from app.ocr.contracts import (
    CancelCheck,
    OCRJobResult,
    OCRLine,
    OCRPageFailure,
    OCRRunOptions,
    ProgressCallback,
)

__all__ = [
    "CancelCheck",
    "OCRJobResult",
    "OCRLine",
    "OCRPageFailure",
    "OCRRunOptions",
    "ProgressCallback",
]
