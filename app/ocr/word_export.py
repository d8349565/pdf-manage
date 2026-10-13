"""Export an existing OCR text layer to editable DOCX without PDF reflow."""
from pathlib import Path
from collections import Counter
from dataclasses import replace
import math
import os
import tempfile
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

from app.output import check_cancel, commit_file, stamp

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
ET.register_namespace('w', W)
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
WP='http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
A='http://schemas.openxmlformats.org/drawingml/2006/main'
PIC='http://schemas.openxmlformats.org/drawingml/2006/picture'
for prefix,namespace in (('r',R),('wp',WP),('a',A),('pic',PIC)):
    ET.register_namespace(prefix,namespace)


def element(parent, name, **attributes):
    return ET.SubElement(parent, f'{{{W}}}{name}', {f'{{{W}}}{key}': str(value) for key, value in attributes.items()})


def paragraph(parent, text='', *, size=11, bold=False, after=4, before=0, first_line_indent=0, colour=None, line_height=None, alignment=None, fit_width=None):
    node = element(parent, 'p')
    properties = element(node, 'pPr')
    element(properties, 'widowControl', val=0)
    element(properties, 'autoSpaceDE', val=0)
    element(properties, 'autoSpaceDN', val=0)
    element(properties, 'snapToGrid', val=0)
    spacing=element(properties, 'spacing', after=round(after*20), before=round(before*20))
    if line_height:
        spacing.set(f'{{{W}}}line',str(round(max(size*1.25,line_height)*20)))
        spacing.set(f'{{{W}}}lineRule','exact')
    if first_line_indent:
        element(properties,'ind',firstLine=round(first_line_indent*20))
    if alignment in ('left', 'center', 'right'):
        element(properties, 'jc', val=alignment)
    # Word's paragraph/cell-end mark otherwise keeps the 11pt default and
    # expands dense rows even when every visible run has a smaller font.
    mark=element(properties,'rPr')
    element(mark,'sz',val=max(2,min(144,round(size*2))))
    element(mark,'szCs',val=max(2,min(144,round(size*2))))
    for index, line in enumerate(text.split('\n')):
        run = element(node, 'r')
        run_properties = element(run, 'rPr')
        if bold:
            element(run_properties, 'b')
        if colour:
            element(run_properties,'color',val=colour.lstrip('#'))
        element(run_properties, 'sz', val=max(2, min(144, round(size*2))))
        element(run_properties, 'szCs', val=max(2, min(144, round(size*2))))
        if fit_width:
            element(run_properties, 'fitText', val=max(20, round(fit_width*20)))
        if index:
            element(run, 'br')
        for field_index, field in enumerate(line.split('\t')):
            if field_index:
                element(run, 'tab')
            value = element(run, 't')
            value.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
            value.text = field
    return node


def section(parent, width, height, *, break_type='continuous', references=(), margins=(36,36,36,36), columns=()):
    node = element(parent, 'sectPr')
    for role,identity in references:
        reference=element(node,role+'Reference',type='default')
        reference.set(f'{{{R}}}id',identity)
    element(node, 'type', val=break_type)
    element(node, 'pgSz', w=round(width * 20), h=round(height * 20),orient='landscape' if width>height else 'portrait')
    left,top,right,bottom=margins
    element(node, 'pgMar', top=round(top*20), bottom=round(bottom*20), left=round(left*20), right=round(right*20), header=180, footer=180, gutter=0)
    if columns:
        grid=element(node,'cols',num=len(columns),equalWidth=0)
        for column_width,space in columns:
            element(grid,'col',w=round(column_width*20),space=round(space*20))
    else:
        element(node,'cols',num=1)


def page_margins(layout, width, height):
    boxes=[item['bbox'] for item in layout if not item.get('role')]
    if not boxes:
        return (36,36,36,36)
    return (max(6,min(72,min(box[0] for box in boxes))),
            max(6,min(72,min(box[1] for box in boxes))),
            max(6,min(72,width-max(box[2] for box in boxes))),
            max(6,min(36,height-max(box[3] for box in boxes))))


