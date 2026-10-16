"""Book spreads and reader interactions; all documents are generated here."""
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
            if i == 2:
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


def test_book_order_cover_tail_and_mixed_sizes(qapp):
    view = PreviewView()
    sizes = [(360, 520), (520, 360), (360, 700), (300, 800), (500, 320), (900, 520)]
    view.set_pages(tuple(range(6)), sizes, spread=True)
    cover, left, right, next_left, next_right, tail = view.page_rects
    assert cover.center().x() == pytest.approx(view.sceneRect().center().x())
    assert left.top() == right.top() > cover.bottom()
    assert left.right() < right.left()
    assert next_left.top() == next_right.top() > right.bottom()
    assert tail.top() > next_left.bottom()
    assert tail.right() < view.spread_rect(5).center().x()
    assert view.sceneRect().contains(view.spread_rect(5))
    # 左右页底边无序时，短页下方不能遮蔽同排的长页。
    area = QRectF(0, next_left.top() + 600, view.content_width(), 40)
    assert list(view.visible_pages(area)) == [3]
    for index, rect in enumerate(view.page_rects):
        assert view.page_at_point(rect.center()) == index
    gutter = QPointF((left.right() + right.left()) / 2, left.top() + 40)
    assert view.page_at_point(gutter) == -1
    assert view.page_at_point(QPointF(next_right.center().x(), area.center().y())) == -1
    generation = view.layout_generation
    view.set_pages(tuple(range(6)), sizes)
    assert view.layout_generation == generation + 1
    assert all(a.bottom() < b.top() for a, b in zip(view.page_rects, view.page_rects[1:]))
    view.set_pages((), [], spread=True)
    assert view.page_at_point(QPointF()) == -1


def test_long_book_lookup_does_not_scan_all_pages(qapp):
    view = PreviewView()
    view.set_pages(tuple(range(10000)), [(300, 400)] * 10000, spread=True)
    left, right = view.page_rects[5001:5003]

    class IndexedOnly(list):
        def __iter__(self):
            raise AssertionError('Page lookup must inspect only the matching spread')

    view.page_rects = IndexedOnly(view.page_rects)
    view.active_page = 5002
    assert view.page_at_y(right.center().y()) == 5002
    assert view.page_at_point(right.center()) == 5002
    assert list(view.visible_pages(left.united(right))) == [5001, 5002]


def test_navigation_and_layout_switch_keep_the_reading_position(reader, qapp):
    reader.go_to_page(3)
    settle(qapp, reader)
    assert reader.ws.preview_index() == 2
    assert reader.page_number.value() == 2
    assert reader.page_total.text() == '–3 / 6 页'
    reader.preview_view.setFocus()
    QTest.keyClick(reader.preview_view, Qt.Key.Key_PageDown)
    assert reader.ws.preview_index() == 3
    reader.step_page(1)
    assert reader.ws.preview_index() == 5
    assert not reader.next_page.isEnabled()
    reader.step_page(-1)
    assert reader.ws.preview_index() == 3
    reader.go_to_page(1)
    assert not reader.previous_page.isEnabled()
    reader.step_page(1)
    assert reader.ws.preview_index() == 1
    # 放大后在右页中部阅读，切换排布和激活同组另一页均不跳回页顶。
    reader.go_to_page(3)
    reader._set_zoom(2)
    view = reader.preview_view
    view.centerOn(view.page_rects[2].topLeft() + QPointF(180, 260))
    settle(qapp, reader)
    before = view.mapToScene(view.viewport().rect().center()) - view.page_rects[2].topLeft()
    reader._set_reading_layout('single')
    settle(qapp, reader)
    after = view.mapToScene(view.viewport().rect().center()) - view.page_rects[2].topLeft()
    assert reader.ws.preview_index() == 2
    assert after.y() == pytest.approx(before.y(), abs=2)
    assert reader._zoom == 2
    reader._set_reading_layout('spread')
    settle(qapp, reader)
    position = view.mapToScene(view.viewport().rect().center())
    reader.go_to_page(2, reveal=False)
    assert (view.mapToScene(view.viewport().rect().center()) - position).manhattanLength() < 2
    assert not reader.ws.dirty


