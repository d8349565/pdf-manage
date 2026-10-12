"""下载 PP-OCRv6-small 模型到本地 OCR 增强包目录。

不依赖 RapidOCR 自身的模型下载机制，避免和升级链路耦合。

PP-OCRv6 官方仓库为 PaddleOCR，但 v6 模型仅在 PaddleOCR 官方发布；
按 yi 用户的合规要求，本脚本默认走 ``--source local``，由用户自行把
``det.onnx`` / ``rec.onnx`` / ``ppocr_keys_v1.txt`` 放到
``%LOCALAPPDATA%\\ZhiXuPDF\\ocr\\`` 中。

``--source official`` 时从 PaddleOCR 官方源下载（需要联网）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

# 确保可以从项目根导入 app 包
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.ocr.model_manager import DEFAULT_INSTALL_DIR, REQUIRED_FILES


OFFICIAL_URLS: dict[str, str] = {
    # PaddleOCR 官方源（请以官方最新地址为准）
    "det.onnx": "https://paddleocr.bj.bcebos.com/PP-OCRv6/chinese/ch_PP-OCRv6_det_server_infer.onnx",
    "rec.onnx": "https://paddleocr.bj.bcebos.com/PP-OCRv6/chinese/ch_PP-OCRv6_rec_server_infer.onnx",
    "ppocr_keys_v1.txt": "https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/release/2.7/ppocr/utils/ppocr_keys_v1.txt",
}


def _download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=60) as r, path.open("wb") as f:
        while chunk := r.read(64 * 1024):
            f.write(chunk)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def install_local(target_dir: Path) -> None:
    """在 ``--source local`` 模式下引导用户手动放置文件。"""
    print(f"目标目录：{target_dir}")
    print("请把以下文件放到该目录：")
    for name in REQUIRED_FILES:
        print(f"  - {name}")
    if not target_dir.exists():
        target_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": "0.1.0",
        "min_app_version": "0.4.0",
        "files": {},
    }
    for name in REQUIRED_FILES:
        path = target_dir / name
        if path.is_file():
            manifest["files"][name] = {"sha256": _sha256(path), "size": path.stat().st_size}
    (target_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest.json 已写入 {target_dir}")


def install_official(target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for name, url in OFFICIAL_URLS.items():
        out = target_dir / name
        if out.is_file() and out.stat().st_size > 1024:
            print(f"已存在 {name}, 跳过下载")
            continue
        print(f"下载 {name} <- {url}")
        try:
            _download(url, out)
        except Exception as exc:  # noqa: BLE001
            print(f"  下载失败：{exc}", file=sys.stderr)
            if out.exists():
                out.unlink()
            raise
    install_local(target_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description="下载/安装 OCR 增强包模型")
    parser.add_argument("--target", default=str(DEFAULT_INSTALL_DIR), help="OCR 增强包安装目录")
    parser.add_argument("--source", choices=("local", "official"), default="local")
    args = parser.parse_args()
    target = Path(args.target)
    if args.source == "official":
        install_official(target)
    else:
        install_local(target)


if __name__ == "__main__":
    main()