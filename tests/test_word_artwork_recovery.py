"""General artwork/grid invariants; no document names or expected OCR answers."""
from io import BytesIO

from PIL import Image,ImageChops,ImageDraw,ImageStat
import pymupdf as p
import pytest

from app.documentlayout import document_layout
from app.ocr.scan_layout import restore_scan
from app.ocr.word_export import write_layout,paragraph,W,WP,exported_text
from app.pdf2excel.raster_grid import raster_grid_lines


def raster_copy(page):
    doc=p.open();target=doc.new_page(width=page.rect.width,height=page.rect.height)
    target.insert_image(target.rect,stream=page.get_pixmap(dpi=144).tobytes('png'))
    return doc


def test_coloured_artwork_crossing_a_cell_does_not_add_rows_or_become_body_text():
    with p.open() as doc:
        page=doc.new_page(width=360,height=320)
        for y in (70,120,180):page.draw_line((20,y),(330,y),color=(.6,.6,.6))
        for x in (20,120,330):page.draw_line((x,70),(x,180),color=(.6,.6,.6))
        page.insert_text((30,95),'Label',fontsize=11)
        page.insert_text((135,95),'Value',fontsize=11)
        page.insert_text((30,148),'Review',fontsize=11)
        page.insert_text((135,148),'Accepted',fontsize=11)
        page.draw_circle((250,172),27,color=(.8,.1,.1),width=1)
        page.insert_text((232,173),'TEST',fontsize=11,color=(.8,.1,.1))
        items=document_layout(doc,include_images=True,preserve_positions=True,spatial=True)[0]['items']
        table=next(item for item in items if item['kind']=='table')
        assert (table['table'].row_count,table['table'].col_count)==(2,2)
        assert any(run.get('image_text') and run['text']=='TEST' for runs in table['cell_runs'].values() for run in runs)
        body,_,_=write_layout(items,360,320,(6,6,6,6),(),{},[],{},'continuous',positioned=True)
        hidden=[run for run in body.iter(f'{{{W}}}r') if run.find(f'{{{W}}}rPr/{{{W}}}vanish') is not None]
        assert not hidden
        assert 'TEST' not in ''.join(n.text or '' for n in body.iter(f'{{{W}}}t'))
        assert 'TEST' in exported_text(body)
        assert all(edge.get(f'{{{W}}}val')=='nil' for node in body.iter(f'{{{W}}}tcBorders') for edge in node)


@pytest.mark.parametrize('grey,tilt',[(.86,0),(.72,.35)])
def test_pale_and_slightly_tilted_rules_are_detected_from_local_contrast(grey,tilt):
    with p.open() as doc:
        page=doc.new_page(width=420,height=320)
        for y in (60,110,160,210,260):page.draw_line((30,y),(390,y),color=(grey,)*3,width=.6)
        for x in (30,150,270,390):page.draw_line((x,60),(x,260),color=(grey,)*3,width=.6)
        pix=page.get_pixmap(dpi=144)
        im=Image.frombytes('RGB',(pix.width,pix.height),pix.samples).rotate(tilt,resample=Image.Resampling.BICUBIC,fillcolor='white')
        stream=BytesIO();im.save(stream,format='JPEG',quality=86)
        with p.open() as scan:
            target=scan.new_page(width=420,height=320);target.insert_image(target.rect,stream=stream.getvalue())
            rules=raster_grid_lines(target)
            assert len(rules)>=9
            assert min(min(start[1],end[1]) for start,end in rules)<65
            assert max(max(start[1],end[1]) for start,end in rules)>255


def test_mixed_ocr_line_crossing_graphic_is_preserved_once_without_erasing_either():
    with p.open() as doc:
        page=doc.new_page(width=420,height=300)
        page.insert_text((30,145),'Approval remains readable',fontsize=11)
        page.draw_circle((300,141),33,color=(.1,.25,.8),width=1.5)
        page.insert_text((283,145),'TEST',fontsize=11,color=(.1,.25,.8))
        page.insert_text((30,220),'Other editable line',fontsize=11)
        with raster_copy(page) as scan:
            items=[dict(kind='text',text='Approval remains readable TEST',bbox=(29,131,322,151),heading=False,size=11),
                   dict(kind='text',text='Other editable line',bbox=(29,208,130,224),heading=False,size=11)]
            warnings=[];picture=restore_scan(scan[0],items,warnings)[0]
            assert items[0]['image_text'] and not items[1]['image_text']
            after=p.Pixmap(picture['data'])
            original=scan[0].get_pixmap(matrix=p.Matrix(after.width/420,after.width/420),alpha=False)
            a=Image.frombytes('RGB',(after.width,after.height),after.samples)
            b=Image.frombytes('RGB',(original.width,original.height),original.samples)
            scale=after.width/420;box=tuple(round(v*scale) for v in (25,100,340,180))
            assert ImageChops.difference(a.crop(box),b.crop(box)).getbbox() is None
            assert warnings


