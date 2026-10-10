from __future__ import annotations

import os
from dataclasses import dataclass

from PyQt6.QtGui import QUndoCommand, QUndoStack


@dataclass(frozen=True)
class PageRef:
    source_id: str
    source_path: str
    source_page: int
    rotation: int = 0

    def rotated(self, delta: int) -> PageRef:
        return PageRef(
            self.source_id,
            self.source_path,
            self.source_page,
            (self.rotation + delta) % 360,
        )


def parse_page_range(spec: str, count: int) -> list[int]:
    """1-based workspace order, e.g. '1-3,5' -> [0, 1, 2, 4]."""
    return [i for seg in parse_range_segments(spec, count) for i in seg]


def drop_target(src: int, dest: int, count: int) -> int:
    """Drop onto item dest (-1 = after last). Result is move_page dst; never grows the list."""
    if count <= 0:
        return 0
    src = max(0, min(src, count - 1))
    if dest < 0 or dest >= count:
        dest = count - 1
    return max(0, min(dest, count - 1))


def parse_range_segments(spec: str, count: int) -> list[list[int]]:
    """Each comma-separated part is one group: '1-3,5' -> [[0, 1, 2], [4]]."""
    text = spec.strip()
    if not text:
        raise ValueError("请输入页码范围，例如 1-3,5")
    if count < 1:
        raise ValueError("工作区没有页面")
    segments: list[list[int]] = []
    for raw in text.split(","):
        part = raw.strip()
        if not part:
            continue
        try:
            if "-" in part:
                a, b = part.split("-", 1)
                start, stop = int(a.strip()), int(b.strip())
            else:
                start = stop = int(part)
        except ValueError as exc:
            raise ValueError(f"无法识别页码「{part}」") from exc
        if start < 1 or stop < 1 or start > count or stop > count:
            raise ValueError(f"页码 {start}-{stop} 超出当前 1–{count} 页")
        if start > stop:
            raise ValueError(f"页码范围 {start}-{stop} 无效")
        segments.append(list(range(start - 1, stop)))
    if not segments:
        raise ValueError("请输入页码范围，例如 1-3,5")
    return segments


class _StateCommand(QUndoCommand):
    def __init__(
        self,
        workspace: Workspace,
        before: tuple[tuple[PageRef, ...], str | None],
        after: tuple[tuple[PageRef, ...], str | None],
        text: str,
    ) -> None:
        super().__init__(text)
        self._ws = workspace
        self._before = before
        self._after = after

    def redo(self) -> None:
        self._ws._restore(*self._after)

    def undo(self) -> None:
        self._ws._restore(*self._before)

    def sources(self) -> set[str]:
        return {p.source_id for p in self._before[0]} | {
            p.source_id for p in self._after[0]
        }


