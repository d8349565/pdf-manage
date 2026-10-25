from __future__ import annotations

import os
import math
import tempfile
import threading
from collections import Counter, OrderedDict
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass, field, replace

from PyQt6.QtGui import QImage

from app.workspace import PageRef
from app.output import check_cancel, commit_file, OperationCancelled, cancellable_lock
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
    compression_dpi: int = 300
    compression_quality: int = 92
    compression_grayscale: bool = False
    editable_copy: bool = False
    page_numbers: "PageNumberOptions | None" = None
    outline: tuple | None = None


# Colour/grayscale images only; keep monochrome scans at their original resolution.
COMPRESSION_PRESETS = {"high": (300, 92), "balanced": (200, 85),
                       "send": (150, 80), "small": (120, 72)}


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
        if len(self.user_password) > 40 or len(self.owner_password) > 40:
            raise PdfError("打开密码和管理密码不能超过 40 个字符。")
        if not self.user_password and self.permissions == 4095:
            raise PdfError("请选择打开密码保护，或限制打印与复制权限。")
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
                 "password", "safety", "disk_stamp", "label_rules", "label_starts",
                 "preview_doc", "preview_fonts", "import_warnings", "needs_encryption_choice",
                 "lost_structures", "safety_scan", "preview_revision", "geometry_limitations", "requires_copy")

    def __init__(self, path: str, doc: pymupdf.Document, data: bytes, password,
                  authentication: int, disk_stamp, progress_callback=None, *, defer_safety=False) -> None:
        from app.pdfstructure import inspect_safety, signature_steps
        self.path = path
        self.doc = doc
        self.preview_doc = None
        self.preview_fonts = set()
        self.preview_revision = 0
        self.safety_scan = signature_steps(doc) if defer_safety else None
        self.import_warnings = ()
        self.lock = threading.Lock()
        self.data = data
        self.password = password
        self.disk_stamp = disk_stamp
        self.needs_encryption_choice = False
        self.lost_structures = ()
        self.requires_copy = False
        if progress_callback:
            progress_callback(0, 0, "正在检查签名与文档结构")
        self.safety = inspect_safety(doc, authentication, signature='pending' if defer_safety else None)
        self.geometry_limitations = tuple(dict.fromkeys(label for key, label in (
            ('Names/Dests', '命名跳转定位'), ('Dests', '命名跳转定位'),
            ('OpenAction', '文档打开动作'), ('AA', '文档附加动作'))
            if doc.xref_get_key(doc.pdf_catalog(), key)[0] != 'null'))
        self.label_rules = tuple(doc.get_page_labels())
        self.label_starts = tuple(rule["startpage"] for rule in self.label_rules)
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
            self.safety_scan = None
            if self.preview_doc is not None:
                self.preview_doc.close()
                self.preview_doc = None
            self.preview_fonts.clear()
            if self.doc is not None:
                self.doc.close()
                self.doc = None
            self.data = b""
            self.password = None

    def doc_locked(self) -> pymupdf.Document:
        """调用方需已持有 self.lock。关闭与渲染可能交错，这里挡掉已关闭的文档。"""
        if self.closed:
            raise PdfError("源文档已关闭")
        if self.doc is None:
            # Outputs already validated in the worker need no second eager parse
            # in the GUI process. Open their native preview handle on first use.
            import pymupdf
            doc = pymupdf.open(stream=self.data, filetype="pdf")
            if doc.is_encrypted and doc.authenticate(self.password or "") <= 0:
                doc.close()
                raise PdfError("处理结果密码验证失败。")
            self.doc = doc
        return self.doc

    def preview_page_locked(self, index):
        from app.fontpreview import page_font_maps
        original = self.doc_locked()
        mappings = page_font_maps(original.load_page(index), self.preview_fonts)
        if mappings:
            self.preview_revision += 1
            if self.preview_doc is None:
                import pymupdf
                preview = pymupdf.open(stream=self.data, filetype="pdf")
                if preview.is_encrypted and preview.authenticate(self.password or "") <= 0:
                    preview.close()
                    raise PdfError("预览密码验证失败。")
                self.preview_doc = preview
            for descendant, mapping in mappings:
                xref = self.preview_doc.get_new_xref()
                self.preview_doc.update_object(xref, "<<>>")
                self.preview_doc.update_stream(xref, mapping)
                self.preview_doc.xref_set_key(descendant, "CIDToGIDMap", f"{xref} 0 R")
        return (self.preview_doc if self.preview_doc is not None else original).load_page(index)

    def page_rotation(self, index: int) -> int:
        """源页面自带的 /Rotate。与尺寸一样在打开时算好，不需要加锁。"""
        try:
            return self.rotations[index]
        except IndexError:
            raise PdfError("页码超出范围") from None


