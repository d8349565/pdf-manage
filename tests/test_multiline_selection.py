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


def test_double_click_selects_only_the_word_for_copy(reader, qapp):
    window, snapshot = reader
    window.set_reading_tool('select')
    view = window.preview_view
    hit = position(window, snapshot, 125, 54)
    qapp.clipboard().setText('Clipboard before selection')
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=hit)
    QTest.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, pos=hit)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=hit)
    QTest.keyClick(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert qapp.clipboard().text() == 'START1'
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


def test_bullet_list_drag_selection_keeps_visual_reading_order(qapp, tmp_path):
    from app.pdfedits import selection_range
    from app.documentlayout import selection_text
    from app.pdfio import PdfStore

    source = tmp_path / 'bullet-list.pdf'
    with p.open() as document:
        page = document.new_page(width=595, height=320)
        # Separate bullet glyph (smaller, vertically centered -> larger bbox y0) from bold+regular body on the same row.
        page.insert_text((40, 100), '\u2022', fontname='helv', fontsize=8)
        page.insert_text((56, 100), 'Included', fontname='hebo', fontsize=12)
        page.insert_text((112, 100), 'Stock balances, bin locations, open transfers.', fontname='helv', fontsize=12)
        page.insert_text((40, 124), '\u2022', fontname='helv', fontsize=8)
        page.insert_text((56, 124), 'Separate work', fontname='hebo', fontsize=12)
        page.insert_text((146, 124), 'Carrier integrations and pricing rules.', fontname='helv', fontsize=12)
        document.save(source)

    store = PdfStore()
    try:
        ref, = store.load(str(source))
        snapshot = store.text_snapshot(ref, include_tables=False)
        raw = snapshot['raw']
        # Drag from mid-line-1 ("balances") to mid-line-2 ("Carrier"): line 1's bullet must NOT be selected.
        start = (148, 96)
        end = (192, 120)
        selected_raw, area = selection_range(raw, start, end)
        text = ' '.join(selection_text(selected_raw, area, ()).split())
        assert text.startswith('alances,') or text.startswith('balances,')
        assert not text.startswith(('\u2022', '\u00b7'))
        assert 'Separate work' in text
        # Dragging from line 1's bullet through line 2 includes both items in natural order.
        full_raw, full_area = selection_range(raw, (39, 96), (192, 120))
        full_text = ' '.join(selection_text(full_raw, full_area, ()).split())
        assert full_text.startswith(('\u2022', '\u00b7'))
        assert full_text.index('Included') < full_text.index('Separate work')
    finally:
        store.close_all()


def test_selecting_text_on_visible_second_page_preserves_scroll_center(qapp, tmp_path):
    source = tmp_path / 'two-pages.pdf'
    with p.open() as document:
        for i in range(2):
            page = document.new_page(width=420, height=560)
            page.insert_text((50, 120), f'Page {i + 1} visible text line for selection.', fontsize=12)
        document.save(source)

    window = MainWindow()
    try:
        window.resize(1000, 760)
        window.show()
        assert window.open_files_in_tabs([str(source)])
        window._set_zoom_mode('page')
        for _ in range(200):
            if not window.preview_view.photo().isNull() and not window.rendering.active and not window.rendering.pending:
                break
            QTest.qWait(10)
        view = window.preview_view
        bar = view.verticalScrollBar()
        # Scroll so bottom of page 1 and top of page 2 are both visible while page 1 is still active.
        page2_top = view.mapFromScene(view.page_rects[1].topLeft()).y()
        bar.setValue(bar.value() + page2_top - view.viewport().height() // 2)
        QApplication.processEvents()
        before_center = view.mapToScene(view.viewport().rect().center())
        # Selecting on page 2 calls go_to_page(2, reveal=False) and must not snap the viewport center.
        window.go_to_page(2, reveal=False)
        QApplication.processEvents()
        after_center = view.mapToScene(view.viewport().rect().center())
        assert abs(after_center.y() - before_center.y()) < 2.0
    finally:
        window.ws.reset()
        window.close()
        qapp.processEvents()


@pytest.mark.parametrize('tight', [False, True])
@pytest.mark.parametrize('backwards', [False, True])
def test_drag_to_empty_space_after_short_line_includes_that_line(qapp, tmp_path, tight, backwards):
    source = tmp_path / 'short-line.pdf'
    sentences = ('FIRST a long preceding sentence.', 'OK', 'NEXT another long sentence.')
    with p.open() as doc:
        page = doc.new_page(width=390, height=275)
        for row, text in enumerate(sentences):
            page.insert_text((40, 90 + row * (15 if tight else 30)), text, fontsize=14)
        doc.save(source)
    window = MainWindow()
    try:
        window.resize(1000, 760)
        window.show()
        assert window.open_files_in_tabs([str(source)])
        snapshot = window.store.text_snapshot(window.ws.pages[0], include_tables=False)
        view = window.preview_view
        QApplication.processEvents()
        QTest.qWait(80)
        boxes = [p.Rect(line['bbox']) for block in snapshot['raw']['blocks'] for line in block.get('lines', ())]
        first, short, following = [(box.tl + box.br) / 2 for box in boxes]
        # In tight leading, stop just inside the short row's half of the overlap.
        y = (first.y + short.y) / 2 + 1 if tight else short.y
        inside = position(window, snapshot, boxes[0].x0, first.y)
        margin = position(window, snapshot, 260, y)
        start, end = (margin, inside) if backwards else (inside, margin)
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(view.viewport(), end)
        selected = window.text_selection
        assert ' '.join(selected.text.split()) == ' '.join(sentences[:2])
        path = selected.overlay.path()
        assert path.contains(view.mapToScene(position(window, snapshot, short.x, short.y)))
        assert not path.contains(view.mapToScene(position(window, snapshot, following.x, following.y)))
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        QTest.keyClick(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        assert ' '.join(QApplication.clipboard().text().split()) == ' '.join(sentences[:2])
    finally:
        window.ws.reset()
        window.close()
        qapp.processEvents()
