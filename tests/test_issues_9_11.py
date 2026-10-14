"""Generated PDFs prove panel persistence and safe image-copy recovery."""
from dataclasses import replace
from pathlib import Path
from random import Random

import pymupdf as p
import pytest
from PyQt6.QtCore import QSettings, QThreadPool
from PyQt6.QtWidgets import QApplication, QMessageBox

from app.output import OperationCancelled
from app.pdfio import EncryptionOptions, PdfError, PdfStore, WriteNeedsConfirmation
from app.rasterpdf import RasterOptions, rasterize_pdf
from app.workspace import PageEdit


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


def make_pdf(path, *, rotation=0, crop=False, signature='none', password=None, permissions=4095):
    with p.open() as doc:
        for index in range(2):
            page = doc.new_page(width=360, height=504)
            page.insert_text((45, 100), f'Editable page {index + 1}')
            page.draw_rect(p.Rect(30, 150, 130, 210), fill=(1, .3, .1))
            page.add_text_annot((50, 240), 'Render this annotation')
            page.insert_link({'kind': p.LINK_URI, 'from': p.Rect(30, 300, 160, 320), 'uri': 'https://example.com'})
            if crop:
                page.set_cropbox(p.Rect(20, 30, 340, 480))
            page.set_rotation(rotation)
        doc.set_toc([[1, 'First', 1], [1, 'Second', 2]])
        doc.set_page_labels([dict(startpage=0, prefix='Office-', style='D')])
        if signature == 'unknown':
            sig = doc.get_new_xref()
            doc.update_object(sig, '<< /FT /Sig /V (unrecognized-signature) >>')
            doc.xref_set_key(doc.pdf_catalog(), 'AcroForm', f'<< /Fields [{sig} 0 R] /SigFlags 3 >>')
        elif signature == 'present':
            sig = doc.get_new_xref()
            doc.update_object(sig, '<< /Type /Sig /ByteRange [0 1 2 3] /Contents <11223344> >>')
            doc.xref_set_key(doc.pdf_catalog(), 'Perms', f'<< /DocMDP {sig} 0 R >>')
        encrypted = {} if password is None else dict(encryption=p.PDF_ENCRYPT_AES_256, user_pw=password,
                                                    owner_pw='owner-password', permissions=permissions)
        doc.save(path, **encrypted)


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
@pytest.mark.parametrize('crop', [False, True])
def test_raster_geometry_order_pixels_and_original_preserved(tmp_path, rotation, crop):
    source, target = tmp_path / 'source.pdf', tmp_path / 'images.pdf'
    make_pdf(source, rotation=rotation, crop=crop)
    before = source.read_bytes()
    store = PdfStore()
    try:
        pages = store.load(str(source))
        selected = [pages[1].rotated(90), pages[0]]
        rasterize_pdf(store, selected, target, RasterOptions(image_format='png'))
        with p.open(source) as original, p.open(target) as output:
            assert output.page_count == 2 and output.get_toc() == [[1, 'First', 2], [1, 'Second', 1]]
            for index, ref in enumerate(selected):
                expected = original[ref.source_page]
                expected.set_rotation(0)
                page = output[index]
                assert page.mediabox == expected.mediabox and page.cropbox == expected.cropbox
                assert page.rotation == (rotation + ref.rotation) % 360
                assert not page.get_text().strip() and not page.get_links() and not list(page.widgets() or ())
                assert not list(page.annots() or ())
                image = p.Pixmap(output, page.get_images()[0][0])
                assert image.samples == expected.get_pixmap(dpi=200, colorspace=p.csRGB, alpha=False).samples
        assert source.read_bytes() == before
    finally:
        store.close_all()


