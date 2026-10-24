"""Paired pages and reader interactions; all documents are generated here."""
import pytest
import pymupdf
from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QApplication

from app.ui import MainWindow, PreviewView


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


def settle(qapp, reader):
    for _ in range(500):
        qapp.processEvents()
        timers = (reader._reading_timer, reader._reading_settle_timer,
                  reader._preview_timer, reader._detail_timer)
        if not reader.rendering.active and not reader.rendering.pending and not any(t.isActive() for t in timers):
            return
        QTest.qWait(10)
    pytest.fail('The generated reader did not settle')


@pytest.fixture
def reader(qapp, tmp_path):
    source = tmp_path / 'generated-spreads.pdf'
    with pymupdf.open() as doc:
        for i in range(6):
            page = doc.new_page(width=360, height=520)
            page.insert_text((50, 80), f'Find target {i + 1}')
            if i == 1:
                page.add_text_annot((120, 120), 'Right page note')
        doc.save(source)
    window = MainWindow()
    window.resize(1400, 900)
    window.act_pages.setChecked(False)
    window.act_tools.setChecked(False)
    window.ws.load_first_or_merge(window.store.load(str(source)), str(source))
    window.refresh()
    window.show()
    settle(qapp, window)
    window._set_reading_layout('spread')
    settle(qapp, window)
    assert window.preview_view.spread
    yield window
    window.ws.reset()
    window.close()
    qapp.processEvents()


def test_spread_order_tail_and_mixed_sizes(qapp):
    view = PreviewView()
    sizes = [(360, 520), (520, 360), (360, 700), (300, 800), (500, 320), (360, 520), (900, 520)]
    view.set_pages(tuple(range(7)), sizes, spread=True)
    left, right, next_left, next_right, last_left, last_right, tail = view.page_rects
    assert left.top() == right.top()
    assert left.right() < right.left()
    assert next_left.top() == next_right.top() > max(left.bottom(), right.bottom())
    assert last_left.top() == last_right.top() > max(next_left.bottom(), next_right.bottom())
    assert tail.top() > max(last_left.bottom(), last_right.bottom())
    assert tail.right() < view.spread_rect(6).center().x()
    assert view.sceneRect().contains(view.spread_rect(6))
    # 左右页底边无序时，短页下方不能遮蔽同排的长页。
    area = QRectF(0, next_left.top() + 740, view.content_width(), 40)
    assert list(view.visible_pages(area)) == [3]
    for index, rect in enumerate(view.page_rects):
        assert view.page_at_point(rect.center()) == index
    gutter = QPointF((left.right() + right.left()) / 2, left.top() + 40)
    assert view.page_at_point(gutter) == -1
    assert view.page_at_point(QPointF(next_left.center().x(), area.center().y())) == -1
    generation = view.layout_generation
    view.set_pages(tuple(range(7)), sizes)
    assert view.layout_generation == generation + 1
    assert all(a.bottom() < b.top() for a, b in zip(view.page_rects, view.page_rects[1:]))
    view.set_pages((), [], spread=True)
    assert view.page_at_point(QPointF()) == -1


@pytest.mark.parametrize('columns', [1, 2, 4])
def test_same_source_with_new_page_sizes_rebuilds_layout_and_drops_old_layers(qapp, columns):
    from PyQt6.QtGui import QImage, QPixmap
    view = PreviewView()
    identity = ((1, 0, 0), (1, 1, 0), (1, 2, 0))
    sizes = [(180, 120), (640, 360), (180, 240)]
    view.set_pages(identity, sizes, columns=columns)
    image = QImage(180, 120, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.red)
    view.set_photo(QPixmap.fromImage(image), 1, sizes[0])
    view.set_neighbor(1, image)
    view.set_detail(image, (0, 0), 1)
    generation = view.layout_generation
    updated = [(595, 842), sizes[1], sizes[2]]
    assert view.set_pages(identity, updated, columns=columns)
    assert view.layout_generation == generation + 1
    assert (view.page_rects[0].width(), view.page_rects[0].height()) == updated[0]
    assert view.photo().isNull() and view._detail.pixmap().isNull()
    assert not view.neighbors
    for i, rect in enumerate(view.page_rects):
        assert view.sceneRect().contains(rect)
        assert not any(rect.intersects(other) for other in view.page_rects[i + 1:])
    assert not view.set_pages(identity, updated, columns=columns)
    assert view.layout_generation == generation + 1
    view.close()


