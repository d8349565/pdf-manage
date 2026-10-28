"""Reader queue contracts, without opening any PDF files."""
from types import SimpleNamespace

import pytest
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from app.rendering import ReadingDiagnostics, RenderScheduler, RenderSignals


@pytest.fixture
def queue(qapp, monkeypatch):
    monkeypatch.delenv("PAGEARK_READING_LOG", raising=False)
    jobs, calls = [], []
    store = SimpleNamespace(
        render_qimage=lambda ref, width: calls.append(width) or QImage(20, 20, QImage.Format.Format_RGB32),
        cached_annotations=lambda ref: {"annotations": [], "matrix": (1, 0, 0, 1, 0, 0)},
    )
    monkeypatch.setattr("app.rendering.QThreadPool.globalInstance",
                        lambda: SimpleNamespace(start=lambda job: jobs.append(job)))
    scheduler = RenderScheduler(store)
    scheduler.set_pages([])
    yield scheduler, jobs, calls
    scheduler.close()


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def complete(job, qapp):
    job.run()
    for _ in range(3):
        qapp.processEvents()


def test_identical_active_render_is_shared_by_two_consumers(queue, qapp):
    scheduler, jobs, calls = queue
    ref = SimpleNamespace(render_key=("source", 0, 0))
    first, second = RenderSignals(), RenderSignals()
    results = []
    first.done.connect(lambda key, result: results.append((key, result)))
    second.done.connect(lambda key, result: results.append((key, result)))
    scheduler.request(ref, 640, ("preview",), first, lambda: False, 20)
    scheduler.request(ref, 640, ("neighbor",), second, lambda: False, -1)
    assert len(jobs) == 1
    complete(jobs[0], qapp)
    assert calls == [640]
    assert [key for key, result in results] == [("preview",), ("neighbor",)]
    assert all(result.annotations["annotations"] == [] for key, result in results)


def test_offscreen_queued_render_is_discarded_before_running(queue, qapp):
    scheduler, jobs, calls = queue
    ref = SimpleNamespace(render_key=("source", 0, 0))
    signals = RenderSignals()
    stale = [False]
    scheduler.request(ref, 640, ("active",), signals, lambda: False, 20)
    scheduler.request(ref, 128, ("offscreen",), signals, lambda: stale[0], 0)
    stale[0] = True
    scheduler.prune()
    complete(jobs[0], qapp)
    assert len(jobs) == 1
    assert calls == [640]
    assert not scheduler.pending


def test_job_waiting_for_a_pool_thread_can_be_cancelled_without_native_rendering(queue, qapp):
    scheduler, jobs, calls = queue
    ref = SimpleNamespace(render_key=("source", 0, 0))
    signals = RenderSignals()
    stale = [False]
    scheduler.request(ref, 640, ("active",), signals, lambda: stale[0], 20)
    stale[0] = True
    scheduler.prune()
    complete(jobs[0], qapp)
    assert not calls


def test_scrolling_pauses_background_work_and_serves_visible_page_first(queue, qapp):
    scheduler, jobs, calls = queue
    ref = SimpleNamespace(render_key=("source", 0, 0))
    signals = RenderSignals()
    scheduler.scrolling = True
    scheduler.request(ref, 128, ("thumb",), signals, lambda: False, 0)
    scheduler.request(ref, 1200, ("sharp",), signals, lambda: False, 10)
    assert not jobs
    scheduler.request(ref, 640, ("visible",), signals, lambda: False, 20)
    complete(jobs[0], qapp)
    assert calls == [640] and len(jobs) == 1
    scheduler.scrolling = False
    scheduler._dispatch()
    assert jobs[1].request.width == 1200
    complete(jobs[1], qapp)
    assert jobs[2].request.width == 128


def test_same_page_after_document_generation_change_rejects_old_result(queue, qapp):
    scheduler, jobs, calls = queue
    ref = SimpleNamespace(render_key=("source", 0, 0))
    signals = RenderSignals()
    results = []
    signals.done.connect(lambda key, result: results.append(result))
    scheduler.request(ref, 640, ("same",), signals, lambda: False, 20)
    scheduler.set_pages([])
    scheduler.request(ref, 640, ("same",), signals, lambda: False, 20)
    complete(jobs[0], qapp)
    assert not results
    complete(jobs[1], qapp)
    assert len(results) == 1 and results[0].generation == scheduler.generation


def test_input_defers_preloading_without_delaying_foreground_work(queue, qapp):
    scheduler, jobs, calls = queue
    ref = SimpleNamespace(render_key=("source", 0, 0))
    signals = RenderSignals()
    scheduler.defer_background(60000)
    scheduler.request(ref, 1280, ("neighbor",), signals, lambda: False, -1)
    assert not jobs and scheduler._background_timer.isActive()
    scheduler.request(ref, 640, ("visible",), signals, lambda: False, 20)
    complete(jobs.pop(0), qapp)
    assert calls == [640] and not jobs
    scheduler._background_after = 0
    scheduler._background_timer.timeout.emit()
    complete(jobs.pop(0), qapp)
    assert calls == [640, 1280]


def test_preloading_waits_for_idle_then_resumes_nearest_page_first(queue, qapp):
    scheduler, jobs, calls = queue
    signals = RenderSignals()
    ready = False
    scheduler.background_ready = lambda: ready
    for index, priority in ((3, -5), (2, -3), (1, -1)):
        ref = SimpleNamespace(render_key=("source", index, 0))
        scheduler.request(ref, index * 640, ("neighbor", index), signals, lambda: False, priority)
    assert not jobs
    ready = True
    scheduler._background_timer.timeout.emit()
    complete(jobs.pop(0), qapp)
    complete(jobs.pop(0), qapp)
    complete(jobs.pop(0), qapp)
    assert calls == [640, 1280, 1920]


def test_foreground_overtakes_pending_preloads_after_one_running_render(queue, qapp):
    scheduler, jobs, calls = queue
    ref = SimpleNamespace(render_key=("source", 0, 0))
    signals = RenderSignals()
    scheduler.request(ref, 1280, ("running_neighbor",), signals, lambda: False, -1)
    scheduler.request(ref, 1920, ("pending_neighbor",), signals, lambda: False, -2)
    scheduler.defer_background(60000)
    scheduler.request(ref, 640, ("user_action",), signals, lambda: False, 60)
    complete(jobs.pop(0), qapp)
    complete(jobs.pop(0), qapp)
    assert calls == [1280, 640] and not jobs
    scheduler.close()
    assert not scheduler._background_timer.isActive()
    scheduler._background_timer.timeout.emit()
    assert not jobs


def test_diagnostics_are_disabled_by_default_and_log_only_numeric_timings(qapp, monkeypatch, tmp_path):
    monkeypatch.delenv("PAGEARK_READING_LOG", raising=False)
    diagnostics = ReadingDiagnostics()
    diagnostics.record("cache", hit=True)
    assert not diagnostics.timer.isActive() and not diagnostics.records
    path = tmp_path / "reading.jsonl"
    monkeypatch.setenv("PAGEARK_READING_LOG", str(path))
    enabled = ReadingDiagnostics()
    try:
        enabled.record("render", queue_ms=4, render_ms=18, failed=False)
        enabled.flush()
        import json
        record = json.loads(path.read_text(encoding="utf-8"))
        assert set(record) == {"stage", "elapsed_ms", "queue_ms", "render_ms", "failed"}
    finally:
        enabled.timer.stop()
        enabled.flush_timer.stop()
