"""Layout-aware text extraction, scan detection, local OCR bridge, and bounded session cache."""
from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from statistics import median
import re

from app.i18n import tr
from app.output import check_cancel
from app.pdf2excel.models import CellModel

_PAGE_NUMBER_RE = re.compile(
    r'^(?:[-—–·\s]*\d{1,5}[-—–·\s]*|page\s*\d+(?:\s*(?:of|/)\s*\d+)?|\d+\s*/\s*\d+|第\s*\d+\s*页(?:\s*共\s*\d+\s*页)?)$',
    re.IGNORECASE,
)
_MONO_FONT = re.compile(r'consolas|courier|mono|menlo|fixed|code', re.I)
_CODE_SYNTAX = re.compile(
    r'^(?:[$>]\s|[\w.]+[-_][\w.-]+\s+\S|(?:cargo|npm|npx|pip3?|python3?|git|curl|docker|node)\s+\S'
    r'|from\s+[\w.]+\s+import\s|import\s+[\w.]|\w+\s*=\s*\S)|\w+\.\w+\('
)


def _literal_code(item):
    text = item['text'].strip()
    if re.fullmatch(r'https?://\S+', text):
        return True
    fonts = [run.get('font', '') for run in item['runs'] if run.get('text', '').strip()]
    return bool(fonts and all(_MONO_FONT.search(font or '') for font in fonts) and _CODE_SYNTAX.search(text))


@dataclass(frozen=True)
class PageSegment:
    segment_id: str
    role: str  # 'heading' | 'body' | 'code' | 'table' | 'header' | 'footer'
    label: str
    text: str
    bbox: tuple[float, float, float, float]  # Display coordinates relative to page top-left
    font_size: float = 0
    ink: str | None = None
    table_id: str = ''
    cell: CellModel | None = None


@dataclass(frozen=True)
class PageExtraction:
    segments: tuple[PageSegment, ...]
    skipped_count: int = 0
    is_scan: bool = False
    via_ocr: bool = False
    selection_snapshot: dict | None = None

    @cached_property
    def _segment_notes(self) -> dict[str, tuple[str, str]]:
        section = ''
        columns = {}
        notes = {}
        for index, seg in enumerate(self.segments):
            if seg.role == 'heading':
                section = 'Section: ' + seg.text[:120]
                notes[seg.segment_id] = ('Section heading', '')
                continue
            parts = [section] if section and seg.role in ('body', 'table') else []
            if seg.cell is not None:
                cell = seg.cell
                if cell.row == 0:
                    for col in range(cell.col, cell.col + cell.col_span):
                        columns[seg.table_id, col] = seg.text[:80]
                    parts.append('Table first row')
                else:
                    first_row = columns.get((seg.table_id, cell.col))
                    if first_row:
                        parts.append('First row of this column: ' + first_row)
            preceding = ''
            if not section and seg.role == 'body' and index and self.segments[index - 1].role == 'body':
                preceding = self.segments[index - 1].text[-160:]
            notes[seg.segment_id] = (' | '.join(parts), preceding)
        return notes

    def reference_for(self, batch: list[PageSegment]) -> str:
        groups = {}
        for index, seg in enumerate(batch):
            note, preceding = self._segment_notes[seg.segment_id]
            if index == 0 and preceding:
                note = 'Preceding passage: ' + preceding
            if note:
                groups.setdefault(note, []).append(seg.segment_id)
        return '\n'.join(f'{", ".join(ids)} — {note}' for note, ids in groups.items())


@dataclass(frozen=True)
class RegionExtraction:
    text: str
    source: str  # 'text' | 'ocr'
    ocr_missing: bool = False
    message: str = ''