def test_long_book_lookup_does_not_scan_all_pages(qapp):
    view = PreviewView()
    view.set_pages(tuple(range(10000)), [(300, 400)] * 10000, spread=True)
    left, right = view.page_rects[5000:5002]

    class IndexedOnly(list):
        def __iter__(self):
            raise AssertionError('Page lookup must inspect only the matching spread')

    view.page_rects = IndexedOnly(view.page_rects)
    view.active_page = 5001
    assert view.page_at_y(right.center().y()) == 5001
    assert view.page_at_point(right.center()) == 5001
    assert list(view.visible_pages(left.united(right))) == [5000, 5001]


@pytest.mark.parametrize('count', (1, 2, 3, 4, 5, 6, 7))
def test_spreads_pair_from_the_first_page_for_any_document_length(qapp, count):
    view = PreviewView()
    view.set_pages(tuple(range(count)), [(360, 520)] * count, spread=True)
    for index in range(count):
        start = index // 2 * 2
        assert list(view.pages_in_spread(index)) == list(range(start, min(start + 2, count)))
    if count == 1:
        assert view.page_rects[0].center() == view.sceneRect().center()


@pytest.mark.parametrize('sizes', (
    [(360, 520), (360, 520)],
    [(520, 360), (300, 700)],
))
def test_two_page_document_forms_one_left_to_right_spread(qapp, sizes):
    view = PreviewView()
    view.set_pages((0, 1), sizes, spread=True)
    first, second = view.page_rects
    assert first.top() == second.top()
    assert first.right() + 12 == pytest.approx(second.left())
    assert list(view.pages_in_spread(0)) == list(view.pages_in_spread(1)) == [0, 1]
    assert view.spread_rect(0) == first.united(second)
    for index, rect in enumerate(view.page_rects):
        assert view.page_at_point(rect.center()) == index


def test_two_page_reader_fits_both_pages_and_keeps_them_paired(reader, qapp, tmp_path):
    source = tmp_path / 'generated-two-pages.pdf'
    with pymupdf.open() as doc:
        for index in range(2):
            page = doc.new_page(width=360, height=520)
            page.insert_text((40, 70), f'Two-page target {index + 1}')
        doc.save(source)
    reader.ws.reset()
    reader.ws.load_first_or_merge(reader.store.load(str(source)), str(source))
    reader.refresh()
    settle(qapp, reader)
    view = reader.preview_view
    for mode in ('page', 'width', 'custom'):
        reader._set_zoom_mode(mode)
        reader.go_to_page(2)
        settle(qapp, reader)
        first, second = view.page_rects
        assert first.top() == second.top() and first.right() < second.left()
        assert reader.page_number.value() == 1 and reader.page_total.text() == '–2 / 2 页'
        assert not reader.previous_page.isEnabled() and not reader.next_page.isEnabled()
        assert view.page_at_point(second.center()) == 1
        if mode == 'page':
            viewport = view.viewport().rect().adjusted(8, 8, -8, -8)
            assert viewport.contains(view.mapFromScene(first.united(second)).boundingRect())
            assert list(view.visible_pages(view.mapToScene(view.viewport().rect()).boundingRect())) == [0, 1]
    assert not reader.ws.dirty


def test_navigation_and_layout_switch_keep_the_reading_position(reader, qapp):
    reader.go_to_page(3)
    settle(qapp, reader)
    assert reader.ws.preview_index() == 2
    assert reader.page_number.value() == 3
    assert reader.page_total.text() == '–4 / 6 页'
    reader.preview_view.setFocus()
    QTest.keyClick(reader.preview_view, Qt.Key.Key_PageDown)
    assert reader.ws.preview_index() == 4
    reader.step_page(1)
    assert reader.ws.preview_index() == 4
    assert not reader.next_page.isEnabled()
    reader.step_page(-1)
    assert reader.ws.preview_index() == 2
    reader.go_to_page(1)
    assert not reader.previous_page.isEnabled()
    reader.step_page(1)
    assert reader.ws.preview_index() == 2
    # 放大后在右页中部阅读，切换排布和激活同组另一页均不跳回页顶。
    reader.go_to_page(4)
    reader._set_zoom(2)
    view = reader.preview_view
    view.centerOn(view.page_rects[3].topLeft() + QPointF(180, 260))
    settle(qapp, reader)
    before = view.mapToScene(view.viewport().rect().center()) - view.page_rects[3].topLeft()
    reader._set_reading_layout('single')
    settle(qapp, reader)
    after = view.mapToScene(view.viewport().rect().center()) - view.page_rects[3].topLeft()
    assert reader.ws.preview_index() == 3
    assert after.y() == pytest.approx(before.y(), abs=2)
    assert reader._zoom == 2
    reader._set_reading_layout('spread')
    settle(qapp, reader)
    position = view.mapToScene(view.viewport().rect().center())
    reader.go_to_page(3, reveal=False)
    assert (view.mapToScene(view.viewport().rect().center()) - position).manhattanLength() < 2
    assert not reader.ws.dirty


