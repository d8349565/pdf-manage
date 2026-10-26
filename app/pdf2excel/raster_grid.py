"""Find connected ruled grids in page pixels, independent of text and filenames."""
from statistics import mean


def _runs(data, minimum):
    # Bridge only tiny antialiasing/compression gaps, not word/column whitespace.
    data = data.replace(b'\1\0\1', b'\1\1\1').replace(b'\1\0\0\1', b'\1\1\1\1')
    offset = 0
    for run in data.split(b'\0'):
        if len(run) >= minimum:
            yield offset, offset + len(run) - 1
        offset += len(run) + 1


def _thin_segments(values):
    groups = []
    for coordinate, start, end in values:
        original=(coordinate,start,end)
        matching = []
        for group in reversed(groups):
            last = group[-1]
            if coordinate - last[0] > 3:
                break
            left = min(value[1] for value in group)
            right = max(value[2] for value in group)
            overlap = min(end, right) - max(start, left)
            if overlap >= min(end - start, right - left) * .25 or -2 <= overlap <= 0:
                matching.append(group)
                start, end = min(start, left), max(end, right)
        combined = [value for group in matching for value in group]
        for group in matching:
            groups.remove(group)
        # Keep groups ordered by their LAST coordinate. Otherwise a finished
        # short stroke hides a still-active rule behind the early break above.
        groups.append([*sorted(combined), original])
    result=[]
    for group in groups:
        # Preserve the actual run lengths: a graphic branching off a long
        # rule must not turn every following short stroke into a full rule.
        span=max(v[2] for v in group)-min(v[1] for v in group)+1
        longest=max(v[2]-v[1]+1 for v in group)
        core=[v for v in group if v[2]-v[1]+1>=longest*.8]
        if (group[-1][0]-group[0][0]>8 and longest>=span*.8 and len(core)<=8
                and max(v[0] for v in core)-min(v[0] for v in core)<=8):
            group=core
        left=min(value[1] for value in group);right=max(value[2] for value in group)
        band=group[-1][0]-group[0][0]
        centers=[(v[1]+v[2])/2 for v in group]
        along=mean(centers);across=mean(v[0] for v in group)
        variance=sum((v-along)**2 for v in centers)
        # Within a thin pixel band, ragged antialiasing endpoints can move the
        # run centres much more than the rule itself. That is not a diagonal.
        if band>8 and max(centers)-min(centers)>(right-left)*.25 and variance:
            slope=sum((center-along)*(value[0]-across) for center,value in zip(centers,group))/variance
            if abs(slope)>.02:continue  # Rotated artwork is not an almost-axis-aligned rule.
        # A slightly tilted thin rule spans more rows than a horizontal one.
        # Its ink area / length stays small; a filled panel does not.
        thickness=sum(value[2]-value[1]+1 for value in group)/max(1,right-left+1)
        if thickness<=8 and band<=max(8,min(20,(right-left)*.02)):
            result.append((mean(value[0] for value in group),left,right))
    return result


def raster_grid_lines(page):
    """Return visible page-coordinate line pairs only for confirmed closed grids."""
    import pymupdf as p
    if not page.get_images():
        return []
    # Dense, large tables lose their thin rules when reduced to a 1600px page.
    scale = min(2, 3200 / max(page.rect.width, page.rect.height))
    pix = page.get_pixmap(matrix=p.Matrix(scale, scale), colorspace=p.csGRAY, alpha=False, annots=False)
    return _grid_lines(pix, scale)


def _grid_lines(pix, scale):
    """Recover rule fragments and supported outer edges from grayscale pixels."""
    width, height = pix.width, pix.height
    from PIL import Image,ImageChops,ImageFilter
    image=Image.frombytes('L',(width,height),pix.samples)
    # Thin pale rules may never reach an absolute grey threshold. Compare
    # each stroke with its local surroundings, including on coloured paper.
    contrast=ImageChops.subtract(image.filter(ImageFilter.MaxFilter(5)),image)
    dark_image=contrast.point(lambda value:1 if value>10 else 0)
    def ridge(dx,dy):
        # A bright rule must be brighter than BOTH sides. Bright paper beside
        # a dark glyph is not evidence of a white grid rule.
        a=ImageChops.subtract(image,ImageChops.offset(image,dx,dy))
        b=ImageChops.subtract(image,ImageChops.offset(image,-dx,-dy))
        return ImageChops.lighter(dark_image,ImageChops.darker(a,b).point(lambda v:1 if v>10 else 0)).tobytes()
    dark=ridge(0,3);vertical_dark=ridge(3,0)
    minimum = max(20, min(width, height) // 35)
    horizontal = _thin_segments([(y, x0, x1) for y in range(height)
                                for x0, x1 in _runs(dark[y * width:(y + 1) * width], minimum)])
    vertical = _thin_segments([(x, y0, y1) for x in range(width)
                              for y0, y1 in _runs(vertical_dark[x::width], minimum)])
    # Bound work for dense artwork; uncertain pages must not become fake tables.
    if len(horizontal) * len(vertical) > 250000:
        return []
    edges = {}
    for h_index, (y, left, right) in enumerate(horizontal):
        for v_index, (x, top, bottom) in enumerate(vertical):
            if left - 3 <= x <= right + 3 and top - 3 <= y <= bottom + 3:
                h, v = ('h', h_index), ('v', v_index)
                edges.setdefault(h, set()).add(v)
                edges.setdefault(v, set()).add(h)
    seen, output = set(), []
    for node in edges:
        if node in seen:
            continue
        component, pending = set(), [node]
        while pending:
            current = pending.pop()
            if current in component:
                continue
            component.add(current)
            pending.extend(edges[current] - component)
        seen.update(component)
        hs = [horizontal[index] for kind, index in component if kind == 'h']
        vs = [vertical[index] for kind, index in component if kind == 'v']
        if len(hs) < 3 or len(vs) < 3:
            continue
        left = min(min(v[0] for v in vs), min(h[1] for h in hs))
        right = max(max(v[0] for v in vs), max(h[2] for h in hs))
        top, bottom = min(h[0] for h in hs), max(h[0] for h in hs)
        if right - left < 40 * scale or bottom - top < 32 * scale:
            continue
        # Outer horizontal/vertical rules separate the table from nearby prose.
        if not all(any(abs(h[0] - y) <= 3 and h[1] <= left + 3 and h[2] >= right - 3 for h in hs)
                   for y in (top, bottom)):
            continue
        borders = []
        for x, endpoint in ((left, 1), (right, 2)):
            if any(abs(v[0] - x) <= 3 and v[1] <= top + 3 and v[2] >= bottom - 3 for v in vs):
                continue
            # A clipped/faded side is supported only by at least three rules
            # ending there, including the confirmed top and bottom rules.
            if sum(abs(h[endpoint] - x) <= 3 for h in hs) < 3:
                break
            borders.append((x, top, bottom))
        else:
            output.extend([(((start + pix.x) / scale, (y + pix.y) / scale),
                            ((end + pix.x) / scale, (y + pix.y) / scale)) for y, start, end in hs])
            output.extend([(((x + pix.x) / scale, (max(start, top) + pix.y) / scale),
                            ((x + pix.x) / scale, (min(end, bottom) + pix.y) / scale))
                           for x, start, end in vs + borders])
    return output
