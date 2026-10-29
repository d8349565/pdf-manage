"""Reader history, tools and shortcut behavior with generated files and isolated settings."""
import os

import pymupdf as p
import pytest
from PyQt6 import sip
from PyQt6.QtCore import QPoint, QPointF, QSettings, QThread, Qt
from PyQt6.QtGui import QDesktopServices, QKeySequence
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit

from app.readinghistory import ReadingHistory
from app.readershortcuts import ReadingShortcutsDialog
from app.recentui import ReadingHistoryDialog
from app.ui import MainWindow
from app import ui


@pytest.fixture(scope='session')
def app():
    return QApplication.instance() or QApplication([])


def generated(path, pages=6):
    with p.open() as doc:
        for number in range(pages):
            page = doc.new_page(width=300, height=600)
            page.insert_text((30, 60), f'Generated reader page {number + 1}')
        page = doc[0]
        page.insert_link(dict(kind=p.LINK_URI, **{'from': p.Rect(25, 40, 220, 70)},
                              uri='https://example.invalid/generated-only'))
        doc.save(path)
    return str(path)


def dispose(window):
    if sip.isdeleted(window):
        return
    for tab in window._tabs:
        tab.ws.reset()
    window.close()
    QApplication.processEvents()


@pytest.fixture
def reader(app, tmp_path):
    window = MainWindow()
    window.resize(1200, 850)
    window.show()
    window.activateWindow()
    assert window.open_files_in_tabs([generated(tmp_path / 'generated.pdf')])
    app.processEvents()
    yield window
    dispose(window)


def test_history_is_bounded_deduplicated_and_checks_missing_off_the_ui_thread(app, tmp_path, monkeypatch):
    settings = QSettings(str(tmp_path / 'history.ini'), QSettings.Format.IniFormat)
    history = ReadingHistory(settings)
    paths = [str(tmp_path / f'generated-{number}.pdf') for number in range(51)]
    for path in paths:
        history.touch(path)
    assert len(history.entries) == 50
    assert history.find(paths[0]) is None
    history.remember(paths[10], 4, .35)
    history.touch(paths[10])
    assert len(history.entries) == 50 and history.entries[0]['page'] == 4
    reopened = ReadingHistory(settings)
    assert reopened.entries == history.entries
    existing = generated(tmp_path / 'exists.pdf', 1)
    unavailable = str(tmp_path / 'temporarily-unavailable.pdf')
    share = r'\\unavailable.invalid\generated\document.pdf'
    history.remove(paths)
    for path in (existing, paths[0], unavailable, share):
        history.touch(path)
    original_stat = os.stat
    visited = []

    def stat(path, *args, **kwargs):
        visited.append(QThread.currentThread() != app.thread())
        if str(path) == unavailable:
            raise PermissionError('Generated unavailable-path case')
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr('app.readinghistory.os.stat', stat)
    history.check_missing()
    for _ in range(100):
        app.processEvents()
        if not history._checking:
            break
        QTest.qWait(10)
    assert not history._checking
    assert history.find(paths[0]) is None
    assert history.find(existing) and history.find(unavailable) and history.find(share)
    assert visited and all(visited)


def test_home_shows_ten_records_and_management_removes_only_records(reader, tmp_path, app):
    path = reader.ws.path
    for number in range(12):
        reader.reading_history.touch(generated(tmp_path / f'other-{number}.pdf', 1))
    reader.close_current_file()
    app.processEvents()
    assert reader.preview_empty.isVisible()
    assert reader.recent_files.files.count() == 10
    dialog = ReadingHistoryDialog(reader)
    assert dialog.files.count() == 13
    dialog.files.setCurrentRow(12)
    dialog.remove_selected()
    assert reader.reading_history.find(path) is None
    assert os.path.isfile(path)
    dialog.deleteLater()


def test_recent_reopen_and_opt_in_resume_keep_last_page_without_overriding_explicit_open(app, tmp_path):
    first = generated(tmp_path / 'first.pdf')
    second = generated(tmp_path / 'second.pdf')
    window = MainWindow()
    window.show()
    assert window.open_files_in_tabs([first])
    window.go_to_page(4)
    window.recent_files.resume.setChecked(True)
    window.close_current_file()
    assert window.open_recent_file(first)
    assert window.ws.preview_index() == 3
    window.close()
    app.processEvents()
    resumed = MainWindow()
    resumed.show()
    app.processEvents()
    assert resumed.ws.path == first and resumed.ws.preview_index() == 3
    resumed.close()
    app.processEvents()
    explicit = MainWindow(opening_paths=[second])
    explicit.show()
    app.processEvents()
    assert explicit.ws.path is None
    explicit.open_startup_files([second])
    assert explicit.ws.path == second
    dispose(explicit)


