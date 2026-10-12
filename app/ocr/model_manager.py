"""OCR 运行环境与模型包状态检测。

负责管理与探测 OCR 推理运行环境与模型文件，提供结构化状态供 UI 展示或引导。
按 PRD §2.1，增强包默认位置：%LOCALAPPDATA%\\ZhiXuPDF\\ocr\\
"""
from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path


def get_default_install_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "ZhiXuPDF" / "ocr"
    return Path.home() / ".zhixupdf" / "ocr"


@dataclass(frozen=True)
class OCREnvironmentStatus:
    """OCR 运行环境状态检测结果。"""

    is_ready: bool
    engine_available: bool
    model_dir: Path | None
    missing_items: tuple[str, ...]
    message: str


def is_rapidocr_installed() -> bool:
    """检测 RapidOCR 与 ONNX Runtime 是否在当前 Python 环境中可用。"""
    spec = importlib.util.find_spec("rapidocr_onnxruntime")
    return spec is not None


def check_environment(custom_dir: Path | str | None = None) -> OCREnvironmentStatus:
    """检测当前系统的 OCR 运行环境状态。

    优先策略：
    1. 检查 Python 运行环境中是否已安装 rapidocr_onnxruntime 推理库；
    2. 若已安装，RapidOCR 自带默认轻量 ONNX 模型，直接就绪；
    3. 若指定了增强包目录或默认增强包目录存在自定义模型，校验模型路径；
    4. 若未安装推理库，返回未安装提示并指引用户安装增强包。
    """
    engine_ok = is_rapidocr_installed()
    install_dir = Path(custom_dir) if custom_dir else get_default_install_dir()

    # 检查独立增强包目录
    has_custom_package = False
    custom_model_dir: Path | None = None
    missing: list[str] = []

    if install_dir.is_dir():
        # 若存在增强包目录，检查是否包含必要模型组件
        det = install_dir / "det.onnx"
        rec = install_dir / "rec.onnx"
        keys = install_dir / "ppocr_keys_v1.txt"
        if det.is_file() and rec.is_file():
            has_custom_package = True
            custom_model_dir = install_dir
        else:
            if not det.is_file():
                missing.append("det.onnx")
            if not rec.is_file():
                missing.append("rec.onnx")

    if engine_ok:
        return OCREnvironmentStatus(
            is_ready=True,
            engine_available=True,
            model_dir=custom_model_dir,
            missing_items=(),
            message="OCR 推理引擎与模型已就绪",
        )

    # 引擎未安装
    msg = (
        f"未检测到 OCR 增强包组件。请先安装离线增强包到：\n{install_dir}"
        if not has_custom_package
        else "OCR 增强包组件不完整，请重新安装。"
    )
    return OCREnvironmentStatus(
        is_ready=False,
        engine_available=False,
        model_dir=custom_model_dir,
        missing_items=tuple(missing) or ("rapidocr_onnxruntime",),
        message=msg,
    )
