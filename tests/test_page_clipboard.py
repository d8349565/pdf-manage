"""Use generated QMimeData only; never read or write the system clipboard."""
from pathlib import Path

import pymupdf as p
import pytest
from PyQt6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl, QThreadPool
from PyQt6.QtGui import QColor, QDropEvent, QImage, QKeySequence
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit, QMenu, QMessageBox

from app import pageclipboard
from app.output import OperationCancelled
from app.pageclipboard import read_pages
from app.pdfio import PdfStore
from app.ui import MainWindow
from app.workspace import PageEdit, Workspace


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


def pdf(path,count=1):
    with p.open() as doc:
        for row in range(count):
            doc.new_page(width=300,height=400).insert_text((30,60),f'{path.stem}-{row}')
        doc.save(path)


def bitmap():
    image=QImage(240,320,QImage.Format.Format_ARGB32)
    image.fill(QColor(150,30,50,170))
    return image


def files(*paths):
    mime=QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    return mime


@pytest.fixture
def host(qapp,monkeypatch):
    state={'mime':QMimeData(),'reads':0,'writes':0}
    original=read_pages
    def fake_clipboard():
        state['reads']+=1
        return original(state['mime'])
    monkeypatch.setattr(pageclipboard,'read_pages',fake_clipboard)
    original_write=pageclipboard.write_pages
    def fake_write(pages):
        state['writes']+=1
        return original_write(pages,set_mime=lambda mime:state.update(mime=mime))
    monkeypatch.setattr(pageclipboard,'write_pages',fake_write)
    warnings=[]
    monkeypatch.setattr(QMessageBox,'warning',lambda *args:warnings.append(args[-1]))
    window=MainWindow(); window.resize(1180,900); window.show(); window.activateWindow()
    qapp.processEvents()
    yield window,state,warnings
    QThreadPool.globalInstance().waitForDone(10000)
    for tab in window._tabs:
        tab.ws.reset()
    window.store.close_all(); window.close(); qapp.processEvents()


def test_file_order_takes_precedence_over_bitmap_and_cut_marker(tmp_path,qapp):
    first=tmp_path/'中文 文件.pdf'; second=tmp_path/'picture.png'
    pdf(first); bitmap().save(str(second))
    mime=files(first,second); mime.setImageData(bitmap())
    mime.setData('Preferred DropEffect',b'\x02\x00\x00\x00')
    paths,image=read_pages(mime)
    assert tuple(Path(value) for value in paths)==(first,second) and image is None
    assert first.is_file() and second.is_file()


def test_bitmap_is_an_owned_snapshot(qapp):
    mime=QMimeData(); mime.setImageData(bitmap())
    paths,image=read_pages(mime)
    replacement=bitmap(); replacement.fill(QColor('blue')); mime.setImageData(replacement)
    assert not paths and image.pixelColor(100,100)!=QColor('blue')
    assert image.hasAlphaChannel()


@pytest.mark.parametrize('kind',['text','remote_url','folder','unsupported','null_image'])
def test_unsupported_clipboards_do_not_import_arbitrary_data(kind,tmp_path,qapp):
    mime=QMimeData()
    if kind=='text': mime.setText('Ordinary text, not a page')
    elif kind=='remote_url': mime.setUrls([QUrl('https://example.com/image.png')])
    elif kind=='folder': mime=files(tmp_path)
    elif kind=='unsupported':
        path=tmp_path/'note.txt'; path.write_text('Generated'); mime=files(path)
    else: mime.setImageData(QImage())
    with pytest.raises(ValueError): read_pages(mime)


