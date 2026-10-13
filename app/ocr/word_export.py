"""Export an existing OCR text layer to editable DOCX without PDF reflow."""
from pathlib import Path
from collections import Counter
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


def paragraph(parent, text='', *, size=11, bold=False, after=4, before=0, first_line_indent=0, colour=None, line_height=None):
    node = element(parent, 'p')
    properties = element(node, 'pPr')
    spacing=element(properties, 'spacing', after=round(after*20), before=round(before*20))
    if line_height:
        spacing.set(f'{{{W}}}line',str(round(max(size*1.25,line_height)*20)))
        spacing.set(f'{{{W}}}lineRule','exact')
    element(properties, 'autoSpaceDE', val=0)
    element(properties, 'autoSpaceDN', val=0)
    if first_line_indent:
        element(properties,'ind',firstLine=round(first_line_indent*20))
    if not text:
        mark=element(properties,'rPr')
        element(mark,'sz',val=max(2,round(size*2)))
    for index, line in enumerate(text.split('\n')):
        run = element(node, 'r')
        run_properties = element(run, 'rPr')
        element(run_properties, 'sz', val=max(2, min(144, round(size*2))))
        element(run_properties, 'szCs', val=max(2, min(144, round(size*2))))
        if bold:
            element(run_properties, 'b')
        if colour:
            element(run_properties,'color',val=colour.lstrip('#'))
        if index:
            element(run, 'br')
        value = element(run, 't')
        value.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        value.text = line
    return node


def section(parent, width, height, *, break_type='continuous', references=(), margins=(36,36,36,36), columns=()):
    node = element(parent, 'sectPr')
    for role,identity in references:
        reference=element(node,role+'Reference',type='default')
        reference.set(f'{{{R}}}id',identity)
    element(node, 'type', val=break_type)
    element(node, 'pgSz', w=round(width * 20), h=round(height * 20))
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


def picture(parent,item,identity,index,available):
    width=item['bbox'][2]-item['bbox'][0]; height=item['bbox'][3]-item['bbox'][1]
    ratio=min(1,available/max(1,width))
    cx,cy=round(width*ratio*12700),round(height*ratio*12700)
    drawing=element(element(element(parent,'p'),'r'),'drawing')
    inline=ET.SubElement(drawing,f'{{{WP}}}inline',distT='0',distB='0',distL='0',distR='0')
    ET.SubElement(inline,f'{{{WP}}}extent',cx=str(cx),cy=str(cy))
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


def write_table(body, table, available=None):
    ratio=min(1,available/max(1,sum(table.col_widths))) if available is not None else 1
    node = element(body, 'tbl')
    properties = element(node, 'tblPr')
    element(properties, 'tblW', w=round(sum(table.col_widths)*ratio*20), type='dxa')
    element(properties, 'tblLayout', type='fixed')
    borders = element(properties, 'tblBorders')
    colour='606060' if any(cell.style.border_left or cell.style.border_top for cell in table.cells) else 'E5E7EB'
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        element(borders, edge, val='single', sz=4, color=colour)
    grid = element(node, 'tblGrid')
    for width in table.col_widths:
        element(grid, 'gridCol', w=max(100, round(width * ratio * 20)))
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
        if row < len(table.row_heights):
            element(tr_properties, 'trHeight', val=round(table.row_heights[row]*20), hRule='atLeast')
        col = 0
        while col < table.col_count:
            cell = coverage.get((row, col), cells.get((row, col)))
            span = min(cell.col_span, table.col_count - col) if cell else 1
            tc = element(tr, 'tc')
            props = element(tc, 'tcPr')
            if span > 1:
                element(props, 'gridSpan', val=span)
            continuation = cell is not None and cell.row < row
            if cell and cell.row_span > 1:
                element(props, 'vMerge', **({} if continuation else {'val': 'restart'}))
            if cell and cell.style.fill_color:
                element(props,'shd',val='clear',fill=cell.style.fill_color.lstrip('#'))
            element(props, 'vAlign', val='center')
            paragraph(tc, '' if continuation or cell is None else cell.text,
                      size=cell.style.font_size or 11 if cell else 11,
                      after=0,
                      bold=(row == 0 or cell.style.bold) if cell else row == 0,
                      colour=cell.style.font_color if cell else None,
                      line_height=(cell.style.font_size or 11)*1.35 if cell else None)
            col += span


