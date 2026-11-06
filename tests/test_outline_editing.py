"""Bookmark editing regressions using generated PDFs and isolated preferences."""
import pymupdf as p
import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QInputDialog, QMenu

from app.pdfio import WriteOptions
from app.ui import MainWindow


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def reader(qapp, tmp_path):
    path = tmp_path / 'bookmarks.pdf'
    with p.open() as doc:
        for _ in range(4):
            doc.new_page(width=360, height=520)
        doc.set_toc([[1, 'A', 1], [2, 'A.1', 1], [3, 'A.1.a', 2],
                     [2, 'A.2', 3], [1, 'B', 4], [2, 'B.1', 4]])
        doc.save(path)
    window = MainWindow()
    window.ws.open_replace(window.store.load(str(path)), str(path))
    window.refresh()
    window.open_outline()
    yield window
    window.ws.reset()
    window.close()
    window.deleteLater()
    qapp.processEvents()


def rows(reader):
    return [(entry.level, entry.title) for entry in reader.outline_panel.entries]


def select(reader, title):
    panel = reader.outline_panel
    panel.tree.setCurrentItem(next(item for item in panel._items if item.text(0) == title))


def test_add_inserts_sibling_after_selected_subtree_and_is_undoable(reader, monkeypatch):
    before = tuple(reader.outline_panel.entries)
    reader.ws.select_index(1)
    select(reader, 'A.1')
    monkeypatch.setattr(QInputDialog, 'getText', lambda *args, **kwargs: ('Inserted', True))
    reader._shortcut_actions['outline.add'].trigger()
    assert rows(reader) == [(1, 'A'), (2, 'A.1'), (3, 'A.1.a'), (2, 'Inserted'),
                            (2, 'A.2'), (1, 'B'), (2, 'B.1')]
    assert reader.ws.outline[3].target_id == reader.ws.pages[1].page_id
    assert reader.outline_panel.tree.currentItem().text(0) == 'Inserted'
    assert reader.ws.dirty
    reader.ws.undo_stack.undo()
    reader.refresh()
    assert reader.outline_panel.entries == before and not reader.ws.dirty
    reader.ws.undo_stack.redo()
    reader.refresh()
    assert rows(reader)[3] == (2, 'Inserted')


def test_add_child_uses_selected_parent_and_saves_hierarchy(reader, monkeypatch, tmp_path):
    select(reader, 'A')
    monkeypatch.setattr(QInputDialog, 'getText', lambda *args, **kwargs: ('New child', True))
    reader._shortcut_actions['outline.add_child'].trigger()
    assert rows(reader) == [(1, 'A'), (2, 'A.1'), (3, 'A.1.a'), (2, 'A.2'),
                            (2, 'New child'), (1, 'B'), (2, 'B.1')]
    output = tmp_path / 'edited.pdf'
    reader.store.write(reader.ws.pages, str(output), options=WriteOptions(outline=reader.ws.outline))
    with p.open(output) as doc:
        assert doc.get_toc() == [[1, 'A', 1], [2, 'A.1', 1], [3, 'A.1.a', 2],
                                 [2, 'A.2', 3], [2, 'New child', 1], [1, 'B', 4], [2, 'B.1', 4]]


def test_move_swaps_whole_sibling_subtrees_and_preserves_destinations(reader):
    before = tuple(reader.outline_panel.entries)
    select(reader, 'A')
    reader._shortcut_actions['outline.move_down'].trigger()
    assert tuple(reader.outline_panel.entries) == before[4:] + before[:4]
    assert reader.outline_panel.tree.currentItem().text(0) == 'A'
    reader._shortcut_actions['outline.move_up'].trigger()
    assert reader.outline_panel.entries == before
    reader.ws.undo_stack.undo()
    reader.refresh()
    assert tuple(reader.outline_panel.entries) == before[4:] + before[:4]
    reader.ws.undo_stack.undo()
    reader.refresh()
    assert reader.outline_panel.entries == before and not reader.ws.dirty


def test_move_nested_branch_stays_within_parent(reader):
    before = tuple(reader.outline_panel.entries)
    select(reader, 'A.1')
    reader._shortcut_actions['outline.move_down'].trigger()
    assert reader.outline_panel.entries == (before[0], before[3], *before[1:3], *before[4:])
    assert reader.outline_panel.tree.currentItem().text(0) == 'A.1'
    reader._shortcut_actions['outline.move_up'].trigger()
    assert reader.outline_panel.entries == before


def test_raising_child_keeps_remaining_siblings_under_original_parent(reader):
    before = tuple(reader.outline_panel.entries)
    select(reader, 'A.1')
    reader._shortcut_actions['outline.raise'].trigger()
    assert rows(reader) == [(1, 'A'), (2, 'A.2'), (1, 'A.1'), (2, 'A.1.a'),
                            (1, 'B'), (2, 'B.1')]
    assert reader.outline_panel.tree.currentItem().text(0) == 'A.1'
    reader.ws.undo_stack.undo()
    reader.refresh()
    assert reader.outline_panel.entries == before


def test_indent_moves_selected_branch_under_previous_sibling(reader):
    before = tuple(reader.outline_panel.entries)
    select(reader, 'B')
    reader._shortcut_actions['outline.child'].trigger()
    assert rows(reader) == [(1, 'A'), (2, 'A.1'), (3, 'A.1.a'), (2, 'A.2'),
                            (2, 'B'), (3, 'B.1')]
    assert reader.outline_panel.tree.currentItem().text(0) == 'B'
    assert [(entry.target_id, entry.destination) for entry in reader.ws.outline] == [
        (entry.target_id, entry.destination) for entry in before]