def test_ctrl_v_inserts_file_batch_after_selection_and_undoes_once(host,tmp_path,qapp):
    window,state,warnings=host
    base=tmp_path/'base.pdf'; incoming=tmp_path/'incoming.pdf'; picture=tmp_path/'picture.png'
    pdf(base,4); pdf(incoming,2); bitmap().save(str(picture))
    originals={path:path.read_bytes() for path in (base,incoming,picture)}
    window.open_files_in_tabs([str(base)]); window.ws.set_selection([1,2],current=2); window._sync_selection()
    state['mime']=files(incoming,picture)
    window.left_view.setFocus(); qapp.processEvents()
    QTest.keyClick(window.left_view,Qt.Key.Key_V,Qt.KeyboardModifier.ControlModifier); qapp.processEvents()
    assert state['reads']==1 and len(window.ws.pages)==7 and len(window._tabs)==1
    assert [(Path(ref.source_path).name,ref.source_page) for ref in window.ws.pages]==[
        ('base.pdf',0),('base.pdf',1),('base.pdf',2),('incoming.pdf',0),('incoming.pdf',1),('picture.png',0),('base.pdf',3)]
    window.ws.undo_stack.undo(); assert len(window.ws.pages)==4
    window.ws.undo_stack.redo(); assert len(window.ws.pages)==7
    assert all(path.read_bytes()==data for path,data in originals.items()) and not warnings


def test_paste_into_empty_workspace_combines_files_in_one_tab(host,tmp_path):
    window,state,warnings=host
    a,b=tmp_path/'a.pdf',tmp_path/'b.pdf'; pdf(a);pdf(b,2)
    state['mime']=files(a,b)
    assert window.paste_pages()
    assert len(window.ws.pages)==3 and len(window._tabs)==1 and window.ws.path is None
    assert window.ws.blocks_save_to(str(a)) and window.ws.blocks_save_to(str(b))
    assert not warnings


def test_bitmap_survives_clipboard_change_undo_save_and_cache_cleanup(host,tmp_path):
    window,state,warnings=host
    mime=QMimeData(); mime.setImageData(bitmap()); state['mime']=mime
    assert window.paste_pages()
    original=window.ws.pages[0]; scratch=Path(original.source_path)
    assert scratch.is_file() and window.ws.path is None
    state['mime']=QMimeData()
    window.ws.undo_stack.undo(); assert not window.ws.pages and scratch.is_file()
    window.ws.undo_stack.redo(); assert window.ws.pages[0]==original
    output=tmp_path/'pasted.pdf'
    window._write_pdf(list(window.ws.pages),str(output),'Generated clipboard save')
    with p.open(output) as doc:
        assert len(doc)==1 and doc[0].get_images()
    window.ws.reset(); window._release_unused_docs()
    assert not scratch.exists() and not window.store._owned_workspaces and not warnings


def test_cancelled_bitmap_preparation_releases_owned_cache(qapp):
    from app.storage import directory
    store=PdfStore(); cancelled=False
    def progress(*_):
        nonlocal cancelled
        cancelled=True
    try:
        with pytest.raises(OperationCancelled):
            store.load_clipboard_image(bitmap(),progress_callback=progress,cancel=lambda:cancelled)
        assert not store._sources and not store._owned_workspaces
        assert not list(directory('cache').glob('task-clipboard-*'))
    finally:
        store.close_all()


@pytest.mark.parametrize('arrange',[False,True])
def test_click_gap_overrides_previous_selection_and_repeat_paste_follows_new_pages(host,tmp_path,qapp,arrange):
    window,state,warnings=host
    base,incoming=tmp_path/'base.pdf',tmp_path/'incoming.pdf';pdf(base,3);pdf(incoming)
    window.open_files_in_tabs([str(base)])
    window.act_pages.setChecked(True)
    if arrange: window.stack.setCurrentIndex(1)
    view=window.arrange_view if arrange else window.left_view
    window.ws.select_index(2);window._sync_selection();qapp.processEvents()
    view.scrollTo(window.model.index(1));qapp.processEvents()
    first,second=(view.visualRect(window.model.index(row)) for row in (0,1))
    if arrange and abs(first.top()-second.top())<3:
        point=QPoint(round((first.right()+second.left())/2),second.center().y())
    else:
        point=QPoint(second.center().x(),round((first.bottom()+second.top())/2))
    assert not view.indexAt(point).isValid()
    assert view.viewport().rect().contains(point)
    QTest.mouseClick(view.viewport(),Qt.MouseButton.LeftButton,pos=point);qapp.processEvents()
    assert view.paste_gap()==1
    window._sync_selection();qapp.processEvents()
    assert view.paste_gap()==1
    state['mime']=files(incoming)
    QTest.keyClick(view,Qt.Key.Key_V,Qt.KeyboardModifier.ControlModifier);qapp.processEvents()
    assert [Path(ref.source_path).name for ref in window.ws.pages]==['base.pdf','incoming.pdf','base.pdf','base.pdf']
    assert view.paste_gap() is None
    assert window.paste_pages(target_view=view)
    assert [Path(ref.source_path).name for ref in window.ws.pages]==['base.pdf','incoming.pdf','incoming.pdf','base.pdf','base.pdf']
    assert not warnings


