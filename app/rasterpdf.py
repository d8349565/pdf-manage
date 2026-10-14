"""Create image-only copies without carrying source signatures or active objects."""
from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import tempfile

import pymupdf as p

from app.output import check_cancel, commit_file, stamp
from app.pdfio import EncryptionOptions, PdfError, WriteNeedsConfirmation, WriteResult
from app.pdfstructure import signature_state


@dataclass(frozen=True)
class RasterOptions:
    dpi: int = 200
    image_format: str = 'jpeg'
    encryption: EncryptionOptions | None = None
    unencrypted_confirmed: bool = False


def rasterize_pdf(store, pages, destination, options=None, progress_callback=None, cancel=None, *, outline=None):
    """Render immutable workspace pages in independent documents and commit a new file."""
    options = options or RasterOptions()
    pages = tuple(pages)
    if not pages:
        raise PdfError('请至少选择一页。')
    if options.dpi not in (150, 200, 300) or options.image_format not in ('png', 'jpeg'):
        raise PdfError('请选择有效的影像清晰度。')
    destination = Path(destination).resolve()
    if destination.suffix.lower() != '.pdf' or not destination.parent.is_dir():
        raise PdfError('请选择已有文件夹中的 PDF 输出位置。')
    if store.is_source_destination(str(destination)):
        raise PdfError('影像副本不能覆盖已打开的原文件，请选择新文件名。')
    if destination.exists():
        raise PdfError('输出文件已存在，请选择新的文件名。')
    store.require_operation(list(pages), 'raster_copy')
    sources = {ref.source_id: store._source(ref.source_id) for ref in pages}
    if (any(source.safety.encrypted for source in sources.values()) and options.encryption is None
            and not options.unencrypted_confirmed):
        raise WriteNeedsConfirmation('影像副本无法直接沿用原密码。请选择新的输出密码或明确选择不加密。', 'encryption')
    if options.encryption:
        options.encryption.validate()
    for source in sources.values():
        if stamp(source.path) != source.disk_stamp:
            raise PdfError('原文件已发生变化，请重新打开或另存当前工作区后再生成副本。')
    warnings = ['页面已转为图片；文字、批注和表单外观固化，链接、附件及交互表单不保留。']
    if any(source.safety.signature != 'none' for source in sources.values()):
        warnings.append('此副本不保留原数字签名效力，原件保持不变。')
    geometries, appearances, labels = [], [], []
    temporary = None
    def report(current, total, message):
        check_cancel(cancel)
        if progress_callback:
            progress_callback(current, total, message)
    try:
        with ExitStack() as resources:
            documents = {}
            for sid, source in sources.items():
                doc = resources.enter_context(p.open(stream=source.data, filetype='pdf'))
                if doc.is_encrypted and doc.authenticate(source.password or '') <= 0:
                    raise PdfError('源文档密码授权已失效，请重新打开。')
                if not doc.is_pdf:
                    raise PdfError('源文件不是有效的 PDF。')
                documents[sid] = doc
            out = resources.enter_context(p.open())
            for index, ref in enumerate(pages):
                report(index, len(pages), f'正在生成第 {index + 1} 页影像')
                doc = documents[ref.source_id]
                if not 0 <= ref.source_page < doc.page_count or ref.rotation % 90:
                    raise PdfError('页面索引或旋转方向无效。')
                with ExitStack() as page_resources:
                    if ref.edits:
                        doc = page_resources.enter_context(p.open(stream=sources[ref.source_id].data, filetype='pdf'))
                        if doc.is_encrypted and doc.authenticate(sources[ref.source_id].password or '') <= 0:
                            raise PdfError('无法授权工作区页面。')
                        from app.pdfedits import apply_edits
                        apply_edits(doc[ref.source_page], ref.edits)
                    page = doc[ref.source_page]
                    rotation = (sources[ref.source_id].page_rotation(ref.source_page) + ref.rotation) % 360
                    page.set_rotation(0)
                    if (not all(math.isfinite(v) for v in page.rect) or page.rect.is_empty
                            or page.rect.width * page.rect.height * (options.dpi / 72) ** 2 > 40_000_000):
                        raise PdfError('页面尺寸过大或异常，请降低清晰度或分段处理。')
                    pix = page.get_pixmap(dpi=options.dpi, colorspace=p.csRGB, alpha=False, annots=True)
                    check_cancel(cancel)
                    copied = out.new_page(width=page.mediabox.width, height=page.mediabox.height)
                    copied.set_mediabox(page.mediabox)
                    copied.set_cropbox(page.cropbox)
                    image = pix.tobytes('png') if options.image_format == 'png' else pix.tobytes('jpg', jpg_quality=90)
                    copied.insert_image(copied.rect, stream=image, keep_proportion=False)
                    # Record the output pixels before serialization; JPEG has an explicitly chosen quality ceiling.
                    appearances.append(hashlib.sha256(copied.get_pixmap(dpi=72, colorspace=p.csRGB, alpha=False).samples).digest())
                    copied.set_rotation(rotation)
                    geometries.append((tuple(copied.mediabox), tuple(copied.cropbox), rotation))
                    labels.append(store.page_label(ref))
                    page.set_rotation(sources[ref.source_id].page_rotation(ref.source_page))
                report(index + 1, len(pages), f'已生成第 {index + 1} 页')
            _restore_bookmarks(out, documents, pages, outline, warnings)
            if any(label is not None for label in labels):
                out.set_page_labels([dict(startpage=i, prefix=label or str(i + 1)) for i, label in enumerate(labels)])
            report(0, 0, '正在保存并核验影像副本')
            fd, temporary = tempfile.mkstemp(prefix='.zhixu-raster-', suffix='.pdf', dir=destination.parent)
            os.close(fd)
            encryption = options.encryption
            save = dict(garbage=4, deflate=True, raise_on_repair=True)
            if encryption:
                save.update(encryption=encryption.algorithm, user_pw=encryption.user_password,
                            owner_pw=encryption.owner_password, permissions=encryption.permissions)
            out.save(temporary, **save)
            with p.open(temporary) as check:
                if check.needs_pass and check.authenticate(encryption.user_password or encryption.owner_password) <= 0:
                    raise PdfError('输出密码校验失败，未提交副本。')
                if check.is_repaired or check.page_count != len(pages) or signature_state(check) != 'none':
                    raise PdfError('副本结构校验失败，未提交文件。')
                for index, expected in enumerate(geometries):
                    check_cancel(cancel)
                    page = check[index]
                    if ((tuple(page.mediabox), tuple(page.cropbox), page.rotation) != expected
                            or page.get_text().strip() or page.get_links() or list(page.widgets() or ())
                            or list(page.annots() or ()) or not page.get_images()):
                        raise PdfError('副本页面内容校验失败，未提交文件。')
                    page.set_rotation(0)
                    actual = hashlib.sha256(page.get_pixmap(dpi=72, colorspace=p.csRGB, alpha=False).samples).digest()
                    if actual != appearances[index]:
                        raise PdfError('副本页面显示校验失败，未提交文件。')
                if check.get_toc() != out.get_toc() or check.get_page_labels() != out.get_page_labels():
                    raise PdfError('副本目录或页标签校验失败，未提交文件。')
            for source in sources.values():
                if stamp(source.path) != source.disk_stamp:
                    raise PdfError('转换期间原文件发生变化，未提交过期副本。')
            commit_file(temporary, destination, None, cancel=cancel)
            temporary = None
        return WriteResult(str(destination), warnings=tuple(warnings))
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


def _restore_bookmarks(out, documents, pages, outline, warnings):
    mapping = {ref.page_id: index + 1 for index, ref in enumerate(pages)}
    toc = []
    try:
        if outline is not None:
            toc = [[entry.level, entry.title, mapping.get(entry.target_id, -1)] for entry in outline]
        elif len(documents) == 1:
            source = next(iter(documents.values()))
            targets = {}
            for index, ref in enumerate(pages):
                targets.setdefault(ref.source_page + 1, index + 1)
            toc = [[level, title, targets.get(page, -1)] for level, title, page in source.get_toc()]
        if toc:
            out.set_toc(toc)
    except (ValueError, RuntimeError):
        out.set_toc([])
        warnings.append('原目录无法可靠转换，影像副本未保留目录，请核对。')