@pytest.mark.parametrize("layout", ["spread", "four"])
@pytest.mark.parametrize("previous_zoom", ["width", "custom"])
def test_choosing_multi_page_reading_fits_the_current_group(reader, qapp, layout, previous_zoom):
    reader.go_to_page(4)
    reader._set_zoom(2)
    if previous_zoom == "width":
        reader._set_zoom_mode("width")
    pages = reader.ws.pages
    reader.reading_layout_actions[layout].trigger()
    settle(qapp, reader)
    assert reader._zoom_mode == "page"
    assert reader.reading_zoom_actions["page"].isChecked()
    assert reader.ws.preview_index() == 3
    assert reader.ws.pages is pages and not reader.ws.dirty
    view = reader.preview_view
    visible = view.mapToScene(view.viewport().rect()).boundingRect()
    assert visible.contains(view.spread_rect(3))


def test_multi_page_fit_is_applied_while_a_narrow_reader_falls_back(reader, qapp):
    reader.resize(980, 720)
    reader.act_pages.setChecked(True)
    reader.act_tools.setChecked(True)
    settle(qapp, reader)
    reader._set_zoom(2)
    reader.reading_layout_actions["four"].trigger()
    settle(qapp, reader)
    assert reader._zoom_mode == "page" and reader.preview_view.page_columns == 1
    reader.act_pages.setChecked(False)
    reader.act_tools.setChecked(False)
    reader.resize(1400, 900)
    settle(qapp, reader)
    assert reader._zoom_mode == "page" and reader.preview_view.page_columns == 4


def test_narrow_reader_falls_back_then_restores_without_toolbar_overflow(reader, qapp):
    reader.go_to_page(3)
    reader.resize(980, 720)
    reader.act_tools.setChecked(True)
    reader.act_pages.setChecked(True)
    settle(qapp, reader)
    assert not reader.preview_view.spread
    assert reader.reading_layout_actions['spread'].isChecked()
    assert reader.reading_fallback_notice.isVisible()
    for widget in (reader.page_number, reader.zoom_label, reader.previous_page, reader.next_page):
        bounds = QRect(widget.mapTo(reader.navigation_bar, QPoint()), widget.size())
        assert reader.navigation_bar.rect().contains(bounds)
    reader.act_tools.setChecked(False)
    reader.act_pages.setChecked(False)
    reader.resize(1400, 900)
    settle(qapp, reader)
    assert reader.preview_view.spread
    assert reader.ws.preview_index() == 2
    assert not reader.reading_fallback_notice.isVisible()


def test_right_page_search_and_annotation_hover_use_the_correct_page(reader, qapp):
    results = []
    for page in (1, 3):
        areas, _ = reader.store.search_page(reader.ws.pages[page], 'Find target')
        results.extend((page, area) for area in areas)
    reader.search_bar.results = results
    for result in (0, 1, 0):
        reader.search_bar.jump(result)
        settle(qapp, reader)
        index, points = results[result]
        assert reader.ws.preview_index() == index
        origin = reader.preview_view.page_rects[index].topLeft()
        point = origin + QPointF(*points[0])
        assert reader.preview_view.viewport().rect().contains(reader.preview_view.mapFromScene(point))
    view = reader.preview_view
    point = view.mapFromScene(view.page_rects[1].topLeft() + QPointF(126, 126))
    QTest.mouseMove(view.viewport(), point)
    qapp.processEvents()
    assert reader.annotation_tools.snapshot_index == 1
    assert reader.annotation_tools.hover_bar.isVisible()
    assert not reader.ws.dirty


