"""Opt-in, generated-only layout screening; not an end-to-end DOCX acceptance test.

Keep family splits and model thresholds fixed before running. Never use customer
documents or tune the pipeline against held-out cases reported by this script.
"""
import argparse
import hashlib
import json
import random
import secrets
import socket
import sys
import time
from pathlib import Path

import pymupdf as p

FAMILIES=('ruled_table','merged_table','prose','two_columns',
          'fielded_form','checklist_panels','seal_notice','diagram_poster')
TEXTS=('This document describes a routine project review and records independent findings.',
       'The inspection team recorded equipment status and the next maintenance schedule.',
       'Each department submits its own observations before the final planning meeting.',
       'Delivery details are checked against the approved inventory and service records.')


def build_case(path,family,variant,seed):
    rng=random.Random(f'{seed}:{family}:{variant}')
    width,height=rng.choice((560,595,612,680,720,760)),rng.choice((800,842,900))
    left=rng.randint(32,60); right=width-left; top=rng.randint(130,190)
    text=rng.choice(TEXTS); font=rng.choice(('helv','tiro','cour'))
    ink=rng.choice(((.7,.1,.1),(.1,.25,.65),(.45,.1,.5)))
    fill=rng.choice(((.85,.93,.94),(.97,.92,.8),(.91,.89,.96)))
    parameters=dict(width=width,height=height,left=left,top=top,font=font,ink=ink,fill=fill)
    regions=[]
    with p.open() as doc:
        page=doc.new_page(width=width,height=height)
        page.insert_text((left,62),'Document layout evaluation',fontsize=rng.randint(18,24),fontname=rng.choice(('hebo','tibo','cobo')))
        page.insert_text((left,90),'Independent generated content - not a customer document',fontsize=9)
        if family in ('ruled_table','merged_table'):
            cols=rng.randint(2,5); rows=rng.randint(4,8); rowheight=rng.randint(28,44)
            parameters.update(columns=cols,rows=rows,rowheight=rowheight)
            bounds=(left,top,right,top+rows*rowheight)
            page.draw_rect((left,top,right,top+rowheight),fill=fill,color=None)
            for row in range(rows+1):
                page.draw_line((left,top+row*rowheight),(right,top+row*rowheight),color=(.4,.4,.4),width=.7)
            for col in range(cols+1):
                x=left+col*(right-left)/cols
                page.draw_line((x,top+(rowheight if family=='merged_table' and col not in (0,cols) else 0)),
                               (x,top+rows*rowheight),color=(.4,.4,.4),width=.7)
            for row in range(rows):
                for col in range(1 if family=='merged_table' and row==0 else cols):
                    page.insert_text((left+8+col*(right-left)/cols,top+22+row*rowheight),
                                     f'Column {col+1}' if row==0 else f'{rng.randint(10,990)}.{rng.randint(0,99):02d}',fontsize=10,fontname=font)
            regions=[dict(kind='table',bbox=bounds)]
        elif family in ('prose','two_columns'):
            count=1 if family=='prose' else 2
            for column in range(count):
                x=left+column*(right-left+20)/count
                for row in range(rng.randint(12,18)):
                    phrase=text[:72 if count==1 else 36]
                    page.insert_text((x,top+row*23),phrase,fontsize=10 if count==1 else 9,fontname=font)
        elif family=='fielded_form':
            bounds=(left,top,right,top+430)
            page.draw_rect(bounds,color=ink,width=.7)
            for y in (top+70,top+340):
                page.draw_line((left,y),(right,y),color=ink,width=.7)
            page.draw_line((width/2,top),(width/2,top+70),color=ink,width=.7)
            for c in range(2):
                page.insert_text((left+10+c*(right-left)/2,top+25),f'Organization {c+1}',fontsize=11)
            cols=rng.randint(3,6); rows=rng.randint(3,6)
            parameters.update(columns=cols,rows=rows)
            for row in range(rows):
                for col in range(cols):
                    x=left+10+col*(right-left-20)/cols
                    page.insert_text((x,top+100+row*24),f'F{col}' if row==0 else str(rng.randint(100,999)),fontsize=10,fontname=font)
            for col in range(3):
                page.insert_text((left+10+col*(right-left)/3,top+377),f'Approval field {col+1}',fontsize=10)
            # Region detection may call this a table. A later topology stage must
            # still distinguish its outer frame from the independent fields.
            regions=[dict(kind='form',bbox=bounds)]
        elif family=='checklist_panels':
            for col,panel_fill in enumerate((fill,(.98,.94,.87))):
                x=left+col*(right-left+20)/2; w=(right-left-20)/2
                page.draw_rect((x,top,x+w,top+300),fill=panel_fill,color=None)
                page.insert_text((x+10,top+25),'Tasks and observations',fontsize=11)
                for row in range(rng.randint(4,7)):
                    page.insert_text((x+10,top+60+row*28),f'[ ] Check item {rng.randint(10,99)}',fontsize=9,fontname=font)
        elif family=='seal_notice':
            for row in range(10):
                page.insert_text((left,top+row*23),text[:72],fontsize=10,fontname=font)
            cx=right-rng.randint(65,115);cy=top+rng.randint(325,380)
            if variant==0:
                page.draw_circle((cx,cy),43,color=ink,width=2)
            else:
                page.draw_rect((cx-43,cy-43,cx+43,cy+43),color=ink,width=2)
            page.insert_text((cx-24,cy),'TEST ONLY',fontsize=9,color=ink)
            regions=[dict(kind='seal',bbox=(cx-45,cy-45,cx+45,cy+45))]
        else:
            # Negative examples: coloured rectangles and circles are not all seals.
            for c in range(3):
                x=left+c*(right-left)/3
                page.draw_rect((x,top,x+120,top+90),fill=fill,color=ink,width=2)
                page.insert_text((x+12,top+45),f'System {c+1}',fontsize=11)
            page.draw_circle((width/2,top+220),65,color=ink,width=2)
            page.insert_text((width/2-35,top+220),'PROCESS',fontsize=12)
            regions=[dict(kind='figure',bbox=(left,top,right,top+290))]
        page.insert_text((left,height-30),f'Generated case {FAMILIES.index(family)+1}-{variant+1}',fontsize=8)
        doc.save(path)
        page.get_pixmap(dpi=150).save(path.with_suffix('.png'))
        with p.open() as scan:
            target=scan.new_page(width=width,height=height)
            target.insert_image(target.rect,stream=page.get_pixmap(dpi=150).tobytes('png'))
            # Perfect OCR isolates layout decisions from recognition errors.
            for block in page.get_text('dict')['blocks']:
                for line in block.get('lines',()):
                    for span in line['spans']:
                        target.insert_text(span['origin'],span['text'],fontsize=span['size'],render_mode=3)
            scan.save(path.with_name(path.stem+'-oracle.pdf'))
    return dict(family=family,variant=variant,split='development' if FAMILIES.index(family)<4 else 'heldout_family',
                parameters=parameters,image=str(path.with_suffix('.png')),
                image_sha256=hashlib.sha256(path.with_suffix('.png').read_bytes()).hexdigest(),
                oracle_pdf=str(path.with_name(path.stem+'-oracle.pdf')),regions=regions)


