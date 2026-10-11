"""Search regressions use synthetic PDFs only."""
from dataclasses import replace
from threading import Event
import time

import pymupdf as p
import pytest
from PyQt6.QtCore import QPointF, QThreadPool, QTimer, Qt
from PyQt6.QtGui import QPolygonF
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app.pdfio import PdfStore
from app.searchui import SearchJob, SearchSignals
from app.ui import MainWindow


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def make_pdf(path, rotation=0, crop=False):
    with p.open() as doc:
        for index in range(3):
            page = doc.new_page(width=400, height=600)
            if index != 1:
                page.insert_text((50, 85), "采购预算及验收记录", fontname="china-s", fontsize=14)
                page.insert_text((50, 145), "Needle and needle", fontsize=12)
                page.insert_text((50, 550), "needle at the bottom", fontsize=12)
                page.insert_text((50, 215), "DATE2020 KEEP", fontsize=12)
            else:
                page.insert_image((30, 30, 300, 300), stream=p.Pixmap(p.csRGB, p.IRect(0, 0, 20, 20)).tobytes("png"))
            if crop:
                page.set_cropbox(p.Rect(20, 30, 380, 580))
            page.set_rotation(rotation)
        doc.save(path)


@pytest.fixture
def reader(qapp, tmp_path):
    source = tmp_path / "search.pdf"
    make_pdf(source)
    win = MainWindow()
    win.resize(1100, 760)
    win.show()
    win.ws.open_replace(win.store.load(str(source)), str(source))
    win.refresh()
    qapp.processEvents()
    try:
        yield win
    finally:
        win.search_bar.cancel()
        QThreadPool.globalInstance().waitForDone(30000)
        qapp.processEvents()
        win.ws.reset()
        win.close()
        qapp.processEvents()


def wait_search(win):
    end = time.monotonic() + 10
    while win.search_bar.busy and time.monotonic() < end:
        QTest.qWait(10)
    assert not win.search_bar.busy
    QApplication.processEvents()


def search(win, query):
    win.open_search()
    win.search_bar.input.setText(query)
    QTest.keyClick(win.search_bar.input, Qt.Key.Key_Return)
    wait_search(win)
    return win.search_bar


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("extra", [0, 90, 180, 270])
def test_chinese_and_rotated_cropped_coordinates(qapp, tmp_path, rotation, extra):
    source = tmp_path / "rotated.pdf"
    make_pdf(source, rotation, crop=True)
    store = PdfStore()
    try:
        ref = replace(store.load(str(source))[0], rotation=extra)
        results, text = store.search_page(ref, "预算")
        assert text and len(results) == 1
        with p.open(source) as doc:
            page = doc[0]
            quad = page.search_for("预算", quads=True)[0]
            matrix = p.Matrix(1, 1).prerotate(extra)
            bounds = page.rect * matrix
            matrix *= p.Matrix(1, 0, 0, 1, -bounds.x0, -bounds.y0)
            expected = [point * page.rotation_matrix * matrix for point in (quad.ul, quad.ur, quad.lr, quad.ll)]
        for actual, point in zip(results[0], expected):
            assert actual == pytest.approx(tuple(point))
        width, height = store.page_size(ref)
        assert all(0 <= x <= width and 0 <= y <= height for x,y in results[0])
        assert len(store.search_page(ref, "NEEDLE")[0]) == 3
        assert store.search_page(ref, " ") == ([], False)
    finally:
        store.close_all()