def selection_reference(snapshot, area, target: str) -> str:
    """Read a short passage at the selected glyphs from the existing PDF/OCR snapshot."""
    import pymupdf as p
    from app.pdfedits import selected_lines

    if not snapshot or area is None or not target.strip() or len(target) > 320:
        return ''
    area = p.Rect(area)
    for table in snapshot.get('tables', ()):
        if area.intersects(p.Rect(table.bbox)):
            for cell in table.cells:
                if cell.bbox and p.Rect(cell.bbox).contains(area) and cell.text.count(target) == 1:
                    start = cell.text.index(target)
                    return _sentence_around(cell.text, start, start + len(target))
            return ''

    hits = selected_lines(snapshot['raw'], area)
    if not hits:
        return ''
    first, last = hits[0][1][0][0], hits[-1][1][-1][0]
    for block in snapshot['raw'].get('blocks', ()):
        lines = block.get('lines', ())
        if not lines or (block.get('bbox') and not p.Rect(block['bbox']).intersects(area)):
            continue
        matched = [i for i, line in enumerate(lines) if line is hits[0][0] or line is hits[-1][0]]
        if not matched or lines[matched[0]] is not hits[0][0] or lines[matched[-1]] is not hits[-1][0]:
            continue
        lo, hi = matched[0], matched[-1]
        # Adjacent wraps in the same column can complete the sentence.
        def continues(a, b):
            a, b = p.Rect(a['bbox']), p.Rect(b['bbox'])
            return (b.y0 >= (a.y0 + a.y1) / 2 and b.y0 - a.y1 <= a.height
                    and a.x0 < b.x1 and b.x0 < a.x1)

        if lo and continues(lines[lo - 1], lines[lo]):
            lo -= 1
        if hi + 1 < len(lines) and continues(lines[hi], lines[hi + 1]):
            hi += 1
        pieces = []
        offset = 0
        start = end = None
        for line in lines[lo:hi + 1]:
            for span in line.get('spans', ()):
                for char in span.get('chars', ()):
                    if char is first:
                        start = offset
                    pieces.append(char['c'])
                    offset += len(char['c'])
                    if char is last:
                        end = offset
            pieces.append(' ')
            offset += 1
        passage = ''.join(pieces)
        if start is not None and end is not None and ''.join(passage[start:end].split()) == ''.join(target.split()):
            return _sentence_around(passage, start, end)
    return ''


def _sentence_around(passage: str, start: int, end: int) -> str:
    boundary = r'(?<=[。！？])\s*|(?<=[.!?])\s+'
    before = re.split(boundary, passage[max(0, start - 160):start])
    after = re.split(boundary, passage[end:end + 160], maxsplit=1)
    left, right = before[-1], after[0]
    if not any(ch.isalnum() for ch in left + right):
        if len(before) == 1 or not before[-2].strip():
            return ''
        left = before[-2] + ' ' + left
    return (left + passage[start:end] + right).strip()


def _cancel_fn(cancel):
    if cancel is None:
        return None
    if callable(cancel):
        return cancel
    is_set = getattr(cancel, 'is_set', None)
    return is_set if callable(is_set) else None


def _has_translatable_content(text: str) -> bool:
    stripped = (text or '').strip()
    if not stripped:
        return False
    # Skip pure punctuation or standalone numbers/codes without linguistic letters.
    return any(ch.isalpha() for ch in stripped)


def _is_page_number(text: str) -> bool:
    return bool(_PAGE_NUMBER_RE.fullmatch((text or '').strip()))


