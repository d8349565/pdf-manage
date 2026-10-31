"""Read-only content comparison prototype. Positions support evidence, not matching.

Native text is compared across pages; optional OCR uses the existing separate bundle.
No imports from the main window and no work on the ordinary reading path.
"""
from bisect import bisect_left
from collections import Counter, defaultdict
from contextlib import redirect_stdout
from dataclasses import asdict
from difflib import SequenceMatcher
from hashlib import sha256
from io import StringIO
import json
import math
from pathlib import Path
import re
from statistics import median
import time
import unicodedata

import pymupdf as pdf

from app.output import check_cancel, stamp
from app.pdfcompare import _open_pdf


HEADING = re.compile(r'^(?:第[\d一二三四五六七八九十百千万零〇]+[章节条款]|[一二三四五六七八九十]+[、．.]|\d+(?:\.\d+)*[、．.、)）])\s*')
PAGE_NUMBER = re.compile(r'^(?:第)?\d+(?:页|/\d+)?$')
NUMBER = re.compile(r'[-+]?\d+(?:[.,]\d+)*(?:[%％]|万元|个月|元|米|件|台|套|天|日)?')
PRESENTATION = str.maketrans('，：；（）％', ',:;()%')
MAX_CHARS = 500_000
MAX_UNIT_CHARS = 4000


def compact(text):
    return ''.join(unicodedata.normalize('NFC', text).split()).translate(PRESENTATION)


def heading_key(text):
    return HEADING.sub('', compact(text))


def locations(refs):
    """Merge actual character boxes by line; OCR without char boxes stays line-level."""
    rows = []
    for page, box, source, confidence in refs:
        match = next((r for r in reversed(rows[-8:]) if r['page'] == page
                      and abs(r['bbox'][1] - box[1]) < max(3, (box[3] - box[1]) * .3)), None)
        if match is None:
            rows.append(dict(page=page, bbox=list(box), source=source, confidence=confidence))
        else:
            match['bbox'] = [min(match['bbox'][0], box[0]), min(match['bbox'][1], box[1]),
                             max(match['bbox'][2], box[2]), max(match['bbox'][3], box[3])]
            match['confidence'] = min(match['confidence'], confidence)
    return rows


class OCRReader:
    def __init__(self, runtime, folder, cancel):
        self.runtime, self.folder, self.cancel = runtime, Path(folder), cancel
        self.engine = None

    def read(self, page, page_index):
        from app.ocr.engine import OCREngine
        from app.ocr.model_manager import validate_runtime
        self.folder.mkdir(parents=True, exist_ok=True)
        scale = min(200 / 72, math.sqrt(4_000_000 / page.rect.get_area()))
        png = page.get_pixmap(matrix=pdf.Matrix(scale, scale), alpha=False).tobytes('png')
        key = sha256(png).hexdigest()
        cache = self.folder / (key + '-chars.json')
        if cache.exists():
            recognized = json.loads(cache.read_text(encoding='utf-8'))
        else:
            if self.engine is None:
                root, _ = validate_runtime(self.runtime, cancel=self.cancel)
                self.engine = OCREngine(root, self.cancel)
            image = self.folder / (key + '.png')
            image.write_bytes(png)
            try:
                recognized = [asdict(line) for line in self.engine.recognize(image, page_index, word_boxes=True)]
                cache.write_text(json.dumps(recognized, ensure_ascii=False), encoding='utf-8')
            finally:
                image.unlink(missing_ok=True)
        groups = []
        for line in sorted(recognized, key=lambda r: (r['bbox'][1], r['bbox'][0])):
            box = [v / scale for v in line['bbox']]
            center, height = (box[1] + box[3]) / 2, box[3] - box[1]
            group = next((g for g in reversed(groups[-12:]) if abs(g['center'] - center) < max(height, g['height']) * .55), None)
            if group is None:
                group = dict(center=center, height=height, pieces=[])
                groups.append(group)
            refs = [(page_index + 1, tuple(box), 'ocr', float(line['confidence']))] * len(line['text'])
            group['pieces'].append((box[0], line['text'], refs))
        output = []
        for group in sorted(groups, key=lambda g: g['center']):
            pieces = sorted(group['pieces'])
            refs = [ref for _, _, rr in pieces for ref in rr]
            box = [min(r[1][0] for r in refs), min(r[1][1] for r in refs),
                   max(r[1][2] for r in refs), max(r[1][3] for r in refs)]
            output.append(dict(text=''.join(t for _, t, _ in pieces), refs=refs, bbox=box,
                               size=group['height'], page=page_index + 1))
        return output

    def close(self):
        if self.engine:
            self.engine.close()


