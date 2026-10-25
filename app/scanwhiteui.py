"""Confirm proposed paper corners in synchronized, continuous before/after previews."""
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path
from threading import Event
import tempfile

from PIL import Image
from PyQt6.QtCore import QObject, QPointF, QRunnable, QRectF, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPen, QPolygonF, QRegion
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QSlider, QVBoxLayout, QWidget)

from app.output import OperationCancelled, commit_file, stamp
from app.pagepreviewlist import ScrollablePagePreview, draw_fitted_page
from app.pagetoolsui import ClearComboBox, PageScope
from app.pdfio import PdfError
from app.progress import run_operation
from app.scanwhite import FULL_PAGE, inset_region, prepare_whitening, recommend_paper, render_scan, valid_quad, whiten_image


def _pil(image):
    image=image.convertToFormat(QImage.Format.Format_RGB888)
    return Image.frombytes('RGB',(image.width(),image.height()),
        image.constBits().asstring(image.sizeInBytes()),'raw','RGB',image.bytesPerLine(),1)


def _qimage(image):
    image=image.convert('RGB')
    return QImage(image.tobytes(),image.width,image.height,image.width*3,QImage.Format.Format_RGB888).copy()


class _Signals(QObject):
    ready=pyqtSignal(int,int,object,object,str)


class _Clean(QRunnable):
    def __init__(self,token,row,image,quad,mode,strength,straighten,signals,cancel):
        super().__init__()
        self.token,self.row,self.image,self.quad=token,row,image,quad
        self.mode,self.strength,self.signals,self.cancel=mode,strength,signals,cancel
        self.straighten=straighten

    def run(self):
        proposal,result,error=None,None,''
        try:
            image=_pil(self.image)
            proposal=recommend_paper(image,self.cancel.is_set) if self.quad is None else None
            result=_qimage(whiten_image(image,mode=self.mode,strength=self.strength,
                quad=self.quad if self.quad is not None else proposal.quad,
                straighten=self.straighten,cancel=self.cancel.is_set))
        except OperationCancelled:
            pass
        except Exception as exc:
            error=str(exc)
        self.signals.ready.emit(self.token,self.row,proposal,result,error)


