"""User-selected storage locations and cleanup limited to owned scratch files."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

from PyQt6.QtCore import QLockFile, QSettings

from app import APP_SLUG
from app.output import check_cancel
from app.runtime import portable_data, portable_root, preferences


class StorageError(OSError):
    pass


def _settings():
    return preferences(QSettings)


def settings_location():
    return _settings().fileName()


def data_directory():
    portable = portable_data()
    if portable is not None:
        return portable
    local = os.environ.get('LOCALAPPDATA')
    if not local and os.name == 'nt':
        raise StorageError('无法找到应用数据目录，请在存储管理中选择保存位置。')
    return Path(local or tempfile.gettempdir()) / APP_SLUG


def _decode_path(value):
    root = portable_root()
    if root is not None and value.startswith('portable:'):
        relative = Path(value[len('portable:'):])
        if relative.is_absolute() or '..' in relative.parts:
            raise StorageError('便携存储路径无效，请重新选择文件夹。')
        return root / relative
    path = Path(value)
    if not path.is_absolute():
        raise StorageError('存储路径无效，请在存储管理中重新选择文件夹。')
    return path


def _encode_path(path):
    path = Path(path).resolve()
    root = portable_root()
    if root is not None and path.is_relative_to(root):
        return 'portable:' + path.relative_to(root).as_posix()
    return str(path)


def directory(kind):
    value = _settings().value(f'storage/{kind}', '', type=str)
    if value:
        return _decode_path(value)
    return data_directory() / kind


def output_directory():
    value = _settings().value('storage/output', '', type=str)
    return _decode_path(value) if value else None


def export_path(suggested):
    folder = output_directory()
    return str(folder / Path(suggested).name) if folder else str(suggested)


def previous_directories(kind):
    values = _settings().value(f'storage/previous-{kind}', [], type=list)
    current = directory(kind).resolve()
    return list(dict.fromkeys(_decode_path(p) for p in values
                             if (Path(p).is_absolute() or p.startswith('portable:'))
                             and _decode_path(p).resolve() != current))


def check_directory(kind, path):
    if path is None:
        return
    path = Path(path).resolve()
    if kind in ('ocr', 'cache'):
        other_kind = 'cache' if kind == 'ocr' else 'ocr'
        others = [directory(other_kind).resolve(), *previous_directories(other_kind)]
        if any(path.is_relative_to(other) or other.is_relative_to(path) for other in others):
            raise StorageError('OCR 组件和任务缓存需要使用彼此独立的文件夹。')
    output = path if kind == 'output' else output_directory()
    caches = [path] if kind == 'cache' else [directory('cache').resolve(), *previous_directories('cache')]
    if output is not None and any(Path(output).resolve().is_relative_to(cache) for cache in caches):
        raise StorageError('输出文件夹须位于缓存目录之外，避免把导出结果当作临时文件。')


def save_directory(kind, path):
    """Save only after the caller has checked/copied the selected location."""
    check_directory(kind, path)
    settings = _settings()
    key = f'storage/{kind}'
    old = settings.value(key, '', type=str)
    previous_key = f'storage/previous-{kind}'
    previous = settings.value(previous_key, [], type=list)
    value = _encode_path(path) if path else ''
    history = previous[:]
    if kind in ('ocr', 'cache'):
        former = _encode_path(directory(kind))
        if former != value and former not in history:
            history.append(former)
        settings.setValue(previous_key, history)
    settings.setValue(key, value)
    settings.sync()
    if settings.status() != QSettings.Status.NoError:
        settings.setValue(key, old)
        settings.setValue(previous_key, previous)
        settings.sync()
        raise StorageError('存储设置无法保存，仍使用原位置；请检查当前用户的写入权限。')


def is_link(path):
    return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)


def writable_directory(path):
    path = Path(path).resolve()
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=path):
            pass
    except OSError:
        raise StorageError(f'文件夹无法创建或写入：{path}\n请在存储管理中选择可用且空间充足的保存位置。') from None
    return path


def tree_files(root, cancel=None):
    """Do not follow directory junctions or symlinks while counting/deleting."""
    root = Path(root)
    if is_link(root):
        raise StorageError('目录包含链接，已跳过以保护其他位置的文件。')
    def failed(exc):
        raise exc
    for parent, dirs, files in os.walk(root, followlinks=False, onerror=failed):
        check_cancel(cancel)
        for name in (*dirs, *files):
            if is_link(Path(parent, name)):
                raise StorageError('目录包含链接，已跳过以保护其他位置的文件。')
        for name in files:
            yield Path(parent, name)


class TemporaryWorkspace:
    """A task owns its directory until cleanup; other processes skip its lock."""
    def __init__(self, purpose):
        root = writable_directory(directory('cache'))
        self._temp = tempfile.TemporaryDirectory(prefix=f'task-{purpose}-', dir=root)
        self.name = self._temp.name
        self._lock = QLockFile(str(Path(self.name) / 'owner.lock'))
        self._lock.setStaleLockTime(0)
        if not self._lock.tryLock(0):
            self._temp.cleanup()
            raise StorageError('无法登记任务临时目录，请检查缓存目录权限。')
        (Path(self.name) / '.zhixu-task').write_text('ZhiXuPDF scratch v1', encoding='ascii')

    def __del__(self):
        try:
            self.cleanup()
        except (OSError, AttributeError, RuntimeError):
            pass

    def cleanup(self):
        self._lock.unlock()
        self._temp.cleanup()

    def __enter__(self):
        return self.name

    def __exit__(self, *args):
        self.cleanup()


def _cache_tasks():
    for root in dict.fromkeys([directory('cache'), *previous_directories('cache')]):
        if not root.is_dir() or is_link(root):
            continue
        for path in root.iterdir():
            if not re.fullmatch(r'task-[a-z]+-[a-z0-9_]+', path.name) or not path.is_dir() or is_link(path):
                continue
            marker = path / '.zhixu-task'
            try:
                if (marker.is_file() and not is_link(marker) and marker.stat().st_size < 64
                        and marker.read_text(encoding='ascii') == 'ZhiXuPDF scratch v1'):
                    yield path
            except (OSError, UnicodeError):
                continue


def _backups():
    roots = [directory('ocr'), *previous_directories('ocr')]
    active_roots = [path.resolve() for path in roots]
    for parent in dict.fromkeys(path.parent for path in roots):
        if not parent.is_dir() or is_link(parent):
            continue
        for path in parent.iterdir():
            if (re.fullmatch(r'\.ocr-backup-[a-f0-9]{32}', path.name) and path.is_dir() and not is_link(path)
                    and not any(root.is_relative_to(path.resolve()) for root in active_roots)):
                yield path


def runtime_in_use(root):
    root = Path(root).resolve()
    for path in _cache_tasks():
        reference = path / 'runtime-path.txt'
        try:
            if (not path.name.startswith('task-runtime-') or not reference.is_file()
                    or is_link(reference) or reference.stat().st_size > 32768):
                continue
            if Path(reference.read_text(encoding='utf-8')).resolve() != root:
                continue
        except (OSError, UnicodeError):
            continue
        lock = QLockFile(str(path / 'owner.lock'))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            return True
        lock.unlock()
    return False


def inspect_storage(progress=None, cancel=None):
    from app.ocr.model_manager import validate_runtime
    from app.ocr.contracts import OCRError
    result = dict(ocr_bytes=0, cache_bytes=0, backup_bytes=0, previous_bytes=0, previous_count=0, cache_count=0,
                  backup_count=0, active=0, notes=[])
    root = directory('ocr')
    if root.is_dir():
        result['ocr_bytes'] = sum(path.stat().st_size for path in tree_files(root, cancel))
    for path in previous_directories('ocr'):
        if not path.is_dir():
            continue
        check_cancel(cancel)
        if progress:
            progress(0, 0, '正在校验以前位置的 OCR 组件')
        try:
            validate_runtime(path, cancel=cancel)
            result['previous_bytes'] += sum(file.stat().st_size for file in tree_files(path, cancel))
            result['previous_count'] += 1
        except (OSError, OCRError):
            result['notes'].append(f'无法安全核验，已跳过：{path}')
    tasks, backups = list(_cache_tasks()), list(_backups())
    for index, path in enumerate(tasks + backups):
        check_cancel(cancel)
        if progress:
            progress(index, len(tasks) + len(backups), '正在统计组件备份和任务缓存')
        lock = None
        try:
            if path in tasks:
                lock = QLockFile(str(path / 'owner.lock'))
                lock.setStaleLockTime(0)
                if not lock.tryLock(0):
                    result['active'] += 1
                    continue
                key = 'cache'
            else:
                validate_runtime(path, cancel=cancel)
                key = 'backup'
            size = sum(file.stat().st_size for file in tree_files(path, cancel))
            result[f'{key}_bytes'] += size
            result[f'{key}_count'] += 1
        except (OSError, OCRError):
            result['notes'].append(f'无法安全核验，已跳过：{path}')
        finally:
            if lock:
                lock.unlock()
    return result


def clean_storage(kind, progress=None, cancel=None):
    """Only explicitly requested scratch tasks or verified component backups."""
    from app.ocr.model_manager import validate_runtime
    from app.ocr.contracts import OCRError
    if kind not in ('cache', 'backup', 'previous-ocr'):
        raise ValueError('不支持的清理类型')
    if kind == 'previous-ocr':
        current, _ = validate_runtime(cancel=cancel)
        candidates = [path for path in previous_directories('ocr') if path.is_dir()
                      and not current.is_relative_to(path) and not path.is_relative_to(current)]
    else:
        candidates = list(_cache_tasks() if kind == 'cache' else _backups())
    removed, skipped = 0, []
    for index, path in enumerate(candidates):
        check_cancel(cancel)
        lock = None
        try:
            if kind == 'cache':
                lock = QLockFile(str(path / 'owner.lock'))
                lock.setStaleLockTime(0)
                if not lock.tryLock(0):
                    skipped.append(f'正在使用：{path}')
                    continue
            else:
                if is_link(path):
                    raise StorageError('组件目录是链接，已跳过。')
                if runtime_in_use(path):
                    skipped.append(f'OCR 任务正在使用：{path}')
                    continue
                validate_runtime(path, cancel=cancel)
            # Check the whole tree before removing any files.
            list(tree_files(path, cancel))
            check_cancel(cancel)
            if lock:
                lock.unlock()
            shutil.rmtree(path)
            removed += 1
        except (OSError, OCRError):
            skipped.append(f'无法清理或包含未登记文件：{path}')
        finally:
            if lock:
                lock.unlock()
        if progress:
            progress(index + 1, len(candidates), '正在清理已结束任务的文件' if kind == 'cache' else '正在清理旧组件备份')
    return removed, skipped


def program_directory():
    return Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent
