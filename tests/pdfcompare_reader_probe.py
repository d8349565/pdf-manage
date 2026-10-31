"""One isolated source-reader measurement; compare repos with the same PDF and fresh settings.

python tests/pdfcompare_reader_probe.py REPO GENERATED_PDF NEW_SETTINGS_DIRECTORY
Measures Qt offscreen behavior, not packaged/native-screen performance.
"""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import time

repo, sample, scratch = (Path(value).resolve() for value in sys.argv[1:])
scratch.mkdir(parents=True, exist_ok=False)
sys.path.insert(0, str(repo))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['LOCALAPPDATA'] = str(scratch)

def resident_mib():
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD),
                    *[(name, ctypes.c_size_t) for name in ('peak', 'rss', 'a', 'b', 'c', 'd', 'commit', 'peak_commit')]]
    value = Counters(); value.cb = ctypes.sizeof(value)
    kernel = ctypes.WinDLL('kernel32'); kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL('psapi')
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    assert psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(value), value.cb)
    return round(value.rss / 2**20, 2)

start = time.perf_counter()
from PyQt6.QtCore import QSettings, QThreadPool
from PyQt6.QtWidgets import QApplication
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(scratch))
settings = QSettings(str(scratch / 'settings.ini'), QSettings.Format.IniFormat)
settings.setValue('updates/automatic', False)
from app import storage
storage._settings = lambda *args: settings
from app import ui
ui.QSettings = lambda *args: settings
app = QApplication([])
win = ui.MainWindow(); win.resize(1180, 820); win.show(); app.processEvents()
result = dict(window_seconds=round(time.perf_counter() - start, 4), idle_rss_mib=resident_mib())
assert 'app.pdfcompare' not in sys.modules and 'app.pdfcompareui' not in sys.modules
from app.__main__ import _start_pdf_warmup
_start_pdf_warmup()
started = time.perf_counter()
win._ingest([str(sample)], replace=True)
deadline = time.perf_counter() + 20
while not win._readable_painted and time.perf_counter() < deadline:
    app.processEvents(); time.sleep(.01)
assert win._readable_painted, 'first readable page not painted'
result['first_readable_seconds'] = round(time.perf_counter() - started, 4)
QThreadPool.globalInstance().waitForDone(10000); app.processEvents()
result['reading_rss_mib'] = resident_mib()
started = time.perf_counter()
for index in (9, 19, 0):
    assert not win.store.render_qimage(win.ws.pages[index], 1000).isNull()
result['three_page_render_seconds'] = round(time.perf_counter() - started, 4)
result['comparison_modules_loaded'] = 'app.pdfcompare' in sys.modules or 'app.pdfcompareui' in sys.modules
win.ws.reset(); win.store.close_all(); win.close(); app.processEvents()
print(json.dumps(result))
