"""渲染路径的性能约束：这些断言一旦失败，说明界面会重新变卡。"""
from __future__ import annotations

import threading
from dataclasses import replace

import pymupdf
import pytest
from PyQt6.QtCore import QThreadPool, Qt, QRectF, QEvent
from PyQt6.QtGui import QPixmap, QImage, QKeyEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app.pdfio import PdfStore
from app.ui import (ARRANGE_ZOOM_MAX, THUMB_CACHE_BYTES, READING_CACHE_BYTES,
                    READING_PRELOAD_PAGES, READING_DRAFT_W, MainWindow, PreviewView)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def _make_pdf(path, n=3, w=300, h=400):
    doc = pymupdf.open()
    for i in range(n):
        page = doc.new_page(width=w, height=h)
        page.insert_text((40, 80), f"P-{i + 1}", fontsize=24)
    doc.save(str(path))
    doc.close()


def test_cached_text_lookup_never_waits_for_native_render_lock():
    store = PdfStore()
    acquired, release, exited = threading.Event(), threading.Event(), threading.Event()
    def hold_render_lock():
        with store._native_lock:
            acquired.set()
            release.wait(5)
        exited.set()
    worker = threading.Thread(target=hold_render_lock)
    worker.start()
    try:
        assert acquired.wait(5)
        # No ref is needed: a busy lock must return before reading page state.
        assert store.cached_text_snapshot(None, include_tables=False) is None
        assert not exited.is_set()
    finally:
        release.set()
        worker.join(5)


@pytest.fixture
def window(qapp):
    win = MainWindow()
    yield win
    win.ws.reset()
    win.store.close_all()
    win.close()


def _render_count(win) -> dict:
    counter = {"n": 0}
    original = win.store.render_qimage

    def counted(ref, width, **kwargs):
        counter["n"] += 1
        return original(ref, width, **kwargs)

    win.store.render_qimage = counted
    return counter


def _settle(qapp):
    # Serial rendering dispatches the next job after GUI completion delivery.
    for _ in range(60):
        QThreadPool.globalInstance().waitForDone(30000)
        QTest.qWait(5)


def test_detail_reuses_covered_image_and_rerenders_when_cleared(window, tmp_path, qapp, monkeypatch):
    source = tmp_path / "detail.pdf"
    _make_pdf(source, n=1)
    assert window._load_one(str(source))
    window.refresh()
    _settle(qapp)
    window._set_zoom(4)
    window._apply_view_zoom()
    view = window.preview_view
    page = window.ws.pages[0]
    scale = view.transform().m11() * max(view.devicePixelRatioF(), 1.0)
    window._preview_image = QImage(80, 100, QImage.Format.Format_RGB32)
    window._preview_key = (*page.render_key, 80)
    window._preview_busy = False
    window._detail_key = (*page.render_key, (0, 0, 300, 400), scale)
    window._detail_busy = False
    image = QImage(int(300 * scale), int(400 * scale), QImage.Format.Format_RGB32)
    view.set_detail(image, (0, 0), scale)
    jobs = []
    from types import SimpleNamespace
    with monkeypatch.context() as patch:
        patch.setattr(QThreadPool, "globalInstance", lambda: SimpleNamespace(start=lambda *args: jobs.append(args)))
        window._request_detail()
        assert not jobs and not window._detail_busy
        view.clear_detail()
        window._request_detail()
        assert len(jobs) == 1 and window._detail_busy


def test_render_width_matches_request_for_every_rotation(qapp, tmp_path):
    src = tmp_path / "rot.pdf"
    _make_pdf(src)
    store = PdfStore()
    page = store.load(str(src))[0]
    for rotation in (0, 90, 180, 270):
        for width in (200, 640):
            image = store.render_qimage(replace(page, rotation=rotation), width)
            assert abs(image.width() - width) <= 1, (rotation, width, image.width())
            assert not image.isNull()
            assert image.format() == QImage.Format.Format_RGB32
    store.close_all()


def test_page_size_never_waits_on_a_running_render(qapp, tmp_path):
    """UI 线程查页尺寸不能去抢渲染锁，否则点一下就卡住。"""
    src = tmp_path / "size.pdf"
    _make_pdf(src)
    store = PdfStore()
    page = store.load(str(src))[0]
    source = store._sources[page.source_id]

    source.lock.acquire()
    try:
        done = threading.Event()

        def call():
            store.page_size(page)
            done.set()

        threading.Thread(target=call, daemon=True).start()
        assert done.wait(2.0), "page_size 阻塞在文档锁上"
    finally:
        source.lock.release()
    assert store.page_size(page) == (300.0, 400.0)
    store.close_all()


