"""Portable isolation uses generated folders and an in-memory registry only."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from app import runtime, storage
from app.contextmenus import ContextMenus, OWNER, EDITION, registration_plan
from app.windows_integration import PDF_MERGE_CLSID, association_entries


class MemoryRegistry:
    def __init__(self):
        self.data = {}
        self.fail_after = None
        self.notifications = 0

    def get(self, path, name=''):
        return self.data.get(path.lower(), {}).get(name)

    def set(self, path, name, value):
        if self.fail_after is not None:
            self.fail_after -= 1
            if self.fail_after == 0:
                self.fail_after = None
                raise PermissionError('Generated write denial')
        self.data.setdefault(path.lower(), {})[name] = value

    def snapshot(self, root):
        root = root.lower()
        found = {path[len(root):].lstrip('\\'): deepcopy(values) for path, values in self.data.items()
                 if path == root or path.startswith(root + '\\')}
        return found or None

    def delete(self, root):
        root = root.lower()
        for path in list(self.data):
            if path == root or path.startswith(root + '\\'):
                del self.data[path]

    def restore(self, root, snapshot):
        self.delete(root)
        for relative, values in (snapshot or {}).items():
            path = root + ('\\' + relative if relative else '')
            self.data[path.lower()] = deepcopy(values)

    def notify(self):
        self.notifications += 1


def executable(folder, portable=False):
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / 'ZhiXuPDF.exe'
    exe.write_bytes(b'Generated placeholder, never executed')
    if portable:
        (folder / runtime.PORTABLE_MARKER).write_text('[Portable]\nEnabled=true\n', encoding='utf-8')
    return exe


def manager(exe, registry):
    return ContextMenus(exe, registry=registry, state_file=exe.parent / 'Data' / 'context-menu.json')


def test_portable_preferences_and_data_do_not_read_the_installed_profile(tmp_path, monkeypatch):
    installed = runtime.preferences()
    installed.setValue('probe/edition', 'installed')
    installed.sync()
    exe = executable(tmp_path / '绿色 版本', True)
    monkeypatch.setattr(runtime, 'application_directory', lambda: exe.parent)
    monkeypatch.setattr(storage, '_settings', lambda: runtime.preferences())
    runtime.prepare_profile()
    portable = runtime.preferences()
    assert not portable.contains('probe/edition')
    portable.setValue('probe/edition', 'portable')
    portable.sync()
    assert installed.value('probe/edition') == 'installed'
    assert Path(portable.fileName()).is_relative_to(exe.parent)
    assert storage.data_directory() == exe.parent / 'Data'
    assert storage.directory('cache') == exe.parent / 'Data/cache'
    assert storage.directory('ocr') == exe.parent / 'Data/ocr'
    assert portable.fallbacksEnabled() is False


def test_portable_relative_paths_follow_the_folder_when_moved(tmp_path, monkeypatch):
    exe = executable(tmp_path / 'before', True)
    current = [exe.parent]
    monkeypatch.setattr(runtime, 'application_directory', lambda: current[0])
    monkeypatch.setattr(storage, '_settings', lambda: runtime.preferences())
    runtime.prepare_profile()
    storage.save_directory('cache', exe.parent / 'Data/custom-cache')
    storage.save_directory('ocr', exe.parent / 'Data/models')
    storage.save_directory('output', exe.parent / 'exports')
    settings = runtime.preferences()
    assert settings.value('storage/cache') == 'portable:Data/custom-cache'
    settings.sync()
    del settings
    target = tmp_path / 'after'
    assert exe.parent.resolve().is_relative_to(tmp_path.resolve()) and target.resolve().is_relative_to(tmp_path.resolve())
    exe.parent.rename(target)
    current[0] = target
    assert storage.directory('cache') == target / 'Data/custom-cache'
    assert storage.directory('ocr') == target / 'Data/models'
    assert storage.output_directory() == target / 'exports'
    assert storage.previous_directories('cache') == [target / 'Data/cache']
    runtime.preferences().setValue('storage/cache', 'portable:../outside')
    with pytest.raises(storage.StorageError):
        storage.directory('cache')


def test_image_menu_upgrade_removes_single_file_commands_and_rolls_back_on_failure(tmp_path):
    registry=MemoryRegistry()
    menus=manager(executable(tmp_path/'generated-portable',True),registry)
    menus.enable()
    image_roots=[root for root in menus.plan.trees if root.endswith('.ImagePDF')]
    for root in image_roots:
        registry.delete(root+'\\DropTarget')
        registry.set(root,'MultiSelectModel','Single')
        registry.set(root+'\\command','', 'generated old command')
    before=deepcopy(registry.data)
    registry.fail_after=1
    with pytest.raises(PermissionError):menus.repair()
    assert registry.data==before
    menus.repair()
    assert menus.status()=='enabled'
    assert all(registry.snapshot(root+'\\command') is None for root in image_roots)
    menus.remove()
    assert menus.status()=='off' and not registry.data


def test_installed_and_portable_plans_and_open_with_entries_are_disjoint(tmp_path):
    installed = executable(tmp_path / 'installed')
    portable = executable(tmp_path / 'portable', True)
    first, second = registration_plan(installed), registration_plan(portable)
    assert first.clsid != second.clsid and first.token != second.token
    assert second.clsid != PDF_MERGE_CLSID
    assert set(first.trees).isdisjoint(second.trees)
    assert len(first.trees) == len(second.trees) == 23
    assert first.image_clsid != second.image_clsid
    assert first.image_clsid != first.clsid
    for plan in (first,second):
        image_entries=[tree for root,tree in plan.trees.items() if root.endswith('.ImagePDF')]
        assert image_entries
        assert all(tree['']['MultiSelectModel']=='Player' and 'command' not in tree
                   and tree['DropTarget']['CLSID']==plan.image_clsid for tree in image_entries)
    for plan in (first, second):
        for tree in plan.trees.values():
            assert tree[''][OWNER] == plan.executable
            assert tree[''][EDITION] == ('portable' if plan.portable else 'installed')
            if 'command' in tree:
                assert tree['command'][''].startswith('"' + plan.executable + '" ')
                assert tree['command'][''].endswith('"%1"')
    installed_entries = {(path, name) for path, name, _ in association_entries(installed)}
    portable_entries = {(path, name) for path, name, _ in association_entries(portable)}
    assert installed_entries.isdisjoint(portable_entries)
    assert all('Applications\\ZhiXuPDF.exe' not in path for path, _ in portable_entries)


def test_default_off_and_enable_repair_remove_never_change_the_other_edition(tmp_path):
    registry = MemoryRegistry()
    first = manager(executable(tmp_path / 'installed'), registry)
    second = manager(executable(tmp_path / 'portable', True), registry)
    assert first.status() == second.status() == 'off'
    assert not registry.data and not first.state_file.exists() and not second.state_file.exists()
    first.enable()
    second.enable()
    assert first.status() == second.status() == 'enabled'
    portable_before = {root: registry.snapshot(root) for root in second.plan.trees}
    root = next(root for root, tree in first.plan.trees.items() if 'command' in tree)
    registry.delete(root + '\\command')
    assert first.status() == 'repair'
    first.repair()
    assert first.status() == 'enabled'
    first.remove()
    assert first.status() == 'off' and second.status() == 'enabled'
    assert all(registry.snapshot(root) == snapshot for root, snapshot in portable_before.items())
    second.remove()
    assert not registry.data


def test_failed_enable_rolls_back_its_own_changes(tmp_path):
    registry = MemoryRegistry()
    first = manager(executable(tmp_path / 'installed'), registry)
    second = manager(executable(tmp_path / 'portable', True), registry)
    first.enable()
    original = deepcopy(registry.data)
    registry.fail_after = 10
    with pytest.raises(PermissionError):
        second.enable()
    assert registry.data == original and not second.state_file.exists()
    assert first.status() == 'enabled' and second.status() == 'off'


def test_moved_portable_repairs_and_removes_only_its_old_missing_location(tmp_path):
    registry = MemoryRegistry()
    installed = manager(executable(tmp_path / 'installed'), registry)
    original = manager(executable(tmp_path / 'before', True), registry)
    installed.enable()
    original.enable()
    target = tmp_path / 'after'
    assert original.state_file.parent.parent.resolve().is_relative_to(tmp_path.resolve())
    assert target.resolve().is_relative_to(tmp_path.resolve())
    Path(original.plan.executable).parent.rename(target)
    moved = manager(target / 'ZhiXuPDF.exe', registry)
    assert moved.status() == 'repair'
    moved.repair()
    assert moved.status() == installed.status() == 'enabled'
    assert all(registry.snapshot(root) is None for root in original.plan.trees)
    moved.remove()
    assert installed.status() == 'enabled'


def test_copied_portable_does_not_remove_the_original_portable_menus(tmp_path):
    registry = MemoryRegistry()
    first = manager(executable(tmp_path / 'original', True), registry)
    first.enable()
    target = tmp_path / 'copy'
    shutil.copytree(Path(first.plan.executable).parent, target)
    second = manager(target / 'ZhiXuPDF.exe', registry)
    assert second.status() == 'off'
    second.enable()
    assert first.status() == second.status() == 'enabled'
    second.remove()
    assert first.status() == 'enabled'


def test_unrecognized_registration_is_not_overwritten_or_removed(tmp_path):
    registry = MemoryRegistry()
    current = manager(executable(tmp_path / 'portable', True), registry)
    root = next(iter(current.plan.trees))
    registry.set(root, OWNER, str(tmp_path / 'foreign.exe'))
    registry.set(root, EDITION, 'installed')
    original = deepcopy(registry.data)
    with pytest.raises(OSError, match='其他来源'):
        current.enable()
    current.remove()
    assert registry.data == original


def test_menu_management_preserves_historical_installer_registrations(tmp_path):
    registry = MemoryRegistry()
    first = manager(executable(tmp_path / 'installed'), registry)
    second = manager(executable(tmp_path / 'portable', True), registry)
    own = r'Software\Classes\SystemFileAssociations\.doc\shell\PageArkWordToPDF'
    other = r'Software\Classes\SystemFileAssociations\.md\shell\ZhiXuMarkdownToPDF'
    registry.set(own + r'\command', '', f'"{first.plan.executable}" --word-to-pdf "%1"')
    registry.set(other + r'\command', '', '"C:\\Other\\ZhiXuPDF.exe" --markdown-to-pdf "%1"')
    old_com = 'Software\\Classes\\CLSID\\' + PDF_MERGE_CLSID
    registry.set(old_com + r'\LocalServer32', '', f'"{first.plan.executable}" --pdf-shell-server')
    old_com_snapshot = registry.snapshot(old_com)
    second.enable()
    assert registry.snapshot(own) is not None
    second.repair()
    assert registry.snapshot(old_com) == old_com_snapshot
    second.remove()
    assert registry.snapshot(old_com) == old_com_snapshot
    second.enable()
    first.remove()
    assert registry.snapshot(own) is not None and registry.snapshot(other) is not None
    assert registry.snapshot(old_com) == old_com_snapshot
    assert second.status() == 'enabled'
