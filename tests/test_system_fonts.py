"""Font replacement checks use installed fonts and generated PDFs only."""
import sys

import pymupdf as p
import pytest

from app.pdfedits import apply_edits, prepare_replacement, snapshot_page
from app.systemfonts import font_face, installed_font_buffer


@pytest.mark.parametrize('name,flags,expected', [
    ('ABCDEF+Arial-BoldItalicMT', 0, ('Arial', True, True)),
    ('MicrosoftYaHei', 16, ('Microsoft YaHei', True, False)),
    ('TimesNewRomanPS-ItalicMT', 0, ('Times New Roman', False, True)),
    ('宋体', 0, ('SimSun', False, False)),
])
def test_font_face_keeps_family_and_style(name, flags, expected):
    assert font_face(name, flags) == expected


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows installed font lookup')
def test_missing_installed_font_does_not_accept_windows_substitution():
    assert installed_font_buffer('ZhiXuMissingFontForTest') is None


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows installed font lookup')
@pytest.mark.parametrize('name,original,updated', [
    ('SimSun', '原文标题测试', '新增功能说明'),
    ('MicrosoftYaHei-Bold', '原文标题测试', '新增功能说明'),
    ('Arial-ItalicMT', 'ABCDE', 'WXYZ'),
])
def test_subset_replacement_matches_full_installed_font(tmp_path, name, original, updated):
    buffer = installed_font_buffer(name)
    if buffer is None:
        pytest.skip(f'Exact installed font unavailable: {name}')
    font = p.Font(fontbuffer=buffer)
    source = tmp_path / 'subset.pdf'
    with p.open() as doc:
        page = doc.new_page(width=600, height=400)
        page.insert_font(fontname='Original', fontbuffer=buffer)
        page.insert_text((60, 100), original, fontname='Original', fontsize=24)
        page.insert_text((60, 180), 'REFERENCE', fontname='Original', fontsize=18)
        doc.subset_fonts()
        doc.save(source, garbage=4, deflate=True)
    before = source.read_bytes()
    with p.open(source) as doc:
        page = doc[0]
        raw = snapshot_page(page)['raw']
        line = raw['blocks'][0]['lines'][0]
        preferred = line['spans'][0]['font']
        extracted = doc.extract_font(page.get_fonts()[0][0])[3]
        subset = p.Font(fontbuffer=extracted)
        assert not all(subset.has_glyph(ord(char)) for char in updated)
        edit = prepare_replacement(page, line['bbox'], updated)
        assert not dict(edit.details)['font_fallback']
        assert dict(edit.details)['font_size'] == pytest.approx(24, abs=.01)
        apply_edits(page, [edit])
        assert updated in page.get_text() and original not in page.get_text()
        assert 'REFERENCE' in page.get_text()
        trace = page.get_texttrace()[-1]
        assert font_face(trace['font']) == font_face(preferred)
        actual = page.get_pixmap().samples
        with p.open() as expected:
            full = expected.new_page(width=600, height=400)
            full.insert_font(fontname='Full', fontbuffer=buffer)
            full.insert_text((60, 100), updated, fontname='Full', fontsize=24)
            full.insert_text((60, 180), 'REFERENCE', fontname='Full', fontsize=18)
            assert actual == full.get_pixmap().samples
    assert source.read_bytes() == before
