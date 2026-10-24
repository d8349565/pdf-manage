"""Explicit per-user Shell integration, owned by an executable path and edition."""
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os
import tempfile
import uuid

from app import APP_NAME, APP_SLUG
from app.imagecompress import IMAGE_SUFFIXES
from app.runtime import portable_root
from app.windows_integration import PDF_MERGE_CLSID

OWNER = 'ZhiXuPDF.Owner'
EDITION = 'ZhiXuPDF.Edition'
CLASSES = r'Software\Classes'


def _normalized(path):
    return os.path.normcase(str(Path(path).resolve()))


@dataclass
class MenuPlan:
    executable: str
    portable: bool
    token: str
    clsid: str
    trees: dict


def registration_plan(executable, *, portable=None):
    exe = str(Path(executable).resolve())
    portable = portable_root(exe) is not None if portable is None else portable
    edition = 'portable' if portable else 'installed'
    identity = edition + ':' + _normalized(exe)
    token = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]
    clsid = '{' + str(uuid.uuid5(uuid.UUID(PDF_MERGE_CLSID), identity)).upper() + '}'
    trees = {}
    suffix = '（绿色版）' if portable else ''
    def verb(file_type, action, title, arguments=None, merge=False):
        root = fr'{CLASSES}\{file_type}\shell\ZhiXuPDF.{edition}.{token}.{action}'
        tree = {'': {OWNER: exe, EDITION: edition, '': title + suffix,
                     'Icon': f'"{exe}",0', 'MultiSelectModel': 'Player' if merge else 'Single'}}
        if merge:
            tree['DropTarget'] = {'CLSID': clsid}
        else:
            tree['command'] = {'': f'"{exe}" {arguments} "%1"'}
        trees[root] = tree
    for extension in IMAGE_SUFFIXES:
        kind = 'SystemFileAssociations\\' + extension
        verb(kind, 'ImagePDF', '图片转 PDF', '--images-to-pdf')
        verb(kind, 'CompressImage', '压缩图片', '--compress-image')
    for extension in ('.md', '.markdown'):
        verb('SystemFileAssociations\\' + extension, 'MarkdownPDF', 'Markdown 转 PDF', '--markdown-to-pdf')
    for kind in (r'SystemFileAssociations\.doc', r'SystemFileAssociations\.docx', 'Directory'):
        verb(kind, 'WordPDF', '批量 Word 转 PDF' if kind == 'Directory' else 'Word 转 PDF', '--word-to-pdf')
    verb(r'SystemFileAssociations\.pdf', 'Split', f'用{APP_NAME}拆分', '--split-pdf')
    verb(r'SystemFileAssociations\.pdf', 'Merge', f'用{APP_NAME}合并', merge=True)
    trees[fr'{CLASSES}\CLSID\{clsid}'] = {
        '': {OWNER: exe, EDITION: edition, '': APP_NAME + ' 多选合并' + suffix},
        'LocalServer32': {'': f'"{exe}" --pdf-shell-server --context-id {clsid}'},
        'PythonCOM': {'': 'app.pdfshell.MergeDropTarget'},
    }
    return MenuPlan(exe, portable, token, clsid, trees)


class WindowsRegistry:
    """Only callers' explicit owned roots are snapshotted or changed."""
    def __init__(self):
        import winreg
        self.api = winreg

    def get(self, path, name=''):
        reg = self.api
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, path, 0, reg.KEY_READ) as key:
                return reg.QueryValueEx(key, name)[0]
        except FileNotFoundError:
            return None

    def set(self, path, name, value):
        reg = self.api
        with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, path, 0, reg.KEY_WRITE) as key:
            reg.SetValueEx(key, name, 0, reg.REG_SZ, value)

    def snapshot(self, path):
        reg = self.api
        result = {}
        def visit(full, relative):
            with reg.OpenKey(reg.HKEY_CURRENT_USER, full, 0, reg.KEY_READ) as key:
                children, values, _ = reg.QueryInfoKey(key)
                result[relative] = {name: (value, kind) for name, value, kind in
                                    (reg.EnumValue(key, index) for index in range(values))}
                names = [reg.EnumKey(key, index) for index in range(children)]
            for name in names:
                visit(full + '\\' + name, relative + '\\' + name if relative else name)
        try:
            visit(path, '')
        except FileNotFoundError:
            return None
        return result

    def delete(self, path):
        reg = self.api
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, path, 0, reg.KEY_READ) as key:
                names = [reg.EnumKey(key, index) for index in range(reg.QueryInfoKey(key)[0])]
            for name in names:
                self.delete(path + '\\' + name)
            reg.DeleteKey(reg.HKEY_CURRENT_USER, path)
        except FileNotFoundError:
            pass

    def restore(self, path, snapshot):
        self.delete(path)
        if snapshot is None:
            return
        reg = self.api
        for relative, values in snapshot.items():
            full = path + '\\' + relative if relative else path
            with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, full, 0, reg.KEY_WRITE) as key:
                for name, (value, kind) in values.items():
                    reg.SetValueEx(key, name, 0, kind, value)

    def notify(self):
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)


