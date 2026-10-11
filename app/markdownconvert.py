"""High-fidelity Markdown to PDF layout using full Markdown/HTML parsing, high-DPI rasterization, and vector typography."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import tempfile
import urllib.parse
import urllib.request

import markdown as md_lib
from PyQt6 import QtSvg
from PyQt6.QtCore import QByteArray, QMarginsF, QRectF, QSize, QSizeF, Qt, QUrl
from PyQt6.QtGui import (
    QFont, QFontDatabase, QImage, QImageReader, QPageLayout, QPageSize, QPainter,
    QPdfWriter, QTextDocument,
)

from app.output import check_cancel, commit_file, stamp
from app.pdfio import PdfError, PdfStore, WriteResult


@dataclass(frozen=True)
class MarkdownPdfOptions:
    landscape: bool = False
    margin_mm: float = 15
    font_size: float = 11


_font_family = None


def markdown_font():
    global _font_family
    if _font_family is None:
        import pymupdf
        font_id = QFontDatabase.addApplicationFontFromData(QByteArray(pymupdf.Font("cjk").buffer))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if not families:
            raise PdfError("无法加载内置中文字体。")
        _font_family = families[0]
    return _font_family


class _LocalDocument(QTextDocument):
    def __init__(self, root, width, height):
        super().__init__()
        self.root = root.resolve()
        self.max_width = width
        self.max_height = height
        self.warnings = []
        self._cache = {}
        self.setBaseUrl(QUrl.fromLocalFile(str(self.root) + os.sep))

    def loadResource(self, resource_type, name):
        if resource_type != QTextDocument.ResourceType.ImageResource.value:
            return None
        url_str = name.toString()
        if url_str in self._cache:
            return self._cache[url_str]

        image = self._load_image(name, url_str)
        self._cache[url_str] = image
        return image

    def _load_image(self, name, url_str):
        # 1. Network image / badge (e.g. shields.io, github avatars, web links)
        if name.scheme() in ("http", "https"):
            try:
                encoded_url = urllib.parse.quote(url_str, safe=":/%?=&,-+#@_")
                req = urllib.request.Request(
                    encoded_url,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = resp.read()
                return self._bytes_to_image(data, url_str)
            except Exception as exc:
                self.warnings.append(f"图片未加载：{url_str}")
                return None

        # 2. Local image
        candidates = []
        if name.isLocalFile():
            candidates.append(Path(name.toLocalFile()))
        candidates.append(self.root / name.path().lstrip("/"))
        candidates.append(Path(name.path()))
        candidates.append(Path.cwd() / name.path().lstrip("/"))

        target = None
        for c in candidates:
            try:
                if c.is_file():
                    target = c
                    break
            except OSError:
                pass

        if target is None:
            self.warnings.append(f"图片未找到：{url_str}")
            return None

        try:
            data = target.read_bytes()
            return self._bytes_to_image(data, target.name)
        except Exception as exc:
            self.warnings.append(f"图片无法读取：{target.name}")
            return None

    def _bytes_to_image(self, data, name_hint):
        is_svg = name_hint.lower().endswith(".svg") or b"<svg" in data[:1024]
        if is_svg:
            renderer = QtSvg.QSvgRenderer(QByteArray(data))
            if not renderer.isValid():
                return None
            sz = renderer.defaultSize()
            scale = 2.5  # High-DPI for crisp vector badges and icons
            w, h = max(1, int(sz.width() * scale)), max(1, int(sz.height() * scale))
            img = QImage(w, h, QImage.Format.Format_ARGB32)
            img.fill(0)
            painter = QPainter(img)
            renderer.render(painter)
            painter.end()
            return img

        img = QImage.fromData(data)
        if img.isNull():
            return None
        # Keep high definition, but scale down if it exceeds printable width
        if img.width() > self.max_width:
            img = img.scaledToWidth(self.max_width, Qt.TransformationMode.SmoothTransformation)
        return img


def _build_html_template(body_html, font_family, font_size):
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
body {{
    font-family: "{font_family}", -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
    font-size: {font_size}pt;
    line-height: 1.6;
    color: #1F2937;
    margin: 0;
    padding: 0;
}}
h1, h2, h3, h4, h5, h6 {{
    color: #0F766E;
    font-weight: 700;
    margin-top: 1.2em;
    margin-bottom: 0.5em;
    line-height: 1.3;
}}
h1 {{ font-size: {font_size * 1.8:.1f}pt; border-bottom: 2px solid #E2EEEC; padding-bottom: 0.3em; }}
h2 {{ font-size: {font_size * 1.4:.1f}pt; border-bottom: 1px solid #E2EEEC; padding-bottom: 0.25em; }}
h3 {{ font-size: {font_size * 1.2:.1f}pt; }}
p {{ margin-top: 0; margin-bottom: 0.8em; }}
table {{
    border-collapse: collapse;
    width: 100%;
    margin: 1em 0;
    border: 1px solid #D5E2DE;
}}
th, td {{
    border: 1px solid #D5E2DE;
    padding: 6px 10px;
    text-align: left;
}}
th {{
    background-color: #F0FDFA;
    font-weight: 600;
    color: #134E4A;
}}
tr:nth-child(even) {{ background-color: #F8FBFA; }}
code {{
    font-family: Consolas, "Courier New", monospace;
    font-size: 0.9em;
    background-color: #F3F6F5;
    padding: 2px 5px;
    border-radius: 4px;
    color: #0F766E;
    border: 1px solid #E2EEEC;
}}
pre {{
    background-color: #F8FBFA;
    border: 1px solid #E2EEEC;
    border-radius: 6px;
    padding: 10px;
    overflow-x: auto;
    margin: 1em 0;
}}
pre code {{
    background-color: transparent;
    padding: 0;
    border: none;
    color: #134E4A;
}}
blockquote {{
    border-left: 4px solid #0F766E;
    padding: 4px 12px;
    margin: 1em 0;
    color: #4D6B67;
    background-color: #F3F6F5;
    border-radius: 0 4px 4px 0;
}}
hr {{
    border: none;
    border-top: 1px solid #E2EEEC;
    margin: 1.5em 0;
}}
img {{
    max-width: 100%;
    vertical-align: middle;
}}
a {{
    color: #0F766E;
    text-decoration: none;
}}
</style>
</head>
<body>
{body_html}
</body>
</html>"""


