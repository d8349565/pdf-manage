"""Default reading views exercised through document opening and visible menu actions."""
import pymupdf
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMenuBar

from app.ui import MainWindow


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


def reading_action(window, text):
    view = next(action.menu() for bar in window.findChildren(QMenuBar)
                for action in bar.actions() if action.text() == '视图')
    menu = next(action.menu() for action in view.actions() if action.text() == '阅读设置')
    action = next((action for action in menu.actions() if action.text() == text), None)
    assert action is not None, f'The reading settings menu must offer {text}'
    return action


def make_pdf(path):
    with pymupdf.open() as document:
        for number in range(4):
            page = document.new_page(width=360, height=520)
            page.insert_text((40, 60), f'Generated reading page {number + 1}')
        document.save(path)
    return str(path)


def expect_page_columns(window, columns):
    for _ in range(300):
        if window.preview_view.page_columns == columns:
            return
        QTest.qWait(10)
    assert window.preview_view.page_columns == columns


@pytest.mark.parametrize('layout, columns', [('单页连续', 1), ('双页对开', 2), ('四页预览（2×2）', 4)])
@pytest.mark.parametrize('zoom', ['适合页面', '适合宽度'])
def test_saved_reading_view_applies_after_restart_and_to_new_document_tabs(qapp, tmp_path, layout, columns, zoom):
    first = make_pdf(tmp_path / 'first.pdf')
    second = make_pdf(tmp_path / 'second.pdf')
    window = MainWindow()
    try:
        window.resize(1400, 900)
        window.show()
        assert window.open_files_in_tabs([first])
        reading_action(window, layout).trigger()
        reading_action(window, zoom).trigger()
        reading_action(window, '将当前视图设为默认').trigger()
    finally:
        window.ws.reset()
        window.close()
        qapp.processEvents()

    reopened = MainWindow()
    try:
        reopened.resize(1400, 900)
        reopened.show()
        assert reopened.open_files_in_tabs([first])
        assert reading_action(reopened, layout).isChecked()
        assert reading_action(reopened, zoom).isChecked()
        expect_page_columns(reopened, columns)
        assert reopened.open_files_in_tabs([second])
        assert reading_action(reopened, layout).isChecked()
        assert reading_action(reopened, zoom).isChecked()
        expect_page_columns(reopened, columns)
        assert not reopened.ws.dirty
    finally:
        reopened.ws.reset()
        reopened.close()
        qapp.processEvents()


def test_new_image_document_uses_the_saved_view_without_changing_existing_tabs(qapp, tmp_path):
    from PIL import Image

    source = make_pdf(tmp_path / 'existing.pdf')
    image_paths = []
    for number in range(4):
        path = tmp_path / f'image-{number}.png'
        Image.new('RGB', (360, 520), 'white').save(path)
        image_paths.append(str(path))
    window = MainWindow()
    try:
        window.resize(1400, 900)
        window.show()
        assert window.open_files_in_tabs([source])
        reading_action(window, '四页预览（2×2）').trigger()
        reading_action(window, '适合宽度').trigger()
        reading_action(window, '将当前视图设为默认').trigger()
        reading_action(window, '单页连续').trigger()
        reading_action(window, '适合页面').trigger()
        assert window.open_files_in_tabs(image_paths)
        assert reading_action(window, '四页预览（2×2）').isChecked()
        assert reading_action(window, '适合宽度').isChecked()
        expect_page_columns(window, 4)
        window.step_tab(-1)
        assert reading_action(window, '单页连续').isChecked()
        assert reading_action(window, '适合页面').isChecked()
        assert not window.ws.dirty
    finally:
        window.ws.reset()
        window.step_tab(1)
        window.ws.reset()
        window.close()
        qapp.processEvents()


def test_reading_and_organizing_have_visible_working_keyboard_shortcuts(qapp, tmp_path):
    source = make_pdf(tmp_path / 'shortcuts.pdf')
    window = MainWindow()
    try:
        window.show()
        assert window.open_files_in_tabs([source])
        view = next(action.menu() for bar in window.findChildren(QMenuBar)
                    for action in bar.actions() if action.text() == '视图')
        actions = {action.text(): action for action in view.actions()}
        assert actions['切换阅读／整理页面'].shortcut().toString() == 'F6'
        assert actions['阅读'].shortcut().isEmpty()
        assert actions['整理页面'].shortcut().isEmpty()
        window.activateWindow()
        window.setFocus()
        qapp.processEvents()
        QTest.keyClick(window, Qt.Key.Key_F6)
        qapp.processEvents()
        assert window.arrange_view.isVisible()
        QTest.keyClick(window, Qt.Key.Key_F6)
        qapp.processEvents()
        assert window.preview_view.isVisible() and not window.arrange_view.isVisible()
        assert not window.ws.dirty
    finally:
        window.ws.reset()
        window.close()
        qapp.processEvents()


def test_restore_interface_layout_also_restores_the_default_reading_view(qapp, tmp_path):
    source = make_pdf(tmp_path / 'restore.pdf')
    window = MainWindow()
    try:
        window.show()
        assert window.open_files_in_tabs([source])
        reading_action(window, '四页预览（2×2）').trigger()
        reading_action(window, '适合宽度').trigger()
        reading_action(window, '将当前视图设为默认').trigger()
        restore = next(action for action in window.findChildren(QAction) if action.text() == '恢复界面布局')
        restore.trigger()
        assert reading_action(window, '单页连续').isChecked()
        assert reading_action(window, '适合页面').isChecked()
        window.close_current_file()
        assert window.open_files_in_tabs([source])
        assert reading_action(window, '单页连续').isChecked()
        assert reading_action(window, '适合页面').isChecked()
        assert not window.ws.dirty
    finally:
        window.ws.reset()
        window.close()
        qapp.processEvents()
