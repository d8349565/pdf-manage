"""Bounded, offline OFD import for ordinary invoices and receipts.

The original is never written. PDF operators retain object transforms and clips;
unsupported visible content fails explicitly instead of producing a partial invoice.
"""
from __future__ import annotations

import io
import math
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET

from app.output import check_cancel
from app.pdfio import PdfError

MM = 72 / 25.4
MAX_RESOURCE = 64 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024


def tag(element):
    return element.tag.rsplit('}', 1)[-1]


def child(element, name):
    return next((e for e in element if tag(e) == name), None) if element is not None else None


def children(element, name):
    return [e for e in element if tag(e) == name] if element is not None else []


def content(element, name):
    node = child(element, name)
    return (node.text or '').strip() if node is not None else ''


def numbers(value, count=None):
    result = [float(v) for v in value.split()]
    if (count is not None and len(result) != count) or any(not math.isfinite(v) or abs(v) > 1e7 for v in result):
        raise PdfError('OFD 的坐标或绘制参数无效。')
    return result


def num(value):
    if not math.isfinite(value) or abs(value) > 1e9:
        raise PdfError('OFD 的绘制范围过大。')
    return f'{value:.7f}'.rstrip('0').rstrip('.') or '0'


def op(values, operator):
    return ' '.join(num(v) for v in values) + ' ' + operator


def location(base, value):
    value = value.replace('\\', '/')
    path = posixpath.normpath(value.lstrip('/') if value.startswith('/') else posixpath.join(posixpath.dirname(base), value))
    if path == '..' or path.startswith('../') or ':' in path or '\x00' in path:
        raise PdfError('OFD 包内资源路径无效。')
    return path


def deltas(value, limit):
    tokens = value.split()
    result = []
    i = 0
    while i < len(tokens):
        if tokens[i] == 'g':
            if i + 2 >= len(tokens):
                raise PdfError('OFD 的文字间距数据不完整。')
            count = int(tokens[i + 1])
            v = numbers(tokens[i + 2], 1)[0]
            i += 3
        else:
            count, v = 1, numbers(tokens[i], 1)[0]
            i += 1
        if count < 0 or len(result) + count > max(limit, 1):
            raise PdfError('OFD 的文字间距数量超出范围。')
        result.extend([v] * count)
    return result


def arc(start, values):
    """SVG/OFD endpoint arc to cubic segments (at most a quarter turn each)."""
    rx, ry, angle, large, sweep, x2, y2 = values
    x1, y1 = start
    rx, ry = abs(rx), abs(ry)
    if large not in (0, 1) or sweep not in (0, 1):
        raise PdfError('OFD 圆弧参数无效。')
    if (x1, y1) == (x2, y2):
        return []
    if not rx or not ry:
        return [op((x2, y2), 'l')]
    phi = math.radians(angle % 360)
    co, si = math.cos(phi), math.sin(phi)
    xp = co * (x1 - x2) / 2 + si * (y1 - y2) / 2
    yp = -si * (x1 - x2) / 2 + co * (y1 - y2) / 2
    scale = math.sqrt(max(1, (xp / rx) ** 2 + (yp / ry) ** 2))
    rx, ry = rx * scale, ry * scale
    denominator = rx * rx * yp * yp + ry * ry * xp * xp
    factor = math.sqrt(max(0, (rx * rx * ry * ry - denominator) / denominator))
    if large == sweep:
        factor = -factor
    cxp, cyp = factor * rx * yp / ry, -factor * ry * xp / rx
    cx = co * cxp - si * cyp + (x1 + x2) / 2
    cy = si * cxp + co * cyp + (y1 + y2) / 2
    theta = math.atan2((yp - cyp) / ry, (xp - cxp) / rx)
    end = math.atan2((-yp - cyp) / ry, (-xp - cxp) / rx)
    delta = (end - theta) % (2 * math.pi)
    if not sweep:
        delta -= 2 * math.pi
    segments = max(1, math.ceil(abs(delta) / (math.pi / 2)))
    step = delta / segments
    def point(t):
        return (cx + co * rx * math.cos(t) - si * ry * math.sin(t),
                cy + si * rx * math.cos(t) + co * ry * math.sin(t))
    def tangent(t):
        return (-co * rx * math.sin(t) - si * ry * math.cos(t),
                -si * rx * math.sin(t) + co * ry * math.cos(t))
    commands = []
    for _ in range(segments):
        end = theta + step
        p, q, u, v = point(theta), point(end), tangent(theta), tangent(end)
        k = 4 / 3 * math.tan(step / 4)
        commands.append(op((p[0] + k*u[0], p[1] + k*u[1], q[0] - k*v[0], q[1] - k*v[1], *q), 'c'))
        theta = end
    return commands


