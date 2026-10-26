"""Explicit-run, invented business scenario for real scan -> OCR -> DOCX review."""
import argparse
from collections import Counter
from hashlib import sha256
import html
import json
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pymupdf as p

from app.ocr.word_export import convert_ocr_pdf_to_docx, W
from app.wordconvert import _convert_one

INK=(.13,.19,.24)
TEAL=(.04,.29,.34)
PALE=(.88,.94,.94)
GOLD=(1,.94,.75)
RED=(.78,.13,.15)
FONT='C:/Windows/Fonts/msyh.ttc'
BOLD='C:/Windows/Fonts/msyhbd.ttc'


def box(page,rect,text,size=10.5,bold=False,color=INK,align=0):
    result=page.insert_textbox(rect,text,fontname='bold' if bold else 'body',fontsize=size,
                               color=color,align=align,lineheight=1.5)
    if result<0:
        raise ValueError(f'Test fixture text does not fit: {text[:30]} ({result})')


def rect(page,area,fill,stroke=None,width=.6):
    page.draw_rect(area,color=stroke,fill=fill,width=width)


def heading(page,y,text):
    rect(page,(40,y+2,44,y+20),TEAL)
    box(page,(53,y,550,y+28),text,13,True)


def new_page(doc,index,title,subtitle):
    page=doc.new_page(width=595,height=842)
    page.insert_font(fontname='body',fontfile=FONT)
    page.insert_font(fontname='bold',fontfile=BOLD)
    rect(page,(30,28,565,101),TEAL)
    box(page,(46,39,550,76),title,21,True,(1,1,1))
    box(page,(47,76,550,97),subtitle,9,False,(.85,.94,.94))
    page.draw_line((40,786),(555,786),color=(.7,.76,.78),width=.5)
    box(page,(40,793,455,820),'场景模拟材料 · 所有单位、人员及业务信息均为虚构 · 不可用于实际交易',8)
    box(page,(495,793,555,820),f'第 {index} / 4 页',8,align=2)
    return page


def cell(page,area,text,*,fill=None,bold=False,size=10,align=0):
    rect(page,area,fill,(.48,.57,.61))
    x0,y0,x1,y1=area
    box(page,(x0+7,y0+6,x1-7,y1-3),text,size,bold,align=align)


