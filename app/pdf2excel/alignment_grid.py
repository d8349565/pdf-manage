"""Conservative local grids from repeated short fields and visual header evidence."""
from statistics import median


def _rows(spans):
    import pymupdf as p
    groups=[]
    for span in sorted(spans,key=lambda item:(item['bbox'][1],item['bbox'][0])):
        if not span['text'].strip():
            continue
        box=p.Rect(span['bbox'])
        center=(box.y0+box.y1)/2
        matches=[row for row in groups if abs(row['center']-center)<=min(row['height'],box.height)*.45]
        if matches:
            row=min(matches,key=lambda row:abs(row['center']-center))
            row['spans'].append(span)
        else:
            groups.append(dict(center=center,height=box.height,spans=[span]))
    result=[]
    for group in groups:
        cells=[]
        for span in sorted(group['spans'],key=lambda item:item['bbox'][0]):
            box=span['bbox']
            if cells and box[0]-cells[-1]['bbox'][2]<=min(group['height'],box[3]-box[1])*1.2:
                previous=cells[-1]
                previous['text']+=' '+span['text']
                previous['bbox']=tuple(p.Rect(previous['bbox'])|p.Rect(box))
            else:
                cells.append(dict(text=span['text'],bbox=box))
        result.append(cells)
    return sorted(result,key=lambda row:min(cell['bbox'][1] for cell in row))


def _header_colour(page,box):
    import pymupdf as p
    # Sample only whitespace around the header text, never reinterpret its words.
    pix=page.get_pixmap(matrix=p.Matrix(.8,.8).prerotate(-page.rotation),colorspace=p.csRGB,alpha=False,annots=False)
    data=pix.samples
    def colour(x,y):
        x=max(0,min(pix.width-1,round(x*.8-pix.x)))
        y=max(0,min(pix.height-1,round(y*.8-pix.y)))
        offset=(y*pix.width+x)*3
        return tuple(data[offset:offset+3])
    samples=[colour(box[0]+(box[2]-box[0])*fraction,box[1]+(box[3]-box[1])*vertical)
             for fraction in (.1,.2,.3,.4,.5,.6,.7,.8,.9) for vertical in (.25,.6,.8)]
    background=colour(2,2)
    for candidate in samples:
        if min(candidate)<=170 or max(abs(candidate[channel]-background[channel]) for channel in range(3))<8:
            continue
        matching=[sample for sample in samples if max(abs(sample[channel]-candidate[channel]) for channel in range(3))<10]
        if len(matching)>=len(samples)*.45:
            common=tuple(round(median(sample[channel] for sample in matching)) for channel in range(3))
            bands=[]
            for y in range(max(0,round(box[1]*.8-pix.y)),min(pix.height,round(box[3]*.8-pix.y))):
                positions=[x for x in range(pix.width) if max(abs(data[(y*pix.width+x)*3+channel]-common[channel]) for channel in range(3))<10]
                if positions and len(positions)>=(box[2]-box[0])*.8*.7:
                    bands.append((min(positions),y,max(positions)+1,y+1))
            if bands:
                bounds=(min(band[0] for band in bands),min(band[1] for band in bands),max(band[2] for band in bands),max(band[3] for band in bands))
                return common,tuple((value+(pix.x if index%2==0 else pix.y))/.8 for index,value in enumerate(bounds))
            return common,None
    return None,None