def column_settings(item, available):
    bounds=item.get('column_bounds',())
    if len(bounds)<2:
        return ()
    widths=[max(28,right-left) for left,right in bounds]
    gaps=[max(6,right[0]-left[1]) for left,right in zip(bounds,bounds[1:])]+[0]
    ratio=available/max(1,sum(widths)+sum(gaps))
    return tuple((width*ratio,gap*ratio) for width,gap in zip(widths,gaps))


def picture(parent,item,identity,index,available, *, positioned=False):
    width=item['bbox'][2]-item['bbox'][0]; height=item['bbox'][3]-item['bbox'][1]
    ratio=min(1,available/max(1,width))
    cx,cy=round(width*ratio*12700),round(height*ratio*12700)
    drawing=element(element(paragraph(parent,size=1,after=0,line_height=1.3),'r'),'drawing')
    if positioned:
        inline=ET.SubElement(drawing,f'{{{WP}}}anchor',distT='0',distB='0',distL='0',distR='0',
                             simplePos='0',relativeHeight=str(index),behindDoc='0',locked='0',layoutInCell='1',allowOverlap='1')
        ET.SubElement(inline,f'{{{WP}}}simplePos',x='0',y='0')
        for direction,coordinate in (('H',item['bbox'][0]),('V',item['bbox'][1])):
            position=ET.SubElement(inline,f'{{{WP}}}position{direction}',relativeFrom='page')
            ET.SubElement(position,f'{{{WP}}}posOffset').text=str(round(coordinate*12700))
    else:
        inline=ET.SubElement(drawing,f'{{{WP}}}inline',distT='0',distB='0',distL='0',distR='0')
    ET.SubElement(inline,f'{{{WP}}}extent',cx=str(cx),cy=str(cy))
    if positioned:
        ET.SubElement(inline,f'{{{WP}}}effectExtent',l='0',t='0',r='0',b='0')
        ET.SubElement(inline,f'{{{WP}}}wrapNone')
    ET.SubElement(inline,f'{{{WP}}}docPr',id=str(index),name=f'Illustration {index}')
    frame=ET.SubElement(inline,f'{{{WP}}}cNvGraphicFramePr')
    ET.SubElement(frame,f'{{{A}}}graphicFrameLocks',noChangeAspect='1')
    graphic=ET.SubElement(inline,f'{{{A}}}graphic')
    data=ET.SubElement(graphic,f'{{{A}}}graphicData',uri=PIC)
    image=ET.SubElement(data,f'{{{PIC}}}pic')
    properties=ET.SubElement(image,f'{{{PIC}}}nvPicPr')
    ET.SubElement(properties,f'{{{PIC}}}cNvPr',id=str(index),name=f'image-{index}.png')
    ET.SubElement(properties,f'{{{PIC}}}cNvPicPr')
    fill=ET.SubElement(image,f'{{{PIC}}}blipFill')
    ET.SubElement(fill,f'{{{A}}}blip',{f'{{{R}}}embed':identity})
    stretch=ET.SubElement(fill,f'{{{A}}}stretch'); ET.SubElement(stretch,f'{{{A}}}fillRect')
    shape=ET.SubElement(image,f'{{{PIC}}}spPr')
    transform=ET.SubElement(shape,f'{{{A}}}xfrm')
    ET.SubElement(transform,f'{{{A}}}off',x='0',y='0')
    ET.SubElement(transform,f'{{{A}}}ext',cx=str(cx),cy=str(cy))
    geometry=ET.SubElement(shape,f'{{{A}}}prstGeom',prst='rect')
    ET.SubElement(geometry,f'{{{A}}}avLst')


