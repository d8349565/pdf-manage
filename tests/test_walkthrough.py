from __future__ import annotations

import pymupdf
import pytest
from PyQt6.QtCore import QItemSelectionModel, QMimeData, QPoint, QSize, QThreadPool, Qt
from PyQt6.QtGui import QColor, QDragLeaveEvent, QDragMoveEvent, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QLabel,
    QListView,
    QMenu,
    QMessageBox,
    QToolButton,
)

from app.ui import (
    ARRANGE_ZOOM_MAX,
    ARRANGE_ZOOM_MIN,
    MIME,
    STYLESHEET,
    MainWindow,
    THUMB_ITEM_H_PAD,
    THUMB_ITEM_W_PAD,
    THUMB_LIST_SPACING,
    THUMB_MIN_H,
    THUMB_MIN_W,
    THUMB_PAD,
    THUMB_RATIO_DEFAULT,
    THUMB_RATIO_MAX,
    THUMB_RATIO_MIN,
    ZOOM_MAX,
    ZOOM_STEP,
    sidebar_thumb_width,
    thumb_ratio,
    wheel_zoom_steps,
)


@pytest.mark.parametrize('ending', ['complete', 'cancel', 'close'])
def test_excel_preparation_keeps_ui_responsive(window, qapp, tmp_path, monkeypatch, ending):
    import threading
    import time
    from PyQt6.QtCore import QTimer
    from app.pdf2excel.models import ConversionResult
    from app.pdf2excel.quality import assess_conversion
    import app.pdf2excel.converter as converter

    source = tmp_path / 'input.pdf'
    _make_pdf(source, 2, 'test')
    assert window._load_one(str(source))
    window.ws.select_index(0)
    window.ws.rotate(90)
    entered, release = threading.Event(), threading.Event()
    observed = {}
    def save_dialog(*args, **kwargs):
        observed['options'] = kwargs.get('options')
        return str(tmp_path / 'output.xlsx'), ''
    def prepare(pages, dest, *, progress_callback=None, options=None, cancel=None):
        observed['thread'] = threading.get_ident()
        observed['temp'] = dest
        observed['rotation'] = pages[0].rotation
        observed['progress'] = progress_callback
        entered.set()
        assert release.wait(5), 'Test did not release preparation'
    def convert(*args, **kwargs):
        result = ConversionResult([], 2, 0, 0, [])
        return result, assess_conversion(result)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', save_dialog)
    monkeypatch.setattr(window.store, 'write', prepare)
    monkeypatch.setattr(converter, 'convert_pdf_to_excel', convert)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: None)
    ticks = []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start()
    worker = None
    try:
        window.convert_to_excel()
        worker = window._conv_thread
        deadline = time.monotonic() + 3
        while (not entered.is_set() or len(ticks) < 3) and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.002)
        assert entered.is_set()
        assert len(ticks) >= 3
        assert worker.isRunning()
        assert observed['thread'] != threading.get_ident()
        assert observed['rotation'] == 90
        assert callable(observed['progress'])
        assert not (observed['options'] or QFileDialog.Option(0)) & QFileDialog.Option.DontUseNativeDialog
        window.convert_to_excel()
        assert window._conv_thread is worker
        if ending == 'cancel':
            window._conv_prog.canceled.emit()
            assert worker._cancelled
        elif ending == 'close':
            window.close()
            assert window._conv_thread is worker
            assert worker._cancelled
        release.set()
        deadline = time.monotonic() + 3
        while window._conv_thread is not None and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.002)
        assert window._conv_thread is None
        assert not __import__('os').path.exists(observed['temp'])
    finally:
        timer.stop()
        release.set()
        if getattr(window, '_conv_thread', None) is not None:
            window._conv_thread.wait(5000)
            qapp.processEvents()


@pytest.mark.parametrize('selected', [False, True])
def test_excel_real_worker_exports(window, qapp, tmp_path, monkeypatch, selected):
    import time
    import openpyxl
    source, output = tmp_path / 'table.pdf', tmp_path / 'out.xlsx'
    with pymupdf.open() as doc:
        for _ in range(2):
            page = doc.new_page(width=400, height=400)
            for x in (50, 150, 250):
                page.draw_line((x, 50), (x, 150))
            for y in (50, 100, 150):
                page.draw_line((50, y), (250, y))
            for r, row in enumerate([['Name', 'Price'], ['ABC', '123']]):
                for c, value in enumerate(row):
                    page.insert_text((55 + c * 100, 75 + r * 50), value)
        doc.save(source)
    assert window._load_one(str(source))
    if selected:
        window.ws.select_index(0)
        window.convert_selected.setChecked(True)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(output), ''))
    monkeypatch.setattr(QMessageBox, 'exec', lambda self: 0)
    messages = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: messages.append(a[-1]))
    window.convert_to_excel()
    worker = window._conv_thread
    try:
        deadline = time.monotonic() + 10
        while window._conv_thread is not None and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.002)
        assert window._conv_thread is None
        assert not messages
        wb = openpyxl.load_workbook(output)
        assert len(wb.worksheets) == (1 if selected else 2)
        for ws in wb.worksheets:
            assert list(ws.values) == [('Name', 'Price'), ('ABC', 123)]
    finally:
        if getattr(window, '_conv_thread', None) is not None:
            worker.cancel()
            worker.wait(10000)
            qapp.processEvents()


