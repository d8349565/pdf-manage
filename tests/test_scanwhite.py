"""Generated samples only. The user's photograph is checked locally outside Git."""
import hashlib
from pathlib import Path
from time import monotonic

from PIL import Image, ImageDraw, ImageStat
import pymupdf as p
import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt, QUrl, QMimeData, QThreadPool
from PyQt6.QtGui import QDropEvent, QImage, QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QFileDialog, QMessageBox

from app.output import OperationCancelled
from app.pdfio import PdfError, PdfStore
from app.scanwhite import FULL_PAGE, PaperProposal, crop_paper, inset_region, prepare_whitening, recommend_paper, valid_quad, whiten_image
from app.scanwhiteui import WhiteningDialog
from app.ui import MainWindow
from app.workspace import Workspace


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


def scan():
    image=Image.new('RGB',(500,700),(175,179,182))
    draw=ImageDraw.Draw(image)
    for x in range(500):
        value=130+round(x/500*70)
        draw.line((x,0,x,699),fill=(value,value+3,value+6))
    draw.text((50,70),'Printed letters and faint pencil',fill=(15,15,15),font_size=21)
    draw.line((50,170,380,170),fill=(105,108,111),width=2)
    draw.line((50,220,80,250,160,190),fill=(170,30,40),width=4)
    return image


@pytest.mark.parametrize('mode',['color','bw'])
def test_local_background_removal_preserves_pencil_and_strokes(mode):
    image=scan(); result=whiten_image(image,mode=mode)
    assert result.size==image.size
    assert ImageStat.Stat(result.crop((250,300,400,500))).mean[0]>248
    assert result.convert('L').getpixel((150,170))<210
    assert result.convert('L').getpixel((65,235))<180
    if mode=='bw':
        assert result.mode=='L'
    else:
        r,g,b=result.getpixel((65,235)); assert r>g+50 and r>b+30


@pytest.mark.parametrize('offset',[0,20,45])
def test_paper_recommendation_on_independent_generated_photos(offset):
    image=Image.new('RGB',(600,850),(80+offset,45,25))
    corners=((70,80),(555,100),(570,790),(45,810))
    draw=ImageDraw.Draw(image)
    draw.polygon(corners,fill=(160+offset,163+offset,164+offset))
    draw.text((130,180),'Document sample',fill='black',font_size=26)
    proposal=recommend_paper(image)
    assert proposal.quad!=FULL_PAGE and valid_quad(proposal.quad)
    for (x,y),(expected_x,expected_y) in zip(proposal.quad,corners):
        assert abs(x-expected_x/600)<.045 and abs(y-expected_y/850)<.045


def test_uncertain_paper_keeps_whole_page_and_invalid_regions_are_rejected():
    assert recommend_paper(Image.new('RGB',(500,700),'white')).quad==FULL_PAGE
    with pytest.raises(ValueError):
        crop_paper(scan(),((0,0),(1,1),(1,0),(0,1)))
    with pytest.raises(ValueError):
        whiten_image(scan(),strength=101)
    with pytest.raises(OperationCancelled):
        whiten_image(scan(),cancel=lambda:True)


def test_inner_trim_is_explicit_and_preserves_corner_order():
    quad=inset_region(FULL_PAGE,.005)
    assert quad==((.005,.005),(.995,.005),(.995,.995),(.005,.995))
    assert valid_quad(quad)
    with pytest.raises(ValueError):
        inset_region(FULL_PAGE,.5)


def test_projective_crop_preserves_corners_without_hardcoded_photo_positions():
    image=Image.new('RGB',(500,700),'white'); draw=ImageDraw.Draw(image)
    quad=((.12,.1),(.9,.14),(.94,.88),(.08,.92))
    colors=('red','green','blue','black')
    for (x,y),color in zip(quad,colors):
        draw.ellipse((x*499-12,y*699-12,x*499+12,y*699+12),fill=color)
    result=crop_paper(image,quad)
    assert result.width<500 and result.height<700
    for point,color in zip(((0,0),(result.width-1,0),(result.width-1,result.height-1),(0,result.height-1)),colors):
        assert result.getpixel(point)==Image.new('RGB',(1,1),color).getpixel((0,0))