def path_commands(value):
    tokens = re.findall(r'[A-Za-z]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?', value)
    if re.sub(r'[\s,]', '', ''.join(tokens)) != re.sub(r'[\s,]', '', value) or len(tokens) > 500000:
        raise PdfError('OFD 路径数据无效或过大。')
    commands, current, start, i = [], (0, 0), (0, 0), 0
    lengths = {'S': 2, 'M': 2, 'L': 2, 'Q': 4, 'B': 6, 'A': 7, 'C': 0}
    while i < len(tokens):
        command = tokens[i]
        if command not in lengths:
            raise PdfError('此 OFD 包含暂不支持的路径指令。')
        size = lengths[command]
        values = numbers(' '.join(tokens[i+1:i+1+size]), size)
        i += size + 1
        if command in ('S', 'M'):
            current = start = tuple(values)
            commands.append(op(values, 'm'))
        elif command == 'L':
            current = tuple(values)
            commands.append(op(values, 'l'))
        elif command == 'B':
            current = tuple(values[-2:])
            commands.append(op(values, 'c'))
        elif command == 'Q':
            qx, qy, x, y = values
            commands.append(op((current[0]+2*(qx-current[0])/3, current[1]+2*(qy-current[1])/3,
                                x+2*(qx-x)/3, y+2*(qy-y)/3, x, y), 'c'))
            current = (x, y)
        elif command == 'A':
            commands.extend(arc(current, values))
            current = tuple(values[-2:])
        else:
            commands.append('h')
            current = start
    return commands