def test_excel_preparation_failure_cleans_up(window, qapp, tmp_path, monkeypatch):
    import os
    import time
    source = tmp_path / 'input.pdf'
    _make_pdf(source, 1, 'test')
    assert window._load_one(str(source))
    window.ws.select_index(0)
    window.ws.rotate(90)
    paths, messages = [], []
    def fail(pages, dest, *, progress_callback=None, options=None, cancel=None):
        assert callable(progress_callback)
        paths.append(dest)
        raise OSError('preparation failed')
    monkeypatch.setattr(window.store, 'write', fail)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(tmp_path / 'out.xlsx'), ''))
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: messages.append(a[-1]))
    window.convert_to_excel()
    worker = window._conv_thread
    try:
        deadline = time.monotonic() + 3
        while window._conv_thread is not None and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.002)
        assert window._conv_thread is None
        assert messages and 'preparation failed' in messages[0]
        assert paths and not os.path.exists(paths[0])
    finally:
        if getattr(window, '_conv_thread', None) is not None:
            worker.wait(5000)
            qapp.processEvents()


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def _make_pdf(path, n, label, rotation=0, w=300, h=400):
    doc = pymupdf.open()
    for i in range(n):
        page = doc.new_page(width=w, height=h)
        page.insert_text((40, 80), f"{label}-{i + 1}", fontsize=24)
        if rotation:
            page.set_rotation(rotation)
    doc.save(path)
    doc.close()


def _make_shaped_pdf(path, shapes):
    """每页给一组 (宽, 高)，用来试横竖混排。"""
    doc = pymupdf.open()
    for i, (w, h) in enumerate(shapes):
        page = doc.new_page(width=w, height=h)
        page.insert_text((40, 80), f"S-{i + 1}", fontsize=24)
    doc.save(path)
    doc.close()


@pytest.fixture
def window(qapp):
    win = MainWindow()
    yield win
    win.ws.reset()
    win.store.close_all()
    win.close()


def test_open_arrange_save(window, tmp_path):
    src = tmp_path / "合同.pdf"
    _make_pdf(src, 5, "C")
    original = src.read_bytes()
    assert window._load_one(str(src))
    window.ws.select_index(2)
    window.ws.move_page(2, 0)
    window.ws.rotate(90)
    assert window.ws.dirty
    assert src.read_bytes() == original
    assert window.save()
    assert not window.ws.dirty
    assert src.read_bytes() != original
    with pymupdf.open(src) as written:
        assert written.page_count == 5
        assert written[0].rotation == 90


def test_details_separate_source_rotation_from_this_session(window, tmp_path):
    """源文件自带 /Rotate 时，面板要把「本次旋转」和「源文件旋转」分开说清楚。"""
    src = tmp_path / "scan.pdf"
    _make_pdf(src, 1, "S", rotation=90)
    assert window._load_one(str(src))
    window.ws.select_index(0)
    window.refresh()
    assert "本次旋转 0°" in window.details.text()
    assert "源文件已旋转 90°" in window.details.text()

    window.ws.rotate(90)
    window.refresh()
    assert "本次旋转 90°" in window.details.text()
    assert "源文件已旋转 90°" in window.details.text()

    index = window.model.index(0)
    tip = window.model.data(index, Qt.ItemDataRole.ToolTipRole)
    assert "本次旋转 90°" in tip
    assert "源文件已旋转 90°" in tip


def test_details_omit_source_rotation_when_it_is_zero(window, tmp_path):
    src = tmp_path / "plain.pdf"
    _make_pdf(src, 1, "P")
    assert window._load_one(str(src))
    window.ws.select_index(0)
    window.refresh()
    assert "本次旋转 0°" in window.details.text()
    assert "源文件" not in window.details.text()


def test_merge_requires_new_path(window, tmp_path):
    a = tmp_path / "A.pdf"
    b = tmp_path / "B.pdf"
    _make_pdf(a, 1, "A")
    _make_pdf(b, 1, "B")
    a_bytes, b_bytes = a.read_bytes(), b.read_bytes()
    assert window._load_one(str(a))
    assert window._load_one(str(b))
    assert window.ws.path is None
    assert window.ws.dirty
    out = tmp_path / "合订.pdf"
    window.store.write(window.ws.pages, str(out))
    window.ws.rebase_to_saved_file(str(out), window.store.load(str(out)))
    assert a.read_bytes() == a_bytes
    assert b.read_bytes() == b_bytes
    assert pymupdf.open(out).page_count == 2