def test_thumbnail_completion_invalidates_only_its_own_row(window, tmp_path, qapp):
    src = tmp_path / "thumbs.pdf"
    _make_pdf(src, 12)
    assert window._load_one(str(src))
    window.refresh()
    window.model.thumb_visible = lambda key: True

    ranges: list[tuple[int, int]] = []
    window.model.dataChanged.connect(
        lambda tl, br, roles=None: ranges.append((tl.row(), br.row()))
    )
    for i in range(window.model.rowCount()):
        window.model.data(window.model.index(i), Qt.ItemDataRole.DecorationRole)
    _settle(qapp)

    assert ranges, "缩略图完成后应当通知视图"
    wide = [(a, b) for a, b in ranges if a != b]
    assert not wide, f"每张缩略图只应刷新自己那一行，实际刷新了 {wide}"


def test_zoom_out_reuses_the_rendered_image(window, tmp_path, qapp):
    src = tmp_path / "zoom.pdf"
    _make_pdf(src)
    assert window._load_one(str(src))
    window.resize(1100, 720)
    window._set_zoom(1.0)
    window._update_preview()
    _settle(qapp)
    base = window._preview_image.width()

    window._set_zoom(1.5)
    window._update_preview()
    _settle(qapp)
    assert window._preview_image.width() > base

    counter = _render_count(window)
    window._set_zoom(1.0)
    window._update_preview()
    _settle(qapp)
    assert counter["n"] == 0, "缩小应当复用已有高分辨率图，不该重新渲染"
    assert window._preview_image.width() == base
    assert window._page_images[window.ws.pages[0].render_key].width() == base


@pytest.mark.parametrize("dpr", [1.0, 1.5, 2.0])
@pytest.mark.parametrize("cached_ratio", [.6, 1.0, 1.8])
def test_settled_reading_uses_native_pixels_after_cached_image_scaling(window, tmp_path, qapp, monkeypatch, dpr, cached_ratio):
    source = tmp_path / "native-pixels.pdf"
    _make_pdf(source, n=1)
    assert window._load_one(str(source))
    window.show()
    window.refresh()
    window._set_zoom(.5)
    _settle(qapp)
    window._reading_cache.retain(set())
    view = window.preview_view
    monkeypatch.setattr(view, "devicePixelRatioF", lambda: dpr)
    scale = view.transform().m11() * dpr
    width = round(300 * scale * cached_ratio)
    window._preview_image = QImage(width, round(width * 4 / 3), QImage.Format.Format_RGB32)
    window._preview_image.fill(Qt.GlobalColor.white)
    ref = window.ws.pages[0]
    window._preview_key = (*ref.render_key, width)
    window._preview_busy = window._scrolling = False
    window._invalidate_detail_requests()
    view.clear_detail()
    requests = []
    monkeypatch.setattr(window.rendering, "request", lambda *args, **kwargs: requests.append((args, kwargs)))
    window._request_detail()
    if cached_ratio == 1:
        assert not requests and view._detail.pixmap().isNull()
        return
    assert len(requests) == 1
    args, kwargs = requests[0]
    region, requested_scale = kwargs["region"]
    assert requested_scale == pytest.approx(scale)
    result = window.store.render_region(ref, region, requested_scale)
    window._on_detail_preview(args[2], result)
    assert not view._detail.pixmap().isNull()
    assert view._detail.scale() * view.transform().m11() * dpr == pytest.approx(1)
    window._request_detail()
    assert len(requests) == 1


def test_extreme_zoom_out_rerenders_a_small_image(window, tmp_path, qapp):
    """复用要有上界，否则一张超大图会被一直重采样。"""
    src = tmp_path / "zoomout.pdf"
    _make_pdf(src)
    assert window._load_one(str(src))
    window.resize(1100, 720)
    window._set_zoom(4.0)
    window._update_preview()
    _settle(qapp)
    big = window._preview_image.width()

    counter = _render_count(window)
    window._set_zoom(1.0)
    window._update_preview()
    _settle(qapp)
    assert counter["n"] == 1
    assert window._preview_image.width() < big


