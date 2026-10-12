"""OCR 推理引擎封装（RapidOCR + ONNX Runtime CPU）。

本模块是按 *MUST* 设计原则实现：

* 引擎实例只在首次识别时懒加载，避免拖累主程序启动（PRD §1.3）；
* 引擎异常被转换为业务层 :class:`OCRRuntimeError`，避免堆栈直露给用户；
* 不主动读取在线仓库；模型缺失直接抛 :class:`OCRModelMissing`，
  让 :mod:`app.ocr.model_manager` 负责引导用户安装。

PoC 阶段只完成接口与懒加载骨架；具体识别函数 ``recognize_page`` 在 D1 阶段补齐
并接入 RapidOCR。骨架先写出来的好处：

* 让 :mod:`app.ocr.service` 可以独立编写与单元测试；
* 让 UI 在模型缺失时也能展示完整错误文案。
"""
from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Iterable

from app.ocr.contracts import OCRLine


class OCREngineError(RuntimeError):
    """引擎层通用错误基类，向业务层抛出时附带可读文案。"""


class OCRModelMissing(OCREngineError):
    """未检测到 OCR 增强包或关键模型文件。"""


class OCREngine:
    """线程安全的 RapidOCR 引擎封装。

    ``recognize_page`` 接收已经渲染好的页面位图数据，返回 :class:`OCRLine` 列表。
    实例生命周期与主进程一致，按需懒加载。
    """

    _instance: "OCREngine | None" = None
    _lock = Lock()

    def __init__(self, install_dir: Path) -> None:
        self.install_dir = install_dir
        self._engine = None  # RapidOCR 实例占位

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
        """仅供单元测试与重启场景使用，重置单例。"""
        with cls._lock:
            cls._instance = None

    # ------------------------------------------------------------------ #
    # 引擎加载与推理
    # ------------------------------------------------------------------ #

    def _ensure_loaded(self) -> None:
        """按需加载 RapidOCR；PoC 阶段只做存在性检查。"""
        if self._engine is not None:
            return
        det = self.install_dir / "det.onnx"
        rec = self.install_dir / "rec.onnx"
        keys = self.install_dir / "ppocr_keys_v1.txt"
        if not (det.is_file() and rec.is_file() and keys.is_file()):
            raise OCRModelMissing(
                f"OCR 增强包不完整：{self.install_dir}"
            )
        # 真正的 RapidOCR 实例化放到 D1 阶段，本接口先保留结构。
        self._engine = _StubEngine(self.install_dir)

    def recognize_page(self, image) -> list[OCRLine]:
        """识别单张页面图，返回 :class:`OCRLine` 列表。

        ``image`` 为 PIL.Image 或 numpy.ndarray，具体类型由 D1 决定。
        PoC 阶段仅返回空列表；坐标语义、DPI 换算、置信度阈值等在 D1 实测。
        """
        self._ensure_loaded()
        assert self._engine is not None
        return self._engine.recognize(image)


class _StubEngine:
    """PoC 阶段占位实现，后续替换为 RapidOCR。"""

    def __init__(self, install_dir: Path) -> None:
        self.install_dir = install_dir

    def recognize(self, image) -> list[OCRLine]:
        # PoC 阶段返回空，便于 service 单测结构跑通。
        return []


# --------------------------------------------------------------------------- #
# 工具函数：RapidOCR 输出 → OCRLine
# --------------------------------------------------------------------------- #


def lines_from_rapidocr(raw: Iterable, page_index: int) -> list[OCRLine]:
    """把 RapidOCR 的 ``(boxes, texts, scores)`` 输出转换为 :class:`OCRLine` 列表。

    该函数仅在 D1 接入 RapidOCR 后启用，先放这里防止接口漂移。
    """
    boxes, _, scores = None, None, None
    return [
        OCRLine(
            text="",
            bbox=(box[0][0], box[0][1], box[2][0], box[2][1]),
            polygon=tuple(float(v) for v in box),
            confidence=float(score),
            page_index=page_index,
        )
        for box, score in zip(boxes or [], scores or [])
    ]