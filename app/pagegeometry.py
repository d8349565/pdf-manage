"""Lossless, reversible page geometry edits in visible-page coordinates."""
import json
import math
import re

import pymupdf as p

from app.workspace import PageEdit

PT_PER_MM = 72 / 25.4
_NUMBER = re.compile(r'[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?')


def geometry_edit(store, ref, size=None, angle=0):
    """Size is the final visible width/height; positive angles turn clockwise."""
    store.require_operation([ref], 'content_edit')
    limitations = store._source(ref.source_id).geometry_limitations
    if limitations:
        raise ValueError('此文档包含' + '、'.join(limitations) + '，暂不支持调整页面或纠偏；原件未修改。')
    width, height = size or store.page_size(ref)
    if (not all(math.isfinite(v) for v in (width, height, angle))
            or not 10 <= width <= 14400 or not 10 <= height <= 14400 or abs(angle) > 10):
        raise ValueError('页面宽高须为 10～14400 磅，纠偏角度须在 ±10° 内。')
    rotation = (store.source_rotation(ref) + ref.rotation) % 360
    if rotation % 180:
        width, height = height, width
    return PageEdit('page_geometry', details=(('width', width), ('height', height), ('angle', angle)))


def _array(doc, xref, key, matrix, *, rectangle=False, scale=None):
    kind, value = doc.xref_get_key(xref, key)
    if kind == 'null':
        return
    if kind != 'array':
        raise ValueError(f'页面对象的 {key} 坐标无法安全调整。')
    values = [float(v) for v in _NUMBER.findall(value)]
    if scale is not None:
        result = [v * scale for v in values]
    elif rectangle:
        if len(values) != 4:
            raise ValueError('页面对象的边界无效。')
        result = list(p.Rect(values) * matrix)
    else:
        if len(values) % 2:
            raise ValueError('页面对象的坐标数量无效。')
        result = [v for a, b in zip(values[::2], values[1::2]) for v in p.Point(a, b) * matrix]
    iterator = iter(result)
    doc.xref_set_key(xref, key, _NUMBER.sub(lambda _: f'{next(iterator):.9f}', value))


def _scale_metadata(doc, xref, matrix, scale):
    # Keep the app's editing controls consistent with the transformed appearance.
    for key in ('ZhiXuText', 'ZhiXuImage'):
        kind, value = doc.xref_get_key(xref, key)
        if kind != 'string':
            continue
        settings = json.loads(value)
        if key == 'ZhiXuText':
            settings['fontsize'] *= scale
        else:
            center = p.Point(settings['cx'], settings['cy']) * matrix
            settings.update(cx=center.x, cy=center.y,
                            width=settings['width'] * scale, height=settings['height'] * scale)
        doc.xref_set_key(xref, key, p.get_pdf_str(json.dumps(settings)))
    kind, value = doc.xref_get_key(xref, 'ZhiXuSeam')
    if kind == 'string':
        settings = json.loads(value)
        settings['width'] *= scale
        settings['height'] *= scale
        doc.xref_set_key(xref, 'ZhiXuSeam', p.get_pdf_str(json.dumps(settings)))


