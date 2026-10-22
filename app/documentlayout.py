"""One geometry-based reading layout shared by text copy and document exports."""
from dataclasses import replace
from collections import Counter
from statistics import median
import re
import math


def _column_cuts(fragments):
    body=[item for item in fragments if not item.get('heading') and not item.get('positioned')]
    if len(body)<6:
        return [], None
    # Short aligned numeric fields remain rows rather than newspaper columns.
    if sum(len(item['text'])<13 and any(char.isdigit() for char in item['text']) for item in body)>len(body)*.5:
        return [], None
    size=median(item['size'] for item in body)
    candidates=[]
    for end in sorted({item['bbox'][2] for item in body}):
        starts=[item['bbox'][0] for item in body if item['bbox'][0]>=end+max(15,size*1.2)]
        if not starts:
            continue
        start=min(starts)
        left=[item for item in body if item['bbox'][2]<=end+1]
        right=[item for item in body if item['bbox'][0]>=start-1]
        if min(len(left),len(right))<3:
            continue
        top=max(min(item['bbox'][1] for item in left),min(item['bbox'][1] for item in right))
        bottom=min(max(item['bbox'][3] for item in left),max(item['bbox'][3] for item in right))
        overlap=bottom-top
        extent=min(max(item['bbox'][3] for item in left)-min(item['bbox'][1] for item in left),
                   max(item['bbox'][3] for item in right)-min(item['bbox'][1] for item in right))
        crossing=sum(min(item['bbox'][3],bottom)-max(item['bbox'][1],top) for item in body
                     if item['bbox'][0]<(end+start)/2<item['bbox'][2] and item['bbox'][1]<bottom and item['bbox'][3]>top)
        if overlap>0 and overlap>=extent*.5 and crossing<=overlap*.25:
            candidates.append((start-end,(start+end)/2,top,bottom))
    chosen=[]
    for candidate in sorted(candidates,reverse=True):
        if all(abs(candidate[1]-other[1])>size*3 for other in chosen):
            chosen.append(candidate)
        if len(chosen)==3:
            break
    return sorted(item[1] for item in chosen), ((min(item[2] for item in chosen),max(item[3] for item in chosen)) if chosen else None)


def _reading_order(items, cuts, region):
    if not cuts:
        return sorted(items,key=lambda item:(item['bbox'][1],item['bbox'][0]))
    spanning=[]
    columns=[]
    for item in items:
        box=item['bbox']
        if box[3]<region[0] or box[1]>region[1] or any(box[0]<cut<box[2] for cut in cuts):
            item.pop('column',None)
            spanning.append(item)
        else:
            item['column']=sum(box[0]>=cut for cut in cuts)
            item['column_count']=len(cuts)+1
            columns.append(item)
    bounds=[]
    for column in range(len(cuts)+1):
        entries=[item['bbox'] for item in columns if item['column']==column]
        bounds.append((min(box[0] for box in entries),max(box[2] for box in entries)) if entries else (0,0))
    for item in columns:
        item['column_bounds']=tuple(bounds)
    result=[]
    for barrier in sorted(spanning,key=lambda item:item['bbox'][1]):
        above=[item for item in columns if item['bbox'][1]<barrier['bbox'][1]]
        result.extend(sorted(above,key=lambda item:(item['column'],item['bbox'][1],item['bbox'][0])))
        columns=[item for item in columns if item not in above]
        result.append(barrier)
    result.extend(sorted(columns,key=lambda item:(item['column'],item['bbox'][1],item['bbox'][0])))
    return result


def _cjk(char):
    return '\u3400'<=char<='\u9fff' or '\uf900'<=char<='\ufaff'


