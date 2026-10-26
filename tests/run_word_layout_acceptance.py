"""Explicit end-to-end acceptance on recorded generated cases using real OCR and Word."""
import argparse
from collections import Counter
from difflib import SequenceMatcher
import hashlib
import html
import json
from pathlib import Path
import time
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pymupdf as p
from PIL import Image,ImageChops,ImageStat,ImageDraw

from benchmark_layout_algorithms import algorithm_snapshot
from app.wordconvert import _convert_one
from app.ocr.word_export import W


def normalized(text):
    return ''.join(text.split())


def measure(reference,docx,rendered,case,folder):
    with ZipFile(docx) as archive:
        body=ET.fromstring(archive.read('word/document.xml'))
        tables=[dict(rows=len(t.findall(f'{{{W}}}tr')),columns=len(t.findall(f'{{{W}}}tblGrid/{{{W}}}gridCol')))
                for t in body.iter(f'{{{W}}}tbl')]
        all_text=''.join(n.text or '' for n in body.iter(f'{{{W}}}t'))
        hidden=''.join(n.text or '' for run in body.iter(f'{{{W}}}r')
                       if run.find(f'{{{W}}}rPr/{{{W}}}vanish') is not None for n in run.iter(f'{{{W}}}t'))
    with p.open(reference) as source,p.open(rendered) as target:
        expected=normalized(''.join(page.get_text() for page in source))
        actual=normalized(''.join(page.get_text() for page in target))
        a,b=Counter(expected),Counter(actual)
        retained=sum((a&b).values())
        for index,page in enumerate(source):page.get_pixmap(dpi=100).save(folder/f'source-{index+1}.png')
        for index,page in enumerate(target):page.get_pixmap(dpi=100).save(folder/f'rendered-{index+1}.png')
        first,second=source[0],target[0]
        heading=first.search_for('Document layout evaluation')
        restored=second.search_for('Document layout evaluation')
        heading_error=max(abs(v-w) for v,w in zip(heading[0],restored[0])) if heading and restored else None
        region_errors=[]
        for index,region in enumerate(case['regions']):
            before=first.get_pixmap(dpi=144,clip=p.Rect(region['bbox']),alpha=False)
            after=second.get_pixmap(dpi=144,clip=p.Rect(region['bbox']),alpha=False)
            before.save(folder/f'region-{index}-source.png');after.save(folder/f'region-{index}-rendered.png')
            ia=Image.frombytes('RGB',(before.width,before.height),before.samples)
            ib=Image.frombytes('RGB',(after.width,after.height),after.samples)
            if ia.size!=ib.size:ib=ib.resize(ia.size)
            region_errors.append(dict(kind=region['kind'],mean_channel_error=round(sum(ImageStat.Stat(ImageChops.difference(ia,ib)).mean)/3,2)))
        record=dict(source_pages=len(source),rendered_pages=len(target),
            paper_size_error=max(abs(v-w) for v,w in zip(first.rect,second.rect)),
            source_characters=len(expected),visible_characters=len(actual),
            character_inventory_recall=round(retained/max(1,len(expected)),4),
            ordered_text_similarity=round(SequenceMatcher(None,expected,actual,autojunk=False).ratio(),4),
            missing_inventory=dict(a-b),extra_inventory=dict(b-a),
            docx_characters=len(normalized(all_text)),hidden_characters=len(normalized(hidden)),
            heading_bbox_error=heading_error,tables=tables,regions=region_errors)
        (folder/'source.txt').write_text('\n'.join(page.get_text() for page in source),encoding='utf-8')
        (folder/'rendered.txt').write_text('\n'.join(page.get_text() for page in target),encoding='utf-8')
    issues=[]
    if record['rendered_pages']!=record['source_pages']:issues.append('页数变化')
    if heading_error is None or heading_error>8:issues.append('标题缺失或位置变化')
    if record['missing_inventory'] or record['extra_inventory']:issues.append('可见文字差异，需区分OCR与图像保留')
    if record['ordered_text_similarity']<1:issues.append('阅读顺序或文字差异')
    if case['family'] in ('ruled_table','merged_table'):
        expected_grid=dict(rows=case['parameters']['rows'],columns=case['parameters']['columns'])
        if tables!=[expected_grid]:issues.append('原生表格结构不符')
    elif case['family'] not in ('fielded_form',) and tables:
        issues.append('非表格区域生成表格')
    record['automatic_review_flags']=issues
    record['visual_review']='pending'
    return record