def test_split_keeps_dirty_workspace(window, tmp_path):
    src = tmp_path / "合同.pdf"
    _make_pdf(src, 3, "C")
    assert window._load_one(str(src))
    window.ws.select_index(0)
    window.ws.delete_selected()
    out = tmp_path / "parts"
    out.mkdir()
    groups = [[i] for i in range(len(window.ws.pages))]
    dests = [str(out / f"p{i}.pdf") for i in range(len(groups))]
    window.store.write_groups(window.ws.pages, groups, dests)
    assert len(list(out.glob("*.pdf"))) == 2
    assert window.ws.dirty
    assert window.ws.path.endswith("合同.pdf")


def test_confirm_leave_when_clean(window, tmp_path):
    src = tmp_path / "合同.pdf"
    _make_pdf(src, 1, "C")
    assert window._load_one(str(src))
    assert window.confirm_leave() is True


def test_zero_pages_save_blocked(window, tmp_path, monkeypatch):
    src = tmp_path / "合同.pdf"
    _make_pdf(src, 1, "C")
    assert window._load_one(str(src))
    window.ws.select_every_page()
    window.ws.delete_selected()
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    assert window.save() is False


def test_preview_and_thumbs_render(window, tmp_path, qapp):
    src = tmp_path / "合同.pdf"
    _make_pdf(src, 2, "C")
    assert window._load_one(str(src))
    window.refresh()
    for i in range(window.model.rowCount()):
        window.model.data(window.model.index(i), Qt.ItemDataRole.DecorationRole)
    QThreadPool.globalInstance().waitForDone(8000)
    qapp.processEvents()
    assert window._preview_image is not None
    assert not window._preview_image.isNull()
    assert not window.preview_view.photo().isNull()
    assert window.model.thumbs


def test_zoom_in_renders_wider(window, tmp_path):
    src = tmp_path / "合同.pdf"
    _make_pdf(src, 1, "C")
    assert window._load_one(str(src))
    window.resize(1100, 720)
    window._zoom = 1.0
    base = window._preview_render_width()
    window.zoom_in()
    assert window._zoom > 1
    assert window._preview_render_width() > base
    window.zoom_fit()
    assert window._zoom == 1.0


def test_wheel_zoom_is_debounced(window, tmp_path):
    src = tmp_path / "合同.pdf"
    _make_pdf(src, 1, "C")
    assert window._load_one(str(src))
    window.refresh()
    calls = {"n": 0}
    orig = window._update_preview

    def wrapped():
        calls["n"] += 1
        orig()

    window._update_preview = wrapped
    calls["n"] = 0
    window._zoom = 1.0
    for _ in range(8):
        window._set_zoom(min(ZOOM_MAX, window._zoom * ZOOM_STEP))
    assert calls["n"] == 0
    assert window._preview_timer.isActive()
    assert window._zoom <= ZOOM_MAX


def _enter_arrange(window, tmp_path, name="合同.pdf"):
    """进入编排视图。窗口得真的显示过，分栏和网格尺寸才算得准。"""
    src = tmp_path / name
    _make_pdf(src, 4, "C")
    assert window._load_one(str(src))
    window.resize(1100, 720)
    window.show()
    app = QApplication.instance()
    if app is not None:
        app.processEvents()
    window._fit_thumbs()
    window.refresh()
    window.stack.setCurrentIndex(1)


def test_arrange_zoom_scales_only_the_arrange_view(window, tmp_path):
    _enter_arrange(window, tmp_path)
    sidebar = window._sidebar_w
    assert window.model.icon_w == sidebar

    window.arrange_zoom_in()
    assert window._arrange_zoom > 1.0
    assert window.model.icon_w > sidebar
    assert window.arrange_view.iconSize().width() == window.model.icon_w
    assert window.left_view.iconSize().width() == sidebar


def test_sidebar_resize_does_not_overwrite_arrange_zoom(window, tmp_path):
    """左栏按窗口宽度算自己的尺寸，不能顺手把编排视图的缩放结果改回去。"""
    _enter_arrange(window, tmp_path)
    window.arrange_zoom_in()
    window.arrange_zoom_in()
    zoomed = window.model.icon_w
    assert zoomed > window._sidebar_w

    window._fit_thumbs()
    assert window.model.icon_w == zoomed
    assert window.arrange_view.iconSize().width() == zoomed


