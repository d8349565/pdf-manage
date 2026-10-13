"""Standard package parts and image retention use invented documents only."""
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import pymupdf as p

from app.documentlayout import document_layout
from app.ocr.word_export import W,R,convert_ocr_pdf_to_docx


def test_repeated_margins_become_native_parts(tmp_path):
    source=tmp_path/'pages.pdf'; output=tmp_path/'pages.docx'
    with p.open() as doc:
        for index in range(3):
            page=doc.new_page(width=500,height=700)
            page.insert_text((50,30),'Internal engineering note',fontsize=11,render_mode=3)
            page.insert_text((50,120),f'A unique body on source page {index+1}.',fontsize=12,render_mode=3)
            page.insert_text((400,665),str(index+1),fontsize=11,render_mode=3)
        doc.save(source)
    with p.open(source) as doc:
        pages=document_layout(doc)
        assert all(any(item.get('role')=='header' for item in page['items']) for page in pages)
        assert all(any(item.get('role')=='footer' for item in page['items']) for page in pages)
    convert_ocr_pdf_to_docx(source,output,False,None,lambda:False)
    with ZipFile(output) as archive:
        root=ET.fromstring(archive.read('word/document.xml'))
        text=''.join(node.text or '' for node in root.iter(f'{{{W}}}t'))
        assert 'Internal engineering note' not in text
        assert len(list(root.iter(f'{{{W}}}headerReference')))==3
        assert len(list(root.iter(f'{{{W}}}footerReference')))==3
        assert all(f'word/header{index}.xml' in archive.namelist() for index in range(1,4))
        assert b'PAGE' in archive.read('word/footer2.xml')


def test_single_page_title_and_bottom_note_stay_in_body(tmp_path):
    source=tmp_path/'single.pdf'
    with p.open() as doc:
        page=doc.new_page(width=500,height=700)
        page.insert_text((50,40),'A unique title',fontsize=20,render_mode=3)
        page.insert_text((50,120),'Narrative on a single page.',render_mode=3)
        page.insert_text((50,655),'Closing approval note.',render_mode=3)
        doc.save(source)
    with p.open(source) as doc:
        assert not any(item.get('role') for item in document_layout(doc)[0]['items'])


def test_missing_header_does_not_inherit_previous_page_content(tmp_path):
    source=tmp_path/'partial-header.pdf'; output=tmp_path/'partial-header.docx'
    with p.open() as doc:
        for index in range(3):
            page=doc.new_page(width=500,height=700)
            if index<2:
                page.insert_text((50,30),'Repeated engineering header',render_mode=3)
            page.insert_text((50,120),f'Body on page {index+1}.',render_mode=3)
        doc.save(source)
    convert_ocr_pdf_to_docx(source,output,False,None,lambda:False)
    with ZipFile(output) as archive:
        root=ET.fromstring(archive.read('word/header3.xml'))
        assert not ''.join(node.text or '' for node in root.iter(f'{{{W}}}t')).strip()


def test_independent_picture_has_valid_drawing_relationship(tmp_path):
    source=tmp_path/'illustrated.pdf'; output=tmp_path/'illustrated.docx'
    with p.open() as doc:
        page=doc.new_page(width=500,height=700)
        page.insert_text((50,100),'A narrative with a small illustration.',render_mode=3)
        image=p.Pixmap(p.csRGB,p.IRect(0,0,60,40)); image.clear_with(80)
        page.insert_image(p.Rect(200,220,320,300),stream=image.tobytes('png'))
        doc.save(source)
    convert_ocr_pdf_to_docx(source,output,False,None,lambda:False)
    with ZipFile(output) as archive:
        assert 'word/media/image-1.png' in archive.namelist()
        root=ET.fromstring(archive.read('word/document.xml'))
        assert list(root.iter(f'{{{W}}}drawing'))
        relationships=ET.fromstring(archive.read('word/_rels/document.xml.rels'))
        assert any(item.get('Target')=='media/image-1.png' for item in relationships)


