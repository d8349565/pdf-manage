"""Reader gestures, fit modes and editable images; all inputs are generated here."""
import base64
import io

import pymupdf as p
import pytest
from PIL import Image
from PyQt6.QtCore import QEvent, QPoint, QPointF, QThreadPool, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit

from app.pdfedits import apply_edits
from app.pdfimages import IMAGE_SOURCE, fit_image, image_bounds, image_settings
from app.pdfio import PdfError, PdfStore, WriteOptions
from app.ui import MainWindow, PDF_LOGICAL_SCALE
from app.workspace import PageEdit


def image_data(transparent=False):
    stream = io.BytesIO()
    image = Image.new('RGBA' if transparent else 'RGB', (24, 16), (240, 40, 30, 0) if transparent else (240, 40, 30))
    if transparent:
        image.putpixel((12, 8), (20, 180, 40, 255))
    image.save(stream, format='PNG')
    return base64.b64encode(stream.getvalue()).decode('ascii')


def image_edit(kind='image', **changes):
    settings = dict(cx=100, cy=120, width=72, height=48, angle=0, locked=True)
    settings.update(changes)
    if kind == 'image':
        settings.setdefault('image', image_data())
    return PageEdit(kind, annotation_id='zhixu-image-regression', details=tuple(settings.items()))


def image_xrefs(doc):
    return [xref for xref in range(1, doc.xref_length()) if doc.xref_get_key(xref, 'Subtype') == ('name', '/Image')]


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_image_transforms_reuse_source_and_survive_save_and_reopen(rotation):
    with p.open() as doc:
        page = doc.new_page(width=240, height=320)
        page.set_rotation(rotation)
        apply_edits(page, [image_edit()])
        first = next(page.annots())
        source = doc.xref_get_key(first.xref, IMAGE_SOURCE)
        count = len(image_xrefs(doc))
        for angle in (23, -47, 90, 179):
            apply_edits(page, [image_edit('image_update', angle=angle)])
            annot = next(page.annots())
            assert doc.xref_get_key(annot.xref, IMAGE_SOURCE) == source
            assert len(image_xrefs(doc)) == count
            assert page.rotation == rotation
        saved = doc.tobytes(garbage=4, deflate=True)
    with p.open(stream=saved, filetype='pdf') as reopened:
        page = reopened[0]
        annot = next(page.annots())
        assert image_settings(page, annot)['angle'] == 179
        assert annot.flags & p.PDF_ANNOT_IS_PRINT
        assert page.get_pixmap().samples != bytes([255]) * len(page.get_pixmap().samples)
        apply_edits(page, [image_edit('image_update', angle=12, width=80)])
        assert image_settings(page, next(page.annots()))['width'] == 80
        apply_edits(page, [image_edit('image_delete')])
        clean = reopened.tobytes(garbage=4, deflate=True)
    with p.open(stream=clean, filetype='pdf') as reopened:
        assert not image_xrefs(reopened)
        assert not list(reopened[0].annots() or ())


def test_rotated_image_fits_page_without_changing_ratio():
    result = fit_image(dict(cx=-200, cy=800, width=800, height=400, angle=37), (0, 0, 240, 320))
    left, top, right, bottom = image_bounds(result)
    assert left >= -.001 and top >= -.001 and right <= 240.001 and bottom <= 320.001
    assert result['width'] / result['height'] == pytest.approx(2)
    with pytest.raises(ValueError):
        fit_image(dict(result, angle=float('nan')), (0, 0, 240, 320))


def test_transparent_image_preview_and_image_free_backdrop(tmp_path):
    path = tmp_path / 'generated.pdf'
    with p.open() as doc:
        doc.new_page(width=240, height=320)
        doc.save(path)
    store = PdfStore()
    try:
        ref = store.load(str(path))[0].edited(image_edit(image=image_data(transparent=True)))
        image, background = store.image_editor_preview(ref, 'zhixu-image-regression', 240)
        assert image.hasAlphaChannel() and image.pixelColor(0, 0).alpha() == 0
        assert background.pixelColor(100, 120).red() == 255
    finally:
        store.close_except(set())


def test_image_edits_require_content_permission(tmp_path):
    path = tmp_path / 'annotation-only.pdf'
    with p.open() as doc:
        doc.new_page()
        doc.save(path, encryption=p.PDF_ENCRYPT_AES_256, owner_pw='owner-generated',
                 user_pw='reader-generated', permissions=p.PDF_PERM_ANNOTATE)
    store = PdfStore()
    try:
        ref = store.load(str(path), 'reader-generated')[0].edited(image_edit())
        assert ref.edits[0].operation == 'content_edit'
        assert store.write_operation([ref], WriteOptions()) == 'modify'
        with pytest.raises(PdfError):
            store.write([ref], str(tmp_path / 'must-not-exist.pdf'))
        assert not (tmp_path / 'must-not-exist.pdf').exists()
    finally:
        store.close_except(set())


