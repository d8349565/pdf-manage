"""区域摘录：按临时选区、矩形批注或页边距导出矢量 PDF。

参考语义（aardio 版 PDF 矩形注释裁剪工具）：
- 读取页面的「矩形批注」(Square) 作为裁剪标记；隐藏的批注跳过；
- 每个标记矩形对应一个输出区域，矢量保留、不转图片；
- 排版两种：独立分页（一区域一页）/ 拼版排版（A4 流式排列，便于打印）；
- 扩展模式：同一页的批注合并为一个区域；
- 页边距来源：不用批注，直接按 36 磅内缩裁剪整页；
- 输出只包含页面内容与批注外观（标记本身除外），原件保留。
"""
from __future__ import annotations

from contextlib import ExitStack
import math
import os
from pathlib import Path
import tempfile

import pymupdf as p

from app.output import check_cancel, commit_file
from app.pdfedits import iter_annotations
from app.pdfmarks import is_watermark
from app.workspace import parse_page_range

MARGIN = 36.0
FLOW_PAGE = (595.276, 841.89)
FLOW_GAP = 12.0


class AnnotationCropError(ValueError):
    """批注裁剪的输入问题，直接展示给用户。"""


def needs_output_protection(store, pages):
    for ref in pages:
        source = store._source(ref.source_id)
        if source.safety.encrypted or source.needs_encryption_choice:
            return True
    return False


def is_marker(page, annot):
    hidden = p.PDF_ANNOT_IS_HIDDEN | p.PDF_ANNOT_IS_INVISIBLE | p.PDF_ANNOT_IS_NO_VIEW
    return (annot.type[0] == p.PDF_ANNOT_SQUARE and not annot.flags & hidden
            and not is_watermark(page, annot))


def collect_markers(page):
    """页面上的矩形批注标记，返回未旋转坐标系的 Rect 列表。"""
    markers = []
    for annot in iter_annotations(page):
        if not is_marker(page, annot):
            continue
        rect = p.Rect(annot.rect)
        if rect.is_empty or rect.width <= 0 or rect.height <= 0:
            continue
        markers.append(rect)
    return markers


def visible_matrix(page, ref):
    """未旋转批注坐标 → 用户可见坐标（含页面 /Rotate 与工作区旋转）。"""
    extra = p.Matrix(1, 1).prerotate(ref.rotation)
    rotated = page.rect * extra
    extra *= p.Matrix(1, 0, 0, 1, -rotated.x0, -rotated.y0)
    return page.rotation_matrix * extra


def layout_flow(regions):
    """拼版排版：区域流式排到 A4 上，返回 [(页宽, 页高, [放置项])]。

    放置项 = (区域序号, 目标 Rect)。小区域原样大小，大区域缩小到可用范围。
    注意 PyMuPDF 页面坐标是左上原点、y 向下：从页顶向下逐行排列。
    """
    page_w, page_h = FLOW_PAGE
    margin, gap = MARGIN, FLOW_GAP
    avail_w, avail_h = page_w - 2 * margin, page_h - 2 * margin
    pages = []
    current = []
    cursor_x, cursor_y = margin, margin
    row_height = 0.0
    for index, region in enumerate(regions):
        width, height = region.width, region.height
        scale = min(1.0, avail_w / width, avail_h / height)
        item_w, item_h = width * scale, height * scale
        if cursor_x + item_w > page_w - margin + .01 and cursor_x > margin:
            cursor_x = margin
            cursor_y += row_height + gap
            row_height = 0.0
        if current and cursor_y + item_h > page_h - margin + .01:
            pages.append((page_w, page_h, current))
            current = []
            cursor_x, cursor_y = margin, margin
            row_height = 0.0
        target = p.Rect(cursor_x, cursor_y, cursor_x + item_w, cursor_y + item_h)
        current.append((index, target))
        cursor_x += item_w + gap
        row_height = max(row_height, item_h)
    if current or not pages:
        pages.append((page_w, page_h, current))
    return pages


def layout_per_region(regions):
    """独立分页：一区域一页，页面尺寸 = 区域尺寸。"""
    return [(region.width, region.height, [(index, p.Rect(0, 0, region.width, region.height))])
            for index, region in enumerate(regions)]