@pytest.mark.parametrize('title, up, down, child, promote', [
    ('A', False, True, False, False), ('A.1', False, True, False, True),
    ('A.1.a', False, False, False, True), ('A.2', True, False, True, True),
    ('B', True, False, True, False), ('B.1', False, False, False, True),
])
def test_actions_respect_sibling_boundaries(reader, title, up, down, child, promote):
    select(reader, title)
    for key, enabled in [('move_up', up), ('move_down', down), ('child', child), ('raise', promote)]:
        assert reader._shortcut_actions['outline.' + key].isEnabled() == enabled
    before = tuple(reader.outline_panel.entries)
    reader.outline_panel.move(-1 if not up else 1)
    assert reader.outline_panel.entries == before
    assert not reader.ws.dirty


@pytest.mark.parametrize('empty', [False, True])
def test_add_without_selection_appends_root_and_child_is_unavailable(reader, monkeypatch, empty):
    panel = reader.outline_panel
    if empty:
        reader.ws.set_outline(())
        reader.refresh()
    panel.tree.setCurrentItem(None)
    before = rows(reader)
    assert not panel.add_child_button.isEnabled()
    assert not reader._shortcut_actions['outline.add_child'].isEnabled()
    monkeypatch.setattr(QInputDialog, 'getText', lambda *args, **kwargs: ('Root', True))
    panel.add_button.click()
    assert rows(reader) == before + [(1, 'Root')]


@pytest.mark.parametrize('answer', [('Cancelled', False), ('   ', True)])
def test_cancel_or_empty_name_leaves_bookmarks_unchanged(reader, monkeypatch, answer):
    before = tuple(reader.outline_panel.entries)
    select(reader, 'A')
    monkeypatch.setattr(QInputDialog, 'getText', lambda *args, **kwargs: answer)
    reader.outline_panel.add_child_button.click()
    assert reader.outline_panel.entries == before
    assert not reader.ws.dirty


def test_insertion_uses_selection_at_dialog_open_even_if_reading_follow_runs(reader, monkeypatch):
    panel = reader.outline_panel
    reader.show()
    QApplication.processEvents()
    reader.ws.select_index(3)
    select(reader, 'A.1')

    def answer(*args, **kwargs):
        panel.sync_position(force=True)
        assert panel.tree.currentItem().text(0) in ('B', 'B.1')
        return 'Child', True

    monkeypatch.setattr(QInputDialog, 'getText', answer)
    panel.add_child_button.click()
    assert rows(reader)[3] == (3, 'Child')
    assert panel.tree.currentItem().text(0) == 'Child'


def test_move_can_use_configured_keyboard_shortcut(reader):
    reader.show()
    reader.activateWindow()
    QApplication.processEvents()
    values = reader.shortcut_manager.values()
    values['outline.move_down'] = ['Ctrl+Alt+Down']
    assert reader.shortcut_manager.apply(values) == ''
    select(reader, 'A')
    reader.outline_panel.tree.setFocus()
    QTest.keyClick(reader.outline_panel.tree, Qt.Key.Key_Down,
                   Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
    assert rows(reader)[0] == (1, 'B')
    assert reader.outline_panel.tree.currentItem().text(0) == 'A'


def test_context_menu_targets_clicked_bookmark(reader, monkeypatch):
    reader.show()
    QApplication.processEvents()
    panel = reader.outline_panel
    select(reader, 'A')
    menus = []
    monkeypatch.setattr(QMenu, 'exec', lambda menu, pos: menus.append(menu))
    item = next(item for item in panel._items if item.text(0) == 'A.2')
    panel.context_menu(panel.tree.visualItemRect(item).center())
    assert panel.tree.currentItem() is item
    actions = {action.text(): action for action in menus[0].actions()}
    assert actions['添加子书签'].isEnabled() and actions['上移'].isEnabled()
    assert not actions['下移'].isEnabled()
    assert not reader.ws.dirty


def test_read_only_menu_and_buttons_block_edits_but_allow_expansion(reader, monkeypatch):
    panel = reader.outline_panel
    select(reader, 'A')
    menus = []
    monkeypatch.setattr(QMenu, 'exec', lambda menu, pos: menus.append(menu))
    monkeypatch.setattr(reader.store, 'operation_reason', lambda *args: 'Generated read-only document')
    panel.menu(QPoint(0, 0))
    assert not panel.add_button.isEnabled() and not panel.add_child_button.isEnabled()
    actions = {action.text(): action for action in menus[0].actions() if not action.isSeparator()}
    assert actions['展开全部'].isEnabled() and actions['折叠全部'].isEnabled()
    assert all(not action.isEnabled() for title, action in actions.items() if title not in ('展开全部', '折叠全部'))
    assert not reader.ws.dirty


def test_controls_can_refresh_while_bookmarks_are_being_loaded(reader, monkeypatch):
    from app import editingui
    run_operation = editingui.run_operation

    def load_with_control_refresh(host, title, work, **kwargs):
        host._sync_controls()
        return run_operation(host, title, work, **kwargs)

    monkeypatch.setattr(editingui, 'run_operation', load_with_control_refresh)
    reader.outline_panel._key = None
    reader.outline_panel.refresh()
    assert rows(reader) == [(1, 'A'), (2, 'A.1'), (3, 'A.1.a'), (2, 'A.2'), (1, 'B'), (2, 'B.1')]
    assert reader.outline_panel.add_button.isEnabled()