def native_lines(page, index):
    output = []
    flags = pdf.TEXTFLAGS_RAWDICT & ~pdf.TEXT_PRESERVE_IMAGES
    for block in page.get_text('rawdict', flags=flags, sort=True)['blocks']:
        if block['type'] != 0:
            continue
        for line in block['lines']:
            chars = [char for span in line['spans'] for char in span['chars']]
            output.append(dict(text=''.join(c['c'] for c in chars), bbox=list(line['bbox']),
                size=max(span['size'] for span in line['spans']), page=index + 1,
                refs=[(index + 1, tuple(c['bbox']), 'native', 1.) for c in chars]))
    return output


def append_text(unit, line):
    if len(unit['text']) + len(line['text']) > MAX_UNIT_CHARS:
        raise ValueError('单个条款超过内容原型的 4000 字上限，请缩小比较范围')
    for char, ref in zip(line['text'], line['refs']):
        normalized = compact(char)
        unit['text'] += normalized
        unit['refs'].extend([ref] * len(normalized))


def extract(doc, ocr, progress, cancel, budget=None, label='文档'):
    from app.ocr.page_detector import has_effective_text
    pages, unread, ocr_pages = [], [], []
    budget = budget if budget is not None else [MAX_CHARS]
    for index, page in enumerate(doc):
        check_cancel(cancel)
        effective = has_effective_text(page)
        if progress:
            action = '识别扫描文字' if not effective and page.get_images() and ocr else '读取正文'
            progress(index, len(doc), f'{label}：{action}，第 {index + 1} / {len(doc)} 页')
        if effective or not page.get_images():
            text_size = len(page.get_text('text'))
            if text_size > 40_000 or text_size > budget[0]:
                raise ValueError('文字量超出内容原型上限，请缩小比较范围')
            budget[0] -= text_size
            lines = native_lines(page, index)
        elif page.get_images() and ocr is not None:
            lines = ocr.read(page, index)
            text_size = sum(len(line['text']) for line in lines)
            if text_size > 40_000 or text_size > budget[0]:
                raise ValueError('识别文字量超出内容原型上限，请缩小比较范围')
            budget[0] -= text_size
            ocr_pages.append(index + 1)
            if not lines:
                unread.append(index + 1)
        elif page.get_images():
            lines = []
            unread.append(index + 1)
        else:
            lines = native_lines(page, index)
        pages.append(dict(lines=lines, height=page.rect.height, effective=effective))
        if progress:
            progress(index + 1, len(doc), f'{label}：已读取第 {index + 1} / {len(doc)} 页')

    edge_counts = Counter()
    sizes = defaultdict(list)
    for item in pages:
        edge_counts.update({compact(line['text']) for line in item['lines']
                            if line['bbox'][1] < item['height'] * .07 or line['bbox'][3] > item['height'] * .94})
        for line in item['lines']:
            if line['refs'] and item['height'] * .07 <= line['bbox'][1] and line['bbox'][3] <= item['height'] * .94:
                sizes[line['refs'][0][2]].append(line['size'])
    typical_sizes = {source: median(values) for source, values in sizes.items()}
    units, current = [], None
    removed = 0
    def add_unit(unit):
        if len(units) >= 4000:
            raise ValueError('条款数量超出内容原型上限，请缩小比较范围')
        units.append(unit)
    def finish():
        nonlocal current
        if current and (current['text'] or current['heading']):
            if not current['text']:
                current['kind'] = 'heading'
            add_unit(current)
        current = None

    for index, item in enumerate(pages):
        page = doc[index]
        events, table_boxes = [], []
        if item['effective']:
            drawings = page.get_drawings()
            if sum(part[0] == 're' for drawing in drawings for part in drawing['items']) >= 4:
                with redirect_stdout(StringIO()):
                    tables = page.find_tables().tables
                for table in tables:
                    cells = table.extract()
                    # Require observable column names and data, rather than trusting a guessed external header.
                    if (len(cells) < 2 or not all(cells[0]) or any(re.search(r'\d', str(c)) for c in cells[0])
                            or not any(re.search(r'\d', str(c)) for row in cells[1:] for c in row)):
                        continue
                    table_boxes.append(pdf.Rect(table.bbox))
                    events.append((table.bbox[1], 'table', (cells, [row.cells for row in table.rows])))
        for line in item['lines']:
            text = compact(line['text'])
            typical_size = typical_sizes.get(line['refs'][0][2], line['size']) if line['refs'] else line['size']
            edge = line['bbox'][1] < item['height'] * .07 or line['bbox'][3] > item['height'] * .94
            if edge and (edge_counts[text] >= 2 or PAGE_NUMBER.fullmatch(text) or line['size'] < typical_size * .85):
                removed += 1
                continue
            center = pdf.Rect(line['bbox']).tl + (pdf.Rect(line['bbox']).br - pdf.Rect(line['bbox']).tl) / 2
            if any(box.contains(center) for box in table_boxes):
                continue
            events.append((line['bbox'][1], 'line', line))
        if item['effective']:
            for image in page.get_image_info(hashes=True):
                events.append((image['bbox'][1], 'image', image))
        for _, kind, value in sorted(events, key=lambda event: event[0]):
            check_cancel(cancel)
            if kind == 'line':
                text = compact(value['text'])
                typical_size = typical_sizes.get(value['refs'][0][2], value['size'])
                if not text:
                    continue
                if len(text) < 90 and (HEADING.match(text) or value['size'] > typical_size * 1.15):
                    finish()
                    title_refs = [ref for char, ref in zip(value['text'], value['refs']) for _ in compact(char)]
                    title = heading_key(text)
                    current = dict(kind='text', heading=title, text='', refs=[],
                                   heading_refs=title_refs[len(text)-len(title):])
                else:
                    if current is None:
                        current = dict(kind='text', heading='', text='', refs=[], heading_refs=[])
                    append_text(current, value)
            elif kind == 'table':
                heading = current['heading'] if current else ''
                cells, boxes = value
                for row, cell_boxes in zip(cells[1:], boxes[1:]):
                    if not row[0] or any(box is None for box in cell_boxes):
                        unread.append(index + 1)
                        continue
                    add_unit(dict(kind='table', heading=heading, key=compact(str(row[0])),
                        text='|'.join(compact(str(c or '')) for c in row),
                        refs=[(index + 1, tuple(box), 'native', 1.) for box in cell_boxes], heading_refs=[],
                        cells={compact(str(col)):dict(text=compact(str(text or '')),
                            refs=[(index + 1, tuple(box), 'native', 1.)])
                               for col, text, box in zip(cells[0], row, cell_boxes)}))
            else:
                heading = current['heading'] if current else ''
                add_unit(dict(kind='image', heading=heading, key=heading,
                    text=value['digest'].hex(), refs=[(index + 1, tuple(value['bbox']), 'native', 1.)], heading_refs=[]))
    finish()
    return units, dict(ocr_pages=ocr_pages, unread_pages=sorted(set(unread)), ignored_edge_lines=removed,
                       edge_texts=sorted(edge_counts))


