"""Merge current document workspaces into a separate PDF without editing the tabs."""
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QVBoxLayout,
)
from PyQt6.QtGui import QColor, QFont, QPalette

from app.icons import toolbar_icon
from app.output import OperationCancelled, check_cancel
from app.pdfio import PdfError, WriteOptions
from app.progress import run_operation
from app.themes import set_theme_style, ui_color
from app.workspace import OutlineEntry, Workspace


def is_pdf_workspace(workspace):
    if workspace.path:
        return workspace.path.lower().endswith('.pdf')
    return any(page.source_path.lower().endswith('.pdf') for page in workspace.pages)


def workspace_name(workspace):
    return Path(workspace.path).name if workspace.path else '未命名文档'


def merge_pages(store, tabs):
    pages, outline = [], []
    for tab in tabs:
        original = tab.ws.pages
        incoming = Workspace._unique_pages(original, pages)
        targets = {before.page_id: after.page_id for before, after in zip(original, incoming)}
        entries = tab.ws.outline
        if entries is None:
            entries = store.outline_entries(original, group_sources=False)
        outline.append(OutlineEntry(1, Path(workspace_name(tab.ws)).stem, incoming[0].page_id))
        outline.extend(replace(entry, level=entry.level + 1, target_id=targets.get(entry.target_id))
                       for entry in entries)
        pages.extend(incoming)
    return pages, tuple(outline)


class MergeFileDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        # initStyleOption normalizes newlines to Unicode line separators.
        title, _, detail = index.data(Qt.ItemDataRole.DisplayRole).partition('\n')
        style = opt.widget.style()
        text_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, opt.widget)
        opt.text = ''
        painter.save()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        painter.restore()
        painter.save()
        painter.setClipRect(option.rect)
        font = QFont(opt.font)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        enabled = bool(opt.state & QStyle.StateFlag.State_Enabled)
        painter.setPen(QColor(ui_color('#173C36')) if enabled else opt.palette.color(
            QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text))
        width = max(0, text_rect.width() - 8)
        painter.drawText(QRect(text_rect.left(), opt.rect.top() + 6, width, 22), Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideMiddle, width))
        painter.setFont(opt.font)
        painter.setPen(QColor(ui_color('#4D6B67')))
        painter.drawText(QRect(text_rect.left(), opt.rect.top() + 29, width, 22), Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(detail, Qt.TextElideMode.ElideRight, width))
        painter.restore()