def test_shortcut_conflicts_focus_space_paging_and_restart_persistence(reader, app):
    controller = reader.reading_shortcuts
    values = controller.values()
    assert values['mode'] == 'F6' and values['read'] == values['arrange'] == ''
    assert controller.error(dict(values, next='Ctrl+S'))
    assert controller.error(dict(values, next='H'))
    dialog = ReadingShortcutsDialog(controller)
    dialog.editors['next'].setKeySequence(QKeySequence('Ctrl+S'))
    assert not dialog.save_button.isEnabled() and dialog.feedback.text()
    dialog.restore_defaults()
    assert dialog.save_button.isEnabled()
    dialog.show()
    dialog.editors['mode'].setFocus()
    app.processEvents()
    QTest.keyClick(dialog.editors['mode'], Qt.Key.Key_F7)
    assert dialog.editors['mode'].keySequence() == QKeySequence('F7')
    dialog.close()
    dialog.deleteLater()
    assert controller.apply(dict(values, next='Space')) == ''
    view = reader.preview_view
    view.setFocus()
    reader.activateWindow()
    app.processEvents()
    QTest.keyClick(view, Qt.Key.Key_Space)
    assert reader.ws.preview_index() == 1
    assert not reader.reader_interaction.panning
    editor = QLineEdit(view.viewport())
    editor.show()
    editor.setFocus()
    app.processEvents()
    QTest.keyClicks(editor, 'v h two words')
    assert editor.text() == 'v h two words'
    assert reader.ws.preview_index() == 1 and reader._reading_tool == 'select'
    restarted = MainWindow()
    assert restarted.reading_shortcuts.values()['next'] == 'Space'
    assert restarted.reading_shortcuts.values()['mode'] == 'F6'
    dispose(restarted)


def test_mode_switch_leaves_fullscreen_and_tools_do_not_change_document(reader, app):
    before = tuple(reader.ws.pages)
    reader.immersive.fullscreen_action.setChecked(True)
    reader.activateWindow()
    QApplication.setActiveWindow(reader)
    reader.preview_view.setFocus()
    app.processEvents()
    QTest.keyClick(reader.preview_view, Qt.Key.Key_F6)
    app.processEvents()
    assert reader.arrange_view.isVisible() and not reader.immersive.fullscreen
    QTest.keyClick(reader.arrange_view, Qt.Key.Key_F6)
    app.processEvents()
    assert reader.preview_view.isVisible()
    QTest.keyClick(reader.preview_view, Qt.Key.Key_H)
    assert reader._reading_tool == 'hand'
    QTest.keyClick(reader.preview_view, Qt.Key.Key_V)
    assert reader._reading_tool == 'select'
    assert tuple(reader.ws.pages) == before and not reader.ws.dirty


def test_hand_drag_reuses_pan_does_not_request_text_and_keeps_links(reader, app, monkeypatch):
    view = reader.preview_view
    reader.store.render_qimage(reader.ws.pages[0], 600)
    reader.set_reading_tool('hand')
    assert not reader.text_selection.active()
    opened = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: opened.append(url.toString()) or True)
    app.processEvents()
    position = view.mapFromScene(view.page_rects[0].topLeft() + QPointF(80, 55))
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=position)
    assert opened == ['https://example.invalid/generated-only']
    reader._set_zoom(4)
    app.processEvents()
    view.setFocus()
    original = view.verticalScrollBar().value()
    position = QPoint(180, 180)
    requests = []
    original_request = reader.rendering.request

    def request(*args, **kwargs):
        requests.append(kwargs.get('kind'))
        return original_request(*args, **kwargs)

    monkeypatch.setattr(reader.rendering, 'request', request)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=position)
    QTest.mouseMove(view.viewport(), position - QPoint(20, 50))
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=position - QPoint(20, 50))
    assert view.verticalScrollBar().value() > original
    assert not reader.reader_interaction.panning
    assert len(opened) == 1 and 'text' not in requests
    reopened = MainWindow()
    reopened.show()
    app.processEvents()
    assert reopened._reading_tool == 'hand'
    assert reopened.preview_empty.isVisible()
    dispose(reopened)
    reader.reader_tool_actions['select'].trigger()
    assert reader.text_selection.active() and reader.reader_tool_actions['select'].isChecked()


def test_cleared_history_stays_clear_after_window_close(reader, app):
    settings = reader._window_settings
    reader.reading_history.remove([entry['path'] for entry in reader.reading_history.entries])
    reader.close()
    app.processEvents()
    assert ReadingHistory(settings).entries == []


def test_home_with_fifty_records_does_not_load_or_render_documents(app, tmp_path, monkeypatch):
    history = ReadingHistory(ui.preferences(ui.QSettings))
    for number in range(50):
        path = tmp_path / f'not-opened-{number}.pdf'
        path.touch()
        history.touch(str(path))

    def unexpected(*args, **kwargs):
        raise AssertionError('Home history must not open or render document contents')

    monkeypatch.setattr(ui.PdfStore, 'load', unexpected)
    monkeypatch.setattr(ui.PdfStore, 'render_qimage', unexpected)
    window = MainWindow()
    window.show()
    app.processEvents()
    assert window.preview_empty.isVisible() and not window.ws.pages
    assert window.recent_files.files.count() == 10
    assert not window._history_timer.isActive()
    dispose(window)