def alignment_grid_lines(page,spans):
    """Require at least a header plus two stable rows; do not guess from prose alone."""
    import pymupdf as p
    if len(spans)>2500:
        return [],[]
    canonical=[]
    for span in spans:
        direction=span.get('dir',(1,0)); matrix=page.derotation_matrix
        dx,dy=direction[0]*matrix.a+direction[1]*matrix.c,direction[0]*matrix.b+direction[1]*matrix.d
        if abs(dy)>.08 or dx<.95:
            continue
        canonical.append(dict(span,bbox=tuple(p.Rect(span['bbox'])*matrix)))
    rows=_rows(canonical)
    lines=[]; fills=[]; cursor=0
    while cursor<len(rows):
        seed=rows[cursor]; count=len(seed)
        if not 2<=count<=12 or any(len(cell['text'])>60 for cell in seed):
            cursor+=1; continue
        size=median(cell['bbox'][3]-cell['bbox'][1] for cell in seed)
        anchors=[cell['bbox'][0] for cell in seed]
        logical=[list(seed)]; finish=cursor+1
        for row in rows[cursor+1:]:
            gap=min(cell['bbox'][1] for cell in row)-max(cell['bbox'][3] for cell in logical[-1])
            if gap>size*3.5:
                break
            aligned=len(row)==count and all(abs(cell['bbox'][0]-anchor)<=size*.9 for cell,anchor in zip(row,anchors))
            if aligned and all(len(cell['text'])<=60 for cell in row):
                logical.append(list(row)); finish+=1; continue
            # A wrapped cell can continue only inside its existing column.
            matches=[]
            for cell in row:
                choices=[index for index,anchor in enumerate(anchors) if abs(cell['bbox'][0]-anchor)<=size*.9]
                if len(choices)!=1:
                    break
                column=choices[0]
                right=anchors[column+1]-size*.4 if column+1<count else page.cropbox.width
                if column==0 or cell['bbox'][2]>right or len(cell['text'])>60 or gap>size*1.2:
                    break
                matches.append(column)
            if len(matches)!=len(row) or len(row)>=count:
                break
            for cell,column in zip(row,matches):
                previous=logical[-1][column]
                logical[-1][column]=dict(text=previous['text']+'\n'+cell['text'],bbox=tuple(p.Rect(previous['bbox'])|p.Rect(cell['bbox'])))
            finish+=1
        if len(logical)<3:
            cursor+=1; continue
        data=[cell for row in logical[1:] for cell in row]
        numeric=sum(any(char.isdigit() for char in cell['text']) for cell in data)/len(data)
        left=min(cell['bbox'][0] for row in logical for cell in row)-size*.45
        right=max(cell['bbox'][2] for row in logical for cell in row)+size*.6
        boundaries=[left]
        for column in range(count-1):
            previous=max(row[column]['bbox'][2] for row in logical)
            following=min(row[column+1]['bbox'][0] for row in logical)
            if following-previous<size*.45:
                break
            boundaries.append((previous+following)/2)
        if len(boundaries)!=count:
            cursor+=1; continue
        boundaries.append(right)
        top=min(cell['bbox'][1] for cell in logical[0])-size*.45
        bottom=max(cell['bbox'][3] for cell in logical[-1])+size*.5
        heights=[top]+[(max(cell['bbox'][3] for cell in before)+min(cell['bbox'][1] for cell in after))/2
                       for before,after in zip(logical,logical[1:])]+[bottom]
        colour,band=_header_colour(page,(left,top,right,heights[1]))
        if numeric<.3 and colour is None:
            cursor+=1; continue
        if band and band[0]<=min(anchors) and band[2]>=right-size*.6:
            padding=min(size,max(2,anchors[0]-band[0]))
            inferred=[band[0]]+[anchor-padding for anchor in anchors[1:]]+[band[2]]
            if all(inferred[column+1]>=max(row[column]['bbox'][2] for row in logical) for column in range(count)):
                left,right=band[0],band[2]; boundaries=inferred
                if band[1]<=min(cell['bbox'][1] for cell in logical[0]) and band[3]>=max(cell['bbox'][3] for cell in logical[0]):
                    heights[0],heights[1]=band[1],band[3]
        def display(point):
            return tuple(p.Point(point)*page.rotation_matrix)
        lines.extend((display((left,y)),display((right,y))) for y in heights)
        lines.extend((display((x,top)),display((x,bottom))) for x in boundaries)
        if colour:
            fills.append(dict(bbox=tuple(p.Rect(left,top,right,heights[1])*page.rotation_matrix),color=tuple(channel/255 for channel in colour)))
        cursor=finish
    return lines,fills
