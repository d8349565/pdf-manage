"""生成用于测试纸序 PDF 及相关转换/解析模块的 Word (.docx) 测试文档。

包含以下三大类（以及一份综合演示样稿）：
1. 01_文稿类型_规范通知与技术备忘录.docx (多级标题、中文规范正文、列表、引用块、多页流式排版)
2. 02_表格类型_财务报表与多维合并单据.docx (斑马纹财务表、多层跨行跨列表头、四列公文审批单卡片、三线对比表)
3. 03_图章类型_商务采购合同与盖章确认书.docx (系统架构图、性能对比图、真实半透明红色圆形公章、法人方形印章、骑缝章)
4. 04_综合类型_纸序PDF全要素演示样稿.docx (全要素综合样卷)
"""
from __future__ import annotations

import math
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

import docx
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn

ROOT_DIR = Path("d:/code/pdf-manage")
ASSETS_DIR = ROOT_DIR / "test_docs_assets"
OUTPUT_DIR = ROOT_DIR / "test_docs"

FONT_HEI = "C:/Windows/Fonts/simhei.ttf"
FONT_YAHEI = "C:/Windows/Fonts/msyh.ttc"
FONT_SONG = "C:/Windows/Fonts/simsun.ttc"


# =========================================================================
# 一、 矢量印章与测试图表生成 (Pillow)
# =========================================================================

def get_font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def make_round_seal(
    text: str = "纸序数字科技有限公司",
    subtext: str = "合同专用章",
    code: str = "91110108MA01TEST88",
    size: int = 400,
    color: tuple[int, int, int, int] = (215, 35, 35, 235),
    rotation_deg: float = 2.5,
) -> Image.Image:
    """生成高保真红色圆形企业公章（带外环、环形公司名、五角星、类别字、底部编码）。"""
    img = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = size / 2.0, size / 2.0

    # 1. 外圆边框
    r_outer = size / 2.0 - 14.0
    draw.ellipse(
        (cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer),
        outline=color,
        width=int(size * 0.024),
    )

    # 2. 中心五角星
    star_r = size * 0.165
    star_pts = []
    for i in range(5):
        a_outer = -math.pi / 2.0 + i * 2.0 * math.pi / 5.0
        star_pts.append(
            (cx + star_r * math.cos(a_outer), cy + star_r * math.sin(a_outer))
        )
        a_inner = a_outer + math.pi / 5.0
        star_pts.append(
            (
                cx + star_r * 0.382 * math.cos(a_inner),
                cy + star_r * 0.382 * math.sin(a_inner),
            )
        )
    draw.polygon(star_pts, fill=color)

    # 3. 横向类型字 (如: 合同专用章)
    sub_font_size = int(size * 0.082)
    sub_font = get_font(FONT_HEI, sub_font_size)
    bbox = sub_font.getbbox(subtext)
    sw = bbox[2] - bbox[0]
    draw.text((cx - sw / 2.0, cy + star_r * 0.82), subtext, font=sub_font, fill=color)

    # 4. 底部防伪统一信用代码
    if code:
        code_font_size = int(size * 0.042)
        code_font = get_font(FONT_HEI, code_font_size)
        cbbox = code_font.getbbox(code)
        cw = cbbox[2] - cbbox[0]
        draw.text((cx - cw / 2.0, cy + r_outer * 0.72), code, font=code_font, fill=color)

    # 5. 上半弧形文字 (公司名称)
    arc_font_size = int(size * 0.088)
    arc_font = get_font(FONT_SONG, arc_font_size)
    n = len(text)
    if n > 0:
        total_angle = math.radians(min(225.0, 18.5 * n))
        start_angle = -math.pi / 2.0 - total_angle / 2.0
        step = total_angle / (n - 1) if n > 1 else 0.0
        r_text = r_outer - size * 0.135

        for i, ch in enumerate(text):
            ang = start_angle + i * step
            ch_x = cx + r_text * math.cos(ang)
            ch_y = cy + r_text * math.sin(ang)

            ch_size = int(size * 0.22)
            ch_img = Image.new("RGBA", (ch_size, ch_size), (255, 255, 255, 0))
            ch_draw = ImageDraw.Draw(ch_img)
            c_bbox = arc_font.getbbox(ch)
            c_w = c_bbox[2] - c_bbox[0]
            c_h = c_bbox[3] - c_bbox[1]
            ch_draw.text(
                ((ch_size - c_w) / 2.0, (ch_size - c_h) / 2.0),
                ch,
                font=arc_font,
                fill=color,
            )

            rot_deg = math.degrees(ang + math.pi / 2.0)
            rotated = ch_img.rotate(-rot_deg, resample=Image.BICUBIC, expand=True)
            img.paste(
                rotated,
                (int(ch_x - rotated.width / 2.0), int(ch_y - rotated.height / 2.0)),
                rotated,
            )

    if rotation_deg:
        img = img.rotate(rotation_deg, resample=Image.BICUBIC, expand=True)

    return img


def make_square_seal(
    text: str = "张伟之印",
    size: int = 180,
    color: tuple[int, int, int, int] = (215, 35, 35, 235),
    rotation_deg: float = -1.8,
) -> Image.Image:
    """生成红色方形法人代表私章。"""
    img = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)

    border_w = int(size * 0.055)
    margin = 8
    draw.rounded_rectangle(
        (margin, margin, size - margin, size - margin),
        radius=int(size * 0.06),
        outline=color,
        width=border_w,
    )

    font_size = int(size * 0.36)
    font = get_font(FONT_HEI, font_size)

    chars = list(text[:4])
    if len(chars) == 4:
        # 传统印章右起两列
        positions = [
            (size * 0.52, size * 0.12, chars[0]),
            (size * 0.52, size * 0.52, chars[1]),
            (size * 0.14, size * 0.12, chars[2]),
            (size * 0.14, size * 0.52, chars[3]),
        ]
        for x, y, c in positions:
            draw.text((x, y), c, font=font, fill=color)

    if rotation_deg:
        img = img.rotate(rotation_deg, resample=Image.BICUBIC, expand=True)
    return img


