from pathlib import Path

import pymupdf as pdf
import pytest
from PyQt6.QtCore import QPoint, QPointF, QStandardPaths, QThreadPool, Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from app.pdfio import PdfError, PdfStore
from app.ui import MainWindow
from app.workspace import PageEdit


PAGE_FILE = b'A test file attached to a page.\n'


@pytest.fixture
def document(tmp_path):
    path = tmp_path / 'attachments.pdf'
    with pdf.open() as child:
        child.new_page().insert_text((40, 80), 'Embedded PDF')
        embedded = child.tobytes()
    with pdf.open() as root:
        page = root.new_page()
        root.embfile_add('child.pdf', embedded, filename='child.pdf')
        root.embfile_add('unsafe', b'plain test bytes', filename=r'folder\launch.lnk')
        clip = page.add_file_annot(pdf.Point(70, 180), PAGE_FILE, 'page.txt', icon='Paperclip').rect
        root.save(path)
    return path, embedded, clip


@pytest.fixture(scope='session')
def attachment_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def reader(document, attachment_app):
    window = MainWindow()
    window.resize(1180, 850)
    window.show()
    assert window.open_files_in_tabs([str(document[0])])
    attachment_app.processEvents()
    yield window
    window.ws.reset()
    window.close()
    QThreadPool.globalInstance().waitForDone(5000)
    attachment_app.processEvents()


def wait_for_rows(panel, count):
    for _ in range(60):
        if panel.tree.topLevelItemCount() == count:
            return
        QTest.qWait(20)
    assert panel.tree.topLevelItemCount() == count


def test_attachment_menu_keeps_auto_sidebar_available(reader):
    QApplication.setActiveWindow(reader)
    reader.attachments_action.trigger()
    panel = reader.attachments_panel
    wait_for_rows(panel, 3)
    reader.immersive._hide_if_away()
    assert reader.immersive.expanded and panel.tree.hasFocus()


def test_sidebar_opens_pdf_and_saves_attachments(reader, document, tmp_path, monkeypatch):
    path, embedded, _ = document
    original = path.read_bytes()
    parent_tab = reader._active_tab_id
    reader.immersive.auto_action.setChecked(False)
    reader.attachments_action.trigger()
    panel = reader.attachments_panel
    wait_for_rows(panel, 3)
    rows = [panel.tree.topLevelItem(i) for i in range(3)]
    assert [(row.text(0), row.text(1)) for row in rows] == [
        ('child.pdf', '文档'), ('launch.lnk', '文档'), ('page.txt', '第 1 页')]

    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[2]))
    for row, data in zip(rows, (embedded, b'plain test bytes', PAGE_FILE), strict=True):
        panel.tree.setCurrentItem(row)
        destination = tmp_path / row.text(0)
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *_: (str(destination), ''))
        assert panel.save_button.isEnabled()
        if row.text(0) == 'launch.lnk':
            assert not panel.open_button.isEnabled()
            # Save the inert test payload under a plain-text name, away from system associations.
            destination = destination.with_suffix('.txt')
        QTest.mouseClick(panel.save_button, Qt.MouseButton.LeftButton)
        assert destination.read_bytes() == data
    assert not warnings

    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *_: (str(path), ''))
    QTest.mouseClick(panel.save_button, Qt.MouseButton.LeftButton)
    assert len(warnings) == 1 and path.read_bytes() == original

    panel.tree.setCurrentItem(rows[0])
    QTest.mouseClick(panel.open_button, Qt.MouseButton.LeftButton)
    assert reader._active_tab_id != parent_tab
    opened = Path(reader.ws.path)
    assert opened.name == 'child.pdf' and opened.read_bytes() == embedded
    assert reader.reading_history.find(str(opened)) is None
    assert reader.store._source(reader.ws.pages[0].source_id).requires_copy
    wait_for_rows(panel, 0)
    assert reader.close_tab(reader._active_tab_id) and not opened.exists()
    wait_for_rows(panel, 3)


def test_save_opened_attachment_to_default_path_survives_close(reader, tmp_path, monkeypatch):
    from app.attachmentui import open_attachment

    monkeypatch.setattr(QStandardPaths, 'writableLocation', lambda *_: str(tmp_path))
    assert open_attachment(reader, reader.ws.pages[0], 0)
    temporary = Path(reader.ws.path)
    unsafe = temporary.with_name('saved-in-cache.pdf')
    with pytest.raises(PdfError, match='缓存目录之外'):
        reader.store.write(reader.ws.pages, str(unsafe))
    assert not unsafe.exists()
    saved = []

    def accept_suggested_path(_host, _title, suggested, *_):
        saved.append(Path(suggested))
        return suggested, ''

    monkeypatch.setattr(QFileDialog, 'getSaveFileName', accept_suggested_path)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: pytest.fail(str(args[2])))
    assert reader.save(True)
    assert saved[0].parent == tmp_path and saved[0].is_file()
    assert reader.close_tab(reader._active_tab_id)
    assert not temporary.exists()
    with pdf.open(saved[0]) as result:
        assert result[0].get_text().strip() == 'Embedded PDF'


