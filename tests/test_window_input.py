import ctypes
import sys

import pytest
import pymupdf
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFileDialog, QToolButton

from app.ui import MainWindow


@pytest.fixture(scope='session')
def input_app():
    app = QApplication.instance() or QApplication([])
    if sys.platform == 'win32':
        from pathlib import Path
        QFontDatabase.addApplicationFont(str(Path('C:/Windows/Fonts/msyh.ttc')))
    return app


@pytest.fixture
def window(input_app):
    app = input_app
    win = MainWindow()
    win.resize(1100, 900)
    win.show()
    app.processEvents()
    yield win
    win.ws.reset()
    win.close()
    app.processEvents()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows native hit testing')
@pytest.mark.parametrize('scale', [1.0, 1.25, 1.5, 2.0])
@pytest.mark.parametrize('origin', [(0, 0), (-1920, -300), (1920, 200)])
def test_native_toolbar_points_are_client_area(window, monkeypatch, scale, origin):
    import ctypes.wintypes as wt
    width, height = window.width(), window.height()
    def screen_to_client(hwnd, point):
        point = ctypes.cast(point, ctypes.POINTER(wt.POINT)).contents
        point.x -= origin[0]
        point.y -= origin[1]
        return 1
    def client_rect(hwnd, rect):
        rect = ctypes.cast(rect, ctypes.POINTER(wt.RECT)).contents
        rect.left = rect.top = 0
        rect.right, rect.bottom = round(width * scale), round(height * scale)
        return 1
    monkeypatch.setattr(ctypes.windll.user32, 'ScreenToClient', screen_to_client)
    monkeypatch.setattr(ctypes.windll.user32, 'GetClientRect', client_rect)
    monkeypatch.setattr(window, 'devicePixelRatioF', lambda: scale)
    # Qt 接收逻辑屏幕坐标；旧代码错误地把原生物理坐标传给这里。
    monkeypatch.setattr(window, 'mapFromGlobal', lambda p: QPoint(
        p.x() - round(origin[0] / scale), p.y() - round(origin[1] / scale)))

    def hit(x, y):
        sx, sy = origin[0] + round(x * scale), origin[1] + round(y * scale)
        msg = wt.MSG()
        msg.hWnd = 1
        msg.message = 0x0084
        msg.lParam = (sx & 0xffff) | ((sy & 0xffff) << 16)
        return window.nativeEvent(b'windows_generic_MSG', ctypes.addressof(msg))

    for x, y in [(width - 100, 100), (width - 100, height - 100), (width / 2, height / 2)]:
        assert hit(x, y) == (False, 0), '内容区点击不应被窗口拉伸截获'
    assert hit(1, 100) == (True, 10)
    assert hit(width - 1, 100) == (True, 11)
    assert hit(1, 1) == (True, 13)
    assert hit(width - 1, height - 1) == (True, 17)


def test_startup_toolbar_accepts_mouse_and_keyboard(window, monkeypatch):
    opened = []
    def choose(*args, **kwargs):
        opened.append(True)
        return [], ''
    monkeypatch.setattr(QFileDialog, 'getOpenFileNames', choose)
    assert QApplication.activeModalWidget() is None
    assert window.welcome_open.isVisible()
    assert window.tools_panel.isHidden()
    assert not window.save_action.isEnabled()
    assert not window.print_action.isEnabled()
    QTest.mouseClick(window.welcome_open, Qt.MouseButton.LeftButton)
    QTest.keyClick(window, Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier)
    assert len(opened) == 2


@pytest.fixture
def loaded_window(window, tmp_path):
    path = tmp_path / 'sample.pdf'
    with pymupdf.open() as doc:
        for _ in range(5):
            doc.new_page()
        doc.save(path)
    window._ingest([str(path)], replace=True)
    QApplication.processEvents()
    return window


