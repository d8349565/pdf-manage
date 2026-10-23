"""One image split over page edges, kept as one editable stamp group."""
import base64
import json
import math
from functools import lru_cache

import pymupdf as p

from app.output import check_cancel
from app.pdfimages import validate_image
from app.workspace import PageEdit

SEAM_DATA = 'ZhiXuSeam'
SEAM_ASSET = 'ZhiXuSeamAsset'


def settings(page, annot):
    kind, value = page.parent.xref_get_key(annot.xref, SEAM_DATA)
    if kind != 'string':
        return None
    value = json.loads(value)
    if (not isinstance(value, dict) or value.get('version') != 1
            or not isinstance(value.get('group'), str) or not 1 <= len(value['group']) <= 128
            or not all(isinstance(value.get(key), (int, float)) and math.isfinite(value[key])
                       for key in ('width', 'height', 'position', 'rotation'))
            or value['width'] <= 0 or value['height'] <= 0 or not 0 <= value['position'] <= 1
            or value.get('edge') not in ('left', 'right', 'top', 'bottom')
            or not isinstance(value.get('count'), int) or not 2 <= value['count'] <= 10000
            or not isinstance(value.get('ordinal'), int) or not 0 <= value['ordinal'] < value['count']):
        raise ValueError('骑缝章信息无法读取，请先撤销当前操作或使用原文件。')
    return value


@lru_cache(maxsize=2)
def image_pdf(encoded):
    data = base64.b64decode(encoded, validate=True)
    width, height = validate_image(data)
    with p.open() as doc:
        page = doc.new_page(width=width, height=height)
        page.insert_image(page.rect, stream=data)
        return doc.tobytes(), width, height


def slice_rect(width, height, count, ordinal, edge, position, page_width, page_height):
    if (not all(math.isfinite(v) for v in (width, height, position))
            or width <= 0 or height <= 0 or not 0 <= position <= 1
            or not isinstance(count, int) or not 2 <= count <= 10000
            or not isinstance(ordinal, int) or not 0 <= ordinal < count
            or edge not in ('left', 'right', 'top', 'bottom')):
        raise ValueError('骑缝章尺寸、页数或位置无效；请至少选择两页。')
    vertical = edge in ('left', 'right')
    w, h = (width / count, height) if vertical else (width, height / count)
    if w > page_width + .001 or h > page_height + .001:
        raise ValueError('印章超出所选页面，请缩小印章尺寸。')
    x = (page_width - w if edge == 'right' else 0) if vertical else (page_width - w) * position
    y = (page_height - h) * position if vertical else (page_height - h if edge == 'bottom' else 0)
    clip = (ordinal / count, 0, (ordinal + 1) / count, 1) if vertical else (0, ordinal / count, 1, (ordinal + 1) / count)
    return p.Rect(x, y, x + w, y + h), clip


def apply_seam(page, edit):
    from app.pdfmarks import set_appearance
    group = edit.annotation_id
    existing = [annot for annot in page.annots() or () if (settings(page, annot) or {}).get('group') == group]
    for annot in existing:
        page.delete_annot(annot)
    if edit.kind == 'seam_remove':
        return
    options = dict(edit.details)
    data, iw, ih = image_pdf(options['image'])
    rotation = page.rotation
    try:
        page.set_rotation(options['rotation'])
        area, clip = slice_rect(options['width'], options['height'], options['count'],
            options['ordinal'], options['edge'], options['position'], page.rect.width, page.rect.height)
        area *= page.derotation_matrix
        clip = p.Rect(clip)
        clip = p.Rect(clip.x0 * iw, clip.y0 * ih, clip.x1 * iw, clip.y1 * ih)
        with p.open(stream=data, filetype='pdf') as source, p.open() as appearance:
            target = appearance.new_page(width=area.width, height=area.height)
            target.show_pdf_page(target.rect, source, 0, clip=clip, rotate=page.rotation)
            annot = page.add_stamp_annot(area)
            if annot.set_rect(area) is False:
                raise ValueError('无法放置骑缝章，本次未修改页面。')
            annot.set_info(title='纸序 PDF 骑缝章', content=f'骑缝章 · 第 {options["ordinal"] + 1}/{options["count"]} 片')
            doc = page.parent
            doc.xref_set_key(annot.xref, 'NM', p.get_pdf_str(f'{group}-{options["ordinal"]}'))
            metadata = {key: value for key, value in options.items() if key != 'image'}
            metadata.update(version=1, group=group)
            doc.xref_set_key(annot.xref, SEAM_DATA, p.get_pdf_str(json.dumps(metadata)))
            # Keep the user's original PNG/JPEG, including its alpha channel,
            # available for editing after saving without the original file path.
            assets = getattr(doc, '_zhixu_seam_assets', None)
            if assets is None:
                assets = doc._zhixu_seam_assets = {}
            asset = assets.get(options['image'])
            if asset is None:
                asset = doc.get_new_xref()
                doc.update_object(asset, '<<>>')
                doc.update_stream(asset, base64.b64decode(options['image'], validate=True))
                assets[options['image']] = asset
            doc.xref_set_key(annot.xref, SEAM_ASSET, f'{asset} 0 R')
            set_appearance(page, annot, appearance)
    finally:
        page.set_rotation(rotation)


def list_groups(store, pages, progress=None, cancel=None):
    groups = {}
    for index, ref in enumerate(pages):
        check_cancel(cancel)
        with store._render_page(ref, cancel) as page:
            for annot in page.annots() or ():
                value = settings(page, annot)
                if value is None:
                    continue
                group = value['group']
                if group not in groups:
                    kind, asset = page.parent.xref_get_key(annot.xref, SEAM_ASSET)
                    if kind != 'xref':
                        raise ValueError('骑缝章图片缺失，无法调整。')
                    data = page.parent.xref_stream(int(asset.split()[0]))
                    validate_image(data)
                    groups[group] = dict(value, image=base64.b64encode(data).decode('ascii'), members=[])
                groups[group]['members'].append(index)
        if progress:
            progress(index + 1, len(pages), '正在查找已有骑缝章')
    return groups


def prepare_group(store, pages, indices, options, group, *, previous=(), remove=False, progress=None, cancel=None):
    indices = sorted(set(indices))
    ordinals = {row: index for index, row in enumerate(indices)}
    if not remove and len(indices) < 2:
        raise ValueError('骑缝章需要至少两页；单页请使用插入图片。')
    changes = {}
    targets = sorted(set(previous) | set(indices))
    for step, row in enumerate(targets):
        check_cancel(cancel)
        ref = pages[row]
        store.require_operation([ref], 'content_edit')
        if row in ordinals and not remove:
            details = dict(options, count=len(indices), ordinal=ordinals[row],
                           rotation=(store.source_rotation(ref) + ref.rotation) % 360)
            edit = PageEdit('seam_stamp', annotation_id=group, details=tuple(details.items()))
        else:
            edit = PageEdit('seam_remove', annotation_id=group)
        candidate = ref.edited(edit)
        with store._render_page(candidate, cancel):
            pass
        changes[row] = candidate
        if progress:
            progress(step + 1, len(targets), f'已准备 {step + 1} / {len(targets)} 页')
    check_cancel(cancel)
    return changes
