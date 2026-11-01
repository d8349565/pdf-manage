"""Shortcut configuration and real Qt input, with isolated settings only."""
import json

import pytest
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit, QMenu

from app.shortcutcommands import COMMANDS
from app.shortcuts import SETTINGS_KEY, ShortcutManager, conflicts, default_values, get_manager
from app.shortcutui import ShortcutDialog
from app.ui import MainWindow


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def manager(tmp_path):
    return ShortcutManager(QSettings(str(tmp_path / 'shortcuts.ini'), QSettings.Format.IniFormat))


@pytest.fixture
def window(qapp):
    win = MainWindow()
    win.show()
    win.activateWindow()
    qapp.processEvents()
    yield win
    win.ws.reset()
    win.close()
    win.deleteLater()
    qapp.processEvents()


def test_catalog_defaults_and_context_reuse(manager):
    assert not conflicts(default_values())
    assert not manager.error(manager.values())
    values = manager.values()
    values['file.save'] = ['Ctrl+C']
    assert '复制' in manager.error(values)
    values = manager.values()
    values['print.submit'] = ['Ctrl+S']
    assert not manager.error(values), 'separate active windows may reuse keys'
    values['reader.hand'] = ['Alt+Right']
    assert manager.error(values), 'reader and window scope overlap'


def test_keypad_and_backtab_use_the_same_logical_binding(manager):
    from PyQt6.QtCore import QEvent
    from PyQt6.QtGui import QKeyEvent
    from app.shortcuts import canonical
    enter = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Enter,
                     Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.KeypadModifier)
    assert manager.matches('textbox.apply', enter)
    assert canonical('Ctrl+Num++') == canonical('Ctrl++')
    assert canonical('Ctrl+Shift+Backtab') == canonical('Ctrl+Shift+Tab')
    values = manager.values()
    values['file.save'] = ['Ctrl+Num++']
    assert manager.error(values), 'keypad aliases must not bypass zoom conflict detection'


@pytest.mark.parametrize('binding', ['Esc', 'Tab', 'Alt+F4', 'Ctrl+K, Ctrl+C', 'not a key'])
def test_invalid_bindings_do_not_change_saved_configuration(manager, binding):
    before = manager.values()
    values = manager.values()
    values['file.save'] = [binding]
    assert manager.apply(values)
    assert manager.values() == before
    assert not manager.settings.contains(SETTINGS_KEY)


def test_legacy_migration_keeps_cleared_binding_and_alternatives_roundtrip(tmp_path):
    settings = QSettings(str(tmp_path / 'migration.ini'), QSettings.Format.IniFormat)
    settings.setValue('reading/shortcuts', json.dumps({'mode': 'F7', 'hand': '', 'next': 'Space'}))
    manager = ShortcutManager(settings)
    assert manager.values()['reader.mode'] == ['F7']
    assert manager.values()['reader.hand'] == []
    assert manager.values()['reader.next'] == ['Space']
    assert settings.contains(SETTINGS_KEY) and settings.contains('reading/shortcuts')
    values = manager.values()
    values['file.save'] = ['F10', 'Ctrl+S', 'F10']
    assert manager.apply(values) == ''
    assert ShortcutManager(settings).values()['file.save'] == ['F10', 'Ctrl+S']
    assert ShortcutManager(settings).values()['reader.hand'] == []


def test_settings_profiles_are_independent_but_same_profile_is_shared(qapp, tmp_path):
    installed = get_manager(QSettings(str(tmp_path / 'installed.ini'), QSettings.Format.IniFormat))
    portable = get_manager(QSettings(str(tmp_path / 'portable.ini'), QSettings.Format.IniFormat))
    values = portable.values()
    values['file.save'] = ['F7']
    assert not portable.apply(values)
    assert installed.values()['file.save'] == ['Ctrl+S']
    assert get_manager(QSettings(str(tmp_path / 'portable.ini'), QSettings.Format.IniFormat)) is portable