def click_thumbnail(window, row, modifiers=Qt.KeyboardModifier.NoModifier):
    window.act_pages.setChecked(True)
    QApplication.processEvents()
    view = window.left_view
    assert view.isVisible()
    index = window.model.index(row)
    view.scrollTo(index)
    QApplication.processEvents()
    point = view.visualRect(index).intersected(view.viewport().rect()).center()
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, modifiers, point)
    # Reader/resize timers must not replace the user's selection after release.
    QTest.qWait(300)


def test_left_thumbnail_standard_clicks_keep_both_views_in_sync_without_flicker(loaded_window):
    window = loaded_window
    changes = []
    window.left_view.selectionModel().selectionChanged.connect(
        lambda *_: changes.append(set(i.row() for i in window.left_view.selectedIndexes())))
    for row, modifiers, expected in (
        (1, Qt.KeyboardModifier.NoModifier, {1}),
        (2, Qt.KeyboardModifier.ControlModifier, {1, 2}),
        (1, Qt.KeyboardModifier.ControlModifier, {2}),
        (2, Qt.KeyboardModifier.ControlModifier, set()),
        (0, Qt.KeyboardModifier.NoModifier, {0}),
    ):
        changes.clear()
        click_thumbnail(window, row, modifiers)
        assert window.ws.selected == expected
        assert set(window.ws.target_indices()) == expected
        assert set(i.row() for i in window.left_view.selectedIndexes()) == expected
        assert set(i.row() for i in window.arrange_view.selectedIndexes()) == expected
        assert changes == [expected], 'One click must not briefly clear or replace the selection'
        assert window.ws.preview_index() == row
        assert window.delete_action.isEnabled() == bool(expected)
    assert not window.ws.dirty


def test_left_thumbnail_shift_ctrl_and_plain_click_remain_compatible(loaded_window):
    window = loaded_window
    click_thumbnail(window, 1)
    assert window.ws.selected == {1}
    click_thumbnail(window, 3, Qt.KeyboardModifier.ShiftModifier)
    assert window.ws.selected == {1, 2, 3}
    click_thumbnail(window, 2, Qt.KeyboardModifier.ControlModifier)
    assert window.ws.selected == {1, 3}
    click_thumbnail(window, 4)
    assert window.ws.selected == {4}
    click_thumbnail(window, 4)
    assert window.ws.selected == {4}, 'An ordinary click must not unselect the current page'
    window.stack.setCurrentIndex(1)
    QApplication.processEvents()
    view = window.arrange_view
    view.scrollTo(window.model.index(4))
    QApplication.processEvents()
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
                     pos=view.visualRect(window.model.index(4)).center())
    assert window.ws.selected == {4}, 'The arrange view retains its existing single-click behavior'


def test_pressing_an_already_selected_thumbnail_keeps_the_group_until_release(loaded_window):
    window = loaded_window
    click_thumbnail(window, 1, Qt.KeyboardModifier.ControlModifier)
    view = window.left_view
    point = view.visualRect(window.model.index(1)).center()
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert window.ws.selected == {0, 1}, 'Pressing a selected page must keep the group available to drag'
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTest.qWait(300)
    assert window.ws.selected == {1}


@pytest.mark.parametrize('selection', [{0, 2}, set(), set(range(5))])
def test_reader_scroll_does_not_replace_an_explicit_multiple_or_empty_selection(loaded_window, selection):
    window = loaded_window
    window.act_pages.setChecked(True)
    window.ws.set_selection(selection, 0 if selection else None)
    window.ws.last_preview_index = 0
    window._sync_selection()
    window._set_zoom(1.0)
    QApplication.processEvents()
    view = window.preview_view
    rectangle = view.page_rects[3]
    view.centerOn(rectangle.center().x(), rectangle.top() + view.viewport().height() / 2 - 4)
    QTest.qWait(350)
    window._reading_scrolled()
    assert window.ws.preview_index() == 3
    assert window.ws.selected == selection
    assert set(i.row() for i in window.left_view.selectedIndexes()) == selection


