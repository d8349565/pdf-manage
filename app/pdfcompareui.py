"""Opt-in version comparison tool. No work or imports on the ordinary reading path."""
from pathlib import Path
from collections import OrderedDict
import os

from PyQt6.QtCore import QEvent, QObject, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QImage, QPen, QPixmap
from PyQt6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFrame, QGridLayout,
    QGraphicsScene, QGraphicsView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPushButton, QSplitter, QTableWidget, QVBoxLayout, QWidget)

from app.output import OperationCancelled, check_cancel
from app.pdfcompare import CompareOptions, check_sources, pair_pages
from app.pdfworker import compare_pdf_versions
from app.progress import run_operation
from app.pagetoolsui import ClearComboBox, ClearSpinBox
from app.themes import set_theme_style, ui_color


COMPARE_STYLE = '''
QDialog#pdfCompare, QDialog#comparePairing { background: #F3F6F5; }
QLabel#compareTitle { font-size: 20px; font-weight: 600; }
QLabel#compareSection { font-size: 14px; font-weight: 600; }
QLabel#compareHint { color: #4D6B67; font-size: 12px; }
QLabel#compareError { color: #B91C1C; font-size: 13px; }
QFrame#comparePages { background: #FFFFFF; border: 1px solid #D7E8E4; border-radius: 10px; }
QListWidget#compareList { background: transparent; border: none; outline: none; padding: 0; }
QListWidget#compareList::item { padding: 8px 10px; border: 1px solid transparent; border-radius: 6px; }
QListWidget#compareList::item:hover { background: #F0FDFA; }
QListWidget#compareList::item:selected { background: #CCFBF1; color: #134E4A; }
QListWidget#compareList::item:focus { border-color: #0F766E; }
QDialog#pdfCompare QPushButton, QDialog#comparePairing QPushButton { min-height: 26px; padding: 5px 12px; border-radius: 7px; }
QDialog#pdfCompare QPushButton#primary { padding: 5px 24px; }
QPushButton#compareMore:checked { background: #CCFBF1; border-color: #99B2AC; }
QDialog#pdfCompare QComboBox { min-height: 26px; padding: 4px 28px 4px 10px; border-radius: 7px; }
QDialog#pdfCompare QLineEdit { min-height: 28px; padding: 4px 10px; border-radius: 7px; }
QDialog#pdfCompare QCheckBox { background: transparent; }
QWidget#compareOptions { background: transparent; }
QWidget#pdfDropTarget { background: transparent; }
QDialog#pdfCompare QLineEdit[pdfDropActive="true"] { border: 1px solid #0D9488; background: #F0FDFA; }
QGraphicsView#compareCanvas { border: none; }
QTableWidget#pairTable { background: #FFFFFF; border: 1px solid #D7E8E4; border-radius: 7px; }
QTableWidget#pairTable QHeaderView::section { background: #EEF3F0; border: none; padding: 10px; font-weight: 600; }
QTableWidget#pairTable QSpinBox { min-height: 26px; padding: 3px 24px 3px 10px; border: 1px solid #D7E8E4; border-radius: 4px; }
QTableWidget#pairTable QSpinBox::up-button, QTableWidget#pairTable QSpinBox::down-button { width: 22px; }
'''


STATUS = {'equal': '当前设置下未检出差异', 'changed': '有差异', 'inserted': '新版未配对（可能新增）',
          'deleted': '旧版未配对（可能删除）'}


