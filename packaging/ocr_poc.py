"""Real-model PoC against generated documents; does not read office files."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pymupdf as p
from app.ocr.contracts import OCROptions
from app.ocr.service import run_ocr


def make_samples(path):
    with p.open() as doc:
        for index in range(9):
            with p.open() as original:
                page = original.new_page(width=595, height=842)
                # Embed proportional CJK/Latin glyphs, like an office document;
                # the PDF built-in china-s font spaces Latin digits unnaturally.
                page.insert_font(fontname='SampleCJK', fontbuffer=p.Font('cjk').buffer)
                page.insert_text((70, 120), '纸序文字识别验证', fontname='SampleCJK', fontsize=20)
                page.insert_text((70, 170), '采购合同 Contract ABC123', fontname='SampleCJK', fontsize=16)
                page.insert_text((70, 220), '含税金额 12345.67 元', fontname='SampleCJK', fontsize=16)
                page.insert_text((70, 270), '折扣 5% 编号 ZX-2026', fontname='SampleCJK', fontsize=16)
                if index in (0,8):
                    for row,value in enumerate(('¥827','¥5326','¥1039')):
                        page.insert_text((70,400+row*50),value,fontname='SampleCJK',fontsize=16)
                if index == 8:
                    page.set_rotation(90)
                scan = page.get_pixmap(dpi=72 if index == 4 else 200)
            target = doc.new_page(width=842 if index == 8 else 595, height=595 if index == 8 else 842)
            target.insert_image(target.rect, stream=scan.tobytes('png'))
            if index == 5:
                target.insert_text((70, 330), 'Electronic page with existing searchable text', fontsize=14)
                target.insert_text((70, 360), 'An ordinary electronic document does not need OCR again.', fontsize=14)
            if index in (3, 6, 7):
                target.set_rotation({3: 90, 6: 180, 7: 270}[index])
            if index == 2:
                target.set_cropbox(p.Rect(20, 30, 575, 812))
        doc.set_toc([[1, '合成 OCR 样例', 1]])
        doc.set_page_labels([{'startpage': 0, 'prefix': 'S-', 'style': 'D'}])
        doc[0].add_text_annot((40, 40), 'Synthetic annotation')
        doc[0].insert_link({'kind': p.LINK_URI, 'from': p.Rect(40, 400, 180, 420), 'uri': 'https://example.com'})
        doc.save(path)


def ui_search(path):
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QThreadPool
    from PyQt6.QtTest import QTest
    from app.ui import MainWindow
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        window.ws.open_replace(window.store.load(str(path)), str(path))
        window.refresh()
        window.open_search()
        window.search_bar.input.setText('文字识别')
        window.search_bar.start()
        deadline = monotonic() + 20
        while window.search_bar.busy and monotonic() < deadline:
            QTest.qWait(10)
        assert not window.search_bar.busy and window.search_bar.results
        return len(window.search_bar.results)
    finally:
        window.search_bar.cancel()
        QThreadPool.globalInstance().waitForDone(30000)
        window.ws.reset()
        window.store.close_all()
        window.close()
        app.processEvents()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    source, output = args.output / 'synthetic.pdf', args.output / 'synthetic_OCR.pdf'
    if source.exists() or output.exists():
        raise ValueError('Choose a new PoC directory; existing artifacts are never overwritten')
    make_samples(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    events = []
    started = monotonic()
    result = run_ocr(source, output, range(9), OCROptions(runtime_dir=str(args.runtime)),
                     lambda current, total, message: events.append((monotonic() - started, current, total, message)))
    seconds = monotonic() - started
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert not result.failed_pages, result.failed_pages
    with p.open(source) as original, p.open(output) as recognized:
        for index in range(9):
            if index != 5:
                text = recognized[index].get_text()
                assert '文字识别' in text, (index, text)
                assert recognized[index].search_for('12345.67'), (index, text)
                if index in (0,8):
                    for amount in ('827','5326','1039'):
                        assert recognized[index].search_for(amount), (index,text)
            assert original[index].get_pixmap(dpi=200).samples == recognized[index].get_pixmap(dpi=200).samples
            assert tuple(original[index].rect) == tuple(recognized[index].rect)
            assert original[index].rotation == recognized[index].rotation
        assert original.get_toc() == recognized.get_toc()
        assert original.get_page_labels() == recognized.get_page_labels()
    hits = ui_search(output)
    report = dict(status='GO for synthetic MVP; real-office acceptance pending',
                  seconds=round(seconds, 3), processed=result.processed_pages,
                  skipped=result.skipped_pages, failed=result.failed_pages,
                  source_bytes=source.stat().st_size, output_bytes=output.stat().st_size,
                  existing_ui_search_hits=hits, events=events)
    (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'events'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
