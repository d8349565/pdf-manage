"""Generate independent Chinese PDF acceptance fixtures, never comparison results.

python tests/pdfcompare_acceptance_samples.py --output build/pdfcompare-acceptance-v1
The output directory must be new. This module is outside app/ and is not shipped.
"""
from copy import deepcopy
from hashlib import sha256
from html import escape
from io import BytesIO
import argparse
import json
from pathlib import Path
import random
import zipfile

from PIL import Image, ImageDraw
import pymupdf as pdf


FONT = pdf.Font('cjk')
NORMAL = dict(fontsize=11, margin=52, leading=1.65)
REFLOW = dict(fontsize=14, margin=92, leading=1.9)


def paragraph(key, title, text, **kwargs):
    return dict(id=key, title=title, text=text, **kwargs)


def base_content():
    clauses = [
        ('scope', '采购范围', '采购范围包括设备供货、运输、安装和调试。供应方应提供完整的交付清单。'),
        ('delivery', '交付安排', '合同生效后30日内完成交付。交付地点由采购方书面指定，运输包装应防潮防震。'),
        ('price', '付款条件', '合同总金额为120000元。验收通过后支付尾款，付款前须提供合法有效的发票。'),
        ('penalty', '延期责任', '延期交付的违约金比例为0.5%。计算依据为未交付部分的合同金额。'),
        ('permission', '分包限制', '未经采购方书面同意，不得转包。经批准的服务分包仍由供应方承担合同责任。'),
        ('count', '数量单位', '配套电缆数量为100米。交付时应逐项检查型号、长度及附件，不以包装件数代替长度。'),
        ('quality', '质量要求', '设备应为全新合格产品。出厂检验报告与实际交付设备的型号应一致。'),
        ('acceptance', '验收流程', '验收包含外观检查、功能检查和资料检查。双方签署验收记录后确认完成交付。'),
        ('warranty', '售后服务', '质量保证期为24个月。供应方应记录故障现象、处理过程和更换部件。'),
        ('contact', '通知方式', '重要通知应采用书面形式。通知内容应列明事项、联系人和需要回复的日期。'),
    ]
    return [paragraph(key, title, text + '履行本条约定时，双方应保存相应记录，并在出现疑问时核对原始资料。'
                      '本示例不包含真实项目、企业或个人信息，仅用于软件功能验收。') for key, title, text in clauses]


def changed(items, key, before, after):
    result = deepcopy(items)
    item = next(item for item in result if item['id'] == key)
    assert before in item['text']
    item['text'] = item['text'].replace(before, after, 1)
    return result


def wrap(text, width, size):
    line = ''
    for char in text:
        if line and FONT.text_length(line + char, fontsize=size) > width:
            yield line
            line = ''
        line += char
    if line:
        yield line