def test_coloured_callout_accent_keeps_ordinary_text_editable():
    with p.open() as doc:
        page=doc.new_page(width=420,height=400)
        page.draw_rect((20,140,400,260),fill=(.94,.98,.95),color=None)
        page.draw_line((20,140),(20,260),color=(.08,.4,.4),width=3)
        page.insert_text((32,163),'Next opening',fontsize=12,color=(.08,.4,.4))
        page.insert_text((32,193),'Budget is 1,486.00.',fontsize=11)
        page.insert_text((32,222),'Complete repairs before Friday.',fontsize=11)
        items=[dict(kind='text',text=''.join(span['text'] for span in line['spans']),
                    bbox=line['bbox'],size=11,heading=False,bold=False)
               for block in page.get_text('dict')['blocks'] for line in block.get('lines',())]
        with raster_copy(page) as scan:
            pictures=restore_scan(scan[0],items,[])
        body,_,_=write_layout(items+pictures,420,400,(6,6,6,6),(),{},[],{},
                             'continuous',positioned=True)
        visible=''.join(node.text or '' for node in body.iter(f'{{{W}}}t'))
        assert all(item['text'] in visible for item in items)
        pix=p.Pixmap(pictures[0]['data']);scale=pix.width/420
        # The accent stays, and editable body text is removed from the image.
        assert pix.pixel(round(20*scale),round(190*scale))[0]<100
        image=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
        crop=image.crop(tuple(round(value*scale) for value in (30,178,250,196)))
        assert min(ImageStat.Stat(crop).extrema[0])>200


def test_scan_box_padding_does_not_expand_short_numbers():
    from xml.etree import ElementTree as ET
    body=ET.Element('body')
    paragraph(body,'42',runs=[dict(text='42',font='Arial',size=10,scanned=True,bbox=(0,0,30,14))])
    assert not list(body.iter(f'{{{W}}}fitText'))
    paragraph(body,'42',runs=[dict(text='42',font='Arial',size=10,scanned=True,bbox=(0,0,6,14))])
    assert len(list(body.iter(f'{{{W}}}fitText')))==1


def test_bright_rules_on_dark_header_and_overlapping_rotated_box_keep_the_grid():
    with p.open() as doc:
        page=doc.new_page(width=420,height=330)
        page.draw_rect((30,50,390,110),fill=(.07,.19,.27),color=None)
        for y in (50,80,110,160,210,260):page.draw_line((30,y),(390,y),color=(.5,.58,.65),width=.6)
        for x in (30,150,270,390):page.draw_line((x,50),(x,260),color=(.5,.58,.65),width=.6)
        for row,y in enumerate((70,100,140,190,240)):
            for col,x in enumerate((40,160,280)):
                page.insert_text((x,y),f'R{row}C{col}',fontsize=8,color=(1,1,1) if row<2 else (0,0,0))
        mark=Image.new('RGBA',(200,100));draw=ImageDraw.Draw(mark)
        draw.rectangle((5,5,195,95),outline=(140,80,180,255),width=3)
        mark=mark.rotate(9,expand=True);data=BytesIO();mark.save(data,format='PNG')
        page.insert_image((300,220,405,285),stream=data.getvalue())
        with raster_copy(page) as scan:
            # Isolate grid geometry here; full acceptance uses actual OCR.
            for row,y in enumerate((70,100,140,190,240)):
                for col,x in enumerate((40,160,280)):
                    scan[0].insert_text((x,y),f'R{row}C{col}',fontsize=8,render_mode=3)
            rules=raster_grid_lines(scan[0])
            tables=scan[0].find_tables(use_layout=False,paths=[],vertical_strategy='lines_strict',horizontal_strategy='lines_strict',add_lines=rules)
            assert [(t.row_count,t.col_count) for t in tables.tables]==[(5,3)]


