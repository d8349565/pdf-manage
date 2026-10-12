"""模型与推理组件的本地完整性校验。

PoC 阶段只负责：

* 解析 OCR 增强包的本地安装路径（``%LOCALAPPDATA%\\ZhiXuPDF\\ocr\\``，见 PRD §2.1）；
* 检查关键文件是否存在（检测 / 识别 / 字典）；
* 返回结构化的 ``OCREnvironmentStatus``，便于 UI 引导用户安装增强包。

模型加载、推理引擎实例化等重量级动作不在本模块触发，统一由 :mod:`app.ocr.engine`
按需懒加载。
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


DEFAULT_INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ZhiXuPDF" / "ocr"

# PRD §2.1 要求增强包自带的最小文件集，文件名仅作约定，便于增强包打包方维护清单。
REQUIRED_FILES: tuple[str, ...] = (
    "det.onnx",         # PP-OCRv6-small 文字检测模型
    "rec.onnx",         # PP-OCRv6-small 文字识别模型
    "ppocr_keys_v1.txt",  # 字符字典
    "manifest.json",    # 版本与校验清单（PRD §7.3）
)


@dataclass(frozen=True)
class OCREnvironmentStatus:
    """OCR 运行环境状态，由 UI 用于引导用户安装增强包。"""

    install_dir: Path
    is_ready: bool
    missing_files: tuple[str, ...] = ()
    manifest: dict | None = None
    warnings: tuple[str, ...] = ()


def _read_manifest(install_dir: Path) -> dict | None:
    manifest_path = install_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _verify_checksums(install_dir: Path, manifest: dict) -> tuple[str, ...]:
    """对 manifest 里声明的关键文件做 SHA-256 校验。

    PoC 阶段采用轻量校验；正式版打包方在 manifest 中携带更详细的元数据。
    """
    files = manifest.get("files") or {}
    problems: list[str] = []
    for filename, expected in files.items():
        path = install_dir / filename
        if not path.is_file():
            problems.append(f"缺少文件 {filename}")
            continue
        sha = expected.get("sha256") if isinstance(expected, dict) else None
        if not sha:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest.lower() != str(sha).lower():
            problems.append(f"{filename} 校验失败")
    return tuple(problems)


def check_environment(
    install_dir: Path | str | None = None,
    required_files: Iterable[str] = REQUIRED_FILES,
) -> OCREnvironmentStatus:
    """探测 OCR 增强包是否完整可用。

    返回的状态对象永远包含有效路径，便于 UI 给出明确指引。
    """
    path = Path(install_dir) if install_dir else DEFAULT_INSTALL_DIR
    missing = tuple(name for name in required_files if not (path / name).is_file())
    manifest = _read_manifest(path)
    warnings: list[str] = []
    if missing:
        warnings.append("请先安装纸序 PDF 的 OCR 增强包")
    if manifest:
        warnings.extend(_verify_checksums(path, manifest))
    return OCREnvironmentStatus(
        install_dir=path,
        is_ready=not missing and not warnings,
        missing_files=missing,
        manifest=manifest,
        warnings=tuple(warnings),
    )