def test_rotating_one_thumbnail_preserves_other_portrait_card_sizes(window, tmp_path, qapp):
    _enter_arrange(window, tmp_path)
    qapp.processEvents()
    view = window.arrange_view
    before = [view.visualRect(window.model.index(i)).size() for i in range(3)]
    window.ws.select_index(3)
    window.rotate(90)
    window.refresh()
    qapp.processEvents()
    after = [view.visualRect(window.model.index(i)).size() for i in range(3)]
    assert after == before
    assert not view.uniformItemSizes()
    portrait = view.visualRect(window.model.index(0))
    landscape = view.visualRect(window.model.index(3))
    assert portrait.height() == landscape.height()
    assert portrait.width() < landscape.width()


def test_arrange_zoom_clamps_and_resets(window, tmp_path):
    _enter_arrange(window, tmp_path)
    for _ in range(30):
        window.arrange_zoom_in()
    assert window._arrange_zoom == ARRANGE_ZOOM_MAX
    for _ in range(60):
        window.arrange_zoom_out()
    assert window._arrange_zoom == ARRANGE_ZOOM_MIN
    window.arrange_zoom_fit()
    assert window._arrange_zoom == 1.0
    assert window.arrange_zoom_label.text() == "100%"
    assert window.model.icon_w == window._sidebar_w


def test_arrange_wheel_zoom_uses_the_same_steps(window, tmp_path):
    _enter_arrange(window, tmp_path)
    window._on_arrange_zoom_steps(1)
    zoomed = window._arrange_zoom
    assert zoomed > 1.0
    window._on_arrange_zoom_steps(-1)
    assert window._arrange_zoom == 1.0


def test_zoom_shortcuts_follow_the_visible_view(window, tmp_path):
    _enter_arrange(window, tmp_path)
    window.zoom_in()
    assert window._arrange_zoom > 1.0
    assert window._zoom == 1.0

    window.stack.setCurrentIndex(0)
    window.zoom_out()
    assert window._zoom < 1.0
    assert window._arrange_zoom > 1.0


def test_switching_views_renders_at_that_view_size(window, tmp_path):
    _enter_arrange(window, tmp_path)
    window.arrange_zoom_in()
    zoomed = window._arrange_width()
    assert window.model.icon_w == zoomed

    window.stack.setCurrentIndex(0)
    assert window.model.icon_w == window._sidebar_w


def test_wheel_zoom_steps_accumulate_small_deltas():
    acc, steps = wheel_zoom_steps(0, 40)
    assert (acc, steps) == (40, 0)
    acc, steps = wheel_zoom_steps(acc, 40)
    assert (acc, steps) == (80, 0)
    acc, steps = wheel_zoom_steps(acc, 40)
    assert (acc, steps) == (0, 1)
    acc, steps = wheel_zoom_steps(0, -240)
    assert (acc, steps) == (0, -2)


def _settled_thumb(window, qapp, row=0):
    """要两轮：第一轮把渲染挂到线程池上，第二轮才拿得到真图。"""
    role = Qt.ItemDataRole.DecorationRole
    for _ in range(2):
        window.model.data(window.model.index(row), role)
        QThreadPool.globalInstance().waitForDone(20000)
        qapp.processEvents()
    return window.model.data(window.model.index(row), role)


def test_thumbnails_render_at_device_pixel_ratio(window, tmp_path, qapp):
    """高分屏上按物理像素出图，但位图的逻辑尺寸仍要落在条目框里。"""
    src = tmp_path / "dpr.pdf"
    _make_pdf(src, 3, "D")
    assert window._load_one(str(src))
    window.model.set_icon_width(200)
    box = window.model._thumb_box(window.ws.pages[0])

    window.model.set_device_pixel_ratio(1.0)
    plain = _settled_thumb(window, qapp)
    window.model.set_device_pixel_ratio(2.0)
    crisp = _settled_thumb(window, qapp)

    assert crisp.devicePixelRatio() == 2.0
    assert crisp.width() > plain.width(), "dpr=2 必须渲染更大的位图"
    assert crisp.deviceIndependentSize().width() <= box.width() + 0.5
    assert crisp.deviceIndependentSize().height() <= box.height() + 0.5


def test_thumb_ratio_follows_page_shape():
    assert thumb_ratio(595, 842) == pytest.approx(842 / 595)
    assert thumb_ratio(842, 595) == pytest.approx(595 / 842)
    assert thumb_ratio(100, 1000) == THUMB_RATIO_MAX, "超长页不能把卡片拉成一根线"
    assert thumb_ratio(1000, 100) == THUMB_RATIO_MIN, "超宽页不能把卡片压扁到看不见"
    assert thumb_ratio(0, 0) == THUMB_RATIO_DEFAULT


