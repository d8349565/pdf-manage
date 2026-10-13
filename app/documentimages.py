"""Preserve embedded illustrations and remaining ink in scanned pages."""
import math


def _regions(active, width, height, cell=6):
    occupied=set()
    for y in range(height):
        for x in range(width):
            if active[y*width+x]:
                occupied.add((x//cell,y//cell))
    # A small geometric dilation reconnects strokes in handwriting and line art.
    expanded={(x+dx,y+dy) for x,y in occupied for dx,dy in ((0,0),(-1,0),(1,0),(0,-1),(0,1))}
    output=[]
    while expanded:
        start=expanded.pop(); pending=[start]; group={start}
        while pending:
            x,y=pending.pop()
            for dx,dy in ((-1,0),(1,0),(0,-1),(0,1)):
                neighbour=(x+dx,y+dy)
                if neighbour in expanded:
                    expanded.remove(neighbour); group.add(neighbour); pending.append(neighbour)
        if len(group)<8:
            continue
        output.append((max(0,min(x for x,y in group)*cell),max(0,min(y for x,y in group)*cell),
                       min(width,(max(x for x,y in group)+1)*cell),min(height,(max(y for x,y in group)+1)*cell)))
    return output


def page_figures(page, layout, warnings):
    import pymupdf as p
    width,height=page.cropbox.width,page.cropbox.height
    area=width*height
    images=page.get_image_info()
    boxes=[(p.Rect(item['bbox']),False) for item in images if 100<p.Rect(item['bbox']).get_area()<area*.65]
    editable=[p.Rect(item['bbox']) for item in layout if item['kind'] in ('text','table')]
    has_scan=any(p.Rect(item['bbox']).get_area()>=area*.65 for item in images)
    exclude_editable=has_scan or any(item.get('positioned') or item['kind']=='table' for item in layout)
    if has_scan:
        scale=min(1.2,1000/max(width,height))
        matrix=p.Matrix(scale,scale).prerotate(-page.rotation)
        pix=page.get_pixmap(matrix=matrix,colorspace=p.csRGB,alpha=False,annots=False)
        samples=pix.samples; size=pix.width*pix.height
        dark=bytearray(size); coloured=bytearray(size)
        for index in range(size):
            red,green,blue=samples[index*3:index*3+3]
            dark[index]=max(red,green,blue)<210
            coloured[index]=red>80 and red-green>45 and red-blue>45
        masks=editable+[box for box,is_colour in boxes]
        for box in masks:
            pixels=box*page.rotation_matrix*matrix
            x0=max(0,int(pixels.x0-pix.x)-4); x1=min(pix.width,math.ceil(pixels.x1-pix.x)+4)
            y0=max(0,int(pixels.y0-pix.y)-4); y1=min(pix.height,math.ceil(pixels.y1-pix.y)+4)
            for y in range(y0,y1):
                dark[y*pix.width+x0:y*pix.width+x1]=b'\0'*(x1-x0)
        for mask,is_colour in ((dark,False),(coloured,True)):
            for rectangle in _regions(mask,pix.width,pix.height):
                box=p.Rect(rectangle[0]+pix.x,rectangle[1]+pix.y,rectangle[2]+pix.x,rectangle[3]+pix.y)*~matrix*page.derotation_matrix
                if box.width<20 or box.height<12 or box.get_area()<400:
                    continue
                if is_colour and (box.width/box.height>3 or box.height<24):
                    continue
                if is_colour and any(item['kind']=='text' and p.Rect(item['bbox']).contains(box)
                                     and box.width/box.height>2 for item in layout):
                    continue
                boxes.append((box,is_colour))
    # Also preserve non-table vector illustrations without pretending they are text.
    tables=[p.Rect(item['bbox']) for item in layout if item['kind']=='table']
    for drawing in page.get_drawings():
        box=p.Rect(drawing['rect'])
        if box.width>=20 and box.height>=12 and not any(table.intersects(box) for table in tables):
            boxes.append((box,False))
    merged=[]
    for box,is_colour in boxes:
        bounds=p.Rect(0,0,width,height)
        box &= bounds
        if box.is_empty:
            continue
        for index,(previous,previous_colour) in enumerate(merged):
            if (box&previous).get_area()>=min(box.get_area(),previous.get_area())*.3:
                merged[index]=(box|previous,is_colour and previous_colour)
                break
        else:
            merged.append((box,is_colour))
    if len(merged)>20:
        warnings.append('图像区域较复杂，仅保留可确认的大区域，请核对原稿。')
        merged=sorted(merged,key=lambda entry:entry[0].get_area(),reverse=True)[:20]
    result=[]
    for box,is_colour in merged:
        matrix=p.Matrix(1.5,1.5).prerotate(-page.rotation)
        pix=page.get_pixmap(matrix=matrix,clip=box*page.rotation_matrix,colorspace=p.csRGB,alpha=True,annots=False)
        if pix.width and pix.height:
            if is_colour:
                # Keep the seal/mark itself without also copying nearby black
                # OCR text or table rules into its rectangular crop.
                samples=pix.samples
                alpha=bytearray(pix.width*pix.height)
                for offset in range(len(alpha)):
                    red,green,blue=samples[offset*4:offset*4+3]
                    alpha[offset]=255 if red>80 and red-green>45 and red-blue>45 else 0
                pix.set_alpha(alpha)
            elif exclude_editable:
                # Region detection uses masked ink. Apply the same exclusion to
                # the exported pixels, otherwise the crop duplicates table and
                # text content that already exists as editable Word elements.
                samples=pix.samples
                for mask in editable:
                    if mask.intersects(box):
                        pixels=mask*page.rotation_matrix*matrix
                        pix.set_rect(pixels.irect,(0,0,0,0))
                # A red seal can share a region with black ink, or cover text.
                # Restore its original pixels after excluding editable content.
                for offset in range(pix.width*pix.height):
                    red,green,blue=samples[offset*4:offset*4+3]
                    if red>80 and red-green>45 and red-blue>45:
                        pix.set_pixel(offset%pix.width,offset//pix.width,samples[offset*4:offset*4+4])
            role='mark' if is_colour else ('background' if exclude_editable and any(mask.intersects(box) for mask in editable) else 'figure')
            result.append(dict(kind='image',bbox=tuple(box),data=pix.tobytes('png'),image_role=role))
            if not exclude_editable and any(item['kind']=='text' and p.Rect(item['bbox']).intersects(box) for item in layout):
                warnings.append('图像与文字区域重叠，已保留图像外观与识别文字，请核对位置。')
    return result
