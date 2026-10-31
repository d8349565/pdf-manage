"""Deterministic real PDF fixtures and measured comparison evidence for issue #59.

python -m app.pdfcompare_demo --output /tmp/pdfcompare-evidence
Only writes into a new, explicitly selected output directory.
"""
from dataclasses import asdict, replace
from io import BytesIO
import argparse
import hashlib
import json
from pathlib import Path
import random
import time

from PIL import Image, ImageDraw
import pymupdf

from app.pdfcompare import CompareOptions, compare_files, preview_pair


def vector_pdf(path, specs):
    with pymupdf.open() as doc:
        for spec in specs:
            page = doc.new_page(width=spec.get('width', 420), height=spec.get('height', 594))
            if spec.get('blank'):
                continue
            dx, dy = spec.get('offset', (0, 0))
            page.insert_text((35 + dx, 55 + dy), spec.get('title', 'Tender / Section A'), fontsize=18)
            page.insert_text((35 + dx, 95 + dy), f"Contract value: {spec.get('amount', '120000')} USD", fontsize=14)
            page.insert_text((35 + dx, 130 + dy), 'Delivery: 30 days. Warranty: 24 months.', fontsize=12)
            page.draw_rect(pymupdf.Rect(35 + dx, 160 + dy, 225 + dx, 205 + dy),
                           color=spec.get('color', (0.1, .35, .85)),
                           fill=spec.get('color', (0.1, .35, .85)))
            if spec.get('image', True):
                image = Image.new('RGB', (120, 70), '#fff4cf')
                draw = ImageDraw.Draw(image); draw.ellipse((20, 5, 90, 65), fill='#dc4d52')
                stream = BytesIO(); image.save(stream, format='PNG')
                page.insert_image(pymupdf.Rect(35 + dx, 245 + dy, 155 + dx, 315 + dy), stream=stream.getvalue())
            for i in range(7):
                page.insert_text((35 + dx, 370 + 18 * i + dy), f'Clause {i + 1}: supplier documentation is required.', fontsize=10)
            if spec.get('annotation'):
                page.add_rect_annot(pymupdf.Rect(25, 70, 300, 110)).update()
        doc.save(path)


def scan_pdf(path, image, size=(420, 594), jpeg_quality=None):
    stream = BytesIO()
    image.save(stream, format='JPEG' if jpeg_quality else 'PNG', **({'quality': jpeg_quality} if jpeg_quality else {}))
    with pymupdf.open() as doc:
        page = doc.new_page(width=size[0], height=size[1])
        page.insert_image(page.rect, stream=stream.getvalue())
        doc.save(path)


