"""Localized captions must keep stable shortcut command bindings."""
import pytest

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QApplication, QPushButton, QWidget

from app.i18n import LANGUAGE_KEY, current_language, initialize_language, tr
from app.shortcutcommands import COMMANDS
from app.shortcuts import ShortcutManager, _target
from app.shortcutui import ShortcutDialog
from app.ui import MainWindow


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def english(qapp, tmp_path):
    previous = current_language()
    settings = QSettings(str(tmp_path / 'language.ini'), QSettings.Format.IniFormat)
    initialize_language(settings, qapp, system_languages=['en-US'])
    assert tr('保存') != '保存'
    yield
    settings.setValue(LANGUAGE_KEY, previous)
    initialize_language(settings, qapp)


def test_translated_main_menus_keep_save_copy_and_print_bindings(english, qapp):
    win = MainWindow()
    try:
        for identifier, action, key in (
            ('file.save', win.save_action, 'Ctrl+S'),
            ('edit.copy_pages', win.copy_pages_action, 'Ctrl+C'),
            ('file.print', win.print_action, 'Ctrl+P'),
        ):
            assert win._shortcut_actions[identifier] is action
            assert action.shortcut() == QKeySequence(key)
            assert action.property('shortcutCommand') == identifier
        assert COMMANDS['file.save'].menu == ('文件', '保存')
        def check(menu):
            for action in menu.actions():
                if action.menu():
                    check(action.menu())
                elif not action.isSeparator() and action is not win.reading_fallback_notice:
                    assert action.property('shortcutCommand') in COMMANDS, action.text()
        check(win._menubar)
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_localized_tool_selectors_keep_index_and_attributes(english):
    owner = QWidget()
    try:
        first = QPushButton(tr('选择文件'), owner)
        second = QPushButton(tr('选择文件'), owner)
        save = QPushButton(tr('保存图片'), owner)
        source_caption = QPushButton('下一页', owner)
        save.setToolTip(tr('复制图片'))
        save.setAccessibleName(tr('选择文件'))
        owner.submit = save
        assert _target(owner, 'text:选择文件#0') is first
        assert _target(owner, 'text:选择文件#1') is second
        assert _target(owner, 'text:保存图片') is save
        assert _target(owner, 'tip:复制图片') is save
        assert _target(owner, 'name:选择文件') is save
        assert _target(owner, 'attr:submit') is save
        assert _target(owner, 'text:下一页') is source_caption
    finally:
        owner.deleteLater()


@pytest.mark.parametrize('language', ['en', 'zh-CN', 'zh-TW', 'ja', 'ko', 'de', 'fr', 'es'])
def test_every_localized_main_menu_entry_keeps_its_command(qapp, tmp_path, language):
    previous = current_language()
    settings = QSettings(str(tmp_path / 'language.ini'), QSettings.Format.IniFormat)
    settings.setValue(LANGUAGE_KEY, language)
    initialize_language(settings, qapp)
    win = MainWindow()
    try:
        def walk(menu):
            for action in menu.actions():
                if action.menu():
                    walk(action.menu())
                elif not action.isSeparator() and action is not win.reading_fallback_notice:
                    assert action.property('shortcutCommand') in COMMANDS, (language, action.text())
        walk(win._menubar)
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()
        settings.setValue(LANGUAGE_KEY, previous)
        initialize_language(settings, qapp)


def test_shortcut_editor_searches_localized_labels_and_keeps_source_categories(english, tmp_path):
    settings = QSettings(str(tmp_path / 'shortcuts.ini'), QSettings.Format.IniFormat)
    manager = ShortcutManager(settings)
    dialog = ShortcutDialog(manager)
    try:
        dialog.search.setText(tr(COMMANDS['file.save'].label))
        assert 'file.save' in dialog._row_by_id
        row = dialog._row_by_id['file.save']
        assert dialog.table.item(row, 0).text() == tr('保存')
        assert dialog.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == 'file.save'
        file_category = next(dialog.categories.topLevelItem(i)
                             for i in range(dialog.categories.topLevelItemCount())
                             if dialog.categories.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == '文件')
        assert file_category.text(0) == tr('文件')
    finally:
        dialog.reject()
        dialog.deleteLater()
