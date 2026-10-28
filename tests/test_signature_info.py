"""Protection information must describe generated PDFs without changing them."""
from threading import Event

import pymupdf as p
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDialog

from app.output import OperationCancelled
from app.pdfio import PdfStore
from app.signatureui import SignatureInfoDialog
from app.ui import MainWindow


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


def signed_pdf(path, mode='signed', *, protected=False):
    with p.open() as doc:
        doc.new_page()
        page = doc.new_page()
        widget = p.Widget()
        widget.field_type = p.PDF_WIDGET_TYPE_SIGNATURE
        widget.field_name = 'Approval'
        widget.rect = p.Rect(40, 100, 190, 160)
        page.add_widget(widget)
        field = next(page.widgets()).xref
        if mode == 'signed':
            value = doc.get_new_xref()
            doc.update_object(value, '<< /Type /Sig /ByteRange [0 1 2 3] /Contents <12345678> '
                '/Name (<b>Alice</b>) /M (D:20261028120000+08\'00\') '
                '/Reason (Approval) /Location (Office) /SubFilter /adbe.pkcs7.detached >>')
            doc.xref_set_key(field, 'V', f'{value} 0 R')
        elif mode == 'unknown':
            doc.xref_set_key(field, 'V', '(unsupported)')
        doc.xref_set_key(doc.pdf_catalog(), 'AcroForm/SigFlags', '3')
        options = dict(encryption=p.PDF_ENCRYPT_AES_256, user_pw='', owner_pw='manager',
                       permissions=p.PDF_PERM_PRINT) if protected else {}
        doc.save(path, **options)
    return path


@pytest.mark.parametrize('mode, state, row_status', [
    ('signed', 'present', '已填写 · 未验签'), ('blank', 'none', '空白签名字段'),
    ('unknown', 'unknown', '结构无法确认')])
def test_reports_metadata_page_and_protection_without_writes(tmp_path, mode, state, row_status):
    path = signed_pdf(tmp_path / 'generated.pdf', mode)
    original = path.read_bytes()
    store = PdfStore()
    try:
        pages = store.load(str(path), defer_safety=True)
        report, = store.protection_reports(pages)
        assert report['safety'].signature == state
        row, = report['signatures']
        assert row.status == row_status and row.name == 'Approval'
        assert row.pages == (1,)
        if mode == 'signed':
            assert row.signer == '<b>Alice</b>'
            assert row.reason == 'Approval' and row.location == 'Office'
            assert row.method == '/adbe.pkcs7.detached'
        if mode == 'unknown':
            assert report['issues']
        assert bool(store.operation_reason(pages, 'modify')) == (state != 'none')
        assert path.read_bytes() == original
    finally:
        store.close_all()


def test_sparse_unknown_reference_is_explained_and_cancellable(tmp_path):
    from test_signature_sparse import sparse_pdf
    store = PdfStore()
    try:
        pages = store.load(str(sparse_pdf(tmp_path / 'sparse.pdf', 'unknown')))
        report, = store.protection_reports(pages)
        assert report['safety'].signature == 'unknown'
        assert '无法读取' in report['signatures'][0].note
        event = Event()
        event.set()
        with pytest.raises(OperationCancelled):
            store.protection_reports(pages, cancel=event.is_set)
        assert store.operation_reason(pages, 'modify')
    finally:
        store.close_all()


@pytest.mark.parametrize('inherit_value', [False, True])
def test_inherited_signature_fields_are_not_blank_or_unlocated(tmp_path, inherit_value):
    from app.signatureinfo import collect_signature_info
    path = signed_pdf(tmp_path / 'nested.pdf')
    with p.open(path) as doc:
        field = next(doc[1].widgets()).xref
        value = doc.xref_get_key(field, 'V')[1]
        parent = doc.get_new_xref()
        doc.update_object(parent, f'<< /FT /Sig /T (Department) /Kids [{field} 0 R] >>')
        doc.xref_set_key(field, 'FT', 'null')
        doc.xref_set_key(field, 'Parent', f'{parent} 0 R')
        doc.xref_set_key(doc.pdf_catalog(), 'AcroForm/Fields', f'[{parent} 0 R]')
        if inherit_value:
            doc.xref_set_key(parent, 'V', value)
            doc.xref_set_key(field, 'V', 'null')
        rows, _ = collect_signature_info(doc)
        assert len(rows) == 1
        assert rows[0].name == 'Department.Approval'
        assert rows[0].status == '已填写 · 未验签'
        assert rows[0].pages == (1,)


