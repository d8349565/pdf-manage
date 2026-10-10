from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication

from app.workspace import PageRef, Workspace, drop_target, parse_page_range, parse_range_segments


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def _pages(*labels: str) -> list[PageRef]:
    return [PageRef("s", "a.pdf", i, 0) for i in range(len(labels))]


def test_drop_target_never_grows(qapp):
    assert drop_target(0, 3, 4) == 3
    assert drop_target(3, -1, 4) == 3
    assert drop_target(1, 8, 4) == 3
    ws = Workspace()
    ws.open_replace(_pages("a", "b", "c", "d"), "C:/tmp/a.pdf")
    ws.move_page(0, 3)
    ws.move_page(3, 1)
    ws.move_page(1, 7)
    assert len(ws.pages) == 4


def test_parse_range():
    assert parse_page_range("1-3,5", 5) == [0, 1, 2, 4]
    assert parse_range_segments("1-3,5", 5) == [[0, 1, 2], [4]]
    with pytest.raises(ValueError):
        parse_page_range("", 3)
    with pytest.raises(ValueError):
        parse_page_range("9", 3)


def test_open_first_file_has_path(qapp):
    ws = Workspace()
    ws.open_replace(_pages("a", "b"), "C:/tmp/a.pdf")
    assert ws.path.endswith("a.pdf")
    assert not ws.dirty
    assert ws.can_save()
    assert ws.selected_index == 0


def test_merge_clears_path(qapp):
    ws = Workspace()
    ws.open_replace(_pages("a"), "C:/tmp/a.pdf")
    extra = [PageRef("b", "C:/tmp/b.pdf", 0, 0)]
    ws.merge_pages(extra)
    assert ws.path is None
    assert ws.dirty
    assert len(ws.pages) == 2
    ws.undo_stack.undo()
    assert ws.path.endswith("a.pdf")
    assert len(ws.pages) == 1


def test_zero_pages_cannot_save(qapp):
    ws = Workspace()
    ws.open_replace(_pages("a", "b"), "C:/tmp/a.pdf")
    ws.select_every_page()
    ws.delete_selected()
    assert ws.pages == []
    assert ws.selected == set()
    assert ws.selected_index is None
    assert not ws.can_save()
    assert ws.dirty


def test_rotate_and_reorder_keep_path(qapp):
    ws = Workspace()
    ws.open_replace(_pages("a", "b", "c"), "C:/tmp/a.pdf")
    ws.select_index(2)
    ws.rotate(90)
    assert ws.pages[2].rotation == 90
    assert ws.path.endswith("a.pdf")
    assert ws.dirty
    ws.move_page(2, 0)
    assert ws.pages[0].source_page == 2
    ws.undo_stack.undo()
    ws.undo_stack.undo()
    assert ws.pages[2].rotation == 0
    assert not ws.dirty


def test_extract_indices_do_not_need_range_when_selected(qapp):
    ws = Workspace()
    ws.open_replace(_pages("a", "b", "c"), "C:/tmp/a.pdf")
    ws.select_index(1)
    assert ws.extract_indices("") == [1]
    ws.select_every_page()
    assert ws.extract_indices("") == [0, 1, 2]
    assert ws.extract_indices("1,3") == [0, 2]


def test_save_does_not_clear_undo(qapp):
    ws = Workspace()
    ws.open_replace(_pages("a", "b"), "C:/tmp/a.pdf")
    ws.select_index(0)
    ws.rotate(90)
    ws.mark_saved()
    assert not ws.dirty
    assert ws.undo_stack.canUndo()
    ws.undo_stack.undo()
    assert ws.pages[0].rotation == 0
    assert ws.dirty


def test_load_first_or_merge(qapp):
    ws = Workspace()
    ws.load_first_or_merge(_pages("a"), "C:/tmp/a.pdf")
    assert ws.path.endswith("a.pdf")
    ws.load_first_or_merge([PageRef("b", "C:/tmp/b.pdf", 0, 0)], "C:/tmp/b.pdf")
    assert ws.path is None
    assert len(ws.pages) == 2