def _reading_paragraphs(layout):
    """Join wrapped instructions and keep distant fields on their original sides."""
    import pymupdf
    paragraphs = []
    for item in layout:
        if item['kind'] == 'text' and _literal_code(item):
            paragraphs.append(dict(item, code=True, text=item.get('source_text') or item['text']))
            continue
        previous = paragraphs[-1] if paragraphs else None
        if item['kind'] == 'text' and previous and previous['kind'] == 'text' and not previous.get('code'):
            left, top, right, bottom = item['bbox']
            x0, y0, x1, y1 = previous['bbox']
            size = item['size']
            prefix = previous['text'].lstrip()
            hanging = prefix.startswith(('•', '▪', '‣')) and 0 < left - x0 <= 2 * size
            continuation = item['text'][:1].islower() and abs(left - x0) < size / 2
            if ((hanging or continuation) and not previous['heading'] and not item['heading']
                    and previous.get('column') == item.get('column') and abs(previous['size'] - size) < 1
                    and -size / 5 <= top - y1 <= size * 1.05 and x1 > left
                    and not prefix.endswith(('.', '!', '?', '。', '！', '？', ':', '：'))
                    and not item['text'].lstrip().startswith(('•', '▪', '‣', '- ', '* '))):
                paragraphs[-1] = dict(previous, text=prefix + (' ' if not prefix.endswith('-') else '') + item['text'],
                                      runs=previous['runs'] + item['runs'],
                                      bbox=(min(x0, left), y0, max(x1, right), bottom))
                continue
        if item['kind'] == 'text' and '\t' in item['text']:
            # Layout inserts an unpositioned tab between distant columns on one baseline.
            fields = [[]]
            for run in item['runs']:
                if run['text'] == '\t' and 'bbox' not in run:
                    fields.append([])
                else:
                    fields[-1].append(run)
            if len(fields) > 1:
                for runs in fields:
                    bounds = pymupdf.Rect()
                    for run in runs:
                        if 'bbox' in run:
                            bounds |= pymupdf.Rect(run['bbox'])
                    if not bounds.is_empty:
                        paragraphs.append(dict(item, runs=runs, bbox=tuple(bounds),
                                               text=''.join(run['text'] for run in runs)))
                continue
        paragraphs.append(item)
    return paragraphs


def _transform_bbox(bbox, matrix) -> tuple[float, float, float, float]:
    import pymupdf as p
    rect = p.Rect(bbox) * p.Matrix(matrix)
    return (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))


def extract_page_segments(store, ref, *, skip_header_footer: bool = True, cancel=None) -> PageExtraction:
    """Extract ordered, paragraph-merged segments using documentlayout.py."""
    from app.documentlayout import layout_from_raw
    from app.ocr.page_detector import has_effective_text

    cancel = _cancel_fn(cancel)
    check_cancel(cancel)
    store.require_operation([ref], 'copy')
    page_w, page_h = store.page_size(ref)
    snapshot = store.text_snapshot(ref, include_tables=True, infer_aligned_tables=True)
    check_cancel(cancel)
    matrix = snapshot['matrix']
    layout = layout_from_raw(snapshot['raw'], snapshot.get('tables', ()))
    sizes = [item['size'] for item in layout if item['kind'] == 'text' and not item['heading']]
    body_size = median(sizes) if sizes else 0

    segments: list[PageSegment] = []
    skipped = 0
    body_idx = 0
    table_idx = 0
    heading_idx = 0

    for item in _reading_paragraphs(layout):
        check_cancel(cancel)
        kind = item.get('kind')
        if kind == 'text':
            text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', item.get('text') or '').strip()
            if not text:
                continue
            bbox = _transform_bbox(item['bbox'], matrix)
            if item.get('code'):
                role = 'code'
            elif _is_page_number(text):
                if skip_header_footer:
                    skipped += 1
                    continue
                role = 'footer'
            elif not _has_translatable_content(text):
                skipped += 1
                continue
            else:
                top_band = page_h > 0 and bbox[3] <= page_h * 0.085 and len(text) <= 90 and item.get('line_count', 1) <= 2
                bottom_band = page_h > 0 and bbox[1] >= page_h * 0.915 and len(text) <= 90 and item.get('line_count', 1) <= 2
                small_print = item['size'] <= body_size * .92
                if top_band and small_print:
                    role = 'header'
                elif bottom_band and small_print:
                    role = 'footer'
                elif item.get('heading') or (item['size'] >= body_size * 1.2 and len(text) <= 100 and item['line_count'] == 1):
                    role = 'heading'
                else:
                    role = 'body'
                if skip_header_footer and role in ('header', 'footer'):
                    skipped += 1
                    continue
            if role == 'heading':
                heading_idx += 1
                label = tr('标题 {p0}', p0=str(heading_idx))
            elif role == 'header':
                label = tr('页眉')
            elif role == 'footer':
                label = tr('页脚')
            else:
                body_idx += 1
                label = tr('第 {p0} 段', p0=str(body_idx))
            colours = Counter()
            for run in item['runs']:
                if run.get('colour'):
                    colours[run['colour']] += len(run['text'].strip())
            ink = max(colours, key=colours.get) if colours else None
            segments.append(PageSegment(f's{len(segments) + 1}', role, label, text, bbox, item['size'], ink))
        elif kind == 'table':
            table = item['table']
            table_idx += 1
            table_id = f't{table_idx}'
            for cell in sorted(table.cells, key=lambda c: (c.row, c.col)):
                text = ' '.join(cell.text.splitlines()).strip()
                if text and cell.bbox:
                    segments.append(PageSegment(
                        f'{table_id}.r{cell.row}c{cell.col}', 'table', tr('表格 {p0}', p0=str(table_idx)), text,
                        _transform_bbox(cell.bbox, matrix), cell.style.font_size or 10,
                        cell.style.font_color, table_id, cell,
                    ))

    is_scan = False
    if not segments:
        with store._render_page(ref, cancel) as page:
            if not has_effective_text(page) and bool(page.get_images()):
                is_scan = True
    return PageExtraction(tuple(segments), skipped_count=skipped, is_scan=is_scan, via_ocr=False)