def test_context_paste_before_first_page_and_old_tab_gap_is_not_reused(host,tmp_path,qapp):
    window,state,warnings=host
    a,b,inc=(tmp_path/name for name in ('a.pdf','b.pdf','incoming.pdf'))
    pdf(a,2);pdf(b);pdf(inc)
    window.open_files_in_tabs([str(a)]);qapp.processEvents()
    view=window.left_view; rectangle=view.visualRect(window.model.index(0))
    point=QPoint(rectangle.center().x(),rectangle.top()-2)
    menu=QMenu(view);action=view.add_page_paste_action(menu,point)
    assert '文档开头' in action.text()
    state['mime']=files(inc);action.trigger()
    assert Path(window.ws.pages[0].source_path).name=='incoming.pdf'
    view._select_paste_gap(0)
    window.open_files_in_tabs([str(b)]);qapp.processEvents()
    assert view.paste_gap() is None
    assert window.paste_pages()
    assert [Path(ref.source_path).name for ref in window.ws.pages]==['b.pdf','incoming.pdf']
    assert not warnings


def test_bad_mixed_batch_and_read_only_target_preserve_document(host,tmp_path,monkeypatch):
    window,state,warnings=host
    base,incoming,bad=(tmp_path/name for name in ('base.pdf','incoming.pdf','bad.png'))
    pdf(base);pdf(incoming);bad.write_bytes(b'not a picture')
    window.open_files_in_tabs([str(base)]);before=tuple(window.ws.pages)
    state['mime']=files(incoming,bad)
    assert not window.paste_pages() and tuple(window.ws.pages)==before and warnings
    reads=state['reads'];monkeypatch.setattr(window,'_allow_operation',lambda *args:False)
    assert not window.paste_pages() and state['reads']==reads and tuple(window.ws.pages)==before


def test_missing_file_is_checked_by_background_import(host,tmp_path):
    window,state,warnings=host
    base=tmp_path/'base.pdf';pdf(base)
    window.open_files_in_tabs([str(base)]);before=tuple(window.ws.pages)
    state['mime']=files(tmp_path/'missing.pdf')
    assert not window.paste_pages() and tuple(window.ws.pages)==before and warnings


def test_text_editor_keeps_paste_shortcut_without_reading_system_clipboard(host,qapp):
    window,state,_=host
    class Editor(QLineEdit):
        received=False
        def keyPressEvent(self,event):
            if event.matches(QKeySequence.StandardKey.Paste):
                self.received=True; event.accept(); return
            super().keyPressEvent(event)
    editor=Editor(window);editor.setGeometry(400,180,240,40);editor.show();editor.setFocus();qapp.processEvents()
    QTest.keyClick(editor,Qt.Key.Key_V,Qt.KeyboardModifier.ControlModifier);qapp.processEvents()
    assert editor.received and state['reads']==0 and not window.ws.pages


def test_dragging_whitespace_does_not_turn_rubber_band_selection_into_paste_gap(host,tmp_path,qapp):
    window,_,_=host
    path=tmp_path/'pages.pdf';pdf(path,4);window.open_files_in_tabs([str(path)])
    window.stack.setCurrentIndex(1);qapp.processEvents();view=window.arrange_view
    rectangle=view.visualRect(window.model.index(0));point=QPoint(rectangle.left()-2,rectangle.top()+15)
    QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=point)
    QTest.mouseMove(view.viewport(),point+QPoint(40,40))
    QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=point+QPoint(40,40))
    assert view.paste_gap() is None


