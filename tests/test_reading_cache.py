"""Lossless session cache and cleanup contracts; no user documents are read."""
from types import SimpleNamespace
from threading import Event

import pytest
from PyQt6.QtCore import QThreadPool
from PyQt6.QtGui import QImage, QColor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app.readingcache import ReadingCache
from app.rendering import RenderSignals


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def cache(qapp):
    value = ReadingCache()
    pool = QThreadPool.globalInstance()
    yield value
    value.close()
    pool.waitForDone(10000)
    qapp.processEvents()


def drain(cache, qapp):
    for _ in range(1000):
        qapp.processEvents()
        if not cache.busy:
            return
        QTest.qWait(2)
    pytest.fail('Reading cache I/O did not settle')


def sample():
    image = QImage(80, 120, QImage.Format.Format_RGB888)
    image.fill(QColor('#edeaff'))
    for y in range(10, 110, 4):
        image.setPixelColor(17, y, QColor('#123456'))
    return image


def test_page_roundtrip_is_lossless_and_has_no_hidden_full_page_memory(cache, qapp):
    original = sample()
    entry = cache.put_page(('generated', 0, 0), original)
    drain(cache, qapp)
    assert entry.path.is_file() and entry.image is None
    assert cache.page(entry.page, 80) is entry
    assert cache.page(entry.page, 160) is None
    results, signals = [], RenderSignals()
    signals.done.connect(lambda key, result: results.append((key, result)))
    cache.request(entry, ('visible',), signals, lambda: False, 3)
    drain(cache, qapp)
    assert len(results) == 1 and results[0][1].generation == 3
    assert results[0][1].value == original
    assert entry.image is None


def test_final_region_is_restored_with_its_position_and_scale(cache, qapp):
    cache.detail_limit = 0
    image = sample()
    entry = cache.put_detail(('generated', 1, 0), (10, 20, 50, 80), 2, (image, (10, 20), 2))
    drain(cache, qapp)
    assert entry.image is None and cache.detail_bytes == 0
    assert cache.detail(entry.page, (12, 22, 48, 78), 2) is entry
    assert cache.detail(entry.page, (12, 22, 48, 78), 3) is None
    assert cache.detail(entry.page, (0, 0, 48, 78), 2) is None
    results, signals = [], RenderSignals()
    signals.done.connect(lambda key, result: results.append(result.value))
    cache.request(entry, ('detail',), signals, lambda: False, 0)
    drain(cache, qapp)
    restored, position, scale = results[0]
    assert restored == image and position == (10, 20) and scale == 2


@pytest.mark.parametrize('budget', ['bytes', 'entries'])
def test_disk_and_entry_budgets_remove_old_files(cache, qapp, budget):
    entries = []
    for number in range(8):
        entries.append(cache.put_page(('generated', number, 0), sample()))
        drain(cache, qapp)
        if number == 0:
            cache.disk_limit = entries[0].disk_bytes * (2 if budget == 'bytes' else 10)
            cache.entry_limit = 3 if budget == 'entries' else 99
    assert cache.disk_bytes <= cache.disk_limit and len(cache.entries) <= cache.entry_limit
    assert not entries[0].path.exists() and entries[-1].path.exists()
    assert sum(path.stat().st_size for path in entries[-1].path.parent.glob('*.png')) == cache.disk_bytes


def test_modified_rotated_and_closed_pages_do_not_reuse_old_images(cache, qapp):
    original = cache.put_page(('generated', 0, 0), sample())
    other = cache.put_page(('other', 0, 0), sample())
    drain(cache, qapp)
    assert cache.page(('generated:edit:1:2', 0, 0), 80) is None
    assert cache.page(('generated', 0, 90), 80) is None
    cache.retain({other.page})
    assert not original.path.exists() and other.path.exists()
    cache.retain(set())
    assert not other.path.parent.exists()


def test_corrupt_cache_is_discarded_and_reports_a_recoverable_miss(cache, qapp):
    entry = cache.put_page(('generated', 0, 0), sample())
    drain(cache, qapp)
    entry.path.write_bytes(b'invalid generated image')
    results, signals = [], RenderSignals()
    signals.done.connect(lambda key, result: results.append(result))
    cache.request(entry, ('visible',), signals, lambda: False, 0)
    drain(cache, qapp)
    assert results[0].error == 'cache_miss' and cache.page(entry.page, 80) is None


