"""Session-owned, lossless reading images; disk I/O never uses the PDF lock."""
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock
from uuid import uuid4
import math
import os

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QImageReader, QImageWriter

from app.rendering import RenderResult, RenderSignals
from app.storage import TemporaryWorkspace


def native_width_matches(width, target):
    return math.isclose(width, target, rel_tol=.01, abs_tol=1)


@dataclass(eq=False)
class _Entry:
    key: tuple
    page: tuple
    path: Path
    width: int
    height: int
    cost: int
    format: object
    area: tuple | None = None
    requested_scale: float = 1
    position: tuple = (0, 0)
    scale: float = 1
    image: object = None
    disk_bytes: int = 0
    cancelled: Event = field(default_factory=Event)

    def value(self, image):
        return image if self.area is None else (image, self.position, self.scale)


class _Files:
    """Keep the owned directory alive until the last in-flight I/O exits."""
    def __init__(self):
        self.workspace = None
        self.closed = Event()
        self.lock = Lock()
        self.jobs = 0

    def release(self):
        with self.lock:
            self.jobs -= 1
            if self.closed.is_set() and not self.jobs and self.workspace is not None:
                self._cleanup()

    def close(self):
        with self.lock:
            self.closed.set()
            if not self.jobs and self.workspace is not None:
                self._cleanup()

    def _cleanup(self):
        try:
            self.workspace.cleanup()
        except OSError:
            pass
        self.workspace = None


class _Signals(QObject):
    done = pyqtSignal(object)


class _ImageJob(QRunnable):
    def __init__(self, kind, entry, files, signals):
        super().__init__()
        self.kind, self.entry, self.files, self.signals = kind, entry, files, signals
        self.image = entry.image if kind == 'write' else None

    def run(self):
        entry, result = self.entry, None
        partial = entry.path.with_suffix('.part')
        try:
            if not self.files.closed.is_set() and not entry.cancelled.is_set():
                if self.kind == 'write':
                    writer = QImageWriter(str(partial), b'PNG')
                    writer.setCompression(20)  # PNG's 0-100 setting: fast lossless compression.
                    written = writer.write(self.image)
                    del writer
                    if written:
                        if not self.files.closed.is_set() and not entry.cancelled.is_set():
                            os.replace(partial, entry.path)
                            result = entry.path.stat().st_size
                else:
                    reader = QImageReader(str(entry.path), b'PNG')
                    size = reader.size()
                    if size.width() == entry.width and size.height() == entry.height:
                        image = reader.read()
                        if not image.isNull():
                            result = image.convertToFormat(entry.format)
                    del reader
        except (OSError, RuntimeError):
            pass
        finally:
            if self.kind == 'write':
                try:
                    partial.unlink(missing_ok=True)
                except OSError:
                    pass
            if not self.files.closed.is_set():
                try:
                    self.signals.done.emit((self.kind, entry, result))
                except RuntimeError:
                    pass
            self.files.release()