def test_landscape_page_thumbnail_is_wide_and_short(window, tmp_path, qapp):
    """横页的框按横的来：宽 200 的框，高应当按 300/400 出 150，而不是 1.35 倍的 270。"""
    src = tmp_path / "wide.pdf"
    _make_pdf(src, 1, "W", w=400, h=300)
    assert window._load_one(str(src))
    window.model.set_icon_width(200)
    page = window.ws.pages[0]

    assert window.model._thumb_box(page) == QSize(200, 150)
    hint = window.model.data(window.model.index(0), Qt.ItemDataRole.SizeHintRole)
    assert hint.height() == 150 + THUMB_ITEM_H_PAD

    pix = _settled_thumb(window, qapp)
    assert pix.deviceIndependentSize().width() == pytest.approx(200, abs=0.5)
    assert pix.deviceIndependentSize().height() == pytest.approx(150, abs=0.5)

    # 转过 90° 的横页就成了竖页，框也得跟着翻过来。
    window.ws.select_index(0)
    window.ws.rotate(90)
    window.refresh()
    window.model.set_icon_width(200)
    assert window.model._thumb_box(window.ws.pages[0]) == QSize(200, 267)


def test_sidebar_cards_follow_each_page_orientation(window, tmp_path, qapp):
    """侧栏里一横一竖两页，条目框高度必须各按自己的比例，不能都被竖版框套住。"""
    src = tmp_path / "mixed.pdf"
    _make_shaped_pdf(src, [(300, 400), (400, 300)])
    assert window._load_one(str(src))
    window.resize(1100, 720)
    window.show()
    qapp.processEvents()
    window._fit_thumbs()

    portrait = window.model.data(window.model.index(0), Qt.ItemDataRole.SizeHintRole)
    landscape = window.model.data(window.model.index(1), Qt.ItemDataRole.SizeHintRole)
    assert landscape.width() == portrait.width()
    assert portrait.height() > landscape.height() * 1.5

    # 视图真的按逐条目的尺寸排：横页那一格比竖页矮，两格都占满侧栏宽度。
    left = window.left_view
    portrait_rect = left.visualRect(window.model.index(0, 0))
    landscape_rect = left.visualRect(window.model.index(1, 0))
    assert portrait_rect.isValid() and landscape_rect.isValid()
    assert landscape_rect.height() < portrait_rect.height()
    assert landscape_rect.width() == portrait_rect.width()
    assert landscape_rect.y() > portrait_rect.y()


def test_extreme_page_shape_still_leaves_room_for_the_label(window, tmp_path, qapp):
    src = tmp_path / "banner.pdf"
    _make_pdf(src, 1, "B", w=2000, h=100)
    assert window._load_one(str(src))
    window.model.set_icon_width(64)
    page = window.ws.pages[0]

    assert window.model._thumb_box(page).height() == THUMB_MIN_H
    assert window.model.item_height(page) - THUMB_ITEM_H_PAD >= THUMB_MIN_H


def test_drop_row_matches_the_slot_under_the_pointer(window, tmp_path, qapp):
    """拖拽时高亮的那一格，必须就是松手后页面落到的那一格。"""
    _enter_arrange(window, tmp_path)
    qapp.processEvents()
    view = window.arrange_view
    count = window.model.rowCount()
    assert count > 1

    seen = 0
    for row in range(count):
        rect = view.visualRect(window.model.index(row, 0))
        if rect.isEmpty():
            continue
        assert view._drop_row_at(rect.center()) == row
        seen += 1
    assert seen == count, "网格里每一格都要能算回自己"

    blank = QPoint(view.viewport().width() - 2, view.viewport().height() - 2)
    assert view._drop_row_at(blank) == count - 1, "拖到空白处按落到末尾算"


