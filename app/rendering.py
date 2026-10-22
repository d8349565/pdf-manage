"""Shared reader render queue; QPixmap conversion stays in the GUI thread."""
from collections import deque
from dataclasses import dataclass, field
import json
import os
from time import perf_counter
from threading import Event

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, pyqtSignal


class RenderSignals(QObject):
    done = pyqtSignal(tuple, object)


@dataclass
class RenderResult:
    generation: int
    value: object = None
    annotations: object = None
    error: str = ""
    cancelled: bool = False


class ReadingDiagnostics(QObject):
    """Opt-in numeric timings only; never log document identifiers or content."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.path = os.environ.get("PAGEARK_READING_LOG")
        self.records = deque(maxlen=4096)
        self.started = self.previous = perf_counter()
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._tick)
        self.flush_timer = QTimer(self)
        self.flush_timer.setInterval(1000)
        self.flush_timer.timeout.connect(self.flush)
        if self.path:
            self.timer.start()
            self.flush_timer.start()

    def record(self, stage, **measurements):
        if self.path:
            self.records.append(dict(stage=stage,
                elapsed_ms=round((perf_counter() - self.started) * 1000, 2),
                **measurements))

    def _tick(self):
        now = perf_counter()
        self.record("ui", gap_ms=round((now - self.previous) * 1000, 2))
        self.previous = now

    def flush(self):
        if not self.path or not self.records:
            return
        records = list(self.records)
        self.records.clear()
        try:
            with open(self.path, "a", encoding="utf-8") as stream:
                stream.write("".join(json.dumps(item) + "\n" for item in records))
        except OSError:
            # Diagnostics must never interfere with reading.
            self.path = None
            self.timer.stop()
            self.flush_timer.stop()


@dataclass
class _Request:
    identity: tuple
    ref: object
    width: int | None
    region: object
    priority: int
    generation: int
    kind: str = 'page'
    submitted: float = field(default_factory=perf_counter)
    listeners: list = field(default_factory=list)
    cancelled: Event = field(default_factory=Event)


class _RenderJob(QRunnable):
    def __init__(self, store, request, signals):
        super().__init__()
        self.store, self.request, self.signals = store, request, signals

    def run(self):
        request = self.request
        started = perf_counter()
        result = RenderResult(request.generation)
        try:
            if request.cancelled.is_set():
                result.cancelled = True
            elif request.kind == 'metadata':
                result.value = self.store.prepare_annotations(request.ref)
            elif request.kind == 'safety':
                result.value = self.store.prepare_safety_step(request.ref)
            elif request.region is None:
                render = getattr(self.store, 'render_preview', self.store.render_qimage)
                result.value = render(request.ref, request.width)
            else:
                result.value = self.store.render_region(request.ref, *request.region)
            if not result.cancelled:
                result.annotations = self.store.cached_annotations(request.ref)
        except Exception:
            result.error = "render_failed"
        self.signals.done.emit(request.identity, (request, result,
            round((started - request.submitted) * 1000, 2),
            round((perf_counter() - started) * 1000, 2)))


class RenderScheduler(QObject):
    """One render at a time; queued work can be reprioritised and discarded."""
    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.generation = 0
        self.pages = None
        self.pending = {}
        self.active = None
        self.closed = False
        self.scrolling = False
        self.delivering = False
        self.diagnostics = ReadingDiagnostics(self)
        self.signals = RenderSignals()
        self.signals.done.connect(self._completed, Qt.ConnectionType.QueuedConnection)

    def set_pages(self, pages):
        if self.pages is not pages:
            self.diagnostics.record("reset", discarded=len(self.pending))
            self.pages = pages
            self.generation += 1
            self.pending.clear()
            if self.active is not None:
                self.active.cancelled.set()

    def request(self, ref, width, key, signals, is_stale, priority, *, region=None, kind='page'):
        if self.closed or is_stale():
            return
        self.prune()
        identity = (self.generation, ref.render_key,
                    (kind, width) if region is None else ("region", *region))
        request = self.pending.get(identity)
        if (self.active is not None and self.active.identity == identity
                and not self.active.cancelled.is_set()):
            request = self.active
        if request is None:
            request = _Request(identity, ref, width, region, priority, self.generation)
            request.kind = kind
            self.pending[identity] = request
        request.priority = max(request.priority, priority)
        if not any(signal is signals and listener_key == key
                   for signal, listener_key, stale in request.listeners):
            request.listeners.append((signals, key, is_stale))
        self.diagnostics.record("request", pending=len(self.pending), priority=priority)
        self._dispatch()

    def prune(self):
        if self.active is not None and not any(not stale()
                for signals, key, stale in self.active.listeners):
            self.active.cancelled.set()
        for identity, request in list(self.pending.items()):
            live = []
            for signals, key, stale in request.listeners:
                if stale():
                    signals.done.emit(key, RenderResult(request.generation, cancelled=True))
                else:
                    live.append((signals, key, stale))
            request.listeners = live
            if not live:
                self.pending.pop(identity, None)
                self.diagnostics.record("discard", pending=len(self.pending))

    def has_request(self, signals, key):
        requests = list(self.pending.values())
        if self.active is not None:
            requests.append(self.active)
        return any(request.generation == self.generation and any(
            signal is signals and listener_key == key and not stale()
            for signal, listener_key, stale in request.listeners) for request in requests)

    def _dispatch(self):
        if self.closed or self.active is not None or self.delivering:
            return
        self.prune()
        if not self.pending:
            return
        candidates = [request for request in self.pending.values()
                      if not self.scrolling or request.priority >= 20]
        if not candidates:
            return
        request = max(candidates, key=lambda item: (item.priority, -item.submitted))
        self.pending.pop(request.identity)
        self.active = request
        QThreadPool.globalInstance().start(_RenderJob(self.store, request, self.signals))

    def _completed(self, identity, completion):
        request, result, queue_ms, render_ms = completion
        self.active = None
        self.diagnostics.record("render", queue_ms=queue_ms, render_ms=render_ms,
                                failed=bool(result.error), cancelled=result.cancelled,
                                pending=len(self.pending))
        if not self.closed and request.generation == self.generation:
            self.delivering = True
            try:
                for signals, key, stale in request.listeners:
                    if not stale():
                        signals.done.emit(key, result)
                    else:
                        signals.done.emit(key, RenderResult(request.generation, cancelled=True))
            finally:
                self.delivering = False
        # Deliver GUI results before starting the next native render.
        QTimer.singleShot(0, self._dispatch)

    def close(self):
        self.closed = True
        self.pending.clear()
        if self.active is not None:
            self.active.cancelled.set()
        self.diagnostics.timer.stop()
        self.diagnostics.flush_timer.stop()
        self.diagnostics.flush()