def test_zoom_in_still_sharpens(window, tmp_path, qapp):
    """复用逻辑不能走过头，否则放大只会越来越糊。"""
    src = tmp_path / "sharp.pdf"
    _make_pdf(src)
    assert window._load_one(str(src))
    window.resize(1100, 720)
    window._set_zoom(1.0)
    window._update_preview()
    _settle(qapp)
    first = window._preview_image.width()

    counter = _render_count(window)
    window._set_zoom(3.0)
    window._update_preview()
    _settle(qapp)
    assert counter["n"] == 1, "放大到 3 倍必须重新渲染"
    assert window._preview_image.width() > first


def test_high_zoom_redraws_visible_pdf_region(window, tmp_path, qapp):
    src = tmp_path / "vector.pdf"
    _make_pdf(src)
    assert window._load_one(str(src))
    window.resize(1100, 720)
    window.show()
    window._update_preview()
    _settle(qapp)
    width = window.preview_view.content_width()
    window._set_zoom(64.0)
    window._update_preview()
    _settle(qapp)
    window._request_detail()
    _settle(qapp)
    assert window.preview_view.content_width() == width
    detail = window.preview_view._detail
    assert not detail.pixmap().isNull()
    assert detail.pixmap().width() < window.preview_view.viewport().width() + 55
    assert detail.pixmap().height() < window.preview_view.viewport().height() + 55
    assert abs(detail.scale() * window.preview_view.transform().m11() - 1.0) < 0.01
    assert window._preview_image.width() * window._preview_image.height() < 8_020_000
    old_key = window._detail_key
    bar = window.preview_view.horizontalScrollBar()
    bar.setValue(bar.maximum() if bar.value() < bar.maximum() else 0)
    window._request_detail()
    _settle(qapp)
    assert window._detail_key != old_key
    window.go_to_page(2)
    assert window.preview_view._detail.pixmap().isNull(), "换页后不能显示上一页的高清局部"


def test_shrinking_the_sidebar_does_not_rerender_thumbnails(window, tmp_path, qapp):
    src = tmp_path / "sidebar.pdf"
    _make_pdf(src, 8, w=595, h=842)
    assert window._load_one(str(src))
    window.model.set_icon_width(320)
    for i in range(window.model.rowCount()):
        window.model.data(window.model.index(i), Qt.ItemDataRole.DecorationRole)
    _settle(qapp)

    counter = _render_count(window)
    for width in (256, 200, 128, 96):
        window.model.set_icon_width(width)
        for i in range(window.model.rowCount()):
            pix = window.model.data(window.model.index(i), Qt.ItemDataRole.DecorationRole)
            box = window.model._thumb_box(window.ws.pages[i])
            assert not pix.isNull()
            # Qt 拿 DecorationRole 位图的原始尺寸当绘制尺寸，超框就会被条目框裁掉。
            assert pix.width() <= box.width(), (width, pix.width(), box.width())
            assert pix.height() <= box.height(), (width, pix.height(), box.height())
        _settle(qapp)
    assert counter["n"] == 0, "侧栏变窄应当复用已有的大缩略图"


def test_thumbnail_cache_drops_pages_that_left_the_workspace(window, tmp_path, qapp):
    src = tmp_path / "cache.pdf"
    _make_pdf(src, 6)
    assert window._load_one(str(src))
    for page in window.ws.pages:
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.GlobalColor.white)
        window.model._cache_put(page.render_key, (pixmap, 16))
    assert len(window.model.thumbs) == 6

    window.ws.select_every_page()
    window.ws.delete_selected()
    window.refresh()
    assert len(window.model.thumbs) <= 1, "离开工作区的缩略图不应一直占着内存"


def test_arrange_zoom_out_reuses_the_rendered_thumbnails(window, tmp_path, qapp):
    src = tmp_path / "arrange.pdf"
    _make_pdf(src, 8, w=595, h=842)
    assert window._load_one(str(src))
    window.resize(1100, 720)
    window.refresh()
    window.stack.setCurrentIndex(1)
    for _ in range(3):
        window.arrange_zoom_in()
    for i in range(window.model.rowCount()):
        window.model.data(window.model.index(i), Qt.ItemDataRole.DecorationRole)
    _settle(qapp)

    counter = _render_count(window)
    for _ in range(3):
        window.arrange_zoom_out()
    for i in range(window.model.rowCount()):
        pix = window.model.data(window.model.index(i), Qt.ItemDataRole.DecorationRole)
        assert not pix.isNull()
        assert pix.width() <= window.model._thumb_box(window.ws.pages[i]).width()
    _settle(qapp)
    assert counter["n"] == 0, "编排视图缩小应当复用已有的大缩略图"