@pytest.mark.parametrize('signature', ['unknown', 'present'])
def test_readonly_signature_has_safe_copy_route_without_unlocking_original(tmp_path, signature):
    from app.pdfstructure import signature_state
    source, target = tmp_path / 'signed.pdf', tmp_path / 'copy.pdf'
    make_pdf(source, signature=signature)
    before = source.read_bytes()
    store = PdfStore()
    try:
        pages = store.load(str(source))
        assert store._source(pages[0].source_id).safety.signature == signature
        assert store.operation_reason(pages, 'modify')
        assert store.operation_reason(pages, 'copy')
        assert not store.operation_reason(pages, 'raster_copy')
        rasterize_pdf(store, pages, target)
        with p.open(target) as doc:
            assert signature_state(doc) == 'none'
            assert all(not page.get_text().strip() for page in doc)
            assert doc.xref_get_key(doc.pdf_catalog(), 'AcroForm')[0] == 'null'
            assert doc.xref_get_key(doc.pdf_catalog(), 'Perms')[0] == 'null'
        copied = store.load(str(target))
        assert not store.operation_reason(copied, 'modify')
        assert source.read_bytes() == before
        assert store.operation_reason(pages, 'modify')
    finally:
        store.close_all()


def test_raster_respects_copy_permission_and_explicit_password_selection(tmp_path):
    source, target = tmp_path / 'encrypted.pdf', tmp_path / 'copy.pdf'
    make_pdf(source, signature='unknown', password='reader', permissions=p.PDF_PERM_COPY | p.PDF_PERM_PRINT)
    store = PdfStore()
    try:
        pages = store.load(str(source), password='reader')
        with pytest.raises(WriteNeedsConfirmation):
            rasterize_pdf(store, pages, target)
        options = RasterOptions(encryption=EncryptionOptions('new-reader', 'new-owner', p.PDF_PERM_COPY))
        rasterize_pdf(store, pages, target, options)
        with p.open(target) as output:
            assert output.needs_pass
            assert not output.authenticate('reader') and output.authenticate('new-reader')
            assert not output.permissions & p.PDF_PERM_PRINT
        plain = tmp_path / 'plain.pdf'
        rasterize_pdf(store, pages, plain, RasterOptions(unencrypted_confirmed=True))
        with p.open(plain) as output:
            assert not output.needs_pass
    finally:
        store.close_all()
    denied = tmp_path / 'denied.pdf'
    make_pdf(denied, signature='unknown', password='reader', permissions=p.PDF_PERM_PRINT)
    store = PdfStore()
    try:
        pages = store.load(str(denied), password='reader')
        with pytest.raises(PdfError, match='不允许导出'):
            rasterize_pdf(store, pages, tmp_path / 'denied-copy.pdf', RasterOptions(unencrypted_confirmed=True))
    finally:
        store.close_all()


def test_raster_cancellation_existing_target_and_source_change_never_commit(tmp_path):
    source, target = tmp_path / 'source.pdf', tmp_path / 'copy.pdf'
    make_pdf(source)
    before = source.read_bytes()
    store = PdfStore()
    try:
        pages = store.load(str(source))
        cancelled = [False]
        def progress(current, total, message):
            if current == 1:
                cancelled[0] = True
        with pytest.raises(OperationCancelled):
            rasterize_pdf(store, pages, target, progress_callback=progress, cancel=lambda: cancelled[0])
        assert not target.exists() and not list(tmp_path.glob('.zhixu-raster-*'))
        with pytest.raises(PdfError, match='不能覆盖'):
            rasterize_pdf(store, pages, source)
        target.write_bytes(b'keep-existing')
        with pytest.raises(PdfError, match='已存在'):
            rasterize_pdf(store, pages, target)
        assert target.read_bytes() == b'keep-existing'
        source.write_bytes(before + b'\n')
        with pytest.raises(PdfError, match='发生变化'):
            rasterize_pdf(store, pages, tmp_path / 'changed.pdf')
    finally:
        store.close_all()


