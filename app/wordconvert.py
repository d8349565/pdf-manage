"""Microsoft Word native PDF export in an isolated, bounded worker process.

The developer never opens customer documents. These functions run only after
the user starts a conversion in the application.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from app.output import OperationCancelled, check_cancel, commit_file, stamp

WORD_SUFFIXES = {".doc", ".docx"}
TIMEOUT_SECONDS = 180


def collect_files(paths, recursive=False, cancel=None):
    files, errors = [], []
    seen = set()
    def add(path):
        check_cancel(cancel)
        if path.suffix.lower() not in WORD_SUFFIXES or path.name.startswith("~$"):
            return
        key = os.path.normcase(os.path.realpath(path))
        if key not in seen:
            seen.add(key)
            files.append(str(path.absolute()))
    for value in paths:
        check_cancel(cancel)
        path = Path(value)
        try:
            if path.is_dir():
                if recursive:
                    for folder, dirs, names in os.walk(path, followlinks=False,
                            onerror=lambda exc: errors.append(str(exc))):
                        check_cancel(cancel)
                        # Avoid entering directory junctions or symlinks outside the chosen tree.
                        dirs[:] = [name for name in dirs if not Path(folder, name).is_symlink()
                                   and not (os.stat(Path(folder, name), follow_symlinks=False).st_file_attributes & 0x400)]
                        for name in sorted(names, key=str.casefold):
                            add(Path(folder, name))
                else:
                    for item in sorted(path.iterdir(), key=lambda p: p.name.casefold()):
                        if item.is_file():
                            add(item)
            elif path.is_file():
                add(path)
            else:
                errors.append(f"路径不存在：{value}")
        except OSError as exc:
            errors.append(f"无法读取目录或路径 {value}：{exc}")
    return files, errors


def _write_json(path, data):
    temp = Path(str(path) + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _worker_command(folder):
    if getattr(sys, "frozen", False):
        return [sys.executable, "--word-worker", folder]
    return [sys.executable, "-m", "app", "--word-worker", folder]


def _convert_one(source, target, overwrite, progress, cancel):
    import win32api
    import win32con
    import win32job
    from app.pdfio import PdfStore

    if stamp(target) is not None and not overwrite:
        return "skipped", "已有同名 PDF，按默认规则跳过", ""
    if os.path.normcase(os.path.realpath(source)) == os.path.normcase(os.path.realpath(target)):
        return "failed", "输出不能覆盖源 Word 文件", ""
    PdfStore()._check_destination(target)
    job = None
    process = None
    owned_word = None
    completed = None
    with tempfile.TemporaryDirectory(prefix=".pageark-word-", dir=str(Path(target).parent)) as folder:
        root = Path(folder)
        job_name = "PageArkWord-" + uuid.uuid4().hex
        _write_json(root / "request.json", dict(source=source, target=target, overwrite=overwrite,
                                                expected=stamp(target), job=job_name))
        try:
            job = win32job.CreateJobObject(None, job_name)
            info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
            info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
            started = time.monotonic()
            process = subprocess.Popen(_worker_command(folder), cwd=str(Path(__file__).resolve().parent.parent),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
            handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, process.pid)
            try:
                win32job.AssignProcessToJobObject(job, handle)
            finally:
                handle.Close()
            last_phase = None
            stop_reason = ""
            completed_at = None
            while True:
                event = _read_json(root / "event.json")
                pid = event.get("pid")
                if pid and owned_word is None:
                    try:
                        owned_word = win32api.OpenProcess(win32con.PROCESS_TERMINATE | win32con.SYNCHRONIZE,
                                                          False, int(pid))
                    except Exception:
                        pass  # It may have already exited after a fast conversion.
                phase = event.get("phase", "正在启动独立 Word 实例")
                if phase != last_phase:
                    progress(phase)
                    last_phase = phase
                completed = _read_json(root / "result.json")
                if process.poll() is not None:
                    break
                if completed:
                    if completed_at is None:
                        completed_at = time.monotonic()
                    elif time.monotonic() - completed_at > 5:
                        break
                if cancel() or time.monotonic() - started > TIMEOUT_SECONDS:
                    stop_reason = "cancelled" if cancel() else "timeout"
                    (root / "cancel").touch()
                    # Give an already completed commit time to publish its result and close Word.
                    deadline = time.monotonic() + 1
                    while process.poll() is None and time.monotonic() < deadline:
                        time.sleep(.05)
                    completed = _read_json(root / "result.json")
                    break
                time.sleep(.1)
            if completed:
                return completed["state"], completed.get("message", ""), completed.get("word_version", "")
            if stop_reason == "cancelled":
                return "cancelled", "已取消，未提交的临时输出已清理", ""
            if stop_reason == "timeout":
                return "failed", "单份转换超过 180 秒，已结束本任务进程；可检查密码保护、文件损坏或 Word 弹窗", ""
            return "failed", "Word 后台进程未正常完成。请确认已安装且可正常使用 Microsoft Word。", ""
        finally:
            if job:
                try:
                    win32job.TerminateJobObject(job, 1)
                except Exception:
                    pass
            if owned_word:
                try:
                    win32api.TerminateProcess(owned_word, 1)
                except Exception:
                    pass
                owned_word.Close()
            if process and process.poll() is None:
                process.kill()
            if process:
                process.wait(timeout=10)
            if job:
                job.Close()
            # Word exports into this directory; removal never touches the source document.


def convert_batch(items, overwrite, progress, cancel):
    results = []
    version = ""
    cancelled = False
    for index, (source, target) in enumerate(items):
        if cancel():
            cancelled = True
            break
        def report(phase):
            progress(index, len(items), f"{Path(source).name} · {phase}")
        try:
            state, message, word_version = _convert_one(source, target, overwrite, report, cancel)
        except Exception as exc:
            state, message, word_version = "failed", str(exc), ""
        if word_version:
            version = word_version
        if state == "cancelled":
            cancelled = True
            break
        results.append(dict(source=source, target=target, state=state, message=message))
        progress(index + 1, len(items), f"已处理 {index + 1} / {len(items)}")
    return results, cancelled or cancel(), version


def worker_main(folder):
    """Worker entry: COM stays on this process's STA; no Qt window is created."""
    import pythoncom
    import win32api
    import win32con
    import win32job
    import win32process
    from win32com.client import DispatchEx
    import pymupdf
    from app.pdfio import PdfStore

    root = Path(folder)
    request = _read_json(root / "request.json")
    source, target = request["source"], request["target"]
    expected = tuple(request["expected"]) if request.get("expected") else None
    result = dict(state="failed", message="Word 未完成转换", word_version="")
    word = document = bootstrap = job = None
    owned = False
    previous_options = {}
    step = "启动独立 Word"
    temp = root / "converted.pdf"
    cancel = lambda: (root / "cancel").exists()
    def phase(message, pid=None):
        nonlocal step
        step = message.removeprefix("正在")
        data = {"phase": message}
        if pid:
            data["pid"] = pid
        _write_json(root / "event.json", data)
    pythoncom.CoInitialize()
    try:
        check_cancel(cancel)
        if expected is not None and not request["overwrite"]:
            result.update(state="skipped", message="已有同名 PDF")
            return
        before_pids = set(win32process.EnumProcesses())
        word = DispatchEx("Word.Application")
        # DispatchEx creates our COM instance. Always close it if startup fails,
        # unless the later PID check reveals a pre-existing user process.
        owned = True
        phase("正在配置后台转换")
        word.Visible = False
        word.DisplayAlerts = 0
        word.ScreenUpdating = False
        word.AutomationSecurity = 3
        for name in ("UpdateLinksAtOpen", "UpdateLinksAtPrint", "UpdateFieldsAtPrint"):
            previous_options[name] = getattr(word.Options, name)
            setattr(word.Options, name, False)
        result["word_version"] = str(word.Version)
        check_cancel(cancel)
        # Word exposes HWND on Window, not Application. A hidden, unsaved blank
        # document provides a window before opening customer files, so an Open
        # timeout can still terminate only this task's process.
        phase("正在识别本次 Word 进程")
        bootstrap = word.Documents.Add(Visible=False)
        hwnd = int(bootstrap.Windows.Item(1).Hwnd)
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        if pid in before_pids:
            owned = False
            raise RuntimeError("无法确认这是独立 Word 进程，已停止；不会结束用户已打开的 Word。")
        phase("正在登记本次 Word 进程", pid)
        job = win32job.OpenJobObject(win32job.JOB_OBJECT_ALL_ACCESS, False, request["job"])
        handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, pid)
        try:
            win32job.AssignProcessToJobObject(job, handle)
        finally:
            handle.Close()
        # Only the parent keeps the job handle, so closing/crashing the app also
        # releases the job and terminates its isolated processes.
        job.Close()
        job = None
        bootstrap.Close(SaveChanges=False)
        bootstrap = None
        check_cancel(cancel)
        original = stamp(source)
        phase("正在只读打开文档（禁用宏与外链更新）", pid)
        # Deliberately incorrect, ephemeral password prevents a password dialog waiting forever.
        inaccessible_password = uuid.uuid4().hex
        document = word.Documents.Open(FileName=source, ConfirmConversions=False, ReadOnly=True,
            AddToRecentFiles=False, NoEncodingDialog=True, Visible=False,
            PasswordDocument=inaccessible_password, PasswordTemplate=inaccessible_password,
            WritePasswordDocument=inaccessible_password, WritePasswordTemplate=inaccessible_password)
        check_cancel(cancel)
        phase("正在使用 Word 原生 PDF 导出", pid)
        document.ExportAsFixedFormat(OutputFileName=str(temp), ExportFormat=17, OpenAfterExport=False,
            OptimizeFor=0, Range=0, Item=0, IncludeDocProps=True, KeepIRM=True, CreateBookmarks=1,
            DocStructureTags=True, BitmapMissingFonts=True, UseISO19005_1=False)
        check_cancel(cancel)
        phase("正在重新打开并检查 PDF", pid)
        with pymupdf.open(temp) as pdf:
            if not pdf.is_pdf or pdf.is_encrypted or pdf.is_repaired or pdf.page_count < 1:
                raise RuntimeError("Word 输出未通过 PDF 检查，未覆盖目标")
            for page in pdf:
                if page.rect.is_empty:
                    raise RuntimeError("Word 输出包含无效页面，未覆盖目标")
        if stamp(source) != original:
            raise RuntimeError("源 Word 在转换期间发生变化，未提交本次输出")
        PdfStore()._check_destination(target)
        commit_file(temp, target, expected, cancel=cancel)
        result.update(state="success", message="Word 原生 PDF 导出完成")
        # Record the commit before COM cleanup, which can itself stall.
        _write_json(root / "result.json", result)
    except OperationCancelled:
        result.update(state="cancelled", message="已取消")
    except Exception as exc:
        result.update(message=f"{step}失败：{exc}")
    finally:
        _write_json(root / "result.json", result)
        if document is not None and owned:
            try:
                document.Close(SaveChanges=False)
            except Exception:
                pass
        if bootstrap is not None:
            try:
                bootstrap.Close(SaveChanges=False)
            except Exception:
                pass
        if word is not None:
            for name, value in previous_options.items():
                try:
                    setattr(word.Options, name, value)
                except Exception:
                    pass
            if owned:
                try:
                    word.Quit(SaveChanges=False)
                except Exception:
                    pass
        document = bootstrap = word = None
        if job:
            job.Close()
        pythoncom.CoUninitialize()