def write_table(body, table, available=None, available_height=None, *, positioned=False):
    import pymupdf as p
    ratio=min(1,available/max(1,sum(table.col_widths))) if available is not None else 1
    if available_height is not None:
        ratio=min(ratio,max(1,available_height)/max(1,sum(table.row_heights)))
    font=p.Font('cjk')
    padding=min(2,min(table.col_widths,default=50)*ratio*.04)
    node = element(body, 'tbl')
    properties = element(node, 'tblPr')
    if positioned:
        element(properties,'tblpPr',horzAnchor='page',vertAnchor='page',tblpX=round(table.bbox[0]*20),
                tblpY=round(table.bbox[1]*20),leftFromText=1,rightFromText=1,topFromText=1,bottomFromText=1)
        element(properties,'tblOverlap',val='overlap')
    element(properties, 'tblW', w=round(sum(table.col_widths)*ratio*20), type='dxa')
    cell_margins=element(properties,'tblCellMar')
    for edge in ('top','left','bottom','right'):
        element(cell_margins,edge,w=round((padding if edge in ('left','right') else 0)*20),type='dxa')
    borders = element(properties, 'tblBorders')
    colour='606060' if any(cell.style.border_left or cell.style.border_top for cell in table.cells) else 'E5E7EB'
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        element(borders, edge, val='single', sz=4, color=colour)
    properties.remove(cell_margins)
    element(properties, 'tblLayout', type='fixed')
    properties.append(cell_margins)
    grid = element(node, 'tblGrid')
    for width in table.col_widths:
        element(grid, 'gridCol', w=max(20, round(width * ratio * 20)))
    cells = {(cell.row, cell.col): cell for cell in table.cells}
    coverage = {}
    for cell in table.cells:
        if cell.row_span > 1 or cell.col_span > 1:
            for row in range(cell.row, min(table.row_count, cell.row + cell.row_span)):
                for col in range(cell.col, min(table.col_count, cell.col + cell.col_span)):
                    coverage[row, col] = cell
    for row in range(table.row_count):
        tr = element(node, 'tr')
        tr_properties = element(tr, 'trPr')
        element(tr_properties, 'cantSplit')
        if row < len(table.row_heights):
            # trHeight describes the content; Word adds the half-point grid rule.
            element(tr_properties, 'trHeight', val=max(20,round((table.row_heights[row]*ratio-.5)*20)),
                    hRule='exact' if positioned else 'atLeast')
        col = 0
        while col < table.col_count:
            cell = coverage.get((row, col), cells.get((row, col)))
            span = min(cell.col_span, table.col_count - col) if cell else 1
            tc = element(tr, 'tc')
            props = element(tc, 'tcPr')
            cell_width=sum(table.col_widths[col:col+span])*ratio
            element(props,'tcW',w=max(20,round(cell_width*20)),type='dxa')
            if span > 1:
                element(props, 'gridSpan', val=span)
            continuation = cell is not None and cell.row < row
            if cell and cell.row_span > 1:
                element(props, 'vMerge', **({} if continuation else {'val': 'restart'}))
            if cell and cell.style.fill_color:
                element(props,'shd',val='clear',fill=cell.style.fill_color.lstrip('#'))
            element(props, 'vAlign', val=cell.style.vertical or 'center' if cell else 'center')
            text='' if continuation or cell is None else cell.text
            size=(cell.style.font_size or 11)*ratio if cell else 11*ratio
            units=max((font.text_length(line,fontsize=1) for line in text.splitlines()),default=0)
            if units:
                size=min(size,max(1,cell_width-2*padding)/units)
            cell_height=sum(table.row_heights[row:row+(cell.row_span if cell else 1)])*ratio
            if cell_height:
                size=min(size,cell_height/(max(1,len(text.splitlines()))*1.35))
            size=max(1,math.floor(size*2)/2)
            paragraph(tc, text,
                      size=size,
                      after=0,
                      bold=(row == 0 or cell.style.bold) if cell else row == 0,
                      colour=cell.style.font_color if cell else None,
                      line_height=size*1.35,
                      alignment=cell.style.horizontal if cell else None,
                      fit_width=cell_width-2*padding if units*size>cell_width-2*padding else None)
            col += span