def test_jpeg_and_duplicate_workspace_edits_use_independent_pages(tmp_path):
    source, target = tmp_path / 'source.pdf', tmp_path / 'copy.pdf'
    make_pdf(source)
    store = PdfStore()
    try:
        pages = store.load(str(source))
        edited = pages[0].edited(PageEdit('rect', ((40, 60), (180, 190)), color=(0, 0, 1), annotation_id='test'))
        rasterize_pdf(store, [edited, pages[0]], target, RasterOptions(150, 'jpeg'))
        with p.open(target) as doc:
            assert doc[0].get_pixmap().samples != doc[1].get_pixmap().samples
            assert all(not page.get_text().strip() for page in doc)
    finally:
        store.close_all()


def test_default_watermarked_scan_uses_jpeg_and_is_smaller_than_lossless(tmp_path):
    source = tmp_path / 'scan.pdf'
    target, lossless = tmp_path / 'compact.pdf', tmp_path / 'lossless.pdf'
    # Fixed continuous-tone pixels expose the PNG expansion of an already compressed scan.
    scan = p.Pixmap(p.csGRAY, 600, 800, Random(0).randbytes(600 * 800), False)
    with p.open() as doc:
        page = doc.new_page(width=216, height=288)
        page.insert_image(page.rect, stream=scan.tobytes('jpg', jpg_quality=80))
        doc.save(source)
    before = source.read_bytes()
    store = PdfStore()
    try:
        pages = store.load(str(source))
        edited = pages[0].edited(PageEdit('watermark', text='Sample', color=(.3, .4, .5),
            annotation_id='zhixu-watermark-size', details=(('opacity', .2), ('tile', True))))
        rasterize_pdf(store, [edited], target)
        rasterize_pdf(store, [edited], lossless, RasterOptions(image_format='png'))
        with p.open(target) as output, p.open(source) as original:
            from app.pdfedits import apply_edits
            page = original[0]
            apply_edits(page, edited.edits)
            expected = p.Pixmap(page.get_pixmap(dpi=200, colorspace=p.csRGB, alpha=False)
                               .tobytes('jpg', jpg_quality=90))
            xref = output[0].get_images()[0][0]
            assert output.xref_get_key(xref, 'Filter') == ('name', '/DCTDecode')
            assert p.Pixmap(output, xref).samples == expected.samples
            assert not output[0].get_text().strip() and not list(output[0].annots() or ())
        assert target.stat().st_size < lossless.stat().st_size
        assert source.read_bytes() == before
    finally:
        store.close_all()


def test_nonzero_media_box_and_visible_signature_appearance_are_retained(tmp_path):
    source, target = tmp_path / 'appearance.pdf', tmp_path / 'copy.pdf'
    with p.open() as doc:
        page = doc.new_page(width=360, height=504)
        page.set_mediabox(p.Rect(10, 20, 370, 524))
        page.set_cropbox(p.Rect(30, 40, 350, 480))
        page.insert_text((35, 80), 'Keep the signature appearance')
        widget = p.Widget()
        widget.field_name = 'Unknown signature'
        widget.field_type = p.PDF_WIDGET_TYPE_SIGNATURE
        widget.rect = p.Rect(40, 160, 200, 210)
        page.add_widget(widget)
        widget = next(page.widgets())
        appearance = doc.get_new_xref()
        doc.update_object(appearance, '<< /Type /XObject /Subtype /Form /BBox [0 0 160 50] /Resources <<>> >>')
        doc.update_stream(appearance, b'0 0.2 0.8 rg 0 0 160 50 re f')
        doc.xref_set_key(widget.xref, 'AP', f'<< /N {appearance} 0 R >>')
        doc.xref_set_key(widget.xref, 'V', '(unrecognized)')
        page.set_rotation(270)
        doc.save(source)
    store = PdfStore()
    try:
        pages = store.load(str(source))
        assert store._source(pages[0].source_id).safety.signature == 'unknown'
        rasterize_pdf(store, pages, target, RasterOptions(image_format='png'))
        with p.open(source) as original, p.open(target) as copied:
            original[0].set_rotation(0)
            expected = original[0].get_pixmap(dpi=200, colorspace=p.csRGB, alpha=False)
            assert p.Pixmap(copied, copied[0].get_images()[0][0]).samples == expected.samples
            assert not list(copied[0].widgets() or ())
    finally:
        store.close_all()