def test_recovery_is_local_and_explicit_binding_wins_over_default(tmp_path):
    settings = QSettings(str(tmp_path / 'recovery.ini'), QSettings.Format.IniFormat)
    raw = json.dumps({'version': 1, 'overrides': {
        'file.save': ['F3'], 'reader.hand': [], 'reader.select': ['not a key'],
        'tabs.next': ['F10'], 'future.command': ['F7'],
    }})
    settings.setValue(SETTINGS_KEY, raw)
    manager = ShortcutManager(settings)
    assert manager.values()['file.save'] == ['F3']
    assert manager.values()['search.next'] == []
    assert manager.values()['reader.select'] == manager.values()['reader.hand'] == []
    assert manager.values()['tabs.next'] == ['F10']
    assert manager.notices and not manager.error(manager.values())
    assert settings.value(SETTINGS_KEY) == raw, 'recovery must not overwrite the original'


def test_import_replaces_draft_and_rejects_unsupported_versions(manager):
    values = manager.values()
    values['reader.hand'] = []
    assert not manager.apply(values)
    imported, unknown = manager.import_config(json.dumps({'version': 1, 'overrides': {
        'file.save': ['F10'], 'future.command': []}}))
    assert imported['reader.hand'] == ['H']
    assert imported['file.save'] == ['F10'] and unknown == ['future.command']
    assert manager.values()['reader.hand'] == [], 'import only returns a draft'
    with pytest.raises(ValueError, match='版本'):
        manager.import_config('{"version": 2, "overrides": {}}')


def test_write_failure_keeps_runtime_and_persisted_settings(manager, monkeypatch, qapp):
    from PyQt6.QtWidgets import QWidget
    host = QWidget()
    action = QAction('保存', host)
    manager.bind_action('file.save', action, host)
    assert not manager.apply(manager.values())
    before = manager.settings.value(SETTINGS_KEY)
    monkeypatch.setattr(manager.settings, 'status', lambda: QSettings.Status.AccessError)
    values = manager.values()
    values['file.save'] = ['F7']
    assert manager.apply(values)
    assert action.shortcut() == QKeySequence('Ctrl+S')
    assert manager.values()['file.save'] == ['Ctrl+S']
    assert manager.settings.value(SETTINGS_KEY) == before
    assert manager.apply(values) == '', 'a fresh QSettings instance permits retry after a write error'
    assert action.shortcut() == QKeySequence('F7')
    host.deleteLater()


def test_manager_does_not_retain_closed_widgets(manager, qapp):
    import gc
    import weakref
    from PyQt6.QtWidgets import QPushButton, QWidget
    widget = QWidget()
    button = QPushButton('打印', widget)
    action = QAction('保存', widget)
    manager.bind_action('file.save', action, widget)
    manager.bind_button('print.submit', button, widget)
    manager.watch_hint('file.open', button, '打开')
    reference = weakref.ref(widget)
    widget.close()
    del button, action, widget
    gc.collect()
    assert reference() is None
    assert not manager._bindings and not manager._hints


def test_every_menu_command_is_managed_and_entry_is_unique(window):
    entries = []
    def walk(menu):
        for action in menu.actions():
            if action.menu():
                walk(action.menu())
            elif not action.isSeparator() and action is not window.reading_fallback_notice:
                assert action.property('shortcutCommand') in COMMANDS, action.text()
                entries.append(action.text())
    walk(window._menubar)
    assert entries.count('快捷键管理') == 1
    assert '阅读快捷键' not in entries