def generate_cases(root):
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    cases = {}

    def add(name, old, new, expected):
        a, b = root / f'{name}-old.pdf', root / f'{name}-new.pdf'
        vector_pdf(a, old); vector_pdf(b, new)
        cases[name] = dict(old=str(a), new=str(b), expected=expected)

    add('identical', [{}], [{}], ['equal'])
    add('text', [{}], [dict(amount='125000')], ['changed'])
    add('image_removed', [{}], [dict(image=False)], ['changed'])
    add('image_added', [dict(image=False)], [{}], ['changed'])
    add('color', [{}], [dict(color=(.85, .12, .2))], ['changed'])
    chapters = [dict(title=f'Tender Section {x}', amount=str(120000 + 10000 * i))
                for i, x in enumerate(('ALPHA', 'BRAVO', 'CHARLIE'))]
    add('insert', chapters, chapters[:1] + [dict(title='Addendum ONLY - Fire Safety')] + chapters[1:],
        ['equal', 'inserted', 'equal', 'equal'])
    add('mixed_insert', chapters, [dict(chapters[0], amount='125000', color=(.85, .12, .2), image=False),
        dict(title='Addendum ONLY - Fire Safety')] + chapters[1:], ['changed', 'inserted', 'equal', 'equal'])
    add('delete', chapters, chapters[:1] + chapters[2:], ['equal', 'deleted', 'equal'])
    add('blank_insert', chapters, chapters[:1] + [dict(blank=True)] + chapters[1:],
        ['equal', 'inserted', 'equal', 'equal'])
    add('size', [dict(blank=True)], [dict(blank=True, width=430)], ['changed'])
    add('blank', [dict(blank=True)], [dict(blank=True)], ['equal'])
    add('translation', [{}], [dict(offset=(1.3, 0))], ['changed'])
    add('annotation', [{}], [dict(annotation=True)], ['changed'])
    source = root / 'identical-old.pdf'
    with pymupdf.open(source) as doc:
        pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False)
        scan = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
    rng = random.Random(59)
    noisy = scan.copy()
    # Random channel noise +/-8 and isolated, high-contrast scanner speckles.
    noisy.frombytes(bytes(max(0, min(255, v + rng.randint(-8, 8))) for v in scan.tobytes()))
    for _ in range(120):
        x, y = rng.randrange(scan.width), rng.randrange(scan.height)
        noisy.putpixel((x, y), (20, 20, 20))
    for name, image, quality, expected in (
            ('scan_noise', noisy, None, ['equal']),
            ('jpeg', scan, 85, ['equal']),
            ('resampled', scan.resize((scan.width * 2, scan.height * 2)), None, ['changed'])):
        a, b = root / f'{name}-old.pdf', root / f'{name}-new.pdf'
        scan_pdf(a, scan); scan_pdf(b, image, jpeg_quality=quality)
        cases[name] = dict(old=str(a), new=str(b), expected=expected)
        if name == 'resampled':
            cases[name]['known_false_positive'] = True
    # Noise plus a real new mark proves tolerances do not simply ignore all scan changes.
    marked = noisy.copy(); ImageDraw.Draw(marked).rectangle((350, 450, 450, 480), fill='black')
    a, b = root / 'noise_change-old.pdf', root / 'noise_change-new.pdf'
    scan_pdf(a, scan); scan_pdf(b, marked)
    cases['noise_change'] = dict(old=str(a), new=str(b), expected=['changed'])
    return cases


def run_evidence(root):
    root = Path(root); root.mkdir(parents=True, exist_ok=False)
    cases = generate_cases(root / 'fixtures')
    strict = CompareOptions(threshold=0, blur=0, min_pixels=1)
    rows = []
    for name, case in cases.items():
        hashes = [hashlib.sha256(Path(case[k]).read_bytes()).hexdigest() for k in ('old', 'new')]
        result = compare_files(case['old'], case['new'])
        observed = [r['status'] for r in result['rows']]
        raw = compare_files(case['old'], case['new'], strict)
        record = dict(case=name, expected=case['expected'], observed=observed,
            passed=observed == case['expected'], seconds=result['seconds'],
            known_false_positive=case.get('known_false_positive', False),
            pairing=[[r['old_page'], r['new_page']] for r in result['rows']],
            filtered_pixels=sum(r['changed_pixels'] for r in result['rows']),
            strict_pixels=sum(r['changed_pixels'] for r in raw['rows']),
            strict_statuses=[r['status'] for r in raw['rows']],
            source_hashes_unchanged=hashes == [hashlib.sha256(Path(case[k]).read_bytes()).hexdigest() for k in ('old', 'new')])
        if name == 'translation':
            aligned = compare_files(case['old'], case['new'], replace(CompareOptions(), align_pixels=2))
            record['aligned_pixels'] = aligned['rows'][0]['changed_pixels']
            record['alignment'] = aligned['rows'][0]['shift']
        if name == 'insert':
            indexed = compare_files(case['old'], case['new'], replace(CompareOptions(), pairing='index'))
            record['index_pairing_statuses'] = [r['status'] for r in indexed['rows']]
        target = root / name; target.mkdir()
        (target / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        index = next((i for i, row in enumerate(result['rows']) if row['status'] != 'equal'), 0)
        _, images = preview_pair(result, index)
        for key, image in images.items():
            image.save(target / f'{key}.png')
        rows.append(record)
    report = dict(engine=pymupdf.__version__, options=asdict(CompareOptions()), cases=rows)
    (root / 'measurements.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', required=True)
    run_evidence(parser.parse_args().output)