def export_annotation_crop(store, pages, options, dest, progress=None, cancel=None):
    """执行批注裁剪导出。

    options: dict(source='regions'|'annot'|'margin', layout='flow'|'per', expand=bool,
                  range='1-3,5', regions={页面序号: [可见页面坐标 Rect]})
    返回汇总 dict(regions=区域数, out_pages=输出页数, path=dest)。
    """
    pages = tuple(pages)
    total_pages = len(pages)
    if not total_pages:
        raise AnnotationCropError("没有打开的页面。")
    source = options.get("source", "annot")
    layout = options.get("layout", "flow")
    expand = bool(options.get("expand"))
    if source not in ("regions", "annot", "margin"):
        raise AnnotationCropError("未知的裁剪来源。")
    if layout not in ("flow", "per"):
        raise AnnotationCropError("未知的排版方式。")

    text = (options.get("range") or "").strip()
    if text:
        indices = parse_page_range(text, total_pages)
    else:
        indices = list(range(total_pages))
    if not indices:
        raise AnnotationCropError("页码范围没有匹配到任何页面。")
    if source == 'regions':
        indices = [index for index in indices if options.get('regions', {}).get(index)]
        if not indices:
            raise AnnotationCropError('请先在需要的页面上画出摘录区域。')
    dest = Path(dest).resolve()
    if dest.suffix.lower() != '.pdf' or not dest.parent.is_dir():
        raise AnnotationCropError('请选择已有文件夹中的 PDF 输出位置。')
    if store.is_source_destination(str(dest)):
        raise AnnotationCropError('区域摘录不能覆盖已打开的原文件，请另存新文件。')
    expected = store._check_destination(str(dest))
    chosen = [pages[index] for index in indices]
    for ref in chosen:
        while not store.prepare_safety_step(ref):
            check_cancel(cancel)
    store.require_operation(chosen, "copy")
    encryption = options.get('encryption')
    if (needs_output_protection(store, chosen)
            and encryption is None and not options.get('unencrypted_confirmed')):
        raise AnnotationCropError('来源文档有密码保护，请先选择新 PDF 的输出保护方式。')

    def report(current, message):
        if progress is not None:
            progress(current, 100, message)
        check_cancel(cancel)

    # 阶段一：逐页归一化（消除旋转、移除标记批注、固化其余批注外观）并收集区域
    normalized = []      # 每个有区域的被选页一个单页文档
    region_refs = []     # (normalized 序号, 可见坐标 Rect)
    temporary = None
    try:
        with ExitStack() as stack:
            for position, index in enumerate(indices):
                report(round(45 * position / len(indices)), f"正在读取第 {index + 1} 页…")
                ref = pages[index]
                markers = []
                with p.open() as copy:
                    with store._render_page(ref, cancel) as page:
                        copy.insert_pdf(page.parent, from_page=page.number, to_page=page.number,
                                        links=False, annots=True, widgets=True)
                        rotation = (page.rotation + ref.rotation) % 360
                        if source == "annot":
                            markers = collect_markers(page)
                            # 批注坐标从未旋转空间换到可见空间
                            matrix = visible_matrix(page, ref)
                            markers = [marker * matrix for marker in markers]
                    # 标记批注只用于裁剪，不进入输出
                    copied = copy[0]
                    if source == 'annot':
                        for annot in [item for item in iter_annotations(copied) if is_marker(copied, item)]:
                            copied.delete_annot(annot)
                    # 与 split_page 相同的旋转归一化：先固化批注外观，再统一方向
                    copy[0].set_rotation(rotation)
                    copy.bake(annots=True, widgets=True)
                    width, height = copy[0].rect.width, copy[0].rect.height
                    copy[0].set_rotation(0)
                    visible_rect = p.Rect(0, 0, width, height)
                    if source == 'margin':
                        markers = [p.Rect(MARGIN, MARGIN, max(MARGIN, width - MARGIN),
                                          max(MARGIN, height - MARGIN))]
                    elif source == 'regions':
                        markers = options.get('regions', {}).get(index, ())
                    page_regions = []
                    for marker in markers:
                        area = p.Rect(marker)
                        if not all(math.isfinite(value) for value in area):
                            raise AnnotationCropError(f'第 {index + 1} 页的选区坐标无效。')
                        area &= visible_rect
                        if not area.is_empty and area.width >= 1 and area.height >= 1:
                            page_regions.append(area)
                    if expand and page_regions:
                        merged = page_regions[0]
                        for area in page_regions[1:]:
                            merged |= area
                        page_regions = [merged]
                    if not page_regions:
                        continue
                    normalized_doc = stack.enter_context(p.open())
                    visible_page = normalized_doc.new_page(width=width, height=height)
                    if copy[0].get_contents():
                        visible_page.show_pdf_page(visible_page.rect, copy, 0, rotate=-rotation)
                    for area in page_regions:
                        region_refs.append((len(normalized), area))
                    normalized.append(normalized_doc)

            report(50, "正在排版区域…")
            regions = [p.Rect(region) for _, region in region_refs]
            if not regions:
                raise AnnotationCropError(
                    "没有找到可摘录的区域。请在预览里画框，或选择已有矩形批注、页边距来源。")
            layouts = layout_flow(regions) if layout == "flow" else layout_per_region(regions)

            report(50, "正在生成 PDF…")
            with p.open() as result:
                completed = 0
                for page_width, page_height, placements in layouts:
                    target = result.new_page(width=page_width, height=page_height)
                    for region_index, rect in placements:
                        normalized_index, _ = region_refs[region_index]
                        check_cancel(cancel)
                        source_doc = normalized[normalized_index]
                        if source_doc[0].get_contents():
                            target.show_pdf_page(rect, source_doc, 0, clip=regions[region_index])
                        completed += 1
                        report(50 + round(40 * completed / len(regions)),
                               f'已排版 {completed} / {len(regions)} 个区域')
                report(95, '正在保存新 PDF…')
                fd, temporary = tempfile.mkstemp(prefix='.zhixu-extract-', suffix='.pdf', dir=dest.parent)
                os.close(fd)
                protection = dict(encryption=encryption.algorithm, user_pw=encryption.user_password,
                                  owner_pw=encryption.owner_password, permissions=encryption.permissions) if encryption else {}
                result.save(temporary, garbage=4, deflate=True, **protection)
                if store.is_source_destination(str(dest)):
                    raise AnnotationCropError('输出位置已变成来源文件，请另存新文件。')
                commit_file(temporary, dest, expected, cancel=cancel)
                temporary = None
            if progress is not None:
                progress(100, 100, '区域摘录已完成')
            return dict(regions=len(regions), out_pages=len(layouts), path=str(dest))
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