def test_arrange_zoom_in_rerenders_sharper_thumbnails(window, tmp_path, qapp):
    """放大不能只把旧位图拉大，否则编排视图越放大越糊。"""
    src = tmp_path / "sharpthumb.pdf"
    _make_pdf(src, 4, w=595, h=842)
    assert window._load_one(str(src))
    window.resize(1100, 720)
    window.refresh()
    window.show()
    qapp.processEvents()
    window._fit_thumbs()
    window.stack.setCurrentIndex(1)
    for i in range(window.model.rowCount()):
        window.model.data(window.model.index(i), Qt.ItemDataRole.DecorationRole)
    _settle(qapp)
    key = window.model._thumb_key(window.ws.pages[0])
    first = window.model.thumbs[key][1]

    counter = _render_count(window)
    # 新工作台会随窗口宽度调整页面栏，因此不要假定固定放大两次就一定跨过
    # 64 px 的渲染档位；放大到确实需要更高分辨率时再验证重绘。
    while window.model._thumb_render_width() <= first and window._arrange_zoom < ARRANGE_ZOOM_MAX:
        window.arrange_zoom_in()
    for i in range(window.model.rowCount()):
        window.model.data(window.model.index(i), Qt.ItemDataRole.DecorationRole)
    _settle(qapp)
    assert counter["n"] > 0, "放大后必须重渲染更清晰的缩略图"
    assert window.model.thumbs[key][1] > first


def test_saving_reuses_the_thumbnail_cache(window, tmp_path, qapp):
    """保存只是把同样的内容写进了新文件，不该让所有缩略图重画一遍。"""
    src = tmp_path / "save.pdf"
    _make_pdf(src, 6)
    assert window._load_one(str(src))
    window.refresh()
    window.model.thumb_visible = lambda key: True
    for i in range(window.model.rowCount()):
        window.model.data(window.model.index(i), Qt.ItemDataRole.DecorationRole)
    _settle(qapp)
    assert len(window.model.thumbs) == 6

    counter = _render_count(window)
    assert window.save()
    for i in range(window.model.rowCount()):
        window.model.data(window.model.index(i), Qt.ItemDataRole.DecorationRole)
    _settle(qapp)
    assert counter["n"] == 0, "保存后缩略图应当沿用已有位图"
    assert len(window.model.thumbs) == 6


def test_saving_releases_documents_the_undo_stack_no_longer_needs(window, tmp_path, qapp):
    src = tmp_path / "recycle.pdf"
    _make_pdf(src, 2)
    assert window._load_one(str(src))
    window.refresh()
    _settle(qapp)
    assert len(window.store._sources) == 1

    window.save()
    _settle(qapp)
    assert len(window.store._sources) == 1, "没有撤销步骤时旧文档应当立刻回收"
    window.save()
    window.save()
    _settle(qapp)
    assert len(window.store._sources) == 1, "反复保存不该累积文档"


def test_thumb_cache_stays_within_budget(window):
    """放大后的位图很大，缓存必须有上限，否则几百页就能吃掉几个 G。"""
    window.model.thumbs.clear()
    window.model._thumb_bytes = 0
    side = 2000
    for i in range(20):
        pix = QPixmap(side, side)
        pix.fill(Qt.GlobalColor.white)
        window.model._cache_put((f"k{i}", 0, 0), (pix, side))

    assert window.model._thumb_bytes <= THUMB_CACHE_BYTES
    assert len(window.model.thumbs) < 20
    assert ("k0", 0, 0) not in window.model.thumbs, "应当先淘汰最久没用过的"


def test_preview_render_is_not_duplicated_by_a_single_click(window, tmp_path, qapp):
    src = tmp_path / "click.pdf"
    _make_pdf(src, 4)
    assert window._load_one(str(src))
    window.refresh()
    _settle(qapp)

    counter = _render_count(window)
    index = window.model.index(0)
    window._preview_key = None
    window._on_click(index)
    window._on_current_changed(index, index)
    window._update_preview()
    _settle(qapp)
    assert counter["n"] <= 1, f"同一页被重复渲染了 {counter['n']} 次"