def render(path, items, layout):
    """Record truth from source items and placement, not from a comparison algorithm."""
    doc = pdf.open()
    size, margin, leading = layout['fontsize'], layout['margin'], layout['leading']
    width, bottom = 595 - 2 * margin, 780
    page, y, locations = None, 70, {}

    def new_page():
        nonlocal page, y
        page = doc.new_page(width=595, height=842)
        page.insert_font(fontname='SampleCJK', fontbuffer=FONT.buffer)
        page.insert_text((margin, 30), '采购文件验收样本（虚构）', fontname='SampleCJK', fontsize=9)
        page.insert_text((margin, 815), f'第 {len(doc)} 页', fontname='SampleCJK', fontsize=9)
        y = 70

    def line(text, key, fontsize=size, x=None):
        nonlocal y
        if page is None or y + fontsize * leading > bottom:
            new_page()
        left = margin if x is None else x
        page.insert_text((left, y + fontsize), text, fontname='SampleCJK', fontsize=fontsize)
        locations.setdefault(key, []).append(dict(page=len(doc), text=text,
            bbox=[left, y, left + FONT.text_length(text, fontsize=fontsize), y + fontsize * 1.4]))
        y += fontsize * leading

    for ordinal, item in enumerate(items, 1):
        if item.get('new_page') and page is not None:
            new_page()
        if page is None:
            new_page()
        if y + 80 > bottom:
            new_page()
        key = item['id']
        line(f'第{ordinal}条  {item["title"]}', key + ':heading', size + 2)
        if item.get('kind') == 'table':
            rows = [['设备名称', '数量', '单位', '单价（元）'], *item['rows']]
            columns = [0, .42, .59, .74, 1]
            for row_index, row in enumerate(rows):
                if y + 32 > bottom:
                    new_page()
                for column, value in enumerate(row):
                    rect = pdf.Rect(margin + width * columns[column], y,
                                    margin + width * columns[column + 1], y + 30)
                    page.draw_rect(rect, color=(.4, .4, .4), width=.5)
                    page.insert_text((rect.x0 + 5, y + 19), str(value), fontname='SampleCJK', fontsize=10)
                    cell_key = f'{key}:{row[0]}:{rows[0][column]}' if row_index else key + ':header'
                    locations.setdefault(cell_key, []).append(dict(page=len(doc), bbox=list(rect), text=str(value)))
                y += 30
        elif item.get('kind') == 'image':
            image = Image.new('RGB', (240, 120), 'white')
            ImageDraw.Draw(image).rectangle((20, 20, 220, 100), fill=item.get('color', '#1755ac'))
            stream = BytesIO(); image.save(stream, format='PNG')
            display_width = item.get('width', 240)
            if y + display_width / 2 > bottom:
                new_page()
            rect = pdf.Rect(margin, y, margin + display_width, y + display_width / 2)
            page.insert_image(rect, stream=stream.getvalue())
            locations[key] = [dict(page=len(doc), bbox=list(rect), image_sha256=sha256(stream.getvalue()).hexdigest())]
            y = rect.y1
        else:
            for text in wrap(item['text'], width, size):
                line(text, key)
        y += 16
    doc.subset_fonts()
    doc.save(path, garbage=4, deflate=True, no_new_id=True)
    doc.close()
    # Verify fixture construction (no clipped or omitted body text); not feature acceptance.
    with pdf.open(path) as check:
        extracted = ''.join(''.join(page.get_text(clip=pdf.Rect(0, 55, 595, 795)).split()) for page in check)
        for item in items:
            if item.get('kind') not in ('table', 'image'):
                assert ''.join(item['text'].split()) in extracted, item['id']
    return locations


def scan(path, *, dpi, noisy=False, mixed=False):
    """Rasterize only generated fixtures; preserve page geometry for truth coordinates."""
    target = path.with_name(path.stem + '-raster.pdf')
    rng = random.Random(59061)
    with pdf.open(path) as source, pdf.open() as result:
        for index, page in enumerate(source):
            if mixed and index == 0:
                result.insert_pdf(source, from_page=index, to_page=index)
                continue
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            image = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
            if noisy:
                for _ in range(160):
                    image.putpixel((rng.randrange(image.width), rng.randrange(image.height)), (100, 100, 100))
            stream = BytesIO(); image.save(stream, format='JPEG', quality=75 if noisy else 94)
            dest = result.new_page(width=page.rect.width, height=page.rect.height)
            dest.insert_image(dest.rect, stream=stream.getvalue())
        result.save(target, garbage=4, deflate=True, no_new_id=True)
    target.replace(path)
    with pdf.open(path) as result:
        assert all(bool(page.get_text().strip()) == (mixed and i == 0) for i, page in enumerate(result))


