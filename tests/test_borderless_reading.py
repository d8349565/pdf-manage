"""Borderless presentation removes UI chrome and restores reading state with Esc."""
import pymupdf
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog

from app.readingcolors import ReadingColors
from app.ui import MainWindow


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def reader(qapp, tmp_path):
    path = tmp_path / 'generated.pdf'
    with pymupdf.open() as doc:
        for number in range(8):
            page = doc.new_page(width=360, height=520)
            page.insert_text((40, 60), f'Generated page {number + 1}')
        doc.save(path)
    window = MainWindow()
    window.resize(1100, 760)
    window.show()
    assert window.open_files_in_tabs([str(path)])
    QTest.qWait(80)
    yield window
    for tab in window._tabs:
        tab.ws.reset()
    window.close()
    qapp.processEvents()


@pytest.mark.parametrize('layout', ['single', 'spread', 'four'])
@pytest.mark.parametrize('colors', [ReadingColors(), ReadingColors(True),
    ReadingColors(True, '#24282E', '#E2E4E7', '#171A1F')])
def test_only_page_content_is_visible_and_escape_restores(reader, qapp, layout, colors):
    reader._set_reading_layout(layout)
    reader._reading_colors = colors
    reader.preview_view.set_reading_colors(colors)
    reader.go_to_page(5)
    QTest.qWait(60)
    geometry = reader.geometry()
    pages = reader.ws.pages
    view = reader.preview_view
    policies = view.horizontalScrollBarPolicy(), view.verticalScrollBarPolicy()
    reader.immersive.borderless_action.trigger()
    QTest.qWait(80)
    assert reader.immersive.borderless and not reader.isFullScreen()
    assert reader.geometry() == geometry
    for widget in (reader.menuWidget(), reader.workbench_bar, reader.tab_bar, reader.statusBar(),
                   reader.left_panel, reader.tools_panel, reader.reading_orb,
                   reader.immersive.edge, reader.immersive.exit_button):
        assert not widget.isVisible(), widget.objectName()
    assert not view.horizontalScrollBar().isVisible() and not view.verticalScrollBar().isVisible()
    paper = QColor(colors.paper if colors.enabled else '#FFFFFF')
    assert view.backgroundBrush().color() == paper
    picture = reader.grab().toImage()
    for x, y in ((0, 0), (picture.width() - 1, 0), (0, picture.height() - 1),
                 (picture.width() - 1, picture.height() - 1)):
        assert picture.pixelColor(x, y) == paper
    reader.immersive.reveal(keyboard=True)
    assert not reader.immersive.overlay.isVisible()
    QTest.keyClick(view, Qt.Key.Key_Escape)
    QTest.qWait(80)
    assert not reader.immersive.borderless
    assert reader.menuWidget().isVisible() and reader.workbench_bar.isVisible()
    assert (view.horizontalScrollBarPolicy(), view.verticalScrollBarPolicy()) == policies
    assert reader.geometry() == geometry
    assert reader.ws.pages is pages and not reader.ws.dirty
    assert reader.ws.preview_index() == 4


@pytest.mark.parametrize('start_fullscreen, toggle_inside', [(False, True), (True, False), (True, True)])
def test_borderless_and_fullscreen_restore_each_other(reader, qapp, start_fullscreen, toggle_inside):
    control = reader.immersive
    geometry = reader.geometry()
    control.fullscreen_action.setChecked(start_fullscreen)
    control.borderless_action.setChecked(True)
    if toggle_inside:
        QTest.keyClick(reader.preview_view, Qt.Key.Key_F11)
    qapp.processEvents()
    assert control.borderless and not control.exit_button.isVisible()
    expected_fullscreen = not start_fullscreen if toggle_inside else start_fullscreen
    QTest.keyClick(reader.preview_view, Qt.Key.Key_Escape)
    qapp.processEvents()
    assert not control.borderless and control.fullscreen == expected_fullscreen
    control.fullscreen_action.setChecked(False)
    qapp.processEvents()
    assert reader.menuWidget().isVisible() and reader.workbench_bar.isVisible()
    assert reader.geometry() == geometry