def test_long_document_only_keeps_visible_and_neighbor_images(window, tmp_path, qapp):
    src = tmp_path / "long.pdf"
    _make_pdf(src, 80)
    window.resize(1100, 720)
    window.show()
    assert window._load_one(str(src))
    window.refresh()
    qapp.processEvents()
    for page in (1, 40, 80):
        window.go_to_page(page)
        window._reading_scrolled()
        _settle(qapp)
        assert window.ws.preview_index() == page - 1
        assert len(window.preview_view.neighbors) <= 3
        assert len(window._neighbor_pending) <= 2 * READING_PRELOAD_PAGES
    assert len(window.preview_view.page_rects) == 80


def _record_preloads(window, tmp_path, monkeypatch, *, page=8, layout='single'):
    source = tmp_path / 'preload.pdf'
    _make_pdf(source, 15)
    requests = []
    monkeypatch.setattr(window.rendering, 'request',
                        lambda *args, **kwargs: requests.append((args, kwargs)))
    window.resize(1400, 900)
    window.act_pages.setChecked(False)
    window.act_tools.setChecked(False)
    window.show()
    assert window._load_one(str(source))
    window.refresh()
    window._set_reading_layout(layout)
    window.go_to_page(page)
    window._reading_settled()
    requests.clear()
    window._reading_scrolled()
    return requests


@pytest.mark.parametrize('page', [1, 8, 15])
def test_preload_covers_three_pages_in_each_direction_at_reading_resolution(
        window, tmp_path, monkeypatch, page):
    requests = _record_preloads(window, tmp_path, monkeypatch, page=page)
    neighbors = [args for args, _ in requests if args[3] is window._neighbor_signals]
    index = page - 1
    expected = set(range(max(0, index - 3), min(15, index + 4))) - {index}
    assert {args[2][1] for args in neighbors} == expected
    assert all(args[1] == window._preview_render_width(args[2][1]) for args in neighbors)
    assert all(args[5] < 0 for args in neighbors if args[2][1] != index)
    if page == 8:
        assert [args[2][1] for args in neighbors] == [8, 6, 9, 5, 10, 4]


@pytest.mark.parametrize('layout', ['spread', 'four'])
def test_preload_surrounds_the_current_page_group(window, tmp_path, monkeypatch, layout):
    requests = _record_preloads(window, tmp_path, monkeypatch, layout=layout)
    group = window.preview_view.pages_in_spread(window.ws.preview_index())
    expected = {index for page in range(max(0, group.start - 3), min(15, group.stop + 3))
                for index in window.preview_view.pages_in_spread(page)}
    expected.discard(window.ws.preview_index())
    neighbors = [args for args, _ in requests if args[3] is window._neighbor_signals]
    assert {args[2][1] for args in neighbors} == expected
    visible = set(window.preview_view.visible_pages(window.preview_view.mapToScene(
        window.preview_view.viewport().rect()).boundingRect()))
    assert all(args[5] < 0 for args in neighbors if args[2][1] not in visible)


def test_preloaded_image_stays_in_cache_and_is_reused_when_opening_that_page(
        window, tmp_path, monkeypatch):
    requests = _record_preloads(window, tmp_path, monkeypatch)
    args = next(args for args, _ in requests if args[3] is window._neighbor_signals and args[5] == -1)
    ref, width, key = args[:3]
    image = window.store.render_preview(ref, width)
    window._on_neighbor(key, image)
    assert ref.render_key in window._page_images
    assert key[1] not in window.preview_view.neighbors
    requests.clear()
    window.go_to_page(key[1] + 1)
    assert window._preview_image.width() >= width
    assert not window.preview_view.photo().isNull()
    assert not any(args[0] is ref and args[3] is window.preview_signals for args, _ in requests)


def test_scroll_and_zoom_discard_outdated_preloads(window, tmp_path, monkeypatch):
    requests = _record_preloads(window, tmp_path, monkeypatch)
    previous = [args for args, _ in requests if args[3] is window._neighbor_signals and args[5] < 0]
    requests.clear()
    window._scrolling = window.rendering.scrolling = True
    window._reading_scrolled()
    assert all(args[4]() for args in previous)
    assert all(args[1] <= READING_DRAFT_W for args, _ in requests if args[3] is window._neighbor_signals)
    window._scrolling = window.rendering.scrolling = False
    window._reading_scrolled()
    previous = [args for args, _ in requests if args[3] is window._neighbor_signals and args[5] < 0]
    window._zoom_mode = 'custom'
    window._zoom *= 2
    window._reading_scrolled()
    assert all(args[4]() for args in previous)


