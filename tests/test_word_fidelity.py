"""Generated-only fidelity regressions; run after authorizing sample validation."""
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pymupdf as p
import pytest

from app import wordconvert
from app.documentimages import electronic_background
from app.documentlayout import layout_from_raw
from app.ocr.word_export import W, WP, paragraph, write_table
from app.pdf2excel.models import CellModel, CellStyle, TableModel


def test_mixed_native_spans_survive_layout_and_docx_runs():
    with p.open() as doc:
        page=doc.new_page()
        page.insert_text((40,80),'Normal ',fontname='helv',fontsize=12)
        x=40+p.get_text_length('Normal ',fontname='helv',fontsize=12)
        page.insert_text((x,80),'Accent',fontname='hebi',fontsize=12,color=(1,0,0))
        items=layout_from_raw(page.get_text('rawdict'),[])
    item=items[0]
    assert item['text']=='Normal Accent'
    body=ET.Element(f'{{{W}}}body')
    paragraph(body,item['text'],runs=item['runs'])
    runs=list(body.iter(f'{{{W}}}r'))
    assert ''.join(node.text or '' for node in body.iter(f'{{{W}}}t'))==item['text']
    assert runs[0].find(f'{{{W}}}rPr/{{{W}}}b') is None
    assert runs[-1].find(f'{{{W}}}rPr/{{{W}}}b') is not None
    assert runs[-1].find(f'{{{W}}}rPr/{{{W}}}i') is not None
    assert runs[-1].find(f'{{{W}}}rPr/{{{W}}}color').get(f'{{{W}}}val')=='FF0000'
    assert runs[-1].find(f'{{{W}}}rPr/{{{W}}}rFonts').get(f'{{{W}}}ascii')=='Arial'


def test_background_retains_fills_under_text_and_thin_rules_without_text():
    with p.open() as doc:
        page=doc.new_page(width=300,height=200)
        page.draw_rect((20,20,280,90),fill=(0,0,1),color=None)
        page.insert_text((40,65),'White title',fontsize=22,color=(1,1,1))
        page.draw_line((20,120),(280,120),color=(1,0,0),width=1)
        before=page.get_text()
        background=electronic_background(page,[])[0]
        assert page.get_text()==before
    pix=p.Pixmap(background['data'])
    assert background['image_role']=='background'
    # Every pixel in the text area remains blue: no glyph duplication or white holes.
    scale=pix.width/300
    for y in range(round(40*scale),round(75*scale)):
        for x in range(round(35*scale),round(175*scale)):
            assert pix.pixel(x,y)[:3]==(0,0,255)
    assert pix.pixel(round(100*scale),round(120*scale))[0]>0
    assert not pix.alpha
    assert pix.pixel(10,10)==(255,255,255)


def test_native_table_keeps_merge_fill_and_only_source_borders():
    table=TableModel(page_no=1,bbox=(20,20,220,100),row_count=2,col_count=2,
        cells=[CellModel(0,0,text='Merged',col_span=2,
                        style=CellStyle(fill_color='#FFEEDD',border_bottom='medium')),
               CellModel(1,0,text='Plain'),CellModel(1,1,text='Italic',
                        style=CellStyle(italic=True,font_name='Times-Roman'))],
        col_widths=[100,100],row_heights=[40,40])
    body=ET.Element(f'{{{W}}}body')
    write_table(body,table,positioned=True)
    assert next(body.iter(f'{{{W}}}gridSpan')).get(f'{{{W}}}val')=='2'
    assert next(body.iter(f'{{{W}}}shd')).get(f'{{{W}}}fill')=='FFEEDD'
    borders=list(body.iter(f'{{{W}}}tcBorders'))
    assert borders[0].find(f'{{{W}}}bottom').get(f'{{{W}}}sz')=='8'
    assert all(edge.get(f'{{{W}}}val')=='nil' for edge in borders[1])
    assert not list(body.iter(f'{{{W}}}b'))
    assert len(list(body.iter(f'{{{W}}}i')))==1