def make_half_seal(
    text: str = "纸序数字科技有限公司",
    size: int = 400,
    color: tuple[int, int, int, int] = (215, 35, 35, 230),
) -> Image.Image:
    """生成合同边缘专用的半边骑缝章。"""
    full_seal = make_round_seal(text=text, subtext="骑缝专用章", size=size, color=color, rotation_deg=0)
    w, h = full_seal.size
    # 裁剪左半边
    half_crop = full_seal.crop((0, 0, w // 2 + 10, h))
    return half_crop


def make_arch_diagram(out_path: Path):
    """生成清晰专业的系统架构拓扑图 (900x460)。"""
    w, h = 900, 460
    img = Image.new("RGBA", (w, h), (248, 250, 252, 255))
    draw = ImageDraw.Draw(img)

    # 外框
    draw.rounded_rectangle((10, 10, w - 10, h - 10), radius=12, outline=(226, 232, 240, 255), width=2)

    # 标题区
    draw.text((32, 24), "纸序 PDF 核心服务架构与数据流转拓扑", font=get_font(FONT_HEI, 22), fill=(15, 23, 42, 255))
    draw.text((32, 56), "ZhiXu PDF High-Performance Document Processing Pipeline (Architecture Overview)", font=get_font(FONT_YAHEI, 12), fill=(100, 116, 139, 255))

    layers = [
        ("表现与交互层 (Presentation Layer)", (37, 99, 235), [
            ("桌面交互视图", "PyQt6 Native Canvas"),
            ("多页网格整理", "Drag & Drop Thumbnails"),
            ("打印与预览", "High-DPI Vector Render")
        ]),
        ("业务流编排层 (Pipeline Orchestration)", (13, 148, 136), [
            ("版面重排与纠偏", "Page Rotation & Crop"),
            ("文档拆分与合并", "Document Assembly"),
            ("表格智能对齐", "Table Model Alignment")
        ]),
        ("底层计算引擎 (Engine Foundation)", (124, 58, 237), [
            ("PyMuPDF 渲染核", "C-Native MuPDF Binding"),
            ("RapidOCR 推理核", "ONNX Runtime Engine"),
            ("Word-XML 导出器", "Lossless Fixed-Page Spec")
        ]),
    ]

    top_y = 95
    layer_h = 95
    gap_y = 25

    for l_idx, (layer_title, color, boxes) in enumerate(layers):
        curr_y = top_y + l_idx * (layer_h + gap_y)

        # 层背景框
        draw.rounded_rectangle(
            (30, curr_y, w - 30, curr_y + layer_h),
            radius=8,
            fill=(255, 255, 255, 255),
            outline=color,
            width=2,
        )

        # 层标题标签
        draw.rounded_rectangle((45, curr_y - 12, 45 + 340, curr_y + 12), radius=6, fill=color)
        draw.text((58, curr_y - 10), layer_title, font=get_font(FONT_HEI, 13), fill=(255, 255, 255, 255))

        col_w = (w - 120) // 3
        for b_idx, (b_title, b_sub) in enumerate(boxes):
            bx = 50 + b_idx * (col_w + 10)
            by = curr_y + 20
            bw = col_w
            bh = 60
            draw.rounded_rectangle((bx, by, bx + bw, by + bh), radius=6, fill=(241, 245, 249, 255))
            draw.text((bx + 16, by + 10), b_title, font=get_font(FONT_HEI, 14), fill=(30, 41, 59, 255))
            draw.text((bx + 16, by + 34), b_sub, font=get_font(FONT_YAHEI, 11), fill=(100, 116, 139, 255))

        if l_idx < len(layers) - 1:
            arrow_y = curr_y + layer_h + 3
            cx = w // 2
            draw.line([(cx, arrow_y), (cx, arrow_y + 16)], fill=(148, 163, 184, 255), width=3)
            draw.polygon([(cx - 6, arrow_y + 14), (cx + 6, arrow_y + 14), (cx, arrow_y + 21)], fill=(148, 163, 184, 255))

    img.save(out_path)


def make_performance_chart(out_path: Path):
    """生成性能柱状统计图 (800x380)。"""
    w, h = 800, 380
    img = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle((8, 8, w - 8, h - 8), radius=10, outline=(226, 232, 240, 255), width=2)
    draw.text((35, 24), "纸序 PDF 核心引擎文档处理吞吐量实测 (页/秒)", font=get_font(FONT_HEI, 18), fill=(30, 41, 59, 255))
    draw.text((35, 52), "测试基准：Intel Core i7 / 32GB RAM / 100页标准图文混合企业文档样本", font=get_font(FONT_YAHEI, 11), fill=(100, 116, 139, 255))

    ox, oy = 80, 310
    plot_w, plot_h = 660, 220

    max_val = 80
    for tick in range(0, max_val + 1, 20):
        ty = oy - int((tick / max_val) * plot_h)
        draw.line([(ox, ty), (ox + plot_w, ty)], fill=(241, 245, 249, 255), width=1)
        draw.text((ox - 35, ty - 8), str(tick), font=get_font(FONT_YAHEI, 11), fill=(148, 163, 184, 255))

    draw.line([(ox, oy), (ox + plot_w, oy)], fill=(203, 213, 225, 255), width=2)
    draw.line([(ox, oy), (ox, oy - plot_h)], fill=(203, 213, 225, 255), width=2)

    data = [
        ("PDF 流式转 Word", 45.2, (37, 99, 235)),
        ("高精度版面导出", 31.6, (59, 130, 246)),
        ("PDF 表格提取 Excel", 68.4, (16, 185, 129)),
        ("OCR 图像文字重构", 22.8, (245, 158, 11)),
    ]

    bar_width = 75
    spacing = plot_w // len(data)

    for i, (label, val, bar_color) in enumerate(data):
        bx = ox + 45 + i * spacing
        bar_h = int((val / max_val) * plot_h)
        by = oy - bar_h

        draw.rounded_rectangle((bx, by, bx + bar_width, oy), radius=4, fill=bar_color)
        val_str = f"{val} p/s"
        draw.text((bx + 10, by - 22), val_str, font=get_font(FONT_HEI, 12), fill=bar_color)
        draw.text((bx - 12, oy + 12), label, font=get_font(FONT_HEI, 12), fill=(51, 65, 85, 255))

    img.save(out_path)


# =========================================================================
# 二、 Word 样式与排版辅助工具函数
# =========================================================================

def add_run(
    paragraph,
    text: str,
    font_name: str = "Microsoft YaHei",
    size_pt: float = 10.5,
    bold: bool = False,
    italic: bool = False,
    underline: bool = False,
    color_hex: str = "262626",
) -> docx.text.run.Run:
    """向段落添加带有中文字体和格式控制的文本 Run。"""
    run = paragraph.add_run(text)
    run.font.name = font_name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), font_name)
    run.font.size = Pt(size_pt)
    run.bold = bold
    run.italic = italic
    run.underline = underline
    if color_hex:
        r = int(color_hex[0:2], 16)
        g = int(color_hex[2:4], 16)
        b = int(color_hex[4:6], 16)
        run.font.color.rgb = RGBColor(r, g, b)
    return run


def add_heading_1(doc: Document, text: str) -> docx.text.paragraph.Paragraph:
    """添加一级大标题（深青蓝，20pt，加粗，段前16pt，段后8pt）。"""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(16)
    p.paragraph_format.space_after = Pt(8)
    p.paragraph_format.keep_with_next = True
    add_run(p, text, font_name="SimHei", size_pt=18, bold=True, color_hex="1E3A8A")
    return p


def add_heading_2(doc: Document, text: str) -> docx.text.paragraph.Paragraph:
    """添加二级标题（14pt，加粗，段前10pt，段后4pt）。"""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.keep_with_next = True
    add_run(p, text, font_name="Microsoft YaHei", size_pt=13.5, bold=True, color_hex="0F766E")
    return p


def add_heading_3(doc: Document, text: str) -> docx.text.paragraph.Paragraph:
    """添加三级标题（12pt，加粗，段前6pt，段后2pt）。"""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.keep_with_next = True
    add_run(p, text, font_name="Microsoft YaHei", size_pt=11.5, bold=True, color_hex="334155")
    return p


def add_body_paragraph(
    doc: Document,
    text: str,
    first_indent_chars: int = 2,
    size_pt: float = 10.5,
    color_hex: str = "333333",
) -> docx.text.paragraph.Paragraph:
    """添加中文正文段落（标准中文排版，首行缩进两字符，1.3倍行距）。"""
    p = doc.add_paragraph()
    p.paragraph_format.line_spacing = 1.3
    p.paragraph_format.space_after = Pt(5)
    if first_indent_chars > 0:
        p.paragraph_format.first_line_indent = Pt(size_pt * first_indent_chars * 1.05)
    add_run(p, text, font_name="Microsoft YaHei", size_pt=size_pt, color_hex=color_hex)
    return p


