"""Application palettes and opt-in styling; document colors stay untouched."""
from __future__ import annotations

from dataclasses import dataclass
import re

from PyQt6.QtCore import QObject, QSettings, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPalette, QPixmap
from PyQt6.QtWidgets import QAbstractButton, QApplication, QGraphicsView


@dataclass(frozen=True)
class Theme:
    key: str
    name: str
    description: str
    colors: dict[str, str]


_LIGHT = dict(
    on_accent="#FCFCFD", danger="#B42332", danger_bg="#FBECEE",
    danger_border="#E9B6BE", warning="#895412", warning_bg="#FFF3D9",
    warning_border="#DBB775", success="#32634D",
)

THEMES = (
    Theme("graphite", "石墨", "中性灰白，简洁专注", dict(
        **_LIGHT, window="#F0F1F2", surface="#FCFCFD", subtle="#E9EBED",
        canvas="#E3E5E7", text="#25282B", muted="#62686D", disabled="#828A94",
        border="#D4D8DC", strong_border="#AAB2BA", hover="#E2E5E8",
        selected="#D5D9DE", accent="#45494D", accent_hover="#303438",
        accent_pressed="#232629", shadow="#CDD1D5",
    )),
    Theme("mountain", "远山", "雾蓝底色，沉静清爽", dict(
        **_LIGHT, window="#EDF2F8", surface="#FAFCFF", subtle="#E5ECF5",
        canvas="#DAE3EF", text="#24354B", muted="#586C83", disabled="#7D8FA4",
        border="#CAD6E6", strong_border="#9AADC5", hover="#E2EBF6",
        selected="#D2E1F3", accent="#315C8A", accent_hover="#25496E",
        accent_pressed="#1D3B5A", shadow="#C1CEDE",
    )),
    Theme("forest", "原版绿", "保留原版青绿色与浅色背景", dict(
        _LIGHT, on_accent="#FFFFFF", window="#F3F6F5", surface="#FFFFFF", subtle="#EEF3F0",
        canvas="#F4F7F6", text="#134E4A", muted="#4D6B67", disabled="#94A3B8",
        border="#D7E8E4", strong_border="#99B2AC", hover="#F0FDFA",
        selected="#CCFBF1", accent="#0F766E", accent_hover="#0D9488",
        accent_pressed="#115E59", shadow="#E4EAE7", danger="#B91C1C",
        danger_bg="#FEF2F2", danger_border="#FECACA", warning="#D97706",
        warning_bg="#FFFBEB", warning_border="#FDE68A", success="#059669",
    )),
    Theme("terracotta", "赤陶", "暖灰底色，陶红点缀", dict(
        **_LIGHT, window="#F6F0EC", surface="#FFFCFA", subtle="#EFE5DE",
        canvas="#E9DCD3", text="#44332D", muted="#766157", disabled="#A18D82",
        border="#E2CFC3", strong_border="#C5A391", hover="#F1E2D9",
        selected="#EAD2C4", accent="#8A4631", accent_hover="#7C402E",
        accent_pressed="#663426", shadow="#D7C2B5",
    )),
    Theme("night", "夜航", "深色界面，适合暗处使用", dict(
        window="#1D2229", surface="#262D36", subtle="#222830", canvas="#151A20",
        text="#E5EAF0", muted="#ABB6C4", disabled="#768292", border="#3B4654",
        strong_border="#64758B", hover="#303D4D", selected="#31465E",
        accent="#A7C5EA", accent_hover="#C0D7F2", accent_pressed="#8DB3DE",
        on_accent="#1A2839", shadow="#11161C", danger="#FFA9A9",
        danger_bg="#472F36", danger_border="#895259", warning="#F1C87B",
        warning_bg="#3E3525", warning_border="#8A7345", success="#8FD0B0",
    )),
)
DEFAULT_THEME = "graphite"
THEME_SETTING = "appearance/theme"
# The paper surface remains white even when the surrounding interface is dark.
PAPER_HINT_COLOR = "#53635B"
_THEMES_BY_KEY = {theme.key: theme for theme in THEMES}