def close_window(window, qapp):
    QThreadPool.globalInstance().waitForDone(30000)
    window.ws.reset()
    window.store.close_all()
    window.close()
    qapp.processEvents()


def test_panel_preferences_restore_user_choices_while_empty_and_reset_only_ui(qapp, tmp_path, monkeypatch):
    import app.ui as ui
    factory = lambda *args: QSettings(str(tmp_path / 'ui.ini'), QSettings.Format.IniFormat)
    monkeypatch.setattr(ui, 'QSettings', factory)
    first = ui.MainWindow()
    try:
        first.act_pages.setChecked(True)
        first.act_tools.setChecked(True)
        first.sidebar_mode.setCurrentIndex(1)
        first.export_tools.setCurrentIndex(2)
        factory().setValue('storage/ocr', str(tmp_path / 'keep-models'))
    finally:
        close_window(first, qapp)
    second = ui.MainWindow()
    try:
        assert second.act_pages.isChecked() and second.act_tools.isChecked()
        assert second.sidebar_mode.currentIndex() == 1 and second.export_tools.currentIndex() == 2
        assert not second.left_panel.isVisible() and not second.tools_panel.isVisible()
        second.reset_ui_preferences()
        assert not second.act_pages.isChecked() and not second.act_tools.isChecked()
        assert factory().value('storage/ocr') == str(tmp_path / 'keep-models')
    finally:
        close_window(second, qapp)


def test_unknown_signature_ui_exposes_recovery_and_keeps_destructive_actions_disabled(qapp, tmp_path, monkeypatch):
    import app.ui as ui
    monkeypatch.setattr(ui, 'QSettings', lambda *args: QSettings(str(tmp_path / 'ui.ini'), QSettings.Format.IniFormat))
    source = tmp_path / 'unknown.pdf'
    make_pdf(source, signature='unknown')
    window = ui.MainWindow()
    try:
        pages = window.store.load(str(source))
        window.ws.open_replace(pages, str(source))
        window.refresh()
        assert window.raster_action.isEnabled() and window.editable_action.isEnabled()
        assert not window.protection_copy_button.isHidden()
        assert not window.save_action.isEnabled() and not window.delete_action.isEnabled()
        calls = []
        monkeypatch.setattr(window, 'rasterize_document', lambda *args, **kwargs: calls.append(kwargs))
        window.editable_copy()
        assert calls == [dict(recovery=True)]
    finally:
        close_window(window, qapp)


def test_raster_dialog_ranges_and_collision_prevention(qapp, tmp_path):
    from app.rasterui import RasterDialog
    source = tmp_path / 'source.pdf'
    (tmp_path / 'source_影像副本.pdf').write_bytes(b'keep')
    dialog = RasterDialog(6, 2, [1, 4], source)
    try:
        assert dialog.options() == RasterOptions(200, 'jpeg')
        for index, expected in enumerate(((200, 'jpeg'), (300, 'png'), (150, 'jpeg'), (200, 'png'))):
            dialog.quality.setCurrentIndex(index)
            assert dialog.options() == RasterOptions(*expected)
        assert dialog.output.text().endswith('source_影像副本_2.pdf')
        assert dialog.indices() == list(range(6))
        dialog.scope.setCurrentIndex(1)
        assert dialog.indices() == [2]
        dialog.scope.setCurrentIndex(2)
        assert dialog.indices() == [1, 4]
        dialog.scope.setCurrentIndex(3)
        assert not dialog.start.isEnabled()
        dialog.range.setText('1-3,5')
        assert dialog.indices() == [0, 1, 2, 4]
        assert dialog.start.isEnabled()
        dialog.output.setText(str(source))
        assert not dialog.start.isEnabled()
    finally:
        dialog.close()