def add_callout_box(doc: Document, title: str, text: str):
    """添加引用高亮提示框（左侧蓝色粗边条，浅蓝背景）。"""
    tbl = doc.add_table(rows=1, cols=1)
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell = tbl.cell(0, 0)
    set_cell_background(cell, "F0F9FF")
    set_cell_margins(cell, top=140, bottom=140, left=200, right=200)

    # 左侧厚蓝边，其余无边框
    tcPr = cell._tc.get_or_add_tcPr()
    borders = parse_xml(
        f'<w:tcBorders {nsdecls("w")}>'
        f'<w:top w:val="none"/>'
        f'<w:left w:val="single" w:sz="24" w:space="0" w:color="0284C7"/>'
        f'<w:bottom w:val="none"/>'
        f'<w:right w:val="none"/>'
        f"</w:tcBorders>"
    )
    tcPr.append(borders)

    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(3)
    add_run(p, f"💡 {title}\n", font_name="Microsoft YaHei", size_pt=10.5, bold=True, color_hex="0369A1")
    add_run(p, text, font_name="Microsoft YaHei", size_pt=9.5, color_hex="334155")

    # 间距段落
    sp = doc.add_paragraph()
    sp.paragraph_format.space_before = Pt(4)
    sp.paragraph_format.space_after = Pt(4)


def set_cell_background(cell, fill_hex: str):
    """设置单元格背景填充色。"""
    tcPr = cell._tc.get_or_add_tcPr()
    tcPr.append(parse_xml(f'<w:shd {nsdecls("w")} w:fill="{fill_hex}"/>'))


def set_cell_margins(cell, top: int = 120, bottom: int = 120, left: int = 160, right: int = 160):
    """设置单元格内部边距（内衬）。"""
    tcPr = cell._tc.get_or_add_tcPr()
    tcPr.append(
        parse_xml(
            f'<w:tcMar {nsdecls("w")}>'
            f'<w:top w:w="{top}" w:type="dxa"/>'
            f'<w:bottom w:w="{bottom}" w:type="dxa"/>'
            f'<w:left w:w="{left}" w:type="dxa"/>'
            f'<w:right w:w="{right}" w:type="dxa"/>'
            f"</w:tcMar>"
        )
    )


def set_table_borders(table, color: str = "CBD5E1", sz: str = "4", val: str = "single"):
    """为表格设置精致轻量边框。"""
    tblPr = table._tbl.tblPr
    tblPr.append(
        parse_xml(
            f'<w:tblBorders {nsdecls("w")}>'
            f'<w:top w:val="{val}" w:sz="{sz}" w:space="0" w:color="{color}"/>'
            f'<w:bottom w:val="{val}" w:sz="{sz}" w:space="0" w:color="{color}"/>'
            f'<w:left w:val="none"/>'
            f'<w:right w:val="none"/>'
            f'<w:insideH w:val="{val}" w:sz="{sz}" w:space="0" w:color="{color}"/>'
            f'<w:insideV w:val="none"/>'
            f"</w:tblBorders>"
        )
    )


def set_table_grid_borders(table, color: str = "CBD5E1", sz: str = "4", val: str = "single"):
    """为表格设置完整网格边框（含内外框线）。"""
    tblPr = table._tbl.tblPr
    tblPr.append(
        parse_xml(
            f'<w:tblBorders {nsdecls("w")}>'
            f'<w:top w:val="{val}" w:sz="{sz}" w:space="0" w:color="{color}"/>'
            f'<w:bottom w:val="{val}" w:sz="{sz}" w:space="0" w:color="{color}"/>'
            f'<w:left w:val="{val}" w:sz="{sz}" w:space="0" w:color="{color}"/>'
            f'<w:right w:val="{val}" w:sz="{sz}" w:space="0" w:color="{color}"/>'
            f'<w:insideH w:val="{val}" w:sz="{sz}" w:space="0" w:color="{color}"/>'
            f'<w:insideV w:val="{val}" w:sz="{sz}" w:space="0" w:color="{color}"/>'
            f"</w:tblBorders>"
        )
    )


def configure_header_footer(doc: Document, header_text: str, footer_text: str = "纸序 PDF 自动化测试集"):
    """配置规范页眉页脚。"""
    for section in doc.sections:
        section.top_margin = Inches(0.9)
        section.bottom_margin = Inches(0.9)
        section.left_margin = Inches(1.0)
        section.right_margin = Inches(1.0)

        # 页眉
        hp = section.header.paragraphs[0]
        hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        add_run(hp, header_text, font_name="Microsoft YaHei", size_pt=8.5, color_hex="94A3B8")

        # 页脚
        fp = section.footer.paragraphs[0]
        fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        add_run(fp, f"— {footer_text} —", font_name="Microsoft YaHei", size_pt=8.5, color_hex="94A3B8")


# =========================================================================
# 三、 文档生成器 1：文稿类型 (01_文稿类型_规范通知与技术备忘录.docx)
# =========================================================================

