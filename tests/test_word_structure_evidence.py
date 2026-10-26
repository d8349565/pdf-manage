"""Layout invariants use varied invented content, never customer-document conditions."""
import pymupdf as p

from app.documentlayout import page_layout,table_has_visible_grid
from app.pdf2excel.models import TableModel


def test_regular_word_spaces_are_not_table_column_gutters():
    with p.open() as doc:
        page=doc.new_page(width=600,height=700)
        page.insert_text((42,55),'Recurring status bulletin',fontsize=19,fontname='hebo')
        for row in range(11):
            page.insert_text((42,130+row*24),'A daily notice may contain evenly aligned words.',fontsize=11)
        items,_=page_layout(page,preserve_positions=True,spatial=True)
        assert not any(item['kind']=='table' for item in items)
        assert sum('A daily notice' in item.get('text','') for item in items)==11


def test_fixed_text_still_has_column_reading_order_for_numbered_lists():
    with p.open() as doc:
        page=doc.new_page(width=600,height=700)
        for col,x in enumerate((40,330)):
            for row in range(5):
                page.insert_text((x,110+row*30),f'Task {col+1} step {row+1}',fontsize=11)
        items,_=page_layout(page,preserve_positions=True,spatial=True)
        texts=[item['text'] for item in items if item['kind']=='text']
        assert texts==[f'Task {col+1} step {row+1}' for col in range(2) for row in range(5)]


def test_coloured_outer_panel_does_not_prove_internal_table_rows():
    with p.open() as doc:
        page=doc.new_page(width=400,height=400)
        page.draw_rect((30,40,370,240),fill=(.8,.9,.9),color=(.2,.3,.3))
        table=TableModel(page_no=1,bbox=(30,40,370,240),row_count=4,col_count=2,cells=[],
                         col_widths=[170,170],row_heights=[50]*4)
        assert not table_has_visible_grid(page,table)
        for y in (90,140,190):page.draw_line((30,y),(370,y),color=(.2,.3,.3))
        page.draw_line((200,40),(200,240),color=(.2,.3,.3))
        assert table_has_visible_grid(page,table)


def test_column_headers_and_longer_column_stay_with_their_own_body():
    with p.open() as doc:
        page=doc.new_page(width=600,height=800)
        for col,x in enumerate((40,330)):
            page.insert_text((x,90),f'Column {col+1} heading',fontsize=11)
            for row in range(5+col*2):
                page.insert_text((x,128+row*28),f'Task {col+1} item {row+1}',fontsize=9)
        page.insert_text((40,760),'Separate page footer',fontsize=8)
        items,_=page_layout(page,preserve_positions=True,spatial=True)
        texts=[item['text'] for item in items if item['kind']=='text']
        expected=[]
        for col in range(2):
            expected += [f'Column {col+1} heading']+[f'Task {col+1} item {row+1}' for row in range(5+col*2)]
        assert texts==expected+['Separate page footer']