def test_copy_selected_pages_to_same_file_is_a_stable_editable_snapshot(host,tmp_path,qapp):
    window,state,warnings=host
    path=tmp_path/'original.pdf';pdf(path,4)
    with p.open(path) as doc:
        doc[0].add_text_annot((90,100),'Original note');doc.saveIncr()
    original_bytes=path.read_bytes()
    window.open_files_in_tabs([str(path)]);window.act_pages.setChecked(True)
    window.ws.set_selection([0,2],current=2);window.rotate(90);window._sync_selection()
    window.activateWindow();window.left_view.setFocus();qapp.processEvents()
    QTest.keyClick(window.left_view,Qt.Key.Key_C,Qt.KeyboardModifier.ControlModifier);qapp.processEvents()
    assert state['writes']==1 and len(window._page_copy.pages)==2
    copied=window._page_copy.pages
    window.rotate(90)
    assert copied[0].rotation==90 and window.ws.pages[0].rotation==180
    assert window.paste_pages(at_index=1,target_view=window.left_view)
    assert len(window.ws.pages)==6
    assert [window.ws.pages[i].rotation for i in (1,2)]==[90,90]
    assert len({ref.page_id for ref in window.ws.pages})==6
    window.ws.select_index(1);window.ws.rotate(-90)
    assert window.ws.pages[1].rotation==0 and window.ws.pages[0].rotation==180
    output=tmp_path/'copied-pages.pdf'
    window._write_pdf(list(window.ws.pages),str(output),'Generated internal copy')
    with p.open(output) as doc:
        assert 'original-0' in doc[1].get_text()
        assert next(doc[1].annots()).info['content']=='Original note'
    assert path.read_bytes()==original_bytes and not warnings


@pytest.mark.parametrize('arrange',[False,True])
def test_copy_pages_to_another_tab_after_source_tab_closes(host,tmp_path,qapp,arrange):
    window,state,warnings=host
    source,target=tmp_path/'source.pdf',tmp_path/'target.pdf';pdf(source,4);pdf(target)
    window.open_files_in_tabs([str(source)]);source_tab=window._active_tab_id
    window.act_pages.setChecked(True)
    if arrange:window.stack.setCurrentIndex(1)
    window.ws.set_selection([1,3],current=3);window._sync_selection()
    view=window.arrange_view if arrange else window.left_view
    view.setFocus();window.activateWindow();qapp.processEvents()
    QTest.keyClick(view,Qt.Key.Key_C,Qt.KeyboardModifier.ControlModifier);qapp.processEvents()
    assert state['writes']==1
    sid=window._page_copy.pages[0].source_id
    window.open_files_in_tabs([str(target)])
    window.close_tab(source_tab)
    assert sid in window.store._sources and len(window._tabs)==1
    assert window.paste_pages(at_index=0)
    assert [(Path(ref.source_path).name,ref.source_page) for ref in window.ws.pages]==[
        ('source.pdf',1),('source.pdf',3),('target.pdf',0)]
    window.ws.undo_stack.undo();assert len(window.ws.pages)==1
    window.ws.undo_stack.redo();assert len(window.ws.pages)==3
    assert not warnings


def test_body_text_copy_shortcut_and_editor_copy_are_not_hijacked(host,tmp_path,qapp):
    window,state,_=host
    path=tmp_path/'source.pdf';pdf(path);window.open_files_in_tabs([str(path)])
    called=[]
    window.text_selection.shortcut.activated.disconnect()
    window.text_selection.shortcut.activated.connect(lambda:called.append('text'))
    window.text_selection.shortcut.setEnabled(True)
    window.activateWindow();window.preview_view.setFocus();qapp.processEvents()
    QTest.keyClick(window.preview_view,Qt.Key.Key_C,Qt.KeyboardModifier.ControlModifier);qapp.processEvents()
    assert called==['text'] and state['writes']==0