class MergeOpenDialog(QDialog):
    def __init__(self, parent, sources):
        from app.exportdialogs import COMPACT_QSS
        super().__init__(parent)
        self.setWindowTitle('合并已打开的文件')
        self.setObjectName('mergeOpen')
        self.resize(660, 500)
        self.setMinimumSize(520, 420)
        self.sources = {source['tab_id']: source for source in sources}
        self.output_path = None
        set_theme_style(self, COMPACT_QSS + '''
            QDialog#mergeOpen { background: #FFFFFF; }
            QListWidget#mergeFiles { background: #FAFCFB; border: 1px solid #D7E8E4;
                border-radius: 8px; padding: 4px; outline: none; }
            QListWidget#mergeFiles::item { height: 58px; padding: 0px 8px;
                border-radius: 5px; }
            QListWidget#mergeFiles::item:hover { background: #F0FDFA; }
            QListWidget#mergeFiles::item:selected { background: #E1F3EF; }
        ''')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)
        title = QLabel('合并已打开的文件')
        set_theme_style(title, 'font-size: 20px; font-weight: 600; color: #173C36;')
        layout.addWidget(title)
        instruction = QLabel('选择文件并调整顺序，合并结果将另存为新 PDF。')
        instruction.setObjectName('hint')
        instruction.setWordWrap(True)
        layout.addWidget(instruction)
        row = QHBoxLayout()
        row.addWidget(QLabel('文件顺序'), 1)
        self.select_all_button = QPushButton('全选')
        self.select_all_button.setProperty('compact', True)
        self.select_all_button.clicked.connect(lambda: self.select_all(
            len(self.selected_ids()) != sum(not source['reason'] for source in self.sources.values())))
        row.addWidget(self.select_all_button)
        layout.addLayout(row)
        self.files = QListWidget()
        self.files.setObjectName('mergeFiles')
        self.files.setItemDelegate(MergeFileDelegate(self.files))
        self.files.setUniformItemSizes(True)
        self.files.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.files.setAccessibleName('待合并文件，勾选并拖动排序')
        self.files.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.files.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.files.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        for source in sources:
            details = f"{source['pages']} 页" + (' · 未保存修改' if source['dirty'] else '')
            if source['reason']:
                details += ' · ' + source['reason']
            item = QListWidgetItem(source['name'] + '\n' + details)
            item.setData(Qt.ItemDataRole.UserRole, source['tab_id'])
            item.setIcon(toolbar_icon('grip-vertical', '#526B63', 16))
            item.setSizeHint(QSize(0, 58))
            item.setToolTip(source['path'] + ('\n' + source['reason'] if source['reason'] else ''))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked if source['reason'] else Qt.CheckState.Checked)
            if source['reason']:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.files.addItem(item)
        layout.addWidget(self.files, 1)
        move_row = QHBoxLayout()
        self.move_up = QPushButton('上移')
        self.move_down = QPushButton('下移')
        for button, delta in ((self.move_up, -1), (self.move_down, 1)):
            button.setProperty('compact', True)
            button.clicked.connect(lambda unused=False, step=delta: self.move_selected(step))
            move_row.addWidget(button)
        move_row.addStretch()
        layout.addLayout(move_row)
        note = QLabel('拖动文件或选中后点上移／下移排序。'
                      '\n包含未保存修改，原文件和原标签保持不变。')
        note.setObjectName('hint')
        note.setWordWrap(True)
        layout.addWidget(note)
        footer = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        footer.addWidget(self.summary, 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.start = box.addButton('合并并另存', QDialogButtonBox.ButtonRole.ActionRole)
        self.start.setObjectName('primary')
        self.start.setDefault(True)
        self.start.clicked.connect(self.choose_output)
        box.rejected.connect(self.reject)
        footer.addWidget(box)
        layout.addLayout(footer)
        self.files.itemChanged.connect(self.refresh)
        self.files.currentRowChanged.connect(self.refresh)
        self.files.model().rowsMoved.connect(self.refresh)
        first = next((row for row in range(self.files.count())
                      if self.files.item(row).flags() & Qt.ItemFlag.ItemIsEnabled), -1)
        self.files.setCurrentRow(first)
        self.refresh()

    def selected_ids(self):
        return [self.files.item(row).data(Qt.ItemDataRole.UserRole) for row in range(self.files.count())
                if self.files.item(row).checkState() == Qt.CheckState.Checked
                and self.files.item(row).flags() & Qt.ItemFlag.ItemIsEnabled]

    def refresh(self):
        selected = self.selected_ids()
        pages = sum(self.sources[tid]['pages'] for tid in selected)
        self.summary.setText(f'已选择 {len(selected)} 份 · 共 {pages} 页'
                             + ('\n至少选择 2 份 PDF。' if len(selected) < 2 else ''))
        eligible = sum(not source['reason'] for source in self.sources.values())
        self.select_all_button.setText('取消全选' if eligible and len(selected) == eligible else '全选')
        self.select_all_button.setEnabled(eligible > 0)
        self.start.setEnabled(len(selected) >= 2)
        row = self.files.currentRow()
        self.move_up.setEnabled(row > 0)
        self.move_down.setEnabled(0 <= row < self.files.count() - 1)

    def select_all(self, checked):
        for row in range(self.files.count()):
            item = self.files.item(row)
            if item.flags() & Qt.ItemFlag.ItemIsEnabled:
                item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def move_selected(self, delta):
        row = self.files.currentRow()
        if 0 <= row < self.files.count() and 0 <= row + delta < self.files.count():
            item = self.files.takeItem(row)
            self.files.insertItem(row + delta, item)
            self.files.setCurrentItem(item)
            self.refresh()

    def choose_output(self):
        if len(self.selected_ids()) < 2:
            return
        from app.exportdialogs import new_pdf_path
        path = new_pdf_path(self, self.parent().store, '合并并另存', '合并文件.pdf')
        if path:
            self.output_path = path
            self.accept()


def merge_open_files(host):
    if host._saving or getattr(host, '_task_dialog', None) is not None:
        return False
    tools = getattr(host, 'annotation_tools', None)
    if tools is not None and not tools.finish_text_box():
        return False
    host._saving = True
    try:
        host._save_current_tab_state()
        tabs = [tab for tab in host._tabs if tab.ws.pages or tab.ws.path]
        snapshots = {tab.tab_id: tab.ws._fingerprint() for tab in tabs}
        refs = {page.source_id: page for tab in tabs for page in tab.ws.pages}
        def prepare(progress, cancel):
            for ref in refs.values():
                check_cancel(cancel)
                while not host.store.prepare_safety_step(ref):
                    check_cancel(cancel)
                    progress(0, 0, '正在检查原文件保护信息')
        run_operation(host, '检查合并来源', prepare, cancellable=True)
        sources = []
        for tab in tabs:
            ws = tab.ws
            reason = ('首期支持 PDF 标签，请先另存为 PDF。' if not is_pdf_workspace(ws) else
                      '没有可合并的页面。' if not ws.pages else '')
            reason = reason or host.store.operation_reason(ws.pages, 'copy') or host.store.operation_reason(ws.pages, 'modify')
            sources.append(dict(tab_id=tab.tab_id, name=workspace_name(ws), path=ws.path or workspace_name(ws),
                                pages=len(ws.pages), dirty=ws.dirty, reason=reason))
        dialog = MergeOpenDialog(host, sources)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            ids, path = dialog.selected_ids(), dialog.output_path
        finally:
            dialog.deleteLater()
        selected = [next(tab for tab in tabs if tab.tab_id == tid) for tid in ids]
        if len(selected) < 2 or not path:
            return False
        if any(tab not in host._tabs or tab.ws._fingerprint() != snapshots[tab.tab_id] for tab in selected):
            raise PdfError('来源工作区已改变，请重新选择合并文件。')
        if host.store.is_source_destination(path):
            raise PdfError('不能覆盖已打开的来源文件，请选择新的文件名。')
        pages, outline = merge_pages(host.store, selected)
        host.store.require_operation(pages, 'copy')
        host.store.require_operation(pages, 'modify')
        result = host._write_pdf(pages, path, '合并已打开的文件', options=WriteOptions(outline=outline))
        if result is None:
            return False
        opened = host.open_files_in_tabs([result.path])
        if result.warnings:
            QMessageBox.information(host, '合并完成 · 请核对', '\n'.join(result.warnings))
        host.statusBar().showMessage('已合并并打开新 PDF。' if opened else
                                    f'已保存合并结果，可从“文件 → 打开”查看：{result.path}', 7000)
        return True
    except OperationCancelled as exc:
        host.statusBar().showMessage(str(exc), 5000)
        return False
    except (PdfError, OSError) as exc:
        QMessageBox.warning(host, '未能完成合并', str(exc))
        return False
    finally:
        host._saving = False