def test_drag_events_track_and_clear_the_landing_slot(window, tmp_path, qapp):
    _enter_arrange(window, tmp_path)
    qapp.processEvents()
    view = window.arrange_view

    mime = QMimeData()
    mime.setData(MIME, b"2")
    rect = view.visualRect(window.model.index(3, 0))
    event = QDragMoveEvent(
        rect.center(),
        Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    view.dragMoveEvent(event)
    assert event.isAccepted()
    assert view._drop_row == 3

    view.dragLeaveEvent(QDragLeaveEvent())
    assert view._drop_row is None


def test_drag_highlight_paints_the_landing_slot(window, tmp_path, qapp):
    _enter_arrange(window, tmp_path)
    qapp.processEvents()
    view = window.arrange_view
    assert view._drop_row is None

    def shot() -> QPixmap:
        canvas = QPixmap(view.viewport().size())
        canvas.fill(QColor("#FFFFFF"))
        painter = QPainter(canvas)
        view.render(painter)
        painter.end()
        return canvas

    plain = shot().toImage()
    view._set_drop_row(1)
    assert view._drop_row == 1
    marked = shot().toImage()

    def teal(image) -> int:
        hits = 0
        for y in range(image.height()):
            for x in range(image.width()):
                c = image.pixelColor(x, y)
                if abs(c.red() - 13) < 30 and abs(c.green() - 148) < 30 and abs(c.blue() - 136) < 30:
                    hits += 1
        return hits

    assert teal(marked) > teal(plain), "落点高亮没有被画出来"

    view._set_drop_row(None)
    assert view._drop_row is None


def test_drag_motion_does_not_reset_or_relayout_pages(window, tmp_path, qapp):
    from PyQt6.QtTest import QTest
    from PyQt6.QtCore import QAbstractAnimation, QRectF
    _enter_arrange(window, tmp_path)
    qapp.processEvents()
    view = window.arrange_view
    resets = []
    window.model.modelReset.connect(lambda: resets.append('reset'))
    window.model.layoutChanged.connect(lambda: resets.append('layout'))
    view._set_drop_row(0)
    view._set_drop_row(1)
    QTest.qWait(180)
    assert view._highlight_rect == QRectF(view.visualRect(window.model.index(1)))
    assert view._drop_motion.state() == QAbstractAnimation.State.Stopped
    assert resets == []
    view._set_drop_row(None)
    assert view._highlight_rect.isEmpty()
    view._show_landing(1)
    QTest.qWait(280)
    assert view._highlight_rect.isEmpty()
    assert view._drop_fade.state() == QAbstractAnimation.State.Stopped


def test_animated_drop_moves_page_and_supports_undo(window, tmp_path, qapp):
    from PyQt6.QtCore import QPointF
    from PyQt6.QtGui import QDropEvent
    _enter_arrange(window, tmp_path)
    qapp.processEvents()
    view = window.arrange_view
    before = list(window.ws.pages)
    mime = window.model.mimeData([window.model.index(0)])
    event = QDropEvent(QPointF(view.visualRect(window.model.index(2)).center()),
                       Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    view.dropEvent(event)
    assert event.isAccepted()
    assert window.ws.pages == [before[1], before[2], before[0], before[3]]
    assert window.ws.preview_index() == 2
    window.undo_action.trigger()
    qapp.processEvents()
    assert window.ws.pages == before


def test_arrange_extended_selection_writes_workspace(window, tmp_path, qapp):
    _enter_arrange(window, tmp_path)
    view = window.arrange_view
    assert view.selectionMode() == QListView.SelectionMode.ExtendedSelection
    sm = view.selectionModel()
    sm.select(
        window.model.index(0),
        QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Current,
    )
    sm.select(window.model.index(2), QItemSelectionModel.SelectionFlag.Select)
    sm.setCurrentIndex(window.model.index(2), QItemSelectionModel.SelectionFlag.Current)
    qapp.processEvents()
    assert window.ws.selected == {0, 2}
    assert window.ws.selected_index == 2
    assert window.ws.target_indices() == [0, 2]
    assert "已选 2 页" in window.arrange_sel.text()
    assert "已选 2 页" in window.statusBar().currentMessage()
    left = {
        idx.row()
        for idx in window.left_view.selectionModel().selectedIndexes()
        if idx.isValid()
    }
    assert left == {0, 2}


def test_arrange_selected_thumbs_are_visibly_stronger(window, tmp_path, qapp):
    _enter_arrange(window, tmp_path)
    window.ws.set_selection({0, 2}, current=0)
    window._sync_selection()
    qapp.processEvents()
    view = window.arrange_view
    canvas = QPixmap(view.viewport().size())
    canvas.fill(QColor("#FFFFFF"))
    painter = QPainter(canvas)
    view.render(painter)
    painter.end()
    image = canvas.toImage()

    def teal_hits(rect) -> int:
        hits = 0
        for y in range(max(0, rect.y()), min(image.height(), rect.y() + rect.height())):
            for x in range(max(0, rect.x()), min(image.width(), rect.x() + rect.width())):
                c = image.pixelColor(x, y)
                if abs(c.red() - 15) < 25 and abs(c.green() - 118) < 25 and abs(c.blue() - 110) < 25:
                    hits += 1
        return hits

    selected = teal_hits(view.visualRect(window.model.index(0)))
    idle = teal_hits(view.visualRect(window.model.index(1)))
    assert selected > idle * 3 + 20, f"选中态不够明显：选中 {selected} 未选 {idle}"


def test_open_failure_keeps_workspace(window, tmp_path, monkeypatch):
    src = tmp_path / "合同.pdf"
    _make_pdf(src, 2, "C")
    assert window._load_one(str(src))
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not-a-pdf")
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    window._ingest([str(bad)], replace=True)
    assert len(window.ws.pages) == 2
    assert window.ws.path.endswith("合同.pdf")


def test_cancel_password_keeps_workspace(window, tmp_path, monkeypatch):
    src = tmp_path / "合同.pdf"
    _make_pdf(src, 2, "C")
    assert window._load_one(str(src))
    enc = tmp_path / "enc.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(enc, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="owner")
    doc.close()
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("", False))
    window._ingest([str(enc)], replace=True)
    assert len(window.ws.pages) == 2
    assert window.ws.path.endswith("合同.pdf")


def test_append_failure_keeps_pages(window, tmp_path, monkeypatch):
    src = tmp_path / "合同.pdf"
    _make_pdf(src, 2, "C")
    assert window._load_one(str(src))
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not-a-pdf")
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    window._ingest([str(bad)], replace=False)
    assert len(window.ws.pages) == 2


def test_merge_save_rejects_source_path(window, tmp_path, monkeypatch):
    a = tmp_path / "A.pdf"
    b = tmp_path / "B.pdf"
    _make_pdf(a, 1, "A")
    _make_pdf(b, 1, "B")
    a_bytes = a.read_bytes()
    assert window._load_one(str(a))
    assert window._load_one(str(b))
    assert window.ws.path is None
    assert window.ws.blocks_save_to(str(a))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(a), "PDF (*.pdf)"))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    assert window.save() is False
    assert a.read_bytes() == a_bytes


