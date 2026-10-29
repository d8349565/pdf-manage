"""Bounded local reading history; loading never probes document paths."""
import json
import math
import os

from PyQt6.QtCore import QObject, QThreadPool, Qt, pyqtSignal


def path_key(path):
    return os.path.normcase(os.path.abspath(path))


class ReadingHistory(QObject):
    changed = pyqtSignal()
    checked = pyqtSignal(list)
    LIMIT = 50

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.entries = []
        self._checking = False
        try:
            saved = json.loads(settings.value('reading/history', '[]', type=str))
        except (ValueError, TypeError):
            saved = []
        seen = set()
        for entry in saved if isinstance(saved, list) else []:
            if not isinstance(entry, dict):
                continue
            path = entry.get('path')
            if (not isinstance(path, str) or not os.path.isabs(path)
                    or not path.lower().endswith(('.pdf', '.ofd')) or path_key(path) in seen):
                continue
            seen.add(path_key(path))
            state = self._state(entry)
            self.entries.append(dict(path=path, **state))
            if len(self.entries) == self.LIMIT:
                break
        self.checked.connect(self._remove_missing, Qt.ConnectionType.QueuedConnection)

    @staticmethod
    def _state(entry):
        page = entry.get('page', 0)
        offset = entry.get('offset', 0)
        return dict(page=max(0, page) if type(page) is int else 0,
                    offset=max(0., min(1., offset)) if isinstance(offset, (int, float))
                    and math.isfinite(offset) else 0.)

    def find(self, path):
        key = path_key(path)
        return next((dict(entry) for entry in self.entries if path_key(entry['path']) == key), None)

    def touch(self, path):
        if not path or not path.lower().endswith(('.pdf', '.ofd')):
            return
        entry = self.find(path) or dict(path=os.path.abspath(path), page=0, offset=0.)
        self.entries = [entry] + [item for item in self.entries if path_key(item['path']) != path_key(path)]
        self.entries = self.entries[:self.LIMIT]
        self._save()

    def remember(self, path, page, offset=0.):
        entry = next((item for item in self.entries if path_key(item['path']) == path_key(path)), None)
        if entry is not None:
            state = self._state(dict(page=page, offset=offset))
            if any(entry.get(key) != value for key, value in state.items()):
                entry.update(state)
                self._save()

    def remove(self, paths):
        keys = {path_key(path) for path in paths}
        remaining = [entry for entry in self.entries if path_key(entry['path']) not in keys]
        if len(remaining) != len(self.entries):
            self.entries = remaining
            self._save()

    def _save(self):
        self.settings.setValue('reading/history', json.dumps(self.entries, ensure_ascii=False))
        self.changed.emit()

    def check_missing(self):
        if self._checking or not self.entries:
            return
        self._checking = True
        self._checked_entries = {path_key(entry['path']): entry for entry in self.entries}
        paths = [entry['path'] for entry in self.entries]

        def check():
            missing = []
            for path in paths:
                # Unreachable shares and unmounted removable drives are not stale records.
                if path.startswith(('\\\\', '//')):
                    continue
                root = os.path.splitdrive(path)[0] + os.sep
                try:
                    os.stat(path)
                except (FileNotFoundError, NotADirectoryError):
                    if os.path.isdir(root):
                        missing.append(path)
                except OSError:
                    pass
            try:
                self.checked.emit(missing)
            except RuntimeError:
                pass  # The owning window may already have closed.

        QThreadPool.globalInstance().start(check, -10)

    def _remove_missing(self, paths):
        self._checking = False
        # A successful open while the worker ran replaces the entry; keep that fresh record.
        unchanged = {path_key(entry['path']) for entry in self.entries
                     if self._checked_entries.get(path_key(entry['path'])) is entry}
        self.remove([path for path in paths if path_key(path) in unchanged])