def build_manuscript_document(out_path: Path):
    """生成文稿类型测试 Word 文档。"""
    doc = Document()
    configure_header_footer(doc, "纸序 PDF 软件设计与工程规范备忘录 [内部文档]")

    # 1. 主标题
    p_title = doc.add_paragraph()
    p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_title.paragraph_format.space_before = Pt(24)
    p_title.paragraph_format.space_after = Pt(4)
    add_run(p_title, "纸序 PDF 架构设计准则与交互开发规范", font_name="SimHei", size_pt=22, bold=True, color_hex="1E293B")

    # 副标题
    p_sub = doc.add_paragraph()
    p_sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_sub.paragraph_format.space_after = Pt(16)
    add_run(p_sub, "ZhiXu PDF Architecture Guidelines & Interaction Specification (v0.8.0)", font_name="Microsoft YaHei", size_pt=11, color_hex="64748B")

    # 元数据信息条
    p_meta = doc.add_paragraph()
    p_meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_meta.paragraph_format.space_after = Pt(18)
    add_run(p_meta, "编制部门：核心平台研发中心  |  密级：内部公开  |  生效日期：2026年10月  |  修订版本：v0.8.0", font_name="Microsoft YaHei", size_pt=9, color_hex="64748B")

    # 核心原则 Callout 框
    add_callout_box(
        doc,
        "用户体验第一准则（面向普通业务人员）",
        "1. 零学习成本：界面自解释，无需阅读长篇操作手册；\n"
        "2. 防错优于报错：设计上避免用户犯错，而不是出错后弹出令人困惑的技术提示；\n"
        "3. 一键直达：高频核心功能两次点击以内直接触达；\n"
        "4. 性能感知：操作即时反馈，大数据量页面虚拟滚动，永不卡死界面。"
    )

    # 修订记录迷你表
    add_heading_2(doc, "文档修订与变更履历")
    rev_tbl = doc.add_table(rows=4, cols=4)
    rev_tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    set_table_borders(rev_tbl, color="CBD5E1", sz="4")

    headers = ["版本号", "发布日期", "主要变更内容", "修订负责人"]
    col_widths = [Inches(1.0), Inches(1.3), Inches(3.2), Inches(1.0)]

    for c_idx, h_text in enumerate(headers):
        cell = rev_tbl.cell(0, c_idx)
        cell.width = col_widths[c_idx]
        set_cell_background(cell, "F1F5F9")
        set_cell_margins(cell, top=80, bottom=80, left=100, right=100)
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        add_run(p, h_text, font_name="Microsoft YaHei", size_pt=9.5, bold=True, color_hex="1E293B")

    rows_data = [
        ("v0.1.0", "2026-09-15", "项目脚手架初始化，完成多页阅读与基础渲染管线", "李工"),
        ("v0.5.0", "2026-09-30", "增加整理视图网格拖拽排序、批量旋转与导出拆分", "王工"),
        ("v0.8.0", "2026-10-12", "重构 OCR 识别流与 Word-XML 排版引擎，支持无损表格与文字还原", "陈架构师"),
    ]
    for r_idx, r_data in enumerate(rows_data, start=1):
        for c_idx, val in enumerate(r_data):
            cell = rev_tbl.cell(r_idx, c_idx)
            cell.width = col_widths[c_idx]
            set_cell_margins(cell, top=60, bottom=60, left=100, right=100)
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER if c_idx != 2 else WD_ALIGN_PARAGRAPH.LEFT
            add_run(p, val, font_name="Microsoft YaHei", size_pt=9, color_hex="334155")

    # 一级标题：一、 业务背景与问题分析
    add_heading_1(doc, "一、 业务背景与工程痛点")
    add_body_paragraph(
        doc,
        "在企业日常数字化办公场景中，PDF 文件往往承担着发票报销、合同归档、标书编制与公文传递等核心法律与业务凭据职责。然而，传统的 PDF 阅读工具往往功能沉重、商业授权费用高昂，而在线免费转换工具则存在严重的数据泄露与商业机密外传隐患。"
    )
    add_body_paragraph(
        doc,
        "针对上述现状，纸序 PDF（ZhiXuPDF）确立了‘本地离线运行、轻量高效、安全合规、极速交付’的研发基调。系统完全摒弃外部未授权联网依赖，所有排版解析、文字纠偏、表格智能对齐与图像重构均在本地客户端环境完成，确保用户商业数据的绝对安全。"
    )

    # 二级标题：1.1 典型业务处理痛点
    add_heading_2(doc, "1.1 行业现有方案的四大瓶颈")

    points = [
        ("排版失真与重叠：", "部分开源工具在将 PDF 转换为 Word 或可编辑文档时，字体字阶计算错误，容易造成段落挤压与文本重叠。"),
        ("表格结构错位破碎：", "多线交错表格与跨行跨列单元格经常被错误拆解为离散文本框，导致用户二次统计与核算困难。"),
        ("印章与手写签名遗失：", "传统 OCR 解析往往误将红色印章与公文戳记视作背景噪点过滤，无法满足法务及归档审计合规要求。"),
        ("大文件卡顿与内存溢出：", "当面对上百页高清扫描标书或施工图纸时，常规客户端界面频繁卡死、无响应，缺乏分页惰性加载机制。"),
    ]
    for label, desc in points:
        p_item = doc.add_paragraph()
        p_item.paragraph_format.left_indent = Inches(0.25)
        p_item.paragraph_format.space_after = Pt(4)
        add_run(p_item, "•  ", font_name="Microsoft YaHei", size_pt=10, bold=True, color_hex="0284C7")
        add_run(p_item, label, font_name="Microsoft YaHei", size_pt=10, bold=True, color_hex="1E293B")
        add_run(p_item, desc, font_name="Microsoft YaHei", size_pt=10, color_hex="475569")

    # 插入分页符，测试多页跨页排版
    doc.add_page_break()

    # 一级标题：二、 核心文档处理与排版体系
    add_heading_1(doc, "二、 核心文档处理与排版体系")
    add_body_paragraph(
        doc,
        "纸序 PDF 采用自研的双轨文档排版引擎（Dual-Track Layout Engine）。针对数字化矢量公文，优先启用轻量流式排版（Flow Layout），最大限度恢复原生段落与标点符号流；针对复杂报表与扫描件，则自动回退至高精度坐标定位模型（Fixed-Page Coordinate Model）。"
    )

    add_heading_2(doc, "2.1 排版参数与字阶换算规范")
    add_body_paragraph(
        doc,
        "为了保证在不同屏幕分辨率与打印机输出时的一致性，系统内部严格遵循统一的度量衡标定体系。以下为推荐文本排版参数对照表："
    )

    # 列表说明
    ordered_items = [
        ("主标题与公文大字号：", "使用二号字或小二号字（20pt ~ 22pt），字体采用黑体或方正大标宋，段前保持 16pt 呼吸空间；"),
        ("正文与说明段落：", "使用小四号字或五号字（10.5pt ~ 12pt），首行缩进 2 字符，行距设定为 1.25 ~ 1.35 倍行高；"),
        ("图表题注与注释角标：", "统一采用小五号字（9pt），颜色推荐使用 Slate Gray (#64748B)，居中对齐；"),
        ("代码与配置片段：", "统一采用 Consolas 或 Courier New 等宽字体，字号 9.5pt，背景填充浅灰 (#F8FAFC)。"),
    ]
    for idx, (head, detail) in enumerate(ordered_items, start=1):
        p_num = doc.add_paragraph()
        p_num.paragraph_format.left_indent = Inches(0.25)
        p_num.paragraph_format.space_after = Pt(4)
        add_run(p_num, f"{idx}. ", font_name="Microsoft YaHei", size_pt=10, bold=True, color_hex="0F766E")
        add_run(p_num, head, font_name="Microsoft YaHei", size_pt=10, bold=True, color_hex="1E293B")
        add_run(p_num, detail, font_name="Microsoft YaHei", size_pt=10, color_hex="475569")

    # 代码块风格展示
    add_heading_2(doc, "2.2 典型导出配置与接口示例")
    add_body_paragraph(doc, "在后端调用 Word 导出服务时，开发者可通过以下规范接口传递版面布局元数据：")

    code_tbl = doc.add_table(rows=1, cols=1)
    code_tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    c_cell = code_tbl.cell(0, 0)
    set_cell_background(c_cell, "F8FAFC")
    set_cell_margins(c_cell, top=100, bottom=100, left=150, right=150)
    set_table_borders(code_tbl, color="E2E8F0", sz="4")

    code_p = c_cell.paragraphs[0]
    code_lines = (
        "# 纸序 PDF 格式导出管道配置样例\n"
        "from app.ocr.word_export import write_layout, LayoutOptions\n\n"
        "options = LayoutOptions(\n"
        "    mode='hybrid',                 # hybrid: 文本流式 + 表格精准定位\n"
        "    detect_tables=True,            # 开启多维表格特征识别\n"
        "    preserve_seals=True,           # 保留红色印章与手写签名图层\n"
        "    dpi=300                        # 高保真输出解析度\n"
        ")\n"
        "result_docx = export_to_word(document, options=options)"
    )
    add_run(code_p, code_lines, font_name="Consolas", size_pt=9, color_hex="0F172A")

    # 结语
    add_heading_1(doc, "三、 总结与测试验证建议")
    add_body_paragraph(
        doc,
        "本文档覆盖了正文长段落、中文首行缩进、多级标题、列表项、高亮提示块、代码块以及跨页断页排版等核心文稿排版要素。在测试纸序 PDF 的排版转换、阅读缩放与打印输出时，可重点比对文字平滑度、行间距合理性及跨页时标题是否保持完整。"
    )

    doc.save(out_path)
    print(f"[OK] Generated: {out_path.name}")


# =========================================================================
# 四、 文档生成器 2：表格类型 (02_表格类型_财务报表与多维合并单据.docx)
# =========================================================================