def scenarios():
    base = base_content()
    def case(code, title, old, new, expected, **options):
        return dict(id=code, title=title, old=old, new=new, expected=expected, **options)
    def edit(key, before, after):
        return dict(type='modified', block=key, before=before, after=after)
    yield case('S01', '完全相同', base, base, [], no_content_changes=True)
    yield case('S02', '整篇改字号、行距、页边距及分页，内容不变', base, base, [], reflow=True, no_content_changes=True)
    critical = [('price', '120000元', '125000元'), ('penalty', '0.5%', '5%'),
                ('permission', '不得转包', '可以转包'), ('count', '100米', '100件')]
    new = base
    for key, before, after in critical:
        new = changed(new, key, before, after)
    yield case('S03', '整篇重排，同时修改金额、小数点、否定词和单位', base, new,
               [edit(*item) for item in critical], reflow=True)
    moved = deepcopy(base[:2] + base[3:] + base[2:3])
    yield case('S04', '付款条款跨页移动，正文不变', base, moved,
               [dict(type='moved', block='price')], no_content_changes=True)
    yield case('S05', '付款条款跨页移动并修改金额', base, changed(moved, 'price', '120000元', '125000元'),
               [dict(type='moved', block='price'), edit('price', '120000元', '125000元')], reflow=True)
    long_text = ''.join(f'第{i}项检查记录应包含设备状态、操作过程、检查结论和双方确认情况。' for i in range(1, 31))
    long = [paragraph('long', '跨页验收条款', long_text)]
    yield case('S06', '一个长段落改排成多页，反向比较检验合并', long, long, [], reflow=True,
               no_content_changes=True, reverse_required=True)
    new = deepcopy(base[:3] + [paragraph('training', '新增培训', '供应方须提供两次现场培训，并提交培训记录。')] +
                   [item for item in base[3:] if item['id'] != 'warranty'])
    yield case('S07', '插入和删除条款，后续编号重新排列', base, new,
               [dict(type='added', block='training'), dict(type='deleted', block='warranty')], reflow=True)
    repeated = [paragraph('lot_a', '第一标段付款', '合同金额为120000元，验收后支付，付款前提供发票。', new_page=True),
                paragraph('lot_b', '第二标段付款', '合同金额为120000元，验收后支付，付款前提供发票。', new_page=True)]
    yield case('S08', '重复条款只修改第二标段，必须保留所属章节', repeated,
               changed(repeated, 'lot_b', '120000元', '125000元'), [edit('lot_b', '120000元', '125000元')], reflow=True)
    ambiguous = [paragraph('copy_a', '付款条件', '金额为120000元，验收后支付。', new_page=True),
                 paragraph('copy_b', '付款条件', '金额为120000元，验收后支付。', new_page=True)]
    yield case('S09', '完全重复且缺少上下文，不能强行确定对应关系', ambiguous,
               changed(ambiguous, 'copy_b', '120000元', '125000元'),
               [dict(type='ambiguous', old_candidates=['copy_a', 'copy_b'], before='120000元', after='125000元')],
               requires_review=True)
    table = [dict(id='equipment', title='设备清单', kind='table',
                  rows=[['设备甲', '10', '台', '1200'], ['设备乙', '10', '套', '1200'], ['设备丙', '8', '米', '300']])]
    new = deepcopy(table); new[0]['rows'] = [['设备丙', '8', '米', '300'], ['设备乙', '12', '台', '1200'], ['设备甲', '10', '台', '1200']]
    yield case('S10', '表格行重排，只修改同一设备的数量及单位', table, new,
               [edit('equipment:设备乙:数量', '10', '12'), edit('equipment:设备乙:单位', '套', '台')], reflow=True)
    pictures = [dict(id='figure_a', title='设备示意图', kind='image', color='#1755ac', width=240),
                dict(id='figure_b', title='标记示意图', kind='image', color='#1755ac', width=200)]
    new = deepcopy(pictures[::-1]); new[0]['color'] = '#c83240'; new[1]['width'] = 180
    yield case('S11', '图片移动缩放与图片内容改色同时发生', pictures, new,
               [dict(type='image_modified', block='figure_b'), dict(type='layout_only', block='figure_a')])
    yield case('S12', '扫描分辨率、压缩和噪点变化，正文相同', base[:3], base[:3], [],
               raster=True, no_content_changes=True)
    yield case('S13', '扫描件重排并修改金额', base[:3], changed(base[:3], 'price', '120000元', '125000元'),
               [edit('price', '120000元', '125000元')], raster=True, reflow=True)
    mixed = deepcopy(base)
    for item in mixed:
        item['new_page'] = True
    yield case('S14', '文字页与扫描页混合，仅扫描页金额变化', mixed,
               changed(mixed, 'price', '120000元', '125000元'), [edit('price', '120000元', '125000元')], mixed=True)
    for pages in (20, 100, 200):
        items = [paragraph(f'load_{i}', f'第{i}部分采购条款',
                           f'本部分采购数量为{i}台。合同金额为120000元，验收通过后办理付款。' * 3,
                           new_page=True) for i in range(1, pages + 1)]
        key = f'load_{pages // 2}'
        yield case(f'P{pages:03}', f'{pages}页文字文档负载，只有一处金额修改', items,
                   changed(items, key, '120000元', '125000元'), [edit(key, '120000元', '125000元')], pages=pages)