def test_invalid_or_foreign_internal_page_tokens_do_not_paste(host):
    window,state,_=host
    for data in (b'a'*32,b'invalid',b'!'*32):
        mime=QMimeData();mime.setData(pageclipboard.PAGE_MIME,data);state['mime']=mime
        assert not window.paste_pages() and not window.ws.pages


def test_copied_clipboard_bitmap_remains_available_after_its_tab_closes(host,tmp_path):
    window,state,_=host
    mime=QMimeData();mime.setImageData(bitmap());state['mime']=mime
    assert window.paste_pages()
    source_tab=window._active_tab_id;cached=Path(window.ws.pages[0].source_path)
    window.ws.mark_saved()
    assert window.copy_pages()
    target=tmp_path/'target.pdf';pdf(target)
    window.open_files_in_tabs([str(target)]);window.close_tab(source_tab)
    assert cached.is_file()
    assert window.paste_pages() and len(window.ws.pages)==2
    assert cached.is_file()


@pytest.mark.parametrize('source_kind', ['pdf', 'bitmap'])
def test_copied_pages_survive_closing_the_last_document(host, tmp_path, source_kind):
    window, state, warnings = host
    if source_kind == 'pdf':
        source = tmp_path / 'generated-last-tab.pdf'
        pdf(source, 2)
        assert window.open_files_in_tabs([str(source)])
        window.ws.set_selection([0, 1], current=1)
    else:
        mime = QMimeData()
        mime.setImageData(bitmap())
        state['mime'] = mime
        assert window.paste_pages()
        window.ws.mark_saved()
    assert window.copy_pages()
    copied = window._page_copy.pages
    assert window.close_tab(window._active_tab_id)
    assert not window.ws.pages and len(window._tabs) == 1
    assert window.paste_pages(), window.statusBar().currentMessage()
    assert [ref.source_page for ref in window.ws.pages] == [ref.source_page for ref in copied]
    assert all(Path(ref.source_path).is_file() for ref in copied)
    window.ws.undo_stack.undo()
    assert not window.ws.pages
    window.ws.undo_stack.redo()
    assert len(window.ws.pages) == len(copied)
    window.ws.reset()
    window._page_copy = None
    window._release_unused_docs()
    assert not window.store._sources and not window.store._owned_workspaces
    assert not warnings


@pytest.mark.parametrize('arrange',[False,True])
def test_ctrl_drop_copies_selection_once_and_preserves_original_order(host,tmp_path,qapp,arrange):
    window,state,_=host
    source=tmp_path/'generated-drag.pdf';pdf(source,4)
    window.open_files_in_tabs([str(source)])
    window.act_pages.setChecked(True)
    if arrange:window.stack.setCurrentIndex(1)
    window.ws.set_selection([0,2],current=2);window._sync_selection();qapp.processEvents()
    view=window.arrange_view if arrange else window.left_view
    before=list(window.ws.pages)
    mime=window.model.mimeData([window.model.index(i) for i in (0,2)])
    event=QDropEvent(QPointF(view.visualRect(window.model.index(1)).center()),
        Qt.DropAction.CopyAction|Qt.DropAction.MoveAction,mime,Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ControlModifier)
    view.dropEvent(event);qapp.processEvents()
    assert event.isAccepted() and event.dropAction()==Qt.DropAction.CopyAction
    assert [ref.source_page for ref in window.ws.pages]==[0,0,2,1,2,3]
    assert len({ref.page_id for ref in window.ws.pages})==6
    assert state['reads']==state['writes']==0
    window.ws.undo_stack.undo();assert window.ws.pages==before
    window.ws.undo_stack.redo();assert len(window.ws.pages)==6


