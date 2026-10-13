"""Focused geometry regressions; no inference models or business files needed."""
from types import SimpleNamespace

import pymupdf as p

from app.ocr.contracts import OCRLine
from app.ocr.pdf_export import add_text_layer
from app.ocr.worker import positioned_words, recognize
from app.pdf2excel.raster_grid import _thin_segments


def test_interleaved_stroke_does_not_split_a_continuing_rule():
    segments=[(0,0,120),(1,0,120),(1,160,200),(2,0,120),
              (3,0,120),(4,0,120),(5,0,120),(6,0,120)]
    rules=_thin_segments(segments)
    assert [(start,end) for _,start,end in rules if start==0]==[(0,120)]


def test_overlapping_rule_fragments_join_without_joining_distant_rules():
    rules=_thin_segments([(0,0,70),(0,100,180),(2,50,150),(2,240,300)])
    assert sorted((start,end) for _,start,end in rules)==[(0,180),(240,300)]


def tokens():
    bounds=[(10,20,16,35),(16,20,22,35),(120,20,126,35),
            (126,20,132,35),(132,20,138,35)]
    return [(text,.99,[(x0,y0),(x1,y0),(x1,y1),(x0,y1)])
            for text,(x0,y0,x1,y1) in zip('AB009',bounds)]


def test_word_positions_keep_column_gaps_and_reject_incomplete_text():
    line=dict(text='AB 009',bbox=[10,20,138,35],polygon=[10,20,138,20,138,35,10,35],
              confidence=.98,page_index=0)
    words=positioned_words(line,tokens())
    assert ''.join(word['text'] for word in words)=='AB009'
    assert words[1]['bbox'][2]==22 and words[2]['bbox'][0]==120
    assert all(word['word_box'] for word in words)
    assert positioned_words(line,tokens()[:-1])==[]


def test_fine_positions_require_a_host_that_understands_word_boxes():
    class Engine:
        word_box_directions=['0']
        def __call__(self, image, **options):
            return SimpleNamespace(boxes=[[(10,20),(138,20),(138,35),(10,35)]],
                                   txts=['AB 009'],scores=[.98],word_results=[tokens()])
    assert [line['text'] for line in recognize(Engine(),'unused.png',0)]==['AB 009']
    assert [line['text'] for line in recognize(Engine(),'unused.png',0,word_boxes=True)]==list('AB009')
    engine=Engine(); engine.word_box_directions=['180']
    assert [line['text'] for line in recognize(engine,'unused.png',0,word_boxes=True)]==['AB 009']


def test_narrow_digit_word_box_keeps_a_horizontal_baseline():
    with p.open() as doc:
        page=doc.new_page(width=120,height=80)
        polygon=(60,80,68,80,68,110,60,110)
        line=OCRLine('1',(60,80,68,110),polygon,.99,0,word_box=True)
        before=page.get_pixmap(dpi=200)
        add_text_layer(page,[line],before)
        direction=page.get_text('dict')['blocks'][0]['lines'][0]['dir']
        assert abs(direction[0]-1)<.01 and abs(direction[1])<.01
        assert page.search_for('1')
        assert page.get_pixmap(dpi=200).samples==before.samples