def test_prepare_save_reopen_undo_and_source_protection(qapp,tmp_path):
    image_path=tmp_path/'scan.png'; scan().save(image_path)
    original=hashlib.sha256(image_path.read_bytes()).hexdigest()
    store=PdfStore(); ws=Workspace()
    try:
        pages=store.load(str(image_path)); ws.insert_pages(pages)
        source_pdf=tmp_path/'source.pdf'; store.write(pages,str(source_pdf))
        ws.open_replace(store.load(str(source_pdf)),str(source_pdf))
        before=tuple(ws.pages)
        changes=prepare_whitening(store,before,[0],mode='bw')
        assert tuple(ws.pages)==before
        ws.edit_pages(changes,'文档漂白',as_copy=True)
        assert ws.path is None and ws.blocks_save_to(str(source_pdf))
        with pytest.raises(PdfError,match='另存副本'):
            store.write(ws.pages,str(source_pdf))
        output=tmp_path/'cleaned.pdf'; store.write(ws.pages,str(output))
        with p.open(output) as document:
            assert len(document)==1 and document[0].get_images()
            pix=document[0].get_pixmap(colorspace=p.csGRAY)
            assert max(pix.samples)>248
        ws.undo_stack.undo(); assert tuple(ws.pages)==before and ws.path==str(source_pdf)
        ws.undo_stack.redo(); assert ws.path is None
        assert hashlib.sha256(image_path.read_bytes()).hexdigest()==original
    finally:
        store.close_all()


def test_batch_failure_and_cancel_do_not_apply_partial_pages(qapp,tmp_path):
    path=tmp_path/'notes.pdf'
    with p.open() as doc:
        doc.new_page(width=200,height=300)
        page=doc.new_page(width=200,height=300); page.add_text_annot((30,40),'Keep this note')
        doc.save(path)
    store=PdfStore(); ws=Workspace()
    try:
        ws.open_replace(store.load(str(path)),str(path)); before=tuple(ws.pages)
        with pytest.raises(ValueError,match='批注'):
            prepare_whitening(store,before,[0,1])
        assert tuple(ws.pages)==before and not ws.dirty
        with pytest.raises(OperationCancelled):
            prepare_whitening(store,before,[0],cancel=lambda:True)
        assert tuple(ws.pages)==before
    finally:
        store.close_all()


@pytest.fixture
def host(qapp,monkeypatch):
    warnings=[]
    monkeypatch.setattr(QMessageBox,'warning',lambda *args:warnings.append(args[-1]))
    window=MainWindow(); window.resize(1180,800); window.show(); qapp.processEvents()
    yield window,warnings
    QThreadPool.globalInstance().waitForDone(10000)
    for tab in window._tabs:
        tab.ws.reset()
    window.store.close_all(); window.close(); qapp.processEvents()


def wait(qapp,condition):
    deadline=monotonic()+12
    while not condition() and monotonic()<deadline:
        qapp.processEvents(); QTest.qWait(10)
    assert condition()


