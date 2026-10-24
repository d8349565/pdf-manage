"""从应用 PNG 与文档 SVG 生成 Windows 多尺寸图标。"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QImage, QPainter, QPen
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


def installer_art() -> None:
    """Build branded artwork from the existing logo; no downloaded image assets."""
    font_file = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'msyh.ttc'
    if font_file.is_file():
        QFontDatabase.addApplicationFont(str(font_file))
    logo = QImage(str(ASSETS / 'app.png'))
    image = QImage(492, 942, QImage.Format.Format_RGB32)
    image.fill(QColor('#EDF4EE'))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.scale(3, 3)
    painter.drawImage(QRectF(20, 24, 38, 38), logo)
    font = QFont('Microsoft YaHei UI')
    font.setPixelSize(18)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor('#174A3A'))
    painter.drawText(QRectF(20, 76, 130, 30), '纸序 PDF')
    font.setPixelSize(13)
    font.setBold(False)
    painter.setFont(font)
    painter.drawText(QRectF(20, 112, 130, 46), '让文档处理\n简单有序')
    painter.setPen(QPen(QColor('#D4E1D8'), 1))
    painter.setBrush(QColor('#F8FBF8'))
    painter.drawRoundedRect(QRectF(32, 183, 103, 75), 5, 5)
    painter.setBrush(QColor('white'))
    painter.drawRoundedRect(QRectF(23, 174, 103, 75), 5, 5)
    painter.setPen(QPen(QColor('#0F766E'), 2))
    painter.drawLine(38, 194, 78, 194)
    painter.setPen(QPen(QColor('#BED1C4'), 2))
    for y, end in ((207, 109), (218, 100), (229, 106)):
        painter.drawLine(38, y, end, y)
    font.setPixelSize(8)
    painter.setFont(font)
    painter.setPen(QColor('#526B65'))
    painter.drawText(QRectF(20, 284, 130, 20), '本地处理 · 无需登录')
    painter.end()
    image.save(str(ROOT / 'wizard-large.bmp'))
    image.save(str(ROOT / 'wizard-large.png'))
    small = QImage(162, 162, QImage.Format.Format_RGB32)
    small.fill(QColor('white'))
    painter = QPainter(small)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawImage(QRectF(15, 15, 132, 132), logo)
    painter.end()
    small.save(str(ROOT / 'wizard-small.bmp'))


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
    installer_art()
    app.quit()


if __name__ == "__main__":
    main()