def build_table_document(out_path: Path):
    """生成带多样化表格的测试 Word 文档。"""
    doc = Document()
    configure_header_footer(doc, "纸序 PDF 结构化报表与数据表格测试集")

    # 标题区
    p_title = doc.add_paragraph()
    p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_title.paragraph_format.space_before = Pt(20)
    p_title.paragraph_format.space_after = Pt(4)
    add_run(p_title, "企业综合运营报表与多维数据结构测试单", font_name="SimHei", size_pt=22, bold=True, color_hex="1E293B")

    p_sub = doc.add_paragraph()
    p_sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_sub.paragraph_format.space_after = Pt(16)
    add_run(p_sub, "包含斑马纹财务表、多维跨行跨列表头、四列公文审批单卡片与对比表", font_name="Microsoft YaHei", size_pt=10.5, color_hex="64748B")

    # 说明引用框
    add_callout_box(
        doc,
        "表格测试重点指引",
        "本文件集中测试以下四种工业级表格形态：\n"
        "1. 标准财务报表：测试深色表头、斑马条纹交替底色、金额与百分比数值严格靠右对齐；\n"
        "2. 复杂多维评估表：测试表头跨列合并（Colspan）与行维度垂直跨行合并（Rowspan/vMerge）；\n"
        "3. 四列行政审批单：测试公文常见键值对网格、大段文本合并单元格与签字行；\n"
        "4. 学术三线对比表：测试无竖线、仅保留粗顶线/细中线/粗底线的极简科技排版。"
    )

    # ------------------ 表格 1：标准财务经营核算表 ------------------
    add_heading_1(doc, "表 1：2026年度季度经营收入与预算执行核算表")
    add_body_paragraph(
        doc,
        "说明：用于测试标准 6 列财务报表。表头深青蓝底色，偶数行浅灰底纹，金额采用千分位靠右对齐，底端合计行双下边线。"
    )

    table1_data = [
        ("01", "核心平台授权", "企业软件部", "¥ 3,420,000.00", "¥ 3,800,000.00", "+11.1%"),
        ("02", "文档云转换服务", "SaaS 云事业部", "¥ 1,850,000.00", "¥ 2,200,000.00", "+18.9%"),
        ("03", "定制化算力部署", "技术工程部", "¥ 980,000.00", "¥ 1,150,000.00", "+17.3%"),
        ("04", "OCR 离线模型包", "AI 算法研究部", "¥ 1,420,000.00", "¥ 1,500,000.00", "+5.6%"),
        ("05", "年度技术维保", "客户成功中心", "¥ 650,000.00", "¥ 720,000.00", "+10.8%"),
    ]

    t1 = doc.add_table(rows=len(table1_data) + 2, cols=6)
    t1.alignment = WD_TABLE_ALIGNMENT.CENTER
    set_table_borders(t1, color="94A3B8", sz="6")

    col_widths1 = [Inches(0.6), Inches(1.8), Inches(1.4), Inches(1.5), Inches(1.5), Inches(1.0)]
    headers1 = ["序号", "业务核算科目", "归属核算中心", "2025年决算金额", "2026年预算指标", "同比增幅"]

    # 填充表头
    for c_idx, h_text in enumerate(headers1):
        cell = t1.cell(0, c_idx)
        cell.width = col_widths1[c_idx]
        set_cell_background(cell, "1E3A8A")  # 深青蓝
        set_cell_margins(cell, top=100, bottom=100, left=80, right=80)
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if c_idx in [3, 4, 5] else (WD_ALIGN_PARAGRAPH.CENTER if c_idx == 0 else WD_ALIGN_PARAGRAPH.LEFT)
        add_run(p, h_text, font_name="Microsoft YaHei", size_pt=9.5, bold=True, color_hex="FFFFFF")

    # 填充数据行 (斑马纹)
    for r_idx, row in enumerate(table1_data, start=1):
        is_even = (r_idx % 2 == 0)
        bg_color = "F8FAFC" if is_even else "FFFFFF"
        for c_idx, val in enumerate(row):
            cell = t1.cell(r_idx, c_idx)
            cell.width = col_widths1[c_idx]
            set_cell_background(cell, bg_color)
            set_cell_margins(cell, top=80, bottom=80, left=80, right=80)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            p = cell.paragraphs[0]
            # 数字靠右，序号居中，文字靠左
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if c_idx in [3, 4, 5] else (WD_ALIGN_PARAGRAPH.CENTER if c_idx == 0 else WD_ALIGN_PARAGRAPH.LEFT)
            add_run(p, val, font_name="Microsoft YaHei", size_pt=9, color_hex="1E293B")

    # 合计行 (最后一行)
    tot_row_idx = len(table1_data) + 1
    t1.cell(tot_row_idx, 0).text = ""
    cell_tot_label = t1.cell(tot_row_idx, 1)
    set_cell_margins(cell_tot_label, top=90, bottom=90, left=80, right=80)
    p_tot = cell_tot_label.paragraphs[0]
    p_tot.alignment = WD_ALIGN_PARAGRAPH.LEFT
    add_run(p_tot, "合 计 (Total)", font_name="Microsoft YaHei", size_pt=9.5, bold=True, color_hex="0F172A")

    totals_cells = [
        (2, "-"),
        (3, "¥ 8,320,000.00"),
        (4, "¥ 9,370,000.00"),
        (5, "+12.6%"),
    ]
    for c_idx, t_val in totals_cells:
        c = t1.cell(tot_row_idx, c_idx)
        c.width = col_widths1[c_idx]
        set_cell_background(c, "E2E8F0")
        set_cell_margins(c, top=90, bottom=90, left=80, right=80)
        c.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        p = c.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if c_idx in [3, 4, 5] else WD_ALIGN_PARAGRAPH.CENTER
        add_run(p, t_val, font_name="Microsoft YaHei", size_pt=9.5, bold=True, color_hex="0F172A")
    set_cell_background(t1.cell(tot_row_idx, 0), "E2E8F0")
    set_cell_background(t1.cell(tot_row_idx, 1), "E2E8F0")

    # 分页测试下一大表格
    doc.add_page_break()

    # ------------------ 表格 2：复杂跨行跨列综合评估矩阵 ------------------
    add_heading_1(doc, "表 2：软件系统核心技术规格与达标评估矩阵（多维合并单元格）")
    add_body_paragraph(
        doc,
        "说明：本表格重点测试 Word-XML 解析器对跨列（Colspan）与跨行（Rowspan/vMerge）合并单元格的拓扑重建能力。"
    )

    t2 = doc.add_table(rows=8, cols=5)
    t2.alignment = WD_TABLE_ALIGNMENT.CENTER
    set_table_grid_borders(t2, color="94A3B8", sz="6")

    col_widths2 = [Inches(1.5), Inches(1.8), Inches(1.5), Inches(1.5), Inches(1.2)]

    # 表头第一行：Colspan
    # [核心技术维度] [子系统模块] [2026年实测规格与指标 (跨3列)]
    h00 = t2.cell(0, 0)
    h00.text = "系统架构维度"
    h01 = t2.cell(0, 1)
    h01.text = "子系统模块"
    h02 = t2.cell(0, 2)
    h04 = t2.cell(0, 4)
    h02.merge(h04)  # 跨3列
    h02.text = "2026年度实测技术指标与验收判定"

    # 表头第二行
    headers2_sub = ["一级架构", "功能组件", "行业基准线", "纸序实测值", "达标判定"]
    for c_idx, val in enumerate(headers2_sub):
        t2.cell(1, c_idx).text = val

    # 格式化前两行表头
    for r in range(2):
        for c in range(5):
            cell = t2.cell(r, c)
            set_cell_background(cell, "0F766E")  # 深青绿
            set_cell_margins(cell, top=90, bottom=90, left=80, right=80)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            add_run(p, p.text, font_name="Microsoft YaHei", size_pt=9.5, bold=True, color_hex="FFFFFF")
            # 清理刚才 text 赋值残留的默认段落
            if len(p.runs) > 1:
                p.runs[0]._r.getparent().remove(p.runs[0]._r)

    # 数据行内容定义
    matrix_rows = [
        # (一级架构, 模块, 基准, 实测, 达标)
        ("底层计算引擎", "PDF 矢量光栅化", "< 50ms/页", "18.5ms/页", "优 秀"),
        ("底层计算引擎", "多线程页面切片", ">= 4 线程并发", "8 线程满载", "达 标"),
        ("底层计算引擎", "内存峰值控制", "< 500MB", "180MB 平稳", "优 秀"),
        ("版面导出还原", "流式 Word 生成", "字体精准匹配", "100% 映射", "达 标"),
        ("版面导出还原", "多维表格还原", "保留行列网格", "无损无错位", "优 秀"),
        ("版面导出还原", "印章与插图保留", "透明通道支持", "原生 RGBA 保留", "优 秀"),
    ]

    for idx, (cat, mod, bench, actual, status) in enumerate(matrix_rows, start=2):
        t2.cell(idx, 0).text = cat
        t2.cell(idx, 1).text = mod
        t2.cell(idx, 2).text = bench
        t2.cell(idx, 3).text = actual
        t2.cell(idx, 4).text = status

        for c_idx in range(5):
            cell = t2.cell(idx, c_idx)
            set_cell_background(cell, "FFFFFF" if idx % 2 == 0 else "F8FAFC")
            set_cell_margins(cell, top=70, bottom=70, left=80, right=80)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            status_color = "059669" if "优" in cell.text else ("0284C7" if "达" in cell.text else "1E293B")
            add_run(p, p.text, font_name="Microsoft YaHei", size_pt=9, bold=(c_idx == 4), color_hex=status_color)
            if len(p.runs) > 1:
                p.runs[0]._r.getparent().remove(p.runs[0]._r)

    # 跨行合并：前3行（第2, 3, 4行）合并第一列
    t2.cell(2, 0).merge(t2.cell(4, 0))
    # 后3行（第5, 6, 7行）合并第一列
    t2.cell(5, 0).merge(t2.cell(7, 0))

    # ------------------ 表格 3：行政公文与项目立项审批单 ------------------
    add_heading_1(doc, "表 3：企业数字化工程立项审批卡片（4列结构化表单）")
    add_body_paragraph(doc, "说明：测试政企典型的 4 列网格审批卡片，包含长文本合并单元格与签字区域。")

    t3 = doc.add_table(rows=6, cols=4)
    t3.alignment = WD_TABLE_ALIGNMENT.CENTER
    set_table_grid_borders(t3, color="94A3B8", sz="6")

    w_lbl = Inches(1.5)
    w_val = Inches(2.25)

    # Row 0: 项目名称 [跨3列]
    t3.cell(0, 0).text = "项目名称"
    t3.cell(0, 1).merge(t3.cell(0, 3))
    t3.cell(0, 1).text = "纸序 PDF 工业级文档智能处理与无损转换引擎研发"

    # Row 1: 申请部门 / 申请人 / 立项日期 / 密级
    t3.cell(1, 0).text = "申请部门"
    t3.cell(1, 1).text = "架构与核心研发中心"
    t3.cell(1, 2).text = "项目编号"
    t3.cell(1, 3).text = "ZX-2026-RD-088"

    # Row 2: 预算额度 / 工期周期
    t3.cell(2, 0).text = "立项预算"
    t3.cell(2, 1).text = "¥ 680,000.00 (自有研发资金)"
    t3.cell(2, 2).text = "预计周期"
    t3.cell(2, 3).text = "2026.10 ~ 2027.03 (6个月)"

    # Row 3: 核心诉求 [跨3列]
    t3.cell(3, 0).text = "立项背景与目标"
    t3.cell(3, 1).merge(t3.cell(3, 3))
    t3.cell(3, 1).text = (
        "为彻底解决企业客户处理长篇公文与扫描标书时的格式跑样问题，本项目拟构建纯本地离线式"
        "高性能排版引擎。重点突破混合流排版、双轨表格无损抽取以及公章/手写签名高精识别，"
        "保障在无外网环境下的毫秒级稳定交付。"
    )

    # Row 4: 技术评审意见 [跨3列]
    t3.cell(4, 0).text = "技术专家评审意见"
    t3.cell(4, 1).merge(t3.cell(4, 3))
    t3.cell(4, 1).text = "方案技术路线清晰，性能指标切合实际，本地化安全合规，同意立项。\n\n评审组长：张明远    日期：2026年10月14日"

    # Row 5: 财务总监审批 [跨3列]
    t3.cell(5, 0).text = "财务预算审批意见"
    t3.cell(5, 1).merge(t3.cell(5, 3))
    t3.cell(5, 1).text = "预算编制合理，资金来源落实到位，准予列支。\n\n审批人：李建国    日期：2026年10月15日"

    # 样式渲染
    for r_idx in range(6):
        for c_idx in range(4):
            cell = t3.cell(r_idx, c_idx)
            is_lbl = (c_idx == 0) or (r_idx in [1, 2] and c_idx == 2)
            bg = "F1F5F9" if is_lbl else "FFFFFF"
            set_cell_background(cell, bg)
            set_cell_margins(cell, top=80, bottom=80, left=100, right=100)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            p = cell.paragraphs[0]
            if is_lbl:
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                add_run(p, p.text, font_name="Microsoft YaHei", size_pt=9.5, bold=True, color_hex="1E293B")
            else:
                p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                add_run(p, p.text, font_name="Microsoft YaHei", size_pt=9, color_hex="334155")
            if len(p.runs) > 1:
                p.runs[0]._r.getparent().remove(p.runs[0]._r)

    doc.save(out_path)
    print(f"[OK] Generated: {out_path.name}")


