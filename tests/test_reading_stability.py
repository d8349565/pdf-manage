"""Actual Qt reading frames, using generated pages and isolated settings only."""
import json
from pathlib import Path

import pymupdf
import pytest
from PyQt6.QtCore import QThreadPool
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app.ui import MainWindow
from app.readingcache import native_width_matches


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


def settle(window, qapp, capture=None):
    timers = (window._reading_timer, window._reading_settle_timer,
              window._preview_timer, window._view_zoom_timer, window._detail_timer,
              window._preparation_timer, window.rendering._background_timer)
    for _ in range(600):
        qapp.processEvents()
        if capture is not None:
            capture()
        if (window.rendering.active is None and not window.rendering.pending
                and not window._reading_cache.busy
                and not any(timer.isActive() for timer in timers)):
            return
        QTest.qWait(10)
    pytest.fail(str(dict(active=window.rendering.active, pending=len(window.rendering.pending),
                         cache_loads=len(window._reading_cache._loads),
                         cache_write=bool(window._reading_cache._writing),
                         cache_pending=len(window._reading_cache._pending),
                         timers=[timer.interval() for timer in timers if timer.isActive()],
                         page=window.ws.preview_index(), cached_pages=len(window._page_images),
                         neighbors=list(window.preview_view.neighbors))))


@pytest.fixture
def reader(tmp_path, qapp, request):
    source = tmp_path / 'generated-reading.pdf'
    with pymupdf.open() as document:
        sizes = getattr(request, 'param', [(300, 400)] * 9)
        for number, (width, height) in enumerate(sizes):
            page = document.new_page(width=width, height=height)
            page.draw_rect(page.rect, color=None, fill=(.96, .97, 1))
            page.insert_text((22, 30), f'Reading page {number + 1}', fontsize=15)
            for line in range(30):
                y = 50 + line * 10
                page.insert_text((22, y), f'Line {line + 1:02} | The reading frame stays steady.', fontsize=7)
                page.draw_line((22, y + 2), (275, y + 2), color=(.65, .7, .75), width=.25)
        document.save(source)
    window = MainWindow()
    window.resize(1100, 740)
    window.act_pages.setChecked(False)
    window.act_tools.setChecked(False)
    window.ws.open_replace(window.store.load(str(source)), str(source))
    window.refresh()
    window.show()
    settle(window, qapp)
    yield window
    window.rendering.close()
    window.ws.reset()
    QThreadPool.globalInstance().waitForDone(10000)
    window.close()
    qapp.processEvents()


def check_frame(window, first, tmp_path, qapp):
    first.save(str(tmp_path / 'first-frame.png'))
    samples = dict(frames=0, changed=0)
    def capture():
        frame = window.preview_view.viewport().grab().toImage()
        samples['frames'] += 1
        if frame != first:
            samples['changed'] += 1
            if samples['changed'] == 1:
                frame.save(str(tmp_path / 'changed-frame.png'))
    settle(window, qapp, capture)
    final = window.preview_view.viewport().grab().toImage()
    final.save(str(tmp_path / 'settled-frame.png'))
    info = dict(page=window.ws.preview_index(), zoom=window._zoom,
                preview_width=window._preview_image.width(),
                detail_width=window.preview_view._detail.pixmap().width(),
                scale=window.preview_view.transform().m11(), samples=samples)
    (tmp_path / 'frame-info.json').write_text(json.dumps(info), encoding='utf-8')
    assert first == final and samples['changed'] == 0, f'The warmed page changed after it was shown: {info}'


def test_warmed_page_does_not_change_after_page_button(reader, tmp_path, qapp):
    assert reader.ws.pages[1].render_key in reader._page_images
    reader.go_to_page(2)
    first = reader.preview_view.viewport().grab().toImage()
    check_frame(reader, first, tmp_path, qapp)


def test_warmed_page_is_drawn_before_the_scroll_update_timer(reader, tmp_path, qapp):
    assert reader.ws.pages[1].render_key in reader._page_images
    reader.preview_view.jump_to_page(1)
    first = reader.preview_view.viewport().grab().toImage()
    check_frame(reader, first, tmp_path, qapp)


@pytest.mark.parametrize('layout', ['spread', 'four'])
def test_warmed_page_group_does_not_change_after_turning(reader, tmp_path, qapp, layout):
    reader.resize(1400, 900)
    reader._set_reading_layout(layout)
    settle(reader, qapp)
    reader.step_page(1)
    first = reader.preview_view.viewport().grab().toImage()
    check_frame(reader, first, tmp_path, qapp)


@pytest.mark.parametrize('reader', [[(300, 400), (520, 360), (300, 800)] * 3], indirect=True)
def test_mixed_page_sizes_are_prepared_for_their_fitted_size(reader, tmp_path, qapp):
    reader.go_to_page(2)
    first = reader.preview_view.viewport().grab().toImage()
    check_frame(reader, first, tmp_path, qapp)


def test_forward_and_backward_reading_keep_every_sampled_frame_stable(reader, tmp_path, qapp):
    for step, number in enumerate((2, 3, 4, 3, 2)):
        assert reader.ws.pages[number - 1].render_key in reader._page_images
        reader.go_to_page(number)
        first = reader.preview_view.viewport().grab().toImage()
        output = tmp_path / f'turn-{step}-{number}'
        output.mkdir()
        check_frame(reader, first, output, qapp)