def test_large_preloads_fit_the_existing_cache_budget(window, tmp_path, monkeypatch):
    requests = _record_preloads(window, tmp_path, monkeypatch)
    window._zoom_mode = 'custom'
    window._zoom = 8
    requests.clear()
    window._reading_scrolled()
    index = window.ws.preview_index()
    widths = {index: window._preview_render_width(index)}
    widths.update({args[2][1]: args[1] for args, _ in requests if args[3] is window._neighbor_signals})
    cost = sum(width * (int(width * 400 / 300) + 2) * 4 for width in widths.values())
    assert cost <= READING_CACHE_BYTES
    assert len(widths) < 7


def test_toolbar_input_also_defers_preloading(window):
    window.rendering._background_after = 0
    QApplication.sendEvent(window.zoom_label, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Shift,
                                                       Qt.KeyboardModifier.ShiftModifier))
    assert window.rendering._background_after > 0
    assert window.rendering._background_timer.isActive()


def test_preloading_yields_until_foreground_reading_and_search_are_ready(window, tmp_path, monkeypatch):
    _record_preloads(window, tmp_path, monkeypatch)
    for timer in (window._detail_timer, window._view_zoom_timer,
                  window._preview_timer, window._reading_settle_timer):
        timer.stop()
    window._preview_busy = False
    assert window._reading_preload_ready()
    for name in ('_preview_busy', '_detail_busy', '_saving'):
        setattr(window, name, True)
        assert not window._reading_preload_ready()
        setattr(window, name, False)
    window.search_bar.busy = True
    assert not window._reading_preload_ready()
    window.search_bar.busy = False
    window._task_dialog = object()
    assert not window._reading_preload_ready()
    window._task_dialog = None
    assert window._reading_preload_ready()


def test_high_zoom_detail_on_later_page_uses_local_pdf_coordinates(window, tmp_path, qapp):
    src = tmp_path / "later.pdf"
    _make_pdf(src)
    window.resize(1100, 720)
    window.show()
    assert window._load_one(str(src))
    window.refresh()
    window.go_to_page(2)
    window._set_zoom(8)
    window._update_preview()
    window.preview_view.jump_to_page(1)
    _settle(qapp)
    window._request_detail()
    _settle(qapp)
    assert window._detail_key is not None
    region = window._detail_key[3]
    assert 0 <= region[1] < region[3] <= 400
    assert window.preview_view._detail.pos().y() >= window.preview_view.page_rects[1].top()
    assert not window.preview_view._detail.pixmap().isNull()


def test_visible_page_lookup_does_not_scan_the_document(qapp):
    view = PreviewView()
    view.set_pages(tuple(range(10000)), [(300, 400)] * 10000)
    class IndexedOnly(list):
        def __iter__(self):
            raise AssertionError('Scrolling must not scan all page rectangles')
    view.page_rects = IndexedOnly(view.page_rects)
    assert view.page_at_y(416 * 5000 + 100) == 5000
    assert list(view.visible_pages(QRectF(0, 416 * 5000 + 100, 300, 500))) == [5000, 5001]
    assert view.page_at_y(-10) == 0
    assert view.page_at_y(10_000_000) == 9999
    assert view.page_at_y(402) == 0
    assert view.page_at_y(414) == 1


def test_sustained_scroll_does_not_starve_page_updates(window):
    ticks = []
    window._reading_timer.timeout.connect(lambda: ticks.append(True))
    for _ in range(30):
        window._schedule_reading_update()
        QTest.qWait(4)
    assert ticks, 'Continuous input must not postpone all updates until scrolling stops'


def test_returning_to_recent_page_reuses_cached_render(window, tmp_path, qapp):
    src = tmp_path / 'back.pdf'
    _make_pdf(src)
    assert window._load_one(str(src))
    window.refresh()
    _settle(qapp)
    window.go_to_page(2)
    _settle(qapp)
    counter = _render_count(window)
    window.go_to_page(1)
    _settle(qapp)
    assert counter['n'] == 0
    assert not window.preview_view.photo().isNull()