def unit_key(unit):
    return (unit['kind'], unit['heading'], unit.get('key', ''))


def match_units(old, new, cancel=None):
    a, b = defaultdict(list), defaultdict(list)
    for i, unit in enumerate(old):
        a[unit_key(unit)].append(i)
    for i, unit in enumerate(new):
        b[unit_key(unit)].append(i)
    pairs, used_old, used_new, reviews = [], set(), set(), []
    for key in a.keys() & b.keys():
        check_cancel(cancel)
        aa, bb = a[key], b[key]
        if len(aa) == len(bb) == 1 and key[1]:
            pairs.append((aa[0], bb[0])); used_old.update(aa); used_new.update(bb)
        elif key[1] and (len(aa) > 1 or len(bb) > 1):
            old_counts = Counter(old[i]['text'] for i in aa)
            new_counts = Counter(new[i]['text'] for i in bb)
            if old_counts == new_counts:
                # Identical duplicates do not establish unique identity, but have no content delta.
                used_old.update(aa); used_new.update(bb)
                continue
            old_delta, new_delta = old_counts - new_counts, new_counts - old_counts
            reviews.append(dict(kind='ambiguous', heading=key[1],
                before=' / '.join(old_delta.keys()), after=' / '.join(new_delta.keys()),
                old=locations([r for i in aa if old[i]['text'] in old_delta
                               for r in (old[i]['refs'] or old[i]['heading_refs'])]),
                new=locations([r for i in bb if new[i]['text'] in new_delta
                               for r in (new[i]['refs'] or new[i]['heading_refs'])]),
                reason='重复标题与正文无法确定唯一对应关系，请确认候选位置'))
            used_old.update(aa); used_new.update(bb)
    # Exact text provides a position-independent anchor even if a heading changed.
    exact_old, exact_new = defaultdict(list), defaultdict(list)
    for i, unit in enumerate(old):
        if i not in used_old:
            exact_old[(unit['kind'], unit['text'])].append(i)
    for j, unit in enumerate(new):
        if j not in used_new:
            exact_new[(unit['kind'], unit['text'])].append(j)
    for key in exact_old.keys() & exact_new.keys():
        if len(exact_old[key]) == len(exact_new[key]) == 1:
            i, j = exact_old[key][0], exact_new[key][0]
            if not key[1] and SequenceMatcher(None, old[i]['heading'], new[j]['heading']).ratio() < .65:
                continue
            pairs.append((i, j)); used_old.add(i); used_new.add(j)
    # Candidate retrieval uses short text shingles; no all-document pixel or N*M comparison.
    index = defaultdict(set)
    for j, unit in enumerate(new):
        check_cancel(cancel)
        if j not in used_new and unit['kind'] == 'text':
            for token in set(unit['text'][k:k+4] for k in range(max(1, len(unit['text'])-3))):
                index[token].add(j)
    for i, unit in enumerate(old):
        check_cancel(cancel)
        if i in used_old or unit['kind'] != 'text':
            continue
        tokens = set(unit['text'][k:k+4] for k in range(max(1, len(unit['text'])-3)))
        candidates = Counter(j for token in tokens if len(index.get(token, ())) <= 64
                             for j in index.get(token, ()) if j not in used_new)
        scored = []
        for j, _ in candidates.most_common(6):
            check_cancel(cancel)
            target = new[j]
            ratio = SequenceMatcher(None, unit['text'], target['text']).ratio()
            title_ratio = SequenceMatcher(None, unit['heading'], target['heading']).ratio()
            first_ratio = SequenceMatcher(None, re.split('[。！？]', unit['text'])[0], re.split('[。！？]', target['text'])[0]).ratio()
            if ratio >= .65 and (ratio >= .94 or title_ratio >= .5 and first_ratio >= .5):
                scored.append((ratio, j))
        scored.sort(reverse=True)
        if scored and (len(scored) == 1 or scored[0][0] - scored[1][0] >= .12):
            j = scored[0][1]; pairs.append((i, j)); used_old.add(i); used_new.add(j)
    return sorted(pairs), used_old, used_new, reviews


