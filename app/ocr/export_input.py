"""Prepare missing scan text privately inside a conversion task."""
from contextlib import contextmanager
from pathlib import Path

from app.ocr.contracts import OCROptions, OCRError
from app.output import check_cancel
from app.storage import TemporaryWorkspace


def needs_text_export(source, cancel=None):
    import pymupdf as p
    try:
        with p.open(source) as doc:
            if doc.is_encrypted or not doc.is_pdf:
                return False
            for page in doc:
                check_cancel(cancel)
                if not page.get_text().strip() and page.get_images():
                    return True
                if any(span['type']==3 and any(chr(char[0]).isalnum() for char in span['chars'])
                       for span in page.get_texttrace()):
                    return True
        return False
    except (OSError,RuntimeError,ValueError):
        return False


@contextmanager
def prepared_text_pdf(source, directory, progress=None, cancel=None, runtime_dir=None):
    import pymupdf as p
    from app.wordconvert import validate_pdf_for_word
    from app.ocr.service import run_ocr
    validate_pdf_for_word(source)
    missing=[]
    with p.open(source) as doc:
        for index,page in enumerate(doc):
            check_cancel(cancel)
            if not page.get_text().strip() and page.get_images():
                missing.append(index)
    if not missing:
        yield str(source)
        return
    with TemporaryWorkspace('recognize') as folder:
        def report(current,total,message):
            if progress:
                progress(message)
        result=run_ocr(source,Path(folder)/'recognized.pdf',missing,OCROptions(runtime_dir=runtime_dir),report,cancel)
        if result.failed_pages:
            raise OCRError('部分扫描页未识别成功，未提交文档，请检查清晰度后重试。')
        yield result.output_path