def test_compression_preview_can_write_to_its_own_task_directory(document):
    from app.pdfio import WriteOptions
    from app.storage import TemporaryWorkspace

    store = PdfStore()
    try:
        pages = store.load(str(document[0]))
        with TemporaryWorkspace('compress') as folder:
            preview = Path(folder) / 'before.pdf'
            store.write(pages, str(preview), options=WriteOptions(compression='quality'))
            with pdf.open(preview) as result:
                assert result.page_count == len(pages)
    finally:
        store.close_all()


def test_nested_attachment_save_uses_configured_output_and_rejects_cache(reader, tmp_path, monkeypatch):
    from app.attachmentui import open_attachment, save_attachment
    from app.storage import save_directory

    parent = tmp_path / 'nested-attachments.pdf'
    with pdf.open() as child:
        child.new_page()
        child.embfile_add('nested.txt', PAGE_FILE)
        with pdf.open() as document:
            document.new_page()
            document.embfile_add('child.pdf', child.tobytes())
            document.save(parent)
    output = tmp_path / 'exports'
    output.mkdir()
    save_directory('output', output)
    assert reader.open_files_in_tabs([str(parent)])
    assert open_attachment(reader, reader.ws.pages[0], 0)
    temporary = Path(reader.ws.path)
    attachment, = reader.store.list_attachments(reader.ws.pages)
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[2]))
    unsafe = temporary.with_name('nested.txt')
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *_: (str(unsafe), ''))
    assert not save_attachment(reader, attachment)
    assert not unsafe.exists() and '缓存目录之外' in warnings[0]
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda _host, _title, path: (path, ''))
    assert save_attachment(reader, attachment)
    assert reader.close_tab(reader._active_tab_id) and not temporary.exists()
    assert (output / 'nested.txt').read_bytes() == PAGE_FILE


@pytest.mark.parametrize('mode', ('select', 'hand'))
def test_paperclip_opens_on_click_but_not_drag(reader, document, monkeypatch, mode):
    opened = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: opened.append(Path(url.toLocalFile())) or True)
    reader.set_reading_tool(mode)
    reader.store.prepare_annotations(reader.ws.pages[0])
    view = reader.preview_view
    clip = document[2]
    local = QPointF((clip.x0 + clip.x1) / 2, (clip.y0 + clip.y1) / 2)
    origin = view.page_rects[0].topLeft()
    point = view.mapFromScene(origin + local)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    end = point + QPoint(40, 20)
    QTest.mouseMove(view.viewport(), end)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert not opened
    point = view.mapFromScene(origin + local)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert len(opened) == 1 and opened[0].read_bytes() == PAGE_FILE


def test_attachment_read_obeys_pdf_copy_permission(tmp_path):
    path = tmp_path / 'protected.pdf'
    with pdf.open() as document:
        document.new_page()
        document.embfile_add('test.txt', PAGE_FILE)
        document.save(path, encryption=pdf.PDF_ENCRYPT_AES_256, user_pw='reader', owner_pw='owner',
                      permissions=pdf.PDF_PERM_PRINT)
    store = PdfStore()
    try:
        refs = store.load(str(path), 'reader')
        attachment, = store.list_attachments(refs)
        with pytest.raises(PdfError, match='不允许导出内容'):
            store.read_attachment(attachment.ref, attachment.key)
    finally:
        store.close_all()


def test_hidden_attachment_is_listed_without_a_page_hit_target(tmp_path):
    path = tmp_path / 'hidden.pdf'
    with pdf.open() as document:
        page = document.new_page()
        clip = page.add_file_annot(pdf.Point(70, 180), PAGE_FILE, 'hidden.txt')
        clip.set_flags(pdf.PDF_ANNOT_IS_NO_VIEW)
        document.save(path)
    store = PdfStore()
    try:
        refs = store.load(str(path))
        assert [item.name for item in store.list_attachments(refs)] == ['hidden.txt']
        assert store.prepare_annotations(refs[0])['file_attachments'] == ()
    finally:
        store.close_all()


