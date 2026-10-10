"""从应用 PNG 与文档 SVG 生成 Windows 多尺寸图标。"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QGuiApplication, QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT.parent / "app" / "assets"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def _png(image: QImage) -> bytes:
    from PyQt6.QtCore import QByteArray, QBuffer, QIODevice

    blob = QByteArray()
    buf = QBuffer(blob)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    buf.close()
    return bytes(blob)


def write_ico(pngs: list[tuple[int, bytes]]) -> bytes:
    count = len(pngs)
    offset = 6 + 16 * count
    out = struct.pack("<HHH", 0, 1, count)
    payload = b""
    for size, data in pngs:
        out += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,
            0 if size >= 256 else size,
            0,
            0,
            1,
            32,
            len(data),
            offset + len(payload),
        )
        payload += data
    return out + payload


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    for name in ("app", "pdf"):
        image = QImage(str(ASSETS / "app.png")) if name == "app" else None
        renderer = QSvgRenderer(str(ASSETS / "pdf.svg")) if name == "pdf" else None
        if (image is not None and image.isNull()) or (renderer is not None and not renderer.isValid()):
            raise SystemExit(f"图标源无效：{name}")
        pngs = []
        for size in SIZES:
            canvas = QImage(size, size, QImage.Format.Format_ARGB32)
            canvas.fill(Qt.GlobalColor.transparent)
            painter = QPainter(canvas)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            if image is not None:
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.drawImage(QRectF(0, 0, size, size), image)
            else:
                renderer.render(painter)
            painter.end()
            pngs.append((size, _png(canvas)))
            if size == 256:
                canvas.save(str(ROOT / f"{name}-icon.png"))
        dest = ROOT / f"{name}.ico"
        dest.write_bytes(write_ico(pngs))
        print(dest, dest.stat().st_size)
    app.quit()


if __name__ == "__main__":
    main()
