"""Opt-in version comparison tool. No work or imports on the ordinary reading path."""
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPen, QPixmap
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFileDialog,
    QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget)

from app.output import OperationCancelled
from app.pdfcompare import CompareOptions
from app.pdfworker import compare_pdf_versions
from app.progress import run_operation
from app.themes import set_theme_style


STATUS = {'equal': '一致', 'changed': '有差异', 'inserted': '新版未配对（可能新增）',
          'deleted': '旧版未配对（可能删除）'}


class CompareView(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.marker = None

    def show_image(self, image):
        self.scene().clear(); self.marker = None
        data = image.convert('RGB')
        qimage = QImage(data.tobytes(), data.width, data.height, data.width * 3,
                        QImage.Format.Format_RGB888).copy()
        self.scene().addPixmap(QPixmap.fromImage(qimage))
        self.scene().setSceneRect(0, 0, data.width, data.height)
        self.fit_page()

    def fit_page(self):
        if not self.sceneRect().isEmpty():
            self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def focus_region(self, box):
        if self.marker is not None:
            self.scene().removeItem(self.marker)
        rect = QRectF(box[0], box[1], box[2] - box[0], box[3] - box[1])
        pen = QPen(QColor('#305ed6')); pen.setCosmetic(True); pen.setWidth(3)
        self.marker = self.scene().addRect(rect, pen)
        self.fitInView(rect.adjusted(-60, -60, 60, 60), Qt.AspectRatioMode.KeepAspectRatio)
        # Cap magnification; navigation magnifies existing raster, never claims new detail.
        if self.transform().m11() > 3:
            self.resetTransform(); self.scale(3, 3); self.centerOn(rect.center())

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
            if .05 <= self.transform().m11() * factor <= 6:
                self.scale(factor, factor)
            event.accept()
        else:
            super().wheelEvent(event)


class PdfCompareDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('两份 PDF 对比 · 实验原型')
        self.resize(1180, 820)
        self.summary = None; self.images = None; self.row = -1; self.busy = False
        self.passwords = ('', '')
        layout = QVBoxLayout(self)
        self.paths, self.password_inputs = [], []
        for title in ('旧版', '新版'):
            row = QHBoxLayout(); row.addWidget(QLabel(title))
            path = QLineEdit(); path.setPlaceholderText('选择磁盘上的 PDF（不含标签页未保存修改）')
            path.setAccessibleName(title + ' PDF 路径'); self.paths.append(path)
            choose = QPushButton('选择…'); choose.clicked.connect(lambda _, p=path: self.choose(p))
            password = QLineEdit(); password.setEchoMode(QLineEdit.EchoMode.Password)
            password.setPlaceholderText('密码（可选）'); password.setMaximumWidth(150)
            password.setAccessibleName(title + '密码'); self.password_inputs.append(password)
            row.addWidget(path, 1); row.addWidget(choose); row.addWidget(password); layout.addLayout(row)
        settings = QHBoxLayout()
        self.pairing = QComboBox(); self.pairing.addItems(['自动顺序配对', '按页号配对'])
        self.strict = QCheckBox('严格像素比较')
        self.align = QCheckBox('容忍 2px 整页平移')
        self.align.setToolTip('可减少扫描错位误报，也可能忽略真实整页移动；默认关闭')
        self.annotations = QCheckBox('包含批注'); self.annotations.setChecked(True)
        self.start = QPushButton('开始比较'); self.start.clicked.connect(self.compare)
        for widget in (self.pairing, self.strict, self.align, self.annotations):
            settings.addWidget(widget)
        settings.addStretch(); settings.addWidget(self.start); layout.addLayout(settings)
        note = QLabel('只读本地比较 · 每份最多 200 页 / 512 MiB · 原始色彩 · 默认过滤细小噪点；细小文字请复核严格模式')
        note.setWordWrap(True); layout.addWidget(note)
        legend = QLabel('叠加图例：红＝旧版独有像素　青＝新版独有像素　橙＝其他变化　蓝框＝当前定位；颜色不代表业务含义')
        legend.setWordWrap(True); layout.addWidget(legend)
        splitter = QSplitter()
        self.rows = QListWidget(); self.rows.setMaximumWidth(310)
        self.rows.setAccessibleName('页面配对与未配对页面')
        self.rows.currentRowChanged.connect(self.select_row); splitter.addWidget(self.rows)
        viewer = QWidget(); vlayout = QVBoxLayout(viewer)
        display = QHBoxLayout(); self.mode = QComboBox()
        self.mode.addItems(['差异叠加', '旧版原页', '新版原页', '旧版差异框', '新版差异框'])
        self.mode.currentIndexChanged.connect(self.display_image)
        fit = QPushButton('适合整页'); fit.clicked.connect(lambda: self.view.fit_page())
        display.addWidget(self.mode); display.addWidget(fit); display.addWidget(QLabel('Ctrl+滚轮缩放 · 拖动平移'))
        vlayout.addLayout(display)
        self.identity = QLabel('选择两份 PDF 后开始'); self.identity.setWordWrap(True)
        vlayout.addWidget(self.identity)
        self.view = CompareView(); vlayout.addWidget(self.view, 1)
        self.regions = QComboBox(); self.regions.setAccessibleName('差异位置')
        self.regions.currentIndexChanged.connect(self.focus_region)
        nav = QHBoxLayout(); prev = QPushButton('上一处差异'); nxt = QPushButton('下一处差异')
        prev.clicked.connect(lambda: self.navigate(-1)); nxt.clicked.connect(lambda: self.navigate(1))
        nav.addWidget(prev); nav.addWidget(self.regions, 1); nav.addWidget(nxt); vlayout.addLayout(nav)
        splitter.addWidget(viewer); splitter.setStretchFactor(1, 1); layout.addWidget(splitter, 1)
        self.status = QLabel('自动配对不能保证匹配任意版本；新增／删除提示表示未配对，请人工核对。')
        self.status.setWordWrap(True); layout.addWidget(self.status)
        set_theme_style(self, 'QDialog { background: #F7FAF9; } QPushButton { padding: 6px 12px; }')

    def choose(self, target):
        path, _ = QFileDialog.getOpenFileName(self, '选择 PDF', '', 'PDF (*.pdf)')
        if path:
            target.setText(path)

    def compare(self):
        if self.busy:
            return
        options = CompareOptions(threshold=0 if self.strict.isChecked() else 24,
            blur=0 if self.strict.isChecked() else .5, min_pixels=1 if self.strict.isChecked() else 8,
            align_pixels=2 if self.align.isChecked() else 0,
            annotations=self.annotations.isChecked(), pairing='auto' if self.pairing.currentIndex() == 0 else 'index')
        paths = [widget.text() for widget in self.paths]
        passwords = tuple(widget.text() for widget in self.password_inputs)
        self.busy = True; self.start.setEnabled(False); self.rows.setEnabled(False)
        try:
            summary = run_operation(self, '比较两份 PDF', lambda progress, cancel:
                compare_pdf_versions(*paths, options, progress, cancel, passwords=passwords), cancellable=True)
            self.summary, self.passwords = summary, passwords
            self.images = None; self.view.scene().clear(); self.view.marker = None
            self.row = -1
            self.rows.blockSignals(True); self.rows.clear()
            for row in summary['rows']:
                self.rows.addItem(f"旧 {row['old_page'] or '—'} ↔ 新 {row['new_page'] or '—'}　{STATUS[row['status']]}")
            self.rows.blockSignals(False)
            self.status.setText(f"完成，用时 {summary['seconds']} 秒；{summary['counts']}。点击页面组查看差异。")
        except OperationCancelled:
            self.status.setText('比较已取消；保留上次完成的结果，来源文件未修改。')
        except Exception as exc:
            QMessageBox.warning(self, '无法比较', str(exc))
        finally:
            self.busy = False; self.start.setEnabled(True); self.rows.setEnabled(True)
        if self.summary is not None and self.row == -1:
            self.rows.setCurrentRow(0)

    def select_row(self, index):
        if self.busy or self.summary is None or index < 0:
            return
        previous = self.row
        self.busy = True; self.rows.setEnabled(False); self.start.setEnabled(False)
        try:
            row, images = run_operation(self, '加载差异页面', lambda progress, cancel:
                compare_pdf_versions(*self.summary['paths'], CompareOptions(**self.summary['options']),
                    progress, cancel, passwords=self.passwords, summary=self.summary, row=index), cancellable=True)
            self.row, self.images = index, images
            self.identity.setText(f"旧版：{Path(self.summary['paths'][0]).name} 第 {row['old_page'] or '—'} 页　｜　"
                f"新版：{Path(self.summary['paths'][1]).name} 第 {row['new_page'] or '—'} 页　｜　{STATUS[row['status']]}")
            self.regions.blockSignals(True); self.regions.clear()
            for i, box in enumerate(row['regions']):
                self.regions.addItem(f'差异 {i + 1} · 像素位置 {box}', box)
            self.regions.setCurrentIndex(-1)
            self.regions.blockSignals(False)
            self.display_image()
            self.status.setText('；'.join(row['warnings']) or
                f"{len(row['regions'])} 处区域 / {row['changed_pixels']} 差异像素；缩放仅放大本次比较栅格。")
        except OperationCancelled:
            self.status.setText('页面加载已取消')
            self._restore_row(previous)
        except Exception as exc:
            QMessageBox.warning(self, '无法加载差异页', str(exc)); self._restore_row(previous)
        finally:
            self.busy = False; self.rows.setEnabled(True); self.start.setEnabled(True)

    def _restore_row(self, index):
        self.rows.blockSignals(True); self.rows.setCurrentRow(index); self.rows.blockSignals(False)

    def display_image(self, *_):
        if self.images is not None:
            key = ['overlay', 'old', 'new', 'old_marked', 'new_marked'][self.mode.currentIndex()]
            self.view.show_image(self.images[key])

    def focus_region(self, index):
        if index >= 0 and self.images is not None:
            box = self.regions.itemData(index)
            if self.mode.currentIndex() in (2, 4):
                dx, dy = self.summary['rows'][self.row]['shift']
                box = [box[0] - dx, box[1] - dy, box[2] - dx, box[3] - dy]
            self.view.focus_region(box)

    def navigate(self, direction):
        if self.summary is None or self.busy:
            return
        targets = [(i, j) for i, row in enumerate(self.summary['rows'])
                   for j in range(len(row['regions']))]
        if not targets:
            return
        current = (self.row, self.regions.currentIndex())
        candidates = [target for target in targets if
                      (target > current if direction > 0 else target < current)]
        target = (candidates[0] if direction > 0 else candidates[-1]) if candidates else (
                  targets[0] if direction > 0 else targets[-1])
        if target[0] != self.row:
            self.rows.setCurrentRow(target[0])
        if self.row == target[0]:
            self.regions.setCurrentIndex(target[1]); self.focus_region(target[1])

    def reject(self):
        if not self.busy:
            super().reject()

    def done(self, result):
        if self.busy:
            return
        self.images = None; self.summary = None; self.passwords = ('', '')
        self.view.scene().clear(); self.view.marker = None
        for widget in self.password_inputs:
            widget.clear()
        super().done(result)

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
        else:
            event.accept()


def main():
    import sys
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle('Fusion')
    dialog = PdfCompareDialog()
    for widget, path in zip(dialog.paths, sys.argv[1:3]):
        widget.setText(path)
    dialog.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
