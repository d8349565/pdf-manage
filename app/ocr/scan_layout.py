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
    for start,end in raster_grid_lines(page):
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

    def recover(text, box, heading=False, keep_image=False):
        bg=paper(box)
        bounds=pixels(box)
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
            run,image_text=recover(item['text'],item['bbox'],item.get('heading',False),keep_image=vertical)
            item.update(size=run['size'],bold=run['bold'],colour=run['colour'],font=run['font'],runs=[run],
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
                    run,_=recover(''.join(part['text'] for part in group),box,cell.style.bold)
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
            item.update(table=replace(table,cells=cells),cell_runs=cell_runs,scanned=True,source_grid=True)
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
