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
        groups.append([*sorted(combined), (coordinate, start, end)])
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
    return _grid_lines(pix, scale)


def _grid_lines(pix, scale):
    """Recover rule fragments and supported outer edges from grayscale pixels."""
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
