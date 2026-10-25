"""Paper proposals and local illumination correction using the shipped Pillow."""
from dataclasses import dataclass
from io import BytesIO
from math import hypot, isfinite, sqrt
from statistics import median

from PIL import Image, ImageChops, ImageFilter, ImageMath, ImageOps
import pymupdf as p

from app.output import check_cancel, cancellable_lock
from app.workspace import PageRef

FULL_PAGE = ((0., 0.), (1., 0.), (1., 1.), (0., 1.))


@dataclass(frozen=True)
class PaperProposal:
    quad: tuple = FULL_PAGE
    confidence: float = 0.
    message: str = '未找到可靠纸边，暂时保留整页；可框选或拖动四角。'


def _cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def _area(points):
    return abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(points, (*points[1:],points[0])))) / 2


def valid_quad(quad):
    return (len(quad) == 4 and all(len(point) == 2 and all(isfinite(v) and 0 <= v <= 1 for v in point) for point in quad)
            and all(_cross(quad[i-1],quad[i],quad[(i+1)%4]) > .0001 for i in range(4))
            and _area(quad) >= .025)


def inset_region(quad,margin=.01):
    """Small, visible inner margin for scanner borders and neighboring paper edges."""
    if not valid_quad(quad) or not 0<=margin<=.1:
        raise ValueError('向内裁边的范围无效。')
    def point(u,v):
        return tuple((1-u)*(1-v)*quad[0][axis]+u*(1-v)*quad[1][axis]
            +u*v*quad[2][axis]+(1-u)*v*quad[3][axis] for axis in (0,1))
    result=tuple(point(u,v) for u,v in ((margin,margin),(1-margin,margin),
                                     (1-margin,1-margin),(margin,1-margin)))
    if not valid_quad(result):
        raise ValueError('区域已经很小，请拖动四角调整。')
    return result


def _hull(points):
    points = sorted(set(points))
    def half(values):
        result = []
        for point in values:
            while len(result)>1 and _cross(result[-2],result[-1],point)<=0:
                result.pop()
            result.append(point)
        return result
    return half(points)[:-1] + half(reversed(points))[:-1]