class PaperPreview(ScrollablePagePreview):
    region_changed=pyqtSignal(int,object)

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.regions={}
        self.drag=None
        self.editing=False
        self.peer=None
        self.hover_handle=None
        self.selected_handle=None
        self.hit_radius=24
        self.scaled_papers=OrderedDict()
        self.render_when_hidden=True
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setAccessibleName('原页和纸张范围：拖动四角或框选；选中角点后可用方向键微调，Shift 加速')

    def image_key(self,row):
        width,height=self.store.page_size(self.pages[row])
        return self.generation,row,max(1,round(min(1100,1900*width/height)))

    def area(self,index):
        return self.paper_rect(self.rows[index.row()],self.visualRect(index))

    def visible_positions(self):
        if not self.isVisible() and self.peer is not None:
            return self.peer.visible_positions()
        return super().visible_positions()

    def handles(self,row,area):
        return [QPointF(area.x()+x*area.width(),area.y()+y*area.height())
                for x,y in self.regions.get(row,FULL_PAGE)]

    def handle_at(self,position):
        index=self.indexAt(position.toPoint())
        if not index.isValid():return None
        row=self.rows[index.row()];area=self.area(index)
        points=self.handles(row,area)
        distances=[(point.x()-position.x())**2+(point.y()-position.y())**2 for point in points]
        corner=min(range(4),key=distances.__getitem__)
        return (row,corner) if distances[corner]<=self.hit_radius**2 else None

    def update_handles(self,*handles):
        dirty=QRegion()
        for handle in handles:
            if handle is None or handle[0] not in self.rows:continue
            row,corner=handle;index=self.page_model.index(self.rows.index(row))
            point=self.handles(row,self.area(index))[corner]
            dirty|=QRegion(QRectF(point.x()-21,point.y()-21,42,42).toAlignedRect())
        self.viewport().update(dirty)

    def update_edges(self,row,area,old,new,corner=None):
        dirty=QRegion()
        edges=range(4) if corner is None else ((corner-1)%4,corner)
        for quad in (old,new):
            points=[QPointF(area.x()+x*area.width(),area.y()+y*area.height()) for x,y in quad]
            for edge in edges:
                rectangle=QRectF(points[edge],points[(edge+1)%4]).normalized().adjusted(-21,-21,21,21)
                dirty|=QRegion(rectangle.toAlignedRect())
        self.viewport().update(dirty)

    def draw_source(self,painter,area,image):
        dpr=painter.device().devicePixelRatioF()
        size=(max(1,round(area.width()*dpr)),max(1,round(area.height()*dpr)))
        key=(image.cacheKey(),*size,dpr)
        cached=self.scaled_papers.get(key)
        if cached is None:
            cached=image.scaled(*size,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
            cached.setDevicePixelRatio(dpr);self.scaled_papers[key]=cached
            while len(self.scaled_papers)>2:self.scaled_papers.popitem(last=False)
        else:self.scaled_papers.move_to_end(key)
        point=QPointF(area.center().x()-cached.width()/dpr/2,area.center().y()-cached.height()/dpr/2)
        painter.save();painter.setClipRect(area)
        painter.setRenderHint(painter.RenderHint.SmoothPixmapTransform,False)
        painter.drawImage(point,cached);painter.restore()

    def mousePressEvent(self,event):
        if not self.editing or event.button()!=Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        index=self.indexAt(event.position().toPoint())
        if not index.isValid():
            return super().mousePressEvent(event)
        area=self.area(index)
        handle=self.handle_at(event.position())
        if handle is None and not area.contains(event.position()):
            return super().mousePressEvent(event)
        row=self.rows[index.row()]
        quad=self.regions.get(row,FULL_PAGE)
        old=self.selected_handle;self.selected_handle=handle
        self.drag=(row,area,handle[1] if handle is not None else None,event.position(),quad)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.setCursor(Qt.CursorShape.ClosedHandCursor if handle else Qt.CursorShape.CrossCursor)
        self.update_handles(old,handle)
        event.accept()

    def mouseMoveEvent(self,event):
        if self.drag is None:
            if self.editing:
                handle=self.handle_at(event.position())
                if handle!=self.hover_handle:
                    old=self.hover_handle;self.hover_handle=handle;self.update_handles(old,handle)
                self.setCursor(Qt.CursorShape.OpenHandCursor if handle else Qt.CursorShape.CrossCursor)
            return super().mouseMoveEvent(event)
        row,area,corner,start,original=self.drag
        point=(max(0,min(1,(event.position().x()-area.x())/area.width())),
               max(0,min(1,(event.position().y()-area.y())/area.height())))
        if corner is None:
            sx,sy=(start.x()-area.x())/area.width(),(start.y()-area.y())/area.height()
            x0,x1=sorted((sx,point[0])); y0,y1=sorted((sy,point[1]))
            quad=((x0,y0),(x1,y0),(x1,y1),(x0,y1))
        else:
            point=(max(0,min(1,original[corner][0]+(event.position().x()-start.x())/area.width())),
                   max(0,min(1,original[corner][1]+(event.position().y()-start.y())/area.height())))
            quad=tuple(point if i==corner else value for i,value in enumerate(original))
        if valid_quad(quad):
            previous=self.regions.get(row,original)
            self.regions[row]=quad
            self.update_edges(row,area,previous,quad,corner)
        event.accept()

    def mouseReleaseEvent(self,event):
        if self.drag is None:
            return super().mouseReleaseEvent(event)
        row,_,_,_,original=self.drag
        self.drag=None
        quad=self.regions.get(row,original)
        if quad!=original:
            self.region_changed.emit(row,quad)
        self.hover_handle=self.handle_at(event.position())
        self.setCursor(Qt.CursorShape.OpenHandCursor if self.hover_handle else Qt.CursorShape.CrossCursor)
        self.update_handles(self.selected_handle)
        event.accept()

    def leaveEvent(self,event):
        previous=self.hover_handle;self.hover_handle=None;self.update_handles(previous)
        super().leaveEvent(event)

    def keyPressEvent(self,event):
        directions={Qt.Key.Key_Left:(-1,0),Qt.Key.Key_Right:(1,0),Qt.Key.Key_Up:(0,-1),Qt.Key.Key_Down:(0,1)}
        if self.editing and self.selected_handle is not None and event.key() in directions:
            row,corner=self.selected_handle
            if row in self.rows:
                area=self.area(self.page_model.index(self.rows.index(row)))
                dx,dy=directions[event.key()];step=10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
                old=self.regions.get(row,FULL_PAGE);x,y=old[corner]
                point=(max(0,min(1,x+dx*step/area.width())),max(0,min(1,y+dy*step/area.height())))
                new=tuple(point if i==corner else value for i,value in enumerate(old))
                if valid_quad(new):
                    self.regions[row]=new;self.update_edges(row,area,old,new,corner)
                    self.region_changed.emit(row,new)
                event.accept();return
        super().keyPressEvent(event)


class WhiteningDialog(QDialog):
    def __init__(self,host):
        super().__init__(host)
        self.host,self.pages=host,tuple(host.ws.pages)
        self.results,self.proposals,self.manual=OrderedDict(),{},{}
        self.reviewed=set()
        self.generation,self.busy,self.closed=0,False,False
        self.coordinate_valid=True
        self.cancel=Event()
        self.setWindowTitle('扫描件增强')
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint,True)
        screen=host.screen().availableGeometry()
        self.resize(min(1500,round(screen.width()*.94)),round(screen.height()*.92))
        self.move(screen.center()-self.rect().center())
        layout=QVBoxLayout(self)
        layout.setContentsMargins(14,12,14,12)
        body=QHBoxLayout();body.setSpacing(14)
        panel=QWidget();panel.setFixedWidth(235)
        controls=QVBoxLayout(panel);controls.setContentsMargins(0,0,0,0);controls.setSpacing(10)
        heading=QLabel('扫描件增强');heading.setStyleSheet('font-size: 20px; font-weight: 600;')
        controls.addWidget(heading)
        controls.addWidget(QLabel('效果'))
        self.mode=ClearComboBox()
        self.mode.addItem('保留彩色','color');self.mode.addItem('黑白打印','bw')
        self.mode.setAccessibleName('增强效果')
        self.mode.setToolTip('去除灰底与阴影，保留文字、彩章和笔迹；黑白打印保留浅灰笔迹。')
        controls.addWidget(self.mode)
        controls.addWidget(QLabel('处理范围'))
        self.scope=PageScope(self.pages,host.ws.target_indices())
        controls.addWidget(self.scope)
        self.straighten=QCheckBox('自动拉正')
        self.straighten.setChecked(True)
        self.straighten.setToolTip('根据检测到的文字行与横线校正局部倾斜，文字不足时保留原状；请核对效果。')
        controls.addWidget(self.straighten)
        self.advanced=QPushButton('更多调整');self.advanced.setCheckable(True)
        controls.addWidget(self.advanced)
        self.strength_panel=QWidget();advanced=QVBoxLayout(self.strength_panel)
        advanced.setContentsMargins(0,0,0,0);advanced.setSpacing(8)
        advanced.addWidget(QLabel('去底色强度'))
        self.strength=QSlider(Qt.Orientation.Horizontal)
        self.strength.setRange(0,100);self.strength.setValue(45)
        self.strength.setAccessibleName('去底色强度')
        self.strength.setToolTip('越强去除的浅色越多，请核对铅笔等浅色笔迹。')
        advanced.addWidget(self.strength)
        self.detect=QPushButton('重新识别纸边');self.whole=QPushButton('保留整页')
        self.trim=QPushButton('向内裁一点')
        self.trim.setToolTip('四边向内收约 0.5%，可在原图中继续调整。')
        for button in (self.detect,self.whole,self.trim):advanced.addWidget(button)
        self.coordinates=QPushButton('四角坐标');self.coordinates.setCheckable(True)
        advanced.addWidget(self.coordinates)
        self.coordinate_panel=QWidget();grid=QGridLayout(self.coordinate_panel)
        grid.setContentsMargins(0,0,0,0);self.corner_inputs=[]
        for i,name in enumerate(('左上','右上','右下','左下')):
            grid.addWidget(QLabel(name),i,0);pair=[]
            for j,axis in enumerate(('横向','纵向')):
                spin=QDoubleSpinBox();spin.setRange(0,100);spin.setDecimals(1);spin.setSuffix('%')
                spin.setKeyboardTracking(False)
                spin.setMaximumWidth(86)
                spin.setStyleSheet('QDoubleSpinBox {padding: 4px 3px; min-height: 28px;}')
                spin.setAccessibleName(name+axis+'位置');spin.valueChanged.connect(self.coordinate_changed)
                grid.addWidget(spin,i,j+1);pair.append(spin)
            self.corner_inputs.append(pair)
        self.coordinate_panel.hide();self.coordinates.toggled.connect(self.coordinate_panel.setVisible)
        advanced.addWidget(self.coordinate_panel)
        self.strength_panel.hide();self.advanced.toggled.connect(self.strength_panel.setVisible)
        controls.addWidget(self.strength_panel);controls.addStretch()
        self.help_button=QPushButton('处理说明');self.help_button.clicked.connect(self.show_help)
        controls.addWidget(self.help_button)
        sidebar=QScrollArea();sidebar.setWidgetResizable(True);sidebar.setFrameShape(QScrollArea.Shape.NoFrame)
        sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sidebar.setFixedWidth(250);sidebar.setWidget(panel)
        body.addWidget(sidebar)
        canvas=QVBoxLayout();toolbar=QHBoxLayout()
        self.status=QLabel('正在生成预览…');self.status.setWordWrap(True)
        toolbar.addWidget(self.status,1)
        self.compare=QPushButton('原图对比');self.compare.setCheckable(True)
        self.crop_button=QPushButton('调整纸边');self.crop_button.setCheckable(True)
        self.detail=QPushButton('放大检查');self.detail.setCheckable(True)
        for button in (self.compare,self.crop_button,self.detail):toolbar.addWidget(button)
        canvas.addLayout(toolbar)
        preview_layout=QHBoxLayout()
        self.original_box=QWidget();before=QVBoxLayout(self.original_box);before.setContentsMargins(0,0,0,0)
        before.addWidget(QLabel('原图'))
        self.cleaned_box=QWidget();after=QVBoxLayout(self.cleaned_box);after.setContentsMargins(0,0,0,0)
        after.addWidget(QLabel('增强效果'))
        self.original=PaperPreview(host.store,self.pages,draw_content=self.draw_original)
        self.cleaned=ScrollablePagePreview(host.store,self.pages,draw_content=self.draw_cleaned,
            paper_size=lambda row:self.result_paper_size(row))
        self.original.peer=self.cleaned
        self.cleaned.render_next=lambda:None
        self.cleaned.image_key=self.original.image_key
        for preview in (self.original,self.cleaned):
            preview.setMinimumSize(180,220);preview.max_paper_width=1400;preview.set_fit_page(True)
        before.addWidget(self.original);after.addWidget(self.cleaned)
        preview_layout.addWidget(self.original_box,1);preview_layout.addWidget(self.cleaned_box,1)
        self.original_box.hide();canvas.addLayout(preview_layout,1)
        self._linking=False
        self.original.verticalScrollBar().valueChanged.connect(lambda:self.sync_preview_scroll(self.original,self.cleaned))
        self.cleaned.verticalScrollBar().valueChanged.connect(lambda:self.sync_preview_scroll(self.cleaned,self.original))
        self.cleaned.verticalScrollBar().valueChanged.connect(self.original.schedule)
        self.compare.toggled.connect(self.preview_mode_changed)
        self.crop_button.toggled.connect(self.preview_mode_changed)
        self.coordinates.toggled.connect(lambda opened:self.crop_button.setChecked(True) if opened else None)
        self.detail.toggled.connect(self.preview_scale_changed)
        body.addLayout(canvas,1);layout.addLayout(body,1)
        footer=QHBoxLayout()
        self.save_image=QPushButton('导出当前页 PNG');self.save_image.clicked.connect(self.export_image)
        footer.addWidget(self.save_image)
        warning=QLabel('增强页转为图片，文字层不保留；原件保留，可撤销。')
        warning.setWordWrap(True);warning.setStyleSheet('color: #5d6f68; font-size: 12px;')
        footer.addWidget(warning,1)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        apply=self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        apply.setText('应用到文档');apply.setDefault(True)
        apply.setStyleSheet('QPushButton {background: #0f766e; color: white; border-color: #0f766e; font-weight: 600;} QPushButton:disabled {background: #dbe7e0; color: #7e9187;}')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.buttons.accepted.connect(self.accept);self.buttons.rejected.connect(self.reject)
        footer.addWidget(self.buttons);layout.addLayout(footer)
        self.signals=_Signals(); self.signals.ready.connect(self.finished_preview)
        self.pump=QTimer(self); self.pump.setSingleShot(True); self.pump.timeout.connect(self.process_next)
        self.original.image_ready.connect(self.schedule)
        self.original.page_changed.connect(self.page_changed)
        self.original.region_changed.connect(self.region_changed)
        self.detect.clicked.connect(self.redetect)
        self.whole.clicked.connect(lambda:self.region_changed(self.current_row(),FULL_PAGE))
        self.trim.clicked.connect(self.trim_region)
        self.mode.currentIndexChanged.connect(self.invalidate)
        self.strength.valueChanged.connect(self.invalidate)
        self.straighten.toggled.connect(self.invalidate)
        self.scope.mode.currentIndexChanged.connect(self.reset_rows)
        self.scope.range.textChanged.connect(self.reset_rows)
        self.reset_rows()

    def showEvent(self,event):
        super().showEvent(event)
        self.original.schedule()

    def result_paper_size(self,row):
        image=self.results.get(row)
        return (image.width(),image.height()) if image is not None else self.host.store.page_size(self.pages[row])

    def show_help(self):
        QMessageBox.information(self,'扫描件增强说明',
            '适用于扫描 PDF 和拍照文档。先选择效果，再核对预览并应用。\n\n'
            '纸边不合适时点“调整纸边”，拖动四角或重新框选。整页模式滚轮换页；放大后滚动检查细节。\n\n'
            '未确认的页面保留整页范围。增强页转为图片，文字层与交互信息不保留；含批注、链接或表单的页不会直接处理。\n\n'
            '原件保留，应用后可 Ctrl+Z 撤销，保存时另存 PDF。')

    def preview_mode_changed(self,*_):
        row=self.current_row()
        editing=self.crop_button.isChecked()
        self.original.editing=editing
        self.original_box.setVisible(editing or self.compare.isChecked())
        self.cleaned_box.setVisible(not editing)
        self.crop_button.setText('完成裁纸' if editing else '调整纸边')
        self.original.setCursor(Qt.CursorShape.CrossCursor if editing else Qt.CursorShape.ArrowCursor)
        if editing:
            self.cancel.set();self.cancel=Event();self.generation+=1;self.pump.stop()
            self.status.setText('拖动四角，或框选纸张')
        else:
            self.schedule()
        self.update_buttons()
        self.restore_preview_row(row)

    def update_buttons(self):
        editing=self.crop_button.isChecked()
        button=self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        button.setText('完成裁纸' if editing else '应用到文档')
        button.setEnabled(self.coordinate_valid and bool(self.original.rows)
                          and (editing or any(value is not None for value in self.results.values())))
        self.save_image.setEnabled(not editing and self.results.get(self.current_row()) is not None and self.coordinate_valid)

    def accept(self):
        if self.crop_button.isChecked():
            self.crop_button.setChecked(False)
            return
        super().accept()

    def restore_preview_row(self,row):
        if self.closed:
            return
        for preview in (self.original,self.cleaned):
            preview.refresh_pages(layout=True)
            if row is not None:
                preview.scroll_to_page(row)
        self.original.schedule()

    def preview_scale_changed(self,*_):
        row=self.current_row()
        bars=[self.original.verticalScrollBar(),self.cleaned.verticalScrollBar()]
        blocked=[bar.blockSignals(True) for bar in bars]
        try:
            for preview in (self.original,self.cleaned):
                preview.set_fit_page(not self.detail.isChecked())
        finally:
            for bar,value in zip(bars,blocked):bar.blockSignals(value)
        self.detail.setText('整页显示' if self.detail.isChecked() else '放大检查')
        self.restore_preview_row(row)

    def sync_preview_scroll(self,source,target):
        if self.closed or self._linking or not source.isVisible():
            return
        self.original.schedule()
        if not target.isVisible():
            return
        self._linking=True
        try:
            if source.fit_page:
                target.verticalScrollBar().setValue(source.verticalScrollBar().value())
            else:
                positions=source.visible_positions()
                if positions:
                    position=positions[0]
                    rect=source.visualRect(source.page_model.index(position))
                    fraction=max(0,-rect.top()/max(1,rect.height()))
                    index=target.page_model.index(position)
                    target.scrollTo(index,target.ScrollHint.PositionAtTop)
                    height=target.visualRect(index).height()
                    bar=target.verticalScrollBar();bar.setValue(bar.value()+round(fraction*height))
        finally:
            self._linking=False

    def current_row(self):
        return self.original.current_source_row

    def reset_rows(self,*_):
        try:
            rows=self.scope.indices()
            self.original.set_rows(rows); self.cleaned.set_rows(rows)
            self.cleaned.images=self.original.images
            self.invalidate()
        except ValueError as exc:
            self.original.set_rows([]); self.cleaned.set_rows([])
            self.invalidate()
            self.status.setText(str(exc))
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

    def invalidate(self,*_):
        self.cancel.set(); self.cancel=Event(); self.generation+=1
        self.results.clear(); self.schedule(); self.cleaned.refresh_pages()
        self.update_buttons()

    def schedule(self,*_):
        if not self.closed and not self.crop_button.isChecked():
            self.pump.start(120)

    def page_changed(self,row):
        if self.original.selected_handle is not None and self.original.selected_handle[0]!=row:
            self.original.selected_handle=None;self.original.hover_handle=None
        self.sync_coordinates(row); self.schedule()
        if self.results.get(row) is not None:
            self.reviewed.add(row)
        self.update_buttons()

    def region_changed(self,row,quad):
        if row is None or not valid_quad(quad):
            return
        self.manual[row]=quad; self.original.regions[row]=quad
        self.reviewed.add(row)
        self.sync_coordinates(row); self.invalidate(); self.original.refresh_pages()

    def redetect(self):
        row=self.current_row()
        self.manual.pop(row,None); self.proposals.pop(row,None); self.original.regions.pop(row,None)
        self.reviewed.discard(row)
        self.invalidate()

    def trim_region(self):
        row=self.current_row()
        if row is not None:
            try:
                self.region_changed(row,inset_region(self.original.regions.get(row,FULL_PAGE),.005))
            except ValueError as exc:
                self.status.setText(str(exc))

    def sync_coordinates(self,row):
        self.coordinate_valid=True
        quad=self.original.regions.get(row,FULL_PAGE)
        for pair,point in zip(self.corner_inputs,quad):
            for spin,value in zip(pair,point):
                spin.blockSignals(True); spin.setValue(value*100); spin.blockSignals(False)

    def coordinate_changed(self):
        quad=tuple(tuple(spin.value()/100 for spin in pair) for pair in self.corner_inputs)
        if valid_quad(quad):
            self.coordinate_valid=True
            self.region_changed(self.current_row(),quad)
        else:
            self.coordinate_valid=False
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
            self.save_image.setEnabled(False)
            self.status.setText('四角范围无效，请按左上、右上、右下、左下调整。')

    def process_next(self):
        if self.closed or self.busy or not self.isVisible() or self.crop_button.isChecked():
            return
        for position in self.original.visible_positions():
            row=self.original.rows[position]
            image=self.original.images.get(self.original.image_key(row))
            if image is None or row in self.results:
                continue
            self.busy=True
            self.status.setText(f'正在生成第 {row+1} 页推荐范围和效果…')
            quad=self.manual.get(row,self.proposals[row].quad if row in self.proposals else None)
            QThreadPool.globalInstance().start(_Clean(self.generation,row,image.copy(),quad,
                self.mode.currentData(),self.strength.value(),self.straighten.isChecked(),self.signals,self.cancel))
            return

    def finished_preview(self,token,row,proposal,image,error):
        self.busy=False
        if self.closed:
            return
        if token==self.generation:
            if error:
                self.results[row]=None; self.status.setText(error)
            elif image is not None:
                if proposal is not None:
                    self.proposals[row]=proposal
                    self.original.regions[row]=proposal.quad
                self.results[row]=image
                if row==self.current_row():
                    self.reviewed.add(row)
                while len(self.results)>4:
                    self.results.popitem(last=False)
                if self.coordinate_valid:
                    self.sync_coordinates(self.current_row())
                if row==self.current_row():
                    proposal=self.proposals.get(row)
                    message='纸边已调整' if row in self.manual else ('预览已更新' if proposal is not None and proposal.confidence else '保留整页')
                    self.status.setText(f'第 {row+1} 页 · {message}')
            self.update_buttons()
            self.original.refresh_pages(); self.cleaned.refresh_pages(layout=not self.cleaned.fit_page)
        self.schedule()

    def draw_original(self,painter,area,row,image):
        self.original.draw_source(painter,area,image)
        quad=self.original.regions.get(row,FULL_PAGE) if self.original.editing else None
        if quad is None:
            return
        points=QPolygonF([QPointF(area.x()+x*area.width(),area.y()+y*area.height()) for x,y in quad])
        painter.save();painter.setRenderHint(painter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#0F766E'),2));painter.drawPolygon(points)
        for corner,point in enumerate(points):
            active=(row,corner) in (self.original.hover_handle,self.original.selected_handle)
            if active:
                painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor(15,118,110,35));painter.drawEllipse(point,18,18)
            painter.setPen(QPen(QColor('#0F766E'),2));painter.setBrush(QColor('#0F766E') if active else QColor('white'))
            radius=11 if active else 9;painter.drawEllipse(point,radius,radius)
            if active:
                painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor('white'));painter.drawEllipse(point,3,3)
        painter.restore()

    def draw_cleaned(self,painter,area,row,image):
        result=self.results.get(row)
        if result is not None:
            draw_fitted_page(painter,area,result)
        else:
            painter.drawText(area,Qt.AlignmentFlag.AlignCenter,'正在准备去底色效果…')

    def regions(self):
        return {row:self.manual.get(row,self.proposals[row].quad if row in self.proposals and row in self.reviewed else FULL_PAGE)
                for row in self.scope.indices()}

    def export_image(self):
        row=self.current_row()
        if row is None or self.results.get(row) is None:
            return
        ref=self.pages[row]
        dest,_=QFileDialog.getSaveFileName(self,'增强图片另存为',str(Path(ref.source_path).with_name(Path(ref.source_path).stem+'_增强.png')),'PNG (*.png)')
        if not dest:
            return
        if self.host.store.is_source_destination(dest):
            QMessageBox.warning(self,'请另存副本','不能覆盖已打开的原图或 PDF。'); return
        expected=stamp(dest)
        mode,strength,quad=self.mode.currentData(),self.strength.value(),self.regions()[row]
        straighten=self.straighten.isChecked()
        def work(progress,cancel):
            image,_=render_scan(self.host.store,ref,cancel)
            result=whiten_image(image,mode=mode,strength=strength,quad=quad,straighten=straighten,cancel=cancel)
            with tempfile.NamedTemporaryFile(suffix='.png',dir=Path(dest).parent,delete=False) as handle:
                temporary=Path(handle.name)
            try:
                result.save(temporary,format='PNG'); commit_file(temporary,dest,expected,cancel=cancel)
            finally:
                temporary.unlink(missing_ok=True)
        try:
            run_operation(self,'导出增强图片',work,cancellable=True)
            self.status.setText('已另存完整清晰度的 PNG：'+Path(dest).name)
        except OperationCancelled:
            self.status.setText('已取消另存，原件保留。')
        except (ValueError,PdfError,OSError) as exc:
            QMessageBox.warning(self,'无法另存图片',str(exc))

    def done(self,result):
        self.closed=True; self.cancel.set(); self.pump.stop()
        self.original.shutdown(); self.cleaned.shutdown()
        super().done(result)