def generate(output):
    archive = Path(str(output) + '.zip')
    if archive.exists():
        raise FileExistsError(archive)
    output.mkdir(parents=True, exist_ok=False)
    records = []
    for case in scenarios():
        folder = output / case['id']; folder.mkdir()
        record = {key: value for key, value in case.items() if key not in ('old', 'new')}
        record['status'] = 'not_evaluated'
        for side in ('old', 'new'):
            path = folder / f'{side}.pdf'
            locations = render(path, case[side], REFLOW if side == 'new' and case.get('reflow') else NORMAL)
            if case.get('raster') or case.get('mixed'):
                scan(path, dpi=120 if side == 'new' else 144, noisy=side == 'new', mixed=case.get('mixed', False))
            with pdf.open(path) as doc:
                page_count = len(doc)
            if 'pages' in case:
                assert page_count == case['pages']
            record[side] = dict(file=f'{case["id"]}/{side}.pdf', pages=page_count,
                bytes=path.stat().st_size, sha256=sha256(path.read_bytes()).hexdigest(),
                locations=locations, source_items=case[side])
        if case['id'] == 'S06':
            assert len({r['page'] for r in record['new']['locations']['long']}) > 1
            assert record['new']['pages'] > record['old']['pages']
        (folder / 'expected.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        records.append(record)
    manifest = dict(schema=1, name='PDF内容对比验收样本 v1', seed=59061, engine=pdf.__version__,
        note='全部为虚构样本；答案由原始内容及人工指定变化生成，不读取比较算法的结果。所有功能验收尚未执行。',
        coordinates='页码从1开始，bbox为PDF点坐标；内部块ID仅存在答案文件中，不写入PDF。', cases=records)
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    write_index(output, records)
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(output.rglob('*')):
            if path.is_file():
                bundle.write(path, path.relative_to(output.parent))
    print(json.dumps(dict(cases=len(records), pdfs=len(records) * 2,
                         archive=str(archive), archive_bytes=archive.stat().st_size), ensure_ascii=False))


def write_index(output, records):
    kinds = dict(modified='内容修改', moved='位置移动', added='新增', deleted='删除',
                 ambiguous='对应关系待核对', image_modified='图片内容修改', layout_only='仅布局变化')
    rows = []
    for record in records:
        names = {item['id']: item['title'] for side in ('old', 'new') for item in record[side]['source_items']}
        descriptions = []
        for change in record['expected']:
            parts = change.get('block', '').split(':')
            label = ' / '.join([names.get(parts[0], '重复付款条款'), *parts[1:]])
            value = f"：{change['before']} → {change['after']}" if 'before' in change else ''
            descriptions.append(f"{kinds[change['type']]} · {label}{value}")
        changes = '；'.join(descriptions) or '不应报告正文内容修改'
        rows.append(f'<tr><td>{record["id"]}</td><td>{escape(record["title"])}</td>'
                    f'<td><a href="{record["old"]["file"]}">旧版 {record["old"]["pages"]} 页</a> / '
                    f'<a href="{record["new"]["file"]}">新版 {record["new"]["pages"]} 页</a></td>'
                    f'<td>{escape(changes)}<br><a href="{record["id"]}/expected.json">完整答案与定位</a></td></tr>')
    (output / 'index.html').write_text('<!doctype html><meta charset="utf-8"><title>PDF 对比验收样本</title>'
        '<style>body{font:15px/1.7 system-ui;max-width:1200px;margin:40px auto;padding:0 24px;color:#25282b}'
        'table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:12px;border-bottom:1px solid #ddd}'
        'th{background:#f0f1f2}a{color:#0f766e}td:first-child{white-space:nowrap}</style>'
        '<h1>PDF 对比验收样本 v1</h1><p>14组功能场景 + 20/100/200页负载。全部虚构，未执行功能验收。'
        '请先看标准答案，再核对产品输出；不能把标准答案当作已实现效果。</p>'
        '<p>每组 expected.json 包含预期变化、原文、页码、坐标和 SHA-256。S06还须反向比较；'
        'S09必须表达歧义；S12～S14涉及OCR。新增样本生成器不在应用打包目录。</p>'
        '<table><tr><th>编号</th><th>场景</th><th>文件</th><th>预期</th></tr>' + ''.join(rows) + '</table>', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    generate(parser.parse_args().output)