def test_ctrl_click_selection_is_used_by_batch_rotate_and_survives_refresh(loaded_window):
    window = loaded_window
    before = tuple(window.ws.pages)
    click_thumbnail(window, 1, Qt.KeyboardModifier.ControlModifier)
    window.rotate(90)
    assert [page.rotation for page in window.ws.pages] == [90, 90, 0, 0, 0]
    assert window.ws.selected == {0, 1}
    assert set(i.row() for i in window.left_view.selectedIndexes()) == {0, 1}
    window.ws.undo_stack.undo()
    window.refresh()
    assert tuple(window.ws.pages) == before and window.ws.selected == {0, 1}
    assert not window.ws.dirty


def test_split_and_conversion_choices_are_independent(loaded_window):
    window = loaded_window
    window.open_export_tool(1)
    assert window.split_each.isChecked()
    assert window.convert_all.isChecked()
    window.export_tools.setCurrentIndex(1)
    QApplication.processEvents()
    assert not window.split_range.isVisible()
    QTest.mouseClick(window.split_range_mode, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    assert window.split_range_mode.isChecked()
    assert window.split_range.isVisible()
    assert window.convert_all.isChecked()
    window.export_tools.setCurrentIndex(2)
    QApplication.processEvents()
    assert not window.convert_range.isVisible()
    QTest.mouseClick(window.convert_selected, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    assert window.convert_selected.isChecked()
    assert not window.convert_all.isChecked()
    assert window.split_range_mode.isChecked()
    QTest.mouseClick(window.convert_range_mode, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    assert window.convert_range.isVisible()
    QTest.mouseClick(window.convert_range, Qt.MouseButton.LeftButton)
    QTest.keyClicks(window.convert_range, '1-3,5')
    assert window.convert_button.isEnabled()
    assert window.convert_range.text() == '1-3,5'


def test_export_tools_flow_without_stretching_the_open_form(loaded_window):
    window = loaded_window
    window.open_export_tool(1)
    gaps = []
    for height in (680, 1000):
        window.resize(1100, height)
        for _ in range(3):
            QApplication.processEvents()
        tools = window.export_tools
        bottom = window.split_button.mapTo(tools.widget(), window.split_button.rect().bottomLeft()).y()
        following = tools.headers[2].mapTo(tools.widget(), QPoint()).y()
        gaps.append(following - bottom)
        assert 0 < gaps[-1] <= 32, '下一项应紧跟设置，不能被空白推到底部'
    assert abs(gaps[0] - gaps[1]) <= 2
    QTest.mouseClick(tools.headers[2], Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    assert window.convert_all.isVisible() and window.export_tools.currentIndex() == 2
    assert tools.headers[2].isChecked()
    QTest.mouseClick(tools.headers[2], Qt.MouseButton.LeftButton)
    assert tools.currentIndex() == -1
    assert all(page.isHidden() for page in tools.pages)
    assert window._tool_index == -1
    window.split_action.trigger()
    assert window.export_tools.currentIndex() == 1
    assert tools.headers[1].isChecked()


def test_export_settings_scroll_only_for_current_form(loaded_window):
    window = loaded_window
    window.resize(1100, 680)
    window.open_export_tool(2)
    window.convert_range_mode.setChecked(True)
    window.convert_range.setText('1-3')
    for _ in range(3):
        QApplication.processEvents()
    tools = window.export_tools
    scrollbar = tools.verticalScrollBar()
    long_form_scroll = scrollbar.maximum()
    assert long_form_scroll > 0
    window.convert_range.setFocus()
    QTest.keyClick(window.convert_range, Qt.Key.Key_Tab)
    QApplication.processEvents()
    assert window.convert_button.hasFocus()
    viewport = tools.viewport()
    assert viewport.rect().contains(window.convert_button.mapTo(viewport, QPoint()))
    assert viewport.rect().contains(window.convert_button.mapTo(viewport, window.convert_button.rect().bottomRight()))
    window.open_export_tool(3)
    for _ in range(3):
        QApplication.processEvents()
    assert window.compress_button.isVisible()
    assert scrollbar.maximum() < long_form_scroll, '短表单不应继承其他工具的高度'
    QTest.mouseClick(tools.headers[3], Qt.MouseButton.LeftButton)
    for _ in range(3):
        QApplication.processEvents()
    assert scrollbar.maximum() == 0, '折叠全部设置后应完整显示工具列表'


def test_page_navigation_and_modes_keep_document_state(loaded_window):
    window = loaded_window
    window._set_zoom(1.25)
    assert not window.previous_page.isEnabled()
    QTest.mouseClick(window.next_page, Qt.MouseButton.LeftButton)
    assert window.ws.preview_index() == 1
    window.focus_page_number()
    QTest.keyClicks(window.page_number, '4')
    QTest.keyClick(window.page_number, Qt.Key.Key_Return)
    assert window.ws.preview_index() == 3
    assert window.ws.selected == {3}
    window.ws.set_selection({1, 3}, 3)
    window._sync_selection()
    QTest.mouseClick(window.mode_buttons[1], Qt.MouseButton.LeftButton)
    assert window.stack.currentIndex() == 1
    assert [button.isChecked() for button in window.mode_buttons] == [False, True]
    QTest.mouseClick(window.mode_buttons[0], Qt.MouseButton.LeftButton)
    assert [button.isChecked() for button in window.mode_buttons] == [True, False]
    assert window.ws.selected == {1, 3}
    assert window.ws.preview_index() == 3
    assert window._zoom == 1.25
    assert not window.ws.dirty
    window.go_to_page(5)
    assert not window.next_page.isEnabled()
    window.step_page(1)
    assert window.ws.preview_index() == 4


def test_invalid_export_ranges_are_blocked_before_file_dialog(loaded_window):
    window = loaded_window
    window.open_export_tool(0)
    window.extract_range.setText('99')
    assert not window.extract_button.isEnabled()
    assert window.extract_feedback.text()
    window.extract_range.setText('1-3')
    assert window.extract_button.isEnabled()
    window.split_range_mode.setChecked(True)
    assert not window.split_button.isEnabled()
    window.split_range.setText('2-4')
    assert window.split_button.isEnabled()
    window.convert_range_mode.setChecked(True)
    window.convert_range.setText('6')
    assert not window.convert_button.isEnabled()
    window.convert_all.setChecked(True)
    assert window.convert_button.isEnabled()
    window.ws.set_selection(set(), None)
    window.extract_range.clear()
    window.convert_selected.setChecked(True)
    window._update_preview()
    assert not window.extract_button.isEnabled()
    assert not window.convert_button.isEnabled()


def test_delete_in_range_field_does_not_delete_pages(loaded_window):
    window = loaded_window
    window.open_export_tool(0)
    window.act_pages.setChecked(True)
    QApplication.processEvents()
    window.extract_range.setText('123')
    window.extract_range.setFocus()
    window.extract_range.selectAll()
    QTest.keyClick(window.extract_range, Qt.Key.Key_Delete)
    assert window.extract_range.text() == ''
    assert len(window.ws.pages) == 5
    window.left_view.setFocus()
    QTest.keyClick(window.left_view, Qt.Key.Key_Delete)
    assert len(window.ws.pages) == 4
    window.undo_action.trigger()
    QApplication.processEvents()
    assert len(window.ws.pages) == 5


def test_save_state_tracks_edit_undo_merge_and_save(loaded_window, tmp_path, monkeypatch):
    window = loaded_window
    assert window.save_state.text() == '已保存'
    window.rotate(90)
    QApplication.processEvents()
    assert window.save_state.text() == '有未保存的修改'
    window.undo_action.trigger()
    QApplication.processEvents()
    assert window.save_state.text() == '已保存'
    window.ws.merge_pages(window.ws.pages[:1])
    window.refresh()
    assert window.save_state.text() == '未保存 · 保存为新文件'
    output = tmp_path / 'combined.pdf'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(output), ''))
    assert window.save()
    assert window.save_state.text() == '已保存'
    assert window.document_name.text() == 'combined.pdf'
    assert str(output) in window.statusBar().currentMessage()
    with pymupdf.open(output) as doc:
        assert doc.page_count == 6


def test_arrange_extract_uses_selection_and_restores_range(loaded_window, tmp_path, monkeypatch):
    window = loaded_window
    window.open_export_tool(0)
    window.ws.set_selection({1, 3}, 3)
    window.refresh()
    window.extract_range.setText('1-5')
    window.stack.setCurrentIndex(1)
    output = tmp_path / 'selected.pdf'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(output), ''))
    window.extract_selected()
    assert window.extract_range.text() == '1-5'
    assert not window.ws.dirty
    with pymupdf.open(output) as doc:
        assert doc.page_count == 2


