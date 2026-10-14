from __future__ import annotations

from functools import lru_cache

from PyQt6.QtCore import QByteArray, QRectF, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication

# 24×24 outline glyphs, 1.5px stroke, 3px inset so they stay crisp at toolbar size.
# Metaphors: document for PDF, tray-arrow for save, copy for save-as.
_PATHS: dict[str, str] = {
    "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5"/>',
    "folder-open": (
        '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/>'
        '<path d="M14 3v5h5"/>'
    ),
    "folder-plus": (
        '<path d="M13 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h6"/>'
        '<path d="M13 3v5h5"/>'
        '<path d="M16 14h6"/><path d="M19 11v6"/>'
    ),
    "save": (
        '<path d="M12 4v11"/>'
        '<path d="m7.5 10.5 4.5 4.5 4.5-4.5"/>'
        '<path d="M5 18h14"/>'
    ),
    "save-as": (
        '<rect width="13" height="13" x="8" y="8" rx="2"/>'
        '<path d="M4 16V6a2 2 0 0 1 2-2h10"/>'
    ),
    "print": (
        '<path d="M6 9V3h12v6"/>'
        '<path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/>'
        '<path d="M6 14h12v7H6z"/>'
    ),
    "layout-grid": (
        '<rect width="7" height="7" x="4" y="4" rx="1.5"/>'
        '<rect width="7" height="7" x="13" y="4" rx="1.5"/>'
        '<rect width="7" height="7" x="4" y="13" rx="1.5"/>'
        '<rect width="7" height="7" x="13" y="13" rx="1.5"/>'
    ),
    "table": (
        '<path d="M12 3v18"/>'
        '<rect width="18" height="18" x="3" y="3" rx="2"/>'
        '<path d="M3 9h18"/>'
        '<path d="M3 15h18"/>'
    ),
    "zoom-out": (
        '<circle cx="10.25" cy="10.25" r="6.25"/>'
        '<path d="M14.8 14.8 20 20"/>'
        '<path d="M7.2 10.25h6.1"/>'
    ),
    "zoom-in": (
        '<circle cx="10.25" cy="10.25" r="6.25"/>'
        '<path d="M14.8 14.8 20 20"/>'
        '<path d="M10.25 7.6v5.3"/>'
        '<path d="M7.6 10.25h5.3"/>'
    ),
    "fit-width": (
        '<path d="M9 5H6.5A1.5 1.5 0 0 0 5 6.5V9"/>'
        '<path d="M15 5h2.5A1.5 1.5 0 0 1 19 6.5V9"/>'
        '<path d="M9 19H6.5A1.5 1.5 0 0 1 5 17.5V15"/>'
        '<path d="M15 19h2.5a1.5 1.5 0 0 0 1.5-1.5V15"/>'
    ),
    "rotate-ccw": (
        '<path d="M7.2 7.2a7.5 7.5 0 1 0 2.4-2.1"/>'
        '<path d="M4.5 4.5v5h5"/>'
    ),
    "rotate-cw": (
        '<path d="M16.8 7.2a7.5 7.5 0 1 1-2.4-2.1"/>'
        '<path d="M19.5 4.5v5h-5"/>'
    ),
    "trash": (
        '<path d="M5 8h14"/>'
        '<path d="M17.5 8v11.5a1.5 1.5 0 0 1-1.5 1.5h-8a1.5 1.5 0 0 1-1.5-1.5V8"/>'
        '<path d="M9.5 8V6a1.5 1.5 0 0 1 1.5-1.5h2A1.5 1.5 0 0 1 14.5 6v2"/>'
        '<path d="M10 12v4M14 12v4"/>'
    ),
    "select-all": (
        '<rect width="16" height="16" x="4" y="4" rx="2"/>'
        '<path d="m8 12.2 2.4 2.4 5.2-5.6"/>'
    ),
    "scissors": (
        '<circle cx="6.5" cy="7" r="2.5"/>'
        '<circle cx="6.5" cy="17" r="2.5"/>'
        '<path d="M8.7 8.7 20 18"/>'
        '<path d="M8.7 15.3 20 6"/>'
    ),
    "split": (
        '<rect width="6.5" height="16" x="4" y="4" rx="1.5"/>'
        '<rect width="6.5" height="16" x="13.5" y="4" rx="1.5"/>'
    ),
    "arrow-left": (
        '<path d="M19 12H6"/>'
        '<path d="m11 6-6 6 6 6"/>'
    ),
    "undo": '<path d="m9 4-5 5 5 5"/><path d="M4 9h9a7 7 0 0 1 0 14"/>',
    "redo": '<path d="m15 4 5 5-5 5"/><path d="M20 9h-9a7 7 0 0 0 0 14"/>',
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "panel-left": (
        '<rect width="18" height="18" x="3" y="3" rx="2"/>'
        '<path d="M9 3v18"/>'
    ),
    "panel-right": (
        '<rect width="18" height="18" x="3" y="3" rx="2"/>'
        '<path d="M15 3v18"/>'
    ),
    "highlighter": (
        '<path d="m9 11-6 6v3h3l6-6"/>'
        '<path d="m22 12-4.6 4.6a2 2 0 0 1-2.8 0l-5.2-5.2a2 2 0 0 1 0-2.8L14 4"/>'
    ),
    "pen": (
        '<path d="M12 20h9"/>'
        '<path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>'
    ),
    "sticky-note": (
        '<path d="M15 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
        '<path d="M15 3v5h5"/>'
    ),
    "type": (
        '<polyline points="4 7 4 4 20 4 20 7"/>'
        '<line x1="9" y1="20" x2="15" y2="20"/>'
        '<line x1="12" y1="4" x2="12" y2="20"/>'
    ),
    "text-box": (
        '<rect x="3" y="4" width="18" height="16" rx="1" stroke-dasharray="2 2"/>'
        '<path d="M8 9V8h8v1M12 8v9M10 17h4"/>'
    ),
    "text-edit": (
        '<path d="M3 7V5h10v2M8 5v11M5 16h6M3 20h8"/>'
        '<path d="m13 17 6-6 3 3-6 6-4 1zM18 12l3 3"/>'
    ),
    "annotation-select": (
        '<path d="M4 8V4h4M12 4h4M20 4v4M4 12v4M4 20h4M12 20h3"/>'
        '<path d="m13 10 2 10 2-4 4-2z"/>'
    ),
    "note-comment": (
        '<path d="M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-5 3V6a2 2 0 0 1 1-2z"/>'
        '<path d="M8 9h9M8 13h6"/>'
    ),
    "ink-pen": (
        '<path d="M3 17c3-8 5 5 8-2M3 21h10"/>'
        '<path d="m12 12 7-7 3 3-7 7-4 1zM18 6l3 3"/>'
    ),
    "mouse-pointer": (
        '<path d="m3 3 7.07 16.97 2.51-7.39 7.39-2.51L3 3z"/>'
        '<path d="m13 13 6 6"/>'
    ),
    "bookmark": (
        '<path d="m19 21-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/>'
    ),
    "square": (
        '<rect width="18" height="18" x="3" y="3" rx="2"/>'
    ),
    "circle": (
        '<circle cx="12" cy="12" r="9"/>'
    ),
    "arrow-up-right": (
        '<path d="M7 17 17 7"/>'
        '<path d="M7 7h10v10"/>'
    ),
    "underline": (
        '<path d="M6 3v7a6 6 0 0 0 12 0V3"/>'
        '<line x1="4" y1="21" x2="20" y2="21"/>'
    ),
    "x": (
        '<line x1="18" y1="6" x2="6" y2="18"/>'
        '<line x1="6" y1="6" x2="18" y2="18"/>'
    ),
    "check": '<path d="m5 12 4 4L19 6"/>',
    "move": (
        '<path d="M12 3v18M3 12h18"/>'
        '<path d="m9 6 3-3 3 3m-6 12 3 3 3-3M6 9l-3 3 3 3m12-6 3 3-3 3"/>'
    ),
    "win-min": '<path d="M5 12h14"/>',
    "win-max": '<rect width="12" height="12" x="6" y="6" rx="1.2"/>',
    "win-restore": (
        '<rect width="9" height="9" x="8.5" y="6" rx="1"/>'
        '<path d="M6.5 9.5H6a1 1 0 0 0-1 1V18a1 1 0 0 0 1 1h7.5a1 1 0 0 0 1-1v-.5"/>'
    ),
    "win-close": (
        '<path d="M6.5 6.5 17.5 17.5"/>'
        '<path d="M17.5 6.5 6.5 17.5"/>'
    ),
}

