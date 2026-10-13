"""Different synthetic layouts prove geometry rules, not a customer-file template."""
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import openpyxl
import pymupdf as p
import pytest

from app.documentlayout import page_layout, layout_text, pdf_to_text, selection_text
from app.ocr.contracts import OCRLine
from app.ocr.pdf_export import add_text_layer
from app.ocr.word_export import W, convert_ocr_pdf_to_docx
from app.pdf2excel.converter import convert_pdf_to_excel


def sample(path, cols, rows, *, dpi=144, thickness=.7, rotation=0, crop=False, prose_only=False, merged=False, second=False):
    width, height = 520, 680
    left, top, cell_width, cell_height = 55, 240, 75, 31
    expected = [[f'Field {col+1}' if row == 0 else f'{row*17+col*3}'
                 for col in range(cols)] for row in range(rows)]
    if merged:
        expected[0][1]=''
        expected[2][0]=''
    with p.open() as original:
        page = original.new_page(width=width, height=height)
        page.insert_text((left, 70), 'Factory report', fontsize=26)
        page.insert_text((left, 120), 'Narrative outside the data region.', fontsize=11)
        page.insert_text((left, 150), 'Reference date: 2031-06-23.', fontsize=11)
        if not prose_only:
            for col in range(cols+1):
                x = left+col*cell_width
                start=top+cell_height if merged and col==1 else top
                page.draw_line((x,start),(x,top+rows*cell_height), color=(.4,.4,.4), width=thickness)
            for row in range(rows+1):
                y = top+row*cell_height
                start=left+cell_width if merged and row==2 else left
                page.draw_line((start,y),(left+cols*cell_width,y),color=(.4,.4,.4),width=thickness)
            for row in range(rows):
                for col in range(cols):
                    if expected[row][col]:
                        page.insert_text((left+col*cell_width+8, top+row*cell_height+20), expected[row][col], fontsize=10)
        if second:
            for x in (150,225,300):
                page.draw_line((x,420),(x,482),color=(.4,.4,.4),width=thickness)
            for y in (420,451,482):
                page.draw_line((150,y),(300,y),color=(.4,.4,.4),width=thickness)
            for row,values in enumerate((('Extra A','Extra B'),('61','73'))):
                for col,value in enumerate(values):
                    page.insert_text((158+col*75,440+row*31),value,fontsize=10)
        page.insert_text((left, 570), 'Footer is prose. Total code: 9837.', fontsize=11)
        raw = page.get_text('dict')
        image = page.get_pixmap(dpi=dpi).tobytes('jpg', jpg_quality=85)
    with p.open() as scanned:
        page = scanned.new_page(width=width,height=height)
        page.insert_image(page.rect, stream=image)
        factor=200/72
        lines=[]
        for block in raw['blocks']:
            for line in block.get('lines',()):
                for span in line['spans']:
                    box=p.Rect(span['bbox'])
                    polygon=tuple(value*factor for point in (box.tl,box.tr,box.br,box.bl) for value in point)
                    lines.append(OCRLine(span['text'], tuple(box), polygon, .99, 0))
        add_text_layer(page,lines,page.get_pixmap(dpi=200))
        if crop:
            page.set_cropbox(p.Rect(15,15,width-15,height-15))
        page.set_rotation(rotation)
        scanned.subset_fonts()
        scanned.save(path)
    return expected


@pytest.mark.parametrize('cols,rows,dpi,thickness,rotation,crop', [
    (2,3,96,.7,0,False), (4,5,144,1.2,0,True),
    (5,6,216,.5,90,False), (3,4,144,1.8,180,True), (4,3,144,.7,270,False)])