@pytest.fixture
def window(qapp):
    host = MainWindow()
    yield host
    host.ws.reset()
    host.close()
    qapp.processEvents()


def load(host, path):
    host.ws.load_first_or_merge(host.store.load(str(path)), str(path))
    host.refresh()
    return host.store.protection_reports(host.ws.pages)


def test_panel_shows_plain_declared_metadata_and_source_page_mapping(window, tmp_path):
    reports = load(window, signed_pdf(tmp_path / 'signed.pdf'))
    dialog = SignatureInfoDialog(window, reports)
    try:
        assert '有效性未验证' in dialog.status.text()
        assert '<b>Alice</b>' in dialog.details.toPlainText()
        assert dialog.status.textFormat() == Qt.TextFormat.PlainText
        assert dialog.target_page == 2 and dialog.jump.isEnabled()
        assert dialog.editable.isEnabled() and dialog.raster.isEnabled()
        assert not window.save_action.isEnabled()
        assert '当前版本尚未解析证书' in dialog.details.toPlainText()
        window.ws.pages = window.ws.pages[:1]
        dialog.show_signature()
        assert not dialog.jump.isEnabled()
    finally:
        dialog.close()
        dialog.deleteLater()


def test_panel_respects_copy_permissions_and_unknown_structure(window, tmp_path):
    reports = load(window, signed_pdf(tmp_path / 'restricted.pdf', 'unknown', protected=True))
    dialog = SignatureInfoDialog(window, reports)
    try:
        assert not dialog.editable.isEnabled() and not dialog.raster.isEnabled()
        assert '复制受限' in dialog.permissions.text()
        assert '影像副本：' in dialog.action_hint.text()
        assert reports[0]['safety'].signature == 'unknown'
    finally:
        dialog.close()
        dialog.deleteLater()


def test_multiple_sources_are_separate_but_copy_policy_covers_whole_document(window, tmp_path):
    load(window, signed_pdf(tmp_path / 'signed.pdf'))
    reports = load(window, signed_pdf(tmp_path / 'blank.pdf', 'blank'))
    dialog = SignatureInfoDialog(window, reports)
    try:
        assert dialog.source.count() == 2
        assert not dialog.editable.isEnabled()
        assert dialog.raster.isEnabled()
        dialog.source.setCurrentIndex(1)
        assert '未发现' in dialog.status.text()
        assert dialog.target_page == 4
        assert dialog.signatures.topLevelItem(0).text(2) == '空白签名字段'
    finally:
        dialog.close()
        dialog.deleteLater()


@pytest.mark.parametrize('action', ['jump', 'editable', 'raster'])
def test_menu_reuses_existing_navigation_and_safe_copy_routes(window, tmp_path, monkeypatch, action):
    load(window, signed_pdf(tmp_path / 'signed.pdf'))
    calls = []
    def choose(dialog):
        dialog.action = action
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(SignatureInfoDialog, 'exec', choose)
    monkeypatch.setattr(window, 'go_to_page', lambda page: calls.append(('jump', page)))
    monkeypatch.setattr(window, 'editable_copy', lambda: calls.append(('editable',)))
    monkeypatch.setattr(window, 'rasterize_document', lambda **kw: calls.append(('raster', kw['recovery'])))
    window.signature_info_action.trigger()
    assert calls == {'jump': [('jump', 2)], 'editable': [('editable',)], 'raster': [('raster', False)]}[action]
    assert not window.save_action.isEnabled()
