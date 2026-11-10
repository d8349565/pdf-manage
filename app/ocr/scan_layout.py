"""Recover scan colours and geometry without replacing editable content by a page image."""
from collections import Counter
from dataclasses import replace
from functools import lru_cache
from io import BytesIO
import math
from statistics import median

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat, ImageFilter


@lru_cache(maxsize=12)
def scan_font(bold=False,family='Microsoft YaHei'):
    import pymupdf as p
    from app.systemfonts import installed_font_buffer
    data=installed_font_buffer(family,16 if bold else 0)
    return p.Font(fontbuffer=data) if data else p.Font('cjk')


@lru_cache(maxsize=12)
def mask_font(family,bold):
    return ImageFont.truetype(BytesIO(scan_font(bold,family).buffer),64)


def match_scan_font(text,ink_mask,heading=False):
    """Choose among installed font families by glyph shape, independent of document type."""
    cjk=any('\u3400'<=char<='\u9fff' for char in text)
    families=('Microsoft YaHei','SimSun') if cjk else ('Arial','Times New Roman','Courier New')
    default=(families[0],False)
    box=ink_mask.getbbox()
    if not box or len(text.strip())<2:
        return default
    target=ink_mask.crop(box)
    target=target.resize((max(1,min(900,round(target.width*48/target.height))),48))
    mass=ImageStat.Stat(target).mean[0]
    scores={}
    for family in families:
        for bold in (False,True):
            glyph=mask_font(family,bold).getmask(text)
            if not glyph.getbbox():continue
            expected=Image.frombytes('L',glyph.size,bytes(glyph)).crop(glyph.getbbox()).resize(target.size)
            overlap=ImageStat.Stat(ImageChops.multiply(target,expected)).mean[0]
            score=2*overlap/max(1,mass+ImageStat.Stat(expected).mean[0])
            scores[family,bold]=score
    if not scores or max(scores.values())<.45:
        return default
    family=max(families,key=lambda name:scores.get((name,False),0))
    # Establish the family from regular outlines first: a heavier unrelated
    # face otherwise wins merely by covering more of the scan's blurred ink.
    # Heading hierarchy alone is not evidence that the source text was bold.
    bold_score=scores.get((family,True),0)
    bold=bold_score>=.75 and bold_score>scores.get((family,False),0)+.12
    return family,bold


