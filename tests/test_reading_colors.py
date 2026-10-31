"""Display colours leave PDF data and all original render layers intact."""
from dataclasses import replace
import threading

import pymupdf
import pytest
from PyQt6.QtCore import QRectF, QSettings, QThreadPool, QTimer
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QDialogButtonBox

from app.readingcolors import ReadingColors, ReadingColorsDialog, ReadingColorPreview
from app.ui import MainWindow, PreviewView


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


def render_scene(view, *, settle=True):
    for _ in range(16):
        rect = view.scene().sceneRect()
        image = QImage(int(rect.width()), int(rect.height()), QImage.Format.Format_RGB32)
        image.fill(QColor('white'))
        painter = QPainter(image)
        view.scene().render(painter, QRectF(image.rect()), rect)
        painter.end()
        if not settle or not getattr(view, '_reading_color_pending', None):
            return image
        assert QThreadPool.globalInstance().waitForDone(5000)
        QApplication.processEvents()
    raise AssertionError('reading colours did not settle')


def test_colour_mapping_runs_off_gui_without_queueing_or_applying_stale_colours(qapp, monkeypatch):
    gui_thread = threading.get_ident()
    started, release = threading.Event(), threading.Event()
    threads = []
    original_map = ReadingColors.map_image

    def map_image(colors, image):
        threads.append(threading.get_ident())
        if threads[-1] != gui_thread:
            started.set()
            assert release.wait(5)
        return original_map(colors, image)

    monkeypatch.setattr(ReadingColors, 'map_image', map_image)
    view = PreviewView()
    original = QImage(120, 160, QImage.Format.Format_RGB32)
    original.fill(QColor('white'))
    original.setDevicePixelRatio(2)
    try:
        view.set_pages('background-colours', [(120, 160)])
        view.set_photo(QPixmap.fromImage(original), 1, (120, 160))
        view.set_reading_colors(ReadingColors(True))
        render_scene(view, settle=False)
        assert started.wait(2), 'colour mapping still ran in the GUI thread'
        render_scene(view, settle=False)
        assert len(threads) == 1, 'repainting queued duplicate work'
        colors = ReadingColors(True, '#24282E', '#E2E4E7', '#171A1F')
        view.set_reading_colors(colors)
        render_scene(view, settle=False)
        assert len(threads) == 1, 'palette changes queued work behind an obsolete job'
        release.set()
        assert QThreadPool.globalInstance().waitForDone(5000)
        qapp.processEvents()
        assert not view._reading_color_cache, 'obsolete palette entered the cache'
        image = render_scene(view)
        assert image.pixelColor(5, 5).name() == colors.paper.lower()
        assert all(thread != gui_thread for thread in threads)
        assert len(threads) == 2
        cached = next(iter(view._reading_color_cache.values()))
        assert cached.devicePixelRatio() == 2
        assert cached.sizeInBytes() < original.sizeInBytes()
        render_scene(view)
        assert len(threads) == 2, 'cached page was converted again'
        assert original.pixelColor(5, 5) == QColor('white')
    finally:
        release.set()
        QThreadPool.globalInstance().waitForDone(5000)
        view.close()


@pytest.mark.parametrize('columns', [1, 2, 4])
def test_page_neighbor_and_detail_layers_change_colour_and_restore(qapp, columns):
    view = PreviewView()
    try:
        view.set_pages('generated-colours', [(120, 160)] * 4, columns=columns)
        original = QImage(120, 160, QImage.Format.Format_RGB32)
        original.fill(QColor('white'))
        painter = QPainter(original)
        painter.fillRect(10, 10, 20, 20, QColor('black'))
        painter.end()
        view.set_photo(QPixmap.fromImage(original), 1, (120, 160))
        view.set_neighbor(1, original)
        detail = QImage(20, 20, QImage.Format.Format_RGB32)
        detail.fill(QColor('black'))
        for index in (0, 1):
            view.set_detail(detail, (50, 60), 1, index)
        colors = ReadingColors(True)
        view.set_reading_colors(colors)
        image = render_scene(view)
        for index in (0, 1):
            origin = view.page_rects[index].topLeft()
            assert image.pixelColor(int(origin.x() + 5), int(origin.y() + 5)).name() == colors.paper.lower()
            assert image.pixelColor(int(origin.x() + 15), int(origin.y() + 15)).name() == colors.ink.lower()
            assert image.pixelColor(int(origin.x() + 55), int(origin.y() + 65)).name() == colors.ink.lower()
        assert view.photo().toImage().pixelColor(5, 5) == QColor('white')
        assert original.pixelColor(5, 5) == QColor('white')
        assert view.detail_item(1).pixmap().toImage().pixelColor(5, 5) == QColor('black')
        view.set_reading_colors(replace(colors, enabled=False))
        image = render_scene(view)
        assert image.pixelColor(5, 5) == QColor('white')
        assert image.pixelColor(15, 15) == QColor('black')
        assert not view._reading_color_cache
    finally:
        view.close()


