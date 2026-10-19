"""Isolate expensive PDF writes/renders; only the parent commits user outputs."""
from __future__ import annotations

from dataclasses import asdict
from contextlib import ExitStack
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import hashlib

from app.output import OperationCancelled, check_cancel, commit_file, stamp, cancellable_lock
from app.pdfio import PdfError, WriteNeedsConfirmation, WriteOptions, WriteResult
from app.storage import TemporaryWorkspace
from app.wordconvert import _read_json, _write_json


def _worker(folder, request, progress, cancel, *, on_prepared=None):
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
    acknowledged = -1
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
            if on_prepared is not None:
                prepared = _read_json(root / "prepared.json")
                if prepared and prepared["index"] > acknowledged:
                    on_prepared(prepared["index"], prepared["result"])
                    acknowledged = prepared["index"]
                    _write_json(root / "ack.json", dict(index=acknowledged))
            time.sleep(.01 if on_prepared is not None else .05)
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


def _snapshot_sources(store, pages, dests, folder, progress, cancel):
    from app.pdfio import _path_key
    sources = {}
    snapshot_ids = list(dict.fromkeys(page.source_id for page in pages))
    # Keep guards for opened destinations even if they contribute no pages.
    for sid, source in list(store._sources.items()):
        if sid in snapshot_ids:
            continue
        for dest in dests:
            same = _path_key(source.path) == _path_key(dest)
            if not same:
                try:
                    same = os.path.samefile(source.path, dest)
                except OSError:
                    pass
            if same:
                snapshot_ids.append(sid)
                break
    for index, sid in enumerate(snapshot_ids):
        check_cancel(cancel)
        source = store._source(sid)
        if progress:
            progress(0, 0, "正在等待页面处理完成，可取消任务")
        with cancellable_lock(source.lock, cancel):
            if source.closed:
                raise PdfError("源文档已关闭")
            data = source.data
            record = dict(path=source.path, password=source.password, stamp=source.disk_stamp)
        if progress:
            progress(0, 0, f"正在准备来源快照 · {os.path.basename(source.path)}")
        snapshot = Path(folder) / f"source-{index}.pdf"
        with snapshot.open("wb") as stream:
            view = memoryview(data)
            for offset in range(0, len(view), 4 * 1024 * 1024):
                check_cancel(cancel)
                stream.write(view[offset:offset + 4 * 1024 * 1024])
        sources[sid] = dict(record, snapshot=str(snapshot))
    return sources


def _encode_pages(pages, cancel):
    payload, assets, asset_tokens = [], {}, {}
    for page in pages:
        check_cancel(cancel)
        item = asdict(page)
        for edit in item["edits"]:
            details = dict(edit["details"])
            image = details.pop("image", "")
            if image:
                token = asset_tokens.get(id(image))
                if token is None:
                    token = hashlib.sha256(image.encode("ascii")).hexdigest()
                    asset_tokens[id(image)] = token
                assets[token] = image
                details["image_asset"] = token
                edit["details"] = tuple(details.items())
        payload.append(item)
    return payload, assets


def write_pdf(store, pages, dest, *, options=None, progress_callback=None,
              register_result=False, cancel=None):
    """Use immutable source bytes so workspace edits and external-file guards survive."""
    from app.pdfio import _Source, _path_key
    dest = os.path.abspath(dest)
    if not pages:
        raise PdfError("没有可保存的页面")
    options = options or WriteOptions()
    check_cancel(cancel)
    if progress_callback:
        progress_callback(0, 0, "正在准备保存位置")
    candidate = None
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with TemporaryWorkspace("pdfwrite") as folder, tempfile.TemporaryDirectory(
                prefix=".zhixu-write-", dir=os.path.dirname(dest)) as staging:
            sources = _snapshot_sources(store, pages, [dest], folder, progress_callback, cancel)
            payload, assets = _encode_pages(pages, cancel)
            result = _worker(folder, dict(operation="write", sources=sources, assets=assets,
                pages=payload, options=asdict(options), dest=dest,
                staging=staging, written_stamps=dict(store._written_stamps)), progress_callback, cancel)
            # A cancelled/killed child can only leave owned temporary files.
            # Build a lightweight preview handle from the child's validated metadata.
            if register_result:
                check_cancel(cancel)
                import pymupdf
                from app.pdfstructure import PdfSafety
                data = Path(result["temp"]).read_bytes()
                candidate = _Source.__new__(_Source)
                candidate.path, candidate.doc, candidate.data = dest, None, data
                candidate.preview_doc, candidate.preview_fonts = None, set()
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


