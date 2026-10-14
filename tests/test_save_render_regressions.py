"""Image-only saves and delayed foreground rendering without personal files."""
from hashlib import sha256

import pymupdf as p
import pytest
from PyQt6.QtCore import QThreadPool, QRect
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from app.progress import TaskProgressDialog
from app.ui import MainWindow, RENDER_SHOW_DELAY_MS, RENDER_HIDE_DELAY_MS


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


def make_image_pdf(path, rotation=0):
    with p.open() as drawing:
        page = drawing.new_page(width=240, height=320)
        page.draw_rect((20, 30, 90, 250), fill=(.2, .5, .8))
        page.insert_text((100, 65), 'Rotation', fontsize=14)
        image = page.get_pixmap().tobytes('png')
    with p.open() as document:
        page = document.new_page(width=240, height=320)
        page.insert_image(page.rect, stream=image)
        page.set_rotation(rotation)
        document.save(path)


@pytest.fixture
def window(qapp, tmp_path):
    source = tmp_path / 'scan.pdf'
    make_image_pdf(source)
    win = MainWindow()
    assert win._load_one(str(source))
    win.resize(1000, 720)
    win.show()
    win.refresh()
    QThreadPool.globalInstance().waitForDone(30000)
    qapp.processEvents()
    QTest.qWait(RENDER_SHOW_DELAY_MS + RENDER_HIDE_DELAY_MS + 80)
    QThreadPool.globalInstance().waitForDone(30000)
    qapp.processEvents()
    win._preview_timer.stop()
    win._detail_timer.stop()
    win._reading_timer.stop()
    yield win
    QThreadPool.globalInstance().waitForDone(30000)
    qapp.processEvents()
    win.ws.reset()
    win.close()


@pytest.mark.parametrize('source_rotation', [0, 90, 180, 270])
@pytest.mark.parametrize('save_as', [False, True])
def test_image_rotation_save_as_and_repeated_save(qapp, tmp_path, monkeypatch, source_rotation, save_as):
    source = tmp_path / 'rotated.pdf'
    destination = tmp_path / 'copy.pdf' if save_as else source
    make_image_pdf(source, source_rotation)
    original = source.read_bytes()
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: (str(destination), 'PDF'))
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args))
    win = MainWindow()
    try:
        assert win._load_one(str(source))
        expected_rotation = source_rotation
        for attempt, turn in enumerate((90, 90, -90)):
            win.ws.select_index(0)
            win.ws.rotate(turn)
            expected_rotation = (expected_rotation + turn) % 360
            assert win.save(save_as and attempt == 0)
            assert not win.ws.dirty
            with p.open(destination) as output, p.open(stream=original, filetype='pdf') as reference:
                reference[0].set_rotation(expected_rotation)
                assert output.page_count == 1
                assert output[0].rotation == expected_rotation
                assert output[0].get_images() and not output[0].get_text().strip()
                assert output[0].get_pixmap().samples == reference[0].get_pixmap().samples
            assert not list(tmp_path.glob('.pdfasm-*'))
            assert not any(dialog.isVisible() for dialog in win.findChildren(TaskProgressDialog))
        assert not warnings
        if save_as:
            assert source.read_bytes() == original
    finally:
        QThreadPool.globalInstance().waitForDone(30000)
        qapp.processEvents()
        win.ws.reset()
        win.close()


@pytest.mark.parametrize('failure', ['target_changed', 'write_denied'])
def test_save_failure_keeps_edits_closes_progress_and_allows_save_as(window, tmp_path, monkeypatch, failure):
    from app import pdfworker
    win = window
    source = tmp_path / 'scan.pdf'
    original = source.read_bytes()
    win.ws.select_index(0)
    win.ws.rotate(90)
    edits = list(win.ws.pages)
    warnings = []
    def warning(*args):
        assert win._task_dialog is None
        assert not any(dialog.isVisible() for dialog in win.findChildren(TaskProgressDialog))
        warnings.append(args)
    monkeypatch.setattr(QMessageBox, 'warning', warning)
    if failure == 'target_changed':
        replacement = tmp_path / 'external.pdf'
        make_image_pdf(replacement, 180)
        expected_source = replacement.read_bytes()
        worker = pdfworker._worker
        def changed(*args, **kwargs):
            prepared = worker(*args, **kwargs)
            source.write_bytes(expected_source)
            return prepared
        with monkeypatch.context() as context:
            context.setattr(pdfworker, '_worker', changed)
            assert not win.save()
    else:
        expected_source = original
        with monkeypatch.context() as context:
            def denied(*args, **kwargs):
                raise PermissionError('写入被拒绝')
            context.setattr(pdfworker, 'commit_file', denied)
            assert not win.save()
    assert len(warnings) == 1
    assert win.ws.dirty and win.ws.pages == edits
    assert source.read_bytes() == expected_source
    assert not list(tmp_path.glob('.pdfasm-*'))
    assert not list(tmp_path.glob('.zhixu-write-*'))
    target = tmp_path / 'recovered.pdf'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: (str(target), 'PDF'))
    assert win.save(True)
    assert len(warnings) == 1 and not win.ws.dirty
    assert sha256(source.read_bytes()).digest() == sha256(expected_source).digest()
    with p.open(target) as saved:
        assert saved[0].rotation == 90