def test_both_visible_pages_get_high_resolution_tiles(reader, qapp):
    reader.go_to_page(2)
    reader._set_zoom(8)
    view = reader.preview_view
    left, right = view.page_rects[:2]
    view.centerOn((left.right() + right.left()) / 2, left.top() + 120)
    settle(qapp, reader)
    for index in (0, 1):
        item = view.detail_item(index)
        assert item is not None and not item.pixmap().isNull()
        assert view.page_rects[index].intersects(item.sceneBoundingRect())
    assert not reader._render_busy_message()


@pytest.mark.parametrize('number', (1, 2, 5, 6))
def test_fit_spread_keeps_first_and_last_pairs_inside_padded_viewport(reader, qapp, number):
    reader.go_to_page(number)
    settle(qapp, reader)
    view = reader.preview_view
    visible = view.mapToScene(view.viewport().rect()).boundingRect()
    pages = view.pages_in_spread(number - 1)
    assert list(view.visible_pages(visible)) == list(pages)
    for index in pages:
        paper = view.mapFromScene(view.page_rects[index]).boundingRect()
        assert view.viewport().rect().adjusted(8, 8, -8, -8).contains(paper)
    center = view.mapFromScene(view.spread_rect(number - 1).center())
    assert abs(center.y() - view.viewport().rect().center().y()) <= 2
    assert not view.horizontalScrollBar().isVisible()


def test_continuous_spread_scroll_keeps_scale_and_position_across_mixed_sizes(reader, qapp, tmp_path):
    source = tmp_path / 'generated-mixed-spreads.pdf'
    with pymupdf.open() as doc:
        for width, height in [(360, 520)] * 2 + [(520, 360)] * 2 + [(300, 420)] * 2:
            doc.new_page(width=width, height=height)
        doc.save(source)
    reader.ws.reset()
    reader.ws.load_first_or_merge(reader.store.load(str(source)), str(source))
    reader.refresh()
    reader._set_zoom_mode('width')
    settle(qapp, reader)
    view = reader.preview_view
    factor = view.transform().m11()
    assert factor == pytest.approx((view.viewport().width() - 24) / view.content_width())
    bounds = QRectF(view.sceneRect())
    boundary = view.page_rects[2].top()
    for probe, index in ((boundary + 8, 2), (boundary - 24, 0), (boundary + 8, 2)):
        height = view.viewport().height() / factor
        view.centerOn(bounds.center().x(), probe + height / 2 - min(height / 4, 100))
        position = view.verticalScrollBar().value()
        reader._reading_scrolled()
        settle(qapp, reader)
        assert reader.ws.preview_index() == index
        assert view.transform().m11() == pytest.approx(factor)
        assert view.sceneRect() == bounds
        assert view.verticalScrollBar().value() == pytest.approx(position, abs=1)
    assert not reader.ws.dirty


