"""Reading issue regressions using only generated pages and isolated settings."""
import json
import pymupdf as p
import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QFont, QFontDatabase, QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMenu

from app.ui import MainWindow
from app.workspace import OutlineEntry


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def reader(qapp, tmp_path):
    path = tmp_path / 'chapters.pdf'
    with p.open() as doc:
        for n in range(8):
            doc.new_page(width=360, height=520).insert_text((40, 60), f'Chapter page {n + 1}')
        doc.set_toc([[1, 'First', 2], [2, 'Section', 4], [1, 'Last', 7]])
        doc.save(path)
    window = MainWindow()
    window.resize(1000, 750)
    window.ws.load_first_or_merge(window.store.load(str(path)), str(path))
    window.refresh()
    window.show()
    QTest.qWait(80)
    yield window
    window.ws.reset()
    window.close()
    qapp.processEvents()


def test_no_passive_reading_tooltip(reader):
    assert not reader.preview_view.toolTip()
    assert not reader.preview_view.viewport().toolTip()
    assert 'Ctrl+C' in reader.preview_view.accessibleDescription()
    for mode in ('single', 'spread', 'four'):
        reader._set_reading_layout(mode)
        assert not reader.preview_view.toolTip()


def test_outline_follows_preceding_chapter_without_jumping(reader, monkeypatch):
    reader.open_outline()
    panel = reader.outline_panel
    jumps = []
    monkeypatch.setattr(reader, 'go_to_page', lambda number: jumps.append(number))
    for page, title in ((0, None), (2, 'First'), (4, 'Section'), (7, 'Last')):
        reader.ws.select_index(page)
        panel.sync_position(force=True)
        item = panel.tree.currentItem()
        assert (item.text(0) if item else None) == title
        assert reader.ws.preview_index() == page
    assert not jumps
    panel.jump(panel._items[0], 0)
    assert jumps == [2]
    panel._follow_timer.start()
    reader.ws.select_index(2)
    panel.sync_position()
    assert panel.tree.currentItem().text(0) == 'Last'
    panel._follow_timer.stop()
    panel.sync_position()
    assert panel.tree.currentItem().text(0) == 'First'
    reader.immersive.collapse(animate=False)
    reader.ws.select_index(7)
    reader.immersive.reveal()
    assert panel.tree.currentItem().text(0) == 'Last'


def test_outline_same_page_fallback_and_deleted_targets(reader):
    pages = reader.ws.pages
    reader.ws.outline = (OutlineEntry(1, 'First on page', pages[1].page_id),
                         OutlineEntry(2, 'Second on page', pages[1].page_id),
                         OutlineEntry(1, 'Deleted', -99))
    reader.open_outline()
    reader.ws.select_index(2)
    reader.outline_panel.sync_position(force=True)
    assert reader.outline_panel.tree.currentItem().text(0) == 'First on page'
    reader.ws.outline = ()
    reader.outline_panel.refresh()
    assert reader.outline_panel.tree.currentItem() is None


def nested_outline(reader):
    pages = reader.ws.pages
    reader.ws.outline = tuple(OutlineEntry(level, title, pages[page].page_id) for level, title, page in (
        (1, 'Chapter A', 0), (2, 'Section A', 1), (3, 'Topic A', 2), (4, 'Detail A', 3),
        (1, 'Chapter B', 4), (2, 'Section B', 5), (3, 'Topic B', 6)))
    reader.open_outline()
    return reader.outline_panel


def test_outline_can_expand_and_collapse_every_level_without_modifying_the_document(reader):
    panel = nested_outline(reader)
    before = (reader.ws.pages, reader.ws.outline, reader.ws.dirty, reader.ws.preview_index(),
              reader.ws.undo_stack.count())
    panel.expand_all_action.trigger()
    assert all(item.isExpanded() for item in panel._items if item.childCount())
    panel.collapse_all_action.trigger()
    assert all(not item.isExpanded() for item in panel._items if item.childCount())
    assert (reader.ws.pages, reader.ws.outline, reader.ws.dirty, reader.ws.preview_index(),
            reader.ws.undo_stack.count()) == before