def _merge_ocr_lines_to_segments(lines, origin: tuple[float, float], scale: float) -> tuple[PageSegment, ...]:
    """Group adjacent local OCR lines into natural paragraphs in display coordinates."""
    if not lines:
        return ()
    mapped = []
    for line in lines:
        text = (line.text or '').strip()
        if not text:
            continue
        x0, y0, x1, y1 = line.bbox
        box = (
            origin[0] + x0 / scale,
            origin[1] + y0 / scale,
            origin[0] + x1 / scale,
            origin[1] + y1 / scale,
        )
        mapped.append((box, text))
    if not mapped:
        return ()
    # Small scan skew can put a label and its value on opposite sides of a
    # fixed y bucket. Group by their vertical overlap before reading left to right.
    mapped.sort(key=lambda pair: (pair[0][1] + pair[0][3], pair[0][0]))
    rows = []
    for box, text in mapped:
        if rows:
            first = rows[-1][0][0]
            center_gap = (box[1] + box[3] - first[1] - first[3]) / 2
            if center_gap <= min(box[3] - box[1], first[3] - first[1]) / 2:
                rows[-1].append((box, text))
                continue
        rows.append([(box, text)])
    mapped = [pair for row in rows for pair in sorted(row, key=lambda pair: pair[0][0])]
    typical_height = median(box[3] - box[1] for box, _ in mapped)

    groups: list[list[tuple[tuple[float, float, float, float], str]]] = []
    for box, text in mapped:
        if not groups:
            groups.append([(box, text)])
            continue
        prev_box, prev_text = groups[-1][-1]
        prev_h = max(6.0, prev_box[3] - prev_box[1])
        cur_h = max(6.0, box[3] - box[1])
        avg_h = (prev_h + cur_h) / 2.0
        v_gap = box[1] - prev_box[3]
        h_overlap = min(box[2], prev_box[2]) - max(box[0], prev_box[0])
        min_w = max(1.0, min(box[2] - box[0], prev_box[2] - prev_box[0]))
        same_column = h_overlap >= min_w * .55 and abs(box[0] - prev_box[0]) <= avg_h * 1.3
        similar_size = max(prev_h, cur_h) <= min(prev_h, cur_h) * 1.25
        separate_fields = any(mark in text or mark in prev_text for mark in ('|', '｜', '\t'))
        if (same_column and similar_size and not separate_fields
                and -avg_h * .35 <= v_gap <= min(prev_h, cur_h) * .35):
            groups[-1].append((box, text))
        else:
            groups.append([(box, text)])

    segments: list[PageSegment] = []
    for index, group in enumerate(groups, 1):
        boxes = [b for b, _ in group]
        merged_box = (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )
        parts = []
        for _, part in group:
            if not parts:
                parts.append(part)
            else:
                prev = parts[-1]
                # Join CJK directly, Latin with space.
                cjk_join = ('\u4e00' <= prev[-1] <= '\u9fff') and ('\u4e00' <= part[0] <= '\u9fff')
                parts.append(('' if cjk_join or prev.endswith('-') else ' ') + part)
        merged_text = ''.join(parts).strip()
        if not _has_translatable_content(merged_text):
            continue
        heading = len(group) == 1 and merged_box[3] - merged_box[1] > typical_height * 1.3
        segments.append(PageSegment(f'ocr{index}', 'heading' if heading else 'body',
                                    tr('识别段 {p0}', p0=str(len(segments) + 1)), merged_text, merged_box))
    return tuple(segments)