def test_current_changed_syncs_selection(window, tmp_path):
    src = tmp_path / "合同.pdf"
    _make_pdf(src, 3, "C")
    assert window._load_one(str(src))
    window.ws.select_index(0)
    window._on_current_changed(window.model.index(2), window.model.index(0))
    assert window.ws.selected_index == 2
    assert window.ws.target_indices() == [2]
    window.ws.select_index(0)
    window._syncing = False
    window.left_view.selectionModel().setCurrentIndex(
        window.model.index(1),
        QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Current,
    )
    assert window.ws.selected_index == 1


def test_encrypted_save_preserves_protection(window, tmp_path, monkeypatch):
    path = tmp_path / "enc.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((40, 80), "secret")
    doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="owner")
    doc.close()
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("pw", True))
    assert window._load_one(str(path))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    assert window.save() is True
    with pymupdf.open(path) as saved:
        assert saved.needs_pass
        assert saved.authenticate("wrong") == 0
        assert saved.authenticate("pw") == 2
        assert saved[0].get_text().strip() == "secret"
        assert saved.authenticate("owner") == 4


def test_sidebar_thumb_width_fills_bar():
    # 缩略图宽度 = 控件宽度 - 竖向滚动条 - 条目横向占位（含左右间距）。
    assert sidebar_thumb_width(220, 17) == 220 - 17 - THUMB_PAD
    assert sidebar_thumb_width(20, 17) == THUMB_MIN_W
    # 条目占位必须把间距算进去，否则条目比 viewport 宽，会多出一条横向滚动条。
    assert THUMB_PAD == THUMB_ITEM_W_PAD + 2 * THUMB_LIST_SPACING


def test_sidebar_does_not_oscillate_with_its_scrollbars(window, tmp_path, qapp):
    window.act_pages.setChecked(True)
    """滚动条一出现 viewport 就变窄；缩略图宽度不能跟着 viewport 走，否则两边互相追，
    每次计时器一到就把卡片重新缩放一遍，看起来就是一直抖。"""
    src = tmp_path / "mixed.pdf"
    _make_shaped_pdf(src, [(802, 555)] * 6)
    assert window._load_one(str(src))
    window.refresh()
    window.resize(1200, 1000)
    window.show()
    qapp.processEvents()
    window._fit_thumbs()
    qapp.processEvents()

    view = window.left_view
    seen = set()
    for _ in range(6):
        window._fit_thumbs()
        qapp.processEvents()
        seen.add(
            (
                window.model.icon_w,
                view.viewport().width(),
                view.horizontalScrollBar().isVisible(),
                view.verticalScrollBar().isVisible(),
            )
        )
    assert len(seen) == 1, f"侧栏在几种尺寸之间来回跳：{sorted(seen)}"

    # 竖向滚动条常驻，viewport 宽度不随内容变，侧栏的卡片尺寸才钉得住。
    assert view.verticalScrollBar().isVisible()
    # 条目要放得进 viewport，横向滚动条不该在缩略图栏里出现。
    assert not view.horizontalScrollBar().isVisible()
    item = view.visualRect(window.model.index(0, 0))
    assert item.x() + item.width() <= view.viewport().width()


def test_thumb_size_hint_follows_icon_w(window, tmp_path):
    src = tmp_path / "合同.pdf"
    _make_pdf(src, 1, "C")
    assert window._load_one(str(src))
    window.model.set_icon_width(160)
    hint = window.model.data(window.model.index(0), Qt.ItemDataRole.SizeHintRole)
    assert hint.width() >= 160


