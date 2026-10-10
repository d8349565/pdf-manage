from __future__ import annotations

import os
import math
import tempfile
import threading
from contextlib import ExitStack
from dataclasses import dataclass, field

from PyQt6.QtGui import QImage

from app.workspace import PageRef
from app.output import check_cancel, commit_file, OperationCancelled
from app.startup import mark


def prepare_pdf_engine() -> None:
    """加载 PDF 引擎；普通 Python import 会协调预热和首次打开的并发。"""
    mark("pdf_import_started")
    import pymupdf
    mark("pdf_engine_ready")


def _default_encryption_algorithm() -> int:
    import pymupdf
    return pymupdf.PDF_ENCRYPT_AES_256


class PdfError(Exception):
    pass


class NeedPassword(PdfError):
    pass


class WriteNeedsConfirmation(PdfError):
    def __init__(self, message: str, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class WriteOptions:
    preserve_encryption: bool = True
    allow_structure_loss: bool = False
    operation: str = "modify"
    encryption: "EncryptionOptions | None" = None
    compression: str = ""
    editable_copy: bool = False


@dataclass(frozen=True)
class EncryptionOptions:
    user_password: str = field(repr=False)
    owner_password: str = field(repr=False)
    permissions: int = 4095
    algorithm: int = field(default_factory=_default_encryption_algorithm)

    @property
    def label(self):
        import pymupdf
        return "AES-128（兼容模式）" if self.algorithm == pymupdf.PDF_ENCRYPT_AES_128 else "AES-256"

    def validate(self):
        import pymupdf
        if self.algorithm not in (pymupdf.PDF_ENCRYPT_AES_128, pymupdf.PDF_ENCRYPT_AES_256):
            raise PdfError("不支持的 PDF 加密格式。")
        if not self.user_password or len(self.user_password) > 40 or len(self.owner_password) > 40:
            raise PdfError("打开密码须为 1～40 个字符，管理密码不能超过 40 个字符。")
        if self.algorithm == pymupdf.PDF_ENCRYPT_AES_128:
            if any(len(value) > 32 or any(not 32 <= ord(char) <= 126 for char in value)
                   for value in (self.user_password, self.owner_password)):
                raise PdfError("兼容模式密码限 1～32 个字符，仅支持字母、数字、英文符号和空格；中文密码请选 AES-256。")
        if self.permissions != 4095 and (not self.owner_password or self.owner_password == self.user_password):
            raise PdfError("限制权限时须使用独立且不同于打开密码的管理密码。")


@dataclass(frozen=True)
class WriteResult:
    path: str
    pages: tuple[PageRef, ...] = ()
    warnings: tuple[str, ...] = ()


def _path_key(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _file_stamp(path):
    stat = os.stat(path)
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


_PIXEL_FORMATS = {
    1: QImage.Format.Format_Grayscale8,
    3: QImage.Format.Format_RGB888,
}


def _to_qimage(pix: pymupdf.Pixmap) -> QImage:
    """把 pixmap 直接映射成 QImage，省掉一次 PNG 编解码（约占渲染耗时 90%）。"""
    import pymupdf
    fmt = _PIXEL_FORMATS.get(pix.n)
    if fmt is None:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        fmt = QImage.Format.Format_RGB888
    # 借用像素内存后直接转为 Qt 原生显示格式，转换结果拥有内存。
    # 在渲染线程完成转换，避免主线程 QPixmap.fromImage 再转换数百万像素。
    return QImage(pix.samples_mv, pix.width, pix.height, pix.stride, fmt).convertToFormat(
        QImage.Format.Format_RGB32)


class _Source:
    """一个已打开的源文档。MuPDF 非线程安全，故每个文档单独加锁。"""

    __slots__ = ("path", "doc", "lock", "sizes", "rotations", "closed", "data",
                 "password", "safety", "disk_stamp")

    def __init__(self, path: str, doc: pymupdf.Document, data: bytes, password,
                 authentication: int, disk_stamp, progress_callback=None) -> None:
        from app.pdfstructure import inspect_safety
        self.path = path
        self.doc = doc
        self.lock = threading.Lock()
        self.data = data
        self.password = password
        self.disk_stamp = disk_stamp
        if progress_callback:
            progress_callback(0, 0, "正在检查签名与文档结构")
        self.safety = inspect_safety(doc, authentication)
        self.sizes = []
        self.rotations = []
        for i in range(doc.page_count):
            page = doc.load_page(i)
            self.sizes.append(page.rect)
            self.rotations.append(page.rotation)
            if progress_callback:
                progress_callback(i + 1, doc.page_count, f"正在读取页面 · {os.path.basename(path)}")
        self.closed = False

    def close(self) -> None:
        with self.lock:
            self.closed = True
            self.doc.close()
            self.data = b""
            self.password = None

    def doc_locked(self) -> pymupdf.Document:
        """调用方需已持有 self.lock。关闭与渲染可能交错，这里挡掉已关闭的文档。"""
        if self.closed:
            raise PdfError("源文档已关闭")
        return self.doc

    def page_rotation(self, index: int) -> int:
        """源页面自带的 /Rotate。与尺寸一样在打开时算好，不需要加锁。"""
        try:
            return self.rotations[index]
        except IndexError:
            raise PdfError("页码超出范围") from None


class PdfStore:
    def __init__(self) -> None:
        self._sources: dict[str, _Source] = {}
        self._n = 0
        self._written_stamps = {}

    def _source(self, sid: str) -> _Source:
        src = self._sources.get(sid)
        if src is None:
            raise PdfError("源文档已关闭")
        return src

    def close_all(self) -> None:
        sources = list(self._sources.values())
        self._sources.clear()
        self._written_stamps.clear()
        for src in sources:
            src.close()

    def close_except(self, keep: set[str]) -> None:
        for sid in [s for s in self._sources if s not in keep]:
            src = self._sources.pop(sid)
            src.close()

    def _register(self, path: str, doc: pymupdf.Document, data: bytes, password,
                  authentication, disk_stamp, progress_callback=None) -> str:
        source = _Source(os.path.abspath(path), doc, data, password, authentication,
                         disk_stamp, progress_callback)
        self._n += 1
        sid = str(self._n)
        self._sources[sid] = source
        return sid

    def load(self, path: str, password: str | None = None, *, progress_callback=None) -> list[PageRef]:
        path = os.path.abspath(path)
        if progress_callback:
            progress_callback(0, 0, f"正在打开 · {os.path.basename(path)}")
        mark("pdf_open_started")
        try:
            prepare_pdf_engine()
            import pymupdf
        except Exception as exc:
            raise PdfError(f"无法加载 PDF 引擎：{exc}") from exc
        try:
            stamp = _file_stamp(path)
            with open(path, "rb") as fh:
                data = fh.read()
            if _file_stamp(path) != stamp:
                raise PdfError("读取期间文件被其他程序修改，请重新打开。")
        except OSError as exc:
            raise PdfError(f"无法打开：{exc}") from exc
        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise PdfError(f"无法打开：{exc}") from exc
        try:
            authentication = 0
            if doc.is_encrypted:
                if not password:
                    raise NeedPassword("需要密码")
            if doc.xref_get_key(-1, "Encrypt")[0] != "null":
                authentication = doc.authenticate(password or "")
                if authentication <= 0:
                    raise PdfError("密码不正确")
            if doc.page_count < 1:
                raise PdfError("PDF 没有页面")
            sid = self._register(path, doc, data, password, authentication, stamp, progress_callback)
            self._written_stamps.pop(_path_key(path), None)
            pages = [PageRef(sid, path, i, 0) for i in range(doc.page_count)]
            mark("first_pdf_loaded")
            return pages
        except (NeedPassword, PdfError):
            doc.close()
            raise
        except Exception as exc:
            doc.close()
            raise PdfError(str(exc)) from exc

    def page_size(self, ref: PageRef) -> tuple[float, float]:
        """页尺寸在打开文档时已算好，这里只查表，不需要加锁（UI 线程可放心调用）。"""
        src = self._source(ref.source_id)
        try:
            rect = src.sizes[ref.source_page]
        except IndexError:
            raise PdfError("页码超出范围") from None
        if ref.rotation % 180:
            return (rect.height, rect.width)
        return (rect.width, rect.height)

    def source_rotation(self, ref: PageRef) -> int:
        """页面在源文件里自带的 /Rotate（0/90/180/270）。同样只查表，UI 线程可放心调用。"""
        return self._source(ref.source_id).page_rotation(ref.source_page)

    def operation_reason(self, pages: list[PageRef], operation: str) -> str:
        if not pages:
            return ""
        import pymupdf
        for sid in dict.fromkeys(page.source_id for page in pages):
            safety = self._source(sid).safety
            if operation not in ("print", "editable_copy") and safety.signature != "none":
                return safety.read_only_reason
            if operation == "editable_copy":
                if safety.signature != "present":
                    return "只有已识别的签名文件可以制作可编辑副本；无法判断的签名结构继续只读保护。"
                if not safety.allows(pymupdf.PDF_PERM_MODIFY) or not safety.allows(pymupdf.PDF_PERM_COPY):
                    return "当前密码不允许修改或导出内容，不能制作可编辑副本。"
            if operation == "modify" and safety.read_only_reason:
                return safety.read_only_reason
            permission = {"print": pymupdf.PDF_PERM_PRINT, "copy": pymupdf.PDF_PERM_COPY}.get(operation)
            if permission is not None and not safety.allows(permission):
                return "当前密码不允许打印。" if operation == "print" else "当前密码不允许导出内容。"
        return ""

    def require_operation(self, pages: list[PageRef], operation: str) -> None:
        reason = self.operation_reason(pages, operation)
        if reason:
            raise PdfError(reason)

    def is_source_destination(self, dest: str) -> bool:
        for source in self._sources.values():
            if _path_key(source.path) == _path_key(dest):
                return True
            try:
                if os.path.samefile(source.path, dest):
                    return True
            except OSError:
                continue
        return False

    def _check_destination(self, dest):
        try:
            return self._inspect_destination(dest)
        except PdfError:
            raise
        except Exception as exc:
            raise PdfError(f"无法检查目标文件，请另存新文件：{exc}") from exc

    def _inspect_destination(self, dest):
        if not os.path.exists(dest):
            return
        import pymupdf
        from app.pdfstructure import inspect_safety, signature_state
        stamp = _file_stamp(dest)
        key = _path_key(dest)
        matching = [source for source in self._sources.values() if _path_key(source.path) == key]
        if not matching:
            for source in self._sources.values():
                try:
                    if os.path.samefile(source.path, dest):
                        matching.append(source)
                except OSError:
                    continue
        password = matching[-1].password if matching else None
        if matching:
            expected = self._written_stamps.get(key, matching[-1].disk_stamp)
            if expected != stamp:
                raise PdfError("目标文件在打开后被其他程序修改，请另存新文件，避免覆盖新的内容。")
        with pymupdf.open(dest) as existing:
            authentication = 0
            if existing.xref_get_key(-1, "Encrypt")[0] != "null":
                authentication = existing.authenticate(password or "")
                if authentication <= 0:
                    raise PdfError("无法检查目标文件的签名状态，请另存到新文件。")
            if signature_state(existing) != "none":
                raise PdfError("目标文件含数字签名或无法确认签名状态，禁止覆盖，请另存新文件。")
            if inspect_safety(existing, authentication).read_only_reason:
                raise PdfError("目标文件的权限不允许覆盖，请另存新文件。")
        if _file_stamp(dest) != stamp:
            raise PdfError("检查期间目标文件被其他程序修改，请另存新文件。")
        return stamp

    def _output_layout(self, pages, sources, options):
        warnings = []
        numbers = {sid: [p.source_page for p in pages if p.source_id == sid] for sid in sources}
        single = len(sources) == 1
        sid = next(iter(sources))
        preserve_structure = single and len(numbers[sid]) == len(set(numbers[sid]))
        for ordinal, (source_id, source) in enumerate(sources.items(), 1):
            special = source.safety.special_structures
            unchanged = single and numbers[source_id] == list(range(len(source.sizes)))
            risky = special if not preserve_structure else tuple(
                item for item in special if item not in ("可选内容图层", "无障碍标签结构") and not unchanged)
            if risky:
                if not options.allow_structure_loss:
                    raise WriteNeedsConfirmation(
                        "当前页面重组无法保证以下信息完整保留：\n" + "、".join(risky) +
                        "\n原文件不会覆盖。可明确确认后另存副本。", "structure")
                warnings.append(f"来源 {ordinal} 的特殊结构需在副本中核对：{'、'.join(risky)}。")
        if options.preserve_encryption and not options.encryption and not preserve_structure and any(s.safety.encrypted for s in sources.values()):
            raise WriteNeedsConfirmation("本次页面组合无法沿用各来源的原密码。\n"
                                         "请明确选择新的输出密码，或选择不加密。", "encryption")
        return numbers, preserve_structure, warnings

    def _assemble(self, pages, sources, options, progress_callback):
        import pymupdf
        from app.pdfstructure import (
            capture_navigation, copy_document_properties, namespace_form_fields,
            page_object_signature, restore_navigation,
        )
        documents = {}
        out = None
        numbers, preserve_structure, warnings = self._output_layout(pages, sources, options)
        single = len(sources) == 1
        sid = next(iter(sources))
        try:
            for source_id, source in sources.items():
                doc = pymupdf.open(stream=source.data, filetype="pdf")
                documents[source_id] = doc
                if doc.is_encrypted and doc.authenticate(source.password or "") <= 0:
                    raise PdfError("无法读取源文档的加密信息")
            snapshots = {}
            used_fields = set()
            for ordinal, (source_id, doc) in enumerate(documents.items(), 1):
                if not preserve_structure:
                    renamed = namespace_form_fields(doc, used_fields, ordinal, numbers[source_id])
                    if renamed:
                        warnings.append(f"来源 {ordinal} 的 {renamed} 个同名表单字段已增加来源编号。")
                try:
                    snapshots[source_id] = capture_navigation(
                        doc, numbers[source_id], allow_structure_loss=options.allow_structure_loss,
                        warnings=warnings)
                except ValueError as exc:
                    raise WriteNeedsConfirmation(f"来源 {ordinal}：{exc}\n"
                                                 "请明确确认后另存副本，原件不会覆盖。", "structure") from exc
            if preserve_structure:
                out = documents.pop(sid)
                if numbers[sid] != list(range(out.page_count)):
                    out.select(numbers[sid])
                for index, ref in enumerate(pages):
                    out[index].set_rotation((sources[sid].page_rotation(ref.source_page) + ref.rotation) % 360)
                    if progress_callback:
                        progress_callback(index + 1, len(pages), "正在保留文档结构并整理页面")
            else:
                out = pymupdf.open()
                remaining = {source_id: len(values) for source_id, values in numbers.items()}
                for index, ref in enumerate(pages):
                    remaining[ref.source_id] -= 1
                    out.insert_pdf(documents[ref.source_id], from_page=ref.source_page, to_page=ref.source_page,
                                   rotate=(sources[ref.source_id].page_rotation(ref.source_page) + ref.rotation) % 360,
                                   links=False, annots=True, widgets=True, join_duplicates=True,
                                   final=int(remaining[ref.source_id] == 0))
                    if progress_callback:
                        progress_callback(index + 1, len(pages), "正在组装 PDF 并保留页面对象")
                copy_document_properties(out, documents, warnings)
            for index, ref in enumerate(pages):
                if page_object_signature(out[index]) != snapshots[ref.source_id]["objects"][ref.source_page]:
                    raise PdfError(f"第 {index + 1} 页的批注或表单未完整保留，已停止写入，原文件未改动。")
            # 全页原序的单一来源保留原始跳转定义；重排、抽取及合并才重新映射。
            if not preserve_structure or numbers[sid] != list(range(len(sources[sid].sizes))):
                restore_navigation(out, pages, snapshots, warnings, merged=not single)
            password = sources[sid].password if preserve_structure and options.preserve_encryption else None
            keep_encryption = preserve_structure and options.preserve_encryption
            return out, password, keep_encryption, warnings
        except Exception:
            if out is not None:
                out.close()
            raise
        finally:
            for doc in documents.values():
                doc.close()

    def render_qimage(self, ref: PageRef, width: int) -> QImage:
        import pymupdf
        src = self._source(ref.source_id)
        with src.lock:
            page = src.doc_locked().load_page(ref.source_page)
            rect = page.rect
            base = rect.height if ref.rotation % 180 else rect.width
            zoom = max(int(width), 1) / (base or 1)
            matrix = pymupdf.Matrix(zoom, zoom).prerotate(ref.rotation)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
        image = _to_qimage(pix)
        if image.isNull():
            raise PdfError("无法渲染页面")
        return image

    def render_region(self, ref: PageRef, region: tuple[float, float, float, float], scale: float):
        """按屏幕像素密度重绘可见区域，坐标为最终旋转后的 PDF 点坐标。"""
        import pymupdf
        if not math.isfinite(scale) or scale <= 0 or not all(math.isfinite(v) for v in region):
            raise PdfError("无效的预览范围")
        src = self._source(ref.source_id)
        with src.lock:
            page = src.doc_locked().load_page(ref.source_page)
            rotation = pymupdf.Matrix(1, 1).prerotate(ref.rotation)
            rotated = page.rect * rotation
            rotation *= pymupdf.Matrix(1, 0, 0, 1, -rotated.x0, -rotated.y0)
            area = pymupdf.Rect(region) & (page.rect * rotation)
            if area.is_empty:
                raise PdfError("预览范围不在页面内")
            # 即使非常大的屏幕或异常请求也不生成无界图片。正常屏幕以原生像素绘制。
            scale = min(scale, math.sqrt(16_000_000 / (area.width * area.height)),
                        8190 / max(area.width, area.height))
            matrix = rotation * pymupdf.Matrix(scale, scale)
            pix = page.get_pixmap(matrix=matrix, clip=area * ~rotation, alpha=False)
        return _to_qimage(pix), (pix.x / scale, pix.y / scale), scale

    def write(self, pages: list[PageRef], dest: str, *, progress_callback=None,
              options: WriteOptions | None = None, register_result: bool = False, cancel=None) -> WriteResult:
        if not pages:
            raise PdfError("没有可保存的页面")
        import pymupdf
        from app.pdfstructure import document_signature, signature_state
        dest = os.path.abspath(dest)
        options = options or WriteOptions()
        check_cancel(cancel)
        if options.encryption:
            options.encryption.validate()
        if options.editable_copy != (options.operation == "editable_copy"):
            raise PdfError("签名副本必须使用专用的保护流程。")
        self.require_operation(pages, options.operation)
        if cancel:
            original_progress = progress_callback
            def guarded_progress(current, total, message):
                check_cancel(cancel)
                if original_progress:
                    original_progress(current, total, message)
            progress_callback = guarded_progress
        sources = {sid: self._source(sid) for sid in dict.fromkeys(p.source_id for p in pages)}
        if not options.editable_copy:
            for source in sources.values():
                if source.safety.signature != "none":
                    raise PdfError(source.safety.read_only_reason)
        for ref in pages:
            if not 0 <= ref.source_page < len(sources[ref.source_id].sizes) or ref.rotation % 90:
                raise PdfError("页面引用或旋转角度无效，已停止写入。")
        target_stamp = self._check_destination(dest)
        if (options.editable_copy or options.encryption or options.compression) and self.is_source_destination(dest):
            raise PdfError("此操作只能另存副本，不能覆盖来源文件。")
        if options.allow_structure_loss and self.is_source_destination(dest):
            raise PdfError("特殊结构只能另存副本，不能覆盖已打开的来源文件。")
        if not options.preserve_encryption and any(s.safety.encrypted for s in sources.values()) and self.is_source_destination(dest):
            raise PdfError("未加密输出只能另存副本，不能覆盖加密原件。")
        folder = os.path.dirname(dest) or "."
        out = None
        tmp = None
        candidate = None
        result_sid = None
        try:
            with ExitStack() as locks:
                for source in sources.values():
                    locks.enter_context(source.lock)
                    source.doc_locked()
                out, password, keep_encryption, warnings = self._assemble(pages, sources, options, progress_callback)
            check_cancel(cancel)
            if options.editable_copy:
                from app.pdfsecurity import freeze_signatures
                freeze_signatures(out, progress_callback, cancel)
                warnings.append("签名外观已冻结，原数字签名效力不会保留；其他表单仍可填写。")
            if options.compression == "send":
                if progress_callback:
                    progress_callback(0, 0, "正在将高分辨率图片降采样至 150 DPI · JPEG 质量 80")
                out.rewrite_images(dpi_threshold=151, dpi_target=150, quality=80,
                                   bitonal=False)
            elif options.compression not in ("", "quality"):
                raise PdfError("未知压缩档位")
            check_cancel(cancel)
            expected = document_signature(out)
            os.makedirs(folder, exist_ok=True)
            fd, tmp = tempfile.mkstemp(suffix=".pdf", prefix=".pdfasm-", dir=folder)
            os.close(fd)
            if progress_callback:
                progress_callback(0, 0, f"正在压缩并写入 · {os.path.basename(dest)}")
            encryption_args = dict(encryption=pymupdf.PDF_ENCRYPT_KEEP if keep_encryption else pymupdf.PDF_ENCRYPT_NONE)
            if options.encryption:
                setting = options.encryption
                password = setting.user_password
                encryption_args = dict(encryption=setting.algorithm,
                                       user_pw=password, owner_pw=setting.owner_password or password,
                                       permissions=setting.permissions)
            out.save(tmp, garbage=4, deflate=True, raise_on_repair=True, **encryption_args)
            out.close()
            out = None
            if progress_callback:
                progress_callback(0, 0, "正在重新打开并核对待保存文件")
            with open(tmp, "rb") as fh:
                data = fh.read()
            check = pymupdf.open(stream=data, filetype="pdf")
            try:
                authentication = 0
                if check.xref_get_key(-1, "Encrypt")[0] != "null":
                    authentication = check.authenticate(password or "")
                    if authentication <= 0:
                        raise PdfError("待保存文件的加密校验失败，原文件未改动。")
                if check.is_repaired or document_signature(check) != expected:
                    raise PdfError("待保存文件的页面、跳转或对象核对失败，原文件未改动。")
                if options.editable_copy and signature_state(check) != "none":
                    raise PdfError("签名验证结构未完整移除，已停止输出。")
                candidate = _Source(dest, check, data, password, authentication, _file_stamp(tmp), progress_callback)
                check = None
            finally:
                if check is not None:
                    check.close()
            # 保存过程中其他软件改写目标，或者出现新的同名文件时，不覆盖它。
            current_stamp = _file_stamp(dest) if os.path.exists(dest) else None
            if current_stamp != target_stamp:
                raise PdfError("保存过程中目标文件已改变，原文件未覆盖，请另存新文件。")
            result_pages = ()
            if register_result:
                self._n += 1
                result_sid = str(self._n)
                result_pages = tuple(PageRef(result_sid, dest, i, 0) for i in range(len(pages)))
                self._sources[result_sid] = candidate
            result = WriteResult(dest, result_pages, tuple(warnings))
            commit_file(tmp, dest, target_stamp, cancel=cancel)
            tmp = None
            self._written_stamps[_path_key(dest)] = candidate.disk_stamp
            if register_result:
                candidate = None
                result_sid = None
            return result
        except (PdfError, OperationCancelled):
            raise
        except Exception as exc:
            raise PdfError(str(exc)) from exc
        finally:
            if result_sid is not None:
                self._sources.pop(result_sid, None)
            if candidate is not None:
                candidate.close()
            if out is not None:
                out.close()
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def write_groups(self, pages: list[PageRef], groups: list[list[int]], dests: list[str], *,
                     progress_callback=None, options: WriteOptions | None = None, cancel=None) -> tuple[WriteResult, ...]:
        from app.pdfstructure import capture_navigation
        if len(groups) != len(dests):
            raise PdfError("拆分范围与输出文件数量不一致")
        if len({_path_key(dest) for dest in dests}) != len(dests):
            raise PdfError("拆分的输出文件名重复，请分别命名。")
        if any(not group or any(i < 0 or i >= len(pages) for i in group) for group in groups):
            raise PdfError("拆分范围包含无效页码")
        results = []
        options = options or WriteOptions()
        # 所有输出先检查保护边界与需要确认的内容，再开始落盘，避免确认时重跑已完成输出。
        for dest, group in zip(dests, groups):
            subset = [pages[i] for i in group]
            self.require_operation(subset, options.operation)
            self._check_destination(dest)
            sources = {sid: self._source(sid) for sid in dict.fromkeys(p.source_id for p in subset)}
            self._output_layout(subset, sources, options)
        # 只读取一次所需导航信息，预先发现需要确认的链接；不重复组装整批页面。
        all_pages = [pages[i] for group in groups for i in group]
        for sid in dict.fromkeys(page.source_id for page in all_pages):
            source = self._source(sid)
            numbers = {page.source_page for page in all_pages if page.source_id == sid}
            try:
                with source.lock:
                    capture_navigation(source.doc_locked(), numbers,
                                       allow_structure_loss=options.allow_structure_loss)
            except ValueError as exc:
                raise WriteNeedsConfirmation(f"{exc}\n请明确确认后另存副本，原件不会覆盖。", "structure") from exc
        for index, (dest, group) in enumerate(zip(dests, groups)):
            subset = [pages[i] for i in group]
            def report(current, total, message):
                if progress_callback:
                    progress_callback(index, len(dests), f"文件 {index + 1} / {len(dests)} · {message}")
            try:
                result = self.write(subset, dest, progress_callback=report, options=options, cancel=cancel)
            except OperationCancelled as exc:
                raise OperationCancelled(f"已取消，已完整写出 {len(results)} 个文件。") from exc
            except WriteNeedsConfirmation:
                raise
            except PdfError as exc:
                raise PdfError(f"已完整写出 {len(results)} 个文件。\n未完成：{os.path.basename(dest)}\n{exc}") from exc
            results.append(result)
            if progress_callback:
                progress_callback(index + 1, len(dests), f"已写出 · {os.path.basename(dest)}")
        return tuple(results)


def is_encrypted_file(path: str) -> bool:
    if not path or not os.path.exists(path):
        return False
    import pymupdf
    try:
        doc = pymupdf.open(path)
    except Exception:
        return False
    try:
        return bool(doc.is_encrypted)
    finally:
        doc.close()


def apply_output_conflicts(
    groups: list[list[int]], dests: list[str], mode: str
) -> tuple[list[list[int]], list[str]] | None:
    if mode == "cancel":
        return None
    if mode == "overwrite":
        return groups, dests
    kept_g: list[list[int]] = []
    kept_d: list[str] = []
    for group, dest in zip(groups, dests):
        if not os.path.exists(dest):
            kept_g.append(group)
            kept_d.append(dest)
    if not kept_d:
        return None
    return kept_g, kept_d
