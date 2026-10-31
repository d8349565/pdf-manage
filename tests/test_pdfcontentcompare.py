"""Content outcomes against independently authored PDF source items."""
import os
from pathlib import Path

import pytest
import pymupdf as pdf

from app.pdfcontentcompare import compare_content_files
from app.output import OperationCancelled
from pdfcompare_acceptance_samples import NORMAL, REFLOW, paragraph, render, scan, scenarios


@pytest.fixture(scope='module')
def documents(tmp_path_factory):
    root = tmp_path_factory.mktemp('content-comparison')
    cases = {}
    for case in scenarios():
        if case['id'] not in [f'S{i:02}' for i in range(1, 12)]:
            continue
        folder = root / case['id']; folder.mkdir()
        render(folder / 'old.pdf', case['old'], NORMAL)
        render(folder / 'new.pdf', case['new'], REFLOW if case.get('reflow') else NORMAL)
        cases[case['id']] = folder
    return cases


def run(documents, code):
    folder = documents[code]
    before = [path.read_bytes() for path in (folder / 'old.pdf', folder / 'new.pdf')]
    result = compare_content_files(folder / 'old.pdf', folder / 'new.pdf')
    assert before == [path.read_bytes() for path in (folder / 'old.pdf', folder / 'new.pdf')]
    return result


@pytest.mark.parametrize('code', ['S01', 'S02', 'S06'])
def test_identical_text_reflow_and_cross_page_paragraph_have_no_content_changes(documents, code):
    result = run(documents, code)
    assert result['complete']
    assert result['changes'] == []


def test_reflow_preserves_amount_decimal_negation_and_unit_changes(documents):
    result = run(documents, 'S03')
    assert result['counts'] == {'modified': 4}
    assert {(c['before'], c['after']) for c in result['changes']} == {
        ('120000元', '125000元'), ('0.5%', '5%'), ('不得', '可以'), ('100米', '100件')}
    assert all(c['old'] and c['new'] and c['old_context'] and c['new_context'] for c in result['changes'])
    unit = next(c for c in result['changes'] if c['before'] == '100米')
    assert unit['old'][0]['page'] == 1 and unit['new'][0]['page'] == 2


@pytest.mark.parametrize('code,expected', [('S04', {'moved': 1}), ('S05', {'moved': 1, 'modified': 1})])
def test_clause_move_is_not_a_delete_add_pair(documents, code, expected):
    result = run(documents, code)
    assert result['counts'] == expected
    moved = next(c for c in result['changes'] if c['kind'] == 'moved')
    assert moved['heading'] == '付款条件'
    assert moved['old'][0]['page'] != moved['new'][0]['page']


def test_renumbered_clauses_do_not_hide_real_additions_and_deletions(documents):
    result = run(documents, 'S07')
    assert result['counts'] == {'added': 1, 'deleted': 1}
    assert {c['heading'] for c in result['changes']} == {'新增培训', '售后服务'}


def test_repeated_clause_uses_section_and_ambiguous_duplicates_require_review(documents):
    result = run(documents, 'S08')
    assert result['counts'] == {'modified': 1}
    assert result['changes'][0]['heading'] == '第二标段付款'
    assert result['changes'][0]['old'][0]['page'] == 2
    ambiguous = run(documents, 'S09')
    assert ambiguous['counts'] == {'ambiguous': 1}
    assert {r['page'] for r in ambiguous['changes'][0]['old']} == {1, 2}
    assert {r['page'] for r in ambiguous['changes'][0]['new']} == {2}


def test_table_matches_row_name_and_column_name_after_reordering(documents):
    result = run(documents, 'S10')
    assert result['counts'] == {'modified': 2}
    assert {(c['heading'], c['before'], c['after']) for c in result['changes']} == {
        ('设备清单 · 设备乙 · 数量', '10', '12'), ('设备清单 · 设备乙 · 单位', '套', '台')}


def test_picture_resize_is_distinct_from_picture_content_change(documents):
    result = run(documents, 'S11')
    assert result['counts'] == {'image_modified': 1}
    assert result['changes'][0]['heading'] == '标记示意图'
    assert len(result['layout_notes']) == 1


