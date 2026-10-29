"""Mixed-style corrections through the public store, using generated PDFs only."""
import pymupdf as p
import pytest

from app.pdfio import PdfStore


def make_mixed_pdf(path):
    with p.open() as doc:
        page = doc.new_page(width=600, height=400)
        x = 60
        for text, font, size, color in (
            ('Due ', 'helv', 12, (0, 0, 0)),
            ('2020', 'hebo', 16, (1, 0, 0)),
            (' now', 'heit', 10, (0, 0, 1)),
        ):
            page.insert_text((x, 100), text, fontname=font, fontsize=size, color=color)
            x += p.Font(fontname=font).text_length(text, fontsize=size)
        page.insert_text((60, 160), 'KEEP', fontsize=12)
        doc.save(path)


def test_mixed_style_correction_keeps_each_style_and_source(tmp_path):
    source, output = tmp_path / 'mixed.pdf', tmp_path / 'corrected.pdf'
    make_mixed_pdf(source)
    original = source.read_bytes()
    store = PdfStore()
    try:
        ref = store.load(str(source))[0]
        with p.open(source) as doc:
            line = doc[0].get_text('dict')['blocks'][0]['lines'][0]
            assert ''.join(span['text'] for span in line['spans']) == 'Due 2020 now'
            area = line['bbox']
        edit = store.prepare_replacement(ref, area, 'Due 2026 now')
        store.write([ref.edited(edit)], str(output))
        with p.open(output) as doc:
            lines = [line for block in doc[0].get_text('dict')['blocks'] for line in block['lines']]
            corrected = next(line for line in lines if ''.join(s['text'] for s in line['spans']) == 'Due 2026 now')
            assert [(s['text'], s['font'], s['size'], s['color']) for s in corrected['spans']] == [
                ('Due ', 'Helvetica', 12, 0),
                ('2026', 'Helvetica-Bold', 16, 0xFF0000),
                (' now', 'Helvetica-Oblique', 10, 0x0000FF),
            ]
            assert all(s['origin'][1] == pytest.approx(100) for s in corrected['spans'])
            assert 'KEEP' in doc[0].get_text() and '2020' not in doc[0].get_text()
        assert source.read_bytes() == original
    finally:
        store.close_all()


def test_mixed_chinese_and_latin_correction_keeps_embedded_styles(tmp_path):
    from app.pdfedits import cjk_font_buffer
    source, output = tmp_path / 'chinese.pdf', tmp_path / 'corrected.pdf'
    with p.open() as doc:
        page = doc.new_page(width=600, height=400)
        page.insert_font(fontname='SourceCJK', fontbuffer=cjk_font_buffer())
        x = 60
        for text, font, size, color in (
            ('项目 ', 'SourceCJK', 12, (0, 0, 0)),
            ('2020', 'hebo', 16, (1, 0, 0)),
            (' 合计', 'SourceCJK', 12, (0, 0, 1)),
        ):
            page.insert_text((x, 100), text, fontname=font, fontsize=size, color=color)
            face = p.Font('cjk') if font == 'SourceCJK' else p.Font(font)
            x += face.text_length(text, fontsize=size)
        doc.subset_fonts()
        doc.save(source)
    store = PdfStore()
    try:
        ref = store.load(str(source))[0]
        area = store.text_snapshot(ref)['raw']['blocks'][0]['lines'][0]['bbox']
        edit = store.prepare_replacement(ref, area, '项目 2026 总计')
        store.write([ref.edited(edit)], str(output))
        with p.open(output) as doc:
            spans = doc[0].get_text('dict')['blocks'][0]['lines'][0]['spans']
            assert [(s['text'], s['size'], s['color']) for s in spans] == [
                ('项目 ', 12, 0), ('2026', 16, 0xFF0000), (' 总计', 12, 0x0000FF),
            ]
    finally:
        store.close_all()


@pytest.mark.parametrize('updated,expected', [
    ('Due X2020 now', [('Due X', 'Helvetica'), ('2020', 'Helvetica-Bold'), (' now', 'Helvetica-Oblique')]),
    ('XDue 2020 now!', [('XDue ', 'Helvetica'), ('2020', 'Helvetica-Bold'), (' now!', 'Helvetica-Oblique')]),
    ('Due  now', [('Due ', 'Helvetica'), (' now', 'Helvetica-Oblique')]),
    ('2020 now', [('2020', 'Helvetica-Bold'), (' now', 'Helvetica-Oblique')]),
    ('ABCXYZ', [('AB', 'Helvetica'), ('CX', 'Helvetica-Bold'), ('YZ', 'Helvetica-Oblique')]),
])
def test_mixed_style_insertions_and_deletions_follow_source_styles(tmp_path, updated, expected):
    source, output = tmp_path / 'mixed.pdf', tmp_path / 'corrected.pdf'
    make_mixed_pdf(source)
    store = PdfStore()
    try:
        ref = store.load(str(source))[0]
        area = store.text_snapshot(ref)['raw']['blocks'][0]['lines'][0]['bbox']
        edit = store.prepare_replacement(ref, area, updated)
        store.write([ref.edited(edit)], str(output))
        with p.open(output) as doc:
            written = [t for t in doc[0].get_texttrace() if t['chars'][0][2][1] == pytest.approx(100)]
            assert [(''.join(chr(c[0]) for c in t['chars']), t['font']) for t in written] == expected
    finally:
        store.close_all()


