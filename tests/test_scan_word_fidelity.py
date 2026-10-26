"""Raster appearance and editable-content contracts for the scan exporter."""
import pymupdf as p

from app.documentlayout import document_layout, table_text_runs
from app.ocr.word_export import W,WP,write_layout,exported_text
from app.pdf2excel.models import CellModel,TableModel


def scanned_page(source):
    doc=p.open(); page=doc.new_page(width=source.rect.width,height=source.rect.height)
    page.insert_image(page.rect,stream=source.get_pixmap(dpi=144).tobytes('png'))
    for block in source.get_text('dict')['blocks']:
        for line in block.get('lines',()):
            for span in line['spans']:
                page.insert_text(span['origin'],span['text'],fontsize=span['size'],render_mode=3)
    return doc


def test_scan_keeps_light_blocks_white_title_and_one_copy_of_stamp_ink():
    with p.open() as original:
        page=original.new_page(width=420,height=500)
        page.draw_rect((20,20,400,95),fill=(.05,.25,.3),color=None)
        page.insert_text((35,65),'White heading',fontsize=24,color=(1,1,1))
        page.draw_rect((25,140,395,185),fill=(1,.94,.75),color=None)
        page.insert_text((35,167),'Editable notice',fontsize=12)
        page.insert_text((35,220),'Red note',fontsize=12,color=(.8,.1,.1))
        page.draw_circle((310,320),42,color=(.8,.1,.1),width=2)
        page.insert_text((292,325),'TEST',fontsize=12,color=(.8,.1,.1))
        with scanned_page(page) as scan:
            record=document_layout(scan,include_images=True,preserve_positions=True,spatial=True)[0]
            layout=record['items']
            title=next(item for item in layout if item.get('text')=='White heading')
            assert min(bytes.fromhex(title['colour'][1:]))>230
            stamp=next(item for item in layout if item.get('text')=='TEST')
            assert stamp['image_text']
            notice=next(item for item in layout if item.get('text')=='Editable notice')
            assert not notice['image_text'] and notice['runs'][0]['font']=='Arial'
            picture=next(item for item in layout if item['kind']=='image')
            pix=p.Pixmap(picture['data']); scale=pix.width/420
            assert pix.pixel(round(30*scale),round(150*scale))[0]>240
            assert pix.pixel(round(30*scale),round(150*scale))[2]<210
            assert pix.pixel(round(40*scale),round(217*scale))[:3]==(255,255,255)
            body,_,_=write_layout(layout,420,500,(6,6,6,6),(),{},[],{},'continuous',positioned=True)
            hidden=[run for run in body.iter(f'{{{W}}}r') if run.find(f'{{{W}}}rPr/{{{W}}}vanish') is not None]
            assert not hidden
            assert [node.get('descr') for node in body.iter(f'{{{WP}}}docPr') if node.get('descr')]==['TEST']
            assert 'TEST' in exported_text(body)
            assert 'TEST' not in ''.join(n.text or '' for n in body.iter(f'{{{W}}}t'))
            fits=list(body.iter(f'{{{W}}}fitText'))
            ids=[node.get(f'{{{W}}}id') for node in fits]
            assert len(ids)==len(set(ids))


def test_parallel_checklists_do_not_become_a_table():
    with p.open() as original:
        page=original.new_page(width=500,height=400)
        for left,fill in ((30,(.88,.94,.94)),(270,(.98,.94,.86))):
            page.draw_rect((left,40,left+200,250),fill=fill,color=None)
            page.insert_text((left+10,66),'Checklist',fontsize=12)
            for row in range(4):
                page.insert_text((left+10,100+row*27),f'[ ] Check item {row+1}',fontsize=11)
        with scanned_page(page) as scan:
            layout=document_layout(scan,include_images=True,preserve_positions=True,spatial=True)[0]['items']
            assert not any(item['kind']=='table' for item in layout)
            assert sum(item.get('text','').startswith('[ ]') for item in layout)==8


def test_antialiasing_does_not_turn_black_ink_grey_or_grey_ink_black():
    with p.open() as original:
        page=original.new_page(width=420,height=300)
        page.insert_text((30,70),'Fine text 839.41',fontname='tiro',fontsize=9)
        page.insert_text((30,125),'Light note 456.28',fontsize=12,color=(.6,.6,.6))
        with scanned_page(page) as scan:
            items=document_layout(scan,include_images=True,preserve_positions=True,spatial=True)[0]['items']
            black=next(i for i in items if i.get('text')=='Fine text 839.41')
            grey=next(i for i in items if i.get('text')=='Light note 456.28')
            assert max(bytes.fromhex(black['colour'][1:]))<70
            assert min(bytes.fromhex(grey['colour'][1:]))>=120


def test_ocr_numeric_fragments_on_nearby_baselines_form_one_cell_line():
    fragments=[('860',(20,22,40,36)),('.',(40,22.4,44,36.4)),('00',(44,22.8,57,36.8))]
    raw={'blocks':[{'lines':[dict(bbox=box,spans=[dict(alpha=0,chars=[dict(c=text,bbox=box)])])
                              for text,box in fragments]}]}
    cell=CellModel(0,0,text='860.00',bbox=(10,10,90,50))
    table=TableModel(page_no=1,bbox=cell.bbox,row_count=1,col_count=1,cells=[cell],col_widths=[80],row_heights=[40])
    runs=table_text_runs(raw,table,include_hidden=True)[0,0]
    assert ''.join(run['text'] for run in runs)=='860.00'


def test_scan_heading_uses_recovered_size_when_search_font_hides_hierarchy():
    from app.ocr.scan_layout import restore_scan
    with p.open() as original:
        page=original.new_page(width=420,height=400)
        page.insert_text((30,50),'Independent headline',fontname='cobo',fontsize=20)
        for index in range(4):
            page.insert_text((30,120+index*40),'Diagram label',fontsize=12)
        with scanned_page(page) as scan:
            items=[]
            for block in page.get_text('dict')['blocks']:
                for line in block.get('lines',()):
                    items.append(dict(kind='text',text=''.join(s['text'] for s in line['spans']),
                                      bbox=line['bbox'],heading=False,size=11))
            restore_scan(scan[0],items,[])
            assert items[0]['heading'] and items[0]['runs'][0]['bold']
            assert all(not item['heading'] for item in items[1:])
