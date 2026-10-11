"""Page numbering is an output option; input pages stay untouched."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PageNumberOptions:
    mode: str = "visible"
    skip: int = 0
    start: int = 1
    format: str = "number"
    position: str = "bottom-center"
    mirror: bool = False
    font_size: float = 10
    margin_mm: float = 10
    label_style: str = "D"

    def validate(self, count):
        if self.mode not in ("visible", "labels") or not 0 <= self.skip < count or self.start < 1:
            raise ValueError("请至少保留一页用于编号，起始编号须大于零。")
        if self.format not in ("number", "fraction", "chinese", "english", "dash"):
            raise ValueError("不支持的页码格式。")
        if self.position not in {f"{v}-{h}" for v in ("top", "bottom") for h in ("left", "center", "right")}:
            raise ValueError("不支持的页码位置。")
        if not 5 <= self.font_size <= 36 or not 2 <= self.margin_mm <= 40:
            raise ValueError("页码字号须为 5～36 pt，距边距离须为 2～40 mm。")
        if self.label_style not in ("D", "r", "R", "a", "A"):
            raise ValueError("不支持的阅读器页码标签。")

    def text(self, number, total):
        return {
            "number": str(number), "fraction": f"{number} / {total}",
            "chinese": f"第 {number} 页，共 {total} 页",
            "english": f"Page {number} of {total}", "dash": f"- {number} -",
        }[self.format]


def stamp_number(page, text, options, index):
    import pymupdf as p
    margin = options.margin_mm * 72 / 25.4
    width, height = page.rect.width, page.rect.height
    box_height = options.font_size * 2
    if width <= 2 * margin or height <= 2 * margin + box_height:
        raise ValueError(f"第 {index + 1} 页太小，无法在指定边距内添加页码。")
    vertical, horizontal = options.position.split("-")
    if options.mirror:
        horizontal = "right" if index % 2 == 0 else "left"
    y = margin if vertical == "top" else height - margin - box_height
    visible = p.Rect(margin, y, width - margin, y + box_height)
    font = "helv"
    cjk = any(ord(c) > 255 for c in text)
    if cjk:
        from app.pdfedits import cjk_font_buffer
        font = "ZhiXuCJK"
        page.insert_font(fontname=font, fontbuffer=cjk_font_buffer())
    result = page.insert_textbox(visible * page.derotation_matrix, text,
        fontname=font, fontsize=options.font_size, color=(0, 0, 0),
        align={"left": 0, "center": 1, "right": 2}[horizontal], rotate=page.rotation)
    if result < 0:
        raise ValueError(f"第 {index + 1} 页放不下页码，请减小字号或边距。")
    return cjk


def apply_page_numbers(doc, options, progress_callback=None, cancel=None):
    from app.output import check_cancel
    options.validate(doc.page_count)
    if options.mode == "labels":
        rules = [{"startpage": 0, "prefix": "", "style": ""}] if options.skip else []
        rules.append({"startpage": options.skip, "prefix": "", "style": options.label_style,
                      "firstpagenum": options.start})
        doc.set_page_labels(rules)
        return False
    subset = False
    total = doc.page_count - options.skip
    for index in range(options.skip, doc.page_count):
        check_cancel(cancel)
        subset |= stamp_number(doc[index], options.text(options.start + index - options.skip, total), options, index)
        if progress_callback:
            progress_callback(index - options.skip + 1, total, "正在添加页码")
    return subset
