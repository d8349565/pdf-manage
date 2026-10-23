"""Review automatic scan angles page by page before applying a batch."""
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox,
                             QHeaderView, QHBoxLayout, QLabel,
                             QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
                             QVBoxLayout)

from app.deskew import analyze_pages, prepare_deskew
from app.output import OperationCancelled
from app.pagetoolsui import ClearDoubleSpinBox, PageScope
from app.pagepreviewlist import ScrollablePagePreview, draw_fitted_page
from app.pdfio import PdfError
from app.progress import run_operation


class DeskewDialog(QDialog):
    def __init__(self, host):
        super().__init__(host)
        self.host, self.pages = host, tuple(host.ws.pages)
        self.rows, self.controls, self.estimates = [], {}, {}
        self.loading, self.preview_row, self.analyzing = False, None, False
        self.setWindowTitle('扫描纠偏')
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        bar.addWidget(QLabel('处理范围'))
        self.scope = PageScope(self.pages, host.ws.target_indices())
        bar.addWidget(self.scope, 1)
        self.analyze_button = QPushButton('自动分析角度')
        self.analyze_button.clicked.connect(self.analyze)
        bar.addWidget(self.analyze_button)
        layout.addLayout(bar)
        body = QHBoxLayout()
        left = QVBoxLayout()
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['应用', '页码', '分析结果', '旋转角度'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for column, width in ((0, 44), (1, 52), (3, 108)):
            self.table.setColumnWidth(column, width)
        self.table.setMinimumSize(430, 330)
        self.table.setAccessibleName('逐页纠偏角度与确认状态')
        left.addWidget(self.table)
        instruction = QLabel('正数顺时针、负数逆时针，支持 ±10°。右侧滚动查看上下页；不可靠建议需勾选确认。')
        instruction.setWordWrap(True)
        left.addWidget(instruction)
        body.addLayout(left, 1)
        right = QVBoxLayout()
        self.following_preview = False
        self.preview = ScrollablePagePreview(host.store, self.pages, draw_content=self.draw_preview)
        self.preview.page_changed.connect(self.follow_preview)
        right.addWidget(self.preview)
        toggles = QHBoxLayout()
        self.grid = QCheckBox('显示水平参考线')
        self.grid.setChecked(True)
        self.original = QCheckBox('查看原页')
        toggles.addWidget(self.grid)
        toggles.addWidget(self.original)
        right.addLayout(toggles)
        body.addLayout(right, 1)
        layout.addLayout(body)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        hint = QLabel('保留原页尺寸，旋转后等比缩小并补白，避免裁掉边角。可撤销，保存时另存副本。'
                      '含批注、印章或表单的页会跳过；请先纠偏再添加。不处理拍照透视或弯曲书页。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('应用已勾选页面')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.table.currentCellChanged.connect(self.load_preview)
        self.table.itemChanged.connect(self.sync)
        self.grid.toggled.connect(self.sync)
        self.original.toggled.connect(self.sync)
        self.scope.mode.currentIndexChanged.connect(self.reset_rows)
        self.scope.range.textChanged.connect(self.reset_rows)
        self.reset_rows()
        QTimer.singleShot(0, self.analyze)

    def reset_rows(self, *_):
        if self.analyzing:
            return
        self.loading = True
        self.preview_row, self.estimates, self.controls = None, {}, {}
        try:
            self.rows = self.scope.indices()
            self.table.setRowCount(len(self.rows))
            for index, row in enumerate(self.rows):
                checked = QTableWidgetItem()
                checked.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                checked.setCheckState(Qt.CheckState.Unchecked)
                self.table.setItem(index, 0, checked)
                self.table.setItem(index, 1, QTableWidgetItem(str(row + 1)))
                self.table.setItem(index, 2, QTableWidgetItem('待分析，也可手动设置'))
                spin = ClearDoubleSpinBox()
                spin.setRange(-10, 10)
                spin.setDecimals(1)
                spin.setSingleStep(.1)
                spin.setSuffix('°')
                spin.setAccessibleName(f'第 {row + 1} 页纠偏角度')
                spin.valueChanged.connect(lambda _, i=index: self.manual_angle(i))
                self.table.setCellWidget(index, 3, spin)
                self.controls[row] = spin
            self.analyze_button.setEnabled(bool(self.rows))
        except ValueError as exc:
            self.rows = []
            self.table.setRowCount(0)
            self.status.setText(str(exc))
            self.analyze_button.setEnabled(False)
        finally:
            self.loading = False
        if self.rows:
            self.preview.set_rows(self.rows)
            self.table.setCurrentCell(0, 1)
            self.load_preview()
        else:
            self.preview.set_rows([])
        self.sync()

    def analyze(self):
        if not self.isVisible() or not self.rows or self.analyzing:
            return
        self.analyzing = True
        self.analyze_button.setEnabled(False)
        self.scope.setEnabled(False)
        self.table.setEnabled(False)
        self.buttons.setEnabled(False)
        indices, notice = tuple(self.rows), ''
        try:
            estimates = run_operation(self, '分析扫描角度', lambda progress, cancel:
                analyze_pages(self.host.store, self.pages, indices, progress, cancel), cancellable=True)
            self.loading = True
            self.estimates = estimates
            for index, row in enumerate(self.rows):
                value = estimates[row]
                self.controls[row].setValue(value.angle)
                self.table.item(index, 2).setText(value.message)
                self.table.item(index, 2).setToolTip(value.message)
                enabled = value.status != 'unsupported'
                self.controls[row].setEnabled(enabled)
                self.table.item(index, 0).setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                                                  if enabled else Qt.ItemFlag.NoItemFlags)
                self.table.item(index, 0).setCheckState(Qt.CheckState.Checked
                    if value.status == 'ready' and abs(value.angle) >= .05 else Qt.CheckState.Unchecked)
        except OperationCancelled:
            notice = '分析已取消，页面未修改；可重新分析或手动设置。'
        except (ValueError, PdfError) as exc:
            QMessageBox.warning(self, '无法分析扫描角度', str(exc))
        finally:
            self.loading = False
            self.analyzing = False
            self.scope.setEnabled(True)
            self.table.setEnabled(True)
            self.buttons.setEnabled(True)
            self.analyze_button.setEnabled(bool(self.rows))
        self.sync()
        if notice:
            self.status.setText(notice)

    def reject(self):
        if self.analyzing:
            task = getattr(self, '_task_dialog', None)
            if task is not None:
                task.request_cancel()
            return
        super().reject()

    def manual_angle(self, index):
        if self.loading:
            return
        row = self.rows[index]
        self.table.item(index, 0).setCheckState(Qt.CheckState.Checked if abs(self.controls[row].value()) >= .05
                                               else Qt.CheckState.Unchecked)
        self.table.item(index, 2).setText('手动设置，请核对右侧预览')
        self.sync()

    def angles(self):
        return {row: self.controls[row].value() for index, row in enumerate(self.rows)
                if self.table.item(index, 0).checkState() == Qt.CheckState.Checked
                and self.controls[row].isEnabled() and abs(self.controls[row].value()) >= .05}

    def load_preview(self, *_):
        if self.loading:
            return
        index = self.table.currentRow()
        if not 0 <= index < len(self.rows):
            return
        row = self.rows[index]
        self.preview_row = row
        if not self.following_preview:
            self.preview.scroll_to_page(row)
        self.sync()

    def follow_preview(self, row):
        if row in self.rows:
            self.following_preview = True
            self.table.setCurrentCell(self.rows.index(row), 1)
            self.preview_row = row
            self.following_preview = False

    def draw_preview(self, painter, rectangle, row, image):
        from PyQt6.QtGui import QColor, QPen
        angle = 0 if self.original.isChecked() else self.controls[row].value()
        draw_fitted_page(painter, rectangle, image, angle)
        if self.grid.isChecked():
            painter.save()
            painter.setPen(QPen(QColor(20, 120, 200, 110), 1))
            y = rectangle.y() + 25
            while y < rectangle.bottom():
                painter.drawLine(int(rectangle.left()), int(y), int(rectangle.right()), int(y))
                y += 25
            painter.restore()

    def done(self, result):
        self.preview.shutdown()
        super().done(result)

    def sync(self, *_):
        if self.loading:
            return
        count = len(self.angles())
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(count > 0)
        if self.rows:
            review = sum(value.status == 'review' for value in self.estimates.values())
            skipped = sum(value.status in ('blank', 'unsupported') for value in self.estimates.values())
            self.status.setText(f'共 {len(self.rows)} 页 · 已勾选 {count} 页 · 待确认 {review} 页 · 跳过 {skipped} 页')
        self.preview.refresh_pages()


def open_deskew(host):
    if not host.ws.pages or not host._allow_operation('content_edit'):
        return
    before = tuple(host.ws.pages)
    dialog = DeskewDialog(host)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        dialog.deleteLater()
        return
    angles = dialog.angles()
    dialog.deleteLater()
    try:
        changes = run_operation(host, '应用扫描纠偏', lambda progress, cancel:
            prepare_deskew(host.store, before, angles, progress, cancel), cancellable=True)
        if tuple(host.ws.pages) != before:
            raise ValueError('工作区已改变，请重新操作。')
        host.ws.edit_pages(changes, '扫描纠偏')
        host.refresh()
        host.statusBar().showMessage(f'已纠偏 {len(changes)} 页；Ctrl+Z 可撤销，保存时另存副本。', 6500)
    except OperationCancelled:
        host.statusBar().showMessage('已取消，页面未修改。', 5000)
    except (ValueError, PdfError) as exc:
        QMessageBox.warning(host, '无法纠偏', str(exc))