def test_positioned_native_cell_uses_source_inset_without_moving_the_grid():
    table=TableModel(page_no=1,bbox=(50,100,250,140),row_count=1,col_count=1,
        cells=[CellModel(0,0,text='Inset',bbox=(50,100,250,140),style=CellStyle(font_size=12))],
        col_widths=[200],row_heights=[40])
    body=ET.Element(f'{{{W}}}body')
    write_table(body,table,positioned=True,
                cell_runs={(0,0):[dict(text='Inset',size=12,bbox=(65,110,95,127))]})
    assert next(body.iter(f'{{{W}}}tblpPr')).get(f'{{{W}}}tblpX')=='1000'
    margins=next(body.iter(f'{{{W}}}tblCellMar'))
    assert margins.find(f'{{{W}}}left').get(f'{{{W}}}w')=='0'
    assert next(body.iter(f'{{{W}}}ind')).get(f'{{{W}}}left')=='300'
    assert next(body.iter(f'{{{W}}}vAlign')).get(f'{{{W}}}val')=='top'
    assert int(next(body.iter(f'{{{W}}}spacing')).get(f'{{{W}}}before'))>=150


def test_layout_default_exports_two_pages_without_word_and_preserves_source(tmp_path,monkeypatch):
    source=tmp_path/'generated.pdf'; output=tmp_path/'generated.docx'
    with p.open() as doc:
        for width,height in ((420,595),(595,420)):
            page=doc.new_page(width=width,height=height)
            page.draw_rect((20,20,width-20,85),fill=(.1,.3,.6),color=None)
            page.insert_text((40,60),'Report heading',fontsize=24,color=(1,1,1))
            for row in range(4):
                page.insert_text((40,130+row*20),f'Body line {row}.',fontsize=11)
        doc.save(source)
    original=source.read_bytes()
    def no_word():
        raise AssertionError('Layout mode must not probe or launch Office')
    monkeypatch.setattr(wordconvert,'word_available',no_word)
    results,cancelled,version=wordconvert.convert_batch([(str(source),str(output))],False,
        lambda *args:None,lambda:False,direction='pdf_to_word')
    assert results[0]['state']=='success',results
    assert not cancelled and not version and source.read_bytes()==original
    wordconvert.validate_docx(output)
    with ZipFile(output) as archive:
        root=ET.fromstring(archive.read('word/document.xml'))
        settings=ET.fromstring(archive.read('word/settings.xml'))
        assert settings.find(f'{{{W}}}doNotAutoCompressPictures').get(f'{{{W}}}val')=='1'
        styles=ET.fromstring(archive.read('word/styles.xml'))
        text=''.join(node.text or '' for node in root.iter(f'{{{W}}}t'))
        assert text.count('Report heading')==2 and text.count('Body line')==8
        assert len(list(root.iter(f'{{{W}}}pgSz')))==2
        assert {node.get(f'{{{W}}}w') for node in root.iter(f'{{{W}}}pgSz')}=={'8400','11900'}
        assert list(root.iter(f'{{{W}}}framePr'))
        assert len(list(root.iter(f'{{{W}}}pStyle')))==2
        assert any(node.get(f'{{{W}}}styleId')=='Heading1' for node in styles)
        anchors=list(root.iter(f'{{{WP}}}anchor'))
        assert len(anchors)==2 and all(node.get('behindDoc')=='1' for node in anchors)


def test_invalid_layout_mode_rejected_before_opening_files():
    with pytest.raises(ValueError,match='排版方式'):
        wordconvert.convert_batch([],False,lambda *args:None,lambda:False,
                                  direction='pdf_to_word',pdf_mode='invalid')


def test_layout_export_keeps_visible_annotations_without_changing_source(tmp_path):
    source=tmp_path/'annotated.pdf'; output=tmp_path/'annotated.docx'
    with p.open() as doc:
        page=doc.new_page(width=360,height=260)
        page.draw_rect((20,20,340,85),fill=(.94,.97,.98),color=None)
        page.insert_text((35,60),'Original body',fontsize=14)
        page.add_freetext_annot((30,110,200,145),'Added instruction',fontsize=12)
        page.add_stamp_annot((210,120,335,195))
        doc.save(source)
    original=source.read_bytes()
    state,message,_=wordconvert._convert_one(str(source),str(output),False,
        lambda *args:None,lambda:False,direction='pdf_to_word')
    assert state=='success',message
    assert source.read_bytes()==original
    with ZipFile(output) as archive:
        body=ET.fromstring(archive.read('word/document.xml'))
        visible=''.join(node.text or '' for node in body.iter(f'{{{W}}}t'))
        assert visible.count('Added instruction')==1
        backgrounds=[p.Pixmap(archive.read(name)) for name in archive.namelist()
                     if name.startswith('word/media/')]
    assert any(sum(red>100 and red-green>40 and red-blue>40
                   for red,green,blue in zip(pix.samples[::3],pix.samples[1::3],pix.samples[2::3]))>100
               for pix in backgrounds)