def test_landscape_fit_hides_adjacent_spreads_and_wheel_turns_whole_groups(reader, qapp, tmp_path):
    source = tmp_path / 'generated-landscape.pdf'
    with pymupdf.open() as doc:
        for i in range(5):
            page = doc.new_page(width=842, height=595)
            page.insert_text((40, 70), f'Landscape page {i + 1}')
        doc.save(source)
    reader.ws.reset()
    reader.ws.load_first_or_merge(reader.store.load(str(source)), str(source))
    reader.refresh()
    reader.go_to_page(2)
    settle(qapp, reader)
    view = reader.preview_view
    visible = view.mapToScene(view.viewport().rect()).boundingRect()
    assert list(view.visible_pages(visible)) == [0, 1]
    assert all(not item.isVisible() for index, item in view.neighbors.items() if index not in (0, 1))
    assert view.page_at_point(view.page_rects[2].center()) == -1
    assert not view.horizontalScrollBar().isVisible()

    def wheel(delta):
        position = view.viewport().rect().center()
        event = QWheelEvent(QPointF(position), QPointF(view.viewport().mapToGlobal(position)),
                            QPoint(), QPoint(0, delta), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(view.viewport(), event)
        settle(qapp, reader)

    wheel(-120)
    assert reader.ws.preview_index() == 2
    wheel(-120)
    assert reader.ws.preview_index() == 4
    assert list(view.visible_pages(view.mapToScene(view.viewport().rect()).boundingRect())) == [4]
    paper = view.mapFromScene(view.page_rects[4]).boundingRect()
    assert abs(paper.center().y() - view.viewport().rect().center().y()) <= 2
    assert view.viewport().rect().adjusted(8, 8, -8, -8).contains(paper)
    wheel(120)
    assert reader.ws.preview_index() == 2
    reader.go_to_page(2)
    reader._set_zoom_mode('width')
    settle(qapp, reader)
    visible = view.mapToScene(view.viewport().rect()).boundingRect()
    assert any(index not in (0, 1) for index in view.visible_pages(visible))


def test_four_page_grid_preserves_order_hit_testing_and_empty_slots(qapp):
    view = PreviewView()
    sizes = [(300, 420), (420, 300), (300, 420), (300, 420), (300, 420), (420, 300)]
    view.set_pages(tuple(range(6)), sizes, columns=4)
    assert view.page_columns == 4
    first, second, third, fourth, fifth, sixth = view.page_rects
    assert first.right() < second.left() and third.right() < fourth.left()
    assert max(first.bottom(), second.bottom()) < min(third.top(), fourth.top())
    assert fifth.top() > fourth.bottom() and fifth.right() < sixth.left()
    assert list(view.pages_in_spread(3)) == [0, 1, 2, 3]
    assert list(view.pages_in_spread(5)) == [4, 5]
    for index, rect in enumerate(view.page_rects):
        assert view.page_at_point(rect.center()) == index
    blank = QPointF(fifth.center().x(), view.spread_rect(5).bottom() - 20)
    assert view.page_at_point(blank) == -1
    view.set_pages(tuple(range(6)), sizes, spread=True)
    assert view.page_columns == 2 and list(view.pages_in_spread(2)) == [2, 3]


def test_layout_icon_and_shortcuts_switch_single_spread_and_four_pages(reader, qapp):
    button = reader.reading_orb
    assert not button.text() and button.width() == 42
    assert button.parentWidget() is reader.preview_view
    assert [action.shortcut().toString() for action in reader.reading_layout_actions.values()] == ['Ctrl+1', 'Ctrl+2', 'Ctrl+4']
    reader.preview_view.setFocus()
    QTest.keyClick(reader.preview_view, Qt.Key.Key_4, Qt.KeyboardModifier.ControlModifier)
    settle(qapp, reader)
    view = reader.preview_view
    assert view.page_columns == 4 and reader.reading_layout_actions['four'].isChecked()
    visible = view.mapToScene(view.viewport().rect()).boundingRect()
    assert list(view.visible_pages(visible)) == [0, 1, 2, 3]
    reader.step_page(1)
    settle(qapp, reader)
    assert reader.ws.preview_index() == 4 and reader.page_number.value() == 5
    assert not reader.next_page.isEnabled()
    for key, columns in ((Qt.Key.Key_2, 2), (Qt.Key.Key_1, 1), (Qt.Key.Key_4, 4)):
        QTest.keyClick(view, key, Qt.KeyboardModifier.ControlModifier)
        settle(qapp, reader)
        assert view.page_columns == columns and reader.ws.preview_index() == 4
    reader.resize(980, 720)
    reader.act_pages.setChecked(True)
    reader.act_tools.setChecked(True)
    settle(qapp, reader)
    assert view.page_columns == 1 and reader.reading_layout_actions['four'].isChecked()
    assert reader.reading_fallback_notice.isVisible()
    reader.act_pages.setChecked(False)
    reader.act_tools.setChecked(False)
    reader.resize(1400, 900)
    settle(qapp, reader)
    assert view.page_columns == 4 and reader.ws.preview_index() == 4
    assert not reader.ws.dirty


def test_reading_orb_peeks_reveals_and_opens_the_shared_menu(reader, qapp):
    orb, view = reader.reading_orb, reader.preview_view
    QTest.mouseMove(view.viewport(), QPoint(30, 30))
    QTest.mouseMove(view.viewport(), QPoint(20, 20))
    QTest.qWait(800)
    assert not orb.expanded and orb.icon().isNull()
    assert orb.x() == view.viewport().geometry().right() + 1 - orb.PEEK
    assert orb.mask().contains(QPoint(8, 21))
    assert not orb.mask().contains(QPoint(orb.PEEK + 1, 21))
    QTest.mouseMove(view.viewport(), QPoint(view.viewport().width() - 40, view.viewport().height() - 50))
    QTest.qWait(220)
    assert orb.expanded and not orb.icon().isNull()
    assert view.viewport().geometry().contains(orb.geometry())
    QTest.mouseClick(orb, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    menu = reader.reading_settings_menu
    assert menu.isVisible()
    viewport_bounds = QRect(view.viewport().mapToGlobal(QPoint()), view.viewport().size())
    assert viewport_bounds.contains(menu.frameGeometry())
    assert menu.frameGeometry().bottom() < orb.mapToGlobal(QPoint()).y()
    assert all(action in menu.actions() for action in reader.reading_layout_actions.values())
    assert all(action in menu.actions() for action in reader.reading_zoom_actions.values())
    reader.reading_layout_actions['four'].trigger()
    menu.hide()
    settle(qapp, reader)
    assert view.page_columns == 4
    assert not reader.ws.dirty


def test_temporarily_hidden_orb_keeps_menu_and_shortcuts_usable(reader, qapp):
    orb = reader.reading_orb
    orb.hide_action.trigger()
    assert orb.temporarily_hidden and orb.isHidden()
    QTest.mouseMove(reader.preview_view.viewport(), QPoint(reader.preview_view.viewport().width() - 30,
                                                         reader.preview_view.viewport().height() - 50))
    QTest.qWait(200)
    reader.reading_layout_actions['four'].trigger()
    reader.reading_zoom_actions['width'].trigger()
    settle(qapp, reader)
    assert orb.isHidden() and reader.preview_view.page_columns == 4
    assert reader._zoom_mode == 'width'
    orb.restore_action.trigger()
    qapp.processEvents()
    assert not orb.temporarily_hidden and orb.isVisible()


def test_new_window_restores_the_orb_default_without_persisting_the_hide_choice(reader, qapp):
    reader.reading_orb.hide_action.trigger()
    other = MainWindow()
    try:
        assert not other.reading_orb.temporarily_hidden
    finally:
        other.close()
        qapp.processEvents()


def test_orb_tracks_the_reader_corner_and_hides_with_the_document(reader, qapp):
    orb = reader.reading_orb
    orb.reveal()
    QTest.qWait(220)
    reader.resize(980, 720)
    settle(qapp, reader)
    QTest.qWait(200)
    assert orb.pos() == orb._target()
    reader.stack.setCurrentIndex(1)
    qapp.processEvents()
    assert not orb.isVisible()
    orb.restore_action.trigger()
    settle(qapp, reader)
    assert reader.stack.currentIndex() == 0 and orb.isVisible()
    reader.ws.reset()
    reader.refresh()
    qapp.processEvents()
    assert orb.isHidden() and not orb.restore_action.isEnabled()


def test_orb_position_does_not_follow_vertical_or_horizontal_document_scrolling(reader, qapp):
    reader._set_reading_layout('single')
    reader._set_zoom(4)
    settle(qapp, reader)
    orb, view = reader.reading_orb, reader.preview_view
    orb.reveal()
    QTest.qWait(220)
    orb.hide_timer.stop()
    fixed = orb.mapTo(view, QPoint())
    for bar in (view.verticalScrollBar(), view.horizontalScrollBar()):
        assert bar.maximum() > bar.minimum()
        bar.setValue(bar.minimum())
        qapp.processEvents()
        assert orb.mapTo(view, QPoint()) == fixed
        bar.setValue(min(bar.minimum() + 140, bar.maximum()))
        qapp.processEvents()
        assert orb.mapTo(view, QPoint()) == fixed


def test_repeated_proximity_does_not_restart_the_reveal_animation(reader, qapp):
    orb = reader.reading_orb
    QTest.mouseMove(reader.preview_view.viewport(), QPoint(20, 20))
    orb.hide_timer.stop()
    orb._motion_duration = 160
    orb.expanded = False
    orb._move(False)
    orb.reveal()
    QTest.qWait(60)
    progress = orb.animation.currentTime()
    orb.reveal()
    assert orb.animation.currentTime() >= progress > 0
    QTest.qWait(120)
    assert orb.pos() == orb._target()