def moved_units(pairs, old):
    ordered = [(i, j) for i, j in pairs if old[i]['kind'] == 'text']
    tails, tail_indices, previous = [], [], []
    for k, (_, j) in enumerate(ordered):
        at = bisect_left(tails, j)
        previous.append(tail_indices[at-1] if at else -1)
        if at == len(tails):
            tails.append(j); tail_indices.append(k)
        else:
            tails[at] = j; tail_indices[at] = k
    stable = set()
    k = tail_indices[-1] if tail_indices else -1
    while k >= 0:
        stable.add(ordered[k][0]); k = previous[k]
    return {i for i, _ in ordered} - stable


def edits(a, b, cancel=None):
    output, seen = [], set()
    check_cancel(cancel)
    prefix = 0
    while prefix < min(len(a['text']), len(b['text'])) and a['text'][prefix] == b['text'][prefix]:
        if prefix % 256 == 0:
            check_cancel(cancel)
        prefix += 1
    ae, be = len(a['text']), len(b['text'])
    while ae > prefix and be > prefix and a['text'][ae-1] == b['text'][be-1]:
        if ae % 256 == 0:
            check_cancel(cancel)
        ae -= 1; be -= 1
    if max(ae-prefix, be-prefix) > 2000:
        operations = [('replace', prefix, ae, prefix, be)]
    else:
        operations = [(op, i+prefix, end+prefix, j+prefix, stop+prefix) for op, i, end, j, stop in
                      SequenceMatcher(None, a['text'][prefix:ae], b['text'][prefix:be], autojunk=False).get_opcodes()]
    for op, i, end, j, stop in operations:
        check_cancel(cancel)
        if op == 'equal':
            continue
        ai, ae, bj, be = i, end, j, stop
        expanded = []
        for text, start, finish in [(a['text'], i, end), (b['text'], j, stop)]:
            candidate = None
            for token in NUMBER.finditer(text):
                if (token.start() <= start < token.end() if start == finish
                        else token.start() <= start and finish <= token.end()):
                    candidate = token.span()
                    break
            expanded.append(candidate)
        if all(expanded):
            (ai, ae), (bj, be) = expanded
        if (ai, ae, bj, be) in seen:
            continue
        seen.add((ai, ae, bj, be))
        old_refs, new_refs = a['refs'][ai:ae], b['refs'][bj:be]
        confidence = min((r[3] for r in old_refs + new_refs), default=1.)
        is_ocr = any(r[2] == 'ocr' for r in old_refs + new_refs)
        # Punctuation-only OCR discrepancies are review items, never definitive content changes.
        uncertain = is_ocr and (confidence < .97 or not any(c.isalnum() for c in a['text'][ai:ae] + b['text'][bj:be]))
        left = max(a['text'].rfind(c, 0, ai) for c in '。！？') + 1
        ends = [a['text'].find(c, ae) for c in '。！？']
        right = min([v + 1 for v in ends if v >= 0] or [min(len(a['text']), ae + 45)])
        nleft = max(b['text'].rfind(c, 0, bj) for c in '。！？') + 1
        ends = [b['text'].find(c, be) for c in '。！？']
        nright = min([v + 1 for v in ends if v >= 0] or [min(len(b['text']), be + 45)])
        old_start, new_start = max(left, ai-45), max(nleft, bj-45)
        output.append(dict(kind='review' if uncertain else 'modified', heading=a['heading'] or b['heading'] or '正文',
            before=a['text'][ai:ae], after=b['text'][bj:be],
            old=locations(old_refs or a['refs'][max(0, ai-1):ai+1]),
            new=locations(new_refs or b['refs'][max(0, bj-1):bj+1]),
            old_context_only=not old_refs, new_context_only=not new_refs,
            old_context=a['text'][old_start:right], new_context=b['text'][new_start:nright],
            old_context_range=[ai-old_start, ae-old_start], new_context_range=[bj-new_start, be-new_start],
            reason='OCR 文字或标点需要人工核对' if uncertain else '', confidence=confidence))
    return output


