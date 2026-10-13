"""Reading flow tests use unrelated synthetic sentences and geometric layouts."""
from app.documentlayout import layout_from_raw, layout_text


def raw_lines(values):
    lines=[]
    for text,box,size in values:
        step=(box[2]-box[0])/max(1,len(text))
        chars=[dict(c=char,bbox=(box[0]+index*step,box[1],box[0]+(index+1)*step,box[3]))
               for index,char in enumerate(text)]
        lines.append(dict(bbox=box,dir=(1,0),spans=[dict(size=size,flags=0,chars=chars)]))
    return dict(blocks=[dict(type=0,lines=lines)])


def test_chinese_wrapped_lines_form_one_editable_paragraph():
    raw=raw_lines([('这是连续正文的第一行内容',(50,100,350,114),12),
                   ('第二行继续说明同一个完整段落。',(50,118,345,132),12),
                   ('另一个独立段落。',(50,170,180,184),12)])
    layout=layout_from_raw(raw,[])
    assert len(layout)==2
    assert layout[0]['line_count']==2 and '内容第二行' in layout[0]['text']
    assert '\n' not in layout[0]['text']


def test_western_flow_and_soft_hyphen():
    raw=raw_lines([('An editable document allows',(40,100,340,114),12),
                   ('natural reflow during revision.',(40,118,335,132),12)])
    layout=layout_from_raw(raw,[])
    assert layout[0]['text']=='An editable document allows natural reflow during revision.'
    raw=raw_lines([('A sample inter\u00ad',(40,100,340,114),12),('national term.',(40,118,330,132),12)])
    assert layout_from_raw(raw,[])[0]['text']=='A sample international term.'


def test_indentation_and_list_items_are_not_merged_into_previous_paragraph():
    raw=raw_lines([('First paragraph reaches its boundary',(40,100,340,114),12),
                   ('Indented paragraph begins here',(64,118,340,132),12),
                   ('continuation of the indented paragraph.',(40,136,337,150),12),
                   ('1. Independent list entry',(40,170,240,184),12)])
    layout=layout_from_raw(raw,[])
    assert len(layout)==3 and layout[1]['first_line_indent']==24
    assert layout[1]['line_count']==2 and layout[2]['text'].startswith('1.')


def test_two_columns_read_down_left_then_down_right():
    values=[('A wider heading',(50,45,500,74),24)]
    for row in range(4):
        y=110+row*20
        values.extend([(f'Left body sentence {row}.',(50,y,245,y+14),12),
                       (f'Right body sentence {row}.',(300,y,495,y+14),12)])
    values.append(('A closing note.',(50,260,220,274),12))
    layout=layout_from_raw(raw_lines(values),[])
    text=layout_text(layout)
    assert text.index('Left body sentence 3.')<text.index('Right body sentence 0.')
    assert '\t' not in text and text.endswith('A closing note.')
    assert any(item.get('column_count')==2 for item in layout)


def test_short_aligned_numbers_keep_row_order():
    values=[]
    for row in range(4):
        y=100+row*30
        values.extend([(f'{row+20}',(50,y,80,y+14),12),(f'{row+70}',(180,y,210,y+14),12)])
    assert layout_text(layout_from_raw(raw_lines(values),[])).splitlines()[0]=='20\t70'