def algorithm_snapshot():
    root=Path(__file__).resolve().parents[1]
    paths=sorted((root/'app').rglob('*.py'))+[Path(__file__).resolve()]
    hashes={path.relative_to(root).as_posix():hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    return hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()


def model_snapshot(directory):
    hashes={}
    for name in ('pp_layout_cdla','pp_doc_layoutv3'):
        with (directory/(name+'.onnx')).open('rb') as stream:
            hashes[name]=hashlib.file_digest(stream,'sha256').hexdigest()
    return hashes


def iou(a,b):
    a,b=p.Rect(a),p.Rect(b)
    inter=(a&b).get_area()
    return inter/max(1,a.get_area()+b.get_area()-inter)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--deps',required=True,type=Path)
    parser.add_argument('--models',required=True,type=Path)
    parser.add_argument('--replay-seed',type=int,help='Replay a recorded seed for regression only; never label as new acceptance')
    parser.add_argument('--generate-only',action='store_true',help='Validate new-case generation without running the candidate engines')
    args=parser.parse_args(); root=args.output.resolve(); root.mkdir(parents=True,exist_ok=False)
    snapshot=algorithm_snapshot()
    seed=args.replay_seed if args.replay_seed is not None else secrets.randbits(64)
    manifest=dict(seed=seed,mode='regression_replay' if args.replay_seed is not None else 'fresh_cases',
                  algorithm_snapshot=snapshot,model_sha256=model_snapshot(args.models),
                  conf_thresh=.5,iou_thresh=.5,status='generating')
    manifest_path=root/'run.json'
    manifest_path.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    cases=[build_case(root/f'{family}-{variant}.pdf',family,variant,seed) for family in FAMILIES for variant in range(2)]
    (root/'cases.json').write_text(json.dumps(cases,indent=2),encoding='utf-8')
    if algorithm_snapshot()!=snapshot:
        raise RuntimeError('Algorithm changed after case generation; this run cannot count as validation')
    if args.generate_only:
        manifest['status']='generated_only_not_evaluated'
        manifest_path.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        print(json.dumps(manifest,indent=2)); return
    sys.path.insert(0,str(args.deps.resolve()))
    from rapid_layout import RapidLayout
    from app.documentlayout import page_layout
    def deny_network(*args,**kwargs):
        raise RuntimeError('Benchmark inference must remain local')
    socket.create_connection=socket.socket.connect=socket.socket.connect_ex=deny_network
    results=[]
    engines=[('geometry_oracle',None)]
    for model in ('pp_layout_cdla','pp_doc_layoutv3'):
        engine=RapidLayout(model_type=model,model_dir_or_path=str((args.models/(model+'.onnx')).resolve()),
                           conf_thresh=.5,engine_cfg={'intra_op_num_threads':2,'inter_op_num_threads':1})
        engines.append((model,engine))
    for name,engine in engines:
        for case in cases:
            started=time.perf_counter()
            if engine is None:
                with p.open(case['oracle_pdf']) as doc:
                    layout,_=page_layout(doc[0],preserve_positions=True,spatial=True)
                    boxes=[dict(kind='table',bbox=item['bbox'],score=None) for item in layout if item['kind']=='table']
            else:
                prediction=engine(case['image'])
                boxes=[dict(kind=label,bbox=[float(v)*72/150 for v in bounds],score=float(score))
                       for label,bounds,score in zip(prediction.class_names,prediction.boxes,prediction.scores)]
            seconds=time.perf_counter()-started
            tables=[b for b in boxes if b['kind'].lower()=='table']
            expected=[b for b in case['regions'] if b['kind']=='table']
            forms=[b for b in case['regions'] if b['kind']=='form']
            matched=sum(any(iou(e['bbox'],b['bbox'])>=.5 for b in tables) for e in expected)
            unexpected=sum(not any(iou(e['bbox'],b['bbox'])>=.5 for e in expected+forms) for b in tables)
            form_hits=sum(any(iou(e['bbox'],b['bbox'])>=.5 for b in tables) for e in forms)
            seals=[b for b in case['regions'] if b['kind']=='seal']
            seal_hits=sum(any(b['kind']=='seal' and iou(e['bbox'],b['bbox'])>=.5 for b in boxes) for e in seals)
            record=dict(engine=name,family=case['family'],variant=case['variant'],split=case['split'],
                        seconds=round(seconds,3),expected_regions=len(expected),matched_regions=matched,
                        unexpected_tables=unexpected,expected_form_containers=len(forms),form_container_hits=form_hits,
                        expected_seals=len(seals),seal_hits=seal_hits if name=='pp_doc_layoutv3' else None,regions=boxes)
            results.append(record)
            print(name,case['family'],case['variant'],'matched',matched,'false_tables',unexpected,'seconds',round(seconds,3),flush=True)
            (root/'results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    summary=[]
    for name,_ in engines:
        for split in ('development','heldout_family'):
            rows=[r for r in results if r['engine']==name and r['split']==split]
            summary.append(dict(engine=name,split=split,cases=len(rows),expected=sum(r['expected_regions'] for r in rows),
                                matched=sum(r['matched_regions'] for r in rows),unexpected_tables=sum(r['unexpected_tables'] for r in rows),
                                expected_form_containers=sum(r['expected_form_containers'] for r in rows),
                                form_container_hits=sum(r['form_container_hits'] for r in rows),
                                expected_seals=sum(r['expected_seals'] for r in rows),
                                seal_hits=sum(r['seal_hits'] for r in rows) if name=='pp_doc_layoutv3' else None,
                                mean_seconds=round(sum(r['seconds'] for r in rows)/len(rows),3)))
    (root/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    unchanged=algorithm_snapshot()==snapshot and model_snapshot(args.models)==manifest['model_sha256']
    manifest['status']='screening_complete' if unchanged else 'invalid_algorithm_changed'
    manifest_path.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    if not unchanged:
        raise RuntimeError('Algorithm changed during screening; discard the validation claim')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    main()