def write_gallery(root,records):
    rows=[]
    for entry in records:
        name=entry['case']+'-'+entry['format']; relative=name+'/'
        flags='；'.join(entry.get('automatic_review_flags',[])) or '自动检查未标记异常，仍需逐页目视对比'
        if entry['state']!='success':
            rows.append(f'<section><h2>{html.escape(name)}</h2><p>失败：{html.escape(entry.get("error",""))}</p></section>');continue
        review=html.escape(entry.get('visual_review_notes','尚未完成目视核对'))
        rows.append(f'<section id="{name}"><h2>{name}</h2><p>{html.escape(flags)}；页数 {entry["source_pages"]} → {entry["rendered_pages"]}</p>'
                    f'<p><strong>逐页核对：</strong>{review}</p>'
                    f'<p><a href="{relative}converted.docx">Word</a> · <a href="{relative}rendered.pdf">Word实际渲染PDF</a></p>'
                    f'<div class="pair"><figure><figcaption>源页面</figcaption><img loading="lazy" src="{relative}source-1.png"></figure>'
                    f'<figure><figcaption>Word实际渲染</figcaption><img loading="lazy" src="{relative}rendered-1.png"></figure></div></section>')
    links=' · '.join(f'<a href="#{e["case"]}-{e["format"]}">{e["case"]}/{e["format"]}</a>' for e in records)
    summary=html.escape((root/'review-summary.txt').read_text(encoding='utf-8')) if (root/'review-summary.txt').exists() else '验收仍在进行，以下为实际生成结果。'
    (root/'comparison.html').write_text('<!doctype html><html lang="zh"><meta charset="utf-8"><title>PDF转Word通用算法逐页验收</title>'
        '<style>body{font:15px system-ui;background:#f0f3f4;color:#20323a;margin:25px}nav{line-height:2.2}section{margin:35px 0}.pair{display:grid;grid-template-columns:1fr 1fr;gap:18px}figure{margin:0}img{width:100%;background:white}figcaption{padding:10px;background:white;font-weight:bold}a{color:#066e63}</style>'
        '<h1>实际PDF → Word → Microsoft Word渲染</h1><p>扫描输入不带文字层，实际运行本地OCR。各指标分别报告，自动检查未标记异常不代表视觉验收通过。</p>'
        f'<p style="white-space:pre-wrap;max-width:1000px">{summary}</p>'
        f'<nav>{links}</nav>'+''.join(rows)+'</html>',encoding='utf-8')


def write_triptychs(root):
    target=root/'review';target.mkdir(exist_ok=True)
    for native in sorted(root.glob('*-native')):
        scan=root/native.name.replace('-native','-scan')
        paths=[native/'source-1.png',native/'rendered-1.png',scan/'rendered-1.png']
        if not all(path.exists() for path in paths):continue
        pictures=[Image.open(path).convert('RGB') for path in paths]
        pictures=[im.resize((round(im.width*1100/im.height),1100)) for im in pictures]
        canvas=Image.new('RGB',(sum(im.width for im in pictures)+80,1160),'#e9edef');draw=ImageDraw.Draw(canvas);x=20
        for label,im in zip(('Source','Native PDF -> Word','Scan PDF -> OCR -> Word'),pictures):
            draw.text((x,12),label,fill='black');canvas.paste(im,(x,40));x+=im.width+20
        canvas.save(target/(native.name.removesuffix('-native')+'.png'))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cases',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--formats',nargs='+',choices=('native','scan'),default=['native','scan'])
    parser.add_argument('--case',action='append',help='Explicit regression subset; omit for full acceptance')
    args=parser.parse_args(); root=args.output.resolve();root.mkdir(parents=True,exist_ok=False)
    cases=json.loads((args.cases/'cases.json').read_text(encoding='utf-8'))
    snapshot=algorithm_snapshot()
    manifest=dict(algorithm_snapshot=snapshot,cases_manifest_sha256=hashlib.sha256((args.cases/'cases.json').read_bytes()).hexdigest(),
                  formats=args.formats,regression_subset=args.case,status='running')
    (root/'run.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    records=[]
    for case in cases:
        name=Path(case['image']).stem
        if args.case and name not in args.case:continue
        reference=Path(case['image']).with_suffix('.pdf')
        assert hashlib.sha256(Path(case['image']).read_bytes()).hexdigest()==case['image_sha256']
        for fmt in args.formats:
            folder=root/f'{name}-{fmt}';folder.mkdir();source=reference
            if fmt=='scan':
                source=folder/'input.pdf'
                with p.open(reference) as ref,p.open() as scan:
                    page=scan.new_page(width=ref[0].rect.width,height=ref[0].rect.height)
                    page.insert_image(page.rect,filename=case['image']);scan.save(source)
                with p.open(source) as scan:assert not scan[0].get_text().strip()
            original=hashlib.sha256(source.read_bytes()).hexdigest();started=time.perf_counter()
            entry=dict(case=name,family=case['family'],format=fmt,state='failed')
            print('START',name,fmt,flush=True)
            try:
                result=_convert_one(str(source),str(folder/'converted.docx'),False,lambda _:None,lambda:False,direction='pdf_to_word')
                if result[0]!='success':raise RuntimeError(result)
                entry['conversion_message']=result[1]
                result=_convert_one(str(folder/'converted.docx'),str(folder/'rendered.pdf'),False,lambda _:None,lambda:False)
                if result[0]!='success':raise RuntimeError(result)
                entry['word_version']=result[2]
                entry.update(measure(reference,folder/'converted.docx',folder/'rendered.pdf',case,folder),state='success')
            except Exception as exc:
                entry['error']=str(exc)
            entry['seconds']=round(time.perf_counter()-started,2)
            entry['source_unchanged']=original==hashlib.sha256(source.read_bytes()).hexdigest()
            records.append(entry)
            (root/'results.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
            write_gallery(root,records)
            print('DONE',name,fmt,entry['state'],entry.get('automatic_review_flags',entry.get('error')),flush=True)
    manifest['status']='completed_awaiting_visual_review' if algorithm_snapshot()==snapshot else 'invalid_algorithm_changed'
    (root/'run.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    write_triptychs(root)
    print('RESULTS',len(records),'completed',sum(r['state']=='success' for r in records),flush=True)


if __name__=='__main__':
    main()