# The existing QSS uses several near-identical shades for the same purpose.
# Map those source colors to roles once, without recoloring PDF/annotation data.
_SOURCE_COLORS = {
    "accent": "0F766E 0F6557 149587 3E6955",
    "accent_hover": "0D9488",
    "accent_pressed": "115E59",
    "text": "134E4A 173C36 174A3A 1E293B 1F2937 254C45 263F39 29413D 29483B 334155 33483F 344D42 36564F",
    "muted": "475569 4B5563 4D6B67 526B63 526B65 536762 53685B 53635B 536F65 5D6F68 5E7266 5E7B73 60756A 607D6C 64748B 65796E 6A8178 6C8B7C 72847D 738078 3B6654",
    "disabled": "7E9187 84958F 8B9B95 94A3B8 9BAAA1",
    "strong_border": "5EEAD4 72A699 80B69E 91B7A9 99B2AC 99BDB7 99C7B2 BDD8CA B6C2C0 9AA8B2",
    "border": "C3D4C9 C5D5D2 C5D8D5 C8DDD0 C9D9D3 CBD5E1 CBDCD4 CED8D1 D5DFD6 D5E2DE D5E5DD D6E3DD D7E3DF D7E8E4 D8E0E8 D8E4DE DCE4D8 DCE5DF DCE8E5 DDE5E9 DDEBE5 DFE8E2 DFE8E3 E0EAE5 E2E8F0 E2EEEC E3EBE6 E3ECE8 E5E7EB E5EEEC E1E7EB CAD5CD E1E7E4 DCE7E4",
    "selected": "CCFBF1 D1FAE5 D9EEEA DDF1EA E3F1E9 E4F2ED 99F6E4",
    "hover": "DBE7E0 DBEAE4 DCECE4 DCEDE2 DDE9E2 DDECE8 E1EEE7 E1F3EF E2EEEB E2F2E9 E6F1EB E6F7F4 EAF1EE EAF4EF EDF4F1 EDF5F0 EDF6F1 F0F6F3 F0F7F4 F0F7F5 F0FAF5 F0FDFA F1F6F3",
    "subtle": "E8F0ED E8F3F1 E9EEF2 E9F3ED EEF3F0 EEF4F2 EFF3F0 F0F4EF F1F5F9 F7FAF9 F8FAF9 F8FAFA F8FBFA",
    "window": "F3F4F6 F3F6F5 F5F7F6 F5F7FA",
    "surface": "FAFCFB FBFCFA FCFFFD F6FCF9 FFFFFF",
    "canvas": "E8EDF2 E8EFEB E9EFEC E9EEEC EAF0EB EDF2F1 F4F7F6 E8F1F4",
    "shadow": "E4EAE7",
    "danger": "A53930 B91C1C DC2626",
    "danger_bg": "FEE2E2 FEF2F2",
    "danger_border": "FCA5A5 FECACA",
    "warning": "713F12 92400E B45309 D97706",
    "warning_bg": "FEFCE8 FFFBEB",
    "warning_border": "FDE047 FDE68A",
    "success": "059669",
}
_COLOR_ROLES = {"#" + color: role for role, colors in _SOURCE_COLORS.items() for color in colors.split()}
_COLOR = re.compile(r"#[0-9a-fA-F]{6}\b|\bwhite\b|\bblack\b", re.I)
_DECLARATION = re.compile(r"([\w-]+)(\s*:\s*)([^;{}]+)")


def current_theme() -> Theme:
    app = QApplication.instance()
    key = app.property("uiTheme") if app else DEFAULT_THEME
    return _THEMES_BY_KEY.get(key, _THEMES_BY_KEY[DEFAULT_THEME])


def ui_color(value: str, *, foreground: bool = False, theme: Theme | None = None) -> str:
    theme = theme or current_theme()
    colors = theme.colors
    if value in colors:
        return colors[value]
    if theme.key == "forest":
        return value  # Preserve each original shade, including context-specific colors.
    value = value.upper()
    if value in ("WHITE", "#FFFFFF", "#F8FAFC"):
        return colors["on_accent" if foreground else "surface"]
    if value == "BLACK":
        return colors["text"]
    return colors.get(_COLOR_ROLES.get(value), value)