class ContextMenus:
    def __init__(self, executable, *, portable=None, registry=None, state_file=None):
        self.plan = registration_plan(executable, portable=portable)
        self.registry = registry if registry is not None else WindowsRegistry()
        if state_file is None:
            if self.plan.portable:
                state_file = Path(self.plan.executable).parent / 'Data' / 'context-menu.json'
            else:
                local = os.environ.get('LOCALAPPDATA')
                if not local:
                    raise OSError('无法找到当前用户的数据目录。')
                state_file = Path(local) / APP_SLUG / 'contextmenus' / (self.plan.token + '.json')
        self.state_file = Path(state_file)

    def _owned(self, root, plan):
        value = self.registry.get(root, OWNER)
        return (isinstance(value, str) and _normalized(value) == _normalized(plan.executable)
                and self.registry.get(root, EDITION) == ('portable' if plan.portable else 'installed'))

    def _previous(self):
        if not self.plan.portable:
            return None
        try:
            saved = json.loads(self.state_file.read_text(encoding='utf-8'))
            old = saved['executable']
            if (saved.get('portable') is True and isinstance(old, str) and Path(old).is_absolute()
                    and _normalized(old) != _normalized(self.plan.executable)):
                try:
                    Path(old).stat()
                except FileNotFoundError:
                    return registration_plan(old, portable=True)
        except (OSError, ValueError, TypeError, KeyError):
            pass
        return None

    def status(self):
        plan = self.plan
        if all(self.registry.get(root + ('\\' + relative if relative else ''), name) == value
               for root, tree in plan.trees.items() for relative, values in tree.items()
               for name, value in values.items()):
            return 'enabled'
        if any(self.registry.snapshot(root) is not None for root in plan.trees):
            return 'repair'
        previous = self._previous()
        if previous and any(self._owned(root, previous) for root in previous.trees):
            return 'repair'
        return 'off'

    def _save_receipt(self):
        data = json.dumps({'executable': self.plan.executable, 'portable': self.plan.portable}, ensure_ascii=False)
        fd, temporary = tempfile.mkstemp(prefix='.context-menu-', suffix='.tmp', dir=self.state_file.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(data)
            os.replace(temporary, self.state_file)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _apply(self, enabled):
        from PyQt6.QtCore import QLockFile
        if enabled and not Path(self.plan.executable).is_file():
            raise OSError('程序位置已改变，请从完整的程序文件夹重新打开软件。')
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(self.state_file) + '.lock')
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            raise OSError('正在更新此版本的右键菜单，请稍后重试。')
        try:
            removed = []
            previous = self._previous()
            if previous:
                removed.extend(root for root in previous.trees if self._owned(root, previous))
            if not enabled:
                removed.extend(root for root in self.plan.trees if self._owned(root, self.plan))
            affected = list(dict.fromkeys([*(self.plan.trees if enabled else ()), *removed]))
            snapshots = {root: self.registry.snapshot(root) for root in affected}
            if enabled:
                for root in self.plan.trees:
                    snapshot = snapshots[root]
                    if snapshot and any(snapshot.values()) and not self._owned(root, self.plan):
                        raise OSError('发现其他来源的菜单登记，已停止操作以避免覆盖。')
            try:
                if enabled:
                    for root, tree in self.plan.trees.items():
                        for relative, values in tree.items():
                            path = root + ('\\' + relative if relative else '')
                            for name, value in values.items():
                                self.registry.set(path, name, value)
                for root in removed:
                    self.registry.delete(root)
                if enabled:
                    self._save_receipt()
                else:
                    self.state_file.unlink(missing_ok=True)
            except Exception as exc:
                errors = []
                for root, snapshot in snapshots.items():
                    try:
                        self.registry.restore(root, snapshot)
                    except OSError as restore_error:
                        errors.append(str(restore_error))
                if errors:
                    raise OSError('操作未完成，部分菜单需要修复：' + '；'.join(errors)) from exc
                raise
            self.registry.notify()
        finally:
            lock.unlock()

    def enable(self):
        self._apply(True)

    def repair(self):
        self._apply(True)

    def remove(self):
        self._apply(False)