@pytest.fixture(scope='session')
def reader_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def reader_window(reader_app, tmp_path):
    path = tmp_path / 'generated-reader.pdf'
    with p.open() as doc:
        doc.new_page(width=240, height=480).insert_text((24, 64), 'Selectable generated words')
        doc.new_page(width=480, height=240)
        doc.save(path)
    window = MainWindow()
    refs = window.store.load(str(path))
    window.ws.open_replace(refs, str(path))
    window.resize(1100, 850)
    window.show()
    window.refresh()
    reader_app.processEvents()
    yield window
    window.ws.reset()
    window.close()
    QThreadPool.globalInstance().waitForDone(30000)
    reader_app.processEvents()


def test_fit_modes_show_whole_page_and_preserve_manual_scale(reader_window, reader_app):
    win = reader_window
    for number in (1, 2):
        win.go_to_page(number)
        win._set_zoom_mode('page')
        rect = win.preview_view.page_rects[number - 1]
        visible = win.preview_view.mapFromScene(rect).boundingRect()
        assert win.preview_view.viewport().rect().adjusted(-1, -1, 1, 1).contains(visible)
        win._reading_scrolled()
        assert win.ws.preview_index() == number - 1
    win.zoom_fit()
    assert win._zoom_mode == 'width'
    assert win.preview_view.transform().m11() * rect.width() == pytest.approx(
        win.preview_view.viewport().width() - 24, abs=2)
    win._set_zoom(1)
    win.resize(1000, 760)
    reader_app.processEvents()
    assert win._zoom_mode == 'custom'
    assert win.preview_view.transform().m11() == pytest.approx(PDF_LOGICAL_SCALE)


def test_space_drag_keeps_working_after_space_release(reader_window, reader_app):
    win = reader_window
    win._set_zoom(4)
    view = win.preview_view
    view.setFocus()
    reader_app.processEvents()
    QTest.keyPress(view, Qt.Key.Key_Space)
    assert win.reader_interaction.space
    origin = QPoint(180, 180)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=origin)
    assert win.reader_interaction.origin is not None
    QTest.keyRelease(view, Qt.Key.Key_Space)
    assert win.reader_interaction.panning
    previous = view.verticalScrollBar().value()
    QTest.mouseMove(view.viewport(), origin - QPoint(20, 40))
    assert view.verticalScrollBar().value() > previous
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=origin - QPoint(20, 40))
    assert not win.reader_interaction.panning


def test_canvas_space_does_not_steal_text_editor_input(reader_window, reader_app):
    edit = QLineEdit(reader_window.preview_view.viewport())
    edit.show()
    edit.setFocus()
    reader_app.processEvents()
    QTest.keyClicks(edit, 'one two')
    assert edit.text() == 'one two'
    assert not reader_window.reader_interaction.panning


def test_deactivation_resets_temporary_pan(reader_window, reader_app):
    view = reader_window.preview_view
    view.setFocus()
    reader_app.processEvents()
    QTest.keyPress(view, Qt.Key.Key_Space)
    assert reader_window.reader_interaction.panning
    QApplication.sendEvent(reader_window, QEvent(QEvent.Type.WindowDeactivate))
    assert not reader_window.reader_interaction.panning
    QTest.keyRelease(view, Qt.Key.Key_Space)


def test_text_cursor_uses_cached_geometry(reader_window, monkeypatch):
    win = reader_window
    ref = win.ws.pages[0]
    win.store.render_qimage(ref, 480)
    summary = win.store.cached_annotations(ref)
    box = p.Rect(summary['text_boxes'][0])
    page = win.preview_view.page_rects[0]
    point = win.preview_view.mapFromScene(page.topLeft() + QPointF(box.tl.x + box.width / 2, box.tl.y + box.height / 2))
    def forbidden(*args):
        raise AssertionError('Hover must not build a text/table snapshot')
    monkeypatch.setattr(win.store, 'text_snapshot', forbidden)
    win.reader_interaction.text_cursor(point)
    assert win.preview_view.viewport().cursor().shape() == Qt.CursorShape.IBeamCursor


def test_drag_preview_does_not_render_or_append_edits(reader_window, monkeypatch):
    win = reader_window
    edit = image_edit()
    candidate = win.ws.pages[0].edited(edit)
    win.store.render_qimage(candidate, 480)
    win.ws.edit_page(0, edit, '插入图片', prepared=candidate)
    win.refresh()
    tools = win.image_tools
    assert tools.select_image(0, dict(id=edit.annotation_id, image_settings=dict(edit.details)))
    previous = dict(tools.settings)
    center = tools.overlay.scenePos()
    tools.drag = dict(kind='move', settings=previous, point=tools.canonical(center))
    def forbidden(*args, **kwargs):
        raise AssertionError('Dragging must only transform the Qt overlay')
    monkeypatch.setattr(win.store, 'render_qimage', forbidden)
    monkeypatch.setattr(win.store, 'image_editor_preview', forbidden)
    for distance in (5, 10, 15):
        tools.update_drag(center + QPointF(distance, 0))
    assert tools.settings['cx'] != previous['cx']
    assert len(win.ws.pages[0].edits) == 1
    tools.clear()
