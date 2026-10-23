"""Image-only saves and delayed foreground rendering without personal files."""
from hashlib import sha256

import pymupdf as p
import pytest
from PyQt6.QtCore import QThreadPool, QRect, Qt, QTimer
from PyQt6.QtGui import QCloseEvent
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
    def warning(box):
        assert box.windowTitle() == '保存失败'
        assert win._task_dialog is None
        assert not any(dialog.isVisible() for dialog in win.findChildren(TaskProgressDialog))
        warnings.append(box.text())
        next(button for button in box.buttons()
             if box.buttonRole(button) == QMessageBox.ButtonRole.RejectRole).click()
        return 0
    monkeypatch.setattr(QMessageBox, 'exec', warning)
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


@pytest.mark.parametrize('phase', ['writing', 'error_dialog'])
def test_save_and_close_cannot_reenter_during_failed_save(window, monkeypatch, phase):
    from app.pdfio import PdfError

    window.ws.select_index(0)
    window.ws.rotate(90)
    writes = []
    prompts = []

    def reenter():
        assert not window.save()
        assert not window.confirm_leave()
        assert not window.close_tab(window._active_tab_id)
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted()

    def denied(*args, **kwargs):
        writes.append(1)
        if phase == 'writing' and len(writes) == 1:
            reenter()
        raise PdfError('写入被拒绝')

    def cancel(box):
        prompts.append(box.windowTitle())
        if phase == 'error_dialog' and len(prompts) == 1:
            reenter()
        box.reject()
        return 0

    monkeypatch.setattr(window, '_write_pdf', denied)
    monkeypatch.setattr(QMessageBox, 'exec', cancel)
    assert not window.save()
    assert writes == [1] and prompts == ['保存失败']
    assert window.ws.dirty and not window._saving


@pytest.mark.parametrize('recovery', ['save_as', 'cancel_destination', 'escape', 'close'])
def test_readonly_save_can_recover_or_cancel_then_close(window, tmp_path, monkeypatch, recovery):
    import stat

    source = tmp_path / 'scan.pdf'
    target = tmp_path / 'copy.pdf'
    original = source.read_bytes()
    mode = source.stat().st_mode
    window.ws.select_index(0)
    window.ws.rotate(90)
    edits = list(window.ws.pages)
    undo_count = window.ws.undo_stack.count()
    prompts = []
    execute = QMessageBox.exec

    def respond(box):
        prompts.append(box.windowTitle())
        assert prompts == ['保存失败']
        assert window._task_dialog is None
        assert not any(dialog.isVisible() for dialog in window.findChildren(TaskProgressDialog))
        if recovery in ('save_as', 'cancel_destination'):
            button = next(button for button in box.buttons()
                          if box.buttonRole(button) == QMessageBox.ButtonRole.AcceptRole)
            QTimer.singleShot(0, button.click)
        elif recovery == 'escape':
            QTimer.singleShot(0, lambda: QTest.keyClick(box, Qt.Key.Key_Escape))
        else:
            QTimer.singleShot(0, box.close)
        return execute(box)

    monkeypatch.setattr(QMessageBox, 'exec', respond)
    destinations = []
    def destination(*args):
        destinations.append(1)
        return ('' if recovery == 'cancel_destination' else str(target), 'PDF')
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', destination)
    source.chmod(stat.S_IREAD)
    try:
        assert window.save() is (recovery == 'save_as')
        assert prompts == ['保存失败']
        assert len(destinations) == int(recovery in ('save_as', 'cancel_destination'))
        assert not window._saving and not window._confirming_leave
        assert source.read_bytes() == original
        assert not list(tmp_path.glob('.zhixu-write-*'))
        if recovery == 'save_as':
            assert not window.ws.dirty and window.ws.path == str(target)
            with p.open(target) as saved:
                assert saved[0].rotation == 90
        else:
            assert window.ws.dirty and window.ws.pages == edits
            assert window.ws.undo_stack.count() == undo_count
            assert not target.exists()

            def leave(box):
                assert box.windowTitle() == '未保存的更改'
                # Repeated close requests must not stack another confirmation dialog.
                assert not window.confirm_leave()
                assert not window.close_tab(window._active_tab_id)
                next(button for button in box.buttons() if button.text() == choice).click()
                return 0

            monkeypatch.setattr(QMessageBox, 'exec', leave)
            choice = '取消'
            assert not window.close_tab(window._active_tab_id)
            assert window.ws.dirty and window.ws.pages == edits
            choice = '不保存'
            assert window.close_tab(window._active_tab_id)
            assert not window.ws.pages and not window._confirming_leave
            assert source.read_bytes() == original
    finally:
        source.chmod(mode)


@pytest.mark.parametrize('closing', ['tab', 'window'])
def test_failed_save_on_close_can_cancel_or_discard(window, monkeypatch, closing):
    from app.pdfio import PdfError

    window.ws.select_index(0)
    window.ws.rotate(90)
    edits = list(window.ws.pages)
    prompts = []
    writes = []
    choice = '保存'

    def denied(*args, **kwargs):
        writes.append(1)
        raise PdfError('写入被拒绝')

    def respond(box):
        prompts.append(box.windowTitle())
        if box.windowTitle() == '保存失败':
            box.reject()
        else:
            next(button for button in box.buttons() if button.text() == choice).click()
        return 0

    def close():
        if closing == 'tab':
            return window.close_tab(window._active_tab_id)
        event = QCloseEvent()
        window.closeEvent(event)
        return event.isAccepted()

    monkeypatch.setattr(window, '_write_pdf', denied)
    monkeypatch.setattr(QMessageBox, 'exec', respond)
    assert not close()
    assert prompts == ['未保存的更改', '保存失败'] and writes == [1]
    assert window.ws.dirty and window.ws.pages == edits
    assert not window._saving and not window._confirming_leave
    choice = '取消'
    assert not close()
    assert writes == [1] and window.ws.pages == edits
    choice = '不保存'
    assert window.close_tab(window._active_tab_id)
    assert writes == [1] and not window.ws.pages


def test_dismissing_leave_prompt_keeps_unsaved_edits(window, monkeypatch):
    window.ws.select_index(0)
    window.ws.rotate(90)
    edits = list(window.ws.pages)
    monkeypatch.setattr(QMessageBox, 'exec', lambda box: 0)
    assert not window.confirm_leave()
    assert window.ws.dirty and window.ws.pages == edits
    assert not window._confirming_leave


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