def test_duplicate_cache_reads_share_one_job_and_stale_results_are_ignored(cache, qapp, monkeypatch):
    entry = cache.put_page(('generated', 0, 0), sample())
    drain(cache, qapp)
    jobs = []
    monkeypatch.setattr('app.readingcache.QThreadPool.globalInstance',
                        lambda: SimpleNamespace(start=lambda job, priority=0: jobs.append(job)))
    results, signals = [], RenderSignals()
    signals.done.connect(lambda key, result: results.append(result))
    stale = False
    for _ in range(3):
        cache.request(entry, ('visible',), signals, lambda: stale, 0)
    assert len(jobs) == 1
    stale = True
    jobs.pop().run()
    qapp.processEvents()
    assert not results and not cache.busy


def test_close_during_queued_io_cleans_the_directory_without_waiting(cache, qapp, monkeypatch):
    jobs = []
    monkeypatch.setattr('app.readingcache.QThreadPool.globalInstance',
                        lambda: SimpleNamespace(start=lambda job, priority=0: jobs.append(job)))
    entry = cache.put_page(('generated', 0, 0), sample())
    directory = entry.path.parent
    cache.close()
    assert directory.exists()
    jobs.pop().run()
    qapp.processEvents()
    assert not directory.exists() and not cache.entries


def test_unwritable_cache_does_not_fail_the_reader(cache, qapp, monkeypatch):
    def unavailable(*args):
        raise OSError('Generated unavailable cache location')
    monkeypatch.setattr('app.readingcache.TemporaryWorkspace', unavailable)
    assert cache.put_page(('generated', 0, 0), sample()) is None
    assert not cache.busy and not cache.entries


def test_failed_background_write_is_recoverable(cache, qapp, monkeypatch):
    monkeypatch.setattr('app.readingcache.QImageWriter', lambda *args: SimpleNamespace(
        setCompression=lambda value: None, write=lambda image: False))
    cache.put_page(('generated', 0, 0), sample())
    drain(cache, qapp)
    assert not cache.entries and not cache.busy
    assert cache.page(('generated', 0, 0), 80) is None


def test_neighbor_final_region_can_be_prepared_before_it_is_visible(cache, qapp):
    cache.detail_limit = 0
    entry = cache.put_detail(('generated', 0, 0), (10, 20, 50, 80), 2, (sample(), (10, 20), 2))
    drain(cache, qapp)
    assert entry.image is None
    cache.detail_limit = entry.cost
    cache.warm_detail(entry.page, 2, False, lambda: False)
    drain(cache, qapp)
    assert cache.value(entry)[0] == sample() and cache.detail_bytes == entry.cost


def test_close_during_an_active_write_removes_files_after_the_worker_exits(cache, qapp, monkeypatch):
    from app.readingcache import QImageWriter
    entered, resume = Event(), Event()
    class SlowWriter:
        def __init__(self, *args):
            self.writer = QImageWriter(*args)
        def setCompression(self, value):
            self.writer.setCompression(value)
        def write(self, image):
            entered.set()
            resume.wait(5)
            return self.writer.write(image)
    monkeypatch.setattr('app.readingcache.QImageWriter', SlowWriter)
    entry = cache.put_page(('generated', 0, 0), sample())
    try:
        assert entered.wait(5)
        cache.close()
        assert entry.path.parent.exists()
    finally:
        resume.set()
    QThreadPool.globalInstance().waitForDone(10000)
    qapp.processEvents()
    assert not entry.path.parent.exists() and not cache.busy


def test_large_high_dpi_final_region_stays_available_in_memory(cache, qapp):
    image = QImage(3200, 2000, QImage.Format.Format_RGB888)
    image.fill(QColor('#edeaff'))
    assert image.sizeInBytes() > 16 * 1024 * 1024
    entry = cache.put_detail(('generated', 0, 0), (0, 0, 1600, 1000), 2, (image, (0, 0), 2))
    drain(cache, qapp)
    assert cache.value(entry)[0] == image
    assert cache.detail_bytes <= cache.detail_limit


def test_cache_matches_the_same_pixel_tolerance_as_the_reader(cache, qapp):
    image = QImage(1000, 100, QImage.Format.Format_RGB888)
    image.fill(QColor('#edeaff'))
    entry = cache.put_page(('generated', 0, 0), image)
    assert cache.page(entry.page, 1005) is entry
    assert cache.page(entry.page, 1040) is None


def test_unicode_cache_directory_roundtrip(cache, qapp, tmp_path, monkeypatch):
    root = tmp_path / '中文 阅读缓存'
    monkeypatch.setattr('app.storage.directory', lambda kind: root)
    entry = cache.put_page(('generated', 0, 0), sample())
    drain(cache, qapp)
    assert entry.path.is_relative_to(root) and entry.path.exists()
    results, signals = [], RenderSignals()
    signals.done.connect(lambda key, result: results.append(result.value))
    cache.request(entry, ('visible',), signals, lambda: False, 0)
    drain(cache, qapp)
    assert results[0] == sample()