def compare_content_files(old_path, new_path, *, ocr_runtime=None, cache_dir=None, progress=None, cancel=None,
                          old_password='', new_password=''):
    start = time.perf_counter()
    paths = [str(Path(p).resolve(strict=True)) for p in (old_path, new_path)]
    stamps = [stamp(p) for p in paths]
    reader = OCRReader(ocr_runtime, cache_dir, cancel) if ocr_runtime and cache_dir else None
    try:
        with _open_pdf(paths[0], old_password) as old_doc, _open_pdf(paths[1], new_password) as new_doc:
            if max(len(old_doc), len(new_doc)) > 200 or any(s[2] > 512 * 1024**2 for s in stamps):
                raise ValueError('内容比较原型每份限 200 页、512 MiB')
            budget = [MAX_CHARS]
            old, old_info = extract(old_doc, reader, progress, cancel, budget, '旧版')
            new, new_info = extract(new_doc, reader, progress, cancel, budget, '新版')
            dimensions = [[list(page.rect) for page in doc] for doc in (old_doc, new_doc)]
        common_edges = set(old_info.pop('edge_texts')) & set(new_info.pop('edge_texts'))
        # OCR box height and native font size differ. A common peripheral preamble
        # must not become a deletion just because one rendering classified it first.
        old = [u for u in old if not (u['kind'] == 'text' and not u['heading'] and u['text'] in common_edges)]
        new = [u for u in new if not (u['kind'] == 'text' and not u['heading'] and u['text'] in common_edges)]
        quality_review = []
        for side, units in [('old', old), ('new', new)]:
            low = [r for unit in units for r in unit['refs'] if r[2] == 'ocr' and r[3] < .9]
            if low:
                quality_review.append(dict(side=side, pages=sorted({r[0] for r in low}), locations=locations(low)))
        pairs, used_old, used_new, changes = match_units(old, new, cancel)
        moved = moved_units(pairs, old)
        layout = []
        incomplete = bool(old_info['unread_pages'] or new_info['unread_pages'])
        for i, j in pairs:
            check_cancel(cancel)
            a, b = old[i], new[j]
            title_changes = []
            if a['kind'] in ('text', 'heading') and a['heading'] != b['heading']:
                aa = dict(text=a['heading'], refs=a['heading_refs'], heading='标题内容')
                bb = dict(text=b['heading'], refs=b['heading_refs'], heading='标题内容')
                title_changes = edits(aa, bb, cancel)
            content_changes = []
            if a['kind'] == 'text':
                if a['text'] != b['text']:
                    content_changes = edits(a, b, cancel)
                if i in moved:
                    changes.append(dict(kind='review' if incomplete else 'moved', heading=a['heading'] or '正文', before=a['text'][:100], after=b['text'][:100],
                        old=locations(a['heading_refs'] or a['refs'][:30]), new=locations(b['heading_refs'] or b['refs'][:30]), reason='条款在内容顺序中的位置改变'))
            elif a['kind'] == 'table':
                for label in dict.fromkeys([*a['cells'], *b['cells']]):
                    aa, bb = a['cells'].get(label, dict(text='',refs=[])), b['cells'].get(label, dict(text='',refs=[]))
                    if aa['text'] != bb['text']:
                        changes.append(dict(kind='modified', heading=f"{a['heading']} · {a['key']} · {label}",
                            before=aa['text'], after=bb['text'], old=locations(aa['refs']), new=locations(bb['refs']), reason='按行名称和列名对应单元格'))
            elif a['kind'] == 'image' and a['text'] != b['text']:
                changes.append(dict(kind='image_modified', heading=a['heading'] or '图片', before='原图片', after='图片内容变化',
                    old=locations(a['refs']), new=locations(b['refs']), reason='图片解码内容不同，请核对原图'))
            elif a['kind'] == 'image' and a['refs'][0][:2] != b['refs'][0][:2]:
                layout.append(dict(heading=a['heading'], reason='图片内容相同，位置或显示尺寸改变'))
            for change in title_changes + content_changes:
                if incomplete:
                    change['kind'] = 'review'
                    change['reason'] = '存在未识别扫描页，读取缺口可能被误当作文字修改，请先补齐识别'
                changes.append(change)
        for side, units, used in [('old', old, used_old), ('new', new, used_new)]:
            for i, unit in enumerate(units):
                if i not in used:
                    refs = unit['refs'] or unit['heading_refs']
                    changes.append(dict(kind='review' if incomplete else 'deleted' if side == 'old' else 'added',
                        heading=(unit['heading'] or '正文') + (' · ' + unit['key'] if unit['kind'] == 'table' else ''),
                        before=(unit['text'] or unit['heading'])[:200] if side == 'old' else '',
                        after=(unit['text'] or unit['heading'])[:200] if side == 'new' else '',
                        old=locations(refs) if side == 'old' else [], new=locations(refs) if side == 'new' else [],
                        reason='存在尚未识别的扫描页，不能确定新增或删除' if incomplete else '另一份文件中未找到对应内容'))
        if stamps != [stamp(p) for p in paths]:
            raise ValueError('来源文件在比较期间发生变化，请重新比较')
        return dict(schema=1, paths=paths, stamps=stamps, changes=changes, layout_notes=layout, quality_review=quality_review,
                    extraction=[old_info, new_info], page_rects=dimensions,
                    counts=dict(Counter(c['kind'] for c in changes)), complete=not incomplete,
                    seconds=round(time.perf_counter() - start, 3))
    finally:
        if reader:
            reader.close()