def make_reference(path):
    with p.open() as doc:
        page=new_page(doc,1,'会议室设备采购立项审批单','模拟项目：青禾园区协作空间改造 / 项目编号 QH-2026-017')
        box(page,(42,118,288,143),'申请部门：行政运营部',11)
        box(page,(314,118,555,143),'申请日期：2026年10月23日',11)
        box(page,(42,148,288,172),'项目负责人：林知远（模拟）',11)
        box(page,(314,148,555,172),'预算上限：48,000.00 元',11,True)
        heading(page,190,'一、采购背景与实施目标')
        box(page,(42,224,550,309),
            '园区现有三间会议室存在声音回传不清晰、投屏连接步骤较多、设备型号不统一等问题。'
            '本次计划更新显示、拾音和会议控制设备，并完成网络调试与操作培训。\n'
            '项目验收以远程会议稳定、多人发言清晰、常用设备连接方便为主要目标。',11)
        rect(page,(40,324,555,379),GOLD)
        box(page,(51,334,544,375),'执行要求：保留原有线路；施工安排在非办公时段；所有配置需提供交接清单。',11,True)
        heading(page,400,'二、审批流转记录')
        cols=(40,130,245,455,555); rows=(434,470,520,570,620,670)
        for r,values in enumerate((('审批环节','经办人','审核意见','日期'),
                                  ('需求确认','林知远','已与三个使用部门确认需求','10月23日'),
                                  ('技术评审','陈书禾','接口兼容；安排现场连通测试','10月24日'),
                                  ('预算审核','沈明远','总价控制在预算范围内','10月25日'),
                                  ('采购批准','周安宁','同意采购，按验收结果付款','10月26日'))):
            for c,value in enumerate(values):
                cell(page,(cols[c],rows[r],cols[c+1],rows[r+1]),value,
                     fill=PALE if r==0 else None,bold=r==0,size=10)
        box(page,(42,699,550,760),'附件清单：设备报价明细、安装位置示意图、现场验收记录、结算确认单。\n备注：本文件仅用于检验图片型 PDF 的文字识别与 Word 版式还原。',10)

        page=new_page(doc,2,'设备采购报价与交付清单','报价编号 QH-BJ-2026-038 / 币种：人民币 / 报价有效期：30 天')
        box(page,(42,118,555,144),'供应单位：星澜设备服务中心（模拟）    交付地点：青禾园区 A 座',10.5)
        xs=(40,76,243,293,373,467,555)
        cell(page,(40,157,293,190),'设备及服务信息',fill=PALE,bold=True,align=1)
        cell(page,(293,157,555,190),'报价与交付安排',fill=PALE,bold=True,align=1)
        labels=('序号','名称与规格','数量','单价（元）','金额（元）','交付周期')
        for c,value in enumerate(labels):
            cell(page,(xs[c],190,xs[c+1],226),value,fill=PALE,bold=True,size=9)
        data=[('1','会议显示屏\n65英寸 / 4K / 壁挂','3','4,680.00','14,040.00','7个工作日'),
              ('2','全向麦克风\nUSB接口 / 回声消除','6','780.00','4,680.00','5个工作日'),
              ('3','会议摄像头\n1080P / 广角镜头','3','1,260.00','3,780.00','7个工作日'),
              ('4','无线投屏终端\n双频网络 / 多设备切换','3','890.00','2,670.00','5个工作日'),
              ('5','交换机\n8口千兆 / 桌面安装','3','320.00','960.00','5个工作日'),
              ('6','线材及辅料包\n网线、标签、固定配件','1','860.00','860.00','随设备交付'),
              ('7','安装调试与培训\n含3间会议室现场服务','1','3,600.00','3,600.00','2个工作日')]
        for r,values in enumerate(data):
            top=226+r*49
            for c,value in enumerate(values):
                cell(page,(xs[c],top,xs[c+1],top+49),value,fill=(.96,.98,.98) if r%2==0 else None,
                     size=9.5,align=2 if c in (3,4) else 0)
        cell(page,(40,569,373,605),'未税合计',fill=PALE,bold=True)
        cell(page,(373,569,555,605),'30,590.00 元',fill=PALE,bold=True,align=2)
        box(page,(43,624,550,650),'税率：13%    税额：3,976.70 元    含税总价：34,566.70 元',11,True)
        heading(page,668,'交付说明')
        box(page,(43,701,550,763),'1. 各项数量、单价和金额应逐项复核；替代型号须书面确认。\n2. 包装完好后再签收；安装前完成设备序列号登记。\n3. 本页数字为自动生成的模拟数据，不构成正式报价。',10)

        page=new_page(doc,3,'安装验收与现场记录','验收日期：2026年11月06日 / 验收区域：A101、A102、A103 会议室')
        heading(page,119,'一、会议室安装示意')
        # An embedded bitmap illustration is subsequently scanned with the page.
        with p.open() as drawing:
            diagram=drawing.new_page(width=480,height=190)
            rect(diagram,(8,8,472,182),(.94,.96,.98),(.6,.68,.72))
            rect(diagram,(170,23,310,50),(.12,.24,.34))
            rect(diagram,(130,75,350,145),(.73,.57,.38))
            for x in (150,210,270,330):
                for y in (65,155):
                    diagram.draw_circle((x,y),8,fill=(.38,.47,.57),color=None)
            diagram.draw_circle((240,110),9,fill=(.08,.55,.58),color=None)
            for end in ((240,50),(430,108)):
                diagram.draw_line((240,110),end,color=(.08,.55,.58),width=2,dashes='[4 3]')
            rect(diagram,(409,94,451,124),(.12,.24,.34))
            page.insert_image((42,157,552,359),stream=diagram.get_pixmap(dpi=144).tobytes('png'))
        box(page,(43,370,550,394),'图1：屏幕、会议桌、拾音设备与网络连接位置示意（模拟）',9.5,align=1)
        heading(page,413,'二、功能检查')
        rect(page,(40,449,290,610),(.94,.97,.97))
        box(page,(51,460,278,602),'显示与连接\n[√] 三台显示屏均能正常点亮\n[√] 笔记本有线投屏正常\n[√] 无线投屏可切换设备\n[ ] A102 备用网口标签待补',10.5)
        rect(page,(306,449,555,610),(.98,.96,.92))
        box(page,(317,460,544,602),'声音与培训\n[√] 远端发言清晰，无明显回声\n[√] 本地多人拾音可区分\n[√] 管理员已完成操作培训\n[ ] 会后关机提醒待张贴',10.5)
        heading(page,633,'三、问题闭环')
        box(page,(43,667,550,747),'发现问题：备用网口标签及关机提醒尚未张贴，不影响本次设备功能验收。\n整改责任人：陈书禾（模拟）    计划完成：2026年11月09日\n复核要求：补充现场照片，由行政运营部确认后归档。',10.5)

        page=new_page(doc,4,'结算与签署确认单','关联项目：QH-2026-017 / 关联报价：QH-BJ-2026-038')
        heading(page,121,'一、结算金额与付款节点')
        rect(page,(40,158,555,221),PALE)
        box(page,(53,169,545,211),'含税结算金额：34,566.70 元\n大写：叁万肆仟伍佰陆拾陆元柒角整',12,True)
        xs=(40,155,350,450,555)
        data=(('付款阶段','触发条件','比例','金额（元）'),
              ('预付款','采购订单确认后','30%','10,370.01'),
              ('验收款','功能验收并完成问题整改','60%','20,740.02'),
              ('质保款','质保期届满，无遗留问题','10%','3,456.67'))
        for r,values in enumerate(data):
            for c,value in enumerate(values):
                cell(page,(xs[c],246+r*42,xs[c+1],288+r*42),value,fill=PALE if r==0 else None,
                     bold=r==0,size=10,align=2 if c==3 else 0)
        heading(page,442,'二、归档及售后要求')
        box(page,(43,477,550,552),'设备保修期自验收次日起十二个月。交接资料包含配置清单、操作指引及维护联系人。\n付款前应核对订单、验收单与结算金额；本模拟文件中的单位和账号均不可用于付款。',10.5)
        box(page,(43,579,290,682),'使用单位：青禾园区管理中心（模拟）\n确认人：林知远\n确认日期：2026年11月09日',10.5)
        box(page,(315,579,555,682),'服务单位：星澜设备服务中心（模拟）\n经办人：陈书禾\n确认日期：2026年11月09日',10.5)
        # A deliberately overlapping test seal, explicitly marked invalid.
        page.draw_circle((450,666),49,color=RED,width=2.2)
        page.draw_circle((450,666),44,color=RED,width=.7)
        box(page,(407,637,493,660),'模拟验收章',11,True,RED,1)
        box(page,(405,665,495,688),'仅供测试',10,True,RED,1)
        box(page,(425,693,480,715),'无效',8,False,RED,1)
        page.draw_polyline([(98,703),(107,689),(116,708),(129,690),(138,704),(160,697),(168,708),(186,699)],
                           color=(.15,.23,.4),width=1.2)
        box(page,(43,726,550,759),'特别标记：签名线与印章均为程序绘制，用于检查图文交叠，不代表真实签署。',9)
        doc.subset_fonts()
        doc.save(path,garbage=4,deflate=True)