def markdown_to_pdf(source, target, *, options=None, progress_callback=None, cancel=None):
    import pymupdf
    source, target = Path(source).resolve(), Path(target).resolve()
    options = options or MarkdownPdfOptions()
    if source == target or target.suffix.lower() != ".pdf" or source.suffix.lower() not in (".md", ".markdown"):
        raise PdfError("请选择 Markdown 源文件和不同的 PDF 输出路径。")
    if not 5 <= options.font_size <= 36 or not 5 <= options.margin_mm <= 40:
        raise PdfError("字号须在 5～36 pt，页边距须在 5～40 mm。")
    check_cancel(cancel)
    original = stamp(source)
    expected = PdfStore()._check_destination(str(target))
    try:
        markdown_text = source.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise PdfError("Markdown 需使用 UTF-8 编码，请在文本编辑器中另存为 UTF-8 后重试。") from exc
    if not markdown_text.strip():
        raise PdfError("Markdown 内容为空。")
    if progress_callback:
        progress_callback(0, 0, "正在渲染 Markdown")

    # Render Markdown to standard HTML with tables, code blocks, lists
    body_html = md_lib.markdown(
        markdown_text,
        extensions=["extra", "tables", "fenced_code", "sane_lists", "nl2br"]
    )
    full_html = _build_html_template(body_html, markdown_font(), options.font_size)

    with tempfile.TemporaryDirectory(prefix=".zhixu-md-", dir=target.parent) as folder:
        temp = Path(folder) / "output.pdf"
        writer = QPdfWriter(str(temp))
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        writer.setPageOrientation(QPageLayout.Orientation.Landscape if options.landscape else QPageLayout.Orientation.Portrait)
        writer.setPageMargins(QMarginsF(*([options.margin_mm] * 4)), QPageLayout.Unit.Millimeter)
        writer.setResolution(150)  # 150 DPI for crystal clear high-DPI rendering
        writer.setCreator("纸序 PDF")
        writer.setTitle(source.stem)

        doc = _LocalDocument(source.parent, writer.width(), writer.height())
        doc.setUndoRedoEnabled(False)
        doc.setDefaultFont(QFont(markdown_font(), int(options.font_size)))
        doc.setDocumentMargin(0)
        doc.documentLayout().setPaintDevice(writer)
        doc.setPageSize(QSizeF(writer.width(), writer.height()))
        doc.setHtml(full_html)

        width, height = writer.width(), writer.height()
        if doc.idealWidth() > width + 1:
            doc.warnings.append("部分内容较宽，请核对输出；可改用横向纸张或调整原文表格、代码。")
        doc.documentLayout().documentSize()
        count = doc.pageCount()
        painter = QPainter(writer)
        try:
            if not painter.isActive():
                raise PdfError("无法创建 PDF 输出。")
            for index in range(count):
                check_cancel(cancel)
                if index and not writer.newPage():
                    raise PdfError("无法写入下一页。")
                painter.save()
                painter.translate(0, -index * height)
                doc.drawContents(painter, QRectF(0, index * height, width, height))
                painter.restore()
                if progress_callback:
                    progress_callback(index + 1, count, f"正在输出第 {index + 1} / {count} 页")
        finally:
            painter.end()
            doc.documentLayout().setPaintDevice(None)
            del writer
        check_cancel(cancel)
        with pymupdf.open(temp) as check:
            if check.is_repaired or check.page_count != count or any(p.rect.is_empty for p in check):
                raise PdfError("Markdown 输出未通过 PDF 核对，未提交结果。")
        if stamp(source) != original:
            raise PdfError("Markdown 在转换期间发生变化，未提交输出。")
        commit_file(temp, target, expected, cancel=cancel)
        return WriteResult(str(target), warnings=tuple(doc.warnings))
