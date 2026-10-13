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
        matching = None
        for group in reversed(groups):
            last = group[-1]
            if coordinate - last[0] > 3:
                break
            overlap = min(end, last[2]) - max(start, last[1])
            if overlap >= min(end - start, last[2] - last[1]) * .75:
                matching = group
                break
        if matching is None:
            groups.append([(coordinate, start, end)])
        else:
            matching.append((coordinate, start, end))
    return [(mean(value[0] for value in group), min(value[1] for value in group),
             max(value[2] for value in group)) for group in groups
            if group[-1][0] - group[0][0] <= 8]


def raster_grid_lines(page):
    """Return visible page-coordinate line pairs only for confirmed closed grids."""
    import pymupdf as p
    if not page.get_images():
        return []
    # Dense, large tables lose their thin rules when reduced to a 1600px page.
    scale = min(2, 3200 / max(page.rect.width, page.rect.height))
    pix = page.get_pixmap(matrix=p.Matrix(scale, scale), colorspace=p.csGRAY, alpha=False, annots=False)
    width, height = pix.width, pix.height
    dark = bytes(value < 200 for value in pix.samples)
    minimum = max(20, min(width, height) // 35)
    horizontal = _thin_segments([(y, x0, x1) for y in range(height)
                                for x0, x1 in _runs(dark[y * width:(y + 1) * width], minimum)])
    vertical = _thin_segments([(x, y0, y1) for x in range(width)
                              for y0, y1 in _runs(dark[x::width], minimum)])
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
        left, right = min(v[0] for v in vs), max(v[0] for v in vs)
        top, bottom = min(h[0] for h in hs), max(h[0] for h in hs)
        if right - left < 40 * scale or bottom - top < 32 * scale:
            continue
        # Outer horizontal/vertical rules separate the table from nearby prose.
        if not all(any(abs(h[0] - y) <= 3 and h[1] <= left + 3 and h[2] >= right - 3 for h in hs)
                   for y in (top, bottom)):
            continue
        if not all(any(abs(v[0] - x) <= 3 and v[1] <= top + 3 and v[2] >= bottom - 3 for v in vs)
                   for x in (left, right)):
            continue
        output.extend([((start / scale, y / scale), (end / scale, y / scale)) for y, start, end in hs])
        output.extend([((x / scale, start / scale), (x / scale, end / scale)) for x, start, end in vs])
    return output