def open_whitening(host):
    if not host.ws.pages or not host._allow_operation('content_edit') or not host._allow_operation('copy'):
        return
    before=tuple(host.ws.pages)
    dialog=WhiteningDialog(host)
    if dialog.exec()!=QDialog.DialogCode.Accepted:
        dialog.deleteLater(); return
    indices,regions=dialog.scope.indices(),dialog.regions()
    mode,strength=dialog.mode.currentData(),dialog.strength.value()
    straighten=dialog.straighten.isChecked()
    outline_snapshot=host.ws.outline
    dialog.deleteLater()
    try:
        def work(progress,cancel):
            outline=outline_snapshot if outline_snapshot is not None else host.store.outline_entries(before)
            changes=prepare_whitening(host.store,before,indices,mode=mode,strength=strength,
                regions=regions,straighten=straighten,progress=progress,cancel=cancel)
            changed_ids={ref.page_id for ref in changes.values()}
            # Crop/perspective changes invalidate old coordinates; keep the bookmark's page.
            outline=tuple(replace(entry,destination=tuple((key,value) for key,value in entry.destination if key!='to'))
                          if entry.target_id in changed_ids else entry for entry in outline)
            return changes,outline
        changes,outline=run_operation(host,'扫描件增强',work,cancellable=True)
        if tuple(host.ws.pages)!=before or host.ws.outline!=outline_snapshot:
            raise ValueError('工作区已改变，请重新操作。')
        host.ws.edit_pages(changes,'扫描件增强',as_copy=True,outline=outline)
        host.refresh()
        host.statusBar().showMessage(f'已增强 {len(changes)} 页；Ctrl+Z 可撤销，保存时另存 PDF。',6500)
    except OperationCancelled:
        host.statusBar().showMessage('已取消增强，页面未修改。',5000)
        host._release_unused_docs()
    except (ValueError,PdfError,OSError) as exc:
        QMessageBox.warning(host,'无法增强扫描件',str(exc))
        host._release_unused_docs()