@pytest.mark.parametrize('code', ['S12', 'S13', 'S14'])
def test_actual_optional_ocr_filters_scan_noise_and_preserves_amount_change(tmp_path, code):
    runtime = os.environ.get('ZHIXU_TEST_OCR_RUNTIME')
    if not runtime:
        pytest.skip('Set ZHIXU_TEST_OCR_RUNTIME to test the existing optional component')
    case = next(c for c in scenarios() if c['id'] == code)
    for side in ('old', 'new'):
        path = tmp_path / (side + '.pdf')
        render(path, case[side], REFLOW if side == 'new' and case.get('reflow') else NORMAL)
        scan(path, dpi=120 if side == 'new' else 144, noisy=side == 'new', mixed=case.get('mixed', False))
    result = compare_content_files(tmp_path / 'old.pdf', tmp_path / 'new.pdf',
        ocr_runtime=Path(runtime), cache_dir=tmp_path / 'ocr-cache')
    assert result['complete'] and result['extraction'][0]['ocr_pages']
    if code == 'S12':
        assert result['changes'] == []
    else:
        assert result['counts'] == {'modified': 1}
        change = result['changes'][0]
        assert (change['before'], change['after']) == ('120000元', '125000元')
        assert change['old'] and change['new']


def test_scan_without_ocr_is_incomplete_instead_of_reported_unchanged(tmp_path):
    source = next(c for c in scenarios() if c['id'] == 'S12')
    for side in ('old', 'new'):
        path = tmp_path / (side + '.pdf'); render(path, source[side], NORMAL); scan(path, dpi=120)
    result = compare_content_files(tmp_path / 'old.pdf', tmp_path / 'new.pdf')
    assert not result['complete']
    assert result['extraction'][0]['unread_pages'] == [1]
    assert result['extraction'][1]['unread_pages'] == [1]


@pytest.mark.parametrize('body', ['', '合同金额为120000元，其他条件保持。'])
def test_title_change_is_not_discarded_even_without_body(tmp_path, body):
    render(tmp_path / 'old.pdf', [paragraph('a', '含税报价', body)], NORMAL)
    render(tmp_path / 'new.pdf', [paragraph('a', '未税报价', body)], NORMAL)
    result = compare_content_files(tmp_path / 'old.pdf', tmp_path / 'new.pdf')
    assert result['counts'] == {'modified': 1}
    change = result['changes'][0]
    assert '含税报价' in change['old_context'] and '未税报价' in change['new_context']
    assert change['old'] and change['new']


def test_unread_scanned_continuation_is_review_not_confirmed_modification(tmp_path):
    with pdf.open() as doc:
        page = doc.new_page()
        page.insert_text((52, 80), '第1条 履约约定', fontname='china-s', fontsize=13)
        page.insert_text((52, 120), '交付资料由双方确认。', fontname='china-s', fontsize=11)
        page = doc.new_page()
        page.insert_text((52, 80), '后续还应提交完整清单。', fontname='china-s', fontsize=11)
        doc.save(tmp_path / 'new.pdf')
    (tmp_path / 'old.pdf').write_bytes((tmp_path / 'new.pdf').read_bytes())
    scan(tmp_path / 'old.pdf', dpi=120, mixed=True)
    result = compare_content_files(tmp_path / 'old.pdf', tmp_path / 'new.pdf')
    assert not result['complete']
    assert result['changes'] and all(c['kind'] == 'review' for c in result['changes'])


def test_added_table_row_has_original_row_location(tmp_path):
    table = [dict(id='t', title='设备清单', kind='table', rows=[['设备甲', '10', '台', '1200']])]
    render(tmp_path / 'old.pdf', table, NORMAL)
    table[0]['rows'].append(['设备乙', '5', '套', '800'])
    render(tmp_path / 'new.pdf', table, NORMAL)
    result = compare_content_files(tmp_path / 'old.pdf', tmp_path / 'new.pdf')
    added = [c for c in result['changes'] if c['kind'] == 'added']
    assert len(added) == 1 and '设备乙' in added[0]['heading']
    assert added[0]['new'] and added[0]['new'][0]['page'] == 1


def test_cancel_between_extraction_and_matching(documents):
    calls = []
    def progress(*args):
        calls.append(args)
    folder = documents['S03']
    with pytest.raises(OperationCancelled):
        compare_content_files(folder / 'old.pdf', folder / 'new.pdf', progress=progress,
                              cancel=lambda: len(calls) >= 5)


def test_text_budget_is_checked_before_allocating_character_positions(documents, monkeypatch):
    import app.pdfcontentcompare as module
    monkeypatch.setattr(module, 'MAX_CHARS', 4)
    monkeypatch.setattr(module, 'native_lines', lambda *a: pytest.fail('raw positions allocated after exceeding budget'))
    folder = documents['S03']
    with pytest.raises(ValueError, match='文字量'):
        compare_content_files(folder / 'old.pdf', folder / 'new.pdf')
