"""批注裁剪：以矩形批注为标记，矢量裁剪页面区域并导出为新 PDF。

参考语义（aardio 版 PDF 矩形注释裁剪工具）：
- 读取页面的「矩形批注」(Square) 作为裁剪标记；隐藏的批注跳过；
- 每个标记矩形对应一个输出区域，矢量保留、不转图片；
- 排版两种：独立分页（一区域一页）/ 拼版排版（A4 流式排列，便于打印）；
- 扩展模式：同一页的批注合并为一个区域；
- 页边距来源：不用批注，直接按 36 磅内缩裁剪整页；
- 输出只包含页面内容与批注外观（标记本身除外），原件保留。
"""
from __future__ import annotations

import pymupdf as p

from app.output import check_cancel
from app.pdfmarks import is_watermark
from app.workspace import parse_page_range

MARGIN = 36.0
FLOW_PAGE = (595.276, 841.89)
FLOW_GAP = 12.0


class AnnotationCropError(ValueError):
    """批注裁剪的输入问题，直接展示给用户。"""


def iter_page_annotations(page):
    """与 pdfedits.iter_annotations 相同的遍历方式，避开 annots() 生成器。"""
    annot = page.first_annot
    while annot is not None:
        following = annot.next
        yield annot
        annot = following


def collect_markers(page):
    """页面上的矩形批注标记，返回未旋转坐标系的 Rect 列表。"""
    markers = []
    for annot in iter_page_annotations(page):
        if annot.type[0] != p.PDF_ANNOT_SQUARE:
            continue
        if annot.flags & p.PDF_ANNOT_IS_HIDDEN:
            continue
        if is_watermark(page, annot):
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
        if cursor_y + item_h > page_h - margin:
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

    options: dict(source='annot'|'margin', layout='flow'|'per', expand=bool,
                   range='1-3,5' 页码范围文本)
    返回汇总 dict(regions=区域数, out_pages=输出页数, path=dest)。
    """
    total_pages = len(pages)
    if not total_pages:
        raise AnnotationCropError("没有打开的页面。")
    source = options.get("source", "annot")
    layout = options.get("layout", "flow")
    expand = bool(options.get("expand"))
    if source not in ("annot", "margin"):
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
    store.require_operation([pages[index] for index in indices], "copy")

    step = 0
    steps = len(indices) + 2

    def report(message):
        nonlocal step
        step += 1
        if progress is not None:
            progress(step, steps, message)
        check_cancel(cancel)

    # 阶段一：逐页归一化（消除旋转、移除标记批注、固化其余批注外观）并收集区域
    normalized = []      # 每个有区域的被选页一个单页文档
    region_refs = []     # (normalized 序号, 可见坐标 Rect)
    try:
        for index in indices:
            report(f"正在读取第 {index + 1} 页…")
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
                for annot in [item for item in iter_page_annotations(copied)
                              if item.type[0] == p.PDF_ANNOT_SQUARE]:
                    copied.delete_annot(annot)
                # 与 split_page 相同的旋转归一化：先固化批注外观，再统一方向
                copy[0].set_rotation(rotation)
                copy.bake(annots=True, widgets=True)
                width, height = copy[0].rect.width, copy[0].rect.height
                copy[0].set_rotation(0)
                normalized_doc = p.open()
                visible_page = normalized_doc.new_page(width=width, height=height)
                if copy[0].get_contents():
                    visible_page.show_pdf_page(visible_page.rect, copy, 0, rotate=-rotation)

            visible_rect = p.Rect(0, 0, width, height)
            if source == "margin":
                area = p.Rect(MARGIN, MARGIN, max(MARGIN, width - MARGIN),
                              max(MARGIN, height - MARGIN))
                page_regions = [area] if area.width > 0 and area.height > 0 else []
            else:
                page_regions = []
                for marker in markers:
                    area = marker & visible_rect
                    if not area.is_empty and area.width >= 1 and area.height >= 1:
                        page_regions.append(area)
                if expand and page_regions:
                    merged = page_regions[0]
                    for area in page_regions[1:]:
                        merged |= area
                    page_regions = [merged]
            if not page_regions:
                normalized_doc.close()
                continue
            for area in page_regions:
                region_refs.append((len(normalized), area))
            normalized.append(normalized_doc)

        report("正在排版区域…")
        regions = [p.Rect(region) for _, region in region_refs]
        if not regions:
            raise AnnotationCropError(
                "没有找到可裁剪的区域。矩形批注模式需要页面里有「矩形框」批注；"
                "隐藏的批注会被跳过；也可改用页边距来源。")
        layouts = layout_flow(regions) if layout == "flow" else layout_per_region(regions)

        report("正在生成 PDF…")
        result = p.open()
        try:
            for page_width, page_height, placements in layouts:
                target = result.new_page(width=page_width, height=page_height)
                for region_index, rect in placements:
                    normalized_index, _ = region_refs[region_index]
                    target.show_pdf_page(rect, normalized[normalized_index], 0,
                                         clip=regions[region_index])
            result.save(dest, garbage=4, deflate=True)
        finally:
            result.close()
        return dict(regions=len(regions), out_pages=len(layouts), path=dest)
    finally:
        for doc in normalized:
            try:
                doc.close()
            except Exception:
                pass
