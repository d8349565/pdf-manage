"""Theme checks use empty widgets and isolated settings, without documents."""
import pytest
from PyQt6.QtCore import QSettings, QSize
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QFrame

from app.themes import (
    DEFAULT_THEME, THEMES, THEME_SETTING, current_theme, initialize_theme,
    set_theme_style, theme_manager,
)
from app.ui import MainWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    return app


@pytest.fixture(autouse=True)
def reset_theme(qapp):
    theme_manager().apply(DEFAULT_THEME)
    yield
    theme_manager().apply(DEFAULT_THEME)


def contrast(first, second):
    def luminance(value):
        color = QColor(value)
        rgb = (color.redF(), color.greenF(), color.blueF())
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
        return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))
    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + .05) / (low + .05)


@pytest.mark.parametrize("theme", THEMES, ids=lambda theme: theme.key)
def test_theme_text_and_controls_have_readable_contrast(theme):
    colors = theme.colors
    for text in ("text", "muted"):
        for background in ("surface", "window", "subtle"):
            assert contrast(colors[text], colors[background]) >= 4.5, (theme.key, text, background)
    for background in ("accent", "accent_hover", "accent_pressed"):
        assert contrast(colors["on_accent"], colors[background]) >= 4.5
    assert contrast(colors["accent"], colors["selected"]) >= 4.5


def test_switches_reapply_source_styles_and_restore_original_palette(qapp):
    widget = QFrame()
    source = 'QFrame { background: #FFFFFF; color: #134E4A; border: 1px solid #D7E8E4; }'
    set_theme_style(widget, source)
    original = widget.styleSheet()
    for theme in THEMES:
        assert theme_manager().apply(theme.key)
        assert theme.colors["surface"] in widget.styleSheet()
        assert theme.colors["text"] in widget.styleSheet()
        assert widget.property("themeStyleSource") == source
    theme_manager().apply(DEFAULT_THEME)
    assert widget.styleSheet() == original
    before = current_theme()
    assert not theme_manager().apply("unknown-theme")
    assert current_theme() is before
    widget.deleteLater()


@pytest.mark.parametrize("saved", ["retired-theme", ["night"], None])
def test_invalid_saved_choice_falls_back_without_overwriting_preferences(tmp_path, saved, qapp):
    settings = QSettings(str(tmp_path / "theme.ini"), QSettings.Format.IniFormat)
    settings.setValue(THEME_SETTING, saved)
    initialize_theme(settings)
    assert current_theme().key == DEFAULT_THEME
    assert settings.value(THEME_SETTING) == saved


def test_empty_windows_share_switches_and_reopen_with_saved_theme(qapp):
    from app.progress import TaskProgressDialog
    from app.searchui import SearchBar
    first = MainWindow()
    first.show()
    qapp.processEvents()
    pages = first.ws.pages
    progress = TaskProgressDialog("主题检查", first)
    search = SearchBar(first)
    second = None
    try:
        assert first.theme_button.menu() is first.theme_menu
        assert len(first.theme_actions) == 5
        first.theme_actions["forest"].trigger()
        first.theme_actions["night"].trigger()
        qapp.processEvents()
        theme = current_theme()
        assert first._window_settings.value(THEME_SETTING) == "night"
        assert first.theme_actions["night"].isChecked()
        assert "夜航" in first.theme_button.accessibleName()
        assert theme.colors["surface"] in search.styleSheet()
        assert theme.colors["text"] in progress.styleSheet()
        assert first.preview_view.backgroundBrush().color().name().upper() == theme.colors["canvas"]
        assert first.ws.pages is pages
        assert not first.ws.dirty
        second = MainWindow()
        assert second.theme_actions["night"].isChecked()
        first.theme_actions["terracotta"].trigger()
        assert second.theme_actions["terracotta"].isChecked()
        assert "赤陶" in second.theme_button.accessibleName()
    finally:
        progress.deleteLater()
        search.deleteLater()
        if second:
            second.close()
        first.close()


