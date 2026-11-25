"""Native PDF arrow styles and the geometry shared by the canvas and print appearance."""
import pymupdf as p


ENDS = {
    'filled': (p.PDF_ANNOT_LE_NONE, p.PDF_ANNOT_LE_CLOSED_ARROW),
    'open': (p.PDF_ANNOT_LE_NONE, p.PDF_ANNOT_LE_OPEN_ARROW),
    'double_filled': (p.PDF_ANNOT_LE_CLOSED_ARROW, p.PDF_ANNOT_LE_CLOSED_ARROW),
    'double_open': (p.PDF_ANNOT_LE_OPEN_ARROW, p.PDF_ANNOT_LE_OPEN_ARROW),
}


def read_style(annot):
    if annot.type[0] != p.PDF_ANNOT_LINE:
        return None
    actual_ends = annot.line_ends
    head = next((key for key, ends in ENDS.items() if ends == actual_ends), None)
    if head is None:
        return None
    dashes = annot.border.get('dashes')
    line = 'solid' if not dashes else 'dotted' if dashes[0] == 1 else 'dashed'
    return dict(head=head, line=line)


def dash_pattern(line, width):
    if line == 'dotted':
        return (1, max(2, round(2.5 * width)))
    if line == 'dashed':
        return (max(3, round(4 * width)), max(2, round(2.5 * width)))
    return ()


def set_style(annot, style, width, color):
    ends = ENDS[style.get('head', 'filled')]
    annot.set_line_ends(*ends)
    annot.set_border(width=width, style='S', dashes=dash_pattern(style.get('line'), width))
    annot.set_colors(fill=color if p.PDF_ANNOT_LE_CLOSED_ARROW in ends else [])


def geometry(start, end, width, head):
    start, end = p.Point(start), p.Point(end)
    length = abs(end - start)
    if length < .01:
        raise ValueError("请拖动绘制箭头。")
    direction = (end - start) / length
    normal = p.Point(-direction.y, direction.x)
    tip_length = min(max(5, width * 3), length * .35)
    shaft, tips = [start, end], []
    for i, (tip, inward, ending) in enumerate(zip((start, end), (direction, -direction), ENDS[head])):
        if ending == p.PDF_ANNOT_LE_NONE:
            continue
        base = tip + inward * tip_length
        filled = ending == p.PDF_ANNOT_LE_CLOSED_ARROW
        tips.append((base + normal * (tip_length * .38), tip, base - normal * (tip_length * .38), filled))
        if filled:
            shaft[i] = tip + inward * (tip_length * .6)
    return shaft, tips


def move_line(page, annot, target):
    """Translate the native endpoints and appearance bounds together, retaining the original artwork."""
    from app.pdfstructure import page_pdf_matrix
    rect = annot.rect
    delta = target - rect.tl
    vertices = annot.vertices
    if not vertices or len(vertices) != 2:
        raise ValueError("箭头端点无效，无法移动。")
    to_pdf = page.rotation_matrix * ~page_pdf_matrix(page)
    start, end = [(p.Point(vertex) + delta) * to_pdf for vertex in vertices]
    bounds = p.Rect(rect.tl + delta, rect.br + delta) * to_pdf
    for key, values in (('L', (*start, *end)), ('Rect', bounds)):
        page.parent.xref_set_key(annot.xref, key, '[' + ' '.join(f'{v:.9f}' for v in values) + ']')


def update_appearance(page, annot):
    style = read_style(annot)
    if not style:
        return
    from app.pdfstructure import page_pdf_matrix
    to_pdf = page.rotation_matrix * ~page_pdf_matrix(page)

    def xy(point):
        point = point * to_pdf
        return f"{point.x:.4f} {point.y:.4f}"

    width = max(.1, annot.border['width'])
    shaft, tips = geometry(*annot.vertices, width, style['head'])
    components = annot.colors['stroke'] or (0, 0, 0)
    color = ' '.join(f'{v:.4f}' for v in components)
    operator = {1: 'G', 3: 'RG', 4: 'K'}[len(components)]
    dashes = ' '.join(str(v) for v in annot.border.get('dashes') or ())
    xref = int(page.parent.xref_get_key(annot.xref, 'AP/N')[1].split()[0])
    opacity = '/H gs' if page.parent.xref_get_key(xref, 'Resources/ExtGState/H')[0] != 'null' else ''
    commands = ['q', opacity, '1 J 1 j', f'{width:.4f} w', f'{color} {operator}', f'{color} {operator.lower()}',
                f'[{dashes}] 0 d', f'{xy(shaft[0])} m {xy(shaft[1])} l S', '[] 0 d']
    for left, tip, right, filled in tips:
        commands.append(f"{xy(left)} m {xy(tip)} l {xy(right)} l " + ('h B' if filled else 'S'))
    page.parent.update_stream(xref, ('\n'.join([*commands, 'Q']) + '\n').encode('ascii'))