def test_merge_blocks_source_save_paths(qapp, tmp_path):
    a = tmp_path / "A.pdf"
    b = tmp_path / "B.pdf"
    ws = Workspace()
    ws.open_replace([PageRef("s", str(a), 0, 0)], str(a))
    assert not ws.blocks_save_to(str(a))
    ws.merge_pages([PageRef("t", str(b), 0, 0)])
    assert ws.path is None
    assert ws.blocks_save_to(str(a))
    assert ws.blocks_save_to(str(b))
    assert not ws.blocks_save_to(str(tmp_path / "合订.pdf"))
    ws.rebase_to_saved_file(str(tmp_path / "合订.pdf"), list(ws.pages))
    assert not ws.blocks_save_to(str(a))


def test_protected_paths_come_back_with_undo(qapp, tmp_path):
    """另存为会清空保护名单；撤销回到合并态时若不同步重算，就能存回源文件了。"""
    a = tmp_path / "A.pdf"
    b = tmp_path / "B.pdf"
    ws = Workspace()
    ws.open_replace([PageRef("s", str(a), 0, 0)], str(a))
    ws.merge_pages([PageRef("t", str(b), 0, 0)])
    ws.rebase_to_saved_file(str(tmp_path / "C.pdf"), [PageRef("u", str(tmp_path / "C.pdf"), 0, 0)])
    assert not ws.protected_paths

    ws.undo_stack.undo()
    ws.undo_stack.redo()
    assert ws.path is None
    assert ws.blocks_save_to(str(a))
    assert ws.blocks_save_to(str(b))


def test_export_refuses_sources_and_the_current_document(qapp, tmp_path):
    a = tmp_path / "A.pdf"
    b = tmp_path / "B.pdf"
    other = tmp_path / "抽取.pdf"
    ws = Workspace()
    ws.open_replace([PageRef("s", str(a), 0, 0)], str(a))
    assert ws.blocks_export_to(str(a))
    assert not ws.blocks_export_to(str(other))

    ws.merge_pages([PageRef("t", str(b), 0, 0)])
    assert ws.blocks_export_to(str(a))
    assert ws.blocks_export_to(str(b))
    assert not ws.blocks_export_to(str(other))


def test_live_sources_keep_what_undo_still_points_at(qapp, tmp_path):
    a = tmp_path / "A.pdf"
    b = tmp_path / "B.pdf"
    ws = Workspace()
    ws.open_replace([PageRef("sa", str(a), 0, 0)], str(a))
    ws.merge_pages([PageRef("sb", str(b), 0, 0)])
    assert ws.live_sources() == {"sa", "sb"}

    ws.rebase_to_saved_file(str(tmp_path / "C.pdf"), [PageRef("sc", str(tmp_path / "C.pdf"), 0, 0)])
    assert {"sa", "sb"} <= ws.live_sources(), "撤销栈还指着的源不能算作可回收"

    ws.undo_stack.clear()
    assert ws.live_sources() == {"sc"}


def test_set_selection_tracks_current_and_select_all(qapp):
    """多选由视图写回来，这里守住 set_selection 对「当前页 / 全选 / 空选」的解释。"""
    ws = Workspace()
    ws.open_replace(_pages("a", "b", "c", "d"), "C:/tmp/a.pdf")
    assert ws.selected == {0}

    ws.set_selection({0, 2}, current=2)
    assert ws.selected == {0, 2}
    assert ws.target_indices() == [0, 2]
    assert ws.selected_index == 2
    assert not ws.select_all

    ws.set_selection({0, 2}, current=None)
    assert ws.selected_index == 0, "没给当前页时回落到最靠前的一页"

    ws.set_selection({0, 1, 2, 3}, current=3)
    assert ws.select_all, "选中全部页等价于全选"

    ws.set_selection({9, -1}, current=None)
    assert ws.selected == set()
    assert ws.selected_index is None
    assert ws.target_indices() == []
    assert not ws.select_all