class PdfFileDrop(QObject):
    """File-reference drops shared by content and visual comparison; never move files."""
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        for widget in [owner, *owner.paths, *owner.file_targets]:
            widget.setAcceptDrops(True)
            widget.installEventFilter(self)

    @staticmethod
    def paths_from(mime):
        if not mime.hasUrls():
            return []
        urls = mime.urls()
        if not 1 <= len(urls) <= 2 or any(not url.isLocalFile() for url in urls):
            return []
        paths = [url.toLocalFile() for url in urls]
        if any(Path(path).suffix.lower() != '.pdf' for path in paths):
            return []
        if len({PdfFileDrop.key(path) for path in paths}) != len(paths):
            return []
        return paths

    @staticmethod
    def key(path):
        return os.path.normcase(os.path.abspath(path))

    def highlight(self, target, active):
        if target is None:
            if not active:
                for index in range(2):
                    self.highlight(index, False)
            return
        field = self.owner.paths[target]
        if bool(field.property('pdfDropActive')) != active:
            field.setProperty('pdfDropActive', active)
            field.style().unpolish(field); field.style().polish(field); field.update()

    def eventFilter(self, widget, event):
        kind = event.type()
        if kind not in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.DragLeave, QEvent.Type.Drop):
            return False
        target = next((i for i in range(2) if widget in (self.owner.paths[i], self.owner.file_targets[i])), None)
        if kind == QEvent.Type.DragLeave:
            self.highlight(target, False)
            return False
        if not event.mimeData().hasUrls():
            return False  # Keep ordinary text editing in the path fields.
        paths = self.paths_from(event.mimeData())
        allowed = (not self.owner.busy and widget.isEnabled() and paths
                   and event.possibleActions() & Qt.DropAction.CopyAction)
        if not allowed:
            self.highlight(target, False); event.ignore(); return True
        if kind == QEvent.Type.Drop:
            self.highlight(target, False)
            if not self.assign(paths, target):
                event.ignore(); return True
        else:
            self.highlight(target, True)
        event.setDropAction(Qt.DropAction.CopyAction); event.accept()
        return True

    def assign(self, paths, target):
        owner = self.owner
        if owner.busy:
            return False
        try:
            available = all(Path(path).is_file() for path in paths)
        except (OSError, ValueError):
            available = False
        if not available:
            owner.status.setText('拖入的 PDF 不存在或不是文件，请重新选择。')
            return False
        if len(paths) == 1 and target is None:
            if any(field.text().strip() and self.key(field.text().strip()) == self.key(paths[0]) for field in owner.paths):
                owner.status.setText('这份文件已填入。请拖入另一份 PDF；也可拖到指定版本框替换。')
                return False
            target = next((i for i, field in enumerate(owner.paths) if not field.text().strip()), None)
            if target is None:
                owner.status.setText('两份文件已选齐，请拖到旧版或新版文件框替换对应文件。')
                return False
        assignments = list(enumerate(paths)) if len(paths) == 2 else [(target, paths[0])]
        for index, path in assignments:
            if self.key(owner.paths[index].text()) != self.key(path):
                owner.password_inputs[index].clear()
            owner.paths[index].setText(path)
        names = [('旧版' if i == 0 else '新版') + '：' + Path(field.text()).name
                 for i, field in enumerate(owner.paths) if field.text().strip()]
        message = '；'.join(names) + '。确认顺序后点击“开始比较”。'
        if getattr(owner, 'stale', False):
            message += '来源已变化，请重新比较。'
        elif owner.summary is not None:
            message += '下方仍显示上次完成的结果。'
        owner.status.setText(message)
        return True