def apply_geometry(page, edit):
    """Transform content streams and live object coordinates, without rasterizing."""
    options = dict(edit.details)
    width, height, angle = (options[k] for k in ('width', 'height', 'angle'))
    if (not all(math.isfinite(v) for v in (width, height, angle))
            or not 10 <= width <= 14400 or not 10 <= height <= 14400 or abs(angle) > 10):
        raise ValueError('页面尺寸或纠偏角度无效。')
    doc, rotation = page.parent, page.rotation
    objects = page.annot_xrefs()
    if angle and any(kind != p.PDF_ANNOT_LINK for _, kind, _ in objects):
        raise ValueError('此页含批注、印章或表单，请先纠偏再添加；本次未修改页面。')
    if any(kind not in range(0, 17) and kind not in (p.PDF_ANNOT_WIDGET, p.PDF_ANNOT_REDACT)
           for _, kind, _ in objects):
        raise ValueError('此页含暂不支持缩放的交互对象，本次未修改页面。')
    unit_kind, unit = doc.xref_get_key(page.xref, 'UserUnit')
    if unit_kind not in ('null', 'int', 'float') or unit_kind != 'null' and float(unit) != 1:
        raise ValueError('此页使用特殊页面单位，暂不支持调整尺寸或纠偏。')
    try:
        page.set_rotation(0)
        old = page.rect
        old_pdf = p.Matrix(page.transformation_matrix)
        turn = p.Matrix(angle)
        turned = old * turn
        scale = min(width / turned.width, height / turned.height)
        matrix = turn * p.Matrix(scale, scale)
        turned = old * matrix
        matrix *= p.Matrix(1, 0, 0, 1, (width - turned.width) / 2 - turned.x0,
                           (height - turned.height) / 2 - turned.y0)
        page.wrap_contents()
        contents = page.get_contents()
        page.set_mediabox(p.Rect(0, 0, width, height))
        raw = old_pdf * matrix * ~page.transformation_matrix
        clip = old * ~old_pdf
        prefix = ('q ' + ' '.join(f'{v:.9f}' for v in raw) + ' cm\n'
                  + f'{clip.x0:.9f} {clip.y0:.9f} {clip.width:.9f} {clip.height:.9f} re W n\n')
        streams = []
        for data in (prefix.encode('ascii'), b'\nQ\n'):
            xref = doc.get_new_xref()
            doc.update_object(xref, '<<>>')
            doc.update_stream(xref, data)
            streams.append(xref)
        doc.xref_set_key(page.xref, 'Contents', '[' + ' '.join(
            f'{xref} 0 R' for xref in (streams[0], *contents, streams[1])) + ']')
        for xref, kind, _ in objects:
            _array(doc, xref, 'Rect', raw, rectangle=True)
            for key in ('QuadPoints', 'Vertices', 'L', 'InkList', 'CL'):
                _array(doc, xref, key, raw)
            _array(doc, xref, 'RD', raw, scale=scale)
            border_kind, border = doc.xref_get_key(xref, 'BS')
            owner, prefix = (int(border.split()[0]), '') if border_kind == 'xref' else (xref, 'BS/')
            if border_kind != 'null':
                if border_kind == 'xref':
                    cloned = doc.get_new_xref()
                    doc.update_object(cloned, doc.xref_object(owner))
                    doc.xref_set_key(xref, 'BS', f'{cloned} 0 R')
                    owner = cloned
                value_kind, value = doc.xref_get_key(owner, prefix + 'W')
                if value_kind in ('int', 'float'):
                    doc.xref_set_key(owner, prefix + 'W', str(float(value) * scale))
            _array(doc, xref, 'Border', raw, scale=scale)
            da_kind, da = doc.xref_get_key(xref, 'DA')
            if da_kind == 'string':
                da = re.sub(r'([-+]?[\d.]+)\s+Tf\b',
                            lambda match: f'{float(match[1]) * scale:.6f} Tf', da)
                doc.xref_set_key(xref, 'DA', p.get_pdf_str(da))
            _scale_metadata(doc, xref, matrix, scale)
        return raw
    finally:
        page.set_rotation(rotation)


def restore_geometry_navigation(doc, snapshot, matrices):
    """Remap incoming destinations as well as links on transformed pages."""
    def destination(xref, original):
        if original.get('kind') != p.LINK_GOTO:
            return
        target = original.get('page', -1)
        value = dict(original)
        value['to'] = value.get('to', p.Point(0, 0)) * matrices.get(target, p.Identity)
        action = p.utils.getDestStr(doc.page_xref(target), value)
        if not action.startswith('/A'):
            raise ValueError('无法保留页面跳转目标，本次未保存。')
        doc.xref_set_key(xref, 'Dest', 'null')
        doc.xref_set_key(xref, 'A', action[2:])

    for index, links in snapshot['links'].items():
        current = doc[index].get_links()
        if len(current) != len(links):
            raise ValueError('页面链接数量发生变化，本次未保存。')
        for link, original in zip(current, links):
            destination(link['xref'], original)
    for current, original in zip(doc.get_toc(False), snapshot['toc']):
        destination(current[3]['xref'], original[3])
