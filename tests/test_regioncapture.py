"""Region sharing checks; all documents are generated inside the test directory."""
import hashlib

import pymupdf as p
import pytest
from PyQt6.QtCore import QPointF, QRectF, QThreadPool, Qt
from PyQt6.QtGui import QFont, QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app.output import OperationCancelled
from app.pdfedits import annotation_summary
from app.pdfio import PdfError, PdfStore
from app.regioncapture import (CapturePreview, comment_document, comment_markers,
                               render_capture, save_capture, selected_comments)
from app.workspace import PageEdit


@pytest.fixture(scope='module')
def capture_app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_comment_selection_uses_displayed_position_and_includes_partial_marks(rotation):
    annotations = [
        dict(kind=4, rect=(20, 90, 70, 110), content='下方备注'),
        dict(kind=4, rect=(20, 20, 70, 40), content='上方备注'),
        dict(kind=4, rect=(210, 20, 250, 40), content='区域外备注'),
        dict(kind=4, rect=(20, 50, 70, 70), content=''),
        dict(kind=2, rect=(20, 50, 70, 70), content='页面上的文本框'),
        dict(kind=4, rect=(20, 50, 70, 70), content='水印', watermark=True),
        dict(kind=4, rect=(20, 50, 70, 70), content='图片', image_settings={'angle': 0}),
    ]
    matrix = p.Matrix(1, 1).prerotate(rotation)
    # Both included annotations cross the left edge of the selected region.
    area = p.Rect(40, 10, 180, 130) * matrix
    notes = selected_comments(annotations, matrix, area)
    assert {item['content'] for item in notes} == {'上方备注', '下方备注'}
    expected = '下方备注' if rotation in (90, 180) else '上方备注'
    assert notes[0]['content'] == expected
    assert all(area.contains(item['rect']) for item in notes)


def test_long_comments_wrap_as_plain_text_without_truncation(capture_app):
    content = '<原文> 金额修改意见\n' + '连续长编号0123456789' * 100
    font = QFont('Microsoft YaHei UI')
    font.setPixelSize(20)
    document = comment_document(content, 240, font)
    assert document.toPlainText() == content
    assert document.size().height() > 200


@pytest.mark.parametrize('scale', [1, 2.3])
def test_comment_numbers_avoid_each_other_and_point_to_their_own_mark(scale):
    # Overlapping notes, an edge note, and another mark require distinct positions.
    notes = [dict(rect=p.Rect(12, 23, 30, 41)) for _ in range(5)]
    notes.append(dict(rect=p.Rect(180, 100, 210, 130)))
    origin = (10.25, 20.5)
    points = comment_markers(notes, origin, scale, 44, 44, 760, 500, 14, 6)
    assert len(points) == len(notes)
    occupied = []
    for note, (point, anchor) in zip(notes, points):
        source = note['rect']
        mark = QRectF(44 + (source.x0 - origin[0]) * scale, 44 + (source.y0 - origin[1]) * scale,
                      source.width * scale, source.height * scale)
        badge = QRectF(point.x() - 16, point.y() - 16, 32, 32)
        assert QRectF(0, 0, 760, 500).contains(badge)
        assert mark.contains(anchor) and not mark.contains(point)
        assert not badge.intersects(mark)
        assert not any(badge.intersects(other) for other in occupied)
        occupied.append(badge)


