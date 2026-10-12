"""OCR 推理引擎封装（RapidOCR + ONNX Runtime CPU）。

PoC 之后的正式实现要点：

* 引擎实例按需懒加载，首次 ``recognize_page`` 时才实例化 RapidOCR；
* 所有 RapidOCR 抛出的异常被转换为 :class:`OCREngineError`，
  避免向 UI 暴露 Python 堆栈；
* 模型路径完全交给 :mod:`app.ocr.model_manager` 校验；
* ``recognize_page`` 接受 PyMuPDF Pixmap，返回 :class:`OCRLine` 列表，
  把 RapidOCR 的 ``[box, text, score]`` 列表转换为契约对象。
"""
from __future__ import annotations

import logging
from pathlib import Path
from threading import Lock
from typing import Iterable

from app.ocr.contracts import OCRLine


log = logging.getLogger(__name__)


class OCREngineError(RuntimeError):
    """引擎层通用错误基类。"""


class OCRModelMissing(OCREngineError):
    """未检测到 OCR 增强包或关键模型文件。"""


class OCREngine:
    """线程安全的 RapidOCR 引擎封装。

    接受已经渲染好的 :class:`pymupdf.Pixmap`，返回 :class:`OCRLine` 列表。
    """

    _instance: "OCREngine | None" = None
    _lock = Lock()

    def __init__(self, install_dir: Path, *, text_score: float = 0.5,
                 box_thresh: float = 0.5, use_angle_cls: bool = True) -> None:
        self.install_dir = install_dir
        self._engine = None
        self._text_score = text_score
        self._box_thresh = box_thresh
        self._use_angle_cls = use_angle_cls

    # ------------------------------------------------------------------ #
    # 单例管理
    # ------------------------------------------------------------------ #

    @classmethod
    def get(cls, install_dir: Path | None = None) -> "OCREngine":
        """获取（或懒加载）全局唯一引擎实例。"""
        with cls._lock:
            if cls._instance is None:
                if install_dir is None:
                    raise OCRModelMissing("未指定 OCR 增强包安装路径")
                cls._instance = cls(install_dir)
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        """仅供单元测试使用，重置单例。"""
        with cls._lock:
            cls._instance = None

    # ------------------------------------------------------------------ #
    # 引擎加载与推理
    # ------------------------------------------------------------------ #

    def _ensure_loaded(self) -> None:
        if self._engine is not None:
            return
        det = self.install_dir / "det.onnx"
        rec = self.install_dir / "rec.onnx"
        keys = self.install_dir / "ppocr_keys_v1.txt"
        cls_path = self.install_dir / "cls.onnx"
        if not (det.is_file() and rec.is_file() and keys.is_file()):
            raise OCRModelMissing(
                f"OCR 增强包不完整：{self.install_dir}（需要 det/rec 模型 + 字典）"
            )
        # 延迟导入：避免主程序启动时强制加载 OCR 推理库
        from rapidocr_onnxruntime import RapidOCR  # type: ignore

        kwargs = dict(
            det_model_path=str(det),
            rec_model_path=str(rec),
            rec_keys_path=str(keys),
            text_score=self._text_score,
            use_angle_cls=self._use_angle_cls,
            print_verbose=False,
        )
        if cls_path.is_file():
            kwargs["cls_model_path"] = str(cls_path)
        try:
            self._engine = RapidOCR(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise OCREngineError(f"RapidOCR 初始化失败：{exc}") from exc

    def recognize_page(self, pixmap) -> list[OCRLine]:
        """识别单张 Pixmap，返回 :class:`OCRLine` 列表。"""
        self._ensure_loaded()
        assert self._engine is not None

        # PyMuPDF Pixmap 转 numpy ndarray（RapidOCR 主路径接受）
        import numpy as np  # 局部导入，避免引擎未加载时占用主进程

        if hasattr(pixmap, "tobytes") and hasattr(pixmap, "samples"):
            arr = np.frombuffer(pixmap.samples, dtype=np.uint8)
            arr = arr.reshape(pixmap.height, pixmap.width, pixmap.n)
            if pixmap.n == 4:
                arr = arr[:, :, :3]
        else:
            arr = pixmap  # 已经传 ndarray

        try:
            result, _elapse = self._engine(arr)
        except Exception as exc:  # noqa: BLE001
            raise OCREngineError(f"OCR 推理失败：{exc}") from exc

        return _rapid_to_lines(result, page_index=getattr(pixmap, "_page_index", 0))


def _rapid_to_lines(raw, *, page_index: int) -> list[OCRLine]:
    """把 RapidOCR 输出 ``[[box, text, score], ...]`` 转换为 :class:`OCRLine`。

    RapidOCR 返回的 box 是 ``[(x0, y0), (x1, y1), (x2, y2), (x3, y3)]`` 四边形。
    我们把它展开成 ``bbox`` 矩形和 ``polygon`` 八元组。
    """
    lines: list[OCRLine] = []
    if not raw:
        return lines
    for entry in raw:
        if not isinstance(entry, (list, tuple)) or len(entry) != 3:
            continue
        box, text, score = entry
        if not text or not isinstance(text, str):
            continue
        pts = [(float(p[0]), float(p[1])) for p in box]
        if len(pts) < 4:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        polygon: tuple[float, ...] = tuple(
            v
            for pair in pts[:4]
            for v in pair
        )
        lines.append(
            OCRLine(
                text=text,
                bbox=(min(xs), min(ys), max(xs), max(ys)),
                polygon=polygon,
                confidence=float(score),
                page_index=page_index,
            )
        )
    return lines


def lines_from_rapidocr(raw: Iterable, page_index: int) -> list[OCRLine]:
    """对外暴露的 RapidOCR → :class:`OCRLine` 转换工具。"""
    return _rapid_to_lines(raw, page_index=page_index)