def _paragraphs(items):
    text_items=[item for item in items if item['kind']=='text']
    margins={}
    for column in {item.get('column',-1) for item in text_items}:
        entries=[item for item in text_items if item.get('column',-1)==column and not item.get('heading')]
        if entries:
            rights=sorted(item['bbox'][2] for item in entries)
            margins[column]=(min(item['bbox'][0] for item in entries),rights[min(len(rights)-1,int(len(rights)*.85))])
    output=[]
    for item in items:
        if item['kind']!='text':
            output.append(item)
            continue
        current=dict(item)
        current['source_text']=item['text']
        current['last_line_box']=item['bbox']
        current['line_count']=1
        left,right=margins.get(item.get('column',-1),(item['bbox'][0],item['bbox'][2]))
        current['first_line_indent']=max(0,item['bbox'][0]-left) if item['bbox'][0]-left>=item['size']*1.3 else 0
        listed=bool(re.match(r'^(?:[•*]\s|[-]\s|\d+[.)、]\s*|[一二三四五六七八九十]+[、])',item['text']))
        previous=output[-1] if output else None
        if previous and previous['kind']=='text' and not listed and not previous.get('heading') and not current.get('heading') and not previous.get('positioned') and not current.get('positioned'):
            box=previous['last_line_box']; gap=item['bbox'][1]-box[3]
            width=max(1,right-left)
            near_right=right-box[2]<=max(item['size']*1.3,width*.08)
            same_column=previous.get('column',-1)==item.get('column',-1)
            normal_style=max(previous['size'],item['size'])<=min(previous['size'],item['size'])*1.2 and previous['bold']==item['bold'] and abs(item.get('angle',0))<3 and abs(previous.get('angle',0))<3
            normal_gap=-item['size']*.2<=gap<=item['size']*.9
            continuation=item['bbox'][0]-left<item['size']*.8
            punctuation=previous['text'].rstrip()[-1:] in '。！？!?；;.'
            clear_stop=punctuation and right-box[2]>item['size']*.6
            if same_column and normal_style and normal_gap and continuation and near_right and not clear_stop and '\t' not in previous['text']+current['text']:
                tail=previous['text'].rstrip(); head=current['text'].lstrip()
                if tail.endswith('\u00ad'):
                    tail=tail[:-1]; separator=''
                else:
                    separator='' if tail.endswith('-') or (_cjk(tail[-1]) and _cjk(head[0])) else ' '
                previous['text']=tail+separator+head
                previous['source_text']+='\n'+current['source_text']
                previous['last_line_box']=item['bbox']
                previous['bbox']=(min(previous['bbox'][0],item['bbox'][0]),previous['bbox'][1],max(previous['bbox'][2],item['bbox'][2]),item['bbox'][3])
                previous['line_count']+=1
                continue
        output.append(current)
    return output


def canonical_tables(page, tables):
    import pymupdf as p
    from app.pdf2excel.detector import _build_grid, _detect_merges
    if page.rotation == 0:
        return tables
    output = []
    for table in tables:
        bbox = tuple(p.Rect(table.bbox) * page.derotation_matrix)
        rectangles = [tuple(p.Rect(cell.bbox) * page.derotation_matrix) for cell in table.cells]
        xs, ys = _build_grid(rectangles, bbox)
        cells = []
        for cell, rectangle in zip(table.cells, rectangles):
            row, col, rows, cols = _detect_merges(rectangle, xs, ys)
            cells.append(replace(cell, row=row, col=col, row_span=rows, col_span=cols, bbox=rectangle))
        output.append(replace(table, bbox=bbox, cells=cells, row_count=len(ys)-1, col_count=len(xs)-1,
                              col_widths=[b-a for a,b in zip(xs,xs[1:])], row_heights=[b-a for a,b in zip(ys,ys[1:])]))
    return output


def _positioned_line_parts(chars, sizes, direction):
    """Keep distant fields in separate editable frames, with their own coordinates."""
    if abs(direction[1]) > .1 or direction[0] <= 0:
        return [(chars,sizes)]
    groups=[]; current=[]; current_sizes=[]
    for char,size in zip(chars,sizes):
        box=char['bbox']
        wide_space=char['c'].isspace() and box[2]-box[0]>size*.8
        separated=current and box[0]-current[-1]['bbox'][2]>min(box[3]-box[1],current[-1]['bbox'][3]-current[-1]['bbox'][1])*.65
        if (wide_space or separated) and current:
            groups.append((current,current_sizes)); current=[]; current_sizes=[]
        if not wide_space:
            current.append(char); current_sizes.append(size)
    if current:
        groups.append((current,current_sizes))
    return groups