def write_pdf_groups(store, pages, groups, dests, *, options=None, progress_callback=None, cancel=None):
    """Share source snapshots and one child; commit each verified output in the parent."""
    from app.pdfio import _path_key
    if len(groups) != len(dests):
        raise PdfError("拆分范围与输出文件数量不一致")
    if not groups:
        return ()
    dests = [os.path.abspath(dest) for dest in dests]
    results = []
    try:
        with ExitStack() as resources:
            folder = resources.enter_context(TemporaryWorkspace("pdfsplit"))
            staging = {}
            for dest in dests:
                check_cancel(cancel)
                directory = os.path.dirname(dest)
                if directory not in staging:
                    os.makedirs(directory, exist_ok=True)
                    staging[directory] = resources.enter_context(tempfile.TemporaryDirectory(
                        prefix=".zhixu-write-", dir=directory))
            used_pages = [pages[index] for index in sorted({i for group in groups for i in group})
                          if 0 <= index < len(pages)]
            sources = _snapshot_sources(store, used_pages, dests, folder, progress_callback, cancel)
            payload, assets = _encode_pages(pages, cancel)
            def commit(index, prepared):
                if index != len(results) or index >= len(dests):
                    raise PdfError("拆分任务返回顺序异常，已停止提交。")
                dest = dests[index]
                expected = tuple(prepared["expected"]) if prepared["expected"] is not None else None
                commit_file(prepared["temp"], dest, expected, cancel=cancel)
                store._written_stamps[_path_key(dest)] = stamp(dest)
                results.append(WriteResult(dest, warnings=tuple(prepared["warnings"])))
            _worker(folder, dict(operation="write_groups", sources=sources, assets=assets,
                pages=payload, options=asdict(options or WriteOptions()), groups=groups,
                dests=dests, staging=staging, written_stamps=dict(store._written_stamps)),
                progress_callback, cancel, on_prepared=commit)
        return tuple(results)
    except OperationCancelled as exc:
        raise OperationCancelled(f"已取消，已完整写出 {len(results)} 个文件。") from exc
    except WriteNeedsConfirmation:
        raise
    except Exception as exc:
        raise PdfError(f"已完整写出 {len(results)} 个文件。\n{exc}") from exc


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


def render_watermark_asset(text, color, image="", cancel=None):
    """Generate only the watermark in a child process, never process a business page."""
    from PyQt6.QtGui import QImage
    with TemporaryWorkspace("watermark") as folder:
        result = _worker(folder, dict(operation="watermark_asset", text=text, color=color, image=image), None, cancel)
        asset = QImage(str(Path(folder) / "watermark.png"))
        if asset.isNull():
            raise PdfError("水印预览素材无法加载，请重试。")
        return asset, result["width"], result["height"]


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
        if request.get("operation") == "watermark_asset":
            import pymupdf
            from app.pdfmarks import watermark_base
            data, width, height = watermark_base(request["text"], tuple(request["color"]), request.get("image", ""))
            with pymupdf.open(stream=data, filetype="pdf") as doc:
                scale = min(1600 / max(width, 1), 2048 / max(width, height, 1),
                            math.sqrt(4_000_000 / max(1, width * height)))
                doc[0].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=True).save(root / "watermark.png")
            result = dict(width=width, height=height)
        elif request.get("operation") == "preview":
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
        elif request.get("operation") in ("write", "write_groups"):
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
                for edit in item["edits"]:
                    details = dict(edit["details"])
                    asset = details.pop("image_asset", None)
                    if asset is not None:
                        details["image"] = request["assets"][asset]
                        edit["details"] = list(details.items())
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
            if request["operation"] == "write":
                result = store.write(pages, request["dest"], options=WriteOptions(**settings),
                    progress_callback=report, _prepare_only=True, _staging_dir=request["staging"])
            else:
                from app.pdfio import _path_key
                completed = 0
                def prepare(subset, dest, **kwargs):
                    nonlocal completed
                    prepared = store.write(subset, dest, **kwargs, _prepare_only=True,
                        _staging_dir=request["staging"][os.path.dirname(dest)])
                    _write_json(root / "prepared.json", dict(index=completed, result=prepared))
                    # Do not prepare the next file until the parent has committed
                    # this one. A killed worker can only leave owned staging files.
                    deadline = time.monotonic() + 30
                    while _read_json(root / "ack.json") != dict(index=completed):
                        if time.monotonic() > deadline:
                            raise PdfError("主进程未确认拆分结果，后台任务已停止。")
                        time.sleep(.01)
                    store._written_stamps[_path_key(dest)] = stamp(dest)
                    completed += 1
                    return WriteResult(dest, warnings=tuple(prepared["warnings"]))
                store.write_groups(pages, request["groups"], request["dests"],
                    options=WriteOptions(**settings), progress_callback=report, _writer=prepare)
                result = dict(success=True)
        else:
            raise PdfError("不支持的 PDF 工作任务。")
        _write_json(root / "result.json", result)
    except WriteNeedsConfirmation as exc:
        _write_json(root / "result.json", dict(error=str(exc), confirmation=exc.kind))
    except Exception as exc:
        _write_json(root / "result.json", dict(error=str(exc)))
    finally:
        store.close_all()