def test_narrow_reader_falls_back_then_restores_without_toolbar_overflow(reader, qapp):
    reader.go_to_page(3)
    reader.resize(980, 720)
    reader.act_tools.setChecked(True)
    reader.act_pages.setChecked(True)
    settle(qapp, reader)
    assert not reader.preview_view.spread
    assert reader.reading_layout_actions['spread'].isChecked()
    assert reader.layout_hint.isVisible()
    for widget in (reader.page_number, reader.zoom_mode, reader.reading_layout_button):
        bounds = QRect(widget.mapTo(reader.navigation_bar, QPoint()), widget.size())
        assert reader.navigation_bar.rect().contains(bounds)
    reader.act_tools.setChecked(False)
    reader.act_pages.setChecked(False)
    reader.resize(1400, 900)
    settle(qapp, reader)
    assert reader.preview_view.spread
    assert reader.ws.preview_index() == 2
    assert reader.layout_hint.isHidden()


def test_right_page_search_and_annotation_hover_use_the_correct_page(reader, qapp):
    results = []
    for page in (2, 4):
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
    point = view.mapFromScene(view.page_rects[2].topLeft() + QPointF(126, 126))
    QTest.mouseMove(view.viewport(), point)
    qapp.processEvents()
    assert reader.annotation_tools.snapshot_index == 2
    assert reader.annotation_tools.hover_bar.isVisible()
    assert not reader.ws.dirty


def test_both_visible_pages_get_high_resolution_tiles(reader, qapp):
    reader.go_to_page(2)
    reader._set_zoom(8)
    view = reader.preview_view
    left, right = view.page_rects[1:3]
    view.centerOn((left.right() + right.left()) / 2, left.top() + 120)
    settle(qapp, reader)
    for index in (1, 2):
        item = view.detail_item(index)
        assert item is not None and not item.pixmap().isNull()
        assert view.page_rects[index].intersects(item.sceneBoundingRect())
    assert not reader._render_busy_message()


@pytest.mark.parametrize('number', (1, 2, 6))
def test_fit_spread_keeps_cover_and_tail_inside_padded_viewport(reader, qapp, number):
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


def test_landscape_fit_hides_adjacent_spreads_and_wheel_turns_whole_groups(reader, qapp, tmp_path):
    source = tmp_path / 'generated-landscape.pdf'
    with pymupdf.open() as doc:
        for i in range(6):
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
    assert list(view.visible_pages(visible)) == [1, 2]
    assert all(not item.isVisible() for index, item in view.neighbors.items() if index not in (1, 2))
    assert view.page_at_point(view.page_rects[0].center()) == -1
    assert not view.horizontalScrollBar().isVisible()

    def wheel(delta):
        position = view.viewport().rect().center()
        event = QWheelEvent(QPointF(position), QPointF(view.viewport().mapToGlobal(position)),
                            QPoint(), QPoint(0, delta), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(view.viewport(), event)
        settle(qapp, reader)

    wheel(-120)
    assert reader.ws.preview_index() == 3
    wheel(-120)
    assert reader.ws.preview_index() == 5
    assert list(view.visible_pages(view.mapToScene(view.viewport().rect()).boundingRect())) == [5]
    paper = view.mapFromScene(view.page_rects[5]).boundingRect()
    assert abs(paper.center().y() - view.viewport().rect().center().y()) <= 2
    assert view.viewport().rect().adjusted(8, 8, -8, -8).contains(paper)
    wheel(120)
    assert reader.ws.preview_index() == 3
    reader.go_to_page(2)
    reader._set_zoom_mode('width')
    settle(qapp, reader)
    visible = view.mapToScene(view.viewport().rect()).boundingRect()
    assert any(index not in (1, 2) for index in view.visible_pages(visible))


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
    assert view.page_columns == 2 and list(view.pages_in_spread(2)) == [1, 2]


def test_layout_icon_and_shortcuts_switch_single_spread_and_four_pages(reader, qapp):
    button = reader.reading_layout_button
    assert not button.text() and button.width() == 30
    assert button.mapTo(reader.navigation_bar, QPoint()).x() > reader.navigation_bar.width() / 2
    assert [action.shortcut().toString() for action in button.menu().actions()] == ['Ctrl+1', 'Ctrl+2', 'Ctrl+4']
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
    assert '四页' in reader.layout_hint.text()
    reader.act_pages.setChecked(False)
    reader.act_tools.setChecked(False)
    reader.resize(1400, 900)
    settle(qapp, reader)
    assert view.page_columns == 4 and reader.ws.preview_index() == 4
    assert not reader.ws.dirty