def layout_from_raw(raw, tables, *, positioned_areas=()):
    import pymupdf as p
    from app.pdf2excel.analyzer import text_rows
    areas = [p.Rect(table.bbox) for table in tables]
    positions = [p.Rect(area) for area in positioned_areas]
    fragments = []
    for block in raw.get('blocks', ()):
        for line in block.get('lines', ()):
            chars, sizes = [], []
            for span in line.get('spans', ()):
                for char in span.get('chars', ()):
                    rectangle = p.Rect(char['bbox'])
                    if not any(area.contains((rectangle.tl + rectangle.br) / 2) for area in areas):
                        chars.append(char)
                        sizes.append(span.get('size', 11))
            if not chars:
                continue
            direction=line.get('dir',(1,0))
            line_box=p.Rect(chars[0]['bbox'])
            for char in chars[1:]:
                line_box |= p.Rect(char['bbox'])
            parts=_positioned_line_parts(chars,sizes,direction) if any(area.intersects(line_box) for area in positions) else [(chars,sizes)]
            for part,part_sizes in parts:
                text=''.join(char['c'] for char in part).replace('\xa0', ' ')
                if not text.strip():
                    continue
                bounds = p.Rect(part[0]['bbox'])
                for char in part[1:]:
                    bounds |= p.Rect(char['bbox'])
                hidden = all(span.get('alpha', 255) == 0 for span in line.get('spans', ()))
                size = min(median(part_sizes), bounds.height / 1.3) if hidden else median(part_sizes)
                fragments.append(dict(kind='text', text=text, bbox=tuple(bounds), size=size,angle=math.degrees(math.atan2(direction[1],direction[0])),
                                      positioned=any(area.contains((bounds.tl+bounds.br)/2) for area in positions),
                                      bold=any(span.get('flags', 0) & 16 for span in line.get('spans', ()))))
    body_size=median([item['size'] for item in fragments]) if fragments else 11
    for item in fragments:
        item['heading']=item['size']>=body_size*1.3 and len(item['text'])<=100
    cuts,region=_column_cuts(fragments)
    for item in fragments:
        item['column']=sum(item['bbox'][0]>=cut for cut in cuts) if cuts else -1
    # Rejoin fragments within the same column, never across a detected gutter.
    rows = []
    rows.extend(dict(item) for item in fragments if item['positioned'])
    for column in sorted({item['column'] for item in fragments}):
        for group in text_rows([item for item in fragments if item['column']==column and not item['positioned']]):
            previous = dict(group[0])
            for fragment in group[1:]:
                box = p.Rect(fragment['bbox'])
                gap = box.x0 - previous['bbox'][2]
                separator = '\t' if gap > max(previous['size'], fragment['size']) * 2 else (' ' if gap > fragment['size'] * .2 else '')
                previous['text'] += separator + fragment['text']
                previous['bbox'] = tuple(p.Rect(previous['bbox']) | box)
                previous['size'] = max(previous['size'], fragment['size'])
            rows.append(previous)
    body_size = median([item['size'] for item in rows]) if rows else 11
    for item in rows:
        item['heading'] = item['size'] >= body_size * 1.3 and len(item['text']) <= 100
        item['bold'] |= item['heading']
    ordered=_reading_order([*rows,*[dict(kind='table',table=table,bbox=table.bbox) for table in tables]],cuts,region)
    return _paragraphs(ordered)


def order_figures(layout,figures):
    column_items=[item for item in layout if item.get('column_count',1)>1]
    if column_items:
        bounds=column_items[0]['column_bounds']
        cuts=[(left[1]+right[0])/2 for left,right in zip(bounds,bounds[1:])]
        region=(min(item['bbox'][1] for item in column_items),max(item['bbox'][3] for item in column_items))
        return _reading_order([*layout,*figures],cuts,region)
    return sorted([*layout,*figures],key=lambda item:(item['bbox'][1],item['bbox'][0]))


def _remove_page_rotation(page):
    """Bake the visible orientation into a private export document, retaining its crop."""
    import pymupdf as p
    if not page.rotation:
        return
    crop=p.Rect(page.cropbox); media=page.mediabox
    full=p.Rect(media.x0,0,media.x1,media.height)
    # remove_rotation uses the media box. Expand temporarily so a rotated
    # cropped page is not shifted or silently expanded to the whole media box.
    page.set_cropbox(full)
    clipped=crop*p.Matrix(1,0,0,1,-full.x0,-full.y0)*page.rotation_matrix
    page.remove_rotation()
    new_full=page.cropbox
    page.set_cropbox(clipped*p.Matrix(1,0,0,1,new_full.x0,new_full.y0))