def test_keyboard_navigation_and_temporary_highlights(reader, tmp_path):
    reader.go_to_page(3)
    bar = search(reader, "needle")
    assert len(bar.results) == 6 and bar.current == 3
    assert bar.feedback.text() == "4 / 6"
    for key, modifiers, expected in [
        (Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier, 4),
        (Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier, 3),
        (Qt.Key.Key_F3, Qt.KeyboardModifier.NoModifier, 4),
        (Qt.Key.Key_F3, Qt.KeyboardModifier.ShiftModifier, 3),
    ]:
        QTest.keyClick(bar.input, key, modifiers)
        assert bar.current == expected
    bar.jump(5)
    QTest.keyClick(bar.input, Qt.Key.Key_Return)
    assert bar.current == 0 and reader.ws.preview_index() == 0
    assert set(reader.preview_view.search_areas) == {0, 2}
    assert not reader.ws.dirty and not reader.ws.undo_stack.count()
    saved = tmp_path / "saved.pdf"
    reader.store.write(reader.ws.pages, str(saved))
    with p.open(saved) as doc:
        assert all(not list(page.annots() or ()) for page in doc)
    QTest.keyClick(bar.input, Qt.Key.Key_Escape)
    assert bar.isHidden() and not reader.preview_view.search_areas
    QTest.keyClick(reader.preview_view, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    assert bar.isVisible() and bar.input.hasFocus()


def test_search_scrolls_to_bottom_and_follows_zoom(reader):
    bar = search(reader, "bottom")
    reader._set_zoom(2)
    bar.jump(0)
    page, points = bar.results[0]
    origin = reader.preview_view.page_rects[page].topLeft()
    bounds = QPolygonF([origin + QPointF(x, y) for x,y in points]).boundingRect()
    visible = reader.preview_view.mapToScene(reader.preview_view.viewport().rect()).boundingRect()
    assert visible.contains(bounds)
    assert reader.preview_view.current_search == 0
    assert bar.parentWidget().rect().contains(bar.geometry())
    reader.preview_view.setFocus()
    QTest.keyClick(reader.preview_view, Qt.Key.Key_Escape)
    assert bar.isHidden() and not reader.preview_view.search_areas


def test_search_minimum_window_with_both_panels(reader):
    reader.resize(980, 680)
    reader.act_pages.setChecked(True)
    reader.act_tools.setChecked(True)
    bar = search(reader, "needle")
    QApplication.processEvents()
    for widget in (bar.input, bar.feedback, bar.previous, bar.next, bar.close_button):
        assert bar.rect().contains(widget.geometry())
    assert reader.search_button.isVisible() and reader.search_button.isEnabled()


def test_reorder_rotation_delete_and_undo(reader):
    bar = search(reader, "预算")
    assert [i for i,_ in bar.results] == [0, 2]
    reader.ws.move_page(2, 0)
    reader.refresh()
    wait_search(reader)
    assert [i for i,_ in bar.results] == [0, 1]
    before = bar.results[0][1]
    reader.ws.select_index(0)
    reader.ws.rotate(90)
    reader.refresh()
    wait_search(reader)
    assert bar.results[0][1] != before
    reader.ws.delete_selected()
    reader.refresh()
    wait_search(reader)
    assert len(bar.results) == 1
    reader.ws.undo_stack.undo()
    reader.refresh()
    wait_search(reader)
    assert len(bar.results) == 2


def test_unsaved_replacement_and_undo(reader):
    bar = search(reader, "DATE2020")
    assert len(bar.results) == 2
    ref = reader.ws.pages[0]
    with p.open(ref.source_path) as doc:
        area = doc[0].search_for("DATE2020")[0]
    reader.ws.edit_page(0, reader.store.prepare_replacement(ref, tuple(area), "DATE2026"))
    reader.refresh()
    wait_search(reader)
    assert [i for i,_ in bar.results] == [2]
    bar = search(reader, "DATE2026")
    assert [i for i,_ in bar.results] == [0]
    reader.ws.undo_stack.undo()
    reader.refresh()
    wait_search(reader)
    assert not bar.results and "未找到" in bar.feedback.text()


def test_clear_close_new_document_and_stale_results(reader):
    bar = search(reader, "预算")
    stale = bar.token
    bar.input.setText("不存在的关键词")
    update = ([(0, ((0,0), (1,0), (1,1), (0,1)))], 3, 3, 2, 0, True, False)
    bar.receive(stale, update)
    assert not bar.results
    wait_search(reader)
    assert "未找到" in bar.feedback.text() and not bar.next.isEnabled()
    bar.input.clear()
    assert not bar.busy and not reader.preview_view.search_areas
    bar = search(reader, "预算")
    stale = bar.token
    bar.close_search()
    bar.receive(stale, update)
    assert not bar.results
    reader.ws.open_replace([reader.ws.pages[1]], "scan.pdf")
    reader.refresh()
    bar = search(reader, "预算")
    assert "文字层" in bar.feedback.text() and not bar.previous.isEnabled()


def test_progress_cancellation_failures_and_limit(qapp, monkeypatch):
    from app import searchui
    monkeypatch.setattr(searchui, "MAX_MATCHES", 12)
    class Store:
        def search_page(self, ref, query):
            if ref == 3:
                raise ValueError("damaged page")
            return [((1,1), (2,1), (2,2), (1,2))], True
    signals = SearchSignals()
    updates = []
    signals.updated.connect(lambda token, update: updates.append(update))
    cancelled = Event()
    SearchJob(Store(), tuple(range(100)), "query", 1, cancelled, signals).run()
    assert len(updates) == 2 and updates[-1][-2:] == (True, True)
    assert updates[-1][4] == 1
    assert sum(len(update[0]) for update in updates) == 12
    cancelled.set()
    SearchJob(Store(), (0,), "query", 1, cancelled, signals).run()
    assert len(updates) == 2


def test_search_finishing_in_arrange_mode_does_not_change_mode(reader, monkeypatch):
    original = reader.store.search_page
    def slow(ref, query):
        time.sleep(.03)
        return original(ref, query)
    monkeypatch.setattr(reader.store, "search_page", slow)
    reader.open_search()
    bar = reader.search_bar
    bar.input.setText("预算")
    bar.enter()
    reader.stack.setCurrentIndex(1)
    wait_search(reader)
    assert reader.stack.currentIndex() == 1 and len(bar.results) == 2
    reader.ws.merge_pages([reader.ws.pages[0]])
    reader.refresh()
    assert bar.busy
    wait_search(reader)
    assert reader.stack.currentIndex() == 1 and len(bar.results) == 3
    reader.stack.setCurrentIndex(0)
    bar.step(1)
    assert bar.current == 0


def test_long_document_keeps_event_loop_responsive(reader, monkeypatch):
    calls = []
    original = reader.store.search_page
    def slow(ref, query):
        time.sleep(.01)
        return original(ref, query)
    monkeypatch.setattr(reader.store, "search_page", slow)
    reader.ws.pages = [reader.ws.pages[0]] * 50
    reader.refresh()
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: calls.append(1))
    timer.start()
    try:
        bar = search(reader, "needle")
        assert len(bar.results) == 150 and len(calls) > 5
        assert bar.scanned == 50
    finally:
        timer.stop()