def drop(window,paths):
    mime=QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    event=QDropEvent(QPointF(500,350),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    window.dropEvent(event)
    return event


def pdf(path):
    with p.open() as doc:
        doc.new_page(width=300,height=400).insert_text((30,60),'Generated PDF')
        doc.save(path)


@pytest.mark.parametrize('image_first',[False,True])
def test_pdf_and_image_continuous_drop_and_single_step_undo(host,qapp,tmp_path,image_first):
    window,warnings=host
    picture=tmp_path/'photo.png'; scan().save(picture)
    document=tmp_path/'sample.pdf'; pdf(document)
    first,second=(picture,document) if image_first else (document,picture)
    assert drop(window,[first]).isAccepted(); qapp.processEvents()
    assert len(window.ws.pages)==1
    assert drop(window,[second]).isAccepted(); qapp.processEvents()
    assert len(window._tabs)==1 and len(window.ws.pages)==2
    assert [Path(ref.source_path).suffix for ref in window.ws.pages]==[first.suffix,second.suffix]
    window.ws.undo_stack.undo(); assert len(window.ws.pages)==1
    window.ws.undo_stack.redo(); assert len(window.ws.pages)==2
    assert not warnings


def test_mixed_batch_order_tiff_frames_and_bad_image_are_atomic(host,qapp,tmp_path,monkeypatch):
    window,warnings=host
    document=tmp_path/'sample.pdf'; pdf(document)
    picture=tmp_path/'photo.tiff'
    scan().save(picture,save_all=True,append_images=[Image.new('RGB',(400,300),'white')])
    assert drop(window,[document,picture]).isAccepted(); qapp.processEvents()
    assert len(window._tabs)==1 and len(window.ws.pages)==3
    assert [ref.source_page for ref in window.ws.pages]==[0,0,1]
    before=tuple(window.ws.pages)
    corrupt=tmp_path/'corrupt.png'; corrupt.write_bytes(b'bad image')
    assert not window.insert_dropped_files([str(document),str(corrupt)],at_index=1)
    assert tuple(window.ws.pages)==before and warnings
    monkeypatch.setattr(QFileDialog,'getOpenFileNames',lambda *args:([str(document),str(corrupt)],''))
    window.insert_files()
    assert tuple(window.ws.pages)==before


def test_whitening_preview_adjustment_mode_scroll_and_cancel(host,qapp,tmp_path):
    window,warnings=host
    picture=tmp_path/'scan.png'; scan().save(picture)
    assert window.open_files_in_tabs([str(picture),str(picture)])
    window.ws.select_every_page(); before=tuple(window.ws.pages)
    dialog=WhiteningDialog(window); dialog.show()
    wait(qapp,lambda:dialog.results.get(0) is not None and not dialog.busy)
    assert dialog.cleaned.images is dialog.original.images
    assert dialog.cleaned.image_key(0)==dialog.original.image_key(0)
    dialog.proposals[1]=PaperProposal(inset_region(FULL_PAGE),.8,'Generated unseen recommendation')
    assert dialog.regions()[1]==FULL_PAGE
    assert dialog.strength_panel.isHidden() and dialog.coordinate_panel.isHidden()
    assert not dialog.original_box.isVisible() and dialog.cleaned_box.isVisible()
    assert dialog.cleaned.fit_page
    QTest.mouseClick(dialog.crop_button,Qt.MouseButton.LeftButton);qapp.processEvents()
    assert dialog.original.isVisible() and not dialog.cleaned.isVisible()
    row=dialog.original.page_model.index(0); area=dialog.original.area(row)
    start=QPoint(round(area.x()+area.width()*.12),round(area.y()+area.height()*.12))
    end=QPoint(round(area.x()+area.width()*.9),round(min(area.bottom(),dialog.original.viewport().height()-2)))
    QTest.mousePress(dialog.original.viewport(),Qt.MouseButton.LeftButton,pos=start)
    QTest.mouseMove(dialog.original.viewport(),end)
    QTest.mouseRelease(dialog.original.viewport(),Qt.MouseButton.LeftButton,pos=end)
    assert 0 in dialog.manual
    QTest.mouseClick(dialog.crop_button,Qt.MouseButton.LeftButton);qapp.processEvents()
    wait(qapp,lambda:dialog.results.get(0) is not None and not dialog.busy)
    region=dialog.manual[0]
    QTest.mouseClick(dialog.advanced,Qt.MouseButton.LeftButton);qapp.processEvents()
    assert dialog.trim.isVisible()
    QTest.mouseClick(dialog.trim,Qt.MouseButton.LeftButton)
    wait(qapp,lambda:dialog.results.get(0) is not None and not dialog.busy)
    assert dialog.manual[0]!=region
    region=dialog.manual[0]
    dialog.mode.setCurrentIndex(1)
    wait(qapp,lambda:dialog.results.get(0) is not None and not dialog.busy)
    assert dialog.manual[0]==region and dialog.regions()[1]==FULL_PAGE
    assert dialog.straighten.isChecked()
    dialog.straighten.setChecked(False)
    wait(qapp,lambda:dialog.results.get(0) is not None and not dialog.busy)
    assert dialog.manual[0]==region
    QTest.mouseClick(dialog.compare,Qt.MouseButton.LeftButton);qapp.processEvents()
    dialog.original.verticalScrollBar().setValue(1); qapp.processEvents()
    assert dialog.cleaned.verticalScrollBar().value()==dialog.original.verticalScrollBar().value()
    previous=dict(dialog.results)
    dialog.finished_preview(dialog.generation-1,0,None,QImage(), '')
    assert dict(dialog.results)==previous
    dialog.reject(); assert dialog.closed and dialog.cancel.is_set()
    assert tuple(window.ws.pages)==before and not warnings
    dialog.deleteLater()


@pytest.mark.parametrize('kind',['png','pdf'])
def test_real_apply_button_is_atomic_and_undoable(host,qapp,tmp_path,monkeypatch,kind):
    import app.scanwhiteui as module
    window,warnings=host
    picture=tmp_path/'scan.png'; scan().save(picture)
    if kind=='pdf':
        image=picture
        picture=tmp_path/'original.pdf'
        with p.open() as doc:
            page=doc.new_page(width=300,height=420)
            page.insert_image(page.rect,filename=str(image))
            doc.set_metadata({'title':'Generated scan title'})
            doc.set_toc([[1,'Chapter',1]])
            doc.set_page_labels([dict(startpage=0,prefix='Form-',style='D',firstpagenum=3)])
            doc.embfile_add('reference.txt',b'generated attachment')
            doc.save(picture)
    window.open_files_in_tabs([str(picture)])
    before=tuple(window.ws.pages)
    def accept_ready(dialog):
        dialog.show()
        wait(qapp,lambda:dialog.results.get(0) is not None and not dialog.busy)
        dialog.straighten.setChecked(True)
        wait(qapp,lambda:dialog.results.get(0) is not None and not dialog.busy)
        QTest.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok),Qt.MouseButton.LeftButton)
        assert dialog.closed
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(module.WhiteningDialog,'exec',accept_ready)
    module.open_whitening(window)
    assert tuple(window.ws.pages)!=before and window.ws.path is None
    if kind=='pdf':
        output=tmp_path/'cleaned-with-properties.pdf'
        window._write_pdf(list(window.ws.pages),str(output),'Test save')
        with p.open(output) as doc:
            assert doc.metadata['title']=='Generated scan title'
            assert doc.get_toc()==[[1,'Chapter',1]]
            assert doc[0].get_label()=='Form-3'
            assert doc.embfile_get('reference.txt')==b'generated attachment'
    window.ws.undo_stack.undo(); assert tuple(window.ws.pages)==before
    window.ws.undo_stack.redo(); assert tuple(window.ws.pages)!=before
    assert not warnings


