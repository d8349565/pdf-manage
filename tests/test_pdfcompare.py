from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading

from PIL import Image, ImageDraw
import pymupdf
import pytest

from app.output import OperationCancelled
from app.pdfcompare import CompareOptions, compare_files, compare_images, preview_pair
from app.pdfcompare_demo import generate_cases, vector_pdf


@pytest.fixture(scope='module')
def cases(tmp_path_factory):
    return generate_cases(tmp_path_factory.mktemp('compare-fixtures'))


@pytest.mark.parametrize('name', ['identical', 'text', 'image_removed', 'image_added',
    'color', 'insert', 'mixed_insert', 'delete', 'blank_insert', 'size', 'blank', 'translation',
    'annotation', 'scan_noise', 'jpeg', 'resampled', 'noise_change'])
def test_known_visual_changes_and_pairing(cases, name):
    case = cases[name]
    hashes = [hashlib.sha256(Path(case[k]).read_bytes()).digest() for k in ('old', 'new')]
    result = compare_files(case['old'], case['new'])
    assert [r['status'] for r in result['rows']] == case['expected']
    for row in result['rows']:
        if row['status'] != 'equal':
            assert row['regions']
        assert row['canvas'][0] * row['canvas'][1] <= result['options']['max_pixels']
    assert hashes == [hashlib.sha256(Path(case[k]).read_bytes()).digest() for k in ('old', 'new')]
    if name == 'insert':
        assert [(r['old_page'], r['new_page']) for r in result['rows']] == [(1, 1), (None, 2), (2, 3), (3, 4)]
    if name == 'text':
        # The changed amount is on the second line: no spurious full-page rectangle.
        row = result['rows'][0]
        assert all(box[1] < 100 * row['scale'] and box[3] > 75 * row['scale'] for box in row['regions'])
        assert row['changed_pixels'] < 1000


def test_noise_tolerance_and_strict_mode(cases):
    c = cases['scan_noise']
    filtered = compare_files(c['old'], c['new'])
    strict = compare_files(c['old'], c['new'], CompareOptions(threshold=0, blur=0, min_pixels=1))
    assert filtered['rows'][0]['changed_pixels'] == 0
    assert strict['rows'][0]['changed_pixels'] > 10000


def test_resampling_has_documented_residual_false_positives(cases):
    c = cases['resampled']
    result = compare_files(c['old'], c['new'])
    assert c['known_false_positive']
    assert 0 < result['rows'][0]['changed_pixels'] < 200


def test_noise_filter_can_hide_tiny_real_edits():
    old = Image.new('RGB', (100, 100), 'white'); new = old.copy()
    new.putpixel((50, 50), (0, 0, 0))
    assert compare_images(old, new, CompareOptions())['changed_pixels'] == 0
    assert compare_images(old, new, CompareOptions(threshold=0, blur=0, min_pixels=1))['changed_pixels'] == 1


def test_alignment_is_explicit_and_reported(cases):
    c = cases['translation']
    plain = compare_files(c['old'], c['new'])
    aligned = compare_files(c['old'], c['new'], CompareOptions(align_pixels=2))
    assert plain['rows'][0]['shift'] == [0, 0]
    assert aligned['rows'][0]['shift'] == [-2, 0]
    assert aligned['rows'][0]['changed_pixels'] < plain['rows'][0]['changed_pixels'] * .1
    assert aligned['rows'][0]['warnings']


def test_annotation_policy(cases):
    c = cases['annotation']
    result = compare_files(c['old'], c['new'], CompareOptions(annotations=False))
    assert result['rows'][0]['status'] == 'equal'


def test_preview_has_actual_colour_evidence_and_originals(cases):
    c = cases['color']
    summary = compare_files(c['old'], c['new'])
    row, images = preview_pair(summary, 0)
    assert row['status'] == 'changed'
    colours = images['overlay'].getcolors(images['overlay'].width * images['overlay'].height)
    assert any(colour == (232, 139, 20) for _, colour in colours)
    assert images['old'].tobytes() != images['new'].tobytes()


def test_source_replacement_invalidates_preview(cases, tmp_path):
    c = cases['text']
    source = tmp_path / 'new.pdf'; source.write_bytes(Path(c['new']).read_bytes())
    result = compare_files(c['old'], source)
    vector_pdf(source, [dict(amount='999999')])
    with pytest.raises(ValueError, match='已经变化'):
        preview_pair(result, 0)


def test_cancel_during_fingerprinting_and_comparison(cases):
    c = cases['insert']
    event = threading.Event()
    def progress(current, total, message):
        event.set()
    with pytest.raises(OperationCancelled):
        compare_files(c['old'], c['new'], progress=progress, cancel=event.is_set)
    event.clear()
    def during_diff(current, total, message):
        if '正在比较' in message:
            event.set()
    with pytest.raises(OperationCancelled):
        compare_files(c['old'], c['new'], progress=during_diff, cancel=event.is_set)


def test_limits_and_bad_inputs(cases, tmp_path):
    c = cases['insert']
    with pytest.raises(ValueError, match='最多 1 页'):
        compare_files(c['old'], c['new'], CompareOptions(max_pages=1))
    invalid = tmp_path / 'not.pdf'; invalid.write_text('not PDF')
    with pytest.raises(Exception):
        compare_files(invalid, c['new'])
    with pytest.raises(ValueError):
        compare_files(c['old'], c['new'], CompareOptions(dpi=500))


def test_small_component_inside_large_component_box_is_not_reintroduced():
    a = Image.new('RGB', (100, 100), 'white'); b = a.copy()
    d = ImageDraw.Draw(b); d.rectangle((10, 10, 80, 80), outline='black', width=3)
    d.point((40, 40), fill='black')
    diff = compare_images(a, b, CompareOptions(blur=0))
    assert diff['mask'].getpixel((40, 40)) == 0
    assert diff['mask'].getpixel((10, 10)) == 255


def test_encryption_and_copy_permissions(cases, tmp_path):
    source = cases['identical']['old']
    restricted = tmp_path / 'restricted.pdf'
    with pymupdf.open(source) as doc:
        doc.save(restricted, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw='owner',
                  user_pw='reader', permissions=pymupdf.PDF_PERM_PRINT)
    with pytest.raises(ValueError, match='打开密码'):
        compare_files(source, restricted)
    with pytest.raises(ValueError, match='不允许复制'):
        compare_files(source, restricted, new_password='reader')
    assert compare_files(source, restricted, new_password='owner')['rows'][0]['status'] == 'equal'


def test_cli_writes_only_new_result_directory(cases, tmp_path):
    c = cases['text']; output = tmp_path / 'result'
    args = [sys.executable, '-m', 'app.pdfcompare', c['old'], c['new'], '--output', str(output), '--preview', '1']
    completed = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert json.loads((output / 'summary.json').read_text())['rows'][0]['status'] == 'changed'
    assert (output / 'overlay.png').is_file()
    assert subprocess.run(args, capture_output=True, timeout=30).returncode != 0


def test_worker_preview_and_cancellation(cases):
    from app.pdfworker import compare_pdf_versions
    c = cases['text']
    progress = []
    result = compare_pdf_versions(c['old'], c['new'], CompareOptions(), lambda *args: progress.append(args))
    assert result['rows'][0]['status'] == 'changed'
    assert progress
    row, images = compare_pdf_versions(c['old'], c['new'], CompareOptions(), summary=result)
    assert images['overlay'].size == tuple(row['canvas'])
    event = threading.Event()
    with pytest.raises(OperationCancelled):
        compare_pdf_versions(c['old'], c['new'], CompareOptions(), lambda *args: event.set(), event.is_set)