class PairingDialog(QDialog):
    """A few ordered anchor pairs; unmatched pages are never discarded."""
    def __init__(self, summary, current, parent=None):
        super().__init__(parent)
        self.setWindowTitle('调整页面配对')
        self.setObjectName('comparePairing'); self.resize(520, 420)
        set_theme_style(self, COMPARE_STYLE)
        self.counts = [max(row[key] or 0 for row in summary['rows']) for key in ('old_page', 'new_page')]
        self.manual_pairs = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16); layout.setSpacing(12)
        title = QLabel('调整页面配对'); title.setObjectName('compareTitle'); layout.addWidget(title)
        note = QLabel('把确认对应的两页填在同一行，其余页面自动在它们之间配对。\n'
                      '两版页码须依次递增；移除全部指定可恢复原配对方式。')
        note.setObjectName('compareHint')
        note.setToolTip('使用当前完成结果的文件和比较设置，不读取标签页未保存的修改。')
        note.setWordWrap(True); layout.addWidget(note)
        self.table = QTableWidget(0, 2)
        self.table.setObjectName('pairTable'); self.table.setShowGrid(False)
        self.table.verticalHeader().hide(); self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setHorizontalHeaderLabels(['旧版页码', '新版页码'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        self.current = current
        for pair in summary.get('manual_pairs', []):
            self.add_pair(pair)
        if not self.table.rowCount():
            self.add_pair()
        actions = QHBoxLayout()
        add = QPushButton('添加对应页'); add.clicked.connect(lambda: self.add_pair())
        remove = QPushButton('移除选中'); remove.clicked.connect(lambda: self.table.removeRow(self.table.currentRow()))
        actions.addWidget(add); actions.addWidget(remove); actions.addStretch(); layout.addLayout(actions)
        self.error = QLabel(); self.error.setObjectName('compareError'); self.error.setWordWrap(True); layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('重新比较')
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName('primary')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def add_pair(self, pair=None):
        pair = pair or [self.current[key] or 1 for key in ('old_page', 'new_page')]
        row = self.table.rowCount(); self.table.insertRow(row)
        for column, (value, maximum) in enumerate(zip(pair, self.counts)):
            spin = ClearSpinBox(); spin.setRange(1, maximum); spin.setValue(value)
            spin.setAccessibleName(('旧版' if column == 0 else '新版') + '页码')
            self.table.setCellWidget(row, column, spin)
        self.table.setCurrentCell(row, 0)

    def accept(self):
        pairs = sorted([[self.table.cellWidget(row, column).value() for column in range(2)]
                        for row in range(self.table.rowCount())])
        try:
            pair_pages(*([None] * n for n in self.counts), mode='index', manual_pairs=pairs)
        except ValueError as exc:
            self.error.setText(str(exc)); return
        self.manual_pairs = pairs
        super().accept()


class CompareView(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setObjectName('compareCanvas'); self.setProperty('themeCanvas', 'canvas')
        self.setBackgroundBrush(QColor(ui_color('canvas')))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.marker = None
        self.fit_mode = True

    def show_image(self, image):
        self.scene().clear(); self.marker = None
        data = image.convert('RGB')
        qimage = QImage(data.tobytes(), data.width, data.height, data.width * 3,
                        QImage.Format.Format_RGB888).copy()
        self.scene().addPixmap(QPixmap.fromImage(qimage))
        self.scene().setSceneRect(-16, -16, data.width + 32, data.height + 32)
        self.fit_page()

    def fit_page(self):
        self.fit_mode = True
        if not self.sceneRect().isEmpty():
            self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit_mode:
            self.fit_page()

    def focus_region(self, box):
        self.fit_mode = False
        if self.marker is not None:
            self.scene().removeItem(self.marker)
        rect = QRectF(box[0], box[1], box[2] - box[0], box[3] - box[1])
        pen = QPen(QColor(ui_color('accent'))); pen.setCosmetic(True); pen.setWidth(3)
        self.marker = self.scene().addRect(rect, pen)
        self.fitInView(rect.adjusted(-60, -60, 60, 60), Qt.AspectRatioMode.KeepAspectRatio)
        # Cap magnification; navigation magnifies existing raster, never claims new detail.
        if self.transform().m11() > 3:
            self.resetTransform(); self.scale(3, 3); self.centerOn(rect.center())

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
            if .05 <= self.transform().m11() * factor <= 6:
                self.fit_mode = False
                self.scale(factor, factor)
            event.accept()
        else:
            super().wheelEvent(event)


class PdfCompareDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('两份 PDF 对比 · 实验原型')
        self.setObjectName('pdfCompare')
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setMinimumSize(900, 620)
        self.resize(1180, 820)
        self.summary = None; self.images = None; self.row = -1; self.busy = False
        self.passwords = ('', '')
        self.preview_cache = OrderedDict()
        self.preview_cache_limit = 48 * 1024 * 1024  # Pillow RGB storage: 4 bytes/pixel, at most two groups
        set_theme_style(self, COMPARE_STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14); layout.setSpacing(12)
        heading = QHBoxLayout()
        title = QLabel('两份 PDF 对比'); title.setObjectName('compareTitle'); heading.addWidget(title)
        subtitle = QLabel('原色比较 · 原文件保留'); subtitle.setObjectName('compareHint')
        heading.addSpacing(12); heading.addWidget(subtitle); heading.addStretch()
        help_button = QPushButton('使用说明'); help_button.clicked.connect(self.show_help)
        heading.addWidget(help_button); layout.addLayout(heading)
        sources = QFrame(); sources.setObjectName('workbench')
        source_layout = QVBoxLayout(sources); source_layout.setContentsMargins(14, 12, 14, 12); source_layout.setSpacing(10)
        files = QHBoxLayout(); files.setSpacing(20)
        self.paths, self.password_inputs, self.file_buttons, self.file_targets = [], [], [], []
        for title in ('旧版', '新版'):
            target = QWidget(); target.setObjectName('pdfDropTarget')
            group = QVBoxLayout(target); group.setContentsMargins(0, 0, 0, 0); group.setSpacing(6)
            self.file_targets.append(target)
            label = QLabel(title + ' PDF'); label.setObjectName('compareSection'); group.addWidget(label)
            row = QHBoxLayout(); row.setSpacing(8)
            path = QLineEdit(); path.setPlaceholderText('选择或拖入' + title + ' PDF')
            path.setToolTip('可拖入 PDF；一次拖两份时按旧版、新版顺序填入。只比较已保存文件。')
            path.setAccessibleName(title + ' PDF 路径'); self.paths.append(path)
            choose = QPushButton('选择文件'); choose.clicked.connect(lambda _, p=path: self.choose(p))
            choose.setAccessibleName('选择' + title + ' PDF')
            self.file_buttons.append(choose)
            password = QLineEdit(); password.setEchoMode(QLineEdit.EchoMode.Password)
            password.setPlaceholderText(title + '密码（如有）')
            password.setAccessibleName(title + '密码'); self.password_inputs.append(password)
            row.addWidget(path, 1); row.addWidget(choose); group.addLayout(row); files.addWidget(target, 1)
        source_layout.addLayout(files)
        settings = QHBoxLayout()
        self.advanced = QPushButton('更多设置'); self.advanced.setCheckable(True); self.advanced.setObjectName('compareMore')
        self.options_panel = QWidget(); self.options_panel.setObjectName('compareOptions')
        advanced_layout = QGridLayout(self.options_panel)
        advanced_layout.setContentsMargins(0, 4, 0, 0); advanced_layout.setHorizontalSpacing(16); advanced_layout.setVerticalSpacing(10)
        self.pairing = ClearComboBox(); self.pairing.addItems(['自动顺序配对', '按页号配对'])
        self.pairing.setAccessibleName('页面配对方式')
        self.strict = QCheckBox('严格像素比较')
        self.strict.setToolTip('关闭噪声过滤，但不提高比较分辨率；重要细节仍需核对原文件')
        self.align = QCheckBox('容忍 2px 整页平移')
        self.align.setToolTip('可减少扫描错位误报，也可能忽略真实整页移动；默认关闭')
        self.annotations = QCheckBox('包含批注'); self.annotations.setChecked(True)
        self.start = QPushButton('开始比较'); self.start.setObjectName('primary'); self.start.clicked.connect(self.compare)
        for column, widget in enumerate((self.pairing, self.strict, self.align, self.annotations)):
            advanced_layout.addWidget(widget, 0, column)
        advanced_layout.addWidget(self.password_inputs[0], 1, 0, 1, 2)
        advanced_layout.addWidget(self.password_inputs[1], 1, 2, 1, 2)
        settings.addWidget(self.advanced)
        limits = QLabel('每份最多 200 页 · 仅比较已保存文件'); limits.setObjectName('compareHint')
        settings.addWidget(limits); settings.addStretch(); settings.addWidget(self.start); source_layout.addLayout(settings)
        source_layout.addWidget(self.options_panel); self.options_panel.hide(); layout.addWidget(sources)
        self.advanced.toggled.connect(self.options_panel.setVisible)
        splitter = QSplitter()
        splitter.setChildrenCollapsible(False); splitter.setHandleWidth(12)
        page_panel = QFrame(); page_panel.setObjectName('comparePages'); page_panel.setMinimumWidth(225)
        page_layout = QVBoxLayout(page_panel); page_layout.setContentsMargins(12, 12, 12, 12); page_layout.setSpacing(10)
        section = QLabel('页面配对'); section.setObjectName('compareSection'); page_layout.addWidget(section)
        self.result_summary = QLabel('比较后在这里查看结果'); self.result_summary.setObjectName('compareHint')
        self.result_summary.setWordWrap(True); page_layout.addWidget(self.result_summary)
        self.rows = QListWidget(); self.rows.setObjectName('compareList'); self.rows.setSpacing(3)
        self.rows.setAccessibleName('页面配对与未配对页面')
        self.rows.currentRowChanged.connect(self.select_row); page_layout.addWidget(self.rows)
        self.adjust_pairs = QPushButton('调整页面配对'); self.adjust_pairs.setEnabled(False)
        self.adjust_pairs.clicked.connect(self.edit_pairing); page_layout.addWidget(self.adjust_pairs)
        splitter.addWidget(page_panel)
        viewer = QFrame(); viewer.setObjectName('workbench'); vlayout = QVBoxLayout(viewer)
        vlayout.setContentsMargins(12, 12, 12, 12); vlayout.setSpacing(10)
        display = QHBoxLayout(); self.mode = ClearComboBox()
        self.mode.addItems(['差异叠加', '旧版原页', '新版原页', '旧版差异框', '新版差异框'])
        self.mode.setFixedWidth(160); self.mode.setAccessibleName('预览内容')
        self.mode.currentIndexChanged.connect(self.display_image)
        fit = QPushButton('适合整页'); fit.clicked.connect(lambda: self.view.fit_page())
        display.addWidget(self.mode); display.addWidget(fit); display.addStretch()
        gestures = QLabel('Ctrl+滚轮缩放'); gestures.setObjectName('compareHint'); display.addWidget(gestures)
        vlayout.addLayout(display)
        self.identity = QLabel('文档预览'); self.identity.setObjectName('compareHint'); self.identity.setWordWrap(True)
        vlayout.addWidget(self.identity)
        self.view = CompareView(); vlayout.addWidget(self.view, 1)
        self.preview_empty = QLabel('选择旧版和新版 PDF\n点击“开始比较”查看差异')
        self.preview_empty.setObjectName('compareHint'); self.preview_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vlayout.addWidget(self.preview_empty, 1); self.view.hide()
        legend = QHBoxLayout(); legend.setSpacing(8)
        for color, text in (('#e03755', '旧版独有'), ('#009991', '新版独有'), ('#e88b14', '其他变化')):
            swatch = QFrame(); swatch.setFixedSize(10, 10); swatch.setStyleSheet(f'background: {color}; border-radius: 2px;')
            label = QLabel(text); label.setObjectName('compareHint')
            legend.addWidget(swatch); legend.addWidget(label); legend.addSpacing(8)
        legend.addStretch(); vlayout.addLayout(legend)
        self.regions = ClearComboBox(); self.regions.setAccessibleName('差异位置')
        self.regions.currentIndexChanged.connect(self.focus_region)
        nav = QHBoxLayout(); prev = QPushButton('上一处差异'); nxt = QPushButton('下一处差异')
        prev.clicked.connect(lambda: self.navigate(-1)); nxt.clicked.connect(lambda: self.navigate(1))
        nav.addWidget(prev); nav.addWidget(self.regions, 1); nav.addWidget(nxt); vlayout.addLayout(nav)
        self.preview_controls = (self.mode, fit, self.regions, prev, nxt)
        for widget in self.preview_controls:
            widget.setEnabled(False)
        splitter.addWidget(viewer); splitter.setStretchFactor(1, 1); layout.addWidget(splitter, 1)
        splitter.setSizes([250, 850])
        self.status = QLabel('未检出差异不代表内容完全相同，请核对重要细节。')
        self.status.setObjectName('compareHint'); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.file_drop = PdfFileDrop(self)
        for path in self.paths:
            path.textChanged.connect(self._refresh_start)
        self._refresh_start()

    def _refresh_start(self):
        self.start.setEnabled(not self.busy and all(path.text().strip() for path in self.paths))

    def show_help(self):
        QMessageBox.information(self, 'PDF 对比说明',
            '选择或拖入磁盘上的旧版与新版 PDF，点击“开始比较”。一次拖入两份时请核对版本顺序。'
            '原文件只读，不包含标签页未保存的修改。\n\n'
            '每份最多 200 页、512 MiB。红色表示旧版独有像素，青色表示新版独有像素，橙色表示其他变化；'
            '颜色不是文字增删或业务含义的判断。未配对页只表示可能新增或删除。\n\n'
            '页面对应不正确时，可在“调整页面配对”中指定有序页对。使用“上一处／下一处差异”逐项核对，'
            '也可切换原页；Ctrl+滚轮缩放，拖动画布平移。\n\n'
            '更多设置可调整配对方式、批注范围、严格模式和文件密码。默认过滤噪点可能漏掉细小变化；'
            '严格模式关闭过滤但不提高比较分辨率，缩放也不会增加检测精度。重要细节请核对原文件。')

    def choose(self, target):
        path, _ = QFileDialog.getOpenFileName(self, '选择 PDF', '', 'PDF (*.pdf)')
        if path:
            target.setText(path)

    def edit_pairing(self):
        if self.busy or self.summary is None:
            return
        dialog = PairingDialog(self.summary, self.summary['rows'][max(0, self.row)], self)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.compare(manual_pairs=dialog.manual_pairs, reuse=True)
        finally:
            dialog.deleteLater()

    def _set_busy(self, busy):
        self.busy = busy
        for widget in [self.start, self.rows, self.advanced, self.options_panel,
                       *self.paths, *self.password_inputs, *self.file_buttons]:
            widget.setEnabled(not busy)
        for widget in self.preview_controls:
            widget.setEnabled(not busy and self.images is not None)
        self._refresh_start()
        self.adjust_pairs.setEnabled(not busy and self.summary is not None)

    def compare(self, *, manual_pairs=None, reuse=False):
        if self.busy:
            return
        options = CompareOptions(threshold=0 if self.strict.isChecked() else 24,
            blur=0 if self.strict.isChecked() else .5, min_pixels=1 if self.strict.isChecked() else 8,
            align_pixels=2 if self.align.isChecked() else 0,
            annotations=self.annotations.isChecked(), pairing='auto' if self.pairing.currentIndex() == 0 else 'index')
        paths = [widget.text() for widget in self.paths]
        passwords = tuple(widget.text() for widget in self.password_inputs)
        if reuse:
            options = CompareOptions(**self.summary['options'])
            paths, passwords = self.summary['paths'], self.passwords
        previous = self.summary
        def work(progress, cancel):
            pairs = manual_pairs or ()
            reset_pairs = False
            inherited_pairs = False
            # Settings changes retain confirmed anchors only for the same, unchanged sources.
            # Resolve and stat off the GUI thread, just like preview validation.
            if manual_pairs is None and previous and previous.get('manual_pairs'):
                if [str(Path(path).resolve()) for path in paths] == previous['paths']:
                    try:
                        check_sources(previous)
                    except (OSError, ValueError):
                        reset_pairs = True
                    else:
                        pairs = previous['manual_pairs']
                        inherited_pairs = True
            result = compare_pdf_versions(*paths, options, progress, cancel,
                                          passwords=passwords, manual_pairs=pairs)
            if inherited_pairs and result['stamps'] != previous['stamps']:
                raise ValueError('来源文件在重用手工配对时发生变化，请重新比较')
            result['manual_pairs_reset'] = reset_pairs
            return result
        self._set_busy(True)
        try:
            summary = run_operation(self, '比较两份 PDF', work, cancellable=True)
            self.summary, self.passwords = summary, passwords
            self.preview_cache.clear()
            self.images = None; self.view.scene().clear(); self.view.marker = None
            self.identity.setText('比较已完成，选择页面查看'); self.regions.clear()
            self.row = -1
            self.rows.blockSignals(True); self.rows.clear()
            for row in summary['rows']:
                manual = ' · 手工指定' if [row['old_page'], row['new_page']] in summary['manual_pairs'] else ''
                self.rows.addItem(f"旧 {row['old_page'] or '无'} / 新 {row['new_page'] or '无'}{manual}\n{STATUS[row['status']]}")
                self.rows.item(self.rows.count() - 1).setSizeHint(QSize(0, 64))
            self.rows.blockSignals(False)
            counts = summary['counts']
            self.result_summary.setText(f"{len(summary['rows'])} 组页面 · {counts.get('changed', 0)} 组有差异\n"
                f"{counts.get('inserted', 0) + counts.get('deleted', 0)} 页未配对")
            self.status.setText(f"完成，用时 {summary['seconds']} 秒；有差异 {counts.get('changed', 0)} 组，"
                f"未检出差异 {counts.get('equal', 0)} 组，"
                f"未配对 {counts.get('inserted', 0) + counts.get('deleted', 0)} 页。")
        except OperationCancelled:
            self.status.setText('比较已取消；保留上次完成的结果，来源文件未修改。')
        except Exception as exc:
            QMessageBox.warning(self, '无法比较', str(exc))
        finally:
            self._set_busy(False)
        if self.summary is not None and self.row == -1:
            self.rows.setCurrentRow(0)

    def select_row(self, index):
        if self.busy or self.summary is None or index < 0:
            return
        previous = self.row
        self._set_busy(True)
        try:
            row, images = run_operation(self, '加载差异页面', lambda progress, cancel:
                self._load_preview(index, progress, cancel), cancellable=True)
            self.row, self.images = index, images
            self.preview_empty.hide(); self.view.show()
            self.identity.setText(f"旧版：{Path(self.summary['paths'][0]).name} 第 {row['old_page'] or '无'} 页　｜　"
                f"新版：{Path(self.summary['paths'][1]).name} 第 {row['new_page'] or '无'} 页　｜　{STATUS[row['status']]}")
            self.regions.blockSignals(True); self.regions.clear()
            for i, box in enumerate(row['regions']):
                self.regions.addItem(f'差异 {i + 1}', box)
            self.regions.setCurrentIndex(-1)
            self.regions.blockSignals(False)
            self.display_image()
            notices = list(row['warnings'])
            if self.summary.get('manual_pairs_reset'):
                notices.insert(0, '来源文件已变化，旧手工配对已清除，请重新核对')
            self.status.setText('；'.join(notices) or
                f"{len(row['regions'])} 处差异区域；缩放仅放大预览，重要细节请核对原文件。")
        except OperationCancelled:
            self.status.setText('页面加载已取消')
            self._restore_row(previous)
        except Exception as exc:
            QMessageBox.warning(self, '无法加载差异页', str(exc)); self._restore_row(previous)
        finally:
            self._set_busy(False)

    def _load_preview(self, index, progress, cancel):
        check_cancel(cancel)
        try:
            check_sources(self.summary)
        except Exception:
            self.preview_cache.clear()
            raise
        check_cancel(cancel)
        if index in self.preview_cache:
            self.preview_cache.move_to_end(index)
            return self.preview_cache[index]
        result = compare_pdf_versions(*self.summary['paths'], CompareOptions(**self.summary['options']),
            progress, cancel, passwords=self.passwords, summary=self.summary, row=index)
        self.preview_cache[index] = result
        while (len(self.preview_cache) > 2 or sum(image.width * image.height * 4
                for _, images in self.preview_cache.values() for image in images.values()) > self.preview_cache_limit):
            self.preview_cache.popitem(last=False)
        return result

    def _restore_row(self, index):
        self.rows.blockSignals(True); self.rows.setCurrentRow(index); self.rows.blockSignals(False)

    def display_image(self, *_):
        if self.images is not None:
            key = ['overlay', 'old', 'new', 'old_marked', 'new_marked'][self.mode.currentIndex()]
            self.view.show_image(self.images[key])
            self.focus_region(self.regions.currentIndex())

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
        self.preview_cache.clear()
        self.view.scene().clear(); self.view.marker = None
        for widget in self.password_inputs:
            widget.clear()
        super().done(result)

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
        else:
            self.done(QDialog.DialogCode.Rejected)
            event.accept()


def main():
    import sys
    from app.ui import STYLESHEET
    from app.pdfcontentcompareui import PdfContentCompareDialog, CONTENT_STYLE
    from app.runtime import preferences
    from app.themes import initialize_theme
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle('Fusion')
    initialize_theme(preferences())
    dialog = PdfContentCompareDialog()
    # Standalone preview uses the same inherited styles as the actual document menu.
    set_theme_style(dialog, STYLESHEET + CONTENT_STYLE)
    for widget, path in zip(dialog.paths, sys.argv[1:3]):
        widget.setText(path)
    dialog.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