def write_layout(layout, width, height, margins, references, parts, links, image_links, start_type, cancel=None, *, positioned=False):
    body=ET.Element(f'{{{W}}}body')
    available=max(1,width-margins[0]-margins[2])
    current_columns=(); current_column=0
    if positioned:
        # Floating tables attach to a normal paragraph on this PDF page.
        paragraph(body,size=1,after=0,line_height=1.3)
    for offset,item in enumerate(layout):
        check_cancel(cancel)
        columns=column_settings(item,available) if not positioned and item.get('column_count',1)>1 else ()
        if columns!=current_columns:
            if len(body):
                boundary=paragraph(body,size=1,after=0,line_height=1.3)
                section(boundary.find(f'{{{W}}}pPr'),width,height,break_type=start_type,
                        references=references,margins=margins,columns=current_columns)
                start_type='continuous'
            current_columns=columns
            current_column=item.get('column',0)
        elif columns and item.get('column',0)!=current_column:
            element(element(paragraph(body,size=1,after=0,line_height=1.3),'r'),'br',type='column')
            current_column=item['column']
        item_width=columns[current_column][0] if columns else available
        if item['kind']=='table':
            if positioned:
                left,top,right,bottom=item['bbox']
                write_table(body,item['table'],right-left,bottom-top,positioned=True)
                paragraph(body,size=1,after=0,line_height=1.3)
            else:
                write_table(body,item['table'],item_width,height-margins[3]-max(margins[1],item['bbox'][1])-3)
        elif item['kind']=='image':
            if id(item) not in image_links:
                count=1+len(image_links)
                identity=f'rIdImage{count}'; name=f'media/image-{count}.png'
                parts['word/'+name]=item['data']; links.append((identity,'image',name))
                image_links[id(item)]=(identity,count)
            identity,count=image_links[id(item)]
            picture(body,item,identity,count,width if positioned else item_width,positioned=positioned)
        else:
            if positioned or item.get('positioned'):
                left,top,right,bottom=item['bbox']
                size=max(1,min(item['size'],(bottom-top)/(1.25*item.get('line_count',1))))
                node=paragraph(body,item['text'],size=size,bold=item['bold'],after=0,
                               line_height=min(size*1.35,(bottom-top)/item.get('line_count',1)),
                               colour=item.get('colour'),fit_width=right-left)
                properties=node.find(f'{{{W}}}pPr')
                frame=element(properties,'framePr',w=max(20,round((right-left+.5)*20)),h=max(20,round((bottom-top)*20)),
                              x=round(left*20),y=round(top*20),hAnchor='page',vAnchor='page',wrap='none',
                              hRule='atLeast',hSpace=0,vSpace=0,anchorLock=1)
                properties.remove(frame); properties.insert(0,frame)
                continue
            following=layout[offset+1] if offset+1<len(layout) else None
            gap=following['bbox'][1]-item['bbox'][3] if following and following.get('column',-1)==item.get('column',-1) else 4
            before=max(0,min(60,item['bbox'][1]-layout[offset-1]['bbox'][3])) if offset and layout[offset-1]['kind']=='table' else 0
            paragraph(body,item['text'],size=item['size'],bold=item['bold'],
                      after=max(4,min(40,gap)),before=before,first_line_indent=item.get('first_line_indent',0),
                      line_height=(item['bbox'][3]-item['bbox'][1])/item.get('line_count',1))
    if positioned or any(item.get('positioned') for item in layout):
        # Text frames anchor to the next non-frame paragraph, including on the
        # last page where there is no section-break paragraph to act as anchor.
        paragraph(body,size=1,after=0,line_height=1.3)
    return body,current_columns,start_type


def scaled_layout(layout, ratio):
    """Scale oversized PDF pages as one unit within Word's supported paper size."""
    if ratio==1:
        return layout
    output=[]
    for item in layout:
        item=dict(item,bbox=tuple(value*ratio for value in item['bbox']))
        for key in ('size','first_line_indent'):
            if key in item:
                item[key]*=ratio
        if 'column_bounds' in item:
            item['column_bounds']=tuple(tuple(value*ratio for value in bounds) for bounds in item['column_bounds'])
        if item['kind']=='table':
            table=item['table']
            cells=[replace(cell,bbox=tuple(value*ratio for value in cell.bbox) if cell.bbox else None,
                           style=replace(cell.style,font_size=cell.style.font_size*ratio if cell.style.font_size else None)) for cell in table.cells]
            item['table']=replace(table,bbox=item['bbox'],cells=cells,
                                  col_widths=[width*ratio for width in table.col_widths],row_heights=[height*ratio for height in table.row_heights])
        output.append(item)
    return output


