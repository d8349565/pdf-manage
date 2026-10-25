"""Smooth vertical remapping for photographed pages with varying line slopes.

This corrects local tilt, not arbitrary folds or missing/blurred text. It keeps
horizontal coordinates and adds white space instead of clipping boundary ink.
"""
from bisect import bisect_right
from math import atan, ceil, degrees, isfinite, radians, tan
from statistics import median

from PIL import Image, ImageChops, ImageFilter
from app.output import check_cancel


def _baseline(columns, width):
    points=[]
    chunk=max(5,width//90)
    for start in range(min(columns),max(columns)+1,chunk):
        values=sorted(columns[x] for x in range(start,start+chunk) if x in columns)
        if values:
            points.append((start+chunk/2,values[round((len(values)-1)*.8)]))
    if len(points)<12:
        return None
    for _ in range(3):
        mx,my=(sum(point[i] for point in points)/len(points) for i in (0,1))
        slope=sum((x-mx)*(y-my) for x,y in points)/max(1,sum((x-mx)**2 for x,y in points))
        offset=my-slope*mx
        error=median(abs(y-slope*x-offset) for x,y in points)
        good=[(x,y) for x,y in points if abs(y-slope*x-offset)<max(1.8,3*error)]
        if len(good)<12:
            return None
        points=good
    angle=-degrees(atan(slope))
    return (angle,error,offset+slope*width/2) if abs(angle)<=5 and error<2.5 else None


def estimate_bands(image, cancel=None):
    """Whole-page directions from actual text baselines, not fixed page halves.

    Dark ink and horizontally connected components suppress colored markings and
    tall diagram boxes. Robust baseline fits discard large handwriting outliers.
    """
    check_cancel(cancel)
    sample=image.convert('L')
    sample.thumbnail((720,1050))
    width,height=sample.size
    if width<160 or height<240:
        return ()
    mask=sample.point(lambda value:255 if value<110 else 0).filter(ImageFilter.MaxFilter(5))
    for distance in (2,4):
        left=Image.new('L',mask.size); right=left.copy()
        left.paste(mask,(-distance,0)); right.paste(mask,(distance,0))
        mask=ImageChops.lighter(mask,ImageChops.lighter(left,right))
    remaining=bytearray(mask.tobytes())
    observed=[]
    for seed in range(width*height):
        if seed%8192==0:
            check_cancel(cancel)
        if not remaining[seed]:
            continue
        queue=[seed]; remaining[seed]=0
        columns={}; top,bottom=height,0; visited=0
        while queue:
            pos=queue.pop(); y,x=divmod(pos,width); visited+=1
            if visited%8192==0:
                check_cancel(cancel)
            top,bottom=min(top,y),max(bottom,y)
            columns[x]=max(columns.get(x,-1),y)
            for nxt in (pos-width if y else -1,pos+width if y+1<height else -1,
                        pos-1 if x else -1,pos+1 if x+1<width else -1):
                if nxt>=0 and remaining[nxt]:
                    remaining[nxt]=0; queue.append(nxt)
        span=max(columns)-min(columns)
        if span<width*.22 or bottom-top>span*.18:
            continue
        fit=_baseline(columns,width)
        if fit is not None:
            angle,error,baseline=fit
            if 0<=baseline<height:
                observed.append((baseline/height,angle,span/(1+error)))
    if len(observed)<3 or max(y for y,_,_ in observed)-min(y for y,_,_ in observed)<.3:
        return ()
    # Fragments on the same text line contribute one weighted observation. This
    # avoids conflicting near-identical y positions creating sharp local warps.
    grouped=[]
    for y,angle,weight in sorted(observed):
        if grouped and y-grouped[-1][0]<.035:
            old_y,old_angle,old_weight=grouped[-1]
            total=weight+old_weight
            grouped[-1]=((old_y*old_weight+y*weight)/total,
                         (old_angle*old_weight+angle*weight)/total,total)
        else:
            grouped.append((y,angle,weight))
    if len(grouped)<3:
        return ()
    measured=tuple((y,0. if abs(angle)<.25 else angle) for y,angle,_ in grouped)
    check_cancel(cancel)
    return measured if max(abs(angle) for _,angle in measured)>=.35 else ()


def straighten_bands(image, corrections, cancel=None):
    """Apply a continuous piecewise-linear shear; corrections use normalized y."""
    check_cancel(cancel)
    if not corrections:
        return image.copy()
    if (len(corrections)<2 or any(not isfinite(y) or not isfinite(angle) or not 0<=y<=1 or abs(angle)>5
            for y,angle in corrections) or any(b[0]<=a[0] for a,b in zip(corrections,corrections[1:]))):
        raise ValueError('局部拉正参数无效。')
    width,height=image.size
    knots=[(y*height,-tan(radians(angle))) for y,angle in corrections]
    # Keep the vertical Jacobian positive. Do not squeeze/stretch text abruptly.
    for (y,a),(z,b) in zip(knots,knots[1:]):
        if abs(b-a)*width/2/(z-y)>.2:
            return image.copy()
    positions=[y for y,_ in knots]
    def slope(y):
        at=bisect_right(positions,y)
        if at==0:
            return knots[0][1]
        if at==len(knots):
            return knots[-1][1]
        start,a=knots[at-1]; stop,b=knots[at]
        return a+(b-a)*(y-start)/(stop-start)
    padding=ceil(max(abs(value) for _,value in knots)*width/2)+3
    output_height=height+padding*2
    boundaries=sorted({0,output_height,*range(padding,output_height,32),
                       *(max(0,min(output_height,round(y)+padding)) for y,_ in knots)})
    mesh=[]
    for start,stop in zip(boundaries,boundaries[1:]):
        check_cancel(cancel)
        def source(x,y):
            row=y-padding
            return x,row+slope(row)*(x-width/2)
        points=(*source(0,start),*source(0,stop),*source(width,stop),*source(width,start))
        mesh.append(((0,start,width,stop),points))
    result=image.transform((width,output_height),Image.Transform.MESH,mesh,
                           Image.Resampling.BICUBIC,fillcolor='white')
    check_cancel(cancel)
    return result


def straighten_photo(image, cancel=None):
    return straighten_bands(image,estimate_bands(image,cancel),cancel)
