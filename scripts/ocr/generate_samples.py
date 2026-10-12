"""合成 PoC / 验收测试样本 PDF。

原理：

* 准备已知真值的"电子 PDF"；
* 使用 PyMuPDF 把每页渲染成位图（不同 DPI）；
* 再把位图嵌入为整页图片，得到"扫描件"PDF；
* 真值文字以 JSON 形式随样本一并保存，便于后续 CER 评估。

PoC 用 6 类小样本；验收用 30 页样本（``--mode acceptance``）。
"""
from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, asdict
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont


def _find_cjk_font(size: int) -> ImageFont.FreeTypeFont:
    """查找系统中可用的中文字体；找不到则使用 PIL 默认字体（英文回退）。"""
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",       # Microsoft YaHei
        r"C:\Windows\Fonts\msyh.ttf",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\Deng.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


# --------------------------------------------------------------------------- #
# 测试样本原始内容（已知真值）
# --------------------------------------------------------------------------- #


SAMPLE_CHINESE_CLEAR = [
    "纸序 PDF 是一款本地化的 PDF 工具。",
    "支持扫描件 OCR 识别，无需联网即可使用。",
    "识别结果以隐藏文字层形式写入 PDF，不改变原始排版。",
    "支持中文、英文与数字混排。",
    "识别完成后，可在纸序 PDF 中使用 Ctrl+F 搜索关键词。",
]


SAMPLE_BILINGUAL_CONTRACT = [
    "本合同（This Contract）由甲方 Party A 与乙方 Party B 签订。",
    "合同金额 Contract Amount：人民币壹拾贰万元整 (CNY 120,000.00)。",
    "签订日期 Signing Date：2026 年 10 月 12 日 October 12, 2026。",
    "交货期 Delivery Period：自签订之日起 30 个工作日内。",
    "违约责任 Liability for Breach：按合同法相关规定执行。",
]


SAMPLE_INVOICE_DIGITS = [
    "报价单 QUOTATION",
    "项目 Item 数量 Qty 单价 Unit 金额 Amount",
    "扫描件 OCR 服务 10 页 ¥300.00 ¥3,000.00",
    "文档转换 Conversion 5 份 ¥120.00 ¥600.00",
    "合计 Total ¥3,600.00",
    "税率 VAT 13% ¥468.00",
    "应付总额 Grand Total ¥4,068.00",
]


@dataclass
class SampleSpec:
    name: str
    category: str
    rotation: int
    dpi: int
    lines: list[str]