def test_reading_toolbar_fits_the_reader_and_top_actions_stay_compact(loaded_window):
    from PyQt6.QtCore import QRect
    from PyQt6.QtWidgets import QFrame
    window = loaded_window
    window.resize(980, 680)
    QApplication.processEvents()
    assert window.left_panel.isHidden()
    assert window.tools_panel.isHidden()
    assert window.banner.isHidden() and window.details.isHidden()
    toolbar = window.findChild(QFrame, 'workbench')
    assert toolbar.height() == 40
    assert window.mode_switch.parentWidget() is toolbar
    assert window.mode_switch.height() == 32
    assert all(button.parentWidget() is window.mode_switch for button in window.mode_buttons)
    assert all(window.mode_switch.rect().contains(button.geometry()) for button in window.mode_buttons)
    assert all(toolbar.rect().contains(QRect(button.mapTo(toolbar, QPoint()), button.size()))
               for button in window.mode_buttons)
    reader_toolbar = window.navigation_bar
    assert reader_toolbar.parentWidget() is toolbar
    assert window.width() == 980
    for widget in (window.page_number, window.zoom_label, window.previous_page, window.next_page):
        assert toolbar.rect().contains(QRect(widget.mapTo(toolbar, QPoint()), widget.size()))
    assert all(not button.text() and button.toolTip() for button in window.mode_buttons)
    canvas_top = window.preview_view.mapTo(window, QPoint()).y()
    toolbar_bottom = toolbar.mapTo(window, QPoint(0, toolbar.height())).y()
    assert 0 <= canvas_top - toolbar_bottom <= 3