def _run_local_ocr_on_image(image, page_index: int, *, cancel=None):
    """Execute one-shot local OCR using the existing offline OCR runtime, never modifying source files."""
    from app.ocr.contracts import OCRError
    from app.ocr.engine import OCREngine
    from app.ocr.model_manager import validate_runtime
    from app.storage import TemporaryWorkspace

    root, _ = validate_runtime(cancel=cancel)
    with TemporaryWorkspace('translate-ocr') as scratch:
        png = Path(scratch) / 'region.png'
        if not image.save(str(png), 'PNG'):
            raise OCRError(tr('临时识别图像写入失败，请检查磁盘空间。'))
        engine = OCREngine(root, cancel=cancel)
        try:
            return engine.recognize(png, page_index, word_boxes=False)
        finally:
            engine.close()


def ocr_page_for_translation(store, ref, page_index: int, *, cancel=None) -> PageExtraction:
    """Temporarily OCR a scan page in memory/scratch for translation without modifying the PDF."""
    cancel = _cancel_fn(cancel)
    store.require_operation([ref], 'copy')
    page_w, page_h = store.page_size(ref)
    scale = min(200 / 72, 2400 / max(1.0, page_w), 3200 / max(1.0, page_h))
    image, origin, actual_scale = store.render_region(ref, (0.0, 0.0, page_w, page_h), scale)
    check_cancel(cancel)
    lines = _run_local_ocr_on_image(image, page_index, cancel=cancel)
    segments = _merge_ocr_lines_to_segments(lines, origin, actual_scale)
    snapshot = None
    if lines:
        import pymupdf as p
        from app.ocr.contracts import OCRError
        from app.ocr.pdf_export import add_text_layer
        check_cancel(cancel)
        # Use the OCR quads to create character positions for dragging on the
        # scan. This in-memory page is separate from the document being read.
        with p.open() as memory:
            page = memory.new_page(width=page_w, height=page_h)
            try:
                add_text_layer(page, lines, p.Point(origin) * actual_scale, 72 * actual_scale, protect_existing=False)
            except OCRError:
                # The OCR font may lack a glyph. Its text is still translatable.
                pass
            else:
                raw = page.get_text('rawdict', flags=p.TEXTFLAGS_RAWDICT & ~p.TEXT_PRESERVE_IMAGES)
                snapshot = dict(raw=raw, matrix=(1, 0, 0, 1, 0, 0), tables=())
    return PageExtraction(segments, is_scan=True, via_ocr=True, selection_snapshot=snapshot)