def test_scan_retains_coloured_mark_and_handwritten_like_strokes(tmp_path):
    source=tmp_path/'marked.pdf'; output=tmp_path/'marked.docx'
    with p.open() as original:
        page=original.new_page(width=500,height=700)
        page.insert_text((50,100),'Recorded narrative text.',fontsize=13)
        page.draw_circle(p.Point(330,410),45,color=(.8,0,0),width=3)
        page.draw_polyline([(80,470),(110,450),(130,480),(160,454),(200,475)],color=(0,0,0),width=2)
        image=page.get_pixmap(dpi=144).tobytes('png')
    with p.open() as scanned:
        page=scanned.new_page(width=500,height=700)
        page.insert_image(page.rect,stream=image)
        page.insert_text((50,100),'Recorded narrative text.',fontsize=13,render_mode=3)
        scanned.save(source)
    convert_ocr_pdf_to_docx(source,output,False,None,lambda:False)
    with ZipFile(output) as archive:
        pictures=[name for name in archive.namelist() if name.startswith('word/media/')]
        assert len(pictures)>=2
        root=ET.fromstring(archive.read('word/document.xml'))
        assert 'Recorded narrative text.' in ''.join(node.text or '' for node in root.iter(f'{{{W}}}t'))


def test_two_columns_use_native_sections_and_column_breaks(tmp_path):
    source=tmp_path/'columns.pdf'; output=tmp_path/'columns.docx'
    with p.open() as doc:
        page=doc.new_page(width=600,height=700)
        page.insert_text((45,60),'Two column technical bulletin',fontsize=22,render_mode=3)
        for row,word in enumerate(('initial','subsequent','third','final')):
            page.insert_text((45,130+row*25),f'Left {word} narrative statement.',fontsize=11,render_mode=3)
            page.insert_text((320,130+row*25),f'Right {word} narrative statement.',fontsize=11,render_mode=3)
        page.insert_text((45,310),'Closing note outside both columns.',fontsize=11,render_mode=3)
        doc.save(source)
    convert_ocr_pdf_to_docx(source,output,False,None,lambda:False)
    with ZipFile(output) as archive:
        root=ET.fromstring(archive.read('word/document.xml'))
        columns=list(root.iter(f'{{{W}}}cols'))
        assert any(node.get(f'{{{W}}}num')=='2' for node in columns)
        assert any(node.get(f'{{{W}}}num')=='1' for node in columns)
        assert any(node.get(f'{{{W}}}type')=='column' for node in root.iter(f'{{{W}}}br'))
        text=''.join(node.text or '' for node in root.iter(f'{{{W}}}t'))
        assert text.index('Left final')<text.index('Right initial')<text.index('Closing note')


def test_table_fallback_still_retains_independent_picture(tmp_path,monkeypatch):
    from app.pdf2excel.models import CellModel,TableModel
    source=tmp_path/'fallback.pdf'; output=tmp_path/'fallback.docx'
    with p.open() as doc:
        page=doc.new_page(width=500,height=700)
        page.insert_text((50,100),'Every word survives an uncertain table.',render_mode=3)
        image=p.Pixmap(p.csRGB,p.IRect(0,0,60,40)); image.clear_with(100)
        page.insert_image(p.Rect(200,220,320,300),stream=image.tobytes('png'))
        doc.save(source)
    table=TableModel(1,(0,0,500,700),2,2,[CellModel(0,0,text='Incomplete')],[250,250],[350,350])
    monkeypatch.setattr('app.pdf2excel.detector.detect_tables',lambda *args:[table])
    result=convert_ocr_pdf_to_docx(source,output,False,None,lambda:False)
    with ZipFile(output) as archive:
        root=ET.fromstring(archive.read('word/document.xml'))
        assert 'Every word survives' in ''.join(node.text or '' for node in root.iter(f'{{{W}}}t'))
        assert list(root.iter(f'{{{W}}}drawing'))
        assert len([name for name in archive.namelist() if name.startswith('word/media/')])==1
    assert '按原位置保留全部识别文字' in result[1]
    assert list(root.iter(f'{{{W}}}framePr'))