def test_top_toolbar_page_tools_work_without_opening_side_panel(loaded_window, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QFrame
    window = loaded_window
    toolbar = window.findChild(QFrame, 'workbench')
    buttons = {b.accessibleName(): b for b in toolbar.findChildren(QToolButton)}
    for name in ('左转所选页', '右转所选页', '删除所选页', '提取所选页', '拆分文件', 'PDF 转 Excel'):
        assert name in buttons
    assert window.tools_panel.isHidden()
    QTest.mouseClick(buttons['右转所选页'], Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    assert window.ws.pages[0].rotation == 90
    QTest.mouseClick(buttons['左转所选页'], Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    assert window.ws.pages[0].rotation == 0
    QTest.mouseClick(buttons['删除所选页'], Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    assert len(window.ws.pages) == 4
    window.undo_action.trigger()
    QApplication.processEvents()
    assert len(window.ws.pages) == 5
    window.ws.set_selection({1, 3}, 3)
    window.refresh()
    output = tmp_path / 'top-toolbar-selected.pdf'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(output), ''))
    QTest.mouseClick(buttons['提取所选页'], Qt.MouseButton.LeftButton)
    with pymupdf.open(output) as doc:
        assert doc.page_count == 2
    QTest.mouseClick(buttons['拆分文件'], Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    assert window.tools_panel.isVisible() and window.export_tools.currentIndex() == 1
    QTest.mouseClick(buttons['PDF 转 Excel'], Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    assert window.export_tools.currentIndex() == 2
    window.act_tools.setChecked(False)
    window.showNormal()
    window.resize(980, 680)
    QApplication.processEvents()
    assert window.width() == 980
    for button in buttons.values():
        assert button.isVisible()
        assert button.geometry().right() < toolbar.width()
        assert button.geometry().bottom() < toolbar.height()


def test_continuous_scroll_updates_page_without_jumping(loaded_window):
    window = loaded_window
    view = window.preview_view
    window._set_zoom(0.5)
    window._update_preview()
    QApplication.processEvents()
    assert len(view.page_rects) == 5
    assert view.sceneRect().height() > view.page_rects[0].height() * 5
    # 同一视口同时跨过两页，滚动不把视口强制吸附到页顶。
    scale = view.transform().m11()
    bar = view.verticalScrollBar()
    bar.setValue(round((view.page_rects[1].top() + 120) * scale))
    before = bar.value()
    window._reading_scrolled()
    assert window.ws.preview_index() == 1
    assert window.page_number.value() == 2
    assert abs(bar.value() - before) <= 1
    visible = view.mapToScene(view.viewport().rect()).boundingRect()
    assert sum(r.intersects(visible) for r in view.page_rects) >= 2
    window.go_to_page(4)
    window._reading_scrolled()
    assert window.page_number.value() == 4
    window.step_page(-1)
    window._reading_scrolled()
    assert window.page_number.value() == 3
    assert not window.ws.dirty


def test_continuous_layout_tracks_rotation_and_deletion(loaded_window):
    window = loaded_window
    before = window.preview_view.page_rects[0]
    window.rotate(90)
    window.refresh()
    assert window.preview_view.page_rects[0].width() == before.height()
    window.delete_pages()
    window.refresh()
    assert len(window.preview_view.page_rects) == 4
    window.undo_action.trigger()
    window.refresh()
    assert len(window.preview_view.page_rects) == 5


def test_open_pdf_preserves_normal_window_size(loaded_window):
    window = loaded_window
    window.showNormal()
    window.resize(1100, 900)
    QApplication.processEvents()
    before = window.size()
    window._ingest([window.ws.path], replace=True)
    QApplication.processEvents()
    assert not window.isMaximized() and window.size() == before


@pytest.mark.parametrize('width,height', [(842, 595), (595, 842)])
def test_single_page_fit_does_not_create_scrollbar_resize_loop(window, input_app, tmp_path, width, height):
    source = tmp_path / 'single-page.pdf'
    with pymupdf.open() as doc:
        doc.new_page(width=width, height=height)
        doc.save(source)
    window.resize(980 if width > height else 1400, 1000)
    assert window.open_files_in_tabs([str(source)])
    QTest.qWait(250)

    view = window.preview_view
    assert window._zoom_mode == 'page'
    assert not view.horizontalScrollBar().isVisible()
    assert not view.verticalScrollBar().isVisible()
    assert view.sceneRect().width() * view.transform().m11() <= view.viewport().width() - 2
    assert view.sceneRect().height() * view.transform().m11() <= view.viewport().height() - 2
    stable = (view.viewport().size(), view.transform().m11())
    for _ in range(3):
        input_app.processEvents()
        QTest.qWait(20)
        assert (view.viewport().size(), view.transform().m11()) == stable
    assert not window._view_zoom_timer.isActive()
    window._set_zoom(2)
    assert view.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert view.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    window._set_zoom_mode('width')
    QTest.qWait(250)
    assert not view.horizontalScrollBar().isVisible()
    stable = (view.viewport().size(), view.transform().m11())
    for _ in range(3):
        input_app.processEvents()
        QTest.qWait(20)
        assert (view.viewport().size(), view.transform().m11()) == stable


@pytest.mark.parametrize('mode', ('page', 'width', 'custom'))
def test_sidebar_resize_defers_zoom_until_qt_finishes_layout(loaded_window, monkeypatch, mode):
    from app.ui import PDF_LOGICAL_SCALE

    window = loaded_window
    window.act_pages.setChecked(True)
    window._set_zoom(1)
    window._set_zoom_mode(mode)
    QTest.qWait(220)
    view = window.preview_view
    current = window.ws.preview_index()
    calls = []
    resizing = False
    original = window.zoom_label.setText

    def record_zoom(text):
        calls.append(resizing)
        original(text)

    monkeypatch.setattr(window.zoom_label, 'setText', record_zoom)
    splitter = window.left_panel.parentWidget()
    resizing = True
    for width in (170, 240, 185, 250):
        splitter.setSizes([width, 820, 0])
    resizing = False
    assert not calls, '调整侧栏时不能在 Qt 的 Resize 调用栈中修改缩放和滚动范围'

    QTest.qWait(220)
    assert calls and not any(calls)
    rect = view.spread_rect(current)
    factor = PDF_LOGICAL_SCALE
    if mode in ('page', 'width'):
        factor = (view.viewport().width() - 24) / rect.width()
        if mode == 'page':
            factor = min(factor, (view.viewport().height() - 24) / rect.height())
    assert view.transform().m11() == pytest.approx(factor)
    assert window.ws.preview_index() == current
    assert window._zoom_mode == mode
    assert not window.ws.dirty


def test_arrange_can_zoom_past_three_hundred_percent(loaded_window):
    window = loaded_window
    window.stack.setCurrentIndex(1)
    window._set_arrange_zoom(3)
    before = window.arrange_view.iconSize().width()
    window.arrange_zoom_in()
    assert window._arrange_zoom > 3
    assert window.arrange_view.iconSize().width() > before
    window.arrange_zoom_out()
    assert window._arrange_zoom == 3


def test_double_click_arranged_page_opens_it_for_reading(loaded_window):
    window = loaded_window
    window._set_zoom(1.25)
    window.stack.setCurrentIndex(1)
    QApplication.processEvents()
    index = window.model.index(2)
    window.arrange_view.scrollTo(index)
    QApplication.processEvents()
    position = window.arrange_view.visualRect(index).center()
    QTest.mouseClick(window.arrange_view.viewport(), Qt.MouseButton.LeftButton, pos=position)
    QTest.mouseDClick(window.arrange_view.viewport(), Qt.MouseButton.LeftButton, pos=position)
    QApplication.processEvents()
    assert window.stack.currentIndex() == 0
    assert window.page_number.value() == 3
    assert window.ws.selected == {2}
    assert window._zoom == 1.25
    window._reading_scrolled()
    assert window.ws.preview_index() == 2
    assert not window.ws.dirty


def test_reading_settings_stay_available_from_the_view_menu(loaded_window):
    window = loaded_window
    view_menu = next(action.menu() for action in window._menubar.actions() if action.text() == '视图')
    assert window.reading_settings_menu.menuAction() in view_menu.actions()
    restore=next(action.menu() for action in view_menu.actions() if action.text()=='界面恢复')
    assert window.reading_orb.restore_action in restore.actions()
    window.reading_zoom_actions['width'].trigger()
    assert window._zoom_mode == 'width'
    assert window.reading_zoom_actions['width'].isChecked()
    window._set_zoom(1.25)
    assert window.reading_zoom_actions['custom'].isChecked()


def test_zoom_below_ten_percent_stays_in_reading_at_minimum(loaded_window):
    window = loaded_window
    window.go_to_page(3)
    window._set_zoom(0.20)
    assert window.stack.currentIndex() == 0
    window._set_zoom(0.10)
    assert window.stack.currentIndex() == 0
    window.zoom_out()
    assert window.stack.currentIndex() == 0
    assert window._zoom == 0.10
    assert window.zoom_label.text() == '10%'
    assert window.ws.preview_index() == 2
    assert not window.ws.dirty
    window.stack.setCurrentIndex(0)
    assert window.stack.currentIndex() == 0
    assert window._zoom == 0.10
    from app.ui import PDF_LOGICAL_SCALE
    expected = PDF_LOGICAL_SCALE * 0.10
    assert abs(window.preview_view.transform().m11() - expected) < 0.001