def normalize(text):
    return ''.join(c for c in text if not c.isspace())


def run(output):
    output.mkdir(parents=True,exist_ok=False)
    reference=output/'reference.pdf'; scan=output/'scanned-input.pdf'
    make_reference(reference)
    with p.open(reference) as ref,p.open() as scanned:
        for i,page in enumerate(ref):
            image=page.get_pixmap(dpi=220,alpha=False)
            target=scanned.new_page(width=page.rect.width,height=page.rect.height)
            target.insert_image(target.rect,stream=image.tobytes('jpeg',jpg_quality=92))
        scanned.save(scan,garbage=4,deflate=True)
    with p.open(scan) as doc:
        for i,page in enumerate(doc):
            page.get_pixmap(dpi=100).save(output/f'source-{i+1}.png')
        probes=[dict(page=i+1,extractable_characters=len(page.get_text().strip()),images=len(page.get_images()))
                for i,page in enumerate(doc)]
        assert all(item['extractable_characters']==0 and item['images']==1 for item in probes)
    before=sha256(scan.read_bytes()).hexdigest()
    print('Verified pure image input:',probes,flush=True)
    exported=output/'converted.docx'
    state,message,_=convert_ocr_pdf_to_docx(scan,exported,False,lambda s:print(s,flush=True),lambda:False,preserve_layout=True)
    print('DOCX:',state,message,flush=True)
    assert state=='success'
    rendered=output/'word-rendered.pdf'
    result=_convert_one(str(exported),str(rendered),False,lambda s:print(s,flush=True),lambda:False)
    print('Word render:',result,flush=True)
    assert result[0]=='success'
    assert sha256(scan.read_bytes()).hexdigest()==before
    with ZipFile(exported) as archive:
        root=ET.fromstring(archive.read('word/document.xml'))
        text=''.join(n.text or '' for n in root.iter(f'{{{W}}}t'))
        tables=len(list(root.iter(f'{{{W}}}tbl')))
        images=len([name for name in archive.namelist() if name.startswith('word/media/')])
    phrases=('QH-2026-017','48,000.00','30,590.00','3,976.70','34,566.70',
             '10,370.01','20,740.02','3,456.67','13%','青禾园区','仅供测试')
    report=dict(input=probes,source_unchanged=True,tables=tables,image_parts=images,conversion_message=message,
                key_checks={s:normalize(s) in normalize(text) for s in phrases},pages=[])
    with p.open(reference) as ref,p.open(rendered) as rendered_doc:
        report['input_pages']=len(ref); report['rendered_pages']=len(rendered_doc)
        for i,page in enumerate(rendered_doc):
            page.get_pixmap(dpi=100).save(output/f'result-{i+1}.png')
            expected=normalize(ref[i].get_text()) if i<len(ref) else ''
            actual=normalize(page.get_text())
            retained=sum((Counter(expected)&Counter(actual)).values())
            report['pages'].append(dict(page=i+1,expected_characters=len(expected),rendered_characters=len(actual),
                unordered_character_coverage=round(retained/max(1,len(expected)),4)))
            (output/f'result-{i+1}.txt').write_text(page.get_text(),encoding='utf-8')
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    cards=''.join(f'<section><h2>第 {i} 页</h2><div class="pair"><figure><figcaption>原始扫描件</figcaption><img src="source-{i}.png"></figure><figure><figcaption>Microsoft Word 实际渲染</figcaption><img src="result-{i}.png"></figure></div></section>' for i in range(1,5))
    summary=f"原稿 {report['input_pages']} 页 → Word 渲染 {report['rendered_pages']} 页 · 可编辑表格 {tables} 个 · 图片 {images} 张"
    checks=''.join(f'<tr><td>{html.escape(k)}</td><td>{"识别到" if v else "未完整识别"}</td></tr>' for k,v in report['key_checks'].items())
    (output/'comparison.html').write_text('<!doctype html><html lang="zh"><meta charset="utf-8"><title>扫描 PDF 转 Word 实际对比</title>'
        '<style>body{font:16px system-ui;margin:32px;background:#eef1f3;color:#172c34}h1{font-size:28px}section{margin:32px 0}.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}figure{margin:0}img{width:100%;background:white}figcaption{padding:12px;background:#fff;font-weight:600}table{border-collapse:collapse;background:white}td{padding:8px 24px;border:1px solid #ccd5d8}p{max-width:1000px;line-height:1.7}a{color:#00675e}</style>'
        f'<h1>图片型 PDF → Word：实际场景验证</h1><p>{summary}</p><p>全部业务内容为虚构。输入 PDF 每页只有一张图片、零个可提取字符；通过实际离线 OCR 和当前 Word 导出实现转换。以下为 Microsoft Word 16.0 导出的 PDF 图片，不能替代其他 DOCX 预览器的兼容性检查。</p>'
        '<p><a href="scanned-input.pdf">扫描原稿</a> · <a href="converted.docx">可编辑 Word</a> · <a href="word-rendered.pdf">Word 渲染 PDF</a></p>'
        f'<h2>关键内容检查</h2><table>{checks}</table><p>字符覆盖率仅用于辅助定位问题，忽略顺序，不代表识别准确率或版式一致率。请重点对比表格、标题、色块、印章及文字位置。</p>{cards}</html>',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    run(args.output.resolve())