def character_counts(text):
    return Counter(char for char in text if not char.isspace() and char!='\u00ad')


def preserve_table_regions(raw, layout, warnings, page_index, *, positioned=False):
    """Downgrade only an unverified region, retaining every other native table."""
    import pymupdf as p
    from app.documentlayout import layout_from_raw, order_figures
    tables=[item['table'] for item in layout if item['kind']=='table']
    bad=[]; good=[]
    for table in tables:
        area=p.Rect(table.bbox)
        expected=''.join(char['c'] for block in raw.get('blocks',()) for line in block.get('lines',())
                         for span in line.get('spans',()) for char in span.get('chars',())
                         if area.contains((p.Rect(char['bbox']).tl+p.Rect(char['bbox']).br)/2))
        body=ET.Element(f'{{{W}}}body'); write_table(body,table)
        actual=''.join(node.text or '' for node in body.iter(f'{{{W}}}t'))
        (good if character_counts(expected)==character_counts(actual) else bad).append(table)
    dense=not tables and sum(item['kind']=='text' and item['text'].count('\t')>=2 for item in layout)>=8
    if not bad and not dense:
        return layout
    areas=[table.bbox for table in bad] if bad and not positioned else [(0,0,raw['width'],raw['height'])]
    recovered=layout_from_raw(raw,good,positioned_areas=areas)
    recovered=order_figures(recovered,[item for item in layout if item['kind']=='image'])
    warnings.append(f'第 {page_index+1} 页部分表格未能完整还原网格，已按原位置保留全部识别文字，其他表格保持可编辑结构。')
    return recovered


def pdf_has_ocr_text(source, cancel=None):
    """Routing probe only; validation and user-facing errors remain in the conversion."""
    import pymupdf as p
    try:
        with p.open(source) as doc:
            if doc.is_encrypted or not doc.is_pdf:
                return False
            for page in doc:
                check_cancel(cancel)
                if any(span['type'] == 3 and any(chr(char[0]).isalnum() for char in span['chars'])
                       for span in page.get_texttrace()):
                    return True
            return False
    except (OSError, RuntimeError, ValueError):
        return False


def convert_ocr_pdf_to_docx(source, target, overwrite, progress, cancel, *, runtime_dir=None):
    from app.ocr.export_input import prepared_text_pdf
    from app.wordconvert import validate_pdf_for_word
    validate_pdf_for_word(source)
    source,target=Path(source).resolve(),Path(target).resolve()
    if target.suffix.lower()!='.docx' or not target.parent.is_dir():
        raise ValueError('请选择已有文件夹中的 .docx 输出路径。')
    if source==target or (target.exists() and os.path.samefile(source,target)):
        raise ValueError('Word 输出不能覆盖源 PDF。')
    if stamp(target) is not None and not overwrite:
        return 'skipped', '已有同名输出，按默认规则跳过', ''
    original=stamp(source)
    with prepared_text_pdf(source,Path(target).parent,progress,cancel,runtime_dir) as working:
        return _export_recognized_docx(working,target,overwrite,progress,cancel,source_guard=(source,original))


