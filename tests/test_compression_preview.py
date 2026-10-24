"""Exercise the real shared viewport, wheel dispatch and asynchronous PDF previews."""
from time import monotonic, sleep

import pymupdf
import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QImage, QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app.compressionpreview import CompressionPreview
from app.pdfio import PdfStore


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


def wait_for(qapp, predicate):
    deadline = monotonic() + 12
    while not predicate() and monotonic() < deadline:
        qapp.processEvents()
        sleep(.01)
    assert predicate()


@pytest.fixture
def preview(qapp, tmp_path, request):
    path = tmp_path / 'generated-comparison.pdf'
    with pymupdf.open() as doc:
        for index in range(8):
            width, height = getattr(request, 'param', (420, 594))
            page = doc.new_page(width=width, height=height)
            page.insert_text((40, 60), f'PAGE {index + 1}  |  Generated comparison', fontsize=18)
            for y in range(100, 550, 35):
                page.insert_text((40, y), 'Sharp text 1234567890 - same location', fontsize=11)
        doc.save(path)
    store = PdfStore()
    refs = store.load(str(path))
    view = CompressionPreview()
    view.resize(1000, 620)
    view.show()
    view.set_documents(store, refs, refs)
    qapp.processEvents()
    yield view, store, refs
    view.shutdown()
    wait_for(qapp, lambda: not view.busy)
    view.close()
    view.deleteLater()
    qapp.processEvents()
    store.close_all()


def wheel(qapp, view, *, right=False, ctrl=False, delta=-120):
    local = QPointF(view.viewport().width() * (.75 if right else .25), view.viewport().height() / 2)
    event = QWheelEvent(local, local, QPoint(), QPoint(0, delta), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.ControlModifier if ctrl else Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    qapp.sendEvent(view.viewport(), event)
    qapp.processEvents()


def test_full_page_default_and_wheel_from_either_column_share_one_position(qapp, preview):
    view, _, _ = preview
    wait_for(qapp, lambda: bool(view.images))
    assert view.mode == 'page'
    assert view.horizontalScrollBar().maximum() == 0
    assert view.sizes[0][1] * view.scales[0] <= view.viewport().height() - 44
    for right in (False, True):
        view.verticalScrollBar().setValue(0)
        wheel(qapp, view, right=right)
        assert view.verticalScrollBar().value() > 0
    view.verticalScrollBar().setValue(view.offsets[3])
    assert view.current_page == 3
    assert view.visible_pages()[0] == 3
    key = next(iter(view.images))
    assert view.images[key][0] == view.images[key][1]


def test_ctrl_wheel_zoom_and_drag_keep_same_pair_and_page(qapp, preview):
    view, _, _ = preview
    view.scroll_to_page(2)
    wheel(qapp, view, right=True, ctrl=True, delta=120)
    assert isinstance(view.mode, int) and view.current_page == 2
    view.set_zoom(200)
    qapp.processEvents()
    assert view.horizontalScrollBar().maximum() > 0
    position = QPoint(view.viewport().width() // 4, 180)
    old = view.verticalScrollBar().value()
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=position)
    QTest.mouseMove(view.viewport(), position - QPoint(90, 80))
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=position - QPoint(90, 80))
    assert view.horizontalScrollBar().value() >= 80
    assert view.verticalScrollBar().value() >= old + 70
    # One pair render at one scale supplies both columns; there is no independent pan state.
    wait_for(qapp, lambda: view.image_key(view.current_page) in view.images)
    pair = view.images[view.image_key(view.current_page)]
    assert pair[0].size() == pair[1].size()


def test_continuous_scroll_updates_page_and_bounds_cache(qapp, preview):
    view, _, _ = preview
    events = []
    view.page_changed.connect(events.append)
    for index in (0, 2, 4, 6, 7):
        view.scroll_to_page(index)
        wait_for(qapp, lambda: view.image_key(index) in view.images)
        assert view.current_page == index
        assert len(view.images) <= 4
    assert 7 in events
    assert all(image.width() <= 2400 and image.width() * image.height() <= 4_010_000
               for pair in view.images.values() for image in pair)