@pytest.mark.parametrize("glyph", ["save", "circle", "square"])
def test_icons_follow_theme_while_document_color_samples_stay_fixed(qapp, glyph):
    from PyQt6.QtWidgets import QToolButton
    from app.icons import toolbar_icon

    button = QToolButton()
    sample = QToolButton()
    button.setIcon(toolbar_icon(glyph, "#526B63"))
    sample.setIcon(toolbar_icon("circle", "#0F766E", 18, themed=False))
    original = button.icon().pixmap(QSize(20, 20)).toImage()
    sample_before = sample.icon().pixmap(QSize(18, 18)).toImage()
    theme_manager().apply("night")
    assert button.icon().pixmap(QSize(20, 20)).toImage() != original
    assert sample.icon().pixmap(QSize(18, 18)).toImage() == sample_before
    theme_manager().apply(DEFAULT_THEME)
    assert button.icon().pixmap(QSize(20, 20)).toImage() == original
    button.deleteLater()
    sample.deleteLater()


@pytest.mark.parametrize("glyph", ["circle", "square"])
def test_color_sample_matching_a_tool_icon_keeps_its_own_cache_identity(qapp, glyph):
    from PyQt6.QtWidgets import QToolButton
    from app.icons import toolbar_icon

    tool, sample = QToolButton(), QToolButton()
    tool.setIcon(toolbar_icon(glyph, "#526B63", 16))
    sample.setIcon(toolbar_icon(glyph, current_theme().colors["muted"], 16, themed=False))
    original = sample.icon().pixmap(QSize(16, 16)).toImage()
    assert tool.icon().pixmap(QSize(16, 16)).toImage() == original
    try:
        theme_manager().apply("night")
        assert tool.icon().pixmap(QSize(16, 16)).toImage() != original
        assert sample.icon().pixmap(QSize(16, 16)).toImage() == original
    finally:
        tool.deleteLater()
        sample.deleteLater()


@pytest.mark.parametrize("kind", ["pages", "compression"])
def test_night_preview_messages_stay_readable_on_white_paper(qapp, kind):
    from types import SimpleNamespace
    from PyQt6.QtCore import QRectF
    from app.compressionpreview import CompressionPreview
    from app.pagepreviewlist import ScrollablePagePreview

    theme_manager().apply("night")
    if kind == "pages":
        # No document is opened: only the unavailable-page display is exercised.
        view = ScrollablePagePreview(SimpleNamespace(page_size=lambda _: (200, 260)), [None])
        view.shutdown()
        view.set_rows([0])
        view.set_fit_page(True)
    else:
        view = CompressionPreview()
        view.shutdown()
        view.pages, view.sizes = [(None, None)], [(200, 260)]
        view.relayout()
    view.resize(700, 420)
    view.show()
    qapp.processEvents()
    try:
        for failed in (False, True):
            if failed:
                view.errors[view.image_key(0)] = "Preview unavailable"
            view.viewport().update()
            qapp.processEvents()
            image = view.viewport().grab().toImage()
            if kind == "pages":
                paper = view.paper_rect(0, view.visualRect(view.page_model.index(0)))
            else:
                width, height = view.sizes[0]
                scale = view.scales[0]
                left = max(14, (view.column_width() - width * scale) / 2)
                paper = QRectF(left, 29, width * scale, height * scale)
            ratio = image.devicePixelRatio()
            area = paper.adjusted(18, 18, -18, -18)
            pixels = {image.pixelColor(x, y).name()
                      for y in range(round(area.top() * ratio), round(area.bottom() * ratio))
                      for x in range(round(area.left() * ratio), round(area.right() * ratio))}
            assert "#ffffff" in pixels
            assert max(contrast(color, "#ffffff") for color in pixels) >= 4.5
    finally:
        view.shutdown()
        view.close()
        view.deleteLater()
        qapp.processEvents()
