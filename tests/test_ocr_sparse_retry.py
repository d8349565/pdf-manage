"""Sparse-line retries must use stronger image evidence and preserve coordinates."""
from dataclasses import replace

from PIL import Image

from app.ocr.contracts import OCRLine
from app.ocr.service import retry_sparse_lines


def test_retry_requires_better_confidence_and_matching_region(tmp_path):
    image=tmp_path/'page.png';Image.new('RGB',(300,100),'white').save(image)
    line=OCRLine('ab        ',(20,20,180,40),(20,20,180,20,180,40,20,40),.6,0)
    good=OCRLine('Recovered content',(10,10,170,30),(10,10,170,10,170,30,10,30),.98,0)
    class Engine:
        def recognize(self,path,index,*,word_boxes):
            assert not word_boxes
            return [replace(good,confidence=.62),replace(good,bbox=(0,0,4,4)),good]
    result=retry_sparse_lines(Engine(),image,[line],0)
    assert result[0].text==good.text
    assert result[0].bbox==line.bbox and result[0].polygon==line.polygon


def test_ordinary_identifiers_do_not_trigger_rewriting(tmp_path):
    image=tmp_path/'page.png';Image.new('RGB',(300,100),'white').save(image)
    line=OCRLine('Code A1 0042',(20,20,180,40),(20,20,180,20,180,40,20,40),.65,0)
    class Engine:
        def recognize(self,*args,**kwargs):
            raise AssertionError('No retry for ordinary word separators or identifiers')
    assert retry_sparse_lines(Engine(),image,[line],0)==[line]


def test_low_confidence_sparse_line_uses_geometry_even_without_many_spaces(tmp_path):
    image=tmp_path/'page.png';Image.new('RGB',(1000,100),'white').save(image)
    line=OCRLine('Ab c d ef',(20,20,900,40),(20,20,900,20,900,40,20,40),.65,0)
    candidate=OCRLine('Longer recognized words from the actual crop',(10,10,890,30),
                      (10,10,890,10,890,30,10,30),.97,0)
    class Engine:
        def recognize(self,*args,**kwargs):return [candidate]
    assert retry_sparse_lines(Engine(),image,[line],0)[0].text==candidate.text