def foreground(win, busy):
    win._preview_key = (*win.ws.pages[0].render_key, 300)
    win._preview_busy = busy
    win._detail_busy = False
    win._update_render_progress()


def test_background_thumbnail_and_neighbor_work_does_not_show_reading_progress(window):
    window.model.inflight.add(window.ws.pages[0].render_key)
    window._neighbor_pending.add((1, 1))
    window._note_render_busy()
    QTest.qWait(RENDER_SHOW_DELAY_MS + 80)
    assert window.render_label.isHidden()
    assert not window._render_status_timer.isActive()
    assert not window._render_show_timer.isActive()


def test_short_foreground_work_never_flashes_and_busy_events_do_not_postpone_show(window):
    foreground(window, True)
    QTest.qWait(60)
    foreground(window, False)
    QTest.qWait(RENDER_SHOW_DELAY_MS + 50)
    assert window.render_label.isHidden()
    foreground(window, True)
    for _ in range(6):
        QTest.qWait(45)
        window._update_render_progress()
    assert not window.render_label.isHidden()
    foreground(window, False)
    assert not window.render_label.isHidden()
    QTest.qWait(50)
    foreground(window, True)
    QTest.qWait(RENDER_HIDE_DELAY_MS + 50)
    assert not window.render_label.isHidden()
    foreground(window, False)
    QTest.qWait(RENDER_HIDE_DELAY_MS + 50)
    assert window.render_label.isHidden()


def test_progress_visibility_and_opening_label_keep_reading_geometry_stable(window, qapp, request):
    before = (window.preview_view.viewport().size(), window.statusBar().height())
    request.node.user_properties.extend([
        ('qt_platform', qapp.platformName()), ('device_pixel_ratio', window.devicePixelRatioF()),
        ('viewport', f'{before[0].width()}x{before[0].height()}'), ('status_height', before[1])])
    foreground(window, True)
    QTest.qWait(RENDER_SHOW_DELAY_MS + 80)
    assert not window.render_label.isHidden()
    assert (window.preview_view.viewport().size(), window.statusBar().height()) == before
    foreground(window, False)
    QTest.qWait(RENDER_HIDE_DELAY_MS + 80)
    assert (window.preview_view.viewport().size(), window.statusBar().height()) == before
    label_size = window.render_label.size()
    window.ws.reset()
    window._opening_paths = ('generated.pdf',)
    window._update_render_progress()
    QTest.qWait(RENDER_SHOW_DELAY_MS + 80)
    assert window.render_label.text() == '正在打开 PDF'
    assert window.render_label.size() == label_size
    window._opening_paths = ()
    window._update_render_progress()
    QTest.qWait(RENDER_HIDE_DELAY_MS + 50)
    assert window.render_label.isHidden()


def test_arrange_progress_ignores_offscreen_thumbnails(window, monkeypatch):
    window.stack.setCurrentIndex(1)
    key = window.ws.pages[0].render_key
    window.model.inflight.add(key)
    monkeypatch.setattr(window.arrange_view, 'visualRect', lambda index: QRect(0, 100000, 40, 50))
    window._note_render_busy()
    QTest.qWait(RENDER_SHOW_DELAY_MS + 50)
    assert window.render_label.isHidden()
    monkeypatch.setattr(window.arrange_view, 'visualRect', lambda index: QRect(0, 0, 40, 50))
    window._note_render_busy()
    QTest.qWait(RENDER_SHOW_DELAY_MS + 50)
    assert window.render_label.text() == '正在生成缩略图'
    assert not window.render_label.isHidden()
    window.model.inflight.clear()
    QTest.qWait(2 * RENDER_HIDE_DELAY_MS + 70)
    assert window.render_label.isHidden()


def test_stale_page_render_and_close_cannot_leave_progress_visible(window):
    foreground(window, True)
    QTest.qWait(RENDER_SHOW_DELAY_MS + 50)
    window._preview_key = ('old-source', 0, 0, 300)
    window._update_render_progress()
    QTest.qWait(RENDER_HIDE_DELAY_MS + 50)
    assert window.render_label.isHidden()
    foreground(window, True)
    window.close_current_file()
    QTest.qWait(RENDER_SHOW_DELAY_MS + RENDER_HIDE_DELAY_MS + 50)
    assert window.render_label.isHidden()
