"""Real generated PDFs cover cancellation, destination protection and visible coordinates."""
from dataclasses import replace

import pymupdf as p
import pytest

from app.annotcrop import AnnotationCropError, export_annotation_crop
from app.output import OperationCancelled
from app.pdfio import EncryptionOptions, PdfStore
from test_annotcrop import make_annotated_pdf


@pytest.fixture
def loaded(tmp_path):
    source = make_annotated_pdf(tmp_path / 'source.pdf', pages=2)
    store = PdfStore()
    pages = store.load(str(source))
    try:
        yield store, pages, source
    finally:
        store.close_all()


def test_cancel_during_generation_preserves_existing_target(loaded, tmp_path):
    store, pages, _ = loaded
    target = make_annotated_pdf(tmp_path / 'target.pdf')
    before = target.read_bytes()
    canceled = False
    def progress(_, total, message):
        nonlocal canceled
        if message.startswith('已排版'):
            canceled = True
    with pytest.raises(OperationCancelled):
        export_annotation_crop(store, pages, {}, str(target), progress, lambda: canceled)
    assert target.read_bytes() == before
    assert not list(tmp_path.glob('.zhixu-extract-*'))


def test_changed_destination_is_not_overwritten(loaded, tmp_path):
    store, pages, _ = loaded
    target = tmp_path / 'target.pdf'
    def progress(_, total, message):
        if message == '正在生成 PDF…':
            target.write_bytes(b'generated concurrent writer')
    with pytest.raises(OSError, match='目标文件'):
        export_annotation_crop(store, pages, {}, str(target), progress)
    assert target.read_bytes() == b'generated concurrent writer'
    assert not list(tmp_path.glob('.zhixu-extract-*'))


def test_save_failure_cleans_staging_and_preserves_target(loaded, tmp_path, monkeypatch):
    store, pages, _ = loaded
    target = make_annotated_pdf(tmp_path / 'target.pdf')
    before = target.read_bytes()
    save = p.Document.save
    def fail(document, path, *args, **kwargs):
        if '.zhixu-extract-' in str(path):
            raise OSError('generated save failure')
        return save(document, path, *args, **kwargs)
    monkeypatch.setattr(p.Document, 'save', fail)
    with pytest.raises(OSError, match='generated save failure'):
        export_annotation_crop(store, pages, {}, str(target))
    assert target.read_bytes() == before and not list(tmp_path.glob('.zhixu-extract-*'))


@pytest.mark.parametrize('source', ['margin', 'regions'])
def test_non_marker_modes_preserve_ordinary_rectangles(loaded, tmp_path, source):
    store, pages, original = loaded
    before = original.read_bytes()
    target = tmp_path / 'target.pdf'
    export_annotation_crop(store, pages, dict(source=source, layout='per', regions={0: [(50, 10, 250, 120)]}), str(target))
    with p.open(target) as saved:
        assert any(item['color'] == (1, 0, 0) for item in saved[0].get_drawings())
    assert original.read_bytes() == before


def test_source_cannot_be_overwritten(loaded):
    store, pages, source = loaded
    before = source.read_bytes()
    with pytest.raises(AnnotationCropError, match='原文件'):
        export_annotation_crop(store, pages, {}, str(source))
    assert source.read_bytes() == before


def test_blank_regions_are_valid_and_not_copied_to_other_pages(tmp_path):
    source, target = tmp_path / 'blank.pdf', tmp_path / 'target.pdf'
    with p.open() as doc:
        doc.new_page()
        doc.new_page()
        doc.save(source)
    store = PdfStore()
    try:
        pages = store.load(str(source))
        result = export_annotation_crop(store, pages, dict(source='regions', layout='per', regions={0: [(10, 10, 110, 60)]}), str(target))
    finally:
        store.close_all()
    assert result['regions'] == result['out_pages'] == 1
    with p.open(target) as saved:
        assert saved[0].rect.width == 100 and saved[0].rect.height == 50


@pytest.mark.parametrize('rotation,extra,area', [(90, 0, (330, 80, 380, 220)),
    (0, 90, (330, 80, 380, 220)), (90, 90, (380, 330, 520, 380))])
def test_manual_regions_use_visible_page_coordinates(tmp_path, rotation, extra, area):
    source = make_annotated_pdf(tmp_path / 'source.pdf', rotation=rotation)
    target = tmp_path / 'target.pdf'
    store = PdfStore()
    try:
        pages = [replace(store.load(str(source))[0], rotation=extra)]
        export_annotation_crop(store, pages, dict(source='regions', layout='per', regions={0: [area]}), str(target))
    finally:
        store.close_all()
    with p.open(target) as saved:
        assert 'TARGET0' in saved[0].get_text() and 'OTHER' not in saved[0].get_text()


@pytest.mark.parametrize('derived', [False, True])
def test_encrypted_source_requires_explicit_output_protection(tmp_path, derived):
    plain = make_annotated_pdf(tmp_path / 'plain.pdf')
    encrypted, target = tmp_path / 'encrypted.pdf', tmp_path / 'target.pdf'
    with p.open(plain) as doc:
        doc.save(encrypted, encryption=p.PDF_ENCRYPT_AES_256, user_pw='test-open', owner_pw='test-owner')
    store = PdfStore()
    try:
        pages = store.load(str(encrypted), 'test-open')
        if derived:
            pages = store.split_page(pages[0], 'vertical', (.5,))
            assert not store._source(pages[0].source_id).safety.encrypted
        options = dict(source='regions', regions={0: [(50, 10, 250, 120)]})
        with pytest.raises(AnnotationCropError, match='输出保护'):
            export_annotation_crop(store, pages, options, str(target))
        options['encryption'] = EncryptionOptions('test-new', 'test-owner-new')
        export_annotation_crop(store, pages, options, str(target))
    finally:
        store.close_all()
    with p.open(target) as saved:
        assert saved.needs_pass and saved.authenticate('test-new')
        assert 'TARGET0' in saved[0].get_text()
