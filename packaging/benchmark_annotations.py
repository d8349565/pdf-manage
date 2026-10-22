"""Reproducible annotation timings on an explicitly generated PDF; no business files.

Generate once with --generate, then run the same sample against --root checkouts.
Reports include input hashes, source hashes, native-call counts and Qt heartbeats.
"""
from __future__ import annotations

import argparse
from collections import Counter
import faulthandler
from functools import wraps
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import random
import sys
import threading
from time import perf_counter


CASES = ("ordinary", "dense", "large_page", "many_annotations")


def generate_sample(path):
    import pymupdf as p
    from PIL import Image

    if path.exists() or path.with_suffix(".manifest.json").exists():
        raise FileExistsError("Choose a new sample path; existing inputs are never overwritten.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with p.open() as document:
        for index in range(320):
            width, height = (1684, 2384) if index == 2 else (595, 842)
            page = document.new_page(width=width, height=height)
            if index in (1, 2):
                rows, columns = (100, 5) if index == 1 else (180, 10)
                shape = page.new_shape()
                for row in range(rows):
                    for column in range(columns):
                        x = 35 + column * (width - 70) / columns
                        y = 45 + row * (height - 90) / rows
                        shape.insert_text((x, y), f"R{row:03d} C{column:02d} value", fontsize=6.5)
                shape.commit()
            else:
                for row in range(12 if index < 4 else 2):
                    page.insert_text((45, 60 + row * 24), f"Generated page {index + 1}, line {row + 1}: ABC 123.45", fontsize=12)
            if index == 3:
                for number in range(1200):
                    note = page.add_text_annot((20 + number % 40 * 14, 170 + number // 40 * 18),
                                               f"Generated annotation {number}")
                    document.xref_set_key(note.xref, "NM", p.get_pdf_str(f"generated-note-{number}"))
        # Distinct, seeded noisy images make the input large in bytes as well as pages.
        random_source = random.Random(330022)
        for index in range(24):
            image = Image.frombytes("RGB", (1600, 2200), random_source.randbytes(1600 * 2200 * 3))
            stream = io.BytesIO()
            image.save(stream, format="JPEG", quality=70)
            page = document[4 + index * 10]
            page.insert_image((20, 120, 575, 812), stream=stream.getvalue())
        document.save(path, garbage=4, deflate=True)
    manifest = dict(generated=True, seed=330022, pages=320, bytes=path.stat().st_size,
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    cases=[dict(name=name, page=index + 1) for index, name in enumerate(CASES)],
                    annotations_on_page_4=1200, images=24)
    path.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest), flush=True)