def test_outline_following_respects_collapse_all_when_reading_or_reopening_the_sidebar(reader):
    panel = nested_outline(reader)
    reader.ws.select_index(3)
    panel.sync_position(force=True)
    panel.collapse_all_action.trigger()
    reader.immersive.collapse(animate=False)
    reader.immersive.reveal()
    reader.ws.select_index(6)
    panel.sync_position(force=True)
    assert all(not item.isExpanded() for item in panel._items if item.childCount())
    assert panel.tree.currentItem().text(0) == 'Chapter B'
    panel.expand_all_action.trigger()
    assert all(item.isExpanded() for item in panel._items if item.childCount())
    assert panel.tree.currentItem().text(0) == 'Topic B'


def test_outline_bulk_view_actions_are_available_in_read_only_menus_and_disabled_when_empty(reader, monkeypatch):
    panel = nested_outline(reader)
    menus = []
    monkeypatch.setattr(QMenu, 'exec', lambda menu, position: menus.append(menu))
    monkeypatch.setattr(reader.store, 'operation_reason', lambda *args: 'Generated read-only document')
    panel.menu(QPoint(0, 0))
    actions = {action.text(): action for action in menus[0].actions()}
    assert actions['展开全部'].isEnabled() and actions['折叠全部'].isEnabled()
    assert not actions['重命名'].isEnabled()
    reader.ws.outline = ()
    panel.refresh()
    assert not panel.expand_all_action.isEnabled() and not panel.collapse_all_action.isEnabled()


def test_outline_bulk_view_preserves_a_manually_selected_branch(reader):
    panel = nested_outline(reader)
    panel.tree.setCurrentItem(panel._items[6])
    before = reader.ws.preview_index()
    panel.expand_all_action.trigger()
    assert panel.tree.currentItem().text(0) == 'Topic B'
    panel.collapse_all_action.trigger()
    assert panel.tree.currentItem().text(0) == 'Chapter B'
    assert reader.ws.preview_index() == before


def test_overlay_reverses_without_resizing_reader_and_pin_restores_splitter(reader):
    control = reader.immersive
    bounds = reader.preview_view.geometry()
    control.reveal()
    QTest.qWait(45)
    position = control.panel.pos()
    control.collapse()
    assert control.panel.pos() == position
    QTest.qWait(20)
    position = control.panel.pos()
    control.reveal()
    assert control.panel.pos() == position
    QTest.qWait(240)
    assert control.panel.x() == 0
    assert reader.preview_view.geometry() == bounds
    control.auto_action.setChecked(False)
    assert reader.reader_split.widget(0) is reader.left_panel
    assert reader.left_panel.isVisible()
    control.auto_action.setChecked(True)
    assert reader.left_panel.parent() is control.overlay
    assert not control.overlay.isVisible()
    reader.ws.reset()
    reader.refresh()
    assert not control.edge.isVisible()
    assert not control.hide_timer.isActive()


@pytest.mark.parametrize('layout', ['single', 'spread', 'four'])
def test_fullscreen_restores_window_and_preserves_document(reader, qapp, layout):
    control = reader.immersive
    reader._set_reading_layout(layout)
    reader.go_to_page(5)
    QTest.qWait(80)
    before_pages = reader.ws.pages
    before_geometry = reader.geometry()
    QTest.keyClick(reader.preview_view, Qt.Key.Key_F11)
    qapp.processEvents()
    assert reader.isFullScreen()
    assert not reader.menuWidget().isVisible()
    assert not reader.workbench_bar.isVisible()
    assert control.exit_button.isVisible()
    control.reveal(keyboard=True)
    QTest.keyClick(reader, Qt.Key.Key_Escape)
    assert not control.expanded and control.fullscreen
    QTest.keyClick(reader, Qt.Key.Key_Escape)
    qapp.processEvents()
    assert not reader.isFullScreen()
    assert reader.menuWidget().isVisible()
    assert reader.workbench_bar.isVisible()
    assert reader.ws.pages is before_pages and not reader.ws.dirty
    assert reader.geometry() == before_geometry
    assert reader.ws.preview_index() == 4


def test_popup_and_keyboard_focus_keep_sidebar_open(reader):
    control = reader.immersive
    control.reveal(keyboard=True)
    control._hide_if_away()
    assert control.expanded
    menu = QMenu(reader)
    menu.addAction('Keep open')
    menu.popup(reader.mapToGlobal(QPoint(100, 100)))
    QApplication.processEvents()
    control._hide_if_away()
    assert control.expanded
    menu.close()
    reader.preview_view.setFocus()
    control.collapse(animate=False)
    assert not control.overlay.isVisible()