def _export_recognized_docx(source, target, overwrite, progress, cancel, *, source_guard=None):
    import pymupdf as p
    from app.wordconvert import validate_docx, validate_pdf_for_word
    from app.documentlayout import document_layout, layout_from_raw, order_figures
    from app.documentimages import page_figures
    validate_pdf_for_word(source)
    source, target = Path(source).resolve(), Path(target).resolve()
    expected = stamp(target)
    if expected is not None and not overwrite:
        return 'skipped', '已有同名输出，按默认规则跳过', ''
    if source == target or (target.exists() and os.path.samefile(source, target)):
        raise ValueError('Word 输出不能覆盖源 PDF。')
    original = stamp(source)
    document = ET.Element(f'{{{W}}}document')
    body = element(document, 'body')
    warnings = []
    parts={}; links=[]; image_links={}
    temp = None
    try:
        with p.open(source) as doc:
            pages=document_layout(doc,include_images=True,preserve_positions=True,cancel=cancel,progress=progress)
            margin_roles={item['role'] for record in pages for item in record['items'] if item.get('role')}
            for index, page in enumerate(doc):
                check_cancel(cancel)
                if progress:
                    progress(f'正在导出识别文字 · 第 {index + 1}/{doc.page_count} 页')
                rotation = page.rotation
                page.set_rotation(0)
                try:
                    if not page.get_text().strip() and page.get_images():
                        raise ValueError(f'第 {index + 1} 页没有可提取文字，请先完成该页 OCR 再转换。')
                    record=pages[index]
                    positioned=record.get('positioned',False)
                    all_items=record['items']
                    layout=list(all_items) if positioned else [item for item in all_items if not item.get('role')]
                    source_width,source_height=page.rect.width,page.rect.height
                    page_ratio=min(1,1584/max(source_width,source_height))
                    if page_ratio<1:
                        warnings.append(f'第 {index+1} 页超出 Word 纸张尺寸，已等比例缩放页面、表格和文字。')
                    warnings.extend(record['warnings'])
                    references=[]
                    marginal=[] if positioned else [item for item in all_items if item.get('role')]
                    for role,root_name in (('header','hdr'),('footer','ftr')):
                        entries=[item for item in marginal if item['role']==role]
                        if role not in margin_roles:
                            continue
                        root=ET.Element(f'{{{W}}}{root_name}')
                        if not entries:
                            paragraph(root,after=0)
                        for item in entries:
                            node=paragraph(root,item['text'],size=item['size']*page_ratio,bold=item['bold'])
                            if role=='footer' and item['text'].strip()==str(index+1):
                                for run in node.findall(f'{{{W}}}r'):
                                    node.remove(run)
                                field=element(node,'fldSimple',instr='PAGE')
                                element(element(field,'r'),'t').text=item['text']
                        name=f'{role}{index+1}.xml'; identity=f'rId{role}{index+1}'
                        parts['word/'+name]=ET.tostring(root,encoding='utf-8',xml_declaration=True)
                        links.append((identity,role,name)); references.append((role,identity))
                    raw = page.get_text('rawdict', flags=p.TEXTFLAGS_RAWDICT & ~p.TEXT_PRESERVE_IMAGES)
                    originals = []; filtered_blocks=[]
                    for block in raw.get('blocks', ()):
                        filtered_lines=[]
                        for line in block.get('lines', ()):
                            spans=[]
                            for span in line.get('spans',()):
                                chars=[char for char in span.get('chars',()) if not any(p.Rect(item['bbox']).contains((p.Rect(char['bbox']).tl+p.Rect(char['bbox']).br)/2) for item in marginal)]
                                if chars:
                                    spans.append(dict(span,chars=chars))
                            if spans:
                                filtered_lines.append(dict(line,spans=spans))
                                originals.append(''.join(char['c'] for span in spans for char in span['chars']))
                        if filtered_lines:
                            filtered_blocks.append(dict(block,lines=filtered_lines))
                    filtered_raw=dict(raw,blocks=filtered_blocks)
                    table_count=sum(item['kind']=='table' for item in layout)
                    layout=preserve_table_regions(filtered_raw,layout,warnings,index,positioned=positioned)
                    if sum(item['kind']=='table' for item in layout)!=table_count:
                        # A downgraded table needs its source grid back. The
                        # earlier figure pass masked that grid as a native table.
                        content=[item for item in layout if item['kind']!='image']
                        layout=order_figures(content,page_figures(page,content,warnings))
                    margins=tuple(value*page_ratio for value in page_margins(layout,source_width,source_height))
                    width,height=source_width*page_ratio,source_height*page_ratio
                    layout=scaled_layout(layout,page_ratio)
                    start_type='nextPage' if index else 'continuous'
                    page_body,columns,end_type=write_layout(layout,width,height,margins,references,parts,links,image_links,start_type,cancel,positioned=positioned)
                    expected_text = ''.join(originals)
                    actual_text = ''.join(node.text or '' for node in page_body.iter(f'{{{W}}}t'))
                    if character_counts(expected_text) != character_counts(actual_text):
                        warnings.append(f'第 {index + 1} 页结构校验未通过，已按原位置保留全部识别文字。')
                        fallback=layout_from_raw(filtered_raw,[],positioned_areas=[(0,0,source_width,source_height)])
                        fallback=scaled_layout(order_figures(fallback,page_figures(page,fallback,warnings)),page_ratio)
                        page_body,columns,end_type=write_layout(fallback,width,height,margins,references,parts,links,image_links,start_type,cancel,positioned=True)
                        actual_text=''.join(node.text or '' for node in page_body.iter(f'{{{W}}}t'))
                        if character_counts(expected_text)!=character_counts(actual_text):
                            raise ValueError(f'第 {index+1} 页文字完整性校验失败，未提交 Word。')
                    check_cancel(cancel)
                    if len(page_body) and page_body[-1].tag==f'{{{W}}}tbl':
                        # Word requires a terminal paragraph after a table.
                        # Size its empty paragraph mark explicitly to avoid a blank page.
                        paragraph(page_body,size=1,after=0,line_height=1.3)
                    body.extend(page_body)
                    if not layout:
                        paragraph(body)
                    if index < doc.page_count - 1:
                        boundary = paragraph(body,size=1,after=0,line_height=1.3)
                        section(boundary.find(f'{{{W}}}pPr'),width,height,break_type=end_type,references=references,margins=margins,columns=columns)
                    else:
                        section(body,width,height,break_type=end_type,references=references,margins=margins,columns=columns)
                finally:
                    page.set_rotation(rotation)
        fd, temp = tempfile.mkstemp(prefix='.zhixu-ocr-word-', suffix='.docx', dir=target.parent)
        os.close(fd)
        content_types = ('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="png" ContentType="image/png"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
        for name in parts:
            if name.endswith('.xml'):
                role='header' if name.startswith('word/header') else 'footer'
                content_types=content_types.replace('</Types>',f'<Override PartName="/{name}" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.{role}+xml"/></Types>')
        relationships = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{}</Relationships>'
        style = ET.Element(f'{{{W}}}styles')
        defaults = element(style, 'docDefaults')
        run = element(element(defaults, 'rPrDefault'), 'rPr')
        element(run, 'rFonts', ascii='Calibri', hAnsi='Calibri', eastAsia='Microsoft YaHei')
        element(run, 'sz', val=22)
        element(run, 'szCs', val=22)
        with ZipFile(temp, 'w', compression=ZIP_DEFLATED) as archive:
            archive.writestr('[Content_Types].xml', content_types)
            archive.writestr('_rels/.rels', relationships.format(
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'))
            archive.writestr('word/document.xml', ET.tostring(document, encoding='utf-8', xml_declaration=True))
            archive.writestr('word/styles.xml', ET.tostring(style, encoding='utf-8', xml_declaration=True))
            archive.writestr('word/_rels/document.xml.rels', relationships.format(
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'+
                ''.join(f'<Relationship Id="{identity}" Type="{R}/{kind}" Target="{name}"/>' for identity,kind,name in links)))
            for name,data in parts.items():
                archive.writestr(name,data)
        validate_docx(temp)
        if stamp(source) != original:
            raise ValueError('源 PDF 在转换期间发生变化，未提交本次输出。')
        if source_guard and stamp(source_guard[0])!=source_guard[1]:
            raise ValueError('源 PDF 在识别或转换期间发生变化，未提交本次输出。')
        commit_file(temp, target, expected, cancel=cancel)
        temp = None
        message = '已按原页位置生成可编辑识别文字和表格；正文页保留段落结构，复杂版式请核对。'
        if warnings:
            message += ' ' + '；'.join(dict.fromkeys(warnings))
        return 'success', message, ''
    finally:
        if temp:
            Path(temp).unlink(missing_ok=True)
