"""Application palettes and opt-in styling; document colors stay untouched."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import tempfile
from time import monotonic

from PyQt6.QtCore import QDir, QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSettings, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPainter, QPalette, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import (
    QAbstractButton, QApplication, QComboBox, QFrame, QGraphicsView, QMenu, QProxyStyle, QStyle,
    QStyledItemDelegate, QWidgetAction,
)


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


_CHEVRON_SEARCH_PREFIX = "zhixu-ui"
_CHEVRON_ICON_DIR: Path | None = None
_CHEVRON_DEFAULT_URL = f"{_CHEVRON_SEARCH_PREFIX}:chevron-526b65.svg"
_CHEVRON_DISABLED_URL = f"{_CHEVRON_SEARCH_PREFIX}:chevron-94a3b8.svg"


def chevron_icon_url(color_hex: str) -> str:
    """Write a tiny crisp SVG chevron for color_hex and return its Qt search-path URL."""
    global _CHEVRON_ICON_DIR
    clean = color_hex.lstrip("#").lower()
    if not re.fullmatch(r"[0-9a-f]{6}", clean):
        clean = "526b65"
    filename = f"chevron-{clean}.svg"
    try:
        if _CHEVRON_ICON_DIR is None:
            base = Path(tempfile.gettempdir()) / "zhixupdf-ui-icons"
            base.mkdir(parents=True, exist_ok=True)
            QDir.addSearchPath(_CHEVRON_SEARCH_PREFIX, str(base))
            _CHEVRON_ICON_DIR = base
        target = _CHEVRON_ICON_DIR / filename
        if not target.is_file():
            svg = (
                '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
                f'<path d="M2.5 4.25L6 7.75L9.5 4.25" fill="none" stroke="#{clean}" '
                'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>\n'
            )
            target.write_text(svg, encoding="utf-8")
    except OSError:
        return ":/qt-project.org/styles/commonstyle/images/arrow-down-16.png"
    return f"{_CHEVRON_SEARCH_PREFIX}:{filename}"


def ensure_chevron_icons() -> tuple[str, str]:
    """Ensure default and themed combobox chevron SVGs exist on disk."""
    default_url = chevron_icon_url("#526B65")
    disabled_url = chevron_icon_url("#94A3B8")
    for theme in THEMES:
        chevron_icon_url(theme.colors["muted"])
        chevron_icon_url(theme.colors["disabled"])
    return default_url, disabled_url


def themed_stylesheet(source: str, theme: Theme | None = None) -> str:
    theme = theme or current_theme()
    if theme.key == "forest":
        return source

    def declaration(match):
        prop, colon, value = match.groups()
        foreground = prop in ("color", "selection-color")
        return prop + colon + _COLOR.sub(
            lambda color: ui_color(color.group(), foreground=foreground, theme=theme), value)

    result = _DECLARATION.sub(declaration, source)
    if _CHEVRON_DEFAULT_URL in result:
        result = result.replace(_CHEVRON_DEFAULT_URL, chevron_icon_url(theme.colors["muted"]))
    if _CHEVRON_DISABLED_URL in result:
        result = result.replace(_CHEVRON_DISABLED_URL, chevron_icon_url(theme.colors["disabled"]))
    return result


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


def _blend_color(base_hex: str, overlay_hex: str, ratio: float) -> QColor:
    base, over = QColor(base_hex), QColor(overlay_hex)
    inv = 1.0 - ratio
    return QColor(
        round(base.red() * inv + over.red() * ratio),
        round(base.green() * inv + over.green() * ratio),
        round(base.blue() * inv + over.blue() * ratio),
    )


def _strip_mnemonic(text: str) -> str:
    return re.sub(r"&([^&])", r"\1", text).replace("&&", "&")


class _ComboItemDelegate(QStyledItemDelegate):
    """Paint crisp rounded dropdown items with an active checkmark and zero FocusRect clutter."""

    def __init__(self, combo: QComboBox):
        super().__init__(combo)
        self._combo = combo

    def sizeHint(self, option, index):
        base = super().sizeHint(option, index)
        explicit = index.data(Qt.ItemDataRole.SizeHintRole)
        if isinstance(explicit, QSize) and explicit.height() > 0:
            return QSize(base.width() + 24, max(28, explicit.height()))
        return QSize(base.width() + 28, max(30, base.height() + 10))

    def paint(self, painter, option, index):
        colors = current_theme().colors
        is_dark = QColor(colors["surface"]).lightness() < 128
        hover_fill = _blend_color(colors["surface"], colors["accent"], 0.16 if is_dark else 0.09)
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        hovered = enabled and bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected = enabled and bool(option.state & QStyle.StateFlag.State_Selected)
        is_current = index.row() == self._combo.currentIndex()

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pill = QRectF(option.rect).adjusted(4.0, 1.0, -4.0, -1.0)
        if hovered or selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(hover_fill)
            painter.drawRoundedRect(pill, 5.5, 5.5)

        left_x = pill.left() + 10.0
        deco = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(deco, QIcon) and not deco.isNull():
            icon_rect = QRect(int(left_x), int(pill.center().y() - 8), 16, 16)
            deco.paint(painter, icon_rect)
            left_x += 22.0

        item_font = index.data(Qt.ItemDataRole.FontRole)
        font = QFont(item_font) if isinstance(item_font, QFont) else QFont(option.font)
        if font.pixelSize() <= 0 and font.pointSize() <= 0:
            font.setPixelSize(13)
        if is_current:
            font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)

        if not enabled:
            fg = colors["disabled"]
        elif is_current:
            fg = colors["accent"]
        else:
            fg = colors["text"]
        painter.setPen(QColor(fg))

        right_pad = 26.0 if is_current else 10.0
        text_rect = QRectF(left_x, pill.top(), max(0.0, pill.right() - left_x - right_pad), pill.height())
        elided = painter.fontMetrics().elidedText(str(index.data() or ""), Qt.TextElideMode.ElideRight, int(text_rect.width()))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided)

        if is_current:
            from app.icons import toolbar_icon
            ck_rect = QRect(int(pill.right() - 22), int(pill.center().y() - 8), 16, 16)
            toolbar_icon("check", colors["accent"] if enabled else colors["disabled"], 16, themed=False).paint(
                painter, ck_rect
            )
        painter.restore()


def _configure_combo_popup(combo: QComboBox) -> None:
    if combo.property("_comboPopupStyled") or combo.isEditable() or combo.property("_customPopup"):
        return
    combo.setProperty("_comboPopupStyled", True)
    combo.setItemDelegate(_ComboItemDelegate(combo))
    view = combo.view()
    if view is None:
        return
    view.setMouseTracking(True)
    container = view.window()
    if container is not None and container is not combo.window():
        container.setObjectName("comboPopupFrame")
        container.setProperty("_ownerCombo", combo)
        if isinstance(container, QFrame):
            container.setFrameShape(QFrame.Shape.NoFrame)
            container.setLineWidth(0)
        container.setWindowFlags(
            container.windowFlags()
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        container.setStyleSheet("QFrame#comboPopupFrame { background: transparent; border: none; }")


def _configure_menu_window(menu: QMenu) -> None:
    if menu.property("_menuWindowStyled") or menu.objectName() == "readingSettingsMenu":
        return
    if any(isinstance(a, QWidgetAction) for a in menu.actions()):
        return
    menu.setProperty("_menuWindowStyled", True)
    menu.setWindowFlags(
        menu.windowFlags()
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.NoDropShadowWindowHint
    )
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)


class _MenuPainter(QObject):
    """Render crisp QMenus and manage clean QComboBox popups with double-click protection."""

    def eventFilter(self, watched, event):
        kind = event.type()

        # Auto-configure non-editable QComboBox popups across the application.
        if isinstance(watched, QComboBox) and kind in (QEvent.Type.Polish, QEvent.Type.Show, QEvent.Type.MouseButtonPress):
            _configure_combo_popup(watched)
            return False

        # Clean 4px gap + accidental double-click guard on QComboBox popup containers.
        if watched.objectName() == "comboPopupFrame":
            combo = watched.property("_ownerCombo")
            if isinstance(combo, QComboBox) and not combo.isEditable():
                if kind == QEvent.Type.Show:
                    watched.setProperty("_shownAt", monotonic())
                    combo_top = combo.mapToGlobal(QPoint(0, 0)).y()
                    combo_bottom = combo_top + combo.height()
                    if watched.y() >= combo_bottom - 2:
                        watched.move(watched.x(), watched.y() + 4)
                    elif watched.y() + watched.height() <= combo_top + 2:
                        watched.move(watched.x(), watched.y() - 4)
                    return False
                if kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
                    shown_at = watched.property("_shownAt") or 0.0
                    if monotonic() - shown_at < 0.28:
                        trigger_rect = QRect(combo.mapToGlobal(QPoint(0, 0)), combo.size())
                        if trigger_rect.contains(event.globalPosition().toPoint()):
                            return True
            return False

        if isinstance(watched, QMenu) and kind == QEvent.Type.Polish:
            _configure_menu_window(watched)
            return False

        if kind != QEvent.Type.Paint or not isinstance(watched, QMenu):
            return False
        if watched.objectName() == "readingSettingsMenu":
            return False
        actions = [a for a in watched.actions() if a.isVisible()]
        if any(isinstance(a, QWidgetAction) for a in actions):
            return False
        # Preserve native Qt scroll arrows when a tall menu overflows the screen.
        if watched.height() < watched.sizeHint().height() - 4:
            return False

        colors = current_theme().colors
        is_dark = QColor(colors["surface"]).lightness() < 128
        hover_fill = _blend_color(colors["surface"], colors["accent"], 0.16 if is_dark else 0.09)
        checked_fill = _blend_color(colors["surface"], colors["accent"], 0.22 if is_dark else 0.13)
        rect = QRectF(watched.rect())

        painter = QPainter(watched)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not watched.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground):
            painter.fillRect(watched.rect(), QColor(colors["surface"]))
        painter.setPen(QPen(QColor(colors["border"]), 1.0))
        painter.setBrush(QColor(colors["surface"]))
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 7.5, 7.5)

        active = watched.activeAction()
        has_icons = any(not a.icon().isNull() for a in actions)
        has_checks = any(a.isCheckable() for a in actions)
        wide_swatch = watched.objectName() == "themeMenu"

        base_font = QFont(watched.font())
        base_font.setPixelSize(13)
        shortcut_font = QFont(base_font)
        shortcut_font.setPixelSize(12)
        section_font = QFont(base_font)
        section_font.setPixelSize(11)
        section_font.setWeight(QFont.Weight.DemiBold)

        for action in actions:
            item_rect = QRectF(watched.actionGeometry(action))
            if item_rect.isEmpty():
                continue
            if action.isSeparator():
                sec_title = _strip_mnemonic(action.text()).strip()
                if sec_title:
                    painter.setFont(section_font)
                    painter.setPen(QColor(colors["muted"]))
                    sec_rect = item_rect.adjusted(12.0, 0.0, -12.0, 0.0)
                    painter.drawText(sec_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, sec_title)
                else:
                    y = round(item_rect.center().y()) + 0.5
                    painter.setPen(QPen(QColor(colors["border"]), 1.0))
                    painter.drawLine(QPointF(item_rect.left() + 8, y), QPointF(item_rect.right() - 8, y))
                continue

            enabled = action.isEnabled()
            hovered = enabled and action is active
            checked = action.isCheckable() and action.isChecked()
            pill_rect = item_rect.adjusted(1.0, 1.0, -1.0, -1.0)
            if hovered:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(hover_fill)
                painter.drawRoundedRect(pill_rect, 5.5, 5.5)
            elif checked and wide_swatch:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(checked_fill)
                painter.drawRoundedRect(pill_rect, 5.5, 5.5)

            icon_mode = QIcon.Mode.Normal if enabled else QIcon.Mode.Disabled
            left_x = item_rect.left() + 12.0
            if wide_swatch and not action.icon().isNull():
                icon_rect = QRect(int(left_x), int(item_rect.center().y() - 9), 36, 18)
                action.icon().paint(painter, icon_rect, Qt.AlignmentFlag.AlignCenter, icon_mode)
                left_x += 44.0
            elif has_icons:
                if not action.icon().isNull():
                    if checked:
                        badge = QRectF(left_x - 2.0, item_rect.center().y() - 10.0, 20.0, 20.0)
                        painter.setPen(QPen(QColor(colors["accent"] if enabled else colors["disabled"]), 1.0))
                        painter.setBrush(checked_fill)
                        painter.drawRoundedRect(badge, 4.5, 4.5)
                    icon_rect = QRect(int(left_x), int(item_rect.center().y() - 8), 16, 16)
                    action.icon().paint(painter, icon_rect, Qt.AlignmentFlag.AlignCenter, icon_mode)
                elif checked:
                    from app.icons import toolbar_icon
                    ck_rect = QRect(int(left_x), int(item_rect.center().y() - 8), 16, 16)
                    toolbar_icon("check", colors["accent"] if enabled else colors["disabled"], 16, themed=False).paint(
                        painter, ck_rect
                    )
                left_x += 25.0
            elif has_checks:
                if checked:
                    from app.icons import toolbar_icon
                    ck_rect = QRect(int(left_x - 2), int(item_rect.center().y() - 8), 16, 16)
                    toolbar_icon("check", colors["accent"] if enabled else colors["disabled"], 16, themed=False).paint(
                        painter, ck_rect
                    )
                left_x += 22.0

            raw = action.text()
            if "\t" in raw:
                label, inline_shortcut = raw.split("\t", 1)
            else:
                label, inline_shortcut = raw, ""
            label = _strip_mnemonic(label)
            shortcut = inline_shortcut or action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)

            right_x = item_rect.right() - 12.0
            if action.menu() is not None:
                cx = right_x - 3.0
                cy = item_rect.center().y()
                chev_color = colors["text"] if hovered else (colors["muted"] if enabled else colors["disabled"])
                painter.setPen(QPen(QColor(chev_color), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                painter.drawPolyline(QPolygonF([
                    QPointF(cx - 2.5, cy - 4.0),
                    QPointF(cx + 1.5, cy),
                    QPointF(cx - 2.5, cy + 4.0),
                ]))
                right_x -= 16.0
            elif wide_swatch and checked:
                from app.icons import toolbar_icon
                ck_rect = QRect(int(right_x - 16), int(item_rect.center().y() - 8), 16, 16)
                toolbar_icon("check", colors["accent"] if enabled else colors["disabled"], 16, themed=False).paint(
                    painter, ck_rect
                )
                right_x -= 20.0
            elif shortcut:
                painter.setFont(shortcut_font)
                painter.setPen(QColor(colors["muted"] if enabled else colors["disabled"]))
                sc_rect = QRectF(left_x, item_rect.top(), max(0.0, right_x - left_x), item_rect.height())
                painter.drawText(sc_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, shortcut)
                sc_w = painter.fontMetrics().horizontalAdvance(shortcut)
                right_x -= sc_w + 14.0
            elif checked and not action.icon().isNull():
                from app.icons import toolbar_icon
                ck_rect = QRect(int(right_x - 16), int(item_rect.center().y() - 8), 16, 16)
                toolbar_icon("check", colors["accent"] if enabled else colors["disabled"], 16, themed=False).paint(
                    painter, ck_rect
                )
                right_x -= 20.0

            label_font = QFont(base_font)
            if checked:
                label_font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(label_font)
            text_color = colors["accent"] if (checked and enabled) else (
                colors["text"] if enabled else colors["disabled"]
            )
            painter.setPen(QColor(text_color))
            avail_w = max(0.0, right_x - left_x)
            text_rect = QRectF(left_x, item_rect.top(), avail_w, item_rect.height())
            elided = painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, int(avail_w))
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided)

        painter.end()
        return True


class _CheckboxStyle(QProxyStyle):
    """Draw crisp themed checkboxes, radio buttons, and combobox popups across all themes."""

    _draws_combo_arrow = True

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_ComboBox_Popup:
            return 0
        return super().styleHint(hint, option, widget, returnData)

    def pixelMetric(self, metric, option=None, widget=None):
        if metric in (
            QStyle.PixelMetric.PM_IndicatorWidth,
            QStyle.PixelMetric.PM_IndicatorHeight,
            QStyle.PixelMetric.PM_ExclusiveIndicatorWidth,
            QStyle.PixelMetric.PM_ExclusiveIndicatorHeight,
        ):
            return 16
        return super().pixelMetric(metric, option, widget)

    def drawPrimitive(self, element, option, painter, widget=None):
        if element == QStyle.PrimitiveElement.PE_IndicatorArrowDown and isinstance(widget, QComboBox):
            colors = current_theme().colors
            enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
            color = colors["muted"] if enabled else colors["disabled"]
            center = QRectF(option.rect).center()
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor(color), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPolyline(QPolygonF([
                QPointF(center.x() - 4.0, center.y() - 1.8),
                QPointF(center.x(), center.y() + 2.2),
                QPointF(center.x() + 4.0, center.y() - 1.8),
            ]))
            painter.restore()
            return

        if element == QStyle.PrimitiveElement.PE_IndicatorRadioButton:
            colors = current_theme().colors
            state = option.state
            flags = QStyle.StateFlag
            enabled = bool(state & flags.State_Enabled)
            focused = enabled and bool(state & flags.State_HasFocus)
            checked = bool(state & flags.State_On)
            hovered = enabled and bool(state & flags.State_MouseOver)

            rect = QRectF(option.rect).adjusted(1.0, 1.0, -1.0, -1.0)
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            if checked:
                fill = colors["accent"] if enabled else colors["disabled"]
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(fill))
                painter.drawEllipse(rect)
                inner = rect.adjusted(4.2, 4.2, -4.2, -4.2)
                painter.setBrush(QColor(colors["on_accent"] if enabled else colors["surface"]))
                painter.drawEllipse(inner)
            else:
                border = colors["accent"] if (hovered or focused) else (
                    colors["strong_border"] if enabled else colors["border"]
                )
                fill = colors["hover"] if hovered else (colors["surface"] if enabled else colors["subtle"])
                painter.setPen(QPen(QColor(border), 1.5))
                painter.setBrush(QColor(fill))
                painter.drawEllipse(rect)
            painter.restore()
            return

        if element not in (QStyle.PrimitiveElement.PE_IndicatorCheckBox,
                           QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck):
            super().drawPrimitive(element, option, painter, widget)
            return

        colors = current_theme().colors
        state = option.state
        flags = QStyle.StateFlag
        enabled = bool(state & flags.State_Enabled)
        focused = enabled and bool(state & flags.State_HasFocus)
        partial = bool(state & flags.State_NoChange)
        checked = bool(state & flags.State_On) or partial
        hovered = enabled and bool(state & flags.State_MouseOver)

        if not enabled:
            border, fill, mark = colors["border"], colors["subtle"], colors["disabled"]
        elif checked:
            fill = colors["accent_pressed"] if (state & flags.State_Sunken) else (
                colors["accent_hover"] if hovered else colors["accent"]
            )
            border, mark = fill, colors["on_accent"]
        else:
            border = colors["accent"] if (hovered or focused) else colors["strong_border"]
            fill = colors["hover"] if hovered else colors["surface"]
            mark = colors["on_accent"]

        outline = 1.5 if (focused and not checked) else 1.25
        inset = outline / 2
        rect = QRectF(option.rect).adjusted(inset, inset, -inset, -inset)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(border), outline))
        painter.setBrush(QColor(fill))
        painter.drawRoundedRect(rect, 4.0, 4.0)
        if partial:
            painter.setPen(QPen(QColor(mark), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            offset = QPointF(3.5, 0)
            painter.drawLine(rect.center() - offset, rect.center() + offset)
        elif checked:
            from app.icons import toolbar_icon

            mark_rect = QRect(0, 0, 18, 18)
            mark_rect.moveCenter(option.rect.center())
            toolbar_icon("check", mark, mark_rect.width(), themed=False).paint(painter, mark_rect)
        painter.restore()


class ThemeManager(QObject):
    changed = pyqtSignal(str)

    def apply(self, key: str, settings=None) -> bool:
        if key not in _THEMES_BY_KEY:
            return False
        app = QApplication.instance()
        ensure_chevron_icons()
        if not isinstance(app.style(), _CheckboxStyle):
            base = app.style().objectName()
            style = _CheckboxStyle(base)
            style.setObjectName(base)
            app.setStyle(style)
        app.setEffectEnabled(Qt.UIEffect.UI_AnimateCombo, False)
        if not getattr(app, "_menu_painter", None):
            menu_painter = _MenuPainter(app)
            app.installEventFilter(menu_painter)
            app._menu_painter = menu_painter
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
            if isinstance(widget, QComboBox):
                _configure_combo_popup(widget)
            if isinstance(widget, QMenu):
                _configure_menu_window(widget)
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