def test_invalid_range_or_corners_cannot_be_applied(host,qapp,tmp_path):
    window,_=host
    picture=tmp_path/'scan.png'; scan().save(picture)
    window.open_files_in_tabs([str(picture)])
    dialog=WhiteningDialog(window); dialog.show()
    wait(qapp,lambda:dialog.results.get(0) is not None and not dialog.busy)
    dialog.corner_inputs[0][0].setValue(100)
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    assert not dialog.save_image.isEnabled()
    dialog.scope.mode.setCurrentIndex(dialog.scope.mode.findData('range'))
    dialog.scope.range.setText('99')
    qapp.processEvents()
    assert not dialog.original.rows and not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    dialog.reject(); dialog.deleteLater()


@pytest.mark.parametrize('size',[(900,700),(1380,900)])
@pytest.mark.parametrize('landscape',[False,True])
def test_enhancement_defaults_to_an_entire_page_with_optional_comparison(host,qapp,tmp_path,size,landscape):
    window,_=host
    path=tmp_path/'generated-layout.png'
    image=scan()
    if landscape:image=image.rotate(90,expand=True)
    image.save(path)
    window.open_files_in_tabs([str(path)])
    dialog=WhiteningDialog(window);dialog.resize(*size);dialog.show()
    wait(qapp,lambda:dialog.results.get(0) is not None and not dialog.busy)
    assert dialog.windowTitle()=='扫描件增强'
    assert dialog.cleaned.isVisible() and not dialog.original.isVisible()
    assert dialog.strength_panel.isHidden() and dialog.coordinate_panel.isHidden()
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).text()=='应用到文档'
    rectangle=dialog.cleaned.visualRect(dialog.cleaned.page_model.index(0))
    paper=dialog.cleaned.paper_rect(0,rectangle)
    assert paper.left()>=0 and paper.top()>=0
    assert paper.right()<=dialog.cleaned.viewport().width() and paper.bottom()<=dialog.cleaned.viewport().height()
    assert dialog.straighten.isChecked()
    QTest.mouseClick(dialog.compare,Qt.MouseButton.LeftButton);qapp.processEvents()
    assert dialog.original.isVisible() and dialog.cleaned.isVisible()
    QTest.mouseClick(dialog.detail,Qt.MouseButton.LeftButton);qapp.processEvents()
    assert not dialog.original.fit_page and not dialog.cleaned.fit_page
    QTest.mouseClick(dialog.detail,Qt.MouseButton.LeftButton);qapp.processEvents()
    assert dialog.cleaned.fit_page
    dialog.reject();dialog.deleteLater()