@pytest.mark.parametrize('failed_write', [2, 5])
def test_partial_mixed_style_write_failure_preserves_files(tmp_path, monkeypatch, failed_write):
    from app.pdfio import PdfError
    source, output = tmp_path / 'mixed.pdf', tmp_path / 'existing.pdf'
    make_mixed_pdf(source)
    original = source.read_bytes()
    output.write_bytes(original)
    store = PdfStore()
    try:
        ref = store.load(str(source))[0]
        area = store.text_snapshot(ref)['raw']['blocks'][0]['lines'][0]['bbox']
        edit = store.prepare_replacement(ref, area, 'Due 2026 now')
        insert_text, attempts = p.Page.insert_text, 0

        def fail_one_write(page, *args, **kwargs):
            nonlocal attempts
            attempts += 1
            return 0 if attempts == failed_write else insert_text(page, *args, **kwargs)

        monkeypatch.setattr(p.Page, 'insert_text', fail_one_write)
        with pytest.raises(PdfError, match='新文字未完整写入'):
            store.write([ref.edited(edit)], str(output))
        assert source.read_bytes() == original and output.read_bytes() == original
        assert not list(tmp_path.glob('.pdfasm-*.pdf'))
    finally:
        store.close_all()


def test_embedded_mixed_fonts_accept_normal_spaces(tmp_path):
    from app.systemfonts import font_face, installed_font_buffer
    buffer = installed_font_buffer('Arial')
    if buffer is None:
        pytest.skip('Exact Arial font unavailable')
    source, output = tmp_path / 'embedded.pdf', tmp_path / 'corrected.pdf'
    with p.open() as doc:
        page = doc.new_page(width=600, height=400)
        page.insert_font(fontname='SourceArial', fontbuffer=buffer)
        page.insert_text((60, 100), 'Total ', fontname='SourceArial', fontsize=12)
        x = 60 + p.Font(fontbuffer=buffer).text_length('Total ', fontsize=12)
        page.insert_text((x, 100), '2020', fontname='hebo', fontsize=16, color=(1, 0, 0))
        doc.save(source)
    store = PdfStore()
    try:
        ref = store.load(str(source))[0]
        area = store.text_snapshot(ref)['raw']['blocks'][0]['lines'][0]['bbox']
        edit = store.prepare_replacement(ref, area, 'Total 2026')
        store.write([ref.edited(edit)], str(output))
        with p.open(output) as doc:
            spans = doc[0].get_text('dict')['blocks'][0]['lines'][0]['spans']
            assert ''.join(s['text'] for s in spans).replace('\N{NO-BREAK SPACE}', ' ') == 'Total 2026'
            assert font_face(spans[0]['font']) == ('Arial', False, False)
            assert spans[-1]['color'] == 0xFF0000
    finally:
        store.close_all()


def test_mixed_style_correction_fits_before_neighbor_and_refuses_overflow(tmp_path):
    source, output = tmp_path / 'mixed.pdf', tmp_path / 'corrected.pdf'
    make_mixed_pdf(source)
    with p.open(source) as doc:
        doc[0].insert_text((170, 100), 'RIGHT', fontsize=12)
        doc.saveIncr()
    original = source.read_bytes()
    store = PdfStore()
    try:
        ref = store.load(str(source))[0]
        area = (60, 82, 143, 105)
        edit = store.prepare_replacement(ref, area, 'Due 20262026 now')
        store.write([ref.edited(edit)], str(output))
        with p.open(output) as doc:
            traces = doc[0].get_texttrace()
            neighbor = next(t for t in traces if ''.join(chr(c[0]) for c in t['chars']) == 'RIGHT')
            written = traces[-3:]
            assert [t['size'] / size for t, size in zip(written, (12, 16, 10))] == pytest.approx([.90962] * 3, abs=.001)
            assert max(t['bbox'][2] for t in written) <= 167.1
            assert neighbor['chars'][0][2] == pytest.approx((170, 100))
        with pytest.raises(ValueError, match='宽度'):
            store.prepare_replacement(ref, area, 'Due 2026202620262026 now')
        assert source.read_bytes() == original
    finally:
        store.close_all()


