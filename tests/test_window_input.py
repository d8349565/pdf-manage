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


def test_split_and_conversion_choices_are_independent(loaded_window):
    window = loaded_window
    window.act_tools.setChecked(True)
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
    QTest.mouseClick(window.mode_buttons[0], Qt.MouseButton.LeftButton)
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
    window.act_tools.setChecked(True)
    window.act_pages.setChecked(True)
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


def test_reading_toolbar_is_one_compact_row(loaded_window):
    window = loaded_window
    window.resize(980, 680)
    QApplication.processEvents()
    assert window.left_panel.isHidden()
    assert window.tools_panel.isHidden()
    assert window.banner.isHidden() and window.details.isHidden()
    toolbar = window.navigation_bar.parentWidget()
    assert toolbar.height() <= 40
    for widget in [*window.mode_buttons, window.navigation_bar]:
        assert widget.geometry().top() >= 0
        assert widget.geometry().bottom() < toolbar.height()
        assert widget.geometry().right() < toolbar.width()


def test_top_toolbar_page_tools_work_without_opening_side_panel(loaded_window, tmp_path, monkeypatch):
    window = loaded_window
    toolbar = window.navigation_bar.parentWidget()
    buttons = {b.accessibleName(): b for b in toolbar.findChildren(QToolButton)}
    for name in ('左转所选页', '右转所选页', '删除所选页', '提取所选页', '拆分文件', '表格转 Excel'):
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
    QTest.mouseClick(buttons['表格转 Excel'], Qt.MouseButton.LeftButton)
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
    window._set_zoom(0.09)
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
    assert window._zoom == 1.0
    window._reading_scrolled()
    assert window.ws.preview_index() == 2
    assert not window.ws.dirty


def test_zoom_below_ten_percent_enters_arrange_once(loaded_window):
    window = loaded_window
    window.go_to_page(3)
    window._set_zoom(0.20)
    assert window.stack.currentIndex() == 0
    window._set_zoom(0.10)
    assert window.stack.currentIndex() == 0
    window.zoom_out()
    assert window.stack.currentIndex() == 1
    assert window._zoom == 0.10
    assert window.zoom_label.text() == '10%'
    assert window.ws.preview_index() == 2
    assert not window.ws.dirty
    window.stack.setCurrentIndex(0)
    assert window.stack.currentIndex() == 0
    assert window._zoom == 0.10
    expected = window._preview_viewport_width() * 0.10 / window.preview_view.content_width()
    assert abs(window.preview_view.transform().m11() - expected) < 0.001