def test_actual_remapping_updates_menu_and_preserves_typing(window, qapp):
    called = []
    window.open_action.triggered.disconnect()
    window.open_action.triggered.connect(lambda: called.append('open'))
    values = window.shortcut_manager.values()
    values['file.open'] = ['F7', 'F10']
    assert not window.shortcut_manager.apply(values)
    QTest.keyClick(window, Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(window, Qt.Key.Key_F7)
    QTest.keyClick(window, Qt.Key.Key_F10)
    assert called == ['open', 'open']
    assert 'F7' in window.tab_bar.new_tab_button.toolTip()
    assert 'Ctrl+O' not in window.open_action.toolTip()
    window.open_action.setEnabled(False)
    QTest.keyClick(window, Qt.Key.Key_F7)
    assert called == ['open', 'open']
    editor = QLineEdit(window)
    editor.show()
    editor.setFocus()
    QTest.keyClicks(editor, 'v h two words')
    assert editor.text() == 'v h two words'


def load_generated(window, qapp, tmp_path):
    import pymupdf
    source = tmp_path / 'generated-shortcuts.pdf'
    with pymupdf.open() as document:
        for index in range(4):
            document.new_page().insert_text((50, 70), f'Generated shortcut page {index + 1}')
        document.save(source)
    pages = window.store.load(str(source))
    window.ws.open_replace(pages, str(source))
    window.refresh()
    qapp.processEvents()
    window.preview_view.setFocus()


def test_review_shortcut_works_without_manually_opening_tool(window, qapp, tmp_path):
    assert not hasattr(window, 'annotation_tools')
    values = window.shortcut_manager.values()
    values['review.highlight'] = ['F7']
    assert not window.shortcut_manager.apply(values)
    assert not hasattr(window, 'annotation_tools')
    load_generated(window, qapp, tmp_path)
    assert not window.annotation_tools.isVisible()
    QTest.keyClick(window.preview_view, Qt.Key.Key_F7)
    assert window.annotation_tools.isVisible()
    assert window.annotation_tools.tool.currentData() == 'highlight'


def test_textbox_save_uses_new_binding_and_exact_modifiers(window, qapp, tmp_path, monkeypatch):
    from PyQt6.QtCore import QPointF
    load_generated(window, qapp, tmp_path)
    window.open_annotation_tools()
    tools = window.annotation_tools
    tools.tool.setCurrentIndex(tools.tool.findData('freetext'))
    assert tools.ensure_snapshot()
    top = window.preview_view.page_rects[0].topLeft()
    tools.finish('freetext', [top + QPointF(60, 220), top + QPointF(320, 280)])
    box = tools._inline_text_box
    box.editor.setPlainText('Generated shortcut input')
    saved = []
    monkeypatch.setattr(box, 'apply', lambda: True)
    monkeypatch.setattr(window, 'save', lambda save_as=False: saved.append(save_as))
    values = window.shortcut_manager.values()
    values['file.save'] = ['F10']
    assert not window.shortcut_manager.apply(values)
    box.editor.setFocus()
    QTest.keyClick(box.editor, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert saved == []
    QTest.keyClick(box.editor, Qt.Key.Key_F10)
    QTest.keyClick(box.editor, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert saved == [False, True]
    values['file.save'] = ['Ctrl+A']
    values['edit.select_all'] = []
    assert not window.shortcut_manager.apply(values)
    box.editor.setFocus()
    QTest.keyClick(box.editor, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert saved == [False, True], 'native text editing takes precedence over remapped global commands'
    assert box.editor.textCursor().selectedText() == 'Generated shortcut input'
    box.close()


def test_spread_key_remaps_without_leaving_old_page_step(window, qapp, tmp_path):
    load_generated(window, qapp, tmp_path)
    window._set_reading_layout('spread')
    values = window.shortcut_manager.values()
    values['reader.next_group'] = ['F7']
    assert not window.shortcut_manager.apply(values)
    stepped = []
    window.preview_view.page_step.connect(stepped.append)
    QTest.keyClick(window.preview_view, Qt.Key.Key_PageDown)
    assert stepped == []
    QTest.keyClick(window.preview_view, Qt.Key.Key_F7)
    assert stepped == [1]


def test_first_last_keys_work_when_reader_toolbar_is_hidden(window, qapp, tmp_path):
    load_generated(window, qapp, tmp_path)
    values = window.shortcut_manager.values()
    values['reader.last'] = ['F7']
    assert not window.shortcut_manager.apply(values)
    window.immersive.borderless_action.setChecked(True)
    qapp.processEvents()
    assert not window.navigation_bar.isVisible()
    window.preview_view.setFocus()
    QTest.keyClick(window.preview_view, Qt.Key.Key_F7)
    assert window.ws.preview_index() == 3


def test_search_preedit_keeps_enter_for_input_method(window, qapp, monkeypatch):
    from PyQt6.QtGui import QInputMethodEvent
    confirmed = []
    monkeypatch.setattr(window.search_bar, 'enter', lambda: confirmed.append(True))
    field = window.search_bar.input
    QApplication.sendEvent(field, QInputMethodEvent('拼音', []))
    QTest.keyClick(field, Qt.Key.Key_Return)
    assert confirmed == []
    QApplication.sendEvent(field, QInputMethodEvent('', []))
    QTest.keyClick(field, Qt.Key.Key_Return)
    assert confirmed == [True]


def test_editor_conflicts_transfer_cancel_and_stable_rows(window, qapp):
    dialog = ShortcutDialog(window.shortcut_manager, window)
    dialog.search.setText('保存')
    dialog.table.selectRow(dialog._row_by_id['file.save'])
    dialog._editors[0].setKeySequence(QKeySequence('Ctrl+O'))
    assert not dialog.save_button.isEnabled()
    assert dialog.selected_conflicts()
    dialog.transfer_binding()
    assert dialog.draft['file.open'] == []
    assert dialog.save_button.isEnabled()
    row = dialog._row_by_id['file.save']
    dialog.remove_binding(0)
    assert dialog._row_by_id['file.save'] == row
    assert dialog.table.item(row, 1).text() == '未设置'
    dialog.restore_selected()
    assert dialog.draft['file.save'] == ['Ctrl+S']
    dialog.reject()
    assert window.shortcut_manager.values()['file.open'] == ['Ctrl+O']
    dialog.deleteLater()


def test_dialog_import_export_roundtrip_uses_saved_values(window, qapp, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog, QMessageBox
    source, exported = tmp_path / 'import.json', tmp_path / 'export.json'
    source.write_text(json.dumps({'version': 1, 'overrides': {'file.save': ['F7']}}), encoding='utf-8')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(source), ''))
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(exported), ''))
    messages = []
    monkeypatch.setattr(QMessageBox, 'exec', lambda box: messages.append(box.textFormat()) or QMessageBox.StandardButton.Ok)
    dialog = ShortcutDialog(window.shortcut_manager, window)
    try:
        dialog.import_file()
        assert messages == [Qt.TextFormat.PlainText]
        assert dialog.draft['file.save'] == ['F7']
        assert window.shortcut_manager.values()['file.save'] == ['Ctrl+S']
        dialog.export_file()
        assert json.loads(exported.read_text(encoding='utf-8'))['overrides'] == {}
        assert dialog.save()
        dialog.export_file()
        assert json.loads(exported.read_text(encoding='utf-8'))['overrides']['file.save'] == ['F7']
    finally:
        dialog.reject()
        dialog.deleteLater()


def test_standalone_window_uses_same_profile_without_main_window(qapp):
    from app.wordui import WordConverterWindow
    manager = get_manager()
    values = manager.values()
    values['word.add'] = ['F7']
    assert not manager.apply(values)
    tool = WordConverterWindow()
    called = []
    tool.add_button.clicked.disconnect()
    tool.add_button.clicked.connect(lambda: called.append('add'))
    tool.show()
    tool.activateWindow()
    tool.setFocus()
    qapp.processEvents()
    try:
        assert tool.shortcut_manager is manager
        QTest.keyClick(tool, Qt.Key.Key_F7)
        assert called == ['add']
        values['word.add'] = ['F10']
        assert not manager.apply(values)
        QTest.keyClick(tool, Qt.Key.Key_F7)
        QTest.keyClick(tool, Qt.Key.Key_F10)
        assert called == ['add', 'add']
    finally:
        tool.close()
        tool.deleteLater()
        qapp.processEvents()


def test_print_window_scopes_and_old_button_binding_removed(window, qapp, tmp_path):
    from app.printing import PrintDialog
    load_generated(window, qapp, tmp_path)
    manager = window.shortcut_manager
    values = manager.values()
    values['file.open'] = values['print.fit'] = ['F7']
    assert not manager.apply(values)
    called = []
    window.open_action.triggered.disconnect()
    window.open_action.triggered.connect(lambda: called.append('main'))
    dialog = PrintDialog(window, window.ws, window.store)
    dialog.btn_fit.clicked.disconnect()
    dialog.btn_fit.clicked.connect(lambda: called.append('print'))
    dialog.show()
    dialog.activateWindow()
    dialog.setFocus()
    qapp.processEvents()
    try:
        assert dialog.btn_fit.shortcut().isEmpty(), 'the legacy button accelerator must be removed'
        QTest.keyClick(dialog, Qt.Key.Key_0, Qt.KeyboardModifier.ControlModifier)
        assert called == []
        QTest.keyClick(dialog, Qt.Key.Key_F7)
        assert called == ['print']
        expected = {key for key, spec in COMMANDS.items() if spec.owner == 'PrintDialog' and spec.target}
        assert expected <= dialog._bound_shortcut_commands
    finally:
        dialog.reject()
        dialog.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize('module,name,arguments', [
    ('annotcropui', 'AnnotationCropDialog', 'host'),
    ('colorcleanupui', 'ColorCleanupDialog', 'host'),
    ('deskewui', 'DeskewDialog', 'host'),
    ('exportdialogs', 'EncryptionDialog', 'host'),
    ('exportdialogs', 'ImageImportDialog', 'host'),
    ('exportdialogs', 'ImageExportDialog', 'range'),
    ('outputui', 'MarkdownDialog', 'host'),
    ('outputui', 'PageNumbersDialog', 'host'),
    ('pdfcompareui', 'PdfCompareDialog', 'host'),
    ('pdfcontentcompareui', 'PdfContentCompareDialog', 'host'),
    ('recentui', 'ReadingHistoryDialog', 'host'),
    ('scanwhiteui', 'WhiteningDialog', 'host'),
    ('storageui', 'StorageDialog', 'host'),
    ('watermarkui', 'WatermarkDialog', 'host'),
    ('editingui', 'NoteViewerDialog', 'note'),
    ('pagesplitui', 'PageSplitDialog', 'image'),
    ('pagetoolsui', 'PageSizeDialog', 'image'),
    ('seamstampui', 'SeamStampDialog', 'seam'),
])
def test_tool_catalog_selectors_bind_real_controls(window, qapp, tmp_path, module, name, arguments):
    import importlib
    from PyQt6.QtGui import QImage
    load_generated(window, qapp, tmp_path)
    factory = getattr(importlib.import_module('app.' + module), name)
    if arguments == 'range':
        dialog = factory(4, 0, {0}, window)
    elif arguments == 'note':
        dialog = factory(window, 'Generated note')
    elif arguments == 'image':
        image = QImage(200, 280, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        dialog = factory(window, image)
    elif arguments == 'seam':
        dialog = factory(window, {})
    else:
        dialog = factory(window)
    try:
        dialog.show()
        qapp.processEvents()
        expected = {key for key, spec in COMMANDS.items() if spec.owner == name and spec.target}
        missing = expected - dialog._bound_shortcut_commands
        assert not missing, {key: COMMANDS[key].target for key in missing}
    finally:
        dialog.reject()
        worker = getattr(dialog, 'worker', None)
        if worker is not None and hasattr(worker, 'wait'):
            worker.wait(10000)
        dialog.deleteLater()
        qapp.processEvents()
