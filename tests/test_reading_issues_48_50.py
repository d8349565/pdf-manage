"""Reading issue regressions using only generated pages and isolated settings."""
import pymupdf as p
import pytest
from PyQt6.QtCore import QPoint, Qt
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