def write_linked_pdf(path, address):
    area = pdf.Rect(70, 85, 210, 110)
    with pdf.open() as child:
        child.new_page().insert_text((40, 80), 'First page')
        child.new_page().insert_text((40, 80), f'Linked destination in {path.stem}')
        child.xref_set_key(child.pdf_catalog(), 'Names/Dests',
                           f'<< /Names [(chapter) [{child.page_xref(1)} 0 R /XYZ 40 720 0]] >>')
        embedded = child.tobytes()
    with pdf.open() as document:
        document.new_page()
        page = document.new_page()
        if address in ('catalogue', 'external'):
            name = '目录中的附件'
            document.embfile_add(name, embedded, filename='nested.pdf')
            key = pdf.get_pdf_str(name)
            target = f'/N {key}'
            if address == 'catalogue':
                catalog = document.pdf_catalog()
                names = document.xref_get_key(catalog, 'Names/EmbeddedFiles/Names')[1]
                node = document.get_new_xref()
                document.update_object(node, f'<< /Limits [{key} {key}] /Names {names} >>')
                document.xref_set_key(catalog, 'Names/EmbeddedFiles', f'<< /Kids [{node} 0 R] >>')
        else:
            page.add_text_annot(pdf.Point(30, 30), 'A separate annotation')
            clip = page.add_file_annot(pdf.Point(70, 180), embedded, 'nested.pdf')
            if address == 'page-index':
                xrefs = [xref for xref, _, _ in page.annot_xrefs()]
                target = f'/P 1 /A {xrefs.index(clip.xref)}'
            else:
                document.xref_set_key(document.pdf_catalog(), 'Names/Dests',
                                      f'<< /Names [(attachment-page) [{page.xref} 0 R /Fit]] >>')
                target = f'/P (attachment-page) /A {pdf.get_pdf_str(clip.info["id"])}'
        page = document[0]
        page.insert_link({'kind': pdf.LINK_URI, 'from': area, 'uri': 'https://example.invalid/'})
        page = document.reload_page(page)
        xref = page.get_links()[0]['xref']
        destination = '[1 /Fit]' if address == 'page-index' else '(chapter)'
        external = '/F (outside.pdf)' if address == 'external' else ''
        document.xref_set_key(xref, 'A', f'<< /S /GoToE /D {destination} {external} /T << /R /C {target} >> >>')
        document.save(path)
    return area, xref, embedded


@pytest.mark.parametrize('address', ('catalogue', 'page-index', 'page-name', 'external'))
def test_body_link_opens_embedded_pdf_at_destination(reader, tmp_path, monkeypatch, address):
    path = tmp_path / 'linked.pdf'
    area, xref, embedded = write_linked_pdf(path, address)

    launches, warnings = [], []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: launches.append(url) or True)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[2]))
    assert reader.open_files_in_tabs([str(path)])
    ref = reader.ws.pages[0]
    reader.store.prepare_annotations(ref)
    if address == 'catalogue':
        draft = ref.edited(PageEdit('note', points=((40, 60),), text='Draft note'))
        summary = reader.store.prepare_annotations(draft)
        link, = [link for link in summary['links'] if link.get('embedded_pdf')]
        assert link['embedded_pdf'].attachment == xref
        assert link['from'] == pytest.approx(tuple(area))
    view = reader.preview_view
    origin = view.page_rects[0].topLeft()
    position = view.mapFromScene(origin + QPointF((area.x0 + area.x1) / 2, (area.y0 + area.y1) / 2))
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=position)
    assert not launches and not warnings
    if address == 'external':
        assert Path(reader.ws.path) == path
        assert reader.statusBar().currentMessage() == '此链接类型暂不支持打开。'
    else:
        opened = Path(reader.ws.path)
        assert opened.name == 'nested.pdf' and opened.read_bytes() == embedded
        assert reader.ws.preview_index() == 1


@pytest.mark.parametrize(('operation', 'address'), (
    ('save', 'catalogue'), ('save', 'page-index'), ('reorder', 'catalogue'),
    ('reorder', 'page-index'), ('reorder', 'page-name'), ('merge', 'catalogue'),
    ('merge', 'page-index'),
))
def test_saved_body_link_resolves_to_original_embedded_pdf(tmp_path, operation, address):
    source = tmp_path / 'source.pdf'
    area, _, embedded = write_linked_pdf(source, address)
    destination = tmp_path / 'saved.pdf'
    store = PdfStore()
    try:
        pages = store.load(str(source))
        expected = {0: embedded}
        if operation == 'save':
            if address == 'page-index':
                # Deleting a note before the attachment shifts the original annotation index.
                annotations = store.prepare_annotations(pages[1])['annotations']
                note = next(item['id'] for item in annotations if item['kind'] == pdf.PDF_ANNOT_TEXT)
                pages[1] = pages[1].edited(PageEdit('delete', annotation_id=note))
            else:
                pages[0] = pages[0].edited(PageEdit('note', points=((40, 60),), text='Saved note'))
        elif operation == 'reorder':
            pages.reverse()
            expected = {1: embedded}
        else:
            second = tmp_path / 'other.pdf'
            _, _, other = write_linked_pdf(second, address)
            expected[len(pages)] = other
            pages.extend(store.load(str(second)))
        store.write(pages, str(destination))
        reopened = store.load(str(destination))
        for index, data in expected.items():
            ref = reopened[index]
            link, = [item for item in store.prepare_annotations(ref)['links'] if item.get('embedded_pdf')]
            assert link['from'] == pytest.approx(tuple(area))
            target = link['embedded_pdf']
            assert target.destination == (1 if address == 'page-index' else 'chapter')
            assert store.read_linked_pdf(ref, target, reopened) == ('nested.pdf', data)
    finally:
        store.close_all()