def test_icon_toolbar_has_names_and_tooltips(window):
    required = {
        "缩小",
        "放大",
        "适合宽度",
        "适合窗口",
        "上一页",
        "下一页",
    }
    named = [btn for btn in window.findChildren(QToolButton) if btn.accessibleName()]
    names = {btn.accessibleName() for btn in named}
    assert required <= names
    for btn in named:
        assert btn.toolTip()
        assert btn.icon() and not btn.icon().isNull()
    from PyQt6.QtWidgets import QPushButton
    labels = {btn.text() for btn in window.findChildren(QPushButton)} | names
    assert {"打开 PDF", "保存", "另存为", "阅读", "整理页面", "左转", "右转", "删除页面", "全选"} <= labels
    assert window.append_action.text() == "合并文件…" and window.append_action.isEnabled()


def test_toolbar_icon_not_clipped_to_top_left(qapp):
    from app.icons import _pixmap

    pm = _pixmap("zoom-in", "#0F766E", 24)
    img = pm.toImage()
    w, h = img.width(), img.height()
    hits = 0
    for y in range(h // 2, h):
        for x in range(w // 2, w):
            if img.pixelColor(x, y).alpha() > 20:
                hits += 1
    assert hits > 30, "放大镜手柄应画在右下角；若只剩左上角碎片说明 DPI 裁切又出现了"


def test_menu_selected_item_shows_highlight(window, qapp):
    menu = next(m for m in window.findChildren(QMenu) if m.menuAction().text() == "视图")
    action = next(a for a in menu.actions() if a.text() == "放大")
    menu.popup(QPoint(80, 80))
    qapp.processEvents()
    menu.setActiveAction(action)
    qapp.processEvents()
    img = menu.grab().toImage()
    menu.hide()
    hits = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if abs(c.red() - 204) < 25 and abs(c.green() - 251) < 25 and abs(c.blue() - 241) < 25:
                hits += 1
    assert hits > 40, "菜单当前项应当有青色底，鼠标划过才看得出选中"


def test_pages_and_tools_can_hide(window, qapp, tmp_path):
    assert window.left_panel.isHidden()
    assert window.tools_panel.isHidden()
    source = tmp_path / "panels.pdf"
    _make_pdf(source, 2, "P")
    window._ingest([str(source)], replace=True)
    assert window.left_panel.isHidden()
    assert window.tools_panel.isHidden()
    window.act_pages.setChecked(True)
    window.act_tools.setChecked(True)
    assert not window.left_panel.isHidden()
    assert not window.tools_panel.isHidden()
    window.act_pages.setChecked(False)
    qapp.processEvents()
    assert window.left_panel.isHidden()
    window.act_tools.setChecked(False)
    qapp.processEvents()
    assert window.tools_panel.isHidden()
    window.act_pages.setChecked(True)
    window.act_tools.setChecked(True)
    qapp.processEvents()
    assert not window.left_panel.isHidden()
    assert not window.tools_panel.isHidden()
    assert window.act_pages.shortcut().toString() == "F8"
    assert window.act_tools.shortcut().toString() == "F9"


def test_tools_panel_uses_one_type_size(window):
    import re

    def rule_size(selector: str) -> int:
        match = re.search(
            rf"{re.escape(selector)}\s*\{{[^}}]*font-size:\s*(\d+)px",
            STYLESHEET,
        )
        assert match, f"找不到 {selector} 的字号"
        return int(match.group(1))

    assert rule_size("QLabel#caption") == 14
    assert rule_size("QPushButton") == 14
    assert rule_size("QRadioButton") == 14
    assert rule_size("QLineEdit") == 14
    assert rule_size("QLabel#hint") == 14
    texts = {w.text() for w in window.tools_panel.findChildren(QLabel) if w.objectName() == "caption"}
    assert "导出与转换" in texts
    assert [window.export_tools.itemText(i) for i in range(3)] == [
        "提取页面 · 一个新文件", "拆分文件 · 多个 PDF", "表格转 Excel",
    ]


def test_custom_title_bar_matches_app_chrome(window, qapp):
    assert bool(window.windowFlags() & Qt.WindowType.FramelessWindowHint)
    assert window.menuWidget() is not None
    from app import APP_NAME, __version__
    title = f"合同.pdf — {APP_NAME} v{__version__}（吾爱破解）"
    assert window.windowTitle().endswith(f"{APP_NAME} v{__version__}（吾爱破解）")
    window.setWindowTitle(title)
    assert window.title_label.toolTip() == title
    assert window.btn_min.accessibleName() == "最小化"
    assert window.btn_close.accessibleName() == "关闭"
    window.show()
    qapp.processEvents()
    bar = window.menuWidget().grab().toImage()
    hits = 0
    for y in range(bar.height()):
        for x in range(bar.width()):
            c = bar.pixelColor(x, y)
            if abs(c.red() - 243) < 20 and abs(c.green() - 246) < 20 and abs(c.blue() - 245) < 20:
                hits += 1
    assert hits > 200, "标题栏应跟窗口同色浅底，而不是系统深色标题"