# =========================================================================
# 五、 文档生成器 3：图片和章类型 (03_图章类型_商务采购合同与盖章确认书.docx)
# =========================================================================

def build_images_and_seal_document(out_path: Path):
    """生成带有技术插图、性能图表与公章/私章的测试 Word 文档。"""
    doc = Document()
    configure_header_footer(doc, "纸序 PDF 软件产品采购与服务保密合作协议")

    # 1. 标题
    p_title = doc.add_paragraph()
    p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_title.paragraph_format.space_before = Pt(22)
    p_title.paragraph_format.space_after = Pt(4)
    add_run(p_title, "企业级软件授权采购与保密服务协议", font_name="SimHei", size_pt=22, bold=True, color_hex="1E293B")

    p_sub = doc.add_paragraph()
    p_sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_sub.paragraph_format.space_after = Pt(16)
    add_run(p_sub, "合同编号：ZX-2026-CT-0992  |  签约地点：中国·北京", font_name="Microsoft YaHei", size_pt=10, color_hex="64748B")

    # 签约双方信息
    p_parties = doc.add_paragraph()
    p_parties.paragraph_format.space_after = Pt(12)
    add_run(p_parties, "甲方（采购方）：", font_name="Microsoft YaHei", size_pt=10.5, bold=True, color_hex="1E293B")
    add_run(p_parties, "北京华盛智联软件工程股份有限公司\n", font_name="Microsoft YaHei", size_pt=10.5, color_hex="334155")
    add_run(p_parties, "乙方（供应方）：", font_name="Microsoft YaHei", size_pt=10.5, bold=True, color_hex="1E293B")
    add_run(p_parties, "纸序数字科技有限公司\n", font_name="Microsoft YaHei", size_pt=10.5, color_hex="334155")
    add_run(
        p_parties,
        "鉴于甲乙双方友好协商，就甲方采购乙方‘纸序 PDF’企业级无损转换与智能排版处理组件事宜，达成如下法律协议并共同恪守：",
        font_name="Microsoft YaHei",
        size_pt=10,
        italic=True,
        color_hex="64748B",
    )

    # 条款 1：技术架构与服务范围 + 插图 1
    add_heading_1(doc, "第一条 软件授权范围与系统架构")
    add_body_paragraph(
        doc,
        "乙方承诺向甲方交付纸序 PDF 工业级独立运行时套件。该套件包含原生 PyQt6 表现交互层、业务流水线编排模块及底层高性能渲染引擎，支持在甲方内网环境中完全离线部署。"
    )

    # 插入插图 1：架构拓扑图
    arch_img_path = ASSETS_DIR / "arch_diagram.png"
    if arch_img_path.exists():
        p_img1 = doc.add_paragraph()
        p_img1.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_img1.paragraph_format.space_before = Pt(8)
        p_img1.paragraph_format.space_after = Pt(4)
        run_img1 = p_img1.add_run()
        run_img1.add_picture(str(arch_img_path), width=Inches(5.8))

        p_caption1 = doc.add_paragraph()
        p_caption1.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_caption1.paragraph_format.space_after = Pt(12)
        add_run(p_caption1, "图 1-1 纸序 PDF 核心服务架构与流水线拓扑示意图", font_name="Microsoft YaHei", size_pt=9, bold=True, color_hex="64748B")

    # 条款 2：性能基准与吞吐量承诺 + 插图 2
    add_heading_1(doc, "第二条 交付验收标准与性能基准")
    add_body_paragraph(
        doc,
        "双方共同约定：在甲方标准硬件配置（8核 CPU / 16GB RAM）测试环境中，乙方软件在处理常规图文混合 PDF 时的吞吐性能不得低于以下实测基准线指标："
    )

    # 插入插图 2：性能对比图
    perf_img_path = ASSETS_DIR / "chart_performance.png"
    if perf_img_path.exists():
        p_img2 = doc.add_paragraph()
        p_img2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_img2.paragraph_format.space_before = Pt(8)
        p_img2.paragraph_format.space_after = Pt(4)
        run_img2 = p_img2.add_run()
        run_img2.add_picture(str(perf_img_path), width=Inches(5.4))

        p_caption2 = doc.add_paragraph()
        p_caption2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_caption2.paragraph_format.space_after = Pt(12)
        add_run(p_caption2, "图 1-2 纸序 PDF 核心转换管道吞吐量基准指标 (页/秒)", font_name="Microsoft YaHei", size_pt=9, bold=True, color_hex="64748B")

    # 换页进入正式签署与盖章页
    doc.add_page_break()

    # 条款 3：合同签署与法律效力
    add_heading_1(doc, "第三条 协议签署与盖章确认")
    add_body_paragraph(
        doc,
        "本协议自双方加盖企业公章或合同专用章、并经法定代表人或授权代表签字之日起正式生效。本协议一式贰份，甲乙双方各执壹份，具有同等法律效力。"
    )

    # 盖章核心测试区：双栏表格（左侧甲方，右侧乙方）
    seal_table = doc.add_table(rows=1, cols=2)
    seal_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    # 签署区无外部框线，保持公文整洁
    col_w = Inches(3.2)
    seal_table.cell(0, 0).width = col_w
    seal_table.cell(0, 1).width = col_w

    # 左侧：甲方华盛智联
    cell_a = seal_table.cell(0, 0)
    set_cell_margins(cell_a, top=100, bottom=100, left=80, right=80)
    p_a = cell_a.paragraphs[0]
    p_a.paragraph_format.line_spacing = 1.3
    add_run(p_a, "甲方（盖章）：\n", font_name="Microsoft YaHei", size_pt=10.5, bold=True, color_hex="1E293B")
    add_run(p_a, "北京华盛智联软件工程股份有限公司\n\n", font_name="Microsoft YaHei", size_pt=10.5, color_hex="1E293B")
    add_run(p_a, "法定代表人（签字）：", font_name="Microsoft YaHei", size_pt=10, color_hex="334155")
    add_run(p_a, " 李 建 国 \n", font_name="SimSun", size_pt=12, bold=True, color_hex="0F172A")
    add_run(p_a, "签署日期：2026 年 10 月 15 日\n\n", font_name="Microsoft YaHei", size_pt=10, color_hex="475569")

    # 甲方印章：华盛智联技术服务专用章
    seal_tech_path = ASSETS_DIR / "seal_tech.png"
    if seal_tech_path.exists():
        p_seal_a = cell_a.add_paragraph()
        p_seal_a.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run_sa = p_seal_a.add_run()
        run_sa.add_picture(str(seal_tech_path), width=Inches(1.85))

    # 右侧：乙方纸序科技
    cell_b = seal_table.cell(0, 1)
    set_cell_margins(cell_b, top=100, bottom=100, left=80, right=80)
    p_b = cell_b.paragraphs[0]
    p_b.paragraph_format.line_spacing = 1.3
    add_run(p_b, "乙方（盖章）：\n", font_name="Microsoft YaHei", size_pt=10.5, bold=True, color_hex="1E293B")
    add_run(p_b, "纸序数字科技有限公司\n\n", font_name="Microsoft YaHei", size_pt=10.5, color_hex="1E293B")
    add_run(p_b, "法定代表人（签字）：", font_name="Microsoft YaHei", size_pt=10, color_hex="334155")
    add_run(p_b, " 张 伟 \n", font_name="SimSun", size_pt=12, bold=True, color_hex="0F172A")
    add_run(p_b, "签署日期：2026 年 10 月 15 日\n\n", font_name="Microsoft YaHei", size_pt=10, color_hex="475569")

    # 乙方印章：纸序合同专用章 + 法人私章
    seal_contract_path = ASSETS_DIR / "seal_contract.png"
    seal_legal_path = ASSETS_DIR / "seal_legal.png"
    if seal_contract_path.exists():
        p_seal_b = cell_b.add_paragraph()
        p_seal_b.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run_sb = p_seal_b.add_run()
        run_sb.add_picture(str(seal_contract_path), width=Inches(1.85))
        if seal_legal_path.exists():
            run_sb.add_text("  ")
            run_sb.add_picture(str(seal_legal_path), width=Inches(0.75))

    # 骑缝章说明与测试
    add_heading_2(doc, "附录：骑缝章测试样标")
    add_body_paragraph(
        doc,
        "注：以下为测试页面边缘骑缝章识别与边缘截断专用样标，模拟多页合同右侧装订骑缝戳记。"
    )
    half_seal_path = ASSETS_DIR / "seal_half.png"
    if half_seal_path.exists():
        p_half = doc.add_paragraph()
        p_half.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        run_half = p_half.add_run()
        run_half.add_picture(str(half_seal_path), width=Inches(1.1))

    doc.save(out_path)
    print(f"[OK] Generated: {out_path.name}")