class PdfStore:
    def __init__(self) -> None:
        self._owned_workspaces = {}
        self._native_lock = threading.RLock()
        self._sources: dict[str, _Source] = {}
        self._n = 0
        self._written_stamps = {}
        self._draft_lock = threading.RLock()
        self._draft = None
        self._draft_key = None
        self._text_cache = None
        self._text_cache_bytes = 0
        self._annotation_cache = OrderedDict()
        self._annotation_lock = threading.Lock()
        self._active_render_key = None

    def _source(self, sid: str) -> _Source:
        src = self._sources.get(sid)
        if src is None:
            raise PdfError("源文档已关闭")
        return src

    def close_all(self) -> None:
        self._clear_edit_caches()
        with self._annotation_lock:
            self._annotation_cache.clear()
        sources = list(self._sources.values())
        self._sources.clear()
        self._written_stamps.clear()
        for src in sources:
            src.close()
        for workspace in self._owned_workspaces.values():
            workspace.cleanup()
        self._owned_workspaces.clear()

    def close_except(self, keep: set[str]) -> None:
        display = getattr(self, '_display_key', None)
        if display is not None and display[0][0].split(':edit:', 1)[0] not in keep:
            self._clear_edit_caches()
        if self._draft_key is not None and self._draft_key[0] not in keep:
            self._clear_edit_caches()
        for sid in [s for s in self._sources if s not in keep]:
            src = self._sources.pop(sid)
            src.close()
            workspace = self._owned_workspaces.pop(sid, None)
            if workspace is not None:
                workspace.cleanup()
        with self._annotation_lock:
            for key in list(self._annotation_cache):
                if key[0].split(":edit:", 1)[0] not in keep:
                    self._annotation_cache.pop(key)

    def _register(self, path: str, doc: pymupdf.Document, data: bytes, password,
                  authentication, disk_stamp, progress_callback=None, *, defer_safety=False) -> str:
        source = _Source(os.path.abspath(path), doc, data, password, authentication,
                          disk_stamp, progress_callback, defer_safety=defer_safety)
        self._n += 1
        sid = str(self._n)
        self._sources[sid] = source
        return sid

    def load(self, path: str, password: str | None = None, *, progress_callback=None, cancel=None,
             defer_safety=False) -> list[PageRef]:
        with cancellable_lock(self._native_lock, cancel):
            return self._load(path, password, progress_callback=progress_callback, cancel=cancel,
                              defer_safety=defer_safety)

    def load_clipboard_image(self, image, *, progress_callback=None, cancel=None):
        from pathlib import Path
        from app.storage import TemporaryWorkspace
        if image.isNull() or image.width()*image.height()>64_000_000:
            raise PdfError('剪贴板图片无效或超过 6400 万像素。')
        check_cancel(cancel)
        workspace=TemporaryWorkspace('clipboard')
        try:
            path=str(Path(workspace.name)/'剪贴板图片.png')
            if progress_callback:
                progress_callback(0,0,'正在准备剪贴板图片…')
            if not image.save(path,'PNG'):
                raise PdfError('无法准备剪贴板图片，请检查缓存目录的可用空间。')
            pages=self.load(path,progress_callback=progress_callback,cancel=cancel)
            self._owned_workspaces[pages[0].source_id]=workspace
            return pages
        except Exception:
            workspace.cleanup()
            raise

    def _load(self, path, password=None, *, progress_callback=None, cancel=None, defer_safety=False):
        path = os.path.abspath(path)
        check_cancel(cancel)
        if cancel:
            original_progress = progress_callback
            def progress_callback(current, total, message):
                check_cancel(cancel)
                if original_progress:
                    original_progress(current, total, message)
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
            warnings = ()
            is_image = False
            if path.lower().endswith('.ofd'):
                from app.pdfworker import import_ofd
                data, warnings = import_ofd(path, progress_callback, cancel)
            else:
                from app.imaging import IMAGE_SUFFIXES, image_pdf_data
                is_image = path.lower().endswith(IMAGE_SUFFIXES)
                if is_image:
                    data = image_pdf_data([path], progress_callback, cancel)
                else:
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
            sid = self._register(path, doc, data, password, authentication, stamp, progress_callback,
                                 defer_safety=defer_safety)
            self._source(sid).import_warnings = tuple(warnings)
            self._source(sid).requires_copy = is_image
            if cancel and cancel():
                self._sources.pop(sid).close()
                check_cancel(cancel)
            self._written_stamps.pop(_path_key(path), None)
            pages = [PageRef(sid, path, i, 0) for i in range(doc.page_count)]
            # One bounded batch avoids a transient read-only/banner/layout change
            # for small PDFs; complex documents yield the rest to the reader queue.
            if defer_safety:
                self.prepare_safety_step(pages[0])
            mark("first_pdf_loaded")
            mark('document_loaded', request_id=int(sid))
            return pages
        except OperationCancelled:
            if not doc.is_closed:
                doc.close()
            raise
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
        for edit in reversed(ref.edits):
            if edit.kind == 'page_geometry':
                options = dict(edit.details)
                width, height = options['width'], options['height']
                if (src.page_rotation(ref.source_page) + ref.rotation) % 180:
                    return height, width
                return width, height
        if ref.rotation % 180:
            return (rect.height, rect.width)
        return (rect.width, rect.height)

    def split_page(self, ref: PageRef, direction: str, cuts: tuple[float, ...], *, fit_a4=False):
        """Create vector-preserving split pages in memory; the original source stays untouched."""
        import pymupdf as p
        from app.pdfstructure import copy_document_properties

        self.require_operation([ref], "modify")
        self.require_operation([ref], "copy")
        stops = (0.0, *cuts, 1.0)
        if (direction not in ("vertical", "horizontal") or len(cuts) not in (1, 2)
                or any(not math.isfinite(v) for v in cuts)
                or any(b - a < .05 - 1e-9 for a, b in zip(stops, stops[1:]))):
            raise ValueError("分割位置无效，每一部分至少保留页面的 5%。")
        origin = self._source(ref.source_id)
        with p.open() as source, p.open() as normalized, p.open() as result:
            with origin.lock:
                copy_document_properties(result, {ref.source_id: origin.doc_locked()}, [])
            with self._render_page(ref) as page:
                source.insert_pdf(page.parent, from_page=page.number, to_page=page.number,
                                  links=False, annots=True, widgets=True)
                rotation = (page.rotation + ref.rotation) % 360
            source[0].set_rotation(rotation)
            source.bake(annots=True, widgets=True)
            width, height = source[0].rect.width, source[0].rect.height
            source[0].set_rotation(0)
            visible = normalized.new_page(width=width, height=height)
            # Import the visible CropBox without changing its origin or extent.
            if source[0].get_contents():
                visible.show_pdf_page(visible.rect, source, 0, rotate=-rotation)
            for start, end in zip(stops, stops[1:]):
                clip = (p.Rect(start * width, 0, end * width, height) if direction == "vertical"
                        else p.Rect(0, start * height, width, end * height))
                with p.open() as cropped:
                    cropped.insert_pdf(normalized, from_page=0, to_page=0, links=False,
                                       annots=False, widgets=False)
                    cropped[0].clip_to_rect(clip)
                    page_width, page_height = clip.width, clip.height
                    if fit_a4:
                        short, long = p.paper_size("a4")
                        page_width, page_height = ((long, short) if clip.width > clip.height
                                                   else (short, long))
                    target = result.new_page(width=page_width, height=page_height)
                    target.show_pdf_page(target.rect, cropped, 0, clip=clip)
            data = result.tobytes(garbage=4, deflate=True)
        doc = p.open(stream=data, filetype="pdf")
        try:
            # Derived bytes remain in memory; retain the real origin's overwrite guard.
            sid = self._register(origin.path, doc, data, origin.password, 0, origin.disk_stamp)
            self._source(sid).needs_encryption_choice = (
                origin.safety.encrypted or origin.needs_encryption_choice)
            self._source(sid).requires_copy = origin.requires_copy
            self._source(sid).lost_structures = tuple(dict.fromkeys(
                (*origin.lost_structures, *origin.safety.special_structures)))
        except Exception:
            doc.close()
            raise
        return [PageRef(sid, ref.source_path, i) for i in range(len(stops) - 1)]

    def source_rotation(self, ref: PageRef) -> int:
        """页面在源文件里自带的 /Rotate（0/90/180/270）。同样只查表，UI 线程可放心调用。"""
        return self._source(ref.source_id).page_rotation(ref.source_page)

    def page_label(self, ref):
        """Cached metadata only: never wait on a render lock in the UI."""
        from bisect import bisect_right
        source = self._source(ref.source_id)
        if not source.label_rules:
            return None
        index = bisect_right(source.label_starts, ref.source_page) - 1
        if index < 0:
            return None
        import pymupdf
        rule = source.label_rules[index]
        style = rule.get("style", "")
        number = ref.source_page - rule["startpage"] + rule.get("firstpagenum", 1) - int(style in ("a", "A"))
        return pymupdf.utils.construct_label(style, rule.get("prefix", ""), number)

    def authenticate_owner(self, sid: str, password: str) -> None:
        """仅在管理密码验证成功后，更新内存中的来源权限和后续写出凭证。"""
        import pymupdf
        source = self._source(sid)
        with self._native_lock, source.lock:
            if source.closed:
                raise PdfError("源文档已关闭")
            with pymupdf.open(stream=source.data, filetype="pdf") as doc:
                authentication = doc.authenticate(password)
                if authentication <= 0 or not authentication & 4:
                    raise PdfError("管理密码不正确，无法解除权限限制。")
            source.password = password
            source.safety = replace(source.safety, owner_authenticated=True)

    def operation_reason(self, pages: list[PageRef], operation: str) -> str:
        if not pages:
            return ""
        import pymupdf
        for sid in dict.fromkeys(page.source_id for page in pages):
            safety = self._source(sid).safety
            if safety.signature == 'pending':
                if operation not in ('copy', 'print'):
                    return safety.read_only_reason
            elif operation not in ("print", "editable_copy", "raster_copy") and safety.signature != "none":
                return safety.read_only_reason
            if operation == "decrypt" and safety.needs_owner_for_decryption:
                return "解除打印、复制等权限限制需要管理密码。"
            if operation == "editable_copy":
                if safety.signature != "present":
                    return "只有已识别的签名文件可以制作可编辑副本；无法判断的签名结构继续只读保护。"
                if not safety.allows(pymupdf.PDF_PERM_MODIFY) or not safety.allows(pymupdf.PDF_PERM_COPY):
                    return "当前密码不允许修改或导出内容，不能制作可编辑副本。"
            if operation == "modify" and safety.read_only_reason:
                return safety.read_only_reason
            permission = {"print": pymupdf.PDF_PERM_PRINT, "copy": pymupdf.PDF_PERM_COPY,
                          "raster_copy": pymupdf.PDF_PERM_COPY,
                          "annotate": pymupdf.PDF_PERM_ANNOTATE, "content_edit": pymupdf.PDF_PERM_MODIFY,
                          "outline": pymupdf.PDF_PERM_MODIFY}.get(operation)
            if permission is not None and not safety.allows(permission):
                return {"print": "当前密码不允许打印。", "copy": "当前密码不允许导出内容。",
                        "raster_copy": "当前密码不允许导出影像副本，请使用管理密码重新打开。",
                        "annotate": "当前密码不允许添加或修改批注。", "content_edit": "当前密码不允许修改页面内容。",
                        "outline": "当前密码不允许修改大纲。"}[operation]
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
        if os.fspath(dest).lower().endswith('.ofd'):
            raise PdfError('OFD 原文件受保护，请另存为 PDF。')
        for source in self._sources.values():
            if source.path.lower().endswith('.ofd'):
                same = _path_key(source.path) == _path_key(dest)
                try:
                    same = same or os.path.samefile(source.path, dest)
                except OSError:
                    pass
                if same:
                    raise PdfError('OFD 原文件受保护，请另存为 PDF。')
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

    def _output_layout(self, pages, sources, options, *, force_rebuild=False):
        warnings = []
        numbers = {sid: [p.source_page for p in pages if p.source_id == sid] for sid in sources}
        single = len(sources) == 1
        sid = next(iter(sources))
        preserve_structure = not force_rebuild and single and len(numbers[sid]) == len(set(numbers[sid]))
        for ordinal, (source_id, source) in enumerate(sources.items(), 1):
            special = source.safety.special_structures
            unchanged = single and numbers[source_id] == list(range(len(source.sizes)))
            risky = special if not preserve_structure else tuple(
                item for item in special if item not in ("可选内容图层", "无障碍标签结构") and not unchanged)
            risky = tuple(dict.fromkeys((*source.lost_structures, *risky)))
            if risky:
                if not options.allow_structure_loss:
                    raise WriteNeedsConfirmation(
                        "当前页面重组无法保证以下信息完整保留：\n" + "、".join(risky) +
                        "\n原文件不会覆盖。可明确确认后另存副本。", "structure")
                warnings.append(f"来源 {ordinal} 的特殊结构需在副本中核对：{'、'.join(risky)}。")
        if (options.preserve_encryption and not options.encryption
                and (any(s.needs_encryption_choice for s in sources.values())
                     or not preserve_structure and any(s.safety.encrypted for s in sources.values()))):
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
        copy_documents = {}
        copy_signatures = {}
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
            if (preserve_structure and documents[sid].is_form_pdf
                    and (numbers[sid] != list(range(len(sources[sid].sizes)))
                         or len({ref.copy_group for ref in pages}) > 1)):
                # Document.select removes the AcroForm catalog, even when widgets remain visible.
                numbers, preserve_structure, warnings = self._output_layout(
                    pages, sources, options, force_rebuild=True)
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
            if not preserve_structure:
                groups = {}
                for ref in pages:
                    if ref.copy_group:
                        groups.setdefault((ref.source_id, ref.copy_group), []).append(ref.source_page)
                for ordinal, (key, selected) in enumerate(groups.items(), len(documents) + 1):
                    source = sources[key[0]]
                    doc = pymupdf.open(stream=source.data, filetype="pdf")
                    copy_documents[key] = doc
                    if doc.is_encrypted and doc.authenticate(source.password or "") <= 0:
                        raise PdfError("无法读取复制页面的信息")
                    namespace_form_fields(doc, used_fields, ordinal, selected)
                    copy_signatures[key] = {number: page_object_signature(doc[number]) for number in set(selected)}
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
                keys = [(ref.source_id, ref.copy_group) if (ref.source_id, ref.copy_group) in copy_documents
                        else (ref.source_id, 0) for ref in pages]
                remaining = Counter(keys)
                form_data = {}
                for key, ref in zip(keys, pages):
                    doc = copy_documents.get(key, documents[ref.source_id])
                    if key not in form_data and doc.is_form_pdf:
                        form_data[key] = doc.tobytes()
                for index, ref in enumerate(pages):
                    key = keys[index]
                    remaining[key] -= 1
                    doc = copy_documents.get(key, documents[ref.source_id])
                    # MuPDF can drop sibling widgets when a form source handle is reused.
                    with (pymupdf.open(stream=form_data[key], filetype="pdf")
                          if key in form_data else nullcontext(doc)) as insert_source:
                        out.insert_pdf(insert_source, from_page=ref.source_page, to_page=ref.source_page,
                                       rotate=(sources[ref.source_id].page_rotation(ref.source_page) + ref.rotation) % 360,
                                       links=False, annots=True, widgets=True, join_duplicates=True,
                                       final=int(key in form_data or remaining[key] == 0))
                    if progress_callback:
                        progress_callback(index + 1, len(pages), "正在组装 PDF 并保留页面对象")
                copy_document_properties(out, documents, warnings,
                    source_paths={sid: _path_key(source.path) for sid, source in sources.items()})
            for index, ref in enumerate(pages):
                expected = copy_signatures.get((ref.source_id, ref.copy_group),
                                               snapshots[ref.source_id]["objects"])[ref.source_page]
                if page_object_signature(out[index]) != expected:
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
            for doc in (*documents.values(), *copy_documents.values()):
                doc.close()

    def prepare_safety_step(self, ref):
        source = self._source(ref.source_id)
        with source.lock:
            source.doc_locked()
            scan = getattr(source, 'safety_scan', None)
            if scan is None:
                return True
            try:
                next(scan)
            except StopIteration as result:
                source.safety = replace(source.safety, signature=result.value)
                source.safety_scan = None
                mark('document_protection_ready', request_id=int(ref.source_id))
                return True
            return False

    def prepare_annotations(self, ref):
        with self._render_page(ref) as page:
            self._cache_annotations(ref, page)
        return self.cached_annotations(ref)

    def render_preview(self, ref, width):
        return self.render_qimage(ref, width, prepare_annotations=False)

    def render_qimage(self, ref: PageRef, width: int, *, prepare_annotations=True) -> QImage:
        import pymupdf
        with self._render_page(ref) as page:
            rect = page.rect
            base = rect.height if ref.rotation % 180 else rect.width
            zoom = max(int(width), 1) / (base or 1)
            matrix = pymupdf.Matrix(zoom, zoom).prerotate(ref.rotation)
            pix = self._display_for_page(ref, page).get_pixmap(matrix=matrix, alpha=False)
            try:
                if prepare_annotations:
                    self._cache_annotations(ref, page)
            except Exception:
                # A malformed annotation must not prevent the page image loading.
                pass
        image = _to_qimage(pix)
        if image.isNull():
            raise PdfError("无法渲染页面")
        return image

    def _display_for_page(self, ref, page):
        if self._active_render_key is not None and ref.render_key != self._active_render_key:
            return page.get_displaylist(annots=True)
        self._select_page_cache(ref)
        if self._display_list is None:
            self._display_list = page.get_displaylist(annots=True)
        return self._display_list

    def _select_page_cache(self, ref):
        key = (ref.render_key, getattr(self._source(ref.source_id), 'preview_revision', 0))
        if getattr(self, '_display_key', None) != key:
            self._display_key = key
            self._display_list = None
            self._textpages = OrderedDict()

    def _textpage_for_page(self, ref, page, flags):
        # TextPage excludes annotation text, matching Page.get_text's semantics.
        if self._active_render_key is not None and ref.render_key != self._active_render_key:
            return page.get_textpage(flags=flags)
        self._select_page_cache(ref)
        cached = self._textpages.get(flags)
        if cached is None:
            text = page.get_textpage(flags=flags)
            # PyMuPDF's TextPage.parent is weak; keep its Page alive too.
            self._textpages[flags] = (page, text)
            while len(self._textpages) > 2:
                self._textpages.popitem(last=False)
        else:
            text = cached[1]
        self._textpages.move_to_end(flags)
        return text

    def _cache_annotations(self, ref, page):
        from app.pdfedits import annotation_summary
        import pymupdf
        with self._annotation_lock:
            if ref.render_key in self._annotation_cache:
                return
        extra = pymupdf.Matrix(1, 1).prerotate(ref.rotation)
        rotated = page.rect * extra
        extra *= pymupdf.Matrix(1, 0, 0, 1, -rotated.x0, -rotated.y0)
        matrix = page.rotation_matrix * extra
        # Prepared off the GUI thread. Mouse hover never builds text/table snapshots.
        boxes = tuple(tuple(pymupdf.Rect(word[:4]) * matrix)
                       for word in self._textpage_for_page(ref, page, pymupdf.TEXTFLAGS_WORDS).extractWORDS()
                      if str(word[4]).strip())
        annotations = annotation_summary(page)
        summary = dict(annotations=annotations, text_boxes=boxes, matrix=tuple(matrix),
                       note_rects=tuple(tuple(pymupdf.Rect(item['rect']) * matrix)
                           for item in annotations if item['kind'] == 0 and not item['image_settings']))
        with self._annotation_lock:
            self._annotation_cache[ref.render_key] = summary
            while len(self._annotation_cache) > 128:
                self._annotation_cache.popitem(last=False)

    def cached_annotations(self, ref):
        """GUI-safe metadata lookup: no native PDF access or document lock."""
        with self._annotation_lock:
            hit = self._annotation_cache.get(ref.render_key)
            if hit is not None:
                self._annotation_cache.move_to_end(ref.render_key)
            return hit

    def image_editor_preview(self, ref, identifier, width):
        """Prepare a bounded source preview and a backdrop once per selection."""
        import pymupdf as p
        from PyQt6.QtCore import Qt
        from app.pdfimages import IMAGE_SOURCE, MAX_IMAGE_PIXELS, image_settings
        from app.workspace import PageEdit
        self.require_operation([ref], 'content_edit')
        with self._render_page(ref) as page:
            annot = next((a for a in (page.annots() or ()) if a.info.get('id') == identifier), None)
            if annot is None or image_settings(page, annot) is None:
                raise PdfError('要编辑的图片已不存在。')
            source = int(page.parent.xref_get_key(annot.xref, IMAGE_SOURCE)[1].split()[0])
            w = int(page.parent.xref_get_key(source, 'Width')[1])
            h = int(page.parent.xref_get_key(source, 'Height')[1])
            if w <= 0 or h <= 0 or w * h > MAX_IMAGE_PIXELS:
                raise PdfError('图片像素过多，无法编辑。')
            pix = p.Pixmap(page.parent, source)
            mask_kind, mask = page.parent.xref_get_key(source, 'SMask')
            if mask_kind == 'xref':
                pix = p.Pixmap(pix, p.Pixmap(page.parent, int(mask.split()[0])))
            if pix.colorspace and pix.colorspace.n not in (1, 3):
                pix = p.Pixmap(p.csRGB, pix)
            if pix.colorspace and pix.colorspace.n == 1:
                pix = p.Pixmap(p.csRGB, pix)
            fmt = QImage.Format.Format_RGBA8888_Premultiplied if pix.alpha else QImage.Format.Format_RGB888
            image = QImage(pix.samples_mv, pix.width, pix.height, pix.stride, fmt).scaled(
                1024, 1024, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation).copy()
        hidden = ref.edited(PageEdit('image_delete', annotation_id=identifier))
        background = self.render_qimage(hidden, width)
        return image, background

    def remap_annotations(self, mapping):
        with self._annotation_lock:
            for before, after in mapping.items():
                if before in self._annotation_cache:
                    self._annotation_cache[after] = self._annotation_cache[before]
            while len(self._annotation_cache) > 128:
                self._annotation_cache.popitem(last=False)

    def render_region(self, ref: PageRef, region: tuple[float, float, float, float], scale: float):
        """按屏幕像素密度重绘可见区域，坐标为最终旋转后的 PDF 点坐标。"""
        import pymupdf
        if not math.isfinite(scale) or scale <= 0 or not all(math.isfinite(v) for v in region):
            raise PdfError("无效的预览范围")
        with self._render_page(ref) as page:
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
            pix = self._display_for_page(ref, page).get_pixmap(matrix=matrix, clip=area * ~rotation, alpha=False)
        return _to_qimage(pix), (pix.x / scale, pix.y / scale), scale

    def _clear_edit_caches(self):
        with self._native_lock, self._draft_lock:
            self._display_key = self._display_list = None
            self._textpages = OrderedDict()
            if self._draft is not None:
                self._draft.close()
            self._draft = self._draft_key = None
            self._text_cache = None
            self._text_cache_bytes = 0

    @contextmanager
    def _render_page(self, ref, cancel=None):
        with cancellable_lock(self._native_lock, cancel):
            with self._render_page_locked(ref, cancel) as page:
                yield page

    @contextmanager
    def _render_page_locked(self, ref, cancel=None):
        src = self._source(ref.source_id)
        if not ref.edits:
            with cancellable_lock(src.lock, cancel):
                yield src.preview_page_locked(ref.source_page)
            return
        import pymupdf
        from app.pdfedits import apply_edits
        key = (ref.source_id, ref.source_page, ref.page_id, ref.revision)
        with cancellable_lock(self._draft_lock, cancel):
            if key != self._draft_key:
                if self._draft is not None:
                    self._draft.close()
                self._draft = self._draft_key = None
                draft = pymupdf.open()
                try:
                    with cancellable_lock(src.lock, cancel):
                        original_page = src.preview_page_locked(ref.source_page)
                        original = original_page.parent
                        draft.insert_pdf(original, from_page=ref.source_page, to_page=ref.source_page)
                        # A one-page preview omits links to other pages. Keep their
                        # rectangles as inert links so text correction still rejects them.
                        for link in original_page.get_links():
                            if link.get("kind") == pymupdf.LINK_GOTO:
                                draft[0].insert_link(dict(kind=pymupdf.LINK_URI, uri="about:blank",
                                    **{"from": pymupdf.Rect(link["from"]) * original_page.derotation_matrix}))
                    apply_edits(draft[0], ref.edits)
                    self._draft, self._draft_key = draft, key
                except Exception as exc:
                    draft.close()
                    if isinstance(exc, (ValueError, PdfError)):
                        raise
                    raise PdfError(f"无法应用页面修改：{exc}") from exc
            yield self._draft[0]

    def cached_text_snapshot(self, ref, *, include_tables=True):
        """GUI-safe lookup: never wait for native rendering or parse a page."""
        if not self._native_lock.acquire(blocking=False):
            return None
        try:
            key = (ref.render_key, include_tables)
            hit = self._text_cache.get(key) if self._text_cache is not None else None
            if hit is None and not include_tables and self._text_cache is not None:
                key = (ref.render_key, True)
                hit = self._text_cache.get(key)
            if hit is not None:
                self._text_cache.move_to_end(key)
                return hit[0]
            return None
        finally:
            self._native_lock.release()

    def text_snapshot(self, ref, *, include_tables=True):
        from collections import OrderedDict
        import pymupdf
        import sys
        with self._native_lock, self._draft_lock:
            if self._text_cache is None:
                self._text_cache = OrderedDict()
            key = (ref.render_key, include_tables)
            hit = self._text_cache.get(key)
            if hit is not None:
                self._text_cache.move_to_end(key)
                return hit[0]
            with self._render_page(ref) as page:
                from app.pdfedits import annotation_summary
                flags = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES
                snapshot = dict(raw=self._textpage_for_page(ref, page, flags).extractRAWDICT(),
                                annotations=annotation_summary(page))
                if include_tables:
                    from app.documentlayout import page_layout
                    layout, _ = page_layout(page)
                    snapshot['tables'] = [item['table'] for item in layout if item['kind'] == 'table']
                extra = pymupdf.Matrix(1, 1).prerotate(ref.rotation)
                rotated = page.rect * extra
                extra *= pymupdf.Matrix(1, 0, 0, 1, -rotated.x0, -rotated.y0)
                snapshot["matrix"] = tuple(page.rotation_matrix * extra)
            seen = set()
            def size(value):
                identity = id(value)
                if identity in seen:
                    return 0
                seen.add(identity)
                total = sys.getsizeof(value)
                if isinstance(value, dict):
                    total += sum(size(k) + size(v) for k, v in value.items())
                elif isinstance(value, (tuple, list)):
                    total += sum(size(v) for v in value)
                return total
            cost = size(snapshot)
            if cost <= 8 * 1024 * 1024:
                self._text_cache[key] = snapshot, cost
                self._text_cache_bytes += cost
                while self._text_cache_bytes > 8 * 1024 * 1024:
                    _, (_, removed) = self._text_cache.popitem(last=False)
                    self._text_cache_bytes -= removed
            return snapshot

    def search_page(self, ref, query):
        """Search displayed PDF points, including unsaved text corrections."""
        import pymupdf
        if not query.strip():
            return [], False
        with self._render_page(ref) as page:
            text = self._textpage_for_page(ref, page, pymupdf.TEXT_DEHYPHENATE |
                pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_PRESERVE_LIGATURES |
                pymupdf.TEXT_MEDIABOX_CLIP)
            extra = pymupdf.Matrix(1, 1).prerotate(ref.rotation)
            rotated = page.rect * extra
            extra *= pymupdf.Matrix(1, 0, 0, 1, -rotated.x0, -rotated.y0)
            matrix = page.rotation_matrix * extra
            matches = []
            for quad in text.search(query, quads=True):
                points = [point * matrix for point in (quad.ul, quad.ur, quad.lr, quad.ll)]
                matches.append(tuple((point.x, point.y) for point in points))
            return matches, bool(text.extractText().strip())

    def prepare_replacement(self, ref, rectangle, text):
        from app.pdfedits import prepare_replacement
        self.require_operation([ref], "content_edit")
        with self._render_page(ref) as page:
            return prepare_replacement(page, rectangle, text)

    def number_preview(self, pages, index, options, width):
        import pymupdf
        from app.pagenumbers import stamp_number
        options.validate(len(pages))
        ref = pages[index]
        draft = pymupdf.open()
        try:
            with self._render_page(ref) as original:
                draft.insert_pdf(original.parent, from_page=original.number, to_page=original.number)
                draft[0].set_rotation((original.rotation + ref.rotation) % 360)
            page = draft[0]
            if index >= options.skip and options.mode == "visible":
                stamp_number(page, options.text(options.start + index - options.skip, len(pages) - options.skip), options, index)
            zoom = width / page.rect.width
            return _to_qimage(page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False))
        finally:
            draft.close()

    def outline_entries(self, pages, *, group_sources=None):
        from pathlib import Path
        from app.workspace import OutlineEntry
        from app.pdfstructure import _destination
        import pymupdf
        by_source = {}
        for page in pages:
            by_source.setdefault(page.source_id, {}).setdefault(page.source_page, page.page_id)
        merged = len(by_source) > 1 if group_sources is None else group_sources
        entries = []
        for sid, targets in by_source.items():
            source = self._source(sid)
            if merged:
                entries.append(OutlineEntry(1, Path(source.path).stem, next(iter(targets.values()))))
            with source.lock:
                doc = source.doc_locked()
                for level, title, target, destination in doc.get_toc(False):
                    try:
                        destination = _destination(doc, destination)
                    except ValueError:
                        destination = dict(destination, kind=pymupdf.LINK_NONE)
                    destination.pop("xref", None)
                    destination.pop("id", None)
                    if "to" in destination:
                        destination["to"] = tuple(destination["to"])
                    entries.append(OutlineEntry(level + int(merged), title,
                        targets.get(destination.get("page", target - 1)), tuple(destination.items())))
        return tuple(entries)

    def write_operation(self, pages, options):
        if options.operation != "modify" or options.outline is not None or options.page_numbers is not None:
            return options.operation
        if pages and any(page.edits for page in pages) and not any(e.operation == 'content_edit' for page in pages for e in page.edits):
            sid = pages[0].source_id
            if all(page.source_id == sid and page.source_page == i and page.rotation == 0 for i, page in enumerate(pages)) and len(pages) == len(self._source(sid).sizes):
                return "annotate"
        return options.operation

    def write(self, pages: list[PageRef], dest: str, *, progress_callback=None,
              options: WriteOptions | None = None, register_result: bool = False, cancel=None,
              _prepare_only=False, _staging_dir=None) -> WriteResult | dict:
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
        self.require_operation(pages, self.write_operation(pages, options))
        for ref in pages:
            for edit in ref.edits:
                self.require_operation([ref], edit.operation)
        if options.outline is not None:
            self.require_operation(pages, "outline")
        if options.page_numbers is not None:
            self.require_operation(pages, "content_edit" if options.page_numbers.mode == "visible" else "outline")
        if cancel:
            original_progress = progress_callback
            def guarded_progress(current, total, message):
                check_cancel(cancel)
                if original_progress:
                    original_progress(current, total, message)
            progress_callback = guarded_progress
        sources = {sid: self._source(sid) for sid in dict.fromkeys(p.source_id for p in pages)}
        if any(source.requires_copy for source in sources.values()) and self.is_source_destination(dest):
            raise PdfError('图片导入或扫描件增强后请另存副本，不能覆盖来源文件。')
        if (any(edit.kind in ('page_geometry', 'seam_stamp', 'seam_remove') for ref in pages for edit in ref.edits)
                and self.is_source_destination(dest)):
            raise PdfError('调整页面、骑缝章或纠偏后请另存副本，不能覆盖来源文件。')
        if not options.editable_copy:
            for source in sources.values():
                if source.safety.signature != "none":
                    raise PdfError(source.safety.read_only_reason)
        for ref in pages:
            if not 0 <= ref.source_page < len(sources[ref.source_id].sizes) or ref.rotation % 90:
                raise PdfError("页面引用或旋转角度无效，已停止写入。")
        target_stamp = self._check_destination(dest)
        if (options.editable_copy or options.encryption or options.compression or options.page_numbers) and self.is_source_destination(dest):
            raise PdfError("此操作只能另存副本，不能覆盖来源文件。")
        if options.allow_structure_loss and self.is_source_destination(dest):
            raise PdfError("特殊结构只能另存副本，不能覆盖已打开的来源文件。")
        if (not options.preserve_encryption
                and any(s.safety.encrypted or s.needs_encryption_choice for s in sources.values())
                and self.is_source_destination(dest)):
            raise PdfError("未加密输出只能另存副本，不能覆盖加密原件。")
        folder = os.path.dirname(dest) or "."
        out = None
        tmp = None
        candidate = None
        result_sid = None
        try:
            with ExitStack() as locks:
                for source in sources.values():
                    locks.enter_context(cancellable_lock(source.lock, cancel))
                    source.doc_locked()
                out, password, keep_encryption, warnings = self._assemble(pages, sources, options, progress_callback)
            check_cancel(cancel)
            if options.editable_copy:
                from app.pdfsecurity import freeze_signatures
                freeze_signatures(out, progress_callback, cancel)
                warnings.append("签名外观已冻结，原数字签名效力不会保留；其他表单仍可填写。")
            if options.compression in (*COMPRESSION_PRESETS, "custom"):
                dpi, quality = COMPRESSION_PRESETS.get(options.compression,
                    (options.compression_dpi, options.compression_quality))
                if not 72 <= dpi <= 600 or not 40 <= quality <= 100:
                    raise PdfError("图片分辨率须为 72～600 DPI，图片质量须为 40～100。")
                if progress_callback:
                    progress_callback(0, 0, f"正在优化图片 · {dpi} DPI / 质量 {quality}")
                from app.pdfcompress import optimize_images
                if options.compression_grayscale:
                    out.recolor(1)
                optimize_images(out, dpi=dpi, quality=quality, preserve_lossless=options.compression == 'high',
                                progress=progress_callback, cancel=cancel)
            elif options.compression == 'quality':
                from app.pdfcompress import optimize_images
                optimize_images(out, progress=progress_callback, cancel=cancel)
            elif options.compression not in ("", "quality"):
                raise PdfError("未知压缩档位")
            check_cancel(cancel)
            subset = False
            geometry_snapshot = None
            geometry_matrices = {}
            if any(edit.kind == 'page_geometry' for ref in pages for edit in ref.edits):
                from app.pdfstructure import capture_navigation
                geometry_snapshot = capture_navigation(out, range(len(pages)),
                    allow_structure_loss=options.allow_structure_loss, warnings=warnings)
            if any(ref.edits for ref in pages):
                from app.pdfedits import apply_edits
                for index, ref in enumerate(pages):
                    check_cancel(cancel)
                    if ref.edits:
                        transforms = []
                        subset |= apply_edits(out[index], ref.edits, transforms=transforms)
                        if transforms:
                            matrix = pymupdf.Matrix(1, 1)
                            for transform in transforms:
                                matrix *= transform
                            geometry_matrices[index] = matrix
            if geometry_snapshot is not None:
                from app.pagegeometry import restore_geometry_navigation
                restore_geometry_navigation(out, geometry_snapshot, geometry_matrices)
            if options.outline is not None:
                from app.pdfedits import apply_outline
                apply_outline(out, pages, options.outline, warnings, transforms=geometry_matrices)
            if options.page_numbers is not None:
                from app.pagenumbers import apply_page_numbers
                subset |= apply_page_numbers(out, options.page_numbers, progress_callback, cancel)
            if subset or (options.compression and not out.is_form_pdf and not out.has_annots()):
                out.subset_fonts()
            expected = document_signature(out)
            os.makedirs(folder, exist_ok=True)
            fd, tmp = tempfile.mkstemp(suffix=".pdf", prefix=".pdfasm-", dir=_staging_dir or folder)
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
            out.save(tmp, garbage=4, deflate=True, deflate_images=bool(options.compression),
                     deflate_fonts=bool(options.compression), use_objstms=int(bool(options.compression)),
                     raise_on_repair=True, **encryption_args)
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
                candidate = _Source(dest, check, data, password, authentication, _file_stamp(tmp))
                check = None
            finally:
                if check is not None:
                    check.close()
            # 保存过程中其他软件改写目标，或者出现新的同名文件时，不覆盖它。
            current_stamp = _file_stamp(dest) if os.path.exists(dest) else None
            if current_stamp != target_stamp:
                raise PdfError("保存过程中目标文件已改变，原文件未覆盖，请另存新文件。")
            if _prepare_only:
                from dataclasses import asdict
                prepared = dict(temp=tmp, expected=target_stamp, warnings=warnings,
                    password=password, safety=asdict(candidate.safety),
                    geometry_limitations=candidate.geometry_limitations,
                    sizes=[tuple(rect) for rect in candidate.sizes], rotations=candidate.rotations,
                    label_rules=candidate.label_rules, label_starts=candidate.label_starts)
                tmp = None
                return prepared
            result_pages = ()
            if register_result:
                self._n += 1
                result_sid = str(self._n)
                result_pages = tuple(PageRef(result_sid, dest, i, 0, page_id=old.page_id) for i, old in enumerate(pages))
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
                     progress_callback=None, options: WriteOptions | None = None, cancel=None,
                     _writer=None) -> tuple[WriteResult, ...]:
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
            check_cancel(cancel)
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
                with cancellable_lock(source.lock, cancel):
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
                result = (_writer or self.write)(subset, dest, progress_callback=report, options=options, cancel=cancel)
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