def test_enhancement_wheel_changes_one_full_page_and_keeps_views_in_sync(host,qapp,tmp_path):
    from PyQt6.QtGui import QWheelEvent
    window,_=host
    path=tmp_path/'generated-multiple.png';scan().save(path)
    window.open_files_in_tabs([str(path)]*3);window.ws.select_every_page()
    dialog=WhiteningDialog(window);dialog.resize(1200,820);dialog.show()
    wait(qapp,lambda:dialog.results.get(0) is not None and not dialog.busy)
    point=dialog.cleaned.viewport().rect().center()
    event=QWheelEvent(QPointF(point),QPointF(dialog.cleaned.viewport().mapToGlobal(point)),QPoint(),QPoint(0,-120),
        Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.ScrollUpdate,False)
    QApplication.sendEvent(dialog.cleaned.viewport(),event)
    wait(qapp,lambda:dialog.current_row()==1 and dialog.results.get(1) is not None)
    assert dialog.cleaned.verticalScrollBar().value()==1
    QTest.mouseClick(dialog.compare,Qt.MouseButton.LeftButton);qapp.processEvents()
    assert dialog.original.verticalScrollBar().value()==dialog.cleaned.verticalScrollBar().value()
    dialog.reject();dialog.deleteLater()


@pytest.mark.parametrize('size',[(940,760),(1380,900)])
@pytest.mark.parametrize('landscape',[False,True])
def test_all_corners_can_be_grabbed_outside_paper_without_jumping(host,qapp,tmp_path,size,landscape):
    window,_=host
    picture=tmp_path/'corner-targets.png';image=scan()
    if landscape:image=image.rotate(90,expand=True)
    image.save(picture);window.open_files_in_tabs([str(picture)])
    dialog=WhiteningDialog(window);dialog.resize(*size);dialog.show()
    try:
        wait(qapp,lambda:dialog.results.get(0) is not None and not dialog.busy)
        dialog.crop_button.setChecked(True);qapp.processEvents()
        view=dialog.original;viewport=view.viewport()
        dialog.activateWindow();view.setFocus();qapp.processEvents()
        for corner,(dx,dy) in enumerate(((1,1),(-1,1),(-1,-1),(1,-1))):
            dialog.region_changed(0,FULL_PAGE);qapp.processEvents()
            area=view.area(view.page_model.index(0));point=view.handles(0,area)[corner]
            grab=QPoint(round(point.x()-dx*14),round(point.y()-dy*14))
            assert viewport.rect().contains(grab) and not area.contains(QPointF(grab))
            assert view.handle_at(QPointF(grab))==(0,corner)
            motion=QMouseEvent(QEvent.Type.MouseMove,QPointF(grab),QPointF(viewport.mapToGlobal(grab)),
                Qt.MouseButton.NoButton,Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(viewport,motion);qapp.processEvents()
            assert view.hover_handle==(0,corner)
            assert view.cursor().shape()==Qt.CursorShape.OpenHandCursor
            QTest.mousePress(viewport,Qt.MouseButton.LeftButton,pos=grab)
            assert view.drag[2]==corner and view.regions[0]==FULL_PAGE
            assert view.cursor().shape()==Qt.CursorShape.ClosedHandCursor
            end=grab+QPoint(dx*20,dy*20)
            QTest.mouseMove(viewport,end);qapp.processEvents()
            QTest.mouseRelease(viewport,Qt.MouseButton.LeftButton,pos=end)
            expected=(FULL_PAGE[corner][0]+dx*20/area.width(),FULL_PAGE[corner][1]+dy*20/area.height())
            assert dialog.manual[0][corner]==pytest.approx(expected)
            assert all(dialog.manual[0][i]==FULL_PAGE[i] for i in range(4) if i!=corner)
            assert not dialog.pump.isActive()
        # Keyboard precision follows the selected corner, with a larger Shift step.
        before=dialog.manual[0][3]
        QTest.keyClick(view,Qt.Key.Key_Right)
        QTest.keyClick(view,Qt.Key.Key_Up,Qt.KeyboardModifier.ShiftModifier)
        assert dialog.manual[0][3]==pytest.approx((before[0]+1/area.width(),before[1]-10/area.height()))
    finally:
        dialog.reject();dialog.deleteLater()


def test_crop_batches_enhancement_and_reuses_scaled_source_during_drag(host,qapp,tmp_path,monkeypatch):
    import app.scanwhiteui as module
    window,_=host
    picture=tmp_path/'crop-preview-work.png';scan().save(picture)
    window.open_files_in_tabs([str(picture)])
    dialog=WhiteningDialog(window);dialog.resize(1380,900);dialog.show()
    try:
        wait(qapp,lambda:dialog.results.get(0) is not None and not dialog.busy)
        QThreadPool.globalInstance().waitForDone(10000)
        jobs=[];renders=[];scales=[]
        clean=module.whiten_image;render=window.store.render_qimage;scale=QImage.scaled
        def tracked_clean(*args,**kwargs):
            jobs.append(True);return clean(*args,**kwargs)
        def tracked_render(*args,**kwargs):
            renders.append(True);return render(*args,**kwargs)
        def tracked_scale(image,*args,**kwargs):
            scales.append(True);return scale(image,*args,**kwargs)
        monkeypatch.setattr(module,'whiten_image',tracked_clean)
        monkeypatch.setattr(window.store,'render_qimage',tracked_render)
        monkeypatch.setattr(QImage,'scaled',tracked_scale)
        dialog.crop_button.setChecked(True);qapp.processEvents()
        dialog.region_changed(0,FULL_PAGE);qapp.processEvents()
        view=dialog.original;viewport=view.viewport()
        wait(qapp,lambda:bool(view.scaled_papers))
        initial_scales=len(scales)
        for corner,(dx,dy) in enumerate(((1,1),(-1,1),(-1,-1),(1,-1))):
            area=view.area(view.page_model.index(0));point=view.handles(0,area)[corner].toPoint()
            QTest.mousePress(viewport,Qt.MouseButton.LeftButton,pos=point)
            for step in range(1,41):
                QTest.mouseMove(viewport,point+QPoint(dx*step,dy*step));qapp.processEvents()
            QTest.mouseRelease(viewport,Qt.MouseButton.LeftButton,pos=point+QPoint(dx*40,dy*40))
            assert dialog.manual[0][corner]==pytest.approx((FULL_PAGE[corner][0]+dx*40/area.width(),
                                                          FULL_PAGE[corner][1]+dy*40/area.height()))
            # Let the old debounce interval elapse between adjusting corners.
            QTest.qWait(150);qapp.processEvents()
            assert not dialog.pump.isActive() and not dialog.busy and not jobs
            assert not renders and len(scales)==initial_scales
            assert dialog.results=={} and len(view.scaled_papers)<=2
        assert dialog.save_image.isEnabled() is False
        finish=dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert finish.text()=='完成裁纸' and finish.isEnabled()
        QTest.mouseClick(finish,Qt.MouseButton.LeftButton)
        assert not dialog.closed and not dialog.crop_button.isChecked()
        wait(qapp,lambda:dialog.results.get(0) is not None and not dialog.busy)
        assert len(jobs)==1 and not renders
        assert finish.text()=='应用到文档' and dialog.save_image.isEnabled()
    finally:
        dialog.reject();dialog.deleteLater()