def preview_content_change(summary, change=None, pages=None, *, passwords=('', ''), cancel=None):
    """Render evidence from checked sources; native and OCR boxes use their own coordinates."""
    from PIL import Image
    from app.pdfcompare import check_sources
    check_cancel(cancel)
    paths, stamps = check_sources(summary)
    metadata, images = {}, {}
    for index, side in enumerate(('old', 'new')):
        check_cancel(cancel)
        refs = change.get(side, []) if change is not None else []
        candidates = sorted({ref['page'] for ref in refs})
        if change is not None and not candidates:
            metadata[side] = dict(available=False, pages=[], page=None, boxes=[])
            continue
        chosen = (pages or {}).get(side) or (candidates[0] if candidates else 1)
        if candidates and chosen not in candidates:
            raise ValueError('所选页不属于当前内容的原文位置')
        with _open_pdf(paths[index], passwords[index]) as doc:
            if type(chosen) is not int or not 1 <= chosen <= len(doc):
                raise ValueError('原文页码超出文件范围')
            page = doc[chosen - 1]
            scale = min(2., math.sqrt(2_000_000 / page.rect.get_area()),
                        4096 / page.rect.width, 4096 / page.rect.height)
            while math.ceil(page.rect.width * scale) * math.ceil(page.rect.height * scale) > 2_000_000:
                scale *= .999
            pix = page.get_pixmap(matrix=pdf.Matrix(scale, scale), colorspace=pdf.csRGB, alpha=False)
            images[side] = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
            sx, sy = pix.width / page.rect.width, pix.height / page.rect.height
            boxes = []
            for ref in refs:
                if ref['page'] != chosen:
                    continue
                box = pdf.Rect(ref['bbox'])
                if ref['source'] == 'native':
                    box = box * page.rotation_matrix
                boxes.append([box.x0 * sx, box.y0 * sy, box.x1 * sx, box.y1 * sy])
            metadata[side] = dict(available=True, pages=candidates or [1], page=chosen, boxes=boxes,
                                  context_only=bool(change and change.get(side + '_context_only')),
                                  width=pix.width, height=pix.height)
    check_cancel(cancel)
    if stamps != [stamp(path) for path in paths]:
        raise ValueError('来源文件在加载原文期间发生变化，请重新比较')
    return metadata, images