def test_shortcut_escape_with_selection_and_pinned_sidebar_restore(reader, qapp):
    control = reader.immersive
    control.auto_action.setChecked(False)
    reader.act_pages.setChecked(True)
    reader.ws.select_index(1)
    reader.ws.rotate(90)
    before = reader.ws._fingerprint()
    history = reader.ws.undo_stack.count()
    maximum = reader.btn_max.mapTo(reader, reader.btn_max.rect().center())
    maximum = round(maximum.x() * reader.devicePixelRatioF()), round(maximum.y() * reader.devicePixelRatioF())
    assert reader._over_max_button(*maximum)
    QTest.keyClick(reader.preview_view, Qt.Key.Key_F11, Qt.KeyboardModifier.ControlModifier)
    QTest.qWait(60)
    assert control.borderless and not reader.left_panel.isVisible()
    assert not reader._over_max_button(*maximum)
    reader.text_selection.text = 'An active selection must not consume the exit key'
    QTest.keyClick(reader.preview_view.viewport(), Qt.Key.Key_Escape)
    qapp.processEvents()
    assert not control.borderless
    assert not control.auto_action.isChecked() and reader.left_panel.isVisible()
    assert reader.ws._fingerprint() == before and reader.ws.undo_stack.count() == history
    assert reader.ws.dirty


def test_dialog_escape_does_not_exit_and_last_document_close_restores_home(reader, qapp):
    reader.immersive.borderless_action.setChecked(True)
    dialog = QDialog(reader)
    dialog.setModal(True)
    dialog.show()
    qapp.processEvents()
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    qapp.processEvents()
    assert reader.immersive.borderless and not dialog.isVisible()
    reader.close_current_file()
    qapp.processEvents()
    assert not reader.immersive.borderless
    assert reader.menuWidget().isVisible() and reader.workbench_bar.isVisible()
    assert not reader.immersive.borderless_action.isEnabled()


def test_entering_from_organizing_restores_organizing_and_keyboard_focus(reader, qapp):
    reader.set_work_mode(1)
    qapp.processEvents()
    assert reader.stack.currentIndex() == 1
    reader.immersive.borderless_action.setChecked(True)
    assert reader.stack.currentIndex() == 0
    QTest.keyClick(reader.preview_view, Qt.Key.Key_Escape)
    qapp.processEvents()
    assert reader.stack.currentIndex() == 1
    assert reader.arrange_view.hasFocus()


@pytest.mark.parametrize('presentation', ['fullscreen', 'borderless'])
@pytest.mark.parametrize('previous_zoom', ['page', 'custom'])
def test_entering_presentation_automatically_fits_width(reader, qapp, presentation, previous_zoom):
    reader.go_to_page(5)
    reader._set_zoom(2.2) if previous_zoom == 'custom' else reader._set_zoom_mode('page')
    pages = reader.ws.pages
    default_zoom = reader._window_settings.value('reading/defaultZoomMode')
    action = getattr(reader.immersive, presentation + '_action')
    action.trigger()
    QTest.qWait(120)
    assert reader._zoom_mode == 'width'
    assert reader.reading_zoom_actions['width'].isChecked()
    view = reader.preview_view
    assert view.transform().m11() == pytest.approx((view.viewport().width() - 24) / 360, abs=.002)
    assert reader.ws.preview_index() == 4
    assert reader.ws.pages is pages and not reader.ws.dirty
    assert reader._window_settings.value('reading/defaultZoomMode') == default_zoom
    action.trigger()
    qapp.processEvents()


def test_exiting_fullscreen_inside_borderless_keeps_a_manual_zoom(reader, qapp):
    control = reader.immersive
    control.borderless_action.setChecked(True)
    control.fullscreen_action.setChecked(True)
    assert reader._zoom_mode == 'width'
    reader._set_zoom(2.2)
    control.fullscreen_action.setChecked(False)
    qapp.processEvents()
    assert control.borderless and not control.fullscreen
    assert reader._zoom_mode == 'custom' and reader._zoom == 2.2
