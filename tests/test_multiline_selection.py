"""Read selections through real mouse gestures, visible highlights, and Ctrl+C."""
import pymupdf as p
import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app.ui import MainWindow


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(params=[0, 90, 180, 270])
def reader(qapp, tmp_path, request):
    source = tmp_path / 'generated-lines.pdf'
    with p.open() as document:
        page = document.new_page(width=360, height=240)
        for baseline, text in ((60, 'HEAD1 START1 END1'), (90, 'HEAD2 MIDDLE2 END2'), (120, 'HEAD3 STOP3 TAIL3')):
            page.insert_text((40, baseline), text, fontname='cour', fontsize=16)
        page.set_rotation(request.param)
        document.save(source)
    window = MainWindow()
    try:
        window.resize(1100, 800)
        window.show()
        assert window.open_files_in_tabs([str(source)])
        snapshot = window.store.text_snapshot(window.ws.pages[0], include_tables=False)
        for _ in range(300):
            if not window.preview_view.photo().isNull() and not window.rendering.active and not window.rendering.pending:
                break
            QTest.qWait(10)
        assert not window.preview_view.photo().isNull()
        QTest.qWait(30)
        yield window, snapshot
    finally:
        window.ws.reset()
        window.close()
        qapp.processEvents()


def position(window, snapshot, x, y):
    mapped = p.Point(x, y) * p.Matrix(snapshot['matrix'])
    return window.preview_view.mapFromScene(window.preview_view.page_rects[0].topLeft() + QPointF(*mapped))


@pytest.mark.parametrize('backwards', [False, True])
@pytest.mark.parametrize('end_x, expected', [
    (146, 'START1 END1 HEAD2 MIDDLE2 END2 HEAD3 STOP3'),
    (98, 'START1 END1 HEAD2 MIDDLE2 END2 HEAD3'),
    (69, 'START1 END1 HEAD2 MIDDLE2 END2 HEA'),
])
def test_multiline_drag_selects_line_end_whole_middle_line_and_last_line_start(reader, qapp, backwards, end_x, expected):
    window, snapshot = reader
    view = window.preview_view
    first = position(window, snapshot, 98, 54)
    last = position(window, snapshot, end_x, 114)
    if backwards:
        first, last = last, first
    middle_start = position(window, snapshot, 44, 76)
    excluded_start = position(window, snapshot, 44, 46)
    before = view.viewport().grab().toImage()
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=first)
    QTest.mouseMove(view.viewport(), last)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=last)
    qapp.processEvents()
    QTest.keyClick(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    copied = ' '.join(qapp.clipboard().text().split())
    assert copied == expected
    after = view.viewport().grab().toImage()
    assert after.pixelColor(middle_start) != before.pixelColor(middle_start)
    assert after.pixelColor(excluded_start) == before.pixelColor(excluded_start)
    assert not window.ws.dirty


@pytest.mark.parametrize('backwards', [False, True])
def test_horizontal_drag_keeps_single_line_character_selection(reader, qapp, backwards):
    window, snapshot = reader
    view = window.preview_view
    first = position(window, snapshot, 98, 54)
    last = position(window, snapshot, 155, 54)
    if backwards:
        first, last = last, first
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=first)
    QTest.mouseMove(view.viewport(), last)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=last)
    QTest.keyClick(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert qapp.clipboard().text().strip() == 'START1'
    assert not window.ws.dirty


def test_chinese_multiline_copy_keeps_the_complete_intermediate_line(reader, qapp, tmp_path):
    window, _ = reader
    with p.open(window.ws.path) as original:
        rotation = original[0].rotation
    source = tmp_path / 'generated-chinese.pdf'
    with p.open() as document:
        page = document.new_page(width=360, height=240)
        for baseline, text in ((60, '甲乙丙丁戊己庚辛壬癸'), (90, '第二行内容需要完整选中'), (120, '末行从头选到终点位置')):
            page.insert_text((40, baseline), text, fontname='china-s', fontsize=16)
        page.set_rotation(rotation)
        document.save(source)
    assert window.open_files_in_tabs([str(source)])
    snapshot = window.store.text_snapshot(window.ws.pages[0], include_tables=False)
    for _ in range(300):
        if not window.preview_view.photo().isNull() and not window.rendering.active and not window.rendering.pending:
            break
        QTest.qWait(10)
    view = window.preview_view
    first = position(window, snapshot, 121, 54)
    last = position(window, snapshot, 121, 114)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=first)
    QTest.mouseMove(view.viewport(), last)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=last)
    QTest.keyClick(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert ''.join(qapp.clipboard().text().split()) == '己庚辛壬癸第二行内容需要完整选中末行从头选'
    assert not window.ws.dirty