class Workspace:
    def __init__(self) -> None:
        self.pages: list[PageRef] = []
        self.path: str | None = None
        self.select_all = False
        self.selected_index: int | None = None
        self.selected: set[int] = set()
        self.last_preview_index: int | None = None
        self.last_error: str | None = None
        self.undo_stack = QUndoStack()
        self._saved: tuple[tuple[PageRef, ...], str | None] = ((), None)
        self.protected_paths: set[str] = set()
        self._dirty_pages = None
        self._dirty_saved = None
        self._dirty_path = None
        self._dirty_value = False

    def _fingerprint(self) -> tuple[tuple[PageRef, ...], str | None]:
        return (tuple(self.pages), self.path)

    @property
    def dirty(self) -> bool:
        # 页面操作始终替换列表，选择和翻页不会改变内容。只在内容或保存状态变化后比较。
        if self.pages is not self._dirty_pages or self._saved is not self._dirty_saved or self.path != self._dirty_path:
            self._dirty_value = self._fingerprint() != self._saved
            self._dirty_pages = self.pages
            self._dirty_saved = self._saved
            self._dirty_path = self.path
        return self._dirty_value

    def can_save(self) -> bool:
        return len(self.pages) >= 1

    def preview_index(self) -> int | None:
        if not self.pages:
            return None
        if self.last_preview_index is not None and 0 <= self.last_preview_index < len(self.pages):
            return self.last_preview_index
        return 0

    def target_indices(self) -> list[int]:
        if not self.pages:
            return []
        if self.select_all:
            return list(range(len(self.pages)))
        return sorted(i for i in self.selected if 0 <= i < len(self.pages))

    def extract_indices(self, range_text: str) -> list[int]:
        text = range_text.strip()
        if text:
            return parse_page_range(text, len(self.pages))
        chosen = self.target_indices()
        if chosen:
            return chosen
        raise ValueError("请先选择一页、加选多页、全选，或输入页码范围")

    def mark_saved(self, path: str | None = None) -> None:
        if path is not None:
            self.path = path
        self._saved = self._fingerprint()
        self.last_error = None

    def rebase_to_saved_file(self, path: str, pages: list[PageRef]) -> None:
        self.pages = list(pages)
        self.path = path
        self.protected_paths = set()
        self._clamp_selection()
        self._saved = self._fingerprint()
        self.last_error = None

    def reset(self) -> None:
        self.pages = []
        self.path = None
        self.select_all = False
        self.selected_index = None
        self.selected = set()
        self.last_preview_index = None
        self.last_error = None
        self.undo_stack.clear()
        self._saved = ((), None)
        self.protected_paths = set()

    def open_replace(self, pages: list[PageRef], path: str) -> None:
        self.pages = list(pages)
        self.path = path
        self.select_all = False
        self.selected_index = 0 if pages else None
        self.selected = {0} if pages else set()
        self.last_preview_index = self.selected_index
        self.last_error = None
        self.undo_stack.clear()
        self._saved = self._fingerprint()
        self.protected_paths = set()

    def blocks_save_to(self, dest: str) -> bool:
        if self.path is not None:
            return False
        if not dest:
            return False
        return os.path.normcase(os.path.abspath(dest)) in self.protected_paths

    def blocks_export_to(self, dest: str) -> bool:
        """抽取和拆分写出的是另外的文件，落到当前文档或源文件上就是毁原件。"""
        if not dest:
            return False
        target = os.path.normcase(os.path.abspath(dest))
        if self.path and os.path.normcase(os.path.abspath(self.path)) == target:
            return True
        return target in self.protected_paths

    def live_sources(self) -> set[str]:
        """工作区页面和撤销栈都还可能用到的源文档，回收时这两边都不能碰。"""
        ids = {p.source_id for p in self.pages}
        for i in range(self.undo_stack.count()):
            command = self.undo_stack.command(i)
            if isinstance(command, _StateCommand):
                ids |= command.sources()
        return ids

    def select_index(self, index: int) -> None:
        if not self.pages:
            return
        index = max(0, min(index, len(self.pages) - 1))
        self.select_all = False
        self.selected_index = index
        self.selected = {index}
        self.last_preview_index = index

    def set_selection(self, rows: list[int] | set[int], current: int | None = None) -> None:
        n = len(self.pages)
        self.selected = {r for r in rows if 0 <= r < n}
        self.select_all = n > 0 and len(self.selected) == n
        if current is not None and current in self.selected:
            self.selected_index = current
            self.last_preview_index = current
        elif self.selected:
            self.selected_index = min(self.selected)
            self.last_preview_index = self.selected_index
        else:
            self.selected_index = None

    def select_every_page(self) -> None:
        if not self.pages:
            return
        self.select_all = True
        self.selected = set(range(len(self.pages)))
        if self.last_preview_index is None:
            self.last_preview_index = 0
        if self.selected_index is None:
            self.selected_index = self.last_preview_index

    def _restore(self, pages: tuple[PageRef, ...], path: str | None) -> None:
        self.pages = list(pages)
        self.path = path
        # 保护名单只由「当前有没有落盘路径」和页面来源决定，必须跟着一起还原：
        # 保存成功后名单会被清空，撤销回到合并态时若不同步重算，就能存回源文件了。
        self.protected_paths = (
            set()
            if path is not None
            else {
                os.path.normcase(os.path.abspath(p.source_path))
                for p in self.pages
                if p.source_path
            }
        )
        self._clamp_selection()

    def _clamp_selection(self) -> None:
        n = len(self.pages)
        if n == 0:
            self.select_all = False
            self.selected_index = None
            self.selected = set()
            self.last_preview_index = None
            return
        self.selected = {i for i in self.selected if 0 <= i < n}
        if self.selected_index is not None:
            self.selected_index = min(self.selected_index, n - 1)
            self.selected.add(self.selected_index)
        elif self.selected:
            self.selected_index = min(self.selected)
        if self.last_preview_index is not None:
            self.last_preview_index = min(self.last_preview_index, n - 1)
        elif self.selected_index is not None:
            self.last_preview_index = self.selected_index
        self.select_all = len(self.selected) == n

    def _push(self, after_pages: list[PageRef], after_path: str | None, text: str) -> None:
        before = (tuple(self.pages), self.path)
        after = (tuple(after_pages), after_path)
        if before == after:
            return
        self.undo_stack.push(_StateCommand(self, before, after, text))

    def rotate(self, delta: int) -> None:
        targets = set(self.target_indices())
        if not targets:
            return
        after = [
            page.rotated(delta) if i in targets else page
            for i, page in enumerate(self.pages)
        ]
        self._push(after, self.path, "旋转")

    def delete_selected(self) -> None:
        targets = set(self.target_indices())
        if not targets:
            return
        after = [page for i, page in enumerate(self.pages) if i not in targets]
        keep = min(targets)
        self._push(after, self.path, "删除页面")
        if after:
            nxt = min(keep, len(after) - 1)
            self.selected = {nxt}
            self.selected_index = nxt
            self.last_preview_index = nxt
            self.select_all = False
        else:
            self.selected = set()
            self.selected_index = None
            self.last_preview_index = None
            self.select_all = False

    def move_page(self, src: int, dst: int) -> None:
        n = len(self.pages)
        dst = drop_target(src, dst, n)
        if src < 0 or src >= n or src == dst:
            return
        after = list(self.pages)
        page = after.pop(src)
        after.insert(dst, page)
        self._push(after, self.path, "重排")
        self.select_index(dst)

    def merge_pages(self, incoming: list[PageRef]) -> None:
        if not incoming:
            return
        after = list(self.pages) + list(incoming)
        self.protected_paths = {
            os.path.normcase(os.path.abspath(p.source_path))
            for p in after
            if p.source_path
        }
        self._push(after, None, "合并")

    def load_first_or_merge(self, incoming: list[PageRef], path: str) -> None:
        if not self.pages:
            self.open_replace(incoming, path)
        else:
            self.merge_pages(incoming)