def run_probe(args):
    faulthandler.enable()
    faulthandler.dump_traceback_later(120, exit=True)
    manifest = json.loads(args.sample.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    if manifest.get("generated") is not True or hashlib.sha256(args.sample.read_bytes()).hexdigest() != manifest["sha256"]:
        raise ValueError("Only this probe's unchanged generated sample is accepted.")
    configuration = args.output.parent / (args.output.stem + "-settings")
    configuration.mkdir(parents=True, exist_ok=False)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["LOCALAPPDATA"] = str(configuration)
    os.environ["TEMP"] = os.environ["TMP"] = str(configuration)
    os.environ.pop("PAGEARK_STARTUP_LOG", None)
    os.environ["PAGEARK_READING_LOG"] = str(configuration / "reading.jsonl")
    sys.path.insert(0, str(args.root))

    import pymupdf as p
    from PyQt6.QtCore import QCoreApplication, QEvent, QPointF, QSettings, QThreadPool, QTimer, Qt, PYQT_VERSION_STR, QT_VERSION_STR
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication
    from app import ui, storage
    from app.workspace import PageEdit

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(configuration))
    preferences = lambda *unused: QSettings(str(configuration / "preferences.ini"), QSettings.Format.IniFormat)
    ui.QSettings = storage._settings = preferences
    qapp = QApplication([])
    pool = QThreadPool.globalInstance()
    main_thread = threading.get_ident()
    events, beats, records = [], [], []

    def instrument(owner, name, label):
        original = getattr(owner, name)
        @wraps(original)
        def tracked(*positional, **keywords):
            started = perf_counter()
            try:
                return original(*positional, **keywords)
            finally:
                events.append(dict(call=label, gui=threading.get_ident() == main_thread,
                                   ms=round((perf_counter() - started) * 1000, 3)))
        setattr(owner, name, tracked)

    for owner, name, label in ((p.Page, "find_tables", "find_tables"),
                              (p.TextPage, "extractRAWDICT", "rawdict"),
                              (p.TextPage, "extractWORDS", "words"),
                              (p.DisplayList, "get_pixmap", "pixmap")):
        instrument(owner, name, label)
    heartbeat = QTimer()
    heartbeat.setTimerType(Qt.TimerType.PreciseTimer)
    heartbeat.setInterval(20)
    heartbeat.timeout.connect(lambda: beats.append(perf_counter()))
    heartbeat.start()
    window = ui.MainWindow()
    window.resize(1100, 760)
    window.show()

    def pause_timers():
        for name in ("_detail_timer", "_preview_timer", "_reading_timer", "_preparation_timer"):
            timer = getattr(window, name, None)
            if timer is not None:
                timer.stop()

    def settle():
        started = perf_counter()
        while True:
            QTest.qWait(10)
            scheduler = window.rendering
            if pool.activeThreadCount() == 0 and scheduler.active is None and not scheduler.pending:
                break
            if perf_counter() - started > 30:
                raise TimeoutError("Reader work did not settle in 30 seconds.")
        pause_timers()

    def measure(case, stage, action, complete=None, cache=None):
        QTest.qWait(60)
        events.clear()
        started = perf_counter()
        value = action()
        if complete is not None:
            while not complete():
                QTest.qWait(5)
                if perf_counter() - started > 30:
                    raise TimeoutError(f"{case}/{stage} did not complete.")
        finished = perf_counter()
        QTest.qWait(45)
        gaps = [round((right - left) * 1000, 3) for left, right in zip(beats, beats[1:])
                if right >= started and left <= finished]
        calls = Counter(item["call"] for item in events)
        gui_calls = Counter(item["call"] for item in events if item["gui"])
        row = dict(case=case, stage=stage, duration_ms=round((finished - started) * 1000, 3),
                   max_ui_gap_ms=max(gaps, default=0), heartbeat_gaps_ms=gaps,
                   calls=dict(calls), gui_calls=dict(gui_calls), events=list(events))
        if cache is not None:
            row["cache_present_before"] = cache
        if isinstance(value, dict):
            row.update(value)
        elif value is False:
            raise AssertionError(f"{case}/{stage} returned False.")
        records.append(row)
        print(json.dumps({key: value for key, value in row.items() if key not in ("events", "heartbeat_gaps_ms")}), flush=True)
        settle()

    started_setup = perf_counter()
    report = dict(label=args.label, sample=manifest, records=records,
                  python=platform.python_version(), pymupdf=p.VersionBind, pyqt=PYQT_VERSION_STR,
                  qt=QT_VERSION_STR, cpu_count=os.cpu_count(), platform=platform.platform(),
                  source_sha256={name: hashlib.sha256((args.root / "app" / name).read_bytes()).hexdigest()
                                 for name in ("editingui.py", "pdfio.py", "pdfedits.py", "documentlayout.py",
                                              "textselection.py", "rendering.py")},
                  probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  heartbeat_interval_ms=20, qt_platform="offscreen", cache_definition="application caches, not OS file caches")
    try:
        assert window._load_one(str(args.sample))
        window.refresh()
        settle()
        window.open_annotation_tools()
        settle()
        tools, view = window.annotation_tools, window.preview_view
        report["setup_ms"] = round((perf_counter() - started_setup) * 1000, 3)

        def browse():
            tools.tool.setCurrentIndex(tools.tool.findData("browse"))
            tools.clear_overlay()
            window.text_selection.clear()
            settle()

        def cold():
            settle()
            tools.snapshot = tools.snapshot_key = None
            window.text_selection.clear()
            window.store._clear_edit_caches()
            with window.store._annotation_lock:
                window.store._annotation_cache.clear()

        for index, case in enumerate(CASES):
            browse()
            window.go_to_page(index + 1)
            settle()
            assert window.ws.preview_index() == index
            measure(case, "idle", lambda: QTest.qWait(120))
            for temperature in ("cold", "warm"):
                browse()
                if temperature == "cold":
                    cold()
                def choose():
                    tools.tool.setCurrentIndex(tools.tool.findData("highlight"))
                    return tools.snapshot is not None and "raw" in tools.snapshot
                measure(case, "tool_select." + temperature, choose)
            tools.tool.setCurrentIndex(tools.tool.findData("rect"))
            settle()
            for temperature in ("cold", "warm"):
                if temperature == "cold":
                    cold()
                point = view.mapFromScene(view.page_rects[index].topLeft() + QPointF(35, 40))
                def press():
                    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
                    return bool(tools.stroke)
                measure(case, "draw_start." + temperature, press)
                # Cancellation is checked separately; this phase measures press only.
                tools.clear_overlay()
                QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
            browse()
            for temperature in ("cold", "warm"):
                window.text_selection.clear()
                if temperature == "cold":
                    cold()
                ref = window.ws.pages[index]
                cache = window.store._text_cache or {}
                cached = any(key[0] == ref.render_key for key in cache)
                origin = view.page_rects[index].topLeft()
                first = view.mapFromScene(origin + QPointF(35, 35))
                last = view.mapFromScene(origin + QPointF(370, 120))
                def select():
                    before_press = perf_counter()
                    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=first)
                    press_ms = (perf_counter() - before_press) * 1000
                    QTest.mouseMove(view.viewport(), last)
                    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=last)
                    return dict(mouse_press_ms=round(press_ms, 3))
                measure(case, "text_select." + temperature, select,
                        lambda: bool(window.text_selection.text) and getattr(window.text_selection, "_pending", None) is None,
                        cache=cached)
                window.text_selection.clear()
            tools.tool.setCurrentIndex(tools.tool.findData("note"))
            cold()
            for number in range(3):
                operation = PageEdit("note", ((400, 100 + number * 24),), text="Generated timing note",
                                     annotation_id=f"generated-commit-{index}-{number}")
                measure(case, "commit.first" if number == 0 else "commit.continuous",
                        lambda edit=operation: tools.commit(edit))
        report["success"] = True
    except Exception as exc:
        report["success"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        heartbeat.stop()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        pause_timers()
        settle()
        window.ws.reset()
        window.store.close_all()
        window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()
        faulthandler.cancel_dump_traceback_later()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--label", default="current")
    args = parser.parse_args()
    args.sample, args.root = args.sample.resolve(), args.root.resolve()
    if args.generate:
        generate_sample(args.sample)
    elif args.output is None:
        parser.error("--output is required when measuring")
    else:
        args.output = args.output.resolve()
        run_probe(args)


if __name__ == "__main__":
    main()