def themed_stylesheet(source: str, theme: Theme | None = None) -> str:
    theme = theme or current_theme()
    if theme.key == "forest":
        return source

    def declaration(match):
        prop, colon, value = match.groups()
        foreground = prop in ("color", "selection-color")
        return prop + colon + _COLOR.sub(
            lambda color: ui_color(color.group(), foreground=foreground, theme=theme), value)

    return _DECLARATION.sub(declaration, source)


def set_theme_style(widget, source: str) -> None:
    """Keep the source so repeated switches never transform already-themed QSS."""
    widget.setProperty("themeStyleSource", source)
    widget.setStyleSheet(themed_stylesheet(source))


def theme_swatch(theme: Theme) -> QIcon:
    """A palette sample, rather than a miniature imitation of the application."""
    pixmap = QPixmap(72, 36)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    for x, role in ((0, "window"), (24, "surface"), (48, "accent")):
        painter.fillRect(x, 0, 24, 36, QColor(theme.colors[role]))
    painter.setPen(QColor(theme.colors["strong_border"]))
    painter.drawRect(0, 0, 71, 35)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


class ThemeManager(QObject):
    changed = pyqtSignal(str)

    def apply(self, key: str, settings=None) -> bool:
        if key not in _THEMES_BY_KEY:
            return False
        app = QApplication.instance()
        app.setProperty("uiTheme", key)
        colors = _THEMES_BY_KEY[key].colors
        palette = QPalette()
        roles = {
            "Window": "window", "WindowText": "text", "Base": "surface",
            "AlternateBase": "subtle", "Text": "text", "Button": "surface",
            "ButtonText": "text", "Highlight": "selected", "HighlightedText": "text",
            "ToolTipBase": "text", "ToolTipText": "surface", "Link": "accent",
            "LinkVisited": "accent", "PlaceholderText": "muted", "Light": "surface",
            "Midlight": "subtle", "Mid": "border", "Dark": "strong_border", "Shadow": "shadow",
        }
        for qt_role, role in roles.items():
            palette.setColor(getattr(QPalette.ColorRole, qt_role), QColor(colors[role]))
        for qt_role in ("WindowText", "Text", "ButtonText", "PlaceholderText"):
            palette.setColor(QPalette.ColorGroup.Disabled, getattr(QPalette.ColorRole, qt_role),
                             QColor(colors["disabled"]))
        app.setPalette(palette)
        from app.icons import refresh_theme_icon
        seen_actions = set()
        for widget in app.allWidgets():
            source = widget.property("themeStyleSource")
            if isinstance(source, str):
                widget.setStyleSheet(themed_stylesheet(source))
            canvas = widget.property("themeCanvas")
            if canvas and isinstance(widget, QGraphicsView):
                widget.setBackgroundBrush(QColor(ui_color(canvas)))
                widget.resetCachedContent()
            if isinstance(widget, QAbstractButton):
                refresh_theme_icon(widget)
            for action in widget.findChildren(QAction, options=Qt.FindChildOption.FindDirectChildrenOnly):
                if action not in seen_actions:
                    refresh_theme_icon(action)
                    seen_actions.add(action)
            widget.update()
        self.changed.emit(key)
        if settings is not None:
            settings.setValue(THEME_SETTING, key)
            settings.sync()
            return settings.status() == QSettings.Status.NoError
        return True


def theme_manager() -> ThemeManager:
    app = QApplication.instance()
    manager = getattr(app, "_theme_manager", None)
    if manager is None:
        manager = ThemeManager(app)
        app._theme_manager = manager
    return manager


def initialize_theme(settings) -> None:
    key = settings.value(THEME_SETTING, DEFAULT_THEME)
    theme_manager().apply(key if isinstance(key, str) and key in _THEMES_BY_KEY else DEFAULT_THEME)