@pytest.mark.parametrize('dpr', [1, 1.5, 2])
def test_preview_keeps_source_pixels_and_can_fit_zoom_and_inspect_actual_size(capture_app, monkeypatch, dpr):
    image = QImage(1000, 1400, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    view = CapturePreview(image)
    monkeypatch.setattr(view, 'devicePixelRatioF', lambda: dpr)
    try:
        view.resize(800, 600)
        view.show()
        capture_app.processEvents()
        assert view.item.pixmap().size() == image.size()
        view.set_mode('actual')
        assert view.transform().m11() * dpr == pytest.approx(1)
        view.zoom_by(1.2)
        assert view.transform().m11() * dpr == pytest.approx(1.2)
        view.resize(900, 650)
        capture_app.processEvents()
        assert view.transform().m11() * dpr == pytest.approx(1.2)
        view.set_mode('whole')
        capture_app.processEvents()
        displayed = view.mapFromScene(view.item.boundingRect()).boundingRect()
        assert view.viewport().rect().adjusted(-2, -2, 2, 2).contains(displayed)
    finally:
        view.close()
        view.deleteLater()
        capture_app.processEvents()


@pytest.mark.parametrize('source_rotation,extra_rotation', [(0, 0), (90, 0), (0, 270), (270, 180)])
def test_capture_uses_unsaved_notes_and_saves_whole_png_without_changing_source(
        capture_app, tmp_path, source_rotation, extra_rotation):
    path = tmp_path / 'generated.pdf'
    with p.open() as document:
        page = document.new_page(width=220, height=180)
        page.insert_text((30, 45), 'Original amount: 10,000')
        first = page.add_rect_annot((20, 25, 195, 55))
        first.set_info(content='原备注')
        first.update()
        second = page.add_text_annot((30, 100), '第二条备注')
        second.update()
        page.set_rotation(source_rotation)
        document.save(path)
    before = hashlib.sha256(path.read_bytes()).digest()
    store = PdfStore()
    try:
        ref = store.load(str(path))[0].rotated(extra_rotation)
        with store._render_page(ref) as page:
            identifier = annotation_summary(page)[0]['id']
        ref = ref.edited(PageEdit('note_text', annotation_id=identifier, text='尚未保存的新备注\n请核对金额。'))
        width, height = store.page_size(ref)
        image, count = render_capture(store, ref, (0, 0, width, height), lambda *args: None, lambda: False)
        assert count == 2 and not image.isNull()
        plain, _, _ = store.render_region(ref, (0, 0, width, height), 200 / 72)
        assert image.height() > plain.height() + 100
        with store._render_page(ref) as page:
            assert annotation_summary(page)[0]['content'] == '尚未保存的新备注\n请核对金额。'
        target = tmp_path / 'region.png'
        save_capture(image, str(target), None)
        reopened = QImage(str(target))
        assert reopened.size() == image.size() and reopened == image
        with pytest.raises(OSError, match='发生变化'):
            save_capture(image, str(target), None)
        assert not list(tmp_path.glob('.zhixu-capture-*'))
        assert hashlib.sha256(path.read_bytes()).digest() == before
    finally:
        store.close_all()


def test_capture_respects_copy_permission_and_cancellation(capture_app, tmp_path):
    plain = tmp_path / 'plain.pdf'
    protected = tmp_path / 'protected.pdf'
    with p.open() as document:
        document.new_page(width=200, height=200)
        document.save(plain)
        document.save(protected, encryption=p.PDF_ENCRYPT_AES_256, owner_pw='test-owner',
                      user_pw='', permissions=p.PDF_PERM_PRINT)
    store = PdfStore()
    try:
        ref = store.load(str(protected))[0]
        with pytest.raises(PdfError, match='不允许导出'):
            render_capture(store, ref, (10, 10, 100, 100), lambda *args: None, lambda: False)
        ref = store.load(str(plain))[0]
        image, count = render_capture(store, ref, (10, 10, 100, 100), lambda *args: None, lambda: False)
        assert count == 0 and not image.isNull()
        assert image.pixelColor(10, image.height() - 10).name() == '#ffffff'
        with pytest.raises(OperationCancelled):
            render_capture(store, ref, (10, 10, 100, 100), lambda *args: None, lambda: True)
        with pytest.raises(PdfError, match='太小'):
            render_capture(store, ref, (10, 10, 10, 10), lambda *args: None, lambda: False)
    finally:
        store.close_all()


def test_native_window_drag_reaches_capture_instead_of_cancelling(capture_app, tmp_path, monkeypatch):
    """Send through QWindow, not straight to the viewport, to cover native forwarding."""
    from app.ui import MainWindow

    path = tmp_path / 'blank-region.pdf'
    with p.open() as document:
        document.new_page(width=300, height=220)
        document.save(path)
    window = MainWindow()
    try:
        refs = window.store.load(str(path))
        window.ws.open_replace(refs, str(path))
        window.resize(1100, 850)
        window.show()
        window.refresh()
        capture_app.processEvents()
        capture = window.region_capture
        exported = []
        monkeypatch.setattr(capture, 'export', lambda *args: exported.append(args))
        window.capture_action.trigger()
        assert capture.active
        view = window.preview_view
        bounds = view.page_rects[0]
        start = view.mapFromScene(bounds.topLeft() + QPointF(30, 30))
        end = view.mapFromScene(bounds.topLeft() + QPointF(180, 130))
        native = window.windowHandle()
        QTest.mousePress(native, Qt.MouseButton.LeftButton, pos=view.viewport().mapTo(window, start))
        assert capture.active and capture.start is not None
        QTest.mouseMove(native, view.viewport().mapTo(window, end))
        assert capture.overlay.rect().width() > 100
        assert capture.overlay.rect().height() > 60
        QTest.mouseRelease(native, Qt.MouseButton.LeftButton, pos=view.viewport().mapTo(window, end))
        capture_app.processEvents()
        assert not capture.active and capture.overlay is None
        assert len(exported) == 1 and exported[0][1] == 0
        assert exported[0][2] == pytest.approx((30, 30, 180, 130), abs=2)
        assert not window.ws.dirty
        window.capture_action.trigger()
        QTest.keyClick(view, Qt.Key.Key_Escape)
        assert not capture.active
        window.capture_action.trigger()
        QTest.mouseClick(window.review_button, Qt.MouseButton.LeftButton)
        assert not capture.active
    finally:
        window.ws.reset()
        window.close()
        QThreadPool.globalInstance().waitForDone(30000)
        capture_app.processEvents()
