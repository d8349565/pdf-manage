"""Isolate expensive PDF writes/renders; only the parent commits user outputs."""
from __future__ import annotations

from dataclasses import asdict
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

from app.output import OperationCancelled, check_cancel, commit_file, stamp
from app.pdfio import PdfError, WriteNeedsConfirmation, WriteOptions, WriteResult
from app.storage import TemporaryWorkspace
from app.wordconvert import _read_json, _write_json


def _worker(folder, request, progress, cancel):
    check_cancel(cancel)
    root = Path(folder)
    _write_json(root / "request.json", request)
    command = ([sys.executable] if getattr(sys, "frozen", False)
               else [sys.executable, "-m", "app"])
    process = subprocess.Popen([*command, "--pdf-worker", str(root)],
        cwd=str(Path(__file__).resolve().parent.parent), stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    started = time.monotonic()
    previous = None
    try:
        while process.poll() is None:
            check_cancel(cancel)
            if time.monotonic() - started > 3600:
                raise PdfError("PDF 处理超过一小时，已停止；请减少页数后重试。")
            event = _read_json(root / "event.json")
            if event and event != previous:
                if progress:
                    progress(event["current"], event["total"], event["message"])
                previous = event
            time.sleep(.05)
        check_cancel(cancel)
        result = _read_json(root / "result.json")
        if not result:
            raise PdfError("PDF 工作进程意外退出，未提交输出，原文件保留。")
        if result.get("confirmation"):
            raise WriteNeedsConfirmation(result["error"], result["confirmation"])
        if result.get("error"):
            raise PdfError(result["error"])
        return result
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        process.wait()


def write_pdf(store, pages, dest, *, options=None, progress_callback=None,
              register_result=False, cancel=None):
    """Use immutable source bytes so workspace edits and external-file guards survive."""
    from app.pdfio import _Source, _path_key
    dest = os.path.abspath(dest)
    if not pages:
        raise PdfError("没有可保存的页面")
    options = options or WriteOptions()
    check_cancel(cancel)
    candidate = None
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with TemporaryWorkspace("pdfwrite") as folder, tempfile.TemporaryDirectory(
                prefix=".zhixu-write-", dir=os.path.dirname(dest)) as staging:
            sources = {}
            snapshot_ids = list(dict.fromkeys(page.source_id for page in pages))
            # Keep overwrite/encryption guards for an opened destination even when
            # it contributes no pages to this export (including hard-link aliases).
            for sid, source in list(store._sources.items()):
                same = _path_key(source.path) == _path_key(dest)
                if not same:
                    try:
                        same = os.path.samefile(source.path, dest)
                    except OSError:
                        pass
                if same and sid not in snapshot_ids:
                    snapshot_ids.append(sid)
            for index, sid in enumerate(snapshot_ids):
                check_cancel(cancel)
                source = store._source(sid)
                with source.lock:
                    if source.closed:
                        raise PdfError("源文档已关闭")
                    data = source.data
                    record = dict(path=source.path, password=source.password, stamp=source.disk_stamp)
                if progress_callback:
                    progress_callback(0, 0, f"正在准备来源快照 · {os.path.basename(source.path)}")
                snapshot = Path(folder) / f"source-{index}.pdf"
                with snapshot.open("wb") as stream:
                    view = memoryview(data)
                    for offset in range(0, len(view), 4 * 1024 * 1024):
                        check_cancel(cancel)
                        stream.write(view[offset:offset + 4 * 1024 * 1024])
                sources[sid] = dict(record, snapshot=str(snapshot))
            result = _worker(folder, dict(operation="write", sources=sources,
                pages=[asdict(page) for page in pages], options=asdict(options), dest=dest,
                staging=staging, written_stamps=dict(store._written_stamps)), progress_callback, cancel)
            # A cancelled/killed child can only leave owned temporary files.
            # Build a lightweight preview handle from the child's validated metadata.
            if register_result:
                import pymupdf
                from app.pdfstructure import PdfSafety
                data = Path(result["temp"]).read_bytes()
                candidate = _Source.__new__(_Source)
                candidate.path, candidate.doc, candidate.data = dest, None, data
                candidate.password, candidate.lock = result["password"], threading.Lock()
                candidate.closed = False
                safety = result["safety"]
                candidate.safety = PdfSafety(**dict(safety, special_structures=tuple(safety["special_structures"])))
                candidate.sizes = [pymupdf.Rect(rect) for rect in result["sizes"]]
                candidate.rotations = result["rotations"]
                candidate.label_rules = tuple(result["label_rules"])
                candidate.label_starts = tuple(result["label_starts"])
                candidate.disk_stamp = stamp(result["temp"])
            expected = tuple(result["expected"]) if result["expected"] is not None else None
            refs = ()
            if candidate is not None:
                from app.workspace import PageRef
                store._n += 1
                sid = str(store._n)
                refs = tuple(PageRef(sid, dest, i, page_id=page.page_id) for i, page in enumerate(pages))
            commit_file(result["temp"], dest, expected, cancel=cancel)
            store._written_stamps[_path_key(dest)] = stamp(dest)
            if candidate is not None:
                store._sources[sid] = candidate
                candidate = None
            return WriteResult(dest, refs, tuple(result["warnings"]))
    except (PdfError, OperationCancelled):
        raise
    except Exception as exc:
        raise PdfError(str(exc)) from exc
    finally:
        if candidate is not None:
            candidate.close()


def render_preview(refs, passwords, progress, cancel=None):
    from PyQt6.QtGui import QImage
    with TemporaryWorkspace("pdfpreview") as folder:
        _worker(folder, dict(operation="preview", pages=[asdict(ref) for ref in refs],
                            passwords=passwords), progress, cancel)
        images = [QImage(str(Path(folder) / f"preview-{index}.png")) for index in range(len(refs))]
        if any(image.isNull() for image in images):
            raise PdfError("无法加载页面预览，请重新计算。")
        return images


def _tuples(value):
    return tuple(_tuples(item) for item in value) if isinstance(value, list) else value


def worker_main(folder):
    from app.pdfio import EncryptionOptions, PdfStore
    from app.workspace import OutlineEntry, PageEdit, PageRef
    root = Path(folder)
    request = _read_json(root / "request.json")
    store = PdfStore()
    last_report = 0.0
    def report(current, total, message):
        nonlocal last_report
        now = time.monotonic()
        if current not in (0, total) and now - last_report < .05:
            return
        last_report = now
        _write_json(root / "event.json", dict(current=current, total=total, message=message))
    try:
        if request.get("operation") == "preview":
            import pymupdf
            for index, item in enumerate(request["pages"]):
                report(index, len(request["pages"]), "正在渲染压缩前后预览")
                with pymupdf.open(item["source_path"]) as doc:
                    if doc.is_encrypted and doc.authenticate(request["passwords"][index] or "") <= 0:
                        raise PdfError("预览文件密码验证失败。")
                    page = doc[item["source_page"]]
                    width = page.rect.height if item["rotation"] % 180 else page.rect.width
                    scale = min(1100 / max(width, 1),
                                math.sqrt(4_000_000 / max(1, page.rect.width * page.rect.height)),
                                8190 / max(1, page.rect.width, page.rect.height))
                    matrix = pymupdf.Matrix(scale, scale).prerotate(item["rotation"])
                    page.get_pixmap(matrix=matrix, alpha=False).save(root / f"preview-{index}.png")
            result = dict(success=True)
        elif request.get("operation") == "write":
            loaded = {}
            for sid, record in request["sources"].items():
                refs = store.load(record["snapshot"], record["password"], progress_callback=report)
                source = store._sources.pop(refs[0].source_id)
                source.path, source.disk_stamp = record["path"], tuple(record["stamp"])
                loaded[sid] = source
            store._sources = loaded
            store._written_stamps = {key: tuple(value) for key, value in request["written_stamps"].items()}
            pages = []
            for item in request["pages"]:
                edits = tuple(PageEdit(**{key: _tuples(value) for key, value in edit.items()})
                              for edit in item.pop("edits"))
                pages.append(PageRef(**item, edits=edits))
            settings = request["options"]
            if settings["encryption"]:
                settings["encryption"] = EncryptionOptions(**settings["encryption"])
            if settings["page_numbers"]:
                from app.pagenumbers import PageNumberOptions
                settings["page_numbers"] = PageNumberOptions(**settings["page_numbers"])
            if settings["outline"] is not None:
                settings["outline"] = tuple(OutlineEntry(**dict(entry, destination=_tuples(entry["destination"])))
                                            for entry in settings["outline"])
            result = store.write(pages, request["dest"], options=WriteOptions(**settings),
                progress_callback=report, _prepare_only=True, _staging_dir=request["staging"])
        else:
            raise PdfError("不支持的 PDF 工作任务。")
        _write_json(root / "result.json", result)
    except WriteNeedsConfirmation as exc:
        _write_json(root / "result.json", dict(error=str(exc), confirmation=exc.kind))
    except Exception as exc:
        _write_json(root / "result.json", dict(error=str(exc)))
    finally:
        store.close_all()
