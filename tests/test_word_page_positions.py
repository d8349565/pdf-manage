"""Fixed-page XML contracts; no Office, OCR engine, or customer files needed."""
from xml.etree import ElementTree as ET

from app.ocr.word_export import W, WP, paragraph, write_layout
from app.pdf2excel.models import CellModel, TableModel
from app.documentlayout import _positioned_line_parts


def test_fixed_page_keeps_editable_text_table_and_image_in_page_coordinates():
    table=TableModel(page_no=1,bbox=(60,210,340,250),row_count=1,col_count=2,
                     cells=[CellModel(0,0,text='项目'),CellModel(0,1,text='金额')],
                     row_heights=[40],col_widths=[140,140])
    layout=[dict(kind='text',text='报价单',bbox=(180,70,300,110),size=30,bold=True),
            dict(kind='table',table=table,bbox=table.bbox),
            dict(kind='image',bbox=(350,270,410,330),data=b'picture-part')]
    parts={}; links=[]
    body,columns,break_type=write_layout(layout,500,700,(36,36,36,36),(),
                                         parts,links,{},'continuous',positioned=True)
    assert columns==() and break_type=='continuous'
    assert ''.join(node.text or '' for node in body.iter(f'{{{W}}}t'))=='报价单项目金额'
    frame=next(body.iter(f'{{{W}}}framePr'))
    assert frame.get(f'{{{W}}}x')=='3600' and frame.get(f'{{{W}}}y')=='1400'
    table_position=next(body.iter(f'{{{W}}}tblpPr'))
    assert table_position.get(f'{{{W}}}tblpX')=='1200'
    assert table_position.get(f'{{{W}}}tblpY')=='4200'
    assert next(body.iter(f'{{{W}}}trHeight')).get(f'{{{W}}}hRule')=='exact'
    assert list(body.iter(f'{{{W}}}cantSplit'))
    anchor=next(body.iter(f'{{{WP}}}anchor'))
    assert anchor.find(f'{{{WP}}}positionH').get('relativeFrom')=='page'
    assert anchor.find(f'{{{WP}}}positionV/{{{WP}}}posOffset').text==str(270*12700)
    assert not list(body.iter(f'{{{WP}}}inline'))
    assert body[-1].tag==f'{{{W}}}p' and body[-1].find(f'{{{W}}}pPr/{{{W}}}framePr') is None
    assert parts['word/media/image-1.png']==b'picture-part'


def test_reflow_tabs_are_word_tab_elements_and_keep_all_field_text():
    body=ET.Element(f'{{{W}}}body')
    paragraph(body,'左侧\t右侧')
    assert len(list(body.iter(f'{{{W}}}tab')))==1
    assert [node.text for node in body.iter(f'{{{W}}}t')]==['左侧','右侧']


def test_positioned_line_keeps_title_together_and_distant_fields_separate():
    chars=[dict(c=text,bbox=box) for text,box in
           [('报',(20,30,40,55)),('价',(41,30,61,55)),('单',(62,30,82,55)),
            ('日',(170,30,190,55)),('期',(191,30,211,55))]]
    groups=_positioned_line_parts(chars,[20]*5,(1,0))
    assert [''.join(char['c'] for char in part) for part,_ in groups]==['报价单','日期']
    assert groups[1][0][0]['bbox'][0]==170


def scan_table(left,top):
    cells=[CellModel(row,col,text='Value' if (row,col)==(0,0) else '',
                     bbox=(left+col*50,top+row*20,left+(col+1)*50,top+(row+1)*20))
           for row in range(2) for col in range(2)]
    table=TableModel(page_no=1,bbox=(left,top,left+100,top+40),row_count=2,col_count=2,
                     cells=cells,row_heights=[20,20],col_widths=[50,50])
    return dict(kind='table',bbox=table.bbox,table=table,scanned=True)


def test_stacked_scan_tables_have_distinct_word_boundaries():
    layout=[scan_table(20,100),scan_table(140,140.75)]
    body,_,_=write_layout(layout,500,250,(6,6,6,6),(),{},[],{},'continuous',positioned=True)
    first,second=[index for index,node in enumerate(body) if node.tag==f'{{{W}}}tbl']
    assert (any(node.tag==f'{{{W}}}p' for node in body[first+1:second])
            or any(node.find(f'{{{W}}}tblPr/{{{W}}}tblpPr') is not None for node in (body[first],body[second])))


def test_inline_scan_cell_text_fits_the_reserved_row_height():
    item=scan_table(20,100)
    item['cell_runs']={(0,0):[dict(text='Value',bbox=(26,114,50,120),size=8,scanned=True)]}
    body,_,_=write_layout([item],500,250,(6,6,6,6),(),{},[],{},'continuous',positioned=True)
    assert not list(body.iter(f'{{{W}}}tblpPr'))
    row=next(body.iter(f'{{{W}}}tr'))
    height=int(row.find(f'{{{W}}}trPr/{{{W}}}trHeight').get(f'{{{W}}}val'))
    spacing=row.find(f'{{{W}}}tc/{{{W}}}p/{{{W}}}pPr/{{{W}}}spacing')
    assert int(spacing.get(f'{{{W}}}before'))+int(spacing.get(f'{{{W}}}line'))<=height