def test_old_render_cannot_fill_new_document_or_closed_view(qapp, preview):
    view, store, refs = preview
    old = view.image_key(0)
    view.set_documents(store, refs[:2], refs[:2])
    view.pump.stop()
    wait_for(qapp, lambda: not view.busy)
    view.pump.stop()
    image = QImage(40, 50, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.red)
    view.rendered(old, (image, image), '')
    assert old not in view.images
    view.shutdown()
    current = view.image_key(0)
    view.rendered(current, (image, image), '')
    assert current not in view.images


def test_resize_retains_current_page_and_fit_without_horizontal_scroll(qapp, preview):
    view, _, _ = preview
    view.scroll_to_page(5)
    view.resize(850, 500)
    qapp.processEvents()
    assert view.current_page == 5 and view.horizontalScrollBar().maximum() == 0


@pytest.mark.parametrize('preview', [(420, 80)], indirect=True)
def test_many_visible_short_pages_do_not_create_a_render_loop(qapp, preview, monkeypatch):
    from app import compressionpreview
    view, _, _ = preview
    original_render = compressionpreview.render_preview
    calls = []
    def tracked(*args, **kwargs):
        calls.append(1)
        return original_render(*args, **kwargs)
    monkeypatch.setattr(compressionpreview, 'render_preview', tracked)
    visible = view.visible_pages()
    assert len(visible) > 3
    wanted = visible + ([visible[-1] + 1] if visible[-1] + 1 < len(view.pages) else [])
    wait_for(qapp, lambda: not view.busy and all(view.image_key(page) in view.images for page in wanted))
    count = len(calls)
    QTest.qWait(250)
    assert len(calls) == count
    assert len(view.images) <= max(4, len(visible) + 1)


def test_preview_does_not_block_event_loop_and_close_cancels_process(qapp, preview):
    from PyQt6.QtCore import QTimer
    view, _, _ = preview
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(5)
    wait_for(qapp, lambda: view.busy)
    view.shutdown()
    wait_for(qapp, lambda: not view.busy)
    timer.stop()
    assert ticks and view.cancel.is_set() and not view.pump.isActive()


def test_dialog_controls_work_at_small_desktop_size(qapp, tmp_path):
    from app.compression import CompressionDialog
    from app.ui import MainWindow
    path = tmp_path / 'dialog.pdf'
    with pymupdf.open() as doc:
        doc.new_page().insert_text((50, 80), 'First page')
        doc.new_page().insert_text((50, 80), 'Second page')
        doc.save(path)
    window = MainWindow()
    dialog = None
    try:
        window.ws.open_replace(window.store.load(str(path)), str(path))
        dialog = CompressionDialog(window)
        dialog.resize(860, 620)
        dialog.show()
        dialog.prepare()
        wait_for(qapp, lambda: bool(dialog.comparison.images))
        assert dialog.profile.currentData() == 'quality'
        assert dialog.baseline is not dialog.result
        assert dialog.rect().contains(dialog.export.geometry())
        assert dialog.export.isEnabled() and dialog.comparison.viewport().height() >= 260
        QTest.mouseClick(dialog.zoom_in, Qt.MouseButton.LeftButton)
        assert isinstance(dialog.comparison.mode, int)
        dialog.page.setValue(2)
        assert dialog.comparison.current_page == 1
        dialog.comparison.scroll_to_page(0)
        assert dialog.page.value() == 1
        dialog.profile.setCurrentIndex(dialog.profile.findData('custom'))
        assert dialog.advanced.isVisible() and not dialog.export.isEnabled()
        assert not dialog.comparison.pages
    finally:
        if dialog:
            dialog.reject()
            wait_for(qapp, lambda: not dialog.folder._temp._finalizer.alive)
            dialog.deleteLater()
        window.ws.reset()
        window.store.close_all()
        window.close()
