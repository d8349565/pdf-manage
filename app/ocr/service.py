"""Independent document processing with checked, non-overwriting output."""
import os
import hashlib
from contextlib import ExitStack
from pathlib import Path
import tempfile

import pymupdf as p

from app.ocr.contracts import OCROptions, OCRJobResult, OCRError
from app.ocr.engine import OCREngine
from app.ocr.model_manager import validate_runtime
from app.ocr.page_detector import has_effective_text
from app.ocr.pdf_export import add_text_layer
from app.output import check_cancel, commit_file, stamp, OperationCancelled
from app.pdfstructure import document_signature, inspect_safety
from app.storage import StorageError, TemporaryWorkspace

DPI = 200
MAX_PIXELS = 40_000_000


def run_ocr(source_path, destination_path, page_indices, options=None,
            progress_callback=None, cancel_check=None):
    options = options or OCROptions()
    source, destination = Path(source_path).resolve(), Path(destination_path).resolve()
    if source == destination or (source.exists() and destination.exists() and os.path.samefile(source, destination)):
        raise OCRError('OCR 必须保存为新文件，不能覆盖原 PDF。')
    if destination.exists():
        raise OCRError('输出文件已存在，请选择新的文件名。')
    if destination.suffix.lower() != '.pdf' or not destination.parent.is_dir():
        raise OCRError('请选择已有文件夹中的 PDF 输出路径。')
    indices = tuple(dict.fromkeys(page_indices))
    if not indices:
        raise OCRError('请至少选择一页进行识别。')
    source_stamp = stamp(source)
    engine = None
    temp = None
    processed, skipped, failures = [], [], []
    checks = {}
    appearances = {}
    def report(current, total, message):
        check_cancel(cancel_check)
        if progress_callback:
            progress_callback(current, total, message)
    def close_engine():
        nonlocal engine
        if engine:
            engine.close()
            engine = None
    try:
        report(0, 0, '正在检查 PDF 和 OCR 环境')
        with p.open(source) as doc:
            authentication = doc.authenticate(options.password or '') if doc.needs_pass else 0
            if doc.is_encrypted:
                raise OCRError('PDF 需要密码，请通过原有打开文件流程验证后再识别。')
            safety = inspect_safety(doc, authentication)
            if safety.signature != 'none':
                raise OCRError('检测到数字签名或无法确认签名结构，MVP 不支持生成 OCR 副本。')
            if not safety.allows(p.PDF_PERM_MODIFY) or not safety.allows(p.PDF_PERM_COPY):
                raise OCRError('当前密码不允许修改或复制 PDF 内容，不能执行 OCR。')
            if not doc.is_pdf or doc.is_repaired:
                raise OCRError('PDF 无法可靠解析，请先修复或另存文档。')
            if any(type(i) is not int or not 0 <= i < doc.page_count for i in indices):
                raise OCRError('识别页码超出文档范围。')
            structure = document_signature(doc)
            root, _ = validate_runtime(options.runtime_dir, cancel=cancel_check)
            with TemporaryWorkspace('ocr') as scratch, ExitStack() as resources:
                image = Path(scratch) / 'page.png'
                for current, index in enumerate(indices):
                    report(current, len(indices), f'正在识别第 {index + 1} 页')
                    try:
                        page = doc[index]
                        skip_page = options.skip_existing_text and has_effective_text(page)
                    except Exception:
                        failures.append((index, '该页无法读取或解析，已跳过识别。'))
                        report(current + 1, len(indices), f'第 {index + 1} 页无法解析')
                        continue
                    if skip_page:
                        skipped.append(index)
                    else:
                        if engine is None:
                            report(current, len(indices), '正在加载本地 CPU 模型')
                            engine = OCREngine(root, cancel_check)
                            resources.callback(close_engine)
                        try:
                            if page.rect.width * page.rect.height * (DPI / 72) ** 2 > MAX_PIXELS:
                                raise OCRError('页面尺寸过大，超过轻量 OCR 的 4000 万像素限制。')
                            appearances[index] = hashlib.sha256(page.get_pixmap(dpi=72).samples).digest()
                            # Normalize /Rotate for recognition; restore it before writing/saving.
                            rotation = page.rotation
                            page.set_rotation(0)
                            try:
                                report(current, len(indices), f'正在渲染第 {index + 1} 页')
                                pix = page.get_pixmap(dpi=DPI, colorspace=p.csRGB, alpha=False, annots=False)
                                pix.save(image)
                                report(current, len(indices), f'正在识别第 {index + 1} 页文字')
                                lines = engine.recognize(image, index)
                                check_cancel(cancel_check)
                                report(current, len(indices), f'正在写入第 {index + 1} 页文字层')
                                written = add_text_layer(page, lines, pix, DPI,
                                                         protect_existing=options.skip_existing_text)
                            finally:
                                page.set_rotation(rotation)
                            if not written:
                                raise OCRError('未找到可新增的文字，请检查清晰度或已有文字层。')
                            checks[index] = written
                            processed.append(index)
                        except OperationCancelled:
                            raise
                        except Exception as exc:
                            failures.append((index, str(exc) if isinstance(exc, OCRError) else '页面内容或文字层异常，该页未识别。'))
                    report(current + 1, len(indices), f'已处理第 {index + 1} 页')
                if failures and not processed:
                    raise OCRError('未生成识别结果：' + '；'.join(f'第 {i + 1} 页：{why}' for i, why in failures))
                report(0, 0, '正在保存并校验搜索文字层')
                fd, temp = tempfile.mkstemp(prefix='.zhixu-ocr-', suffix='.pdf', dir=destination.parent)
                os.close(fd)
                doc.subset_fonts()
                doc.save(temp, garbage=4, deflate=True, encryption=p.PDF_ENCRYPT_KEEP, raise_on_repair=True)
                with p.open(temp) as check:
                    if check.needs_pass and not check.authenticate(options.password or ''):
                        raise OCRError('输出 PDF 密码验证失败，未提交结果。')
                    if check.is_repaired or document_signature(check) != structure:
                        raise OCRError('输出 PDF 页面或导航结构校验失败，未提交结果。')
                    for index, written in checks.items():
                        check_cancel(cancel_check)
                        page = check[index]
                        for text, box in written:
                            hits = page.search_for(text)
                            if not any(hit.intersects(box) and (hit & box).get_area() >= box.get_area() * .4 for hit in hits):
                                raise OCRError(f'第 {index + 1} 页文字搜索或位置校验失败，未提交结果。')
                        if hashlib.sha256(page.get_pixmap(dpi=72).samples).digest() != appearances[index]:
                            raise OCRError('输出页面显示校验失败，未提交结果。')
                    # Font subsetting can touch shared resources: also verify skipped/unselected pages.
                    with p.open(source) as original:
                        if original.needs_pass and not original.authenticate(options.password or ''):
                            raise OCRError('原文档在校验期间无法授权，未提交结果。')
                        for index in range(doc.page_count):
                            check_cancel(cancel_check)
                            if index not in checks and original[index].get_pixmap(dpi=72).samples != check[index].get_pixmap(dpi=72).samples:
                                raise OCRError('未识别页面的可见内容校验失败，未提交结果。')
            if stamp(source) != source_stamp:
                raise OCRError('识别期间原文件发生变化，未提交过期结果。')
            commit_file(temp, destination, None, cancel=cancel_check)
            temp = None
        warnings = ('扫描页判定使用启发式；复杂混合版面请核对搜索结果及金额、编号。',)
        return OCRJobResult(str(destination), tuple(processed), tuple(skipped), tuple(failures), warnings)
    except (OperationCancelled, OCRError):
        raise
    except StorageError as exc:
        raise OCRError(str(exc)) from None
    except Exception:
        raise OCRError('OCR 未完成：文件无法读写、磁盘空间不足或 PDF 内容异常。原文件未改动。') from None
    finally:
        close_engine()
        if temp:
            Path(temp).unlink(missing_ok=True)
