from __future__ import annotations

import os
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.__main__ import startup_pdf_paths
from app import APP_NAME, APP_EXE_NAME


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_cold_window_defers_pdf_engine_and_opens_during_warmup(tmp_path):
    import pymupdf
    source = tmp_path / "synthetic.pdf"
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 100), "Startup regression")
        doc.save(source)
    # 必须另起进程：pytest 的其他测试会提前导入 PyMuPDF，掩盖启动回归。
    result = subprocess.run([sys.executable, "-c", """
import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QThreadPool
from app.__main__ import _start_pdf_warmup
from app.ui import MainWindow
app = QApplication([])
win = MainWindow()
win.show()
app.processEvents()
assert 'pymupdf' not in sys.modules, 'empty window eagerly loaded PDF engine'
assert 'app.pdfstructure' not in sys.modules
assert win.store.operation_reason([], 'modify') == ''
assert 'pymupdf' not in sys.modules
_start_pdf_warmup()
win._ingest([sys.argv[1]], replace=True)
assert len(win.ws.pages) == 1
assert not win.store.render_qimage(win.ws.pages[0], 200).isNull()
QThreadPool.globalInstance().waitForDone(10000)
app.processEvents()
win.ws.reset()
win.store.close_all()
win.close()
""", str(source)], cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_default_encryption_still_uses_aes256():
    import pymupdf
    from app.pdfio import EncryptionOptions
    setting = EncryptionOptions("user", "owner")
    assert setting.algorithm == pymupdf.PDF_ENCRYPT_AES_256
    assert setting.label == "AES-256"
    setting.validate()


def test_icon_cache_reuses_renders_and_separates_dpi(qapp, monkeypatch):
    from types import SimpleNamespace
    from PyQt6.QtCore import QSize
    from PyQt6.QtGui import QPixmap
    from app import icons
    icons._cached_toolbar_icon.cache_clear()
    calls = []
    original = icons._pixmap
    def render(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(icons, "_pixmap", render)
    ratio = [2.0]
    monkeypatch.setattr(icons, "QApplication", SimpleNamespace(
        instance=lambda: SimpleNamespace(devicePixelRatio=lambda: ratio[0])))
    first = icons.toolbar_icon("save")
    second = icons.toolbar_icon("save")
    assert len(calls) == 2
    first.addPixmap(QPixmap(8, 8))
    assert first.cacheKey() != second.cacheKey()
    assert icons.toolbar_icon("save").cacheKey() == second.cacheKey()
    ratio[0] = 3.0
    sharp = icons.toolbar_icon("save")
    assert len(calls) == 4
    assert sharp.pixmap(QSize(20, 20), 3.0).width() == 60
    icons.toolbar_icon("save", color="#123456")
    icons.toolbar_icon("save", size=24)
    assert len(calls) == 8
    icons._cached_toolbar_icon.cache_clear()


def test_startup_log_contains_only_timing_and_fails_open(tmp_path, monkeypatch):
    from app import startup
    path = tmp_path / "startup.jsonl"
    monkeypatch.setattr(startup, "_path", str(path))
    monkeypatch.setattr(startup, "_seen", set())
    startup.mark("test_stage")
    startup.mark("test_stage")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 1
    assert set(rows[0]) == {"pid", "stage", "elapsed_ms", "delta_ms"}
    assert rows[0]["elapsed_ms"] >= 0
    monkeypatch.setattr(startup, "_path", str(tmp_path / "missing" / "log"))
    startup.mark("unwritable_stage")


def test_status_widgets_are_registered_once_while_hidden(qapp, monkeypatch):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QStatusBar
    from app.ui import MainWindow
    hidden = []
    original = QStatusBar.addPermanentWidget
    def add(status, widget, *args):
        layout = widget.layout()
        children = [layout.itemAt(i).widget() for i in range(layout.count())] if layout else []
        hidden.append([child.isHidden() and child.testAttribute(
            Qt.WidgetAttribute.WA_WState_ExplicitShowHide) for child in children])
        return original(status, widget, *args)
    monkeypatch.setattr(QStatusBar, "addPermanentWidget", add)
    win = MainWindow()
    assert hidden == [[True, True, True]], "empty startup repeatedly lays out unused status widgets"
    win._preview_busy = True
    win._update_render_progress()
    assert not win.render_label.isHidden()
    assert not win.render_progress.isHidden()
    win._preview_busy = False
    win._update_render_progress()
    assert win.render_label.isHidden()
    assert win.render_progress.isHidden()
    win.close()


def test_startup_pdf_paths_accepts_existing_pdfs(tmp_path):
    first = tmp_path / "one.pdf"
    second = tmp_path / "TWO.PDF"
    other = tmp_path / "notes.txt"
    for path in (first, second, other):
        path.write_bytes(b"test")

    assert startup_pdf_paths(["app.exe", str(first), "--ignored", str(other), str(second)]) == [
        os.path.abspath(first),
        os.path.abspath(second),
    ]


def test_startup_pdf_paths_ignores_missing_files(tmp_path):
    assert startup_pdf_paths(["app.exe", str(tmp_path / "missing.pdf")]) == []


def test_pdf_registration_quotes_paths_and_leaves_user_choice_alone(tmp_path):
    from app.windows_integration import association_entries, APP_REG_NAME, PROG_ID

    executable = tmp_path / "含 空格" / APP_EXE_NAME
    entries = association_entries(str(executable))
    command = next(value for path, name, value in entries if path.endswith(PROG_ID + "\\shell\\open\\command"))
    assert command == f'"{executable.resolve()}" "%1"'
    assert any(name == APP_REG_NAME and path == "Software\\RegisteredApplications" for path, name, value in entries)
    assert any(path.endswith("Capabilities\\FileAssociations") and name == ".pdf" and value == PROG_ID for path, name, value in entries)
    assert all("userchoice" not in path.lower() for path, name, value in entries)
    assert next(v for p, n, v in entries if n == "FriendlyAppName") == APP_NAME


def test_register_application_uses_only_current_user(tmp_path, monkeypatch):
    import sys
    import types
    import ctypes
    from contextlib import nullcontext
    from app.windows_integration import register_pdf_application

    executable = tmp_path / "reader.exe"
    executable.write_bytes(b"synthetic executable for registry test")
    (tmp_path / "pdf.ico").write_bytes(b"synthetic icon for registry test")
    writes, notifications = [], []
    registry = types.SimpleNamespace(HKEY_CURRENT_USER="current-user", REG_SZ=1)
    registry.CreateKey = lambda root, path: nullcontext((root, path))
    registry.SetValueEx = lambda key, name, reserved, kind, value: writes.append((key, name, value))
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(shell32=types.SimpleNamespace(
        SHChangeNotify=lambda *args: notifications.append(args))), raising=False)
    register_pdf_application(str(executable))
    assert writes and all(key[0] == "current-user" for key, name, value in writes)
    assert len(notifications) == 1


def test_pdf_file_icon_is_separate_from_application_icon(tmp_path):
    from app.windows_integration import association_entries, PROG_ID
    exe = tmp_path / 'reader.exe'
    internal = tmp_path / '_internal'
    internal.mkdir()
    icon = internal / 'pdf.ico'
    icon.write_bytes(b'icon')
    entries = association_entries(str(exe))
    document = next(v for p, n, v in entries if p.endswith(PROG_ID + '\\DefaultIcon'))
    application = next(v for p, n, v in entries if n == 'ApplicationIcon')
    assert document == f'"{icon.resolve()}",0'
    assert application == f'"{exe.resolve()}",0'
    assert document != application


def test_single_exe_registration_uses_embedded_document_icon(tmp_path):
    from app.windows_integration import association_entries, pdf_icon_path, PROG_ID

    exe = tmp_path / '含 空格' / APP_EXE_NAME
    entries = association_entries(str(exe))
    document = next(v for p, n, v in entries if p.endswith(PROG_ID + '\\DefaultIcon'))
    application = next(v for p, n, v in entries if n == 'ApplicationIcon')
    assert pdf_icon_path(str(exe)) == exe.resolve()
    assert document == f'"{exe.resolve()}",1'
    assert application == f'"{exe.resolve()}",0'
    assert all('_MEI' not in v for p, n, v in entries)