@pytest.mark.parametrize('reader', [[(300, 400)] * 36], indirect=True)
def test_seen_pages_are_reused_during_fast_scroll_after_memory_eviction(reader, qapp, monkeypatch, tmp_path):
    monkeypatch.setattr('app.ui.READING_CACHE_BYTES', 2 * 1024 * 1024)
    for number in range(1, 37):
        reader.go_to_page(number)
        settle(reader, qapp)
    assert reader.ws.pages[0].render_key not in reader._page_images
    renders = []
    presentations = []
    original = reader.store.render_preview
    def rendered(ref, width):
        renders.append((ref.source_page, width))
        return original(ref, width)
    monkeypatch.setattr(reader.store, 'render_preview', rendered)
    show = reader.preview_view.set_photo
    def presented(pixmap, dpr, page_size=None):
        presentations.append((pixmap.width(), reader._preview_render_width()))
        return show(pixmap, dpr, page_size)
    monkeypatch.setattr(reader.preview_view, 'set_photo', presented)
    for number in (1, 12, 5, 30, 2, 35, 3):
        reader.preview_view.jump_to_page(number - 1)
        reader._reading_scrolled()
        QTest.qWait(35)
    settle(reader, qapp)
    (tmp_path / 'cache-return-metrics.json').write_text(json.dumps(dict(
        pages_read=36, memory_limit=2 * 1024 * 1024, native_renders=len(renders),
        presentations=presentations,
        low_quality_presentations=sum(not native_width_matches(width, target) for width, target in presentations)
    )), encoding='utf-8')
    assert not renders, f'Already read pages were rendered again: {renders}'
    assert presentations and all(native_width_matches(width, target) for width, target in presentations), presentations


def test_final_sharp_region_is_restored_from_disk_after_returning(reader, qapp, monkeypatch):
    reader._reading_cache.detail_limit = 0
    reader._set_zoom(8)
    reader.preview_view.jump_to_page(0)
    settle(reader, qapp)
    assert not reader.preview_view._detail.pixmap().isNull()
    first_page = reader.ws.pages[0].render_key
    assert first_page in reader._reading_cache.regions
    assert all(entry.image is None for entry in reader._reading_cache.regions[first_page].values())
    reader.go_to_page(2)
    settle(reader, qapp)
    renders = []
    original = reader.store.render_region
    def rendered(*args):
        renders.append(args[0].source_page)
        return original(*args)
    monkeypatch.setattr(reader.store, 'render_region', rendered)
    reader.go_to_page(1)
    settle(reader, qapp)
    assert not renders, f'The final sharp region was rendered again: {renders}'
    assert not reader.preview_view._detail.pixmap().isNull()


def test_closing_the_document_removes_its_temporary_reading_images(reader, qapp):
    directory = Path(reader._reading_cache._files.workspace.name)
    assert directory.is_dir() and reader._reading_cache.entries
    reader.close_current_file()
    settle(reader, qapp)
    assert not directory.exists() and not reader._reading_cache.entries


def test_closing_paint_does_not_access_released_sources(reader, qapp):
    reader._set_zoom(.5)
    view = reader.preview_view
    view.centerOn(view.page_rects[0].center().x(), view.page_rects[0].bottom())
    settle(reader, qapp)
    visible = view.mapToScene(view.viewport().rect()).boundingRect()
    assert any(index != view.active_page for index in view.visible_pages(visible))
    reader._closing = True
    reader.rendering.close()
    reader._reading_cache.close()
    reader.store.close_all()
    try:
        reader._show_cached_visible_pages()
    finally:
        reader._closing = False


def test_rotating_a_page_invalidates_its_old_reading_images(reader, qapp):
    page = reader.ws.pages[0]
    entry = reader._reading_cache.page(page.render_key, reader._preview_render_width())
    assert entry is not None and entry.path.exists()
    reader.ws.rotate(90)
    settle(reader, qapp)
    assert reader.ws.pages[0].render_key != page.render_key
    assert reader._reading_cache.page(page.render_key, entry.width) is None
    assert not entry.path.exists()


def test_failed_disk_restore_falls_back_to_rendering_without_getting_stuck(reader, qapp, monkeypatch):
    page = reader.ws.pages[0]
    entry = reader._reading_cache.page(page.render_key, reader._preview_render_width())
    reader.go_to_page(2)
    settle(reader, qapp)
    reader._page_images.clear()
    reader._page_image_bytes = 0
    entry.path.write_bytes(b'corrupt generated cache image')
    rendered = []
    original = reader.store.render_preview
    def render(ref, width):
        rendered.append(ref.source_page)
        return original(ref, width)
    monkeypatch.setattr(reader.store, 'render_preview', render)
    reader.go_to_page(1)
    settle(reader, qapp)
    assert 0 in rendered and not reader.preview_view.photo().isNull()
    assert not entry.path.exists()


def test_available_final_region_is_present_in_the_first_returning_frame(reader, qapp, tmp_path):
    reader._set_zoom(8)
    reader.preview_view.jump_to_page(0)
    settle(reader, qapp)
    reader.go_to_page(2)
    settle(reader, qapp)
    reader.go_to_page(1)
    first = reader.preview_view.viewport().grab().toImage()
    assert not reader.preview_view._detail.pixmap().isNull()
    check_frame(reader, first, tmp_path, qapp)