class Importer:
    def __init__(self, source, progress=None, cancel=None):
        self.archive = zipfile.ZipFile(io.BytesIO(source) if isinstance(source, bytes) else source)
        entries = self.archive.infolist()
        if len(entries) > 10000 or sum(e.file_size for e in entries) > MAX_TOTAL or any(e.file_size > MAX_RESOURCE for e in entries):
            self.archive.close()
            raise PdfError('OFD 解压后的资源过大，请使用较小的文档。')
        self.entries = {e.filename: e for e in entries}
        if len(self.entries) != len(entries):
            self.archive.close()
            raise PdfError('OFD 包含重复资源名称。')
        self.progress, self.cancel = progress, cancel
        self.resources, self.fonts, self.images, self.states = {}, {}, {}, {}
        self.warnings = set()
        self.object_count = 0
        self.read_bytes = 0

    def read(self, path):
        check_cancel(self.cancel)
        if path not in self.entries:
            raise PdfError('OFD 缺少所引用的资源，已停止转换。')
        self.read_bytes += self.entries[path].file_size
        if self.read_bytes > MAX_TOTAL * 4:
            raise PdfError('OFD 重复引用的资源过多，已停止转换。')
        return self.archive.read(path)

    def xml(self, path):
        data = self.read(path)
        # DTD/entities are unnecessary for OFD; reject before allocating an XML tree.
        if len(data) > 16 * 1024 * 1024 or re.search(br'<!\s*(DOCTYPE|ENTITY)', data.replace(b'\x00', b''), re.I):
            raise PdfError('OFD 的 XML 资源过大或包含不支持的实体声明。')
        root = ET.fromstring(data)
        if sum(1 for _ in root.iter()) > 200000:
            raise PdfError('OFD 页面结构过于复杂。')
        return root

    def load_resources(self, root, base):
        for name in ('PublicRes', 'DocumentRes', 'PageRes'):
            for ref in children(root, name):
                path = location(base, (ref.text or '').strip())
                resource = self.xml(path)
                folder = location(path, resource.get('BaseLoc', '.'))
                for group in resource:
                    for item in group:
                        identifier = item.get('ID')
                        if identifier:
                            self.resources[identifier] = (item, folder + '/')

    def style(self, element, inherited=None, visited=()):
        result = dict(inherited or {})
        identifier = element.get('DrawParam') or element.get('Relative')
        if identifier:
            if identifier in visited or len(visited) > 32 or identifier not in self.resources:
                raise PdfError('OFD 绘制参数引用无效。')
            result = self.style(self.resources[identifier][0], result, (*visited, identifier))
        result.update(element.attrib)
        for name in ('FillColor', 'StrokeColor'):
            value = child(element, name)
            if value is not None:
                result[name] = value
        return result

    def color(self, element, stroke=False):
        if element is None:
            return '0 G' if stroke else '0 g'
        if len(element):
            raise PdfError('此 OFD 使用暂不支持的渐变或底纹。')
        space = self.resources.get(element.get('ColorSpace'), (None, ''))[0]
        value = element.get('Value')
        if value is None and element.get('Index') is not None and space is not None:
            palette = child(space, 'Palette')
            entries = list(palette) if palette is not None else []
            index = int(element.get('Index'))
            if not 0 <= index < len(entries):
                raise PdfError('OFD 颜色索引无效。')
            value = entries[index].get('Value') or entries[index].text
        if not value:
            raise PdfError('OFD 颜色数据不完整。')
        bits = int(space.get('BitsPerComponent', '8')) if space is not None else 8
        if bits not in (1, 2, 4, 8, 16):
            raise PdfError('OFD 颜色位深不受支持。')
        values = [v / (2**bits - 1) for v in numbers(value)]
        operator = {1: 'g', 3: 'rg', 4: 'k'}.get(len(values))
        if operator is None or any(not 0 <= v <= 1 for v in values):
            raise PdfError('OFD 颜色值无效。')
        return op(values, operator.upper() if stroke else operator)

    def graphics(self, page, style):
        fill, stroke = style.get('FillColor'), style.get('StrokeColor')
        alpha = numbers(style.get('Alpha', '255'), 1)[0] / 255
        fa = alpha * (numbers(fill.get('Alpha', '255'), 1)[0] / 255 if fill is not None else 1)
        sa = alpha * (numbers(stroke.get('Alpha', '255'), 1)[0] / 255 if stroke is not None else 1)
        blend = style.get('BlendMode', 'Normal')
        if blend not in ('Normal', 'Multiply', 'Screen', 'Overlay', 'Darken', 'Lighten') or not (0 <= fa <= 1 and 0 <= sa <= 1):
            raise PdfError('此 OFD 的透明混合参数暂不支持。')
        key = (page.number, fa, sa, blend)
        if key not in self.states:
            name = 'GS' + str(len(self.states))
            doc = page.parent
            xref = doc.get_new_xref()
            doc.update_object(xref, f'<< /Type /ExtGState /ca {num(fa)} /CA {num(sa)} /BM /{blend} >>')
            kind, value = doc.xref_get_key(page.xref, 'Resources')
            resource_xref = int(value.split()[0]) if kind == 'xref' else page.xref
            prefix = '' if kind == 'xref' else 'Resources/'
            if doc.xref_get_key(resource_xref, prefix + 'ExtGState')[0] == 'null':
                doc.xref_set_key(resource_xref, prefix + 'ExtGState', '<<>>')
            doc.xref_set_key(resource_xref, prefix + 'ExtGState/' + name, f'{xref} 0 R')
            self.states[key] = name
        return '/' + self.states[key] + ' gs'

    def font(self, page, identifier):
        import pymupdf as p
        if identifier not in self.fonts:
            if len(self.fonts) >= 64:
                raise PdfError('OFD 使用的字体过多，暂不支持打开。')
            if identifier not in self.resources:
                raise PdfError('OFD 缺少字体定义。')
            element, folder = self.resources[identifier]
            filename = content(element, 'FontFile')
            buffer = self.read(location(folder, filename)) if filename else None
            if not buffer:
                from app.systemfonts import installed_font_buffer
                flags = (16 if element.get('Bold') == 'true' else 0) | (2 if element.get('Italic') == 'true' else 0)
                for family in (element.get('FontName', ''), element.get('FamilyName', '')):
                    buffer = installed_font_buffer(family, flags)
                    if buffer:
                        break
            if not buffer:
                buffer = p.Font('cjk').buffer
                self.warnings.add('部分字体未提供或未安装，已使用替代字体；请核对文字外观。')
            self.fonts[identifier] = (p.Font(fontbuffer=buffer), 'OF' + str(len(self.fonts)))
        font, name = self.fonts[identifier]
        page.insert_font(fontname=name, fontbuffer=font.buffer)
        return font, name

    def text(self, page, element, style):
        codes = children(element, 'TextCode')
        if not any(e.text for e in codes):
            return []
        font, name = self.font(page, element.get('Font'))
        size = numbers(element.get('Size', ''), 1)[0]
        if size <= 0:
            raise PdfError('OFD 字号无效。')
        if element.get('ReadDirection', '0') != '0' or element.get('CharDirection', '0') != '0':
            raise PdfError('此 OFD 使用暂不支持的竖排文字。')
        glyphs = {}
        for transform in children(element, 'CGTransform'):
            start = int(transform.get('CodePosition', '0'))
            count = int(transform.get('CodeCount', '1'))
            ids = [int(v) for v in content(transform, 'Glyphs').split()]
            if start < 0 or count != len(ids) or count != int(transform.get('GlyphCount', str(len(ids)))):
                raise PdfError('此 OFD 使用暂不支持的组合字形。')
            glyphs.update({start+i: gid for i, gid in enumerate(ids)})
        fill = style.get('Fill', 'true') != 'false'
        stroke = style.get('Stroke', 'false') == 'true'
        mode = 2 if fill and stroke else 0 if fill else 1 if stroke else 3
        result = [self.color(style.get('FillColor')), self.color(style.get('StrokeColor'), True),
                  op(numbers(style.get('LineWidth', '0.353'), 1), 'w'), f'BT /{name} {num(size)} Tf {mode} Tr']
        x0 = y0 = None
        offset = 0
        hscale = numbers(element.get('HScale', '1'), 1)[0]
        italic = 0.25 if element.get('Italic') == 'true' else 0
        for code in codes:
            text = code.text or ''
            x0 = numbers(code.get('X'), 1)[0] if code.get('X') is not None else x0
            y0 = numbers(code.get('Y'), 1)[0] if code.get('Y') is not None else y0
            if not text:
                continue
            if x0 is None or y0 is None or len(text) > 100000:
                raise PdfError('OFD 文字坐标缺失或文本过长。')
            dx, dy = deltas(code.get('DeltaX', ''), len(text)), deltas(code.get('DeltaY', ''), len(text))
            x, y = x0, y0
            for i, character in enumerate(text):
                if i:
                    x += dx[min(i-1, len(dx)-1)] if dx else 0
                    y += dy[min(i-1, len(dy)-1)] if dy else 0
                gid = glyphs.get(offset+i, font.has_glyph(ord(character)))
                if not gid and character.isspace():
                    continue
                if not 0 < gid < font.glyph_count:
                    raise PdfError('OFD 字体缺少所需字形，无法完整显示。')
                if offset+i in glyphs and gid != font.has_glyph(ord(character)):
                    self.warnings.add('部分文字使用专用字形，已保留外观；复制和搜索结果可能不完整。')
                result.extend([op((hscale, 0, italic, -1, x, y), 'Tm'), f'<{gid:04x}> Tj'])
            offset += len(text)
        return [*result, 'ET']

    def image(self, page, element):
        import pymupdf as p
        from PIL import Image
        identifier = element.get('ResourceID')
        key = page.number, identifier
        if key not in self.images:
            if identifier not in self.resources:
                raise PdfError('OFD 缺少图片资源。')
            resource, folder = self.resources[identifier]
            data = self.read(location(folder, content(resource, 'MediaFile')))
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > 40_000_000:
                    raise PdfError('OFD 图片过大或格式暂不支持。')
            xref = page.insert_image(p.Rect(0, 0, 1, 1), stream=data)
            self.images[key] = next(item[7] for item in page.get_images() if item[0] == xref)
        return ['1 0 0 -1 0 1 cm', '/' + self.images[key] + ' Do']

    def objects(self, page, container, inherited=None, depth=0):
        if depth > 32:
            raise PdfError('OFD 页面嵌套过深。')
        result = []
        inherited = self.style(container, inherited)
        for element in container:
            check_cancel(self.cancel)
            name = tag(element)
            if name in ('Actions', 'Parameters'):
                continue  # Never execute document actions or attachments.
            if name == 'PageBlock':
                result.extend(self.objects(page, element, inherited, depth+1))
                continue
            if element.get('Visible', 'true') == 'false':
                continue
            if name not in ('TextObject', 'PathObject', 'ImageObject'):
                raise PdfError(f'此 OFD 包含暂不支持的页面内容（{name}），未生成不完整副本。')
            self.object_count += 1
            if self.object_count > 100000:
                raise PdfError('OFD 图元数量过多。')
            if child(element, 'Clips') is not None:
                raise PdfError('此 OFD 使用暂不支持的复杂裁剪，未生成不完整副本。')
            if name == 'ImageObject' and (element.get('Mask') or element.get('Substitution')):
                raise PdfError('此 OFD 使用暂不支持的图片蒙版。')
            x, y, w, h = numbers(element.get('Boundary', ''), 4)
            if w < 0 or h < 0:
                raise PdfError('OFD 图元边界无效。')
            style = self.style(element, inherited)
            result += ['q', op((x, y, w, h), 're'), 'W n', op((1, 0, 0, 1, x, y), 'cm')]
            default = f'{w} 0 0 {h} 0 0' if name == 'ImageObject' else '1 0 0 1 0 0'
            result += [op(numbers(element.get('CTM', default), 6), 'cm'), self.graphics(page, style)]
            if name == 'TextObject':
                result += self.text(page, element, style)
            elif name == 'ImageObject':
                result += self.image(page, element)
            else:
                result += [self.color(style.get('FillColor')), self.color(style.get('StrokeColor'), True),
                           op(numbers(style.get('LineWidth', '0.353'), 1), 'w')]
                result += [str({'Butt': 0, 'Round': 1, 'Square': 2}.get(style.get('Cap'), 0)) + ' J',
                           str({'Miter': 0, 'Round': 1, 'Bevel': 2}.get(style.get('Join'), 0)) + ' j']
                if style.get('DashPattern'):
                    dash = numbers(style['DashPattern'])
                    if any(v < 0 for v in dash) or not any(dash):
                        raise PdfError('OFD 虚线参数无效。')
                    result.append('[' + ' '.join(map(num, dash)) + '] ' + num(numbers(style.get('DashOffset', '0'), 1)[0]) + ' d')
                result += path_commands(content(element, 'AbbreviatedData'))
                fill, stroke = style.get('Fill', 'false') == 'true', style.get('Stroke', 'true') != 'false'
                paint = 'B' if fill and stroke else 'f' if fill else 'S' if stroke else 'n'
                result.append(paint + ('*' if fill and style.get('Rule') == 'Even-Odd' else ''))
            result.append('Q')
        return result

    def layers(self, page, root):
        layers = children(child(root, 'Content'), 'Layer')
        order = {'Background': 0, 'Body': 1, 'Foreground': 2}
        return [command for layer in sorted(layers, key=lambda e: order.get(e.get('Type', 'Body'), 1))
                for command in self.objects(page, layer)]

    def annotations(self, page, path):
        result = []
        for annot in children(self.xml(path), 'Annot'):
            if annot.get('Visible', 'true') == 'false':
                continue
            appearance = child(annot, 'Appearance')
            if appearance is None:
                raise PdfError('OFD 注释缺少外观，无法完整显示。')
            x, y, w, h = numbers(appearance.get('Boundary', ''), 4)
            result += ['q', op((x, y, w, h), 're'), 'W n', op((1, 0, 0, 1, x, y), 'cm'),
                       *self.objects(page, appearance), 'Q']
        return result

    def convert(self):
        import pymupdf as p
        doc = p.open()
        try:
            if self.progress:
                self.progress(0, 0, '正在读取 OFD 页面与资源')
            entry = self.xml('OFD.xml')
            bodies = children(entry, 'DocBody')
            if len(bodies) != 1:
                raise PdfError('暂不支持包含多个文档的 OFD 文件包。')
            body = bodies[0]
            root_path = location('OFD.xml', content(body, 'DocRoot'))
            root = self.xml(root_path)
            permissions = child(root, 'Permissions')
            if permissions is not None:
                raise PdfError('此 OFD 包含使用权限声明，暂不支持转换；请使用原阅读器查看。')
            if content(body, 'Signatures'):
                raise PdfError('此 OFD 含数字签名，当前版本尚不支持完整转换签章外观；请使用原阅读器查看。')
            common = child(root, 'CommonData')
            self.load_resources(common, root_path)
            templates = {e.get('ID'): location(root_path, e.get('BaseLoc', '')) for e in children(common, 'TemplatePage')}
            refs = children(child(root, 'Pages'), 'Page')
            if not 1 <= len(refs) <= 1000:
                raise PdfError('OFD 没有页面或超过 1000 页。')
            annots = {}
            annotation_path = content(root, 'Annotations')
            if annotation_path:
                path = location(root_path, annotation_path)
                for ref in children(self.xml(path), 'Page'):
                    annots.setdefault(ref.get('PageID'), []).append(location(path, content(ref, 'FileLoc')))
            known_ids = {e.get('ID') for e in refs}
            orphan_annots = set(annots) - known_ids
            # Some invoice producers number annotation pages from 1 rather than Page.ID.
            # Only accept the unambiguous single-page form; never guess a multi-page target.
            if orphan_annots:
                if len(refs) == 1 and orphan_annots == {'1'}:
                    annots.setdefault(refs[0].get('ID'), []).extend(annots.pop('1'))
                else:
                    raise PdfError('OFD 注释引用了不存在的页面。')
            global_resources = self.resources.copy()
            for index, ref in enumerate(refs):
                check_cancel(self.cancel)
                if self.progress:
                    self.progress(index, len(refs), f'正在转换 OFD · 第 {index+1} / {len(refs)} 页')
                path = location(root_path, ref.get('BaseLoc', ''))
                page_root = self.xml(path)
                self.resources = global_resources.copy()
                self.load_resources(page_root, path)
                area = content(child(page_root, 'Area'), 'PhysicalBox') or content(child(common, 'PageArea'), 'PhysicalBox')
                x, y, w, h = numbers(area, 4)
                if not 0 < w <= 5000 or not 0 < h <= 5000:
                    raise PdfError('OFD 页面尺寸无效或过大。')
                page = doc.new_page(width=w*MM, height=h*MM)
                commands = ['q', op((MM, 0, 0, -MM, -x*MM, (h+y)*MM), 'cm')]
                page_templates = children(page_root, 'Template')
                orders = ('Background', 'Body', 'Foreground')
                if any(e.get('ZOrder', 'Background') not in orders for e in page_templates) or any(
                        e.get('Type', 'Body') not in orders for e in children(child(page_root, 'Content'), 'Layer')):
                    raise PdfError('OFD 图层顺序无效。')
                for order in ('Background', 'Body', 'Foreground'):
                    for template in page_templates:
                        if template.get('ZOrder', 'Background') != order:
                            continue
                        template_path = templates.get(template.get('TemplateID'))
                        if not template_path:
                            raise PdfError('OFD 缺少模板页。')
                        template_root = self.xml(template_path)
                        resources = self.resources.copy()
                        self.load_resources(template_root, template_path)
                        commands += self.layers(page, template_root)
                        self.resources = resources
                    for layer in children(child(page_root, 'Content'), 'Layer'):
                        if layer.get('Type', 'Body') == order:
                            commands += self.objects(page, layer)
                for annotation in annots.get(ref.get('ID'), []):
                    commands += self.annotations(page, annotation)
                commands.append('Q')
                xref = doc.get_new_xref()
                doc.update_object(xref, '<<>>')
                doc.update_stream(xref, '\n'.join(commands).encode('ascii'))
                page.set_contents(xref)
            if self.progress:
                self.progress(0, 0, '正在整理 PDF 工作副本')
            check_cancel(self.cancel)
            doc.subset_fonts()
            data = doc.tobytes(garbage=4, deflate=True)
            if len(data) > MAX_TOTAL:
                raise PdfError('转换后的 PDF 过大，已停止打开。')
            check_cancel(self.cancel)
            if self.progress:
                self.progress(len(refs), len(refs), 'OFD 转换完成 · 原文件保留')
            return data, sorted(self.warnings)
        finally:
            doc.close()
            self.archive.close()


def convert_ofd(source, progress=None, cancel=None):
    try:
        return Importer(source, progress, cancel).convert()
    except (zipfile.BadZipFile, ET.ParseError, ValueError, KeyError, RuntimeError, OSError) as exc:
        # Do not expose invoice text or archive paths in error dialogs/logs.
        raise PdfError('OFD 文件损坏或包含暂不支持的内容，原文件未修改。') from exc