class ReadingCache(QObject):
    def __init__(self, parent=None, *, disk_limit=512 * 1024 * 1024,
                 detail_limit=64 * 1024 * 1024, pending_limit=64 * 1024 * 1024, entry_limit=2048):
        super().__init__(parent)
        self.disk_limit, self.detail_limit = disk_limit, detail_limit
        self.pending_limit, self.entry_limit = pending_limit, entry_limit
        self.disk_bytes = self.detail_bytes = self.pending_bytes = 0
        self.entries = OrderedDict()
        self.pages, self.regions = {}, {}
        self._pending, self._details, self._loads = OrderedDict(), OrderedDict(), {}
        self._writing = None
        self._files = _Files()
        self._write_failed = False
        self.signals = _Signals(self)
        self._warm_signals = RenderSignals(self)
        self.signals.done.connect(self._completed, Qt.ConnectionType.QueuedConnection)

    @property
    def busy(self):
        return bool(self._writing or self._pending or self._loads or self._files.jobs)

    def _touch(self, entry):
        if entry is not None and not entry.cancelled.is_set():
            self.entries.move_to_end(entry.key)
            if entry.key in self._details:
                self._details.move_to_end(entry.key)
            return entry

    def page(self, page_key, width):
        variants = self.pages.get(page_key, {})
        entry = variants.get(width)
        if entry is None:
            entry = min((entry for size, entry in variants.items() if native_width_matches(size, width)),
                        key=lambda item: abs(item.width - width), default=None)
        return self._touch(entry)

    def detail(self, page_key, area, scale):
        for entry in reversed(tuple(self.regions.get(page_key, {}).values())):
            bounds = entry.area
            if (math.isclose(entry.requested_scale, scale, rel_tol=.0001)
                    and bounds[0] <= area[0] and bounds[1] <= area[1]
                    and bounds[2] >= area[2] and bounds[3] >= area[3]):
                return self._touch(entry)

    def value(self, entry):
        return entry.value(entry.image) if (entry is not None and not entry.cancelled.is_set()
                                            and entry.image is not None) else None

    def warm_detail(self, page_key, scale, from_bottom, stale):
        entries = [entry for entry in self.regions.get(page_key, {}).values()
                   if math.isclose(entry.requested_scale, scale, rel_tol=.0001)]
        if entries:
            entry = (max if from_bottom else min)(entries, key=lambda item: item.area[1])
            if entry.image is None:
                self.request(entry, entry.key, self._warm_signals, stale, 0, 20)

    def put_page(self, page_key, image):
        return self._put(('page', page_key, image.width()), page_key, image)

    def put_detail(self, page_key, area, scale, value):
        image, position, actual_scale = value
        return self._put(('detail', page_key, area, scale), page_key, image,
                         area=area, requested_scale=scale, position=position, scale=actual_scale)

    def _put(self, key, page_key, image, **metadata):
        if self._files.closed.is_set() or self._write_failed or image.isNull():
            return
        if key in self.entries:
            return self._touch(self.entries[key])
        if image.sizeInBytes() > self.pending_limit:
            return
        try:
            if self._files.workspace is None:
                self._files.workspace = TemporaryWorkspace('reading')
            path = Path(self._files.workspace.name) / f'{uuid4().hex}.png'
        except OSError:
            self._write_failed = True
            return
        entry = _Entry(key, page_key, path, image.width(), image.height(), image.sizeInBytes(),
                       image.format(), image=QImage(image), **metadata)
        self.entries[key] = entry
        if entry.area is None:
            self.pages.setdefault(page_key, {})[entry.width] = entry
        else:
            self.regions.setdefault(page_key, {})[key] = entry
            self._details[key] = entry
            self.detail_bytes += entry.cost
        self._pending[key] = entry
        self.pending_bytes += entry.cost
        while self.pending_bytes > self.pending_limit:
            self._remove(next(iter(self._pending.values())))
        self._trim_details()
        self._write_next()
        return entry

    def _start(self, kind, entry, priority):
        with self._files.lock:
            self._files.jobs += 1
        QThreadPool.globalInstance().start(_ImageJob(kind, entry, self._files, self.signals), priority)

    def _write_next(self):
        if self._writing is not None or not self._pending or self._files.closed.is_set():
            return
        _, entry = self._pending.popitem(last=False)
        self.pending_bytes -= entry.cost
        self._writing = entry
        self._start('write', entry, -10)

    def request(self, entry, key, signals, stale, generation, priority=40):
        if entry is None or stale() or self._files.closed.is_set():
            return
        value = self.value(entry)
        if value is not None:
            signals.done.emit(key, RenderResult(generation, value))
            return
        started = entry in self._loads
        listeners = self._loads.setdefault(entry, [])
        if not any(signal is signals and listener_key == key for signal, listener_key, *_ in listeners):
            listeners.append((signals, key, stale, generation))
        if not started:
            self._start('read', entry, priority)

    def _release_image(self, entry):
        if entry is not self._writing and entry.key not in self._pending and entry.key not in self._details:
            entry.image = None

    def _trim_details(self):
        while self.detail_bytes > self.detail_limit:
            _, entry = self._details.popitem(last=False)
            self.detail_bytes -= entry.cost
            self._release_image(entry)

    def _completed(self, completion):
        kind, entry, result = completion
        if self._files.closed.is_set():
            return
        live = self.entries.get(entry.key) is entry
        if kind == 'write':
            if self._writing is not entry:
                self._unlink(entry)
                return
            self._writing = None
            if live and result is not None:
                entry.disk_bytes = result
                self.disk_bytes += result
                self._release_image(entry)
                while self.disk_bytes > self.disk_limit or len(self.entries) > self.entry_limit:
                    self._remove(next(iter(self.entries.values())))
            elif live:
                self._write_failed = True
                self._remove(entry)
            else:
                self._unlink(entry)
            self._write_next()
            return
        listeners = self._loads.pop(entry, [])
        if not live:
            self._unlink(entry)
        if result is None and live:
            self._remove(entry)
        if result is not None and live and entry.area is not None:
            entry.image = result
            if entry.key not in self._details:
                self._details[entry.key] = entry
                self.detail_bytes += entry.cost
            self._trim_details()
        for signals, key, stale, generation in listeners:
            if not stale():
                signals.done.emit(key, RenderResult(generation,
                    entry.value(result) if result is not None and live else None,
                    error='' if result is not None and live else 'cache_miss'))

    def _unlink(self, entry):
        try:
            entry.path.unlink(missing_ok=True)
        except OSError:
            pass

    def _remove(self, entry):
        entry.cancelled.set()
        self.entries.pop(entry.key, None)
        index = self.pages if entry.area is None else self.regions
        variants = index.get(entry.page, {})
        variants.pop(entry.width if entry.area is None else entry.key, None)
        if not variants:
            index.pop(entry.page, None)
        if self._pending.pop(entry.key, None) is not None:
            self.pending_bytes -= entry.cost
        if self._details.pop(entry.key, None) is not None:
            self.detail_bytes -= entry.cost
        self.disk_bytes -= entry.disk_bytes
        self._release_image(entry)
        self._unlink(entry)

    def retain(self, pages):
        for entry in list(self.entries.values()):
            if entry.page not in pages:
                self._remove(entry)
        if not self.entries and self._files.workspace is not None:
            self._files.close()
            self._files = _Files()
            self._writing = None
            self._loads.clear()
            self._write_failed = False

    def close(self):
        self._files.close()
        for entry in self.entries.values():
            entry.cancelled.set()
        self.entries.clear()
        self.pages.clear()
        self.regions.clear()
        self._pending.clear()
        self._details.clear()
        self._loads.clear()
        self._writing = None
        self.disk_bytes = self.detail_bytes = self.pending_bytes = 0
