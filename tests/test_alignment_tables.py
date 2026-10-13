"""Weak-rule grids use unrelated fields and include negative narrative controls."""
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import openpyxl
import pymupdf as p
import pytest

from app.documentlayout import page_layout,pdf_to_text
from app.ocr.word_export import W,convert_ocr_pdf_to_docx
from app.pdf2excel.converter import convert_pdf_to_excel


def make_table(path,columns,*,shading=False,numeric=True,wrap=False,rotation=0):
    expected=[]
    with p.open() as original:
        page=original.new_page(width=620,height=700)
        page.insert_text((45,65),'Workshop reference',fontsize=24)
        page.insert_text((45,100),'Narrative remains outside aligned fields.',fontsize=12)
        for row in range(4):
            values=[]
            top=180+row*50
            if shading and row==0:
                page.draw_rect(p.Rect(40,top,40+columns*105,top+50),fill=(.88,.94,.97),color=None)
            for column in range(columns):
                value=f'Label {column}' if row==0 else (f'{row*37+column}' if numeric and column else f'Item {row}{column}')
                if not numeric and row:
                    value=f'Group {chr(65+row)}{chr(65+column)}'
                if wrap and row==2 and column==1:
                    value+='\ncontinuation'
                for offset,line in enumerate(value.splitlines()):
                    page.insert_text((48+column*105,top+24+offset*14),line,fontsize=10)
                values.append(value)
            expected.append(values)
        page.insert_text((45,480),'Independent closing statement.',fontsize=12)
        raw=page.get_text('dict'); image=page.get_pixmap(dpi=120).tobytes('jpg',jpg_quality=85)
    with p.open() as scanned:
        page=scanned.new_page(width=620,height=700)
        page.insert_image(page.rect,stream=image)
        for block in raw['blocks']:
            for line in block.get('lines',()):
                for span in line['spans']:
                    page.insert_text(span['origin'],span['text'],fontsize=span['size'],render_mode=3)
        page.set_rotation(rotation)
        scanned.save(path)
    return expected


@pytest.mark.parametrize('columns,shading,numeric,wrap,rotation',[(3,False,True,False,0),(4,True,False,False,0),(5,True,True,True,0),(3,True,True,False,90)])
def test_weak_rule_tables_keep_cells_and_wrapped_content(tmp_path,columns,shading,numeric,wrap,rotation):
    source=tmp_path/'aligned.pdf'; expected=make_table(source,columns,shading=shading,numeric=numeric,wrap=wrap,rotation=rotation)
    with p.open(source) as doc:
        layout,_=page_layout(doc[0]); tables=[item['table'] for item in layout if item['kind']=='table']
        assert len(tables)==1
        table=tables[0]; actual=[['' for _ in range(columns)] for _ in range(4)]
        for cell in table.cells:actual[cell.row][cell.col]=cell.text
        assert actual==expected
    word=tmp_path/'result.docx'; txt=tmp_path/'result.txt'
    convert_ocr_pdf_to_docx(source,word,False,None,lambda:False); pdf_to_text(source,txt)
    with ZipFile(word) as archive:
        root=ET.fromstring(archive.read('word/document.xml'))
        table=list(root.iter(f'{{{W}}}tbl'))[0]
        actual=[[''.join(node.text or '' for node in cell.iter(f'{{{W}}}t')) for cell in row.findall(f'{{{W}}}tc')] for row in table.findall(f'{{{W}}}tr')]
        assert actual==[[value.replace('\n','') for value in row] for row in expected]
        assert 'Workshop reference' in ''.join(node.text or '' for node in root.iter(f'{{{W}}}t'))
    assert '\t'.join(expected[1]) in txt.read_text(encoding='utf-8-sig')
    if not rotation:
        excel=tmp_path/'result.xlsx'; convert_pdf_to_excel(str(source),str(excel))
        workbook=openpyxl.load_workbook(excel,data_only=True)
        try:assert [[str(value) for value in row] for row in workbook.active.values]==expected
        finally:workbook.close()


def test_short_nonnumeric_parallel_prose_is_not_a_table(tmp_path):
    source=tmp_path/'prose.pdf'
    make_table(source,3,numeric=False)
    with p.open(source) as doc:
        layout,_=page_layout(doc[0])
        assert not any(item['kind']=='table' for item in layout)