def prepare_export_page(page):
    """Use one upright coordinate space for text, grids, images and paper size."""
    import pymupdf as p
    _remove_page_rotation(page)
    votes=Counter(); lines=0
    raw=page.get_text('dict',flags=p.TEXTFLAGS_DICT & ~p.TEXT_PRESERVE_IMAGES)
    for block in raw.get('blocks',()):
        for line in block.get('lines',()):
            text=''.join(span.get('text','') for span in line.get('spans',())).strip()
            # Genuine vertical writing and isolated rotated headings do not
            # define the page's reading axis. Use a strong body-text consensus.
            if line.get('wmode',0)==1 or len(text)<3:
                continue
            dx,dy=line.get('dir',(1,0))
            if max(abs(dx),abs(dy))<.9:
                continue
            angle=round(math.degrees(math.atan2(dy,dx))/90)*90%360
            votes[angle]+=min(40,len(text)); lines+=1
    if lines>=3 and sum(votes.values())>=20:
        angle,weight=votes.most_common(1)[0]
        if angle and weight>=sum(votes.values())*.8:
            page.set_rotation((-angle)%360)
            _remove_page_rotation(page)


def _raster_colours(page,layout):
    """Carry a uniform scanned background into editable elements, not white holes."""
    import pymupdf as p
    if not page.get_images():
        return
    scale=min(1.5,1800/max(page.rect.width,page.rect.height))
    matrix=p.Matrix(scale,scale)
    pix=page.get_pixmap(matrix=matrix,colorspace=p.csRGB,alpha=False,annots=False)
    samples=pix.samples
    def colours(rectangle):
        box=p.Rect(rectangle)*page.rotation_matrix*matrix
        ring=[]
        for y in range(7):
            for x in range(7):
                px=min(pix.width-1,max(0,round(box.x0+box.width*(.03+.94*x/6)-pix.x)))
                py=min(pix.height-1,max(0,round(box.y0+box.height*(.03+.94*y/6)-pix.y)))
                offset=(py*pix.width+px)*3
                value=tuple(samples[offset:offset+3])
                if x in (0,6) or y in (0,6):
                    ring.append(value)
        key,count=Counter(tuple(value//16 for value in rgb) for rgb in ring).most_common(1)[0]
        if count<len(ring)*.55:
            return None,None
        matching=[rgb for rgb in ring if tuple(value//16 for value in rgb)==key]
        rgb=tuple(round(sum(value[channel] for value in matching)/len(matching)) for channel in range(3))
        if min(rgb)>240 and max(rgb)-min(rgb)<6:
            return None,None
        fill='#'+''.join(f'{value:02X}' for value in rgb)
        light=sum(weight*value for weight,value in zip((.299,.587,.114),rgb))
        foreground=None
        if light<150:
            # A sparse grid can miss thin white glyphs. Inspect the inner
            # pixels only for a confirmed dark uniform background.
            x0=max(0,math.ceil(box.x0-pix.x)+1); x1=min(pix.width,math.floor(box.x1-pix.x)-1)
            y0=max(0,math.ceil(box.y0-pix.y)+1); y1=min(pix.height,math.floor(box.y1-pix.y)-1)
            step=max(1,min(x1-x0,y1-y0)//40); whites=0
            for y in range(y0,y1,step):
                for x in range(x0,x1,step):
                    offset=(y*pix.width+x)*3
                    whites+=min(samples[offset:offset+3])>230
                    if whites>=3:
                        foreground='#FFFFFF'; break
                if foreground:
                    break
        return fill,foreground
    for item in layout:
        if item['kind']=='table':
            table=item['table']; cells=[]
            for cell in table.cells:
                fill,foreground=colours(cell.bbox)
                style=replace(cell.style,fill_color=fill or cell.style.fill_color,
                              font_color=foreground or cell.style.font_color)
                cells.append(replace(cell,style=style))
            item['table']=replace(table,cells=cells)
        elif item['kind']=='text':
            fill,foreground=colours(item['bbox'])
            if fill:
                item['fill_colour']=fill
            if foreground:
                item['colour']=foreground


def page_layout(page, *, include_images=False, preserve_positions=False, spatial=False):
    import pymupdf as p
    from app.pdf2excel.analyzer import extract_spans, extract_drawings
    from app.pdf2excel.detector import detect_tables
    warnings = []
    lines, fills = extract_drawings(page)
    tables = canonical_tables(page, detect_tables(page, extract_spans(page), lines, fills, warnings))
    raw = page.get_text('rawdict', flags=p.TEXTFLAGS_RAWDICT & ~p.TEXT_PRESERVE_IMAGES)
    verified=[]; unverified=[]
    for table in tables:
        area=p.Rect(table.bbox)
        source=''.join(char['c'] for block in raw.get('blocks',()) for line in block.get('lines',())
                       for span in line.get('spans',()) for char in span.get('chars',())
                       if area.contains((p.Rect(char['bbox']).tl+p.Rect(char['bbox']).br)/2))
        exported=''.join(cell.text for cell in table.cells)
        counts=lambda text: Counter(char for char in text if not char.isspace() and char!='\u00ad')
        if counts(source)==counts(exported):
            verified.append(table)
        else:
            unverified.append(table.bbox)
    tables=verified
    if unverified:
        warnings.append(f'第 {page.number+1} 页部分网格未能完整承载原文，已按原位置保留全部识别文字，请核对表格。')
    hidden=any(span.get('alpha',255)==0 and any(char['c'].isalnum() for char in span.get('chars',()))
               for block in raw.get('blocks',()) for line in block.get('lines',()) for span in line.get('spans',()))
    page_area=page.cropbox.width*page.cropbox.height
    scan_area=sum(p.Rect(image['bbox']).get_area() for image in page.get_image_info())
    fixed=spatial or preserve_positions and (bool(tables) or bool(unverified) or hidden and scan_area>=page_area*.65)
    positions=[(0,0,page.cropbox.width,page.cropbox.height)] if fixed else unverified
    layout=layout_from_raw(raw,tables,positioned_areas=positions)
    _raster_colours(page,layout)
    if any(abs(item.get('angle',0))>=3 for item in layout):
        warnings.append('存在倾斜或竖排文字，已保留文字内容，段落方向及图像位置请核对。')
    if include_images:
        from app.documentimages import page_figures
        figures=page_figures(page,layout,warnings)
        if figures:
            layout=order_figures(layout,figures)
    return layout,warnings


def document_layout(doc, *, include_images=False, preserve_positions=False, normalize_orientation=False, cancel=None, progress=None):
    from app.output import check_cancel
    pages=[]; repeats={}
    for page in doc:
        check_cancel(cancel)
        if progress:
            progress(f'正在分析排版 · 第 {len(pages)+1}/{len(doc)} 页')
        if normalize_orientation:
            prepare_export_page(page)
        items,warnings=page_layout(page,include_images=include_images,preserve_positions=preserve_positions)
        width,height=page.cropbox.width,page.cropbox.height
        positioned=preserve_positions and any(item.get('positioned') or item['kind']=='table' for item in items)
        pages.append(dict(items=items,warnings=warnings,width=width,height=height,positioned=positioned))
        for index,item in enumerate(items):
            if item['kind']!='text' or len(item['text'])>100:
                continue
            box=item['bbox']; position='header' if box[3]<=height*.09 else ('footer' if box[1]>=height*.9 else None)
            if not position:
                continue
            other=[entry for entry in items if entry is not item and entry['kind'] in ('text','table')]
            gap=min((entry['bbox'][1]-box[3] for entry in other if entry['bbox'][1]>=box[3]),default=0) if position=='header' else min((box[1]-entry['bbox'][3] for entry in other if entry['bbox'][3]<=box[1]),default=0)
            if gap<item['size']*1.2:
                continue
            key=(position,re.sub(r'\d+','#',re.sub(r'\s+','',item['text']).casefold()))
            repeats.setdefault(key,[]).append((len(pages)-1,index))
    if len(pages)>=2:
        for (role,key),entries in repeats.items():
            if len({page for page,index in entries})>=max(2,math.ceil(len(pages)*.5)):
                for page,index in entries:
                    pages[page]['items'][index]['role']=role
    return pages


def layout_text(layout, rectangle=None):
    import pymupdf as p
    area = p.Rect(rectangle) if rectangle is not None else None
    result = []
    for item in layout:
        if item['kind'] == 'text':
            if area is None or area.intersects(p.Rect(item['bbox'])):
                result.append(item['text'])
            continue
        if item['kind']!='table':
            continue
        table = item['table']
        cells = {(cell.row, cell.col): cell for cell in table.cells}
        selected = [cell for cell in table.cells if area is None or (cell.text.strip() and area.intersects(p.Rect(cell.bbox)))]
        if not selected:
            continue
        first, last = min(cell.col for cell in selected), max(cell.col+cell.col_span-1 for cell in selected)
        rows = sorted({cell.row for cell in selected})
        for row in rows:
            result.append('\t'.join(cells[row,col].text.replace('\n', ' ') if (row,col) in cells and cells[row,col] in selected else ''
                                    for col in range(first,last+1)))
    return '\n'.join(result)


def selection_text(raw, rectangle, tables):
    import pymupdf as p
    area = p.Rect(rectangle)
    blocks = []
    for block in raw.get('blocks', ()):
        if block.get('bbox') is not None and not area.intersects(p.Rect(block['bbox'])):
            continue
        lines = []
        for line in block.get('lines', ()):
            if line.get('bbox') is not None and not area.intersects(p.Rect(line['bbox'])):
                continue
            spans = []
            for span in line.get('spans', ()):
                chars = [char for char in span.get('chars', ())
                         if area.contains((p.Rect(char['bbox']).tl+p.Rect(char['bbox']).br)/2)]
                if chars:
                    spans.append(dict(span, chars=chars))
            if spans:
                lines.append(dict(line, spans=spans))
        if lines:
            blocks.append(dict(block, lines=lines))
    selected_raw = {'blocks': blocks}
    clipped = []
    for table in tables:
        cells = []
        for cell in table.cells:
            pieces = []
            box = p.Rect(cell.bbox)
            for block in blocks:
                for line in block['lines']:
                    text = ''.join(char['c'] for span in line['spans'] for char in span['chars']
                                   if box.contains((p.Rect(char['bbox']).tl+p.Rect(char['bbox']).br)/2))
                    if text.strip():
                        pieces.append(text)
            cells.append(replace(cell, text=' '.join(pieces)))
        clipped.append(replace(table, cells=cells))
    return layout_text(layout_from_raw(selected_raw, clipped), area)


def pdf_to_text(source, destination, progress=None, cancel=None):
    import os
    from pathlib import Path
    import tempfile
    import pymupdf as p
    from app.output import check_cancel, commit_file, stamp
    from app.wordconvert import validate_pdf_for_word
    validate_pdf_for_word(source)
    target = Path(destination).resolve()
    if target.exists():
        raise ValueError('TXT 输出已存在，请选择新文件名。')
    if target.suffix.lower() != '.txt':
        raise ValueError('输出文件必须使用 .txt 扩展名。')
    original = stamp(source)
    temp = None
    try:
        fd, temp = tempfile.mkstemp(prefix='.zhixu-text-', suffix='.txt', dir=target.parent)
        with os.fdopen(fd, 'w', encoding='utf-8-sig', newline='') as stream, p.open(source) as doc:
            for index,page in enumerate(doc):
                check_cancel(cancel)
                if not page.get_text().strip() and page.get_images():
                    raise ValueError(f'第 {index+1} 页需要先进行 OCR。')
                layout, _ = page_layout(page)
                if index:
                    stream.write('\n\f\n')
                stream.write(layout_text(layout))
                if progress:
                    progress(index+1, len(doc), f'正在导出第 {index+1} 页文字')
        if stamp(source) != original:
            raise ValueError('源 PDF 在导出期间改变，未提交结果。')
        commit_file(temp, target, None, cancel=cancel)
        temp = None
        return str(target)
    finally:
        if temp:
            Path(temp).unlink(missing_ok=True)
