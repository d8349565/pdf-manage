"""Opt-in 10/100-page benchmark with generated scans and Windows process memory."""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import platform
import sys
from threading import Event, Thread
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymupdf as p
from app.ocr.contracts import OCROptions
from app.ocr.engine import OCREngine
import app.ocr.service as service
from ocr_poc import make_samples


class MemoryCounters(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
               *[(name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
                  'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                  'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]]


def memory(handle):
    counters = MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    api = ctypes.WinDLL('psapi').GetProcessMemoryInfo
    api.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
    api.restype = wintypes.BOOL
    if not api(handle, ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    return counters.WorkingSetSize


class MeasuredEngine(OCREngine):
    measurements = {}
    def __init__(self, root, cancel=None):
        started = monotonic()
        super().__init__(root, cancel)
        self.measurements['model_load_seconds'] = round(monotonic() - started, 3)
        self.stopped = Event()
        self.monitor = Thread(target=self.sample, daemon=True)
        self.monitor.start()
    def sample(self):
        while not self.stopped.wait(.05):
            if self.process.poll() is not None:
                return
            main_handle = ctypes.WinDLL('kernel32').GetCurrentProcess
            main_handle.restype = wintypes.HANDLE
            total = memory(main_handle()) + memory(self.process._handle)
            self.measurements['observed_peak_working_set_bytes'] = max(total,
                self.measurements.get('observed_peak_working_set_bytes', 0))
    def recognize(self, image, page_index):
        started = monotonic()
        result = super().recognize(image, page_index)
        self.measurements.setdefault('page_inference_seconds', []).append(round(monotonic() - started, 3))
        return result
    def close(self):
        if hasattr(self, 'stopped'):
            self.stopped.set()
            self.monitor.join(timeout=2)
        super().close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--pages', nargs='+', type=int, default=[10, 100])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    seed = args.output / 'seed.pdf'
    make_samples(seed)
    service.OCREngine = MeasuredEngine
    reports = []
    for count in args.pages:
        source, output = args.output / f'scan-{count}.pdf', args.output / f'scan-{count}_OCR.pdf'
        with p.open(seed) as template, p.open() as doc:
            for _ in range(count):
                doc.insert_pdf(template, from_page=0, to_page=0)
            doc.save(source, deflate=True)
        MeasuredEngine.measurements = {}
        started = monotonic()
        def progress(current, total, message):
            if total > 0 and current in (0, total) or total > 0 and current % 10 == 0:
                print(f'{count} pages: {current}/{total} ({monotonic() - started:.1f}s)', flush=True)
        result = service.run_ocr(source, output, range(count), OCROptions(runtime_dir=str(args.runtime)), progress)
        assert len(result.processed_pages) == count and not result.failed_pages
        reports.append(dict(pages=count, total_seconds=round(monotonic() - started, 3),
                            source_bytes=source.stat().st_size, output_bytes=output.stat().st_size,
                            **MeasuredEngine.measurements))
        report = dict(platform=platform.platform(), processor=platform.processor(), logical_cpus=os.cpu_count(),
                      dpi=200, samples='Generated A4 scans; not real-office acceptance',
                      memory_method='sum of main/worker working sets sampled every 50 ms after model loading',
                      runs=reports)
        (args.output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(reports[-1]), flush=True)


if __name__ == '__main__':
    main()
