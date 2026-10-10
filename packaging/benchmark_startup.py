"""Windows 启动基准：计入 PyInstaller 引导时间，仅使用空窗口或合成 PDF。"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def measure(command, folder, with_pdf):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    log = folder / "startup.jsonl"
    environment = os.environ.copy()
    environment.pop("QT_QPA_PLATFORM", None)
    environment["PAGEARK_STARTUP_LOG"] = str(log)
    environment["TEMP"] = environment["TMP"] = str(folder)
    started = time.perf_counter()
    process = subprocess.Popen(command, cwd=ROOT, env=environment,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    windows = []
    app_pid = process.pid

    @callback_type
    def find(hwnd, extra):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == app_pid and user32.IsWindowVisible(hwnd):
            windows.append(hwnd)
        return True

    def records():
        if not log.is_file():
            return []
        # 另一个线程可能刚写到半行；只读取完整的 JSON 记录。
        result = []
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                result.append(json.loads(line))
            except ValueError:
                pass
        return result

    try:
        deadline = started + 30
        while not windows and time.perf_counter() < deadline:
            trace = records()
            if trace:
                # Windows venv 的 python.exe 可能是启动另一个 Python 进程的跳板。
                app_pid = trace[0]["pid"]
            user32.EnumWindows(find, 0)
            if process.poll() is not None:
                raise RuntimeError(f"窗口出现前进程退出：{process.returncode}")
            time.sleep(.005)
        if not windows:
            raise TimeoutError("30 秒内未显示窗口")
        visible_ms = (time.perf_counter() - started) * 1000
        ready_ms = None
        while time.perf_counter() < deadline:
            trace = records()
            stages = {row["stage"] for row in trace}
            if with_pdf and "first_page_ready" in stages:
                ready_ms = (time.perf_counter() - started) * 1000
                break
            if not with_pdf and ("pdf_engine_ready" in stages or not trace):
                break
            if process.poll() is not None:
                raise RuntimeError(f"启动期间进程退出：{process.returncode}")
            time.sleep(.005)
        else:
            raise TimeoutError("30 秒内未完成首次页面渲染或引擎预热")
        return dict(window_visible_ms=round(visible_ms, 2),
                    first_page_ms=round(ready_ms, 2) if ready_ms else None, stages=trace)
    finally:
        for hwnd in windows:
            user32.PostMessageW(hwnd, 0x0010, 0, 0)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, help="测量目录包或已安装的 EXE；默认测源码")
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--with-pdf", action="store_true", help="用自动生成的 12 页 PDF 测首屏")
    parser.add_argument("--output", type=Path, default=ROOT / "build/startup-benchmark.json")
    args = parser.parse_args()
    if sys.platform != "win32" or args.samples < 1:
        parser.error("仅支持 Windows，样本数须大于零")
    command = [str(args.executable.resolve())] if args.executable else [sys.executable, str(ROOT / "run.py")]
    (ROOT / "build").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="startup-benchmark-", dir=ROOT / "build") as temp:
        folder = Path(temp)
        if args.with_pdf:
            fixture = folder / "synthetic.pdf"
            import pymupdf
            with pymupdf.open() as doc:
                for index in range(12):
                    doc.new_page().insert_text((72, 100), f"Synthetic startup page {index + 1}")
                doc.save(fixture)
            command.append(str(fixture))
        runs = []
        for index in range(args.samples):
            sample_folder = folder / str(index)
            sample_folder.mkdir()
            runs.append(measure(command, sample_folder, args.with_pdf))
            print(f"{index + 1}: window={runs[-1]['window_visible_ms']} ms, first_page={runs[-1]['first_page_ms']} ms", flush=True)
        medians = {key: statistics.median(row[key] for row in runs if row[key] is not None)
                   for key in ("window_visible_ms", "first_page_ms") if runs[0][key] is not None}
        report = dict(command=command[:len(command) - 1] if args.with_pdf else command,
                      cache_state="uncontrolled; separate processes, no OS cache clearing",
                      synthetic_pdf=args.with_pdf, median=medians, runs=runs)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Median: {medians}\nReport: {args.output}")


if __name__ == "__main__":
    main()