def restore_scan(page, layout, warnings):
    from bisect import bisect_right
    from itertools import groupby
    import pymupdf as p
    from app.documentimages import export_image_scale, _regions
    from app.documentlayout import table_text_runs
    from app.pdf2excel.analyzer import text_rows
    from app.pdf2excel.raster_grid import raster_grid_lines
    scale=export_image_scale(page.rect.width,page.rect.height,3)
    pix=page.get_pixmap(matrix=p.Matrix(scale,scale),colorspace=p.csRGB,alpha=False,annots=False)
    original=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
    background=original.copy()
    r,g,b=original.split()
    high=ImageChops.lighter(ImageChops.lighter(r,g),b)
    low=ImageChops.darker(ImageChops.darker(r,g),b)
    chromatic=ImageChops.subtract(high,low).point(lambda v:255 if v>35 else 0)
    small=chromatic.resize((max(1,round(pix.width/scale)),max(1,round(pix.height/scale))))
    tables=[p.Rect(item['bbox']) for item in layout if item['kind']=='table']
    # Compact sparse coloured artwork may overlap an OCR line. Preserve that
    # complete local line from the image rather than guessing which letters are
    # stamp lettering. Filled panels and measured table grids are excluded.
    def graphic_extent(box):
        mask=small.crop(box).point(lambda value:255 if value>=64 else 0)
        # A thin accent joined to a heading has a large bounding box, but its
        # colour does not span both dimensions like an actual compact graphic.
        wide_rows=0
        for y in range(mask.height):
            ink=mask.crop((0,y,mask.width,y+1)).getbbox()
            if ink and ink[2]-ink[0]>=mask.width*.5:
                wide_rows+=1
        return wide_rows>=mask.height*.4

    marks=[p.Rect(box) for box in _regions(small.tobytes(),small.width,small.height)
           if 30<box[2]-box[0]<page.rect.width*.4 and 30<box[3]-box[1]<page.rect.height*.4
           and .45<(box[2]-box[0])/(box[3]-box[1])<2.2
           and ImageStat.Stat(small.crop(box)).mean[0]<90
           and graphic_extent(box)
           and not any((p.Rect(box)&table).get_area()>=table.get_area()*.3 for table in tables)]

    def pixels(box, expansion=0):
        return (max(0,math.floor((box[0]-expansion)*scale)),max(0,math.floor((box[1]-expansion)*scale)),
                min(pix.width,math.ceil((box[2]+expansion)*scale)),min(pix.height,math.ceil((box[3]+expansion)*scale)))

    protected=Image.new('L',original.size)
    protect=ImageDraw.Draw(protected)
    for mark in marks:protect.rectangle(pixels(mark,1),fill=255)
    rules=Image.new('L',original.size);rule_draw=ImageDraw.Draw(rules)
    grid_lines=raster_grid_lines(page)
    for start,end in grid_lines:
        rule_draw.line((start[0]*scale,start[1]*scale,end[0]*scale,end[1]*scale),fill=255,width=max(2,round(scale*2)))
    keep=ImageChops.lighter(protected,rules)
    raw=page.get_text('rawdict',flags=(p.TEXTFLAGS_RAWDICT & ~p.TEXT_PRESERVE_IMAGES)|p.TEXT_INHIBIT_SPACES)
    line_boxes=[p.Rect(line['bbox']) for block in raw.get('blocks',()) for line in block.get('lines',())]

    def common(values):
        bins=Counter(tuple(c//16 for c in value) for value in values)
        if not bins:
            return (255,255,255)
        key=bins.most_common(1)[0][0]
        matches=[v for v in values if tuple(c//16 for c in v)==key]
        return tuple(round(sum(v[c] for v in matches)/len(matches)) for c in range(3))

    def paper(box, inside=False):
        x0,y0,x1,y1=pixels(box,-2 if inside else 1.5)
        if x1<=x0 or y1<=y0:
            return (255,255,255)
        # Sample just outside glyph bounds, rather than the white strokes of a title.
        pts=[(x,y) for x in range(x0,x1,max(1,(x1-x0)//80)) for y in (y0,y1-1)]
        pts += [(x,y) for y in range(y0,y1,max(1,(y1-y0)//30)) for x in (x0,x1-1)]
        return common([original.getpixel(point) for point in pts])

    def different_pixels(first, second):
        red,green,blue=ImageChops.difference(first,second).split()
        return ImageChops.lighter(ImageChops.lighter(red,green),blue).point(lambda value:255 if value>32 else 0)

    def table_clear_mask(table, cell_runs, bounds, paper_colour):
        area=p.Rect(table.bbox)+(-2,-2,2,2)
        if any(mark.intersects(area) for mark in marks):
            return None
        if sum(cell.row_span*cell.col_span for cell in table.cells if cell.bbox)!=table.row_count*table.col_count:
            return None
        left,top,right,bottom=bounds
        expected=Image.new('RGB',(right-left,bottom-top),paper_colour)
        replaceable=Image.new('L',expected.size)
        draw=ImageDraw.Draw(replaceable)
        cell_regions=[]
        for cell in table.cells:
            runs=cell_runs.get((cell.row,cell.col),())
            if any(run.get('image_text') for run in runs):
                return None
            if not cell.bbox:
                continue
            x0,y0,x1,y1=pixels(cell.bbox)
            expected.paste(cell.style.fill_color,(x0-left,y0-top,x1-left,y1-top))
            # Inferred borders can be a few pixels from the colour transition.
            # Allow a matching strip of fill, while keeping isolated ink of
            # the same colour outside the cell.
            nearby=pixels(cell.bbox,2)
            cell_regions.append((nearby[0]-left,nearby[1]-top,nearby[2]-left,nearby[3]-top))
            strips=((nearby[0],y0,x0,y1),(x1,y0,nearby[2],y1),
                    (x0,nearby[1],x1,y0),(x0,y1,x1,nearby[3]))
            for sx0,sy0,sx1,sy1 in strips:
                if sx0>=sx1 or sy0>=sy1:
                    continue
                crop=background.crop((sx0,sy0,sx1,sy1))
                same_fill=ImageChops.invert(different_pixels(crop,Image.new('RGB',crop.size,cell.style.fill_color)))
                if same_fill.histogram()[255]>=.75*crop.width*crop.height:
                    replaceable.paste(255,(sx0-left,sy0-top),same_fill)
            x0,y0,x1,y1=(round(value*scale)-offset for value,offset in zip(cell.bbox,(left,top,left,top)))
            edges=(('left',(x0,y0,x0,y1)),('top',(x0,y0,x1,y0)),
                   ('right',(x1,y0,x1,y1)),('bottom',(x0,y1,x1,y1)))
            for edge,line in edges:
                # Allow a thin band around the reconstructed rule, or a
                # single pixel at the seam between adjoining cell fills.
                width=2*max(1,round(scale))+1 if getattr(cell.style,'border_'+edge) else 1
                draw.line(line,fill=255,width=width)
            for run in runs:
                if 'bbox' in run:
                    x0,y0,x1,y1=pixels(run['bbox'])
                    draw.rectangle((x0-left,y0-top,x1-left-1,y1-top-1),fill=255)
        # Inspect the same area that will be cleared, including the outer rim.
        unexplained=ImageChops.subtract(different_pixels(background.crop(bounds),expected),replaceable)
        if any(unexplained.crop(region).histogram()[255]>3*scale*scale for region in cell_regions):
            return None
        return ImageChops.invert(unexplained)

    def grid_colour(box):
        area=p.Rect(box)+(-2,-2,2,2)
        colours=[]
        for start,end in grid_lines:
            if not area.contains(p.Point(start)) or not area.contains(p.Point(end)):
                continue
            horizontal=abs(end[0]-start[0])>=abs(end[1]-start[1])
            for fraction in (.2,.45,.7):
                x,y=(a+(b-a)*fraction for a,b in zip(start,end))
                def sample(offset):
                    px=round((x if horizontal else x+offset)*scale)
                    py=round((y+offset if horizontal else y)*scale)
                    return original.getpixel((min(pix.width-1,max(0,px)),min(pix.height-1,max(0,py))))
                sides=[sample(-2),sample(2)]
                def contrast(rgb):
                    return min(sum(abs(a-b) for a,b in zip(rgb,side)) for side in sides)
                ink=max((sample(offset) for offset in (-.5,0,.5)),key=contrast)
                if contrast(ink)>30:
                    colours.append(ink)
        return '%02X%02X%02X'%tuple(round(median(channel)) for channel in zip(*colours)) if colours else '606060'

    def word_spaces(text, glyphs, mask, ink, x_offset):
        # The searchable PDF can omit spaces. Measure word gaps in the scan,
        # since adjacent OCR glyph boxes can have artificial gaps or overlap.
        characters=[(i,glyph) for i,glyph in enumerate(glyphs) if not glyph['c'].isspace()]
        if not ink or len(characters)<2:
            return text
        centers = [(glyph['bbox'][0] + glyph['bbox'][2]) / 2 for _,glyph in characters]
        if centers != sorted(centers):
            return text
        has_authored_spaces=any(glyph['c'].isspace() and not glyph.get('synthetic') for glyph in glyphs)
        minimum = max(2, round((ink[3] - ink[1]) * .24))
        spaces = set()
        column = ink[0]
        for inked, run in groupby(mask.getprojection()[0][ink[0]:ink[2]]):
            length = sum(1 for _ in run)
            if not inked and length >= minimum:
                index = bisect_right(centers, (x_offset + column + length / 2) / scale)
                if 0 < index < len(characters):
                    left_index,left_glyph=characters[index-1]
                    right_index,right_glyph=characters[index]
                    between=glyphs[left_index+1:right_index]
                    left,right=left_glyph['c'][-1:],right_glyph['c'][:1]
                    latin_gap=(left.isascii() and right.isascii() and left.isalnum() and right.isalnum()
                               and (left.isalpha() or right.isalpha()))
                    punctuation_gap=left in ':;!?：；！？' and right.isalpha()
                    # Keep authored spaces in place and confirm inferred
                    # spaces against the pixels at each individual gap.
                    # The invisible font can differ from the scan. Where it
                    # already has authored spaces, require geometric support
                    # before inserting a new break between touching glyphs.
                    box_gap=right_glyph['bbox'][0]-left_glyph['bbox'][2]
                    if between and all(glyph.get('synthetic') for glyph in between):
                        spaces.add(right_index)
                    elif not between and (latin_gap or punctuation_gap) and (not has_authored_spaces or box_gap>=1/scale):
                        spaces.add(right_index)
            column += length
        return ''.join((' ' if i in spaces else '') + glyph['c'].replace('\xa0',' ')
                       for i,glyph in enumerate(glyphs) if not (glyph.get('synthetic') and glyph['c'].isspace()))

    def recover(text, box, heading=False, keep_image=False, glyphs=()):
        bg=paper(box)
        # Individual OCR character boxes can trim the outer edge of a stroke.
        margin = 1.25 if glyphs else 0
        bounds=pixels((box[0]-margin,box[1],box[2]+margin,box[3]))
        crop=original.crop(bounds)
        diff=ImageChops.difference(crop,Image.new('RGB',crop.size,bg))
        mask=diff.convert('L').point(lambda v:255 if v>45 else 0)
        mask=ImageChops.subtract(mask,rules.crop(bounds))
        # OCR search-font boxes often overlap the next line. Assign shared
        # pixels to the nearer line centre before measuring glyph height;
        # otherwise a short label is sized from two lines' combined ink.
        area=p.Rect(box);centre=(area.y0+area.y1)/2
        top,bottom=area.y0,area.y1
        samples=mask.tobytes()
        row_ink=[sum(samples[y*mask.width:(y+1)*mask.width]) for y in range(mask.height)]
        for other in line_boxes:
            distance=(other.y0+other.y1)/2-centre
            overlap=min(area.x1,other.x1)-max(area.x0,other.x0)
            if (overlap<=min(area.width,other.width)*.5 or abs(distance)<min(area.height,other.height)*.5
                    or other.y1<=area.y0 or other.y0>=area.y1):
                continue
            boundary=centre+distance/2
            # Search the middle half between line centres for the quietest
            # scanline. Small OCR baseline offsets must not leave a neighbour's
            # last stroke inside this line's measurement.
            middle=round(boundary*scale-bounds[1]);radius=max(1,round(abs(distance)*scale/4))
            candidates=range(max(0,middle-radius),min(mask.height,middle+radius+1))
            cut=min(candidates,key=lambda y:(row_ink[y],abs(y-middle)),default=middle)
            boundary=(bounds[1]+cut)/scale
            if distance<0:top=max(top,boundary)
            else:bottom=min(bottom,boundary)
        first=max(0,min(mask.height,round(top*scale-bounds[1])))
        last=max(first,min(mask.height,round(bottom*scale-bounds[1])))
        if first:mask.paste(0,(0,0,mask.width,first))
        if last<mask.height:mask.paste(0,(0,last,mask.width,mask.height))
        ink=mask.getbbox()
        text=word_spaces(text,glyphs,mask,ink,bounds[0])
        values=[rgb for rgb,m in zip(crop.get_flattened_data(),mask.get_flattened_data()) if m]
        if values:
            contrast=lambda rgb:max(abs(c-b) for c,b in zip(rgb,bg))
            # Use stroke interiors, not the far more numerous antialiased edge
            # pixels; re-rendering those blended greys would fade the text twice.
            cutoff=sorted(contrast(v) for v in values)[int(len(values)*.95)]
            fg=common([v for v in values if contrast(v)>=cutoff])
        else:
            fg=(0,0,0)
        image_text=keep_image or any(mark.intersects(p.Rect(box)) for mark in marks)
        ink_height=(ink[3]-ink[1])/scale if ink else box[3]-box[1]
        family,heading=match_scan_font(text,mask,heading)
        font=scan_font(heading,family)
        glyph=mask_font(family,heading).getmask(text).getbbox()
        ink_ratio=(glyph[3]-glyph[1])/64 if glyph else .7
        size=max(3,min(96,(box[2]-box[0])/max(.1,font.text_length(text,fontsize=1)),ink_height/max(.1,ink_ratio)))
        # OCR stores a search font stretched to each glyph. Use a visible font and
        # a single line size instead of exporting those artificial per-glyph sizes.
        size=round(size*2)/2
        result=dict(text=text,font=family,size=size,bold=heading,colour='#%02X%02X%02X'%fg,
                    bbox=tuple(box),scanned=True,image_text=image_text)
        if not image_text:
            eraser=ImageChops.subtract(mask.filter(ImageFilter.MaxFilter(3)),keep.crop(bounds))
            background.paste(Image.new('RGB',crop.size,bg),bounds,eraser)
        return result,image_text

    for item in layout:
        if item['kind']=='text':
            vertical=abs(item.get('angle',0))>=10
            run,image_text=recover(item['text'],item['bbox'],item.get('heading',False),keep_image=vertical,
                                   glyphs=item.pop('ocr_glyphs',()))
            item.update(text=run['text'],size=run['size'],bold=run['bold'],colour=run['colour'],font=run['font'],runs=[run],
                        image_text=image_text,scanned=True)
            item.pop('fill_colour',None)
        elif item['kind']=='table':
            table=item['table']; raw_runs=table_text_runs(raw,table,include_hidden=True)
            cell_runs={}; cells=[]
            for cell in table.cells:
                parts=raw_runs.get((cell.row,cell.col),())
                groups=text_rows([part for part in parts if 'bbox' in part])
                recovered=[]
                for group in groups:
                    if not group:
                        continue
                    box=p.Rect(group[0]['bbox'])
                    for part in group[1:]:
                        box |= p.Rect(part['bbox'])
                    glyphs=[glyph for part in group for glyph in part.get('ocr_glyphs',())]
                    if not all(part.get('ocr_glyphs') for part in group):
                        glyphs=[]
                    run,_=recover(''.join(part['text'] for part in group),box,cell.style.bold,glyphs=glyphs)
                    if recovered:
                        recovered.append(dict(text='\n'))
                    recovered.append(run)
                if recovered:
                    cell_runs[cell.row,cell.col]=recovered
                fill=paper(cell.bbox,inside=True) if cell.bbox else (255,255,255)
                styles=[run for run in recovered if 'size' in run and not run.get('image_text')]
                style=replace(cell.style,font_name='Microsoft YaHei',
                              font_size=max((run['size'] for run in styles),default=cell.style.font_size or 11),
                              fill_color='#%02X%02X%02X'%fill,
                              **{f'border_{edge}':'thin' if getattr(cell.style,f'border_{edge}') else None
                                 for edge in ('left','right','top','bottom')})
                cells.append(replace(cell,text=''.join(run['text'] for run in recovered) if recovered else cell.text,style=style))
            table=replace(table,cells=cells)
            bounds=pixels(table.bbox,2)
            paper_colour=paper(table.bbox)
            clear_mask=table_clear_mask(table,cell_runs,bounds,paper_colour)
            native_grid=clear_mask is not None
            if native_grid:
                background.paste(paper_colour,bounds,clear_mask)
                item['border_colour']=grid_colour(table.bbox)
            else:
                warning=f'原 PDF 第 {page.number+1} 页的表格底图无法可靠分离，已保留该处外观，编辑后请核对位置。'
                if warning not in warnings:
                    warnings.append(warning)
            item.update(table=table,cell_runs=cell_runs,scanned=True,source_grid=not native_grid)
    # Search-layer font sizes are distorted by OCR box fitting. Re-evaluate
    # hierarchy using the visible glyph sizes recovered from the scan itself.
    text_items=[item for item in layout if item['kind']=='text' and not item.get('image_text')]
    body_size=median(item['size'] for item in text_items) if text_items else 11
    for item in text_items:
        if item['size']>=body_size*1.3 and len(item['text'])<=100:
            item.update(heading=True)
    if ImageChops.difference(background,Image.new('RGB',background.size,'white')).getbbox() is None:
        return []
    stream=BytesIO(); background.save(stream,format='PNG')
    if any(item.get('image_text') or any(run.get('image_text') for runs in item.get('cell_runs',{}).values() for run in runs) for item in layout):
        warnings.append('与图形交叠的文字行、图形字样和竖排标签保留原图，识别内容保存在图片说明中，避免混入正文；其余正文与表格仍可编辑。')
    return [dict(kind='image',bbox=(0,0,page.rect.width,page.rect.height),data=stream.getvalue(),image_role='background')]