def test_mixed_style_correction_can_be_edited_again_before_saving(tmp_path):
    source, output = tmp_path / 'mixed.pdf', tmp_path / 'corrected.pdf'
    make_mixed_pdf(source)
    store = PdfStore()
    try:
        ref = store.load(str(source))[0]
        area = store.text_snapshot(ref)['raw']['blocks'][0]['lines'][0]['bbox']
        ref = ref.edited(store.prepare_replacement(ref, area, 'Due 2026 now'))
        ref = ref.edited(store.prepare_replacement(ref, area, 'Due 2027 later'))
        store.write([ref], str(output))
        with p.open(output) as doc:
            traces = doc[0].get_texttrace()[-3:]
            assert [(''.join(chr(c[0]) for c in t['chars']), t['color']) for t in traces] == [
                ('Due ', (0, 0, 0)), ('2027', (1, 0, 0)), (' later', (0, 0, 1)),
            ]
            assert 'KEEP' in doc[0].get_text()
    finally:
        store.close_all()


def test_user_can_choose_installed_font_size_emphasis_and_color(tmp_path):
    from app.systemfonts import font_face, installed_font_buffer
    if installed_font_buffer('Arial', 18) is None:
        pytest.skip('Exact Arial bold italic font unavailable')
    source, output = tmp_path / 'mixed.pdf', tmp_path / 'styled.pdf'
    make_mixed_pdf(source)
    original = source.read_bytes()
    store = PdfStore()
    try:
        ref = store.load(str(source))[0]
        area = store.text_snapshot(ref)['raw']['blocks'][0]['lines'][0]['bbox']
        edit = store.prepare_replacement(ref, area, 'Due 2026 now', style={
            'font': 'Arial', 'fontsize': 24, 'bold': True, 'italic': True, 'color': (0, .5, 0),
        })
        store.write([ref.edited(edit)], str(output))
        with p.open(output) as doc:
            written = [t for t in doc[0].get_texttrace() if t['chars'][0][2][1] == pytest.approx(100)]
            assert ''.join(chr(c[0]) for t in written for c in t['chars']).replace('\u00a0', ' ') == 'Due 2026 now'
            assert all(font_face(t['font']) == ('Arial', True, True) for t in written)
            assert all(t['size'] == pytest.approx(24) and t['color'] == (0, .5, 0) for t in written)
            assert 'KEEP' in doc[0].get_text()
        assert source.read_bytes() == original
    finally:
        store.close_all()


@pytest.mark.parametrize('style,expected', [
    ({'italic': False}, ('Arial', True, False)),
    ({'bold': False, 'italic': False}, ('Arial', False, False)),
])
def test_user_can_turn_off_emphasis_on_embedded_bold_italic_text(tmp_path, style, expected):
    from app.systemfonts import font_face, installed_font_buffer
    buffer = installed_font_buffer('Arial', 18)
    if buffer is None:
        pytest.skip('Exact Arial bold italic font unavailable')
    source, output = tmp_path / 'italic.pdf', tmp_path / 'bold.pdf'
    with p.open() as doc:
        page = doc.new_page(width=600, height=400)
        page.insert_font(fontname='SourceArial', fontbuffer=buffer)
        page.insert_text((60, 100), 'ALPHA', fontname='SourceArial', fontsize=18)
        doc.save(source)
    store = PdfStore()
    try:
        ref = store.load(str(source))[0]
        area = store.text_snapshot(ref)['raw']['blocks'][0]['lines'][0]['bbox']
        edit = store.prepare_replacement(ref, area, 'ALPHA', style=style)
        store.write([ref.edited(edit)], str(output))
        with p.open(output) as doc:
            assert font_face(doc[0].get_texttrace()[0]['font']) == expected
    finally:
        store.close_all()


def test_changing_only_color_keeps_original_mixed_faces_and_sizes(tmp_path):
    source, output = tmp_path / 'mixed.pdf', tmp_path / 'colored.pdf'
    make_mixed_pdf(source)
    store = PdfStore()
    try:
        ref = store.load(str(source))[0]
        area = store.text_snapshot(ref)['raw']['blocks'][0]['lines'][0]['bbox']
        edit = store.prepare_replacement(ref, area, 'Due 2020 now', style={'color': (0, .5, 0)})
        store.write([ref.edited(edit)], str(output))
        with p.open(output) as doc:
            written = doc[0].get_texttrace()[-3:]
            assert [(t['font'], t['size'], t['color']) for t in written] == [
                ('Helvetica', 12, (0, .5, 0)), ('Helvetica-Bold', 16, (0, .5, 0)),
                ('Helvetica-Oblique', 10, (0, .5, 0)),
            ]
        with pytest.raises(ValueError, match='页面范围|重叠|宽度'):
            store.prepare_replacement(ref, area, 'Due 2020 now', style={'fontsize': 144})
    finally:
        store.close_all()
