"""Opt-in real Word rendering of generated samples, never customer documents."""
import os
from collections import Counter

import pymupdf as p
import pytest

from app.wordconvert import convert_batch, word_available


def create_sample(path):
    with p.open() as doc:
        page=doc.new_page(width=595,height=842)
        page.draw_rect((30,30,565,110),fill=(.08,.26,.43),color=None)
        page.insert_text((50,79),'Quarterly Review',fontname='hebo',fontsize=26,color=(1,1,1))
        page.insert_text((50,152),'Project summary',fontname='hebo',fontsize=17)
        page.insert_text((50,185),'Regular text and ',fontsize=12)
        x=50+p.get_text_length('Regular text and ',fontsize=12)
        page.insert_text((x,185),'coloured emphasis',fontname='hebi',fontsize=12,color=(.8,.1,.1))
        page.insert_text((50,215),'This editable line stays below the heading.',fontsize=12)
        page.draw_line((50,240),(545,240),color=(.1,.6,.5),width=1)
        page.draw_rect((370,290,535,355),fill=(.95,.75,.2),color=None)
        page.insert_text((385,325),'Colour panel',fontname='hebo',fontsize=14)
        page.draw_circle((460,440),38,color=(.8,.1,.1),width=3)
        page.insert_text((50,790),'Generated sample - no business data',fontsize=9)

        page=doc.new_page(width=595,height=842)
        page.insert_text((50,65),'Table and merged cells',fontname='hebo',fontsize=22)
        page.draw_rect((50,110,545,150),fill=(.8,.9,.95),color=None)
        for y in (110,150,200,250,300):
            page.draw_line((50,y),(545,y),color=(.2,.2,.2),width=.5)
        for x in (50,545):
            page.draw_line((x,110),(x,300),color=(.2,.2,.2),width=.5)
        for x in (230,390):
            page.draw_line((x,150),(x,300),color=(.2,.2,.2),width=.5)
        page.insert_text((65,135),'Merged heading',fontname='hebo',fontsize=12)
        for row,values in enumerate((('Item','Count','Amount'),('Alpha','3','120.00'),('Beta','7','980.50'))):
            for x,text in zip((65,245,405),values):
                page.insert_text((x,180+row*50),text,fontsize=12)
                if text=='Alpha':
                    page.insert_text((x+p.get_text_length(text,fontsize=12),180+row*50),
                                     ' item',fontname='heit',fontsize=12,color=(.8,.1,.1))
        page.insert_text((50,350),'Text below the table remains editable.',fontsize=12)

        page=doc.new_page(width=842,height=595)
        page.insert_text((45,60),'Landscape columns',fontname='hebo',fontsize=22)
        for row in range(6):
            page.insert_text((45,130+row*32),f'Left column paragraph {row+1}.',fontsize=12)
            page.insert_text((440,130+row*32),f'Right column paragraph {row+1}.',fontsize=12)
        page.insert_text((45,420),'中文标题与正文',fontname='china-s',fontsize=18)
        page.insert_text((45,460),'这是一份自动生成的版式验证样例。',fontname='china-s',fontsize=12)
        doc.save(path)


@pytest.mark.skipif(os.environ.get('ZHIXU_WORD_FIDELITY_OFFICE')!='1',reason='Requires explicit Office sample validation')
def test_generated_pages_render_in_real_word(tmp_path):
    assert word_available(),'Word must be installed for the opt-in rendering check'
    source=tmp_path/'source.pdf'; exported=tmp_path/'exported.docx'; rendered=tmp_path/'rendered.pdf'
    create_sample(source)
    original=source.read_bytes()
    def report(*args):
        print(args[-1],flush=True)
    results,cancelled,_=convert_batch([(str(source),str(exported))],False,report,lambda:False,direction='pdf_to_word')
    assert results[0]['state']=='success',results
    assert not cancelled
    results,cancelled,version=convert_batch([(str(exported),str(rendered))],False,report,lambda:False)
    assert results[0]['state']=='success',results
    assert not cancelled and source.read_bytes()==original
    print('Office version:',version,'Artifacts:',tmp_path,flush=True)
    with p.open(source) as before,p.open(rendered) as after:
        for index,page in enumerate(before):
            page.get_pixmap(dpi=110).save(tmp_path/f'source-{index+1}.png')
        for index,page in enumerate(after):
            page.get_pixmap(dpi=110).save(tmp_path/f'rendered-{index+1}.png')
        assert len(after)==len(before)
        counts=lambda text: Counter(char for char in text if not char.isspace())
        for index,(first,second) in enumerate(zip(before,after)):
            assert abs(first.rect.width-second.rect.width)<1
            assert abs(first.rect.height-second.rect.height)<1
            assert counts(first.get_text())==counts(second.get_text()),f'Page {index+1} text mismatch'
        # Check the empty area above the banner, its colour and the gold panel.
        first=before[0].get_pixmap(); second=after[0].get_pixmap()
        for x,y in ((100,20),(35,40),(530,95),(380,300),(520,345)):
            assert max(abs(a-b) for a,b in zip(first.pixel(x,y),second.pixel(x,y)))<=8,(x,y)
        for index,labels in enumerate((('Quarterly Review','Project summary','Colour panel'),
                                       ('Table and merged cells','Merged heading','980.50'),
                                       ('Landscape columns','Left column paragraph 1.','中文标题与正文'))):
            for label in labels:
                first=before[index].search_for(label)[0]
                second=after[index].search_for(label)[0]
                drift=max(abs(a-b) for a,b in zip(first,second))
                print(f'{label}: max bbox drift {drift:.2f} pt',flush=True)
                assert drift<8,(label,first,second)