def test_copied_annotations_can_be_changed_and_deleted_without_affecting_the_source(qapp,tmp_path):
    source=tmp_path/'generated-note.pdf';output=tmp_path/'copied-notes.pdf'
    with p.open() as doc:
        page=doc.new_page();page.insert_text((40,60),'Original text')
        note=page.add_text_annot((80,80),'original note');token=note.info['id']
        doc.save(source)
    original=source.read_bytes();store=PdfStore()
    try:
        pages=store.load(str(source))
        first=Workspace._unique_pages(pages,pages)[0]
        second=Workspace._unique_pages(pages,pages)[0]
        first=first.edited(PageEdit('note_text',text='copy only',annotation_id=token))
        second=second.edited(PageEdit('delete',annotation_id=token))
        store.write([pages[0],first,second],str(output))
        with p.open(output) as doc:
            assert [[a.info['content'] for a in (page.annots() or ())] for page in doc]==[
                ['original note'],['copy only'],[]]
            assert all('Original text' in page.get_text() for page in doc)
    finally:
        store.close_all()
    assert source.read_bytes()==original


@pytest.mark.parametrize('background',[False,True])
def test_copied_form_batches_are_independent_but_keep_their_own_cross_page_fields(qapp,tmp_path,background):
    source=tmp_path/'generated-linked-form.pdf';output=tmp_path/'copied-forms.pdf'
    with p.open() as original,p.open() as linked:
        page=original.new_page()
        field=p.Widget();field.field_name='generated_name';field.field_type=p.PDF_WIDGET_TYPE_TEXT
        field.field_value='original';field.rect=p.Rect(50,50,200,90);page.add_widget(field)
        linked.insert_pdf(original)
        linked.insert_pdf(original,join_duplicates=True)
        linked.save(source)
    original_bytes=source.read_bytes()
    store=PdfStore()
    try:
        pages=store.load(str(source))
        copied=Workspace._unique_pages(pages,pages)
        second_copy=Workspace._unique_pages(pages,pages)
        if background:
            from app.pdfworker import write_pdf
            write_pdf(store,[copied[0],pages[0],copied[1],pages[1],*second_copy],str(output))
        else:
            store.write([copied[0],pages[0],copied[1],pages[1],*second_copy],str(output))
        with p.open(output) as doc:
            names=[next(page.widgets()).field_name for page in doc]
            assert names[0]==names[2] and names[1]==names[3] and names[4]==names[5]
            assert len({names[0],names[1],names[4]})==3
            roots=[doc.xref_get_key(next(page.widgets()).xref,'Parent')[1] for page in doc]
            assert roots[0]==roots[2] and roots[1]==roots[3] and roots[4]==roots[5]
            assert len({roots[0],roots[1],roots[4]})==3
            doc.xref_set_key(int(roots[0].split()[0]),'V',p.get_pdf_str('copy only'))
            for index in (0,2):
                doc.xref_set_key(next(doc[index].widgets()).xref,'V',p.get_pdf_str('copy only'))
            doc.saveIncr()
        with p.open(output) as doc:
            assert doc.is_form_pdf
            assert [next(page.widgets()).field_value for page in doc]==[
                'copy only','original','copy only','original','original','original']
        # Extract/reorder must also retain an interactive form catalog.
        store.write([pages[1]],str(tmp_path/'one-form-page.pdf'))
        with p.open(tmp_path/'one-form-page.pdf') as doc:
            assert doc.is_form_pdf and next(doc[0].widgets()).field_value=='original'
    finally:
        store.close_all()
    assert source.read_bytes()==original_bytes


def test_workspace_undo_signal_is_disconnected_when_window_is_destroyed(qapp,tmp_path,monkeypatch):
    import sys
    from PyQt6.QtCore import QCoreApplication,QEvent
    window=MainWindow();path=tmp_path/'source.pdf';pdf(path)
    window.open_files_in_tabs([str(path)])
    workspace=window.ws;workspace.rotate(90)
    monkeypatch.setattr(window,'_confirm_leave_all_tabs',lambda:True)
    window.close();window.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    errors=[]
    monkeypatch.setattr(sys,'excepthook',lambda *args:errors.append(args[1]))
    workspace.undo_stack.undo()
    assert not errors