def test_raster_layout_consistent_across_formats(tmp_path,cols,rows,dpi,thickness,rotation,crop):
    source=tmp_path/'input.pdf'
    expected=sample(source,cols,rows,dpi=dpi,thickness=thickness,rotation=rotation,crop=crop)
    with p.open(source) as doc:
        layout,_=page_layout(doc[0])
        tables=[item['table'] for item in layout if item['kind']=='table']
        assert len(tables)==1 and tables[0].row_count==rows and tables[0].col_count==cols
        actual=[['' for _ in range(cols)] for _ in range(rows)]
        for cell in tables[0].cells:
            actual[cell.row][cell.col]=cell.text
        assert actual==expected
        text=layout_text(layout)
        assert '\t'.join(expected[1]) in text
        assert 'Narrative outside the data region.' in text and '9837' in text
        assert layout[0]['heading']
        raw=doc[0].get_text('rawdict',flags=p.TEXTFLAGS_RAWDICT & ~p.TEXT_PRESERVE_IMAGES)
        box=p.Rect(tables[0].bbox)
        selected=selection_text(raw,box,tables)
        assert selected=='\n'.join('\t'.join(row) for row in expected)
    if rotation==0:
        txt,word,excel=tmp_path/'out.txt',tmp_path/'out.docx',tmp_path/'out.xlsx'
        pdf_to_text(source,txt)
        assert '\t'.join(expected[1]) in txt.read_text(encoding='utf-8-sig')
        convert_ocr_pdf_to_docx(source,word,False,None,lambda:False)
        with ZipFile(word) as archive:
            document=ET.fromstring(archive.read('word/document.xml'))
        table=list(document.iter(f'{{{W}}}tbl'))[0]
        actual=[[''.join(t.text or '' for t in cell.iter(f'{{{W}}}t'))
                 for cell in row.findall(f'{{{W}}}tc')] for row in table.findall(f'{{{W}}}tr')]
        assert actual==expected
        result,_=convert_pdf_to_excel(source,excel)
        workbook=openpyxl.load_workbook(excel,data_only=True)
        try:
            actual=[[str(value) for value in row] for row in workbook.active.values]
            assert actual==expected
        finally:
            workbook.close()


def test_prose_cannot_become_a_whole_page_table(tmp_path):
    source=tmp_path/'narrative.pdf'
    sample(source,3,4,prose_only=True)
    with p.open(source) as doc:
        layout,warnings=page_layout(doc[0])
        assert all(item['kind']=='text' for item in layout)
        assert 'Narrative outside the data region.' in layout_text(layout)


def test_single_cell_partial_copy(tmp_path):
    source=tmp_path/'grid.pdf'
    expected=sample(source,3,4)
    with p.open(source) as doc:
        layout,_=page_layout(doc[0]); table=next(item['table'] for item in layout if item['kind']=='table')
        raw=doc[0].get_text('rawdict',flags=p.TEXTFLAGS_RAWDICT & ~p.TEXT_PRESERVE_IMAGES)
        cell=next(cell for cell in table.cells if cell.row==1 and cell.col==1)
        assert selection_text(raw,cell.bbox,[table])==expected[1][1]


def test_raster_merged_cells_are_geometry_based(tmp_path):
    source=tmp_path/'merged.pdf'
    sample(source,4,4,merged=True)
    with p.open(source) as doc:
        layout,_=page_layout(doc[0]); table=next(item['table'] for item in layout if item['kind']=='table')
        assert table.row_count==4 and table.col_count==4
        assert any(cell.row==0 and cell.col==0 and cell.col_span==2 for cell in table.cells)
        assert any(cell.row==1 and cell.col==0 and cell.row_span==2 for cell in table.cells)


def test_two_tables_stay_separate_from_prose(tmp_path):
    source=tmp_path/'multiple.pdf'
    sample(source,3,3,second=True)
    with p.open(source) as doc:
        layout,_=page_layout(doc[0])
        tables=[item['table'] for item in layout if item['kind']=='table']
        assert [(table.row_count,table.col_count) for table in tables]==[(3,3),(2,2)]
        assert layout[0]['kind']=='text' and layout[-1]['kind']=='text'