_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="{color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
    "{body}</svg>"
)


def _pixmap(name: str, color: str, logical: int, scale: float | None = None) -> QPixmap:
    if scale is None:
        app = QApplication.instance()
        scale = max(float(app.devicePixelRatio()) if app else 1.0, 2.0)
    px = max(1, int(round(logical * scale)))
    svg = _SVG.format(color=color, body=_PATHS[name]).encode("utf-8")
    renderer = QSvgRenderer(QByteArray(svg))
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    inset = px * 0.06
    renderer.render(painter, QRectF(inset, inset, px - 2 * inset, px - 2 * inset))
    painter.end()
    pm.setDevicePixelRatio(scale)
    return pm


@lru_cache(maxsize=256)
def _cached_toolbar_icon(name: str, color: str, size: int, scale: float) -> QIcon:
    icon = QIcon()
    pm = _pixmap(name, color, size, scale)
    disabled = _pixmap(name, "#94A3B8", size, scale)
    for mode in (QIcon.Mode.Normal, QIcon.Mode.Active, QIcon.Mode.Selected):
        icon.addPixmap(pm, mode)
    icon.addPixmap(disabled, QIcon.Mode.Disabled)
    return icon


def toolbar_icon(name: str, color: str = "#0F766E", size: int = 20) -> QIcon:
    app = QApplication.instance()
    scale = max(float(app.devicePixelRatio()) if app else 1.0, 2.0)
    # 返回共享像素的副本，调用方修改 QIcon 时不会污染缓存。
    return QIcon(_cached_toolbar_icon(name, color, size, scale))


def brand_icon() -> QIcon:
    """窗口、任务栏与 EXE 共用同一份 PDF 品牌图。"""
    from pathlib import Path
    return QIcon(str(Path(__file__).resolve().parent / "assets" / "app.png"))