def test_reading_cache_has_a_byte_budget(window):
    for i in range(12):
        image = QImage(1600, 1600, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        window._cache_page_image(('test', i, 0), image)
    assert window._page_image_bytes <= READING_CACHE_BYTES
    assert ('test', 0, 0) not in window._page_images
    assert ('test', 11, 0) in window._page_images


def test_scroll_displays_small_cached_page_before_requesting_a_draft(window, tmp_path, qapp, monkeypatch):
    src = tmp_path / 'draft.pdf'
    _make_pdf(src)
    assert window._load_one(str(src))
    window.refresh()
    _settle(qapp)
    page = window.ws.pages[1]
    image = QImage(96, 128, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    window._page_images.clear()
    window._page_image_bytes = 0
    window._cache_page_image(page.render_key, image)
    window.ws.select_index(1)
    window.preview_view.active_page = 1
    window._scrolling = True
    requested = []
    monkeypatch.setattr(window.rendering, 'request', lambda *args, **kwargs: requested.append(args))
    window._request_preview(page)
    assert window.preview_view.photo().width() == 96
    assert requested and requested[0][1] <= 640
    assert window._preview_busy


def test_sharp_result_keeps_scroll_position_and_zoom(window, tmp_path, qapp):
    src = tmp_path / 'anchor.pdf'
    _make_pdf(src)
    window.resize(1100, 720)
    window.show()
    assert window._load_one(str(src))
    window.refresh()
    _settle(qapp)
    view = window.preview_view
    page = window.ws.pages[window.ws.preview_index()]
    width = window._preview_render_width()
    window._preview_key = (*page.render_key, width)
    image = QImage(width, int(width * 4 / 3), QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    before = (view.horizontalScrollBar().value(), view.verticalScrollBar().value(), view.transform().m11())
    from app.rendering import RenderResult
    window._on_preview(window._preview_key, RenderResult(window.rendering.generation, image))
    assert before == (view.horizontalScrollBar().value(), view.verticalScrollBar().value(), view.transform().m11())


def test_scroll_updates_page_number_without_full_permission_sync(window, tmp_path, qapp, monkeypatch):
    src = tmp_path / 'navigation.pdf'
    _make_pdf(src)
    window.resize(1100, 720)
    window.show()
    assert window._load_one(str(src))
    window.refresh()
    _settle(qapp)
    def unexpected_sync():
        raise AssertionError('Scrolling must not rescan document permissions')
    monkeypatch.setattr(window, '_sync_controls', unexpected_sync)
    window.preview_view.jump_to_page(1)
    window._reading_scrolled()
    assert window.ws.preview_index() == 1
    assert window.page_number.value() == 2


def test_old_generation_cannot_replace_a_current_preview(window):
    from app.rendering import RenderResult
    key = ('source', 0, 0, 640)
    window._preview_key = key
    window._preview_busy = True
    image = QImage(640, 800, QImage.Format.Format_RGB32)
    window._on_preview(key, RenderResult(window.rendering.generation - 1, image))
    assert window._preview_image is None and window._preview_busy


def test_old_neighbor_completion_after_reset_is_discarded(window, tmp_path, qapp):
    src = tmp_path / 'closed.pdf'
    _make_pdf(src)
    assert window._load_one(str(src))
    window.refresh()
    _settle(qapp)
    key = (window.preview_view.layout_generation, 1)
    window._neighbor_pending.add(key)
    window.ws.reset()
    window._on_neighbor(key, QImage(20, 20, QImage.Format.Format_RGB32))
    assert key not in window._neighbor_pending
    assert 1 not in window.preview_view.neighbors


def test_thumbnail_completion_uses_row_index_including_duplicate_pages(window, tmp_path, qapp):
    src = tmp_path / 'duplicates.pdf'
    _make_pdf(src)
    assert window._load_one(str(src))
    window.ws.merge_pages(window.ws.pages[:1])
    window.refresh()
    _settle(qapp)
    changed = []
    window.model.dataChanged.connect(lambda a, b, roles: changed.append((a.row(), b.row())))
    key = window.model._thumb_key(window.ws.pages[0])
    window.model._thumb_key = lambda p: pytest.fail('Completion must not scan every page')
    window.model._on_thumb(key, QImage(100, 100, QImage.Format.Format_RGB32))
    assert changed == [(0, 0), (3, 3)]