def _boundary_line(points):
    """Fit the dominant straight boundary, ignoring small attached paper/backgrounds."""
    probes=points[::max(1,len(points)//20)]
    best=[]
    for i,(x,y) in enumerate(probes):
        for u,v in probes[i+1:]:
            if abs(u-x)<12:
                continue
            slope=(v-y)/(u-x)
            if abs(slope)>.65:
                continue
            offset=y-slope*x
            inline=[(a,b) for a,b in points if abs(b-slope*a-offset)<2.2]
            if len(inline)>len(best):
                best=inline
    if len(best)<max(12,len(points)*.42):
        return None
    mx,my=(sum(v[i] for v in best)/len(best) for i in range(2))
    slope=sum((x-mx)*(y-my) for x,y in best)/max(1,sum((x-mx)**2 for x,y in best))
    return slope,my-slope*mx


def _boundary_quad(spans,columns,w,h,fallback):
    top=_boundary_line([(x,ends[0]) for x,ends in sorted(columns.items())])
    bottom=_boundary_line([(x,ends[1]) for x,ends in sorted(columns.items())])
    left=_boundary_line([(y,ends[0]) for y,ends in sorted(spans.items())])
    right=_boundary_line([(y,ends[1]) for y,ends in sorted(spans.items())])
    def through(a,b,swap=False):
        if swap:
            a,b=(a[1],a[0]),(b[1],b[0])
        if abs(b[0]-a[0])<1e-5:
            return None
        slope=(b[1]-a[1])/(b[0]-a[0])
        return slope,a[1]-slope*a[0]
    sides=(through(fallback[0],fallback[1]),through(fallback[3],fallback[2]),
           through(fallback[0],fallback[3],True),through(fallback[1],fallback[2],True))
    top,bottom,left,right=tuple(found or old for found,old in zip((top,bottom,left,right),sides))
    if any(line is None for line in (top,bottom,left,right)):
        return None
    if right[1]>=w-2 and abs(right[0])<.01:
        right=sides[3]
    if left[1]<=1 and abs(left[0])<.01:
        left=sides[2]
    def meet(horizontal,vertical,dx,dy):
        a,b=horizontal; c,d=vertical
        b+=dy; d+=dx
        x=(c*b+d)/(1-c*a); y=a*x+b
        return max(0,min(1,x/(w-1))),max(0,min(1,y/(h-1)))
    quad=(meet(top,left,-2,-2),meet(top,right,2,-2),meet(bottom,right,2,2),meet(bottom,left,-2,2))
    return quad if valid_quad(quad) else None


def recommend_paper(image, cancel=None):
    """Conservative neutral-paper segmentation; every proposal needs visual review.

    Deliberately falls back on a full page for low-contrast or busy backgrounds.
    It does not use image-specific coordinates, OCR, or an optional CV runtime.
    """
    sample = image.convert('RGB')
    sample.thumbnail((360,480))
    w,h = sample.size
    if min(w,h)<40:
        return PaperProposal()
    center = sample.crop((w//4,h//5,3*w//4,4*h//5))
    pixels = list(center.get_flattened_data())
    brightness = median((r+g+b)/3 for r,g,b in pixels)
    paper = [(r-g,b-g) for r,g,b in pixels if (r+g+b)/3 >= brightness]
    rg,bg = (median(value[i] for value in paper) for i in range(2))
    all_pixels = list(sample.get_flattened_data())
    edges = ImageOps.grayscale(sample).filter(ImageFilter.FIND_EDGES)
    edge_pixels = edges.load()
    best = None
    for tolerance in (12,20,30):
        check_cancel(cancel)
        mask = Image.new('L',(w,h))
        mask.putdata([255 if (r+g+b)/3 > max(55,brightness*.55)
            and abs(r-g-rg)<tolerance and abs(b-g-bg)<tolerance else 0 for r,g,b in all_pixels])
        mask = mask.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))
        remaining = bytearray(mask.tobytes())
        largest, largest_count, largest_spans, largest_columns = [],0,{},{}
        # Connected components on a bounded thumbnail; collect only each row's extent.
        for seed in range(w*h):
            if remaining[seed] == 0:
                continue
            queue, count, spans, columns = [seed],0,{},{}
            remaining[seed]=0
            while queue:
                pos=queue.pop(); y,x=divmod(pos,w); count+=1
                left,right=spans.get(y,(x,x)); spans[y]=(min(x,left),max(x,right))
                top,bottom=columns.get(x,(y,y)); columns[x]=(min(y,top),max(y,bottom))
                for nxt in (pos-w if y else -1,pos+w if y+1<h else -1,
                            pos-1 if x else -1,pos+1 if x+1<w else -1):
                    if nxt>=0 and remaining[nxt]:
                        remaining[nxt]=0; queue.append(nxt)
            if count>largest_count:
                largest_count=count
                largest_spans,largest_columns=spans,columns
                largest=[point for y,(left,right) in spans.items() for point in ((left,y),(right,y))]
        if largest_count < w*h*.2:
            continue
        hull = _hull(largest)
        while len(hull)>4:
            index=min(range(len(hull)),key=lambda i:abs(_cross(hull[i-1],hull[i],hull[(i+1)%len(hull)])))
            hull.pop(index)
        if len(hull)!=4:
            continue
        start=min(range(4),key=lambda i:sum(hull[i]))
        hull=hull[start:]+hull[:start]
        quad=tuple((x/(w-1),y/(h-1)) for x,y in hull)
        boundary=_boundary_quad(largest_spans,largest_columns,w,h,hull)
        if boundary is not None:
            quad=boundary
            hull=[(x*(w-1),y*(h-1)) for x,y in quad]
        if not valid_quad(quad):
            continue
        area=_area(hull)
        fill=min(1,largest_count/max(area,1))
        line=[]
        for a,b in zip(hull,(*hull[1:],hull[0])):
            for step in range(3,47):
                x=round(a[0]+(b[0]-a[0])*step/50); y=round(a[1]+(b[1]-a[1])*step/50)
                line.append(max(edge_pixels[max(1,min(w-2,x+dx)),max(1,min(h-2,y+dy))]
                                for dx,dy in ((0,0),(-1,0),(1,0),(0,-1),(0,1))))
        edge=sum(line)/len(line)
        score=fill*.5+min(edge/70,1)*.3+min(area/(w*h),.8)*.2
        if fill>=.75 and area/(w*h)<.97 and edge>=14 and (best is None or score>best[0]):
            best=(score,quad)
    if best is None or best[0]<.64:
        return PaperProposal()
    return PaperProposal(best[1],min(.95,best[0]),'已推荐纸张范围，请核对四角；残留细边可向内裁一点或拖动调整。')


def crop_paper(image, quad=FULL_PAGE):
    if not valid_quad(quad):
        raise ValueError('纸张四角须按顺序围成有效区域，至少保留页面的 2.5%。')
    if tuple(quad)==FULL_PAGE:
        return image.copy()
    points=[(x*(image.width-1),y*(image.height-1)) for x,y in quad]
    distance=lambda a,b:hypot(a[0]-b[0],a[1]-b[1])
    width=max(2,round((distance(points[0],points[1])+distance(points[3],points[2]))/2))
    height=max(2,round((distance(points[0],points[3])+distance(points[1],points[2]))/2))
    # Solve the eight projective coefficients mapping output pixels to input pixels.
    matrix=[]
    for (u,v),(x,y) in zip(((0,0),(width-1,0),(width-1,height-1),(0,height-1)),points):
        matrix.extend(([u,v,1,0,0,0,-x*u,-x*v,x],[0,0,0,u,v,1,-y*u,-y*v,y]))
    for col in range(8):
        pivot=max(range(col,8),key=lambda row:abs(matrix[row][col]))
        matrix[col],matrix[pivot]=matrix[pivot],matrix[col]
        divisor=matrix[col][col]
        if abs(divisor)<1e-10:
            raise ValueError('纸张范围过窄，请重新框选。')
        matrix[col]=[value/divisor for value in matrix[col]]
        for row in range(8):
            if row!=col:
                factor=matrix[row][col]
                matrix[row]=[a-factor*b for a,b in zip(matrix[row],matrix[col])]
    return image.transform((width,height),Image.Transform.PERSPECTIVE,
        [row[8] for row in matrix],Image.Resampling.BICUBIC,fillcolor='white')


def whiten_image(image, *, mode='color', strength=45, quad=FULL_PAGE, straighten=False, cancel=None):
    if mode not in ('color','bw') or not 0<=strength<=100:
        raise ValueError('增强模式或强度无效。')
    check_cancel(cancel)
    rgba=image.convert('RGBA')
    rgb=Image.new('RGB',rgba.size,'white'); rgb.paste(rgba,mask=rgba.getchannel('A'))
    rgb=crop_paper(rgb,quad)
    small=rgb.copy(); small.thumbnail((1100,1100))
    radius=max(3,round(max(small.size)/90)) | 1
    corrected=[]
    for channel,small_channel in zip(rgb.split(),small.split()):
        check_cancel(cancel)
        background=small_channel.filter(ImageFilter.MaxFilter(radius)).filter(ImageFilter.GaussianBlur(radius/2))
        background=background.resize(rgb.size,Image.Resampling.BILINEAR).point(lambda value:max(32,value))
        corrected.append(ImageMath.lambda_eval(lambda d:d['convert'](d['a']*255/d['b'],'L'),a=channel,b=background))
    normalized=Image.merge('RGB',corrected)
    black,white=28+strength*.2,252-strength*.15
    result=normalized.point([max(0,min(255,round((value-black)*255/(white-black)))) for value in range(256)]*3)
    if mode=='bw':
        # Keep gray pencil strokes instead of turning paper texture into binary ink blobs.
        result=ImageOps.grayscale(result)
    if straighten:
        from app.scanstraighten import straighten_photo
        result=straighten_photo(result,cancel)
    check_cancel(cancel)
    return result


def render_scan(store, ref, cancel=None, *, max_side=4200):
    store.require_operation([ref],'content_edit')
    store.require_operation([ref],'copy')
    with store._render_page(ref,cancel) as page:
        if page.annot_xrefs() or page.get_links():
            raise ValueError('含批注、印章、链接或表单的页请先另存图片副本，再增强；原页未修改。')
        width,height=store.page_size(ref)
        scale=min(300/72,max_side/max(width,height),sqrt(24_000_000/(width*height)))
        pix=page.get_pixmap(matrix=p.Matrix(scale,scale).prerotate(ref.rotation),colorspace=p.csRGB,alpha=False,annots=False)
        return Image.frombytes('RGB',(pix.width,pix.height),pix.samples),(width,height)


def prepare_whitening(store, pages, indices, *, mode='color', strength=45, regions=None, straighten=False, progress=None, cancel=None):
    """Prepare every raster page before the caller atomically changes its workspace."""
    regions=regions or {}
    changes={}
    for step,row in enumerate(indices):
        check_cancel(cancel)
        ref=pages[row]
        if progress:
            progress(step,len(indices),f'正在增强第 {row+1} 页…')
        try:
            image,(width,height)=render_scan(store,ref,cancel)
        except ValueError as exc:
            raise ValueError(f'第 {row+1} 页：{exc}') from exc
        region=regions.get(row,FULL_PAGE)
        result=whiten_image(image,mode=mode,strength=strength,quad=region,straighten=straighten,cancel=cancel)
        out=BytesIO(); result.save(out,format='PNG')
        origin=store._source(ref.source_id)
        from app.pdfstructure import copy_document_properties
        with cancellable_lock(store._native_lock,cancel):
            with p.open() as doc:
                with origin.lock:
                    copy_document_properties(doc,{ref.source_id:origin.doc_locked()},[])
                page=doc.new_page(width=width*result.width/image.width,height=height*result.height/image.height)
                page.insert_image(page.rect,stream=out.getvalue())
                label=store.page_label(ref)
                if label is not None:
                    doc.set_page_labels([dict(startpage=0,prefix=label,style='')])
                data=doc.tobytes(garbage=4,deflate=True)
            document=p.open(stream=data,filetype='pdf')
            try:
                sid=store._register(origin.path,document,data,origin.password,0,origin.disk_stamp)
                source=store._source(sid)
                source.requires_copy=True
                source.needs_encryption_choice=origin.safety.encrypted or origin.needs_encryption_choice
                source.lost_structures=tuple(dict.fromkeys((*origin.lost_structures,*origin.safety.special_structures)))
            except Exception:
                document.close()
                raise
        changes[row]=PageRef(sid,ref.source_path,0,page_id=ref.page_id)
    check_cancel(cancel)
    if progress:
        progress(len(indices),len(indices),'增强页面已准备好')
    return changes