def extract_region_for_translation(store, ref, page_index: int, region: tuple[float, float, float, float], *, cancel=None) -> RegionExtraction:
    """Smart region extraction: use native PDF text layer first; fall back to local OCR for scan/diagram."""
    import pymupdf as p
    from app.documentlayout import selection_text
    from app.ocr.contracts import OCRError

    cancel = _cancel_fn(cancel)
    check_cancel(cancel)
    store.require_operation([ref], 'copy')
    page_w, page_h = store.page_size(ref)
    display_area = p.Rect(region) & p.Rect(0.0, 0.0, page_w, page_h)
    if display_area.is_empty or display_area.width < 2 or display_area.height < 2:
        return RegionExtraction('', 'text', message=tr('框选区域太小，请重新框选。'))

    # 1. Try native text layer inside the selected rectangle.
    snapshot = store.text_snapshot(ref, include_tables=True, infer_aligned_tables=True)
    check_cancel(cancel)
    inv = ~p.Matrix(snapshot['matrix'])
    raw_rect = display_area * inv
    tables = [t for t in snapshot.get('tables', ()) if p.Rect(t.bbox).intersects(raw_rect)]
    native_text = (selection_text(snapshot['raw'], raw_rect, tables) or '').strip()
    if native_text and _has_translatable_content(native_text):
        return RegionExtraction(native_text, 'text')

    # 2. No native text -> fall back to local offline OCR on the cropped image.
    scale = min(240 / 72, 2200 / max(1.0, display_area.width), 2200 / max(1.0, display_area.height))
    crop, origin, actual_scale = store.render_region(ref, tuple(display_area), scale)
    check_cancel(cancel)
    try:
        lines = _run_local_ocr_on_image(crop, page_index, cancel=cancel)
    except OCRError as exc:
        msg = str(exc)
        try:
            from app.ocr.model_manager import default_runtime_dir
            runtime_missing = not (default_runtime_dir() / 'manifest.json').is_file()
        except (OCRError, OSError):
            runtime_missing = False
        missing = runtime_missing or ('尚未安装' in msg)
        return RegionExtraction('', 'ocr', ocr_missing=missing, message=msg)
    segments = _merge_ocr_lines_to_segments(lines, origin, actual_scale)
    ocr_text = '\n\n'.join(seg.text for seg in segments).strip()
    if not ocr_text:
        return RegionExtraction('', 'ocr', message=tr('框选区域内未识别到文字，请放大选区或检查清晰度。'))
    return RegionExtraction(ocr_text, 'ocr')


def group_segments_into_batches(
    segments: tuple[PageSegment, ...] | list[PageSegment],
    *,
    max_items: int = 5,
    max_chars: int = 1800,
) -> list[list[PageSegment]]:
    batches: list[list[PageSegment]] = []
    current: list[PageSegment] = []
    chars = 0
    for seg in segments:
        seg_len = len(seg.text)
        if current and (len(current) >= max_items or chars + seg_len > max_chars):
            batches.append(current)
            current = []
            chars = 0
        current.append(seg)
        chars += seg_len
    if current:
        batches.append(current)
    return batches


@dataclass
class CachedPageTranslation:
    extraction: PageExtraction
    translations: dict[str, str]
    complete: bool = False
    error: str = ''


class TranslationCache:
    """Bounded in-memory LRU cache cleared on document reset or window close."""

    def __init__(self, max_entries: int = 64):
        self._max = max(1, max_entries)
        self._items: OrderedDict[tuple, CachedPageTranslation] = OrderedDict()

    def get(self, key: tuple) -> CachedPageTranslation | None:
        hit = self._items.get(key)
        if hit is not None:
            self._items.move_to_end(key)
        return hit

    def put(self, key: tuple, value: CachedPageTranslation) -> None:
        self._items[key] = value
        self._items.move_to_end(key)
        while len(self._items) > self._max:
            self._items.popitem(last=False)

    def invalidate_for_page(self, render_key) -> None:
        for key in [k for k in self._items if k and k[0] == render_key]:
            self._items.pop(key, None)

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)