def test_reading_palette_live_preview_cancel_persistence_and_pdf_output(qapp, tmp_path, monkeypatch):
    source = tmp_path / 'source.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=240, height=320)
        page.insert_text((30, 60), 'Original PDF text')
        page.draw_rect((30, 90, 100, 140), color=(1, 0, 0), fill=(1, 0, 0))
        doc.save(source)
    original_bytes = source.read_bytes()
    window = MainWindow()
    restored = None
    try:
        assert window.open_files_in_tabs([str(source)])
        real_exec = ReadingColorsDialog.exec
        def cancel_preview(dialog):
            def interact():
                dialog.presets.setCurrentRow(3)
                assert window.preview_view.reading_colors.enabled
                assert not window.ws.dirty
                dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).click()
            QTimer.singleShot(0, interact)
            return real_exec(dialog)
        monkeypatch.setattr(ReadingColorsDialog, 'exec', cancel_preview)
        window.reading_colors_action.trigger()
        assert not window.preview_view.reading_colors.enabled
        assert not ReadingColors.load(window._window_settings).enabled

        def apply_preview(dialog):
            def interact():
                assert dialog.custom_area.isHidden()
                dialog.presets.setCurrentRow(3)
                assert dialog.custom_area.isHidden()
                dialog.presets.setCurrentRow(4)
                assert not dialog.custom_area.isHidden()
                dialog.apply_button.click()
            QTimer.singleShot(0, interact)
            return real_exec(dialog)
        monkeypatch.setattr(ReadingColorsDialog, 'exec', apply_preview)
        window.reading_colors_action.trigger()
        assert window.preview_view.reading_colors.enabled
        restored = MainWindow()
        assert restored.preview_view.reading_colors == window.preview_view.reading_colors
        assert not window.ws.dirty
        output = tmp_path / 'saved.pdf'
        window._finish_document_checks(window.ws.pages)
        window.store.write(window.ws.pages, str(output))
        with pymupdf.open(output) as result:
            pix = result[0].get_pixmap()
            assert pix.pixel(5, 5) == (255, 255, 255)
            assert pix.pixel(50, 110) == (255, 0, 0)
            assert 'Original PDF text' in result[0].get_text()
        assert source.read_bytes() == original_bytes
        window.restore_reading_colors_action.trigger()
        assert not window.preview_view.reading_colors.enabled
        assert not ReadingColors.load(window._window_settings).enabled
    finally:
        if restored is not None:
            restored.close()
        window.close()
        qapp.processEvents()


@pytest.mark.parametrize('saved', ['invalid-json', '[]', '{"enabled":"true"}',
                                   '{"enabled":true,"paper":"#FFFFFF","ink":"#FFFFFF"}'])
def test_invalid_saved_colours_fall_back_to_original(qapp, tmp_path, saved):
    settings = QSettings(str(tmp_path / 'colours.ini'), QSettings.Format.IniFormat)
    settings.setValue('reading/colors', saved)
    assert not ReadingColors.load(settings).enabled


def test_low_contrast_cannot_be_applied(qapp):
    previews = []
    dialog = ReadingColorsDialog(None, ReadingColors(True), previews.append)
    try:
        dialog.colors = replace(dialog.colors, paper='#FFFFFF', ink='#FFFFFF')
        dialog.refresh()
        assert not dialog.apply_button.isEnabled()
        assert previews[-1].contrast >= 4.5
        dialog.restore.click()
        assert dialog.apply_button.isEnabled()
        assert not previews[-1].enabled
    finally:
        dialog.close()


def test_preview_fits_the_entire_page_in_a_short_wide_area(qapp):
    preview = ReadingColorPreview()
    original = QImage(120, 160, QImage.Format.Format_RGB32)
    original.fill(QColor('white'))
    painter = QPainter(original)
    for x, y in ((0, 0), (110, 0), (0, 150), (110, 150)):
        painter.fillRect(x, y, 10, 10, QColor('black'))
    painter.end()
    preview.image = original
    preview.setStyleSheet('background: white;')
    preview.resize(250, 100)
    try:
        preview.show()
        qapp.processEvents()
        result = preview.grab().toImage()
        ratio = result.devicePixelRatio()
        for x, y in ((90, 2), (159, 2), (90, 97), (159, 97)):
            assert result.pixelColor(round(x * ratio), round(y * ratio)) == QColor('black')
    finally:
        preview.close()