def test_overlay_thumbnail_width_is_ready_when_the_sidebar_opens(reader, qapp):
    control = reader.immersive
    control.collapse(animate=False)
    reader._sidebar_w = 96
    reader._apply_thumb_size()
    control.reveal()
    qapp.processEvents()
    assert reader._sidebar_w >= reader.left_view.width() * .75
    assert reader.left_view.iconSize().width() == reader._sidebar_w
    assert reader.left_view.horizontalScrollBar().maximum() == 0


def test_pressing_the_document_starts_hiding_the_sidebar_immediately(reader, qapp):
    control = reader.immersive
    control.reveal(keyboard=True)
    QTest.qWait(240)
    before = reader.preview_view.geometry()
    view = reader.preview_view.viewport()
    point = QPoint(view.width() - 35, view.height() // 2)
    QTest.mousePress(view, Qt.MouseButton.LeftButton, pos=point)
    try:
        assert not control.expanded
        assert not control.hide_timer.isActive()
    finally:
        QTest.mouseRelease(view, Qt.MouseButton.LeftButton, pos=point)
    QTest.qWait(240)
    assert not control.overlay.isVisible()
    assert reader.preview_view.geometry() == before


def test_mouse_sidebar_focus_does_not_keep_it_open_after_the_pointer_leaves(reader, monkeypatch):
    control = reader.immersive
    control.reveal()
    reader.sidebar_mode.setFocus(Qt.FocusReason.MouseFocusReason)
    outside = reader.preview_view.mapToGlobal(QPoint(reader.preview_view.width() - 20, 20))
    monkeypatch.setattr('app.immersivereading.QCursor.pos', lambda: outside)
    monkeypatch.setattr('app.immersivereading.QApplication.mouseButtons', lambda: Qt.MouseButton.NoButton)
    control._hide_if_away()
    assert not control.expanded


def test_mouse_sidebar_button_does_not_hold_keyboard_focus(reader, monkeypatch):
    control = reader.immersive
    control.collapse(animate=False)
    QTest.mouseClick(control.edge, Qt.MouseButton.LeftButton)
    assert control.expanded
    outside = reader.preview_view.mapToGlobal(QPoint(reader.preview_view.width() - 20, 20))
    monkeypatch.setattr('app.immersivereading.QCursor.pos', lambda: outside)
    monkeypatch.setattr('app.immersivereading.QApplication.mouseButtons', lambda: Qt.MouseButton.NoButton)
    control._hide_if_away()
    assert not control.expanded


def test_keyboard_sidebar_button_keeps_keyboard_access(reader, monkeypatch):
    control = reader.immersive
    control.collapse(animate=False)
    control.edge.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(control.edge, Qt.Key.Key_Space)
    assert control.expanded and control._keyboard_reveal
    outside = reader.preview_view.mapToGlobal(QPoint(reader.preview_view.width() - 20, 20))
    monkeypatch.setattr('app.immersivereading.QCursor.pos', lambda: outside)
    control._hide_if_away()
    assert control.expanded


def test_sidebar_handle_and_pin_use_compact_clear_states(reader, qapp):
    control = reader.immersive
    assert not control.edge.icon().isNull() and not control.edge.text()
    assert control.edge.width() <= 32 and control.edge.height() <= 48
    control.collapse(animate=False)
    control.edge.click()
    assert control.expanded
    QTest.qWait(240)
    assert control.edge.x() == control.overlay.width()
    assert control.pin.parent() is reader.sidebar_mode.parent()
    control.edge.click()
    assert not control.expanded
    control.pin.click()
    qapp.processEvents()
    assert not control.auto_action.isChecked() and control.pin.isChecked()
    assert reader.reader_split.widget(0) is reader.left_panel
    assert reader.left_panel.width() >= 138
    control.pin.click()
    assert control.auto_action.isChecked() and not control.pin.isChecked()


def test_sidebar_respects_reduced_motion_and_switching_back_to_thumbnails(reader, qapp, monkeypatch):
    control = reader.immersive
    monkeypatch.setattr(control, '_animation_duration', lambda: 0)
    control.reveal()
    assert control.panel.x() == 0
    assert control.animation.state() != control.animation.State.Running
    reader.sidebar_mode.setCurrentIndex(1)
    reader.sidebar_mode.setCurrentIndex(0)
    qapp.processEvents()
    assert reader._sidebar_w >= reader.left_view.width() * .75
    control.collapse()
    assert not control.overlay.isVisible()


@pytest.mark.parametrize('theme', ['forest', 'night'])
def test_sidebar_real_widget_preview(reader, qapp, tmp_path, theme):
    font_ids = [QFontDatabase.addApplicationFont(path) for path in (
        'C:/Windows/Fonts/segoeui.ttf', 'C:/Windows/Fonts/msyh.ttc')]
    before_font = qapp.font()
    qapp.setFont(QFont('Microsoft YaHei UI', 10))
    reader.change_theme(theme)
    control = reader.immersive
    control.reveal()
    QTest.qWait(300)
    reader.grab().save(str(tmp_path / f'sidebar-expanded-{theme}.png'))
    assert reader._sidebar_w >= reader.left_view.width() * .75
    control.collapse()
    QTest.qWait(200)
    reader.grab().save(str(tmp_path / f'sidebar-collapsed-{theme}.png'))
    control.edge.grab().save(str(tmp_path / f'sidebar-handle-{theme}.png'))
    assert not control.overlay.isVisible()
    qapp.setFont(before_font)
    for font_id in font_ids:
        if font_id >= 0:
            QFontDatabase.removeApplicationFont(font_id)


def test_native_window_click_and_alt_pan_are_not_blocked_by_sidebar_hiding(reader, qapp):
    control = reader.immersive
    control.reveal(keyboard=True)
    QTest.qWait(240)
    view = reader.preview_view.viewport()
    point = QPoint(view.width() - 35, view.height() // 2)
    global_point = view.mapToGlobal(point)
    event = QMouseEvent(QEvent.Type.MouseButtonPress,
        QPointF(reader.mapFromGlobal(global_point)), QPointF(global_point),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    qapp.sendEvent(reader.windowHandle(), event)
    assert not control.expanded
    QTest.mouseRelease(view, Qt.MouseButton.LeftButton, pos=point)
    control.reveal(keyboard=True)
    QTest.qWait(240)
    QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.AltModifier, point)
    assert not control.expanded and reader.reader_interaction.origin is not None
    QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.AltModifier, point)
    assert reader.reader_interaction.origin is None


def test_sidebar_motion_moves_only_the_panel_and_keeps_the_handle_attached(reader, monkeypatch, tmp_path):
    control = reader.immersive
    monkeypatch.setattr(control, '_animation_duration', lambda: 200)
    control.collapse(animate=False)
    bounds = reader.preview_view.geometry()
    frames = []
    for expanding in (True, False):
        (control.reveal if expanding else control.collapse)()
        control.hide_timer.stop()
        positions = [control.panel.x()]
        for _ in range(20):
            QTest.qWait(16)
            x = control.panel.x()
            positions.append(x)
            frames.append(dict(expanding=expanding, x=x, edge_x=control.edge.x()))
            assert reader.preview_view.geometry() == bounds
            assert control.edge.x() == max(0, x + control.overlay.width())
        assert positions == sorted(positions, reverse=not expanding)
    (tmp_path / 'sidebar-motion.json').write_text(json.dumps(frames), encoding='utf-8')


def test_f8_opens_again_after_clicking_the_document(reader):
    control = reader.immersive
    control.collapse(animate=False)
    assert not reader.act_pages.isChecked()
    QTest.keyClick(reader.preview_view, Qt.Key.Key_F8)
    assert control.expanded and reader.act_pages.isChecked()
    QTest.mouseClick(reader.preview_view.viewport(), Qt.MouseButton.LeftButton,
                     pos=QPoint(reader.preview_view.viewport().width() - 30, 30))
    assert not control.expanded and not reader.act_pages.isChecked()
    QTest.keyClick(reader.preview_view, Qt.Key.Key_F8)
    assert control.expanded and reader.act_pages.isChecked()


def test_fullscreen_tab_switch_keeps_chrome_hidden_and_refreshes_outline(reader, tmp_path, qapp):
    reader.open_outline()
    reader.immersive.fullscreen_action.setChecked(True)
    path = tmp_path / 'second.pdf'
    with p.open() as doc:
        doc.new_page()
        doc.set_toc([[1, 'Second document', 1]])
        doc.save(path)
    assert reader.open_files_in_tabs([str(path)])
    qapp.processEvents()
    assert len(reader._tabs) == 2
    assert not reader.tab_bar.isVisible()
    assert not reader.immersive.overlay.isVisible()
    reader.open_outline()
    assert reader.outline_panel.tree.topLevelItem(0).text(0) == 'Second document'
    reader.immersive.fullscreen_action.setChecked(False)
    assert reader.tab_bar.isVisible()