def test_positioned_native_run_fits_its_original_width():
    from xml.etree import ElementTree as ET
    body=ET.Element('body')
    paragraph(body,'Narrow metadata',fit_width=68,runs=[dict(text='Narrow metadata',font='Times-Roman',size=11,bbox=(20,30,88,44))])
    assert next(body.iter(f'{{{W}}}fitText')).get(f'{{{W}}}val')=='1360'


def test_ragged_antialiasing_ends_do_not_turn_a_thin_horizontal_rule_into_artwork():
    from app.pdf2excel.raster_grid import _thin_segments
    # A blurred boundary has short end fragments at neighbouring scanlines.
    # The entire stroke stays horizontal within a five-pixel band.
    rules=_thin_segments([(40,160,388),(41,14,388),(42,14,388),
                          (43,14,388),(44,14,309)])
    assert len(rules)==1
    assert rules[0][1:]==(14,388)


def test_artwork_above_cell_text_does_not_insert_a_blank_editable_line():
    with p.open() as doc:
        page=doc.new_page(width=360,height=320)
        for y in (70,120,220):page.draw_line((20,y),(330,y))
        for x in (20,120,330):page.draw_line((x,70),(x,220))
        page.insert_text((30,95),'Label',fontsize=11)
        page.insert_text((135,95),'Value',fontsize=11)
        page.insert_text((30,170),'Review',fontsize=11)
        page.draw_circle((250,153),27,color=(.8,.1,.1))
        page.insert_text((232,150),'TEST',fontsize=9,color=(.8,.1,.1))
        page.insert_text((135,175),'Accepted',fontsize=11)
        items=document_layout(doc,include_images=True,preserve_positions=True,spatial=True)[0]['items']
        body,_,_=write_layout(items,360,320,(6,6,6,6),(),{},[],{},'continuous',positioned=True)
        cell=next(node for node in body.iter(f'{{{W}}}tc')
                  if 'Accepted' in ''.join(n.text or '' for n in node.iter(f'{{{W}}}t')))
        assert not list(cell.iter(f'{{{W}}}br'))
        assert 'TEST' in exported_text(body)


@pytest.mark.skipif(__import__('sys').platform!='win32',reason='Installed Windows font outlines')
@pytest.mark.parametrize('text,family,size,heading,bold',[
    ('Category label','Arial',22,True,False),
    ('Weight sample','Arial',22,False,True),
    ('类别','SimSun',10.5,False,False),
])
def test_visible_glyphs_control_scan_weight_and_short_label_size(text,family,size,heading,bold):
    from app.ocr.scan_layout import scan_font
    with p.open() as doc:
        page=doc.new_page(width=360,height=240)
        page.insert_font(fontname='Source',fontbuffer=scan_font(bold,family).buffer)
        page.insert_text((30,100),text,fontname='Source',fontsize=size)
        box=page.get_text('dict')['blocks'][0]['lines'][0]['bbox']
        with raster_copy(page) as scan:
            items=[dict(kind='text',text=text,bbox=box,heading=heading,size=size)]
            restore_scan(scan[0],items,[])
            assert items[0]['bold'] is bold
            assert abs(items[0]['size']-size)<=1.5


@pytest.mark.skipif(__import__('sys').platform!='win32',reason='Installed Windows font outlines')
def test_overlapping_ocr_boxes_measure_only_their_own_line_ink():
    from app.ocr.scan_layout import scan_font
    with p.open() as doc:
        page=doc.new_page(width=300,height=220)
        font=scan_font(False,'SimSun').buffer
        page.insert_font(fontname='Source',fontbuffer=font)
        for text,y in [('现场',100),('支出',112.2)]:
            page.insert_text((40,y),text,fontname='Source',fontsize=10.5)
        with raster_copy(page) as scan:
            scan[0].insert_font(fontname='Search',fontbuffer=font)
            for text,y in [('现场',100),('支出',112.2)]:
                scan[0].insert_text((40,y),text,fontname='Search',fontsize=15,render_mode=3)
            lines=[line for block in scan[0].get_text('dict')['blocks'] for line in block.get('lines',())]
            items=[dict(kind='text',text=line['spans'][0]['text'],bbox=line['bbox'],heading=False,size=15)
                   for line in lines]
            assert items[0]['bbox'][3]>items[1]['bbox'][1]
            restore_scan(scan[0],items,[])
            assert all(9<=item['size']<=12 for item in items)
            assert abs(items[0]['size']-items[1]['size'])<=1
