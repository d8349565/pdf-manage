"""Regression checks for detached symbols and dense scanned table exports."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pymupdf as p
import pytest

from app.documentlayout import layout_from_raw
from app.ocr.contracts import OCRLine
from app.ocr.pdf_export import add_text_layer
from app.ocr.word_export import W, convert_ocr_pdf_to_docx
from app.pdf2excel.detector import _format_cell_text_and_bbox


def read_document(path):
    with ZipFile(path) as archive:
        return ET.fromstring(archive.read('word/document.xml'))


@pytest.mark.parametrize('offset', [2, 5, -5])
def test_detached_symbol_keeps_left_to_right_order_without_merging_next_line(offset):
    spans=[dict(text='872.40',bbox=(80,20,120,34)),
           dict(text='￥',bbox=(10,20+offset,24,34+offset)),
           dict(text='91.70',bbox=(80,42,120,56)),
           dict(text='￥',bbox=(10,42+offset,24,56+offset))]
    assert _format_cell_text_and_bbox(spans)[0]=='￥ 872.40\n￥ 91.70'
    raw=dict(blocks=[dict(lines=[dict(spans=[dict(size=10,chars=[dict(c=span['text'],bbox=span['bbox'])])]) for span in spans])])
    layout=layout_from_raw(raw,[])
    assert layout[0]['text'].startswith('￥') and layout[1]['text'].startswith('￥')


@pytest.mark.parametrize('reread,score,corrected', [('￥872.40',.96,True),('￥872.41',.99,False),
                                                  ('夫872.40',.99,False),('￥872.40',.7,False)])
def test_currency_retry_requires_image_confirmation_and_unchanged_digits(monkeypatch,reread,score,corrected):
    deps=Path(__file__).resolve().parents[1]/'build/ocr-deps'
    if deps.is_dir():
        monkeypatch.syspath_prepend(str(deps))
    np=pytest.importorskip('numpy')
    pytest.importorskip('rapidocr')
    from app.ocr.worker import refine_currency_symbols
    lines=[dict(text=text,bbox=box,polygon=[box[0],box[1],box[2],box[1],box[2],box[3],box[0],box[3]],confidence=.99)
           for text,box in [('夫',(4,4,28,28)),('872.40',(65,4,145,28))]]
    engine=SimpleNamespace(text_rec=lambda request: SimpleNamespace(txts=[reread,''],scores=[score,0]))
    refine_currency_symbols(engine,np.full((36,160,3),255,dtype=np.uint8),lines)
    assert lines[0]['text']==('￥' if corrected else '夫')
    assert lines[1]['text']=='872.40'


def dense_scan(path, columns=24, rows=55, width=2400, height=1700):
    left,top=20,60
    cell_width=(width-60)/columns
    cell_height=(height-110)/rows
    with p.open() as original:
        page=original.new_page(width=width,height=height)
        for col in range(columns+1):
            x=left+col*cell_width
            page.draw_line((x,top),(x,top+rows*cell_height),width=.7)
        for row in range(rows+1):
            y=top+row*cell_height
            page.draw_line((left,y),(left+columns*cell_width,y),width=.7)
        values=[]
        for row in range(rows):
            for col in range(columns):
                text=f'Field {col}' if row==0 else str(row*137+col)
                page.insert_text((left+col*cell_width+3,top+row*cell_height+cell_height*.65),text,fontsize=8)
                values.append(text)
        raw=page.get_text('dict')
        image=page.get_pixmap(dpi=96).tobytes('png')
    with p.open() as doc:
        page=doc.new_page(width=width,height=height)
        page.insert_image(page.rect,stream=image)
        lines=[]
        for block in raw['blocks']:
            for line in block.get('lines',()):
                for span in line['spans']:
                    box=p.Rect(span['bbox'])
                    polygon=tuple(value*200/72 for point in (box.tl,box.tr,box.br,box.bl) for value in point)
                    lines.append(OCRLine(span['text'],tuple(box),polygon,.99,0))
        add_text_layer(page,lines,page.get_pixmap(dpi=200))
        doc.subset_fonts()
        doc.save(path)
    return values


def test_large_dense_scan_stays_a_native_table_and_scales_paper_and_text(tmp_path):
    source=tmp_path/'dense.pdf'; output=tmp_path/'dense.docx'
    expected=dense_scan(source)
    state,message,_=convert_ocr_pdf_to_docx(source,output,False,None,lambda:False)
    root=read_document(output)
    tables=list(root.iter(f'{{{W}}}tbl'))
    assert state=='success' and len(tables)==1
    assert len(tables[0].findall(f'{{{W}}}tr'))==55
    actual=[''.join(node.text or '' for node in cell.iter(f'{{{W}}}t')) for row in tables[0].findall(f'{{{W}}}tr') for cell in row.findall(f'{{{W}}}tc')]
    assert actual==expected
    assert max(int(node.get(f'{{{W}}}w')) for node in root.iter(f'{{{W}}}pgSz'))<=31680
    assert max(int(node.get(f'{{{W}}}val')) for node in tables[0].iter(f'{{{W}}}sz'))<=12
    assert list(tables[0].iter(f'{{{W}}}tblCellMar'))
    for cell in tables[0].iter(f'{{{W}}}tc'):
        para=cell.find(f'{{{W}}}p')
        assert para.find(f'{{{W}}}pPr/{{{W}}}rPr/{{{W}}}sz').get(f'{{{W}}}val')==para.find(f'{{{W}}}r/{{{W}}}rPr/{{{W}}}sz').get(f'{{{W}}}val')
    assert '等比例缩放' in message and '按原位置' not in message


def test_one_bad_table_does_not_discard_a_valid_table(tmp_path,monkeypatch):
    from app.pdf2excel import detector
    source=tmp_path/'two.pdf'; output=tmp_path/'two.docx'
    with p.open() as doc:
        page=doc.new_page(width=500,height=650)
        for top,values in ((80,('Valid A','Valid B','12','34')),(300,('Keep A','Keep B','56','78'))):
            for x in (40,220,400):
                page.draw_line((x,top),(x,top+100))
            for y in (top,top+50,top+100):
                page.draw_line((40,y),(400,y))
            for index,text in enumerate(values):
                page.insert_text((50+(index%2)*180,top+30+(index//2)*50),text,fontsize=10,render_mode=3)
        doc.save(source)
    detect=detector.detect_tables
    def damaged(*args):
        tables=detect(*args)
        assert len(tables)==2
        tables[1]=replace(tables[1],cells=[replace(tables[1].cells[0],text='Incomplete')])
        return tables
    monkeypatch.setattr(detector,'detect_tables',damaged)
    convert_ocr_pdf_to_docx(source,output,False,None,lambda:False)
    root=read_document(output)
    assert len(list(root.iter(f'{{{W}}}tbl')))==1
    assert len(list(root.iter(f'{{{W}}}framePr')))==4
    text=''.join(node.text or '' for node in root.iter(f'{{{W}}}t'))
    assert all(value in text for value in ('Valid A','Valid B','Keep A','Keep B','56','78'))


def test_dense_unconfirmed_grid_uses_source_positions_instead_of_tabbed_prose(tmp_path,monkeypatch):
    source=tmp_path/'no-grid.pdf'; output=tmp_path/'no-grid.docx'
    expected=dense_scan(source,columns=4,rows=12,width=500,height=650)
    monkeypatch.setattr('app.pdf2excel.detector.detect_tables',lambda *args:[])
    convert_ocr_pdf_to_docx(source,output,False,None,lambda:False)
    root=read_document(output)
    assert len(list(root.iter(f'{{{W}}}framePr')))==len(expected)
    assert not list(root.iter(f'{{{W}}}tbl'))
    actual=[node.text for node in root.iter(f'{{{W}}}t')]
    assert sorted(actual)==sorted(expected)