# =========================================================================
# 六、 文档生成器 4：综合全要素样卷 (04_综合类型_纸序PDF全要素演示样稿.docx)
# =========================================================================

def build_comprehensive_document(out_path: Path):
    """生成集文稿、复杂表格、数据图表与印章签署于一体的综合演示样稿。"""
    doc = Document()
    configure_header_footer(doc, "纸序 PDF 工业级全功能特性综合演示与测试文档")

    # 1. 封面 / 标题
    p_title = doc.add_paragraph()
    p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_title.paragraph_format.space_before = Pt(26)
    p_title.paragraph_format.space_after = Pt(4)
    add_run(p_title, "纸序 PDF 综合能力评估与全要素测试报告", font_name="SimHei", size_pt=22, bold=True, color_hex="1E3A8A")

    p_sub = doc.add_paragraph()
    p_sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_sub.paragraph_format.space_after = Pt(16)
    add_run(p_sub, "包含规范公文文本、财务斑马表、多维评估矩阵、高保真拓扑图表与公章签署", font_name="Microsoft YaHei", size_pt=10.5, color_hex="64748B")

    # 导读提示卡片
    add_callout_box(
        doc,
        "全要素验证清单 (All-in-One Checklist)",
        "✅ 1. 文稿排版层：多级标题、中文两字符首行缩进、有序/无序列表、彩色提示块；\n"
        "✅ 2. 结构化表格层：标准财务斑马纹表、金额右对齐、多层 Colspan / Rowspan 矩阵；\n"
        "✅ 3. 矢量图表层：系统架构流转拓扑图、吞吐量基准柱状图；\n"
        "✅ 4. 印章图层：半透明圆形合同专用章、技术服务专用章、方形法人印、半圆骑缝章。"
    )

    # 第一部分：文稿
    add_heading_1(doc, "一、 产品定位与用户价值说明")
    add_body_paragraph(
        doc,
        "纸序 PDF 是一款面向普通业务人员与政企客户的离线文档处理套件。产品恪守‘零学习成本、防错优于报错、一键直达’交互准则，帮助用户在极短时间内完成 PDF 阅读、页面旋转、多文档归集、智能表格提取以及高质量 Word 转码。"
    )

    # 第二部分：图表
    add_heading_1(doc, "二、 核心系统拓扑与性能指标")
    arch_img_path = ASSETS_DIR / "arch_diagram.png"
    if arch_img_path.exists():
        p_img = doc.add_paragraph()
        p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_img.add_run().add_picture(str(arch_img_path), width=Inches(5.5))
        p_cap = doc.add_paragraph()
        p_cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        add_run(p_cap, "图 1 核心系统流水线架构拓扑", font_name="Microsoft YaHei", size_pt=9, bold=True, color_hex="64748B")

    # 换页
    doc.add_page_break()

    # 第三部分：表格
    add_heading_1(doc, "三、 经营核算与预算执行表")
    t = doc.add_table(rows=4, cols=4)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    set_table_borders(t, color="94A3B8", sz="6")
    headers = ["核算科目", "2025年实际值", "2026年预算值", "增长率"]
    widths = [Inches(2.2), Inches(1.6), Inches(1.6), Inches(1.0)]
    for i, h in enumerate(headers):
        c = t.cell(0, i)
        c.width = widths[i]
        set_cell_background(c, "1E3A8A")
        set_cell_margins(c, top=80, bottom=80, left=80, right=80)
        p = c.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if i in [1, 2, 3] else WD_ALIGN_PARAGRAPH.LEFT
        add_run(p, h, font_name="Microsoft YaHei", size_pt=9.5, bold=True, color_hex="FFFFFF")

    rows = [
        ("本地离线转换套件", "¥ 2,800,000.00", "¥ 3,200,000.00", "+14.3%"),
        ("企业级 OCR 识别包", "¥ 1,450,000.00", "¥ 1,800,000.00", "+24.1%"),
        ("年度技术支持服务", "¥ 750,000.00", "¥ 850,000.00", "+13.3%"),
    ]
    for r_idx, r_vals in enumerate(rows, start=1):
        for c_idx, val in enumerate(r_vals):
            c = t.cell(r_idx, c_idx)
            c.width = widths[c_idx]
            set_cell_background(c, "F8FAFC" if r_idx % 2 == 0 else "FFFFFF")
            set_cell_margins(c, top=70, bottom=70, left=80, right=80)
            p = c.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if c_idx in [1, 2, 3] else WD_ALIGN_PARAGRAPH.LEFT
            add_run(p, val, font_name="Microsoft YaHei", size_pt=9, color_hex="1E293B")

    # 第四部分：签署与印章
    add_heading_1(doc, "四、 报告确认与签章生效")
    add_body_paragraph(doc, "本技术测试报告经双方联合验证签署并加盖印章确认无误。")

    seal_table = doc.add_table(rows=1, cols=2)
    seal_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    w_col = Inches(3.2)
    seal_table.cell(0, 0).width = w_col
    seal_table.cell(0, 1).width = w_col

    c0 = seal_table.cell(0, 0)
    p0 = c0.paragraphs[0]
    add_run(p0, "验证方：华盛智联软件\n授权代表：李建国\n日期：2026年10月15日\n", font_name="Microsoft YaHei", size_pt=10, color_hex="1E293B")
    seal_tech_path = ASSETS_DIR / "seal_tech.png"
    if seal_tech_path.exists():
        c0.add_paragraph().add_run().add_picture(str(seal_tech_path), width=Inches(1.8))

    c1 = seal_table.cell(0, 1)
    p1 = c1.paragraphs[0]
    add_run(p1, "报告编制方：纸序数字科技\n授权代表：张伟\n日期：2026年10月15日\n", font_name="Microsoft YaHei", size_pt=10, color_hex="1E293B")
    seal_contract_path = ASSETS_DIR / "seal_contract.png"
    seal_legal_path = ASSETS_DIR / "seal_legal.png"
    if seal_contract_path.exists():
        p_s = c1.add_paragraph()
        run_s = p_s.add_run()
        run_s.add_picture(str(seal_contract_path), width=Inches(1.8))
        if seal_legal_path.exists():
            run_s.add_text(" ")
            run_s.add_picture(str(seal_legal_path), width=Inches(0.7))

    doc.save(out_path)
    print(f"[OK] Generated: {out_path.name}")