def write_layout(layout, width, height, margins, references, parts, links, image_links, start_type, cancel=None):
    body=ET.Element(f'{{{W}}}body')
    available=max(1,width-margins[0]-margins[2])
    current_columns=(); current_column=0
    for offset,item in enumerate(layout):
        check_cancel(cancel)
        columns=column_settings(item,available) if item.get('column_count',1)>1 else ()
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
            write_table(body,item['table'],item_width)
        elif item['kind']=='image':
            if id(item) not in image_links:
                count=1+len(image_links)
                identity=f'rIdImage{count}'; name=f'media/image-{count}.png'
                parts['word/'+name]=item['data']; links.append((identity,'image',name))
                image_links[id(item)]=(identity,count)
            identity,count=image_links[id(item)]
            picture(body,item,identity,count,item_width)
        else:
            following=layout[offset+1] if offset+1<len(layout) else None
            gap=following['bbox'][1]-item['bbox'][3] if following and following.get('column',-1)==item.get('column',-1) else 4
            before=max(0,min(60,item['bbox'][1]-layout[offset-1]['bbox'][3])) if offset and layout[offset-1]['kind']=='table' else 0
            paragraph(body,item['text'],size=item['size'],bold=item['bold'],
                      after=max(4,min(40,gap)),before=before,first_line_indent=item.get('first_line_indent',0),
                      line_height=(item['bbox'][3]-item['bbox'][1])/item.get('line_count',1))
    return body,current_columns,start_type


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
            pages=document_layout(doc,include_images=True,cancel=cancel,progress=progress)
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
                    all_items=record['items']
                    layout=[item for item in all_items if not item.get('role')]
                    warnings.extend(record['warnings'])
                    references=[]
                    marginal=[item for item in all_items if item.get('role')]
                    for role,root_name in (('header','hdr'),('footer','ftr')):
                        entries=[item for item in marginal if item['role']==role]
                        if role not in margin_roles:
                            continue
                        root=ET.Element(f'{{{W}}}{root_name}')
                        if not entries:
                            paragraph(root,after=0)
                        for item in entries:
                            node=paragraph(root,item['text'],size=item['size'],bold=item['bold'])
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
                    width,height=page.rect.width,page.rect.height
                    margins=page_margins(layout,width,height)
                    start_type='nextPage' if index else 'continuous'
                    page_body,columns,end_type=write_layout(layout,width,height,margins,references,parts,links,image_links,start_type,cancel)
                    expected_text = ''.join(originals)
                    actual_text = ''.join(node.text or '' for node in page_body.iter(f'{{{W}}}t'))
                    if Counter(char for char in expected_text if not char.isspace() and char!='\u00ad') != Counter(char for char in actual_text if not char.isspace() and char!='\u00ad'):
                        # Report a table-structure limitation while preserving every OCR character.
                        warnings.append(f'第 {index + 1} 页表格结构无法完整还原，已按正文保留全部识别文字。')
                        fallback=layout_from_raw({'blocks':filtered_blocks},[])
                        # Keep figures even when a table cannot carry every character.
                        fallback=order_figures(fallback,[item for item in layout if item['kind']=='image'])
                        page_body,columns,end_type=write_layout(fallback,width,height,margins,references,parts,links,image_links,start_type,cancel)
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
        message = '已使用 OCR 文字层生成可编辑正文和识别到的表格；图片及复杂版式请核对。'
        if warnings:
            message += ' ' + '；'.join(dict.fromkeys(warnings))
        return 'success', message, ''
    finally:
        if temp:
            Path(temp).unlink(missing_ok=True)
