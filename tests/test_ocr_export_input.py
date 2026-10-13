"""Conversion preparation tests are independent of local models and office data."""
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import pymupdf as p
import pytest

from app.ocr.contracts import OCRJobResult, OCRError
from app.ocr.export_input import needs_text_export
from app.ocr.word_export import W,convert_ocr_pdf_to_docx
from app.output import OperationCancelled


def make_mixed(path):
    with p.open() as doc:
        doc.new_page().insert_text((50,100),'Existing electronic narrative.')
        page=doc.new_page()
        image=p.Pixmap(p.csRGB,p.IRect(0,0,20,20)); image.clear_with(255)
        page.insert_image(page.rect,stream=image.tobytes('png'))
        doc.save(path)


def test_scanned_page_is_prepared_once_and_temp_is_removed(tmp_path,monkeypatch):
    source=tmp_path/'mixed.pdf'; target=tmp_path/'mixed.docx'
    make_mixed(source); original=source.read_bytes(); calls=[]
    def recognize(src,dst,pages,options,progress,cancel):
        calls.append((tuple(pages),options.runtime_dir))
        with p.open(src) as doc:
            doc[1].insert_text((50,100),'Recognized second narrative.',render_mode=3)
            doc.save(dst)
        progress(1,1,'prepared')
        return OCRJobResult(str(dst),(1,),(),())
    monkeypatch.setattr('app.ocr.service.run_ocr',recognize)
    assert needs_text_export(source)
    result=convert_ocr_pdf_to_docx(source,target,False,None,lambda:False,runtime_dir='local-component')
    assert result[0]=='success' and calls==[((1,),'local-component')]
    assert source.read_bytes()==original and not list(tmp_path.glob('.zhixu-recognize-*'))
    with ZipFile(target) as archive:
        root=ET.fromstring(archive.read('word/document.xml'))
        text=''.join(node.text or '' for node in root.iter(f'{{{W}}}t'))
        assert 'Existing electronic narrative.' in text and 'Recognized second narrative.' in text


@pytest.mark.parametrize('error',[OCRError('组件缺失'),OperationCancelled('已取消')])
def test_preparation_error_does_not_publish_or_leave_temp(tmp_path,monkeypatch,error):
    source=tmp_path/'mixed.pdf'; target=tmp_path/'mixed.docx'
    make_mixed(source); original=source.read_bytes()
    def recognize(src,dst,*args):
        Path(dst).write_bytes(b'partial')
        raise error
    monkeypatch.setattr('app.ocr.service.run_ocr',recognize)
    with pytest.raises(type(error)):
        convert_ocr_pdf_to_docx(source,target,False,None,lambda:False)
    assert not target.exists() and source.read_bytes()==original
    assert not list(tmp_path.glob('.zhixu-recognize-*'))


def test_partial_recognition_failure_prevents_incomplete_docx(tmp_path,monkeypatch):
    source=tmp_path/'mixed.pdf'; target=tmp_path/'mixed.docx'
    make_mixed(source)
    monkeypatch.setattr('app.ocr.service.run_ocr',lambda *args:OCRJobResult('unused',(),(),((1,'不清晰'),)))
    with pytest.raises(OCRError,match='部分扫描页'):
        convert_ocr_pdf_to_docx(source,target,False,None,lambda:False)
    assert not target.exists() and not list(tmp_path.glob('.zhixu-recognize-*'))


def test_existing_destination_skips_before_model_loading(tmp_path,monkeypatch):
    source=tmp_path/'mixed.pdf'; target=tmp_path/'mixed.docx'
    make_mixed(source); target.write_bytes(b'keep')
    def never_run(*args):
        raise AssertionError('A skipped document must not load models')
    monkeypatch.setattr('app.ocr.service.run_ocr',never_run)
    assert convert_ocr_pdf_to_docx(source,target,False,None,lambda:False)[0]=='skipped'
    assert target.read_bytes()==b'keep'


def test_cancel_during_layout_analysis_preserves_source_and_no_output(tmp_path):
    source=tmp_path/'recognized.pdf'; target=tmp_path/'recognized.docx'
    with p.open() as doc:
        for index in range(2):
            doc.new_page().insert_text((50,100),'Recognized editable narrative.',render_mode=3)
        doc.save(source)
    original=source.read_bytes(); cancelled=[False]
    def report(message):
        if '分析排版' in message:
            cancelled[0]=True
    with pytest.raises(OperationCancelled):
        convert_ocr_pdf_to_docx(source,target,False,report,lambda:cancelled[0])
    assert source.read_bytes()==original and not target.exists()
    assert not list(tmp_path.glob('.zhixu-*'))