# =========================================================================
# 七、 统一入口
# =========================================================================

def main():
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Step 1: Generating seal assets and charts...")
    seal1 = make_round_seal("纸序数字科技有限公司", "合同专用章", "91110108MA01TEST88")
    seal1.save(ASSETS_DIR / "seal_contract.png")

    seal2 = make_round_seal("华盛智联软件工程股份有限公司", "技术服务专用章", "91110105MA02TECH66")
    seal2.save(ASSETS_DIR / "seal_tech.png")

    seal3 = make_square_seal("张伟之印")
    seal3.save(ASSETS_DIR / "seal_legal.png")

    seal_half = make_half_seal("纸序数字科技有限公司")
    seal_half.save(ASSETS_DIR / "seal_half.png")

    make_arch_diagram(ASSETS_DIR / "arch_diagram.png")
    make_performance_chart(ASSETS_DIR / "chart_performance.png")
    print("Assets generated successfully.\n")

    print("Step 2: Generating test Word documents...")
    doc1 = OUTPUT_DIR / "01_文稿类型_规范通知与技术备忘录.docx"
    doc2 = OUTPUT_DIR / "02_表格类型_财务报表与多维合并单据.docx"
    doc3 = OUTPUT_DIR / "03_图章类型_商务采购合同与盖章确认书.docx"
    doc4 = OUTPUT_DIR / "04_综合类型_纸序PDF全要素演示样稿.docx"

    build_manuscript_document(doc1)
    build_table_document(doc2)
    build_images_and_seal_document(doc3)
    build_comprehensive_document(doc4)

    print("\nAll 4 test Word documents generated successfully in:")
    print(f" -> {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