def _render_lines_to_image(lines: list[str], *, dpi: int,
                           rotation: int = 0) -> Image.Image:
    """用 PIL 把文字渲染到位图，作为"扫描件"源图。"""
    # 1 inch = dpi px，所以 A4 200DPI ≈ 1654×2205（英寸 × DPI）
    width = int(8.27 * dpi)
    height = int(11.69 * dpi)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font_size = max(18, dpi // 10)
    font = _find_cjk_font(font_size)
    # 把多行文字按行高均匀分布
    line_spacing = int(font_size * 2.0)
    margin = int(dpi * 0.75)
    y = margin
    for line in lines:
        draw.text((margin, y), line, fill="black", font=font)
        y += line_spacing
    if rotation:
        image = image.rotate(rotation, expand=True, fillcolor="white")
    return image


def _build_electronic_pdf(path: Path, spec: SampleSpec) -> None:
    """构造一个含文字层的电子 PDF（用于 mixed 样本的电子页）。"""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_textbox(
        page.rect,
        "\n".join(spec.lines),
        fontname="SimSun",
        fontsize=12,
    )
    doc.save(path)
    doc.close()


def _rasterize_to_scanned_pdf(
    source_pdf: Path,
    output_pdf: Path,
    *,
    dpi: int,
) -> None:
    """把电子 PDF 每页渲染成位图，再嵌回 PDF，得到"扫描件"。"""
    src = pymupdf.open(source_pdf)
    dst = pymupdf.open()
    for page in src:
        pix = page.get_pixmap(dpi=dpi)
        new_page = dst.new_page(width=pix.width, height=pix.height)
        new_page.insert_image(new_page.rect, pixmap=pix)
    dst.save(output_pdf, deflate=True, garbage=4)
    src.close()
    dst.close()


def _build_scanned_pdf_from_text(
    lines: list[str],
    *,
    output_pdf: Path,
    dpi: int,
    rotation: int,
) -> None:
    """直接从已知真值文字生成扫描 PDF，不经过电子 PDF 中转。"""
    image = _render_lines_to_image(lines, dpi=dpi, rotation=rotation)
    import io
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    doc = pymupdf.open()
    page = doc.new_page(width=image.width, height=image.height)
    page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(output_pdf, deflate=True, garbage=4)
    doc.close()


# --------------------------------------------------------------------------- #
# 6 类 PoC 样本 + 30 页验收样本
# --------------------------------------------------------------------------- #


POC_SPECS: list[SampleSpec] = [
    SampleSpec("chinese_clear", "清晰中文扫描", 0, 200, SAMPLE_CHINESE_CLEAR),
    SampleSpec("bilingual_contract", "中英混排合同", 0, 200, SAMPLE_BILINGUAL_CONTRACT),
    SampleSpec("invoice_digits", "数字金额报价单", 0, 200, SAMPLE_INVOICE_DIGITS),
    SampleSpec("rotated_90", "旋转 90°", 90, 200, SAMPLE_CHINESE_CLEAR),
    SampleSpec("low_resolution", "低分辨率 ≤150 DPI", 0, 130, SAMPLE_CHINESE_CLEAR),
]


def _build_mixed_sample(output_dir: Path, gamma_dpi: int = 200) -> Path:
    """合成一份混合 PDF：前半页含文字，后半页是扫描。"""
    path = output_dir / "mixed_electronic_plus_scanned.pdf"
    doc = pymupdf.open()
    # 第一页：电子文字（可搜索）
    page1 = doc.new_page(width=595, height=842)
    page1.insert_textbox(
        page1.rect,
        "此页为电子文字，不可被 OCR 跳过。电子文字用于验证 page_detector 的跳过逻辑。",
        fontname="SimSun",
        fontsize=12,
    )
    # 第二页：扫描位图（用 PIL 渲染，避免 PyMuPDF 字体问题）
    _build_scanned_pdf_from_text(
        SAMPLE_CHINESE_CLEAR,
        output_pdf=output_dir / "_tmp_scanned.pdf",
        dpi=gamma_dpi,
        rotation=0,
    )
    scanned = pymupdf.open(output_dir / "_tmp_scanned.pdf")
    doc.insert_pdf(scanned)
    scanned.close()
    (output_dir / "_tmp_scanned.pdf").unlink(missing_ok=True)
    doc.save(path)
    doc.close()
    return path


def _build_acceptance_corpus(output_dir: Path, total: int = 30) -> list[Path]:
    """合成 30 页真实办公风格的样本，每页之间内容略作变化。"""
    paths: list[Path] = []
    rng = random.Random(20261012)
    bank = [
        SAMPLE_CHINESE_CLEAR,
        SAMPLE_BILINGUAL_CONTRACT,
        SAMPLE_INVOICE_DIGITS,
    ]
    for i in range(total):
        scanned = output_dir / f"acceptance_{i + 1:02d}.pdf"
        _build_scanned_pdf_from_text(
            bank[i % len(bank)],
            output_pdf=scanned,
            dpi=200,
            rotation=0,
        )
        doc = pymupdf.open(scanned)
        meta = doc.metadata or {}
        meta["subject"] = f"acceptance-{i + 1:02d}-seed-{rng.randint(1000, 9999)}"
        doc.set_metadata(meta)
        doc.close()
        paths.append(scanned)
    return paths


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main() -> None:
    parser = argparse.ArgumentParser(description="合成 OCR PoC / 验收样本 PDF")
    parser.add_argument("--out", default="scripts/ocr/samples", help="输出目录")
    parser.add_argument("--mode", choices=("poc", "acceptance", "mixed"), default="poc")
    parser.add_argument("--count", type=int, default=30, help="验收样本页数")
    args = parser.parse_args()

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    truth: dict[str, list[str]] = {}

    if args.mode in ("poc", "mixed"):
        for spec in POC_SPECS:
            scanned = out_dir / f"{spec.name}.pdf"
            _build_scanned_pdf_from_text(
                spec.lines,
                output_pdf=scanned,
                dpi=spec.dpi,
                rotation=spec.rotation,
            )
            truth[spec.name] = spec.lines
        if args.mode == "mixed":
            _build_mixed_sample(out_dir)
            truth["mixed_scan_page"] = SAMPLE_CHINESE_CLEAR

    if args.mode == "acceptance":
        paths = _build_acceptance_corpus(out_dir, total=args.count)
        truth["acceptance"] = SAMPLE_CHINESE_CLEAR
        truth["acceptance_files"] = [str(p.relative_to(out_dir)) for p in paths]

    truth_path = out_dir / "truth.json"
    truth_path.write_text(json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Generated samples into {out_dir}")
    print(f"Truth written to {truth_path}")


if __name__ == "__main__":
    main()