"""RapidOCR 推理封装模块。

按需懒加载 PP-OCR 推理库，转换 PyMuPDF Pixmap 输入，将推理输出统一规整为 OCRLine。
"""
from __future__ import annotations

import logging
from pathlib import Path
from threading import Lock
from typing import Sequence

import pymupdf

from app.ocr.contracts import OCRLine

logger = logging.getLogger(__name__)


class OCREngineError(RuntimeError):
    """OCR 引擎推理异常基类。"""


class OCRNotAvailableError(OCREngineError):
    """OCR 运行环境或模型不可用。"""


class OCREngine:
    """RapidOCR 推理引擎单例封装。"""

    _instance: "OCREngine | None" = None
    _lock = Lock()

    def __init__(
        self,
        model_dir: Path | None = None,
        *,
        text_score: float = 0.5,
        use_angle_cls: bool = True,
    ) -> None:
        self.model_dir = model_dir
        self.text_score = text_score
        self.use_angle_cls = use_angle_cls
        self._engine = None

    @classmethod
    def get(cls, model_dir: Path | None = None) -> "OCREngine":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(model_dir=model_dir)
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._instance = None

    def _ensure_loaded(self) -> None:
        if self._engine is not None:
            return

        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            raise OCRNotAvailableError("未安装 rapidocr_onnxruntime，请先配置或安装增强包。") from exc

        kwargs: dict = {
            "text_score": self.text_score,
            "use_angle_cls": self.use_angle_cls,
            "print_verbose": False,
        }

        if self.model_dir and self.model_dir.is_dir():
            det = self.model_dir / "det.onnx"
            rec = self.model_dir / "rec.onnx"
            cls_model = self.model_dir / "cls.onnx"
            keys = self.model_dir / "ppocr_keys_v1.txt"
            if det.is_file():
                kwargs["det_model_path"] = str(det)
            if rec.is_file():
                kwargs["rec_model_path"] = str(rec)
            if cls_model.is_file():
                kwargs["cls_model_path"] = str(cls_model)
            if keys.is_file():
                kwargs["rec_keys_path"] = str(keys)

        try:
            self._engine = RapidOCR(**kwargs)
        except Exception as exc:
            raise OCREngineError(f"初始化 RapidOCR 推理引擎失败：{exc}") from exc

    def recognize_pixmap(self, pix: pymupdf.Pixmap, page_index: int) -> list[OCRLine]:
        """识别 PyMuPDF Pixmap 页面图像并返回 OCRLine 列表。"""
        self._ensure_loaded()
        assert self._engine is not None

        import numpy as np

        # 将 Pixmap 转换为 numpy ndarray（RGB 格式）
        try:
            if pix.n - (1 if pix.alpha else 0) == 4:
                # CMYK 或其他特殊格式先转 RGB
                rgb_pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            else:
                rgb_pix = pix

            arr = np.frombuffer(rgb_pix.samples, dtype=np.uint8).reshape(
                rgb_pix.height, rgb_pix.width, rgb_pix.n
            )
            # 若有 alpha 通道则剥离
            if rgb_pix.alpha:
                arr = arr[:, :, :3]
            elif rgb_pix.n == 1:
                # 灰度转 3 通道
                arr = np.repeat(arr, 3, axis=2)

            raw_results, _elapse = self._engine(arr)
        except Exception as exc:
            raise OCREngineError(f"第 {page_index + 1} 页 OCR 推理失败：{exc}") from exc

        return _convert_rapid_results(raw_results, page_index)


def _convert_rapid_results(raw_results, page_index: int) -> list[OCRLine]:
    """把 RapidOCR 的结果 [[box, text, score], ...] 转换为 OCRLine 列表。

    box 格式为 [[x0, y0], [x1, y1], [x2, y2], [x3, y3]]
    """
    lines: list[OCRLine] = []
    if not raw_results:
        return lines

    for item in raw_results:
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            continue
        box, text, score = item
        if not text or not isinstance(text, str):
            continue

        try:
            pts = [(float(p[0]), float(p[1])) for p in box]
            if len(pts) < 4:
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            bbox = (min(xs), min(ys), max(xs), max(ys))
            polygon = (
                pts[0][0], pts[0][1],
                pts[1][0], pts[1][1],
                pts[2][0], pts[2][1],
                pts[3][0], pts[3][1],
            )
            lines.append(
                OCRLine(
                    text=text.strip(),
                    bbox=bbox,
                    polygon=polygon,
                    confidence=float(score),
                    page_index=page_index,
                )
            )
        except (ValueError, TypeError, IndexError):
            continue

    return lines
