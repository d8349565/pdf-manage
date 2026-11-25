"""Merge current workspaces, saving a new PDF by default or replacing a chosen one."""
from app.i18n import tr
import os
from dataclasses import replace
from itertools import zip_longest
from pathlib import Path

from PyQt6.QtCore import QRect, QSignalBlocker, QSize, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QStyle, QStyledItemDelegate,
    QSpinBox, QStackedWidget, QStyleOptionViewItem, QToolButton, QVBoxLayout,
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


def merge_order(groups, interleave):
    if interleave:
        if len(groups) != 2:
            raise PdfError(tr('交叉合并需要勾选两份 PDF。'))
        return [page for pair in zip_longest(*groups) for page in pair if page is not None]
    return [page for group in groups for page in group]


def merge_pages(store, tabs, *, interleave=False):
    pages, groups, outline = [], [], []
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
        groups.append(incoming)
    return merge_order(groups, interleave), tuple(outline)


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
    def __init__(self, parent, sources, *, interleave=False):
        from app.exportdialogs import COMPACT_QSS
        super().__init__(parent)
        self.setWindowTitle(tr('交叉合并两个 PDF') if interleave else tr('合并已打开的文件'))
        self.setObjectName('mergeOpen')
        self.resize(960, 620)
        self.sources = {source['tab_id']: source for source in sources}
        self.files_from_disk = bool(sources) and all(source.get('from_disk') for source in sources)
        self.output_path = None
        self.overwrite_tab_id = None
        self.remember_overwrite = False
        set_theme_style(self, COMPACT_QSS + '''
            QDialog#mergeOpen { background: #FFFFFF; }
            QListWidget#mergeFiles { background: #FAFCFB; border: 1px solid #D7E8E4;
                border-radius: 8px; padding: 4px; outline: none; }
            QListWidget#mergeFiles::item { height: 58px; padding: 0px 8px;
                border-radius: 5px; }
            QListWidget#mergeFiles::item:hover { background: #F0FDFA; }
            QListWidget#mergeFiles::item:selected { background: #E1F3EF; }
        ''')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        columns = QHBoxLayout()
        columns.setSpacing(18)
        outer.addLayout(columns, 1)
        layout = QVBoxLayout()
        columns.addLayout(layout, 4)
        layout.setSpacing(10)
        title = QLabel(self.windowTitle())
        set_theme_style(title, 'font-size: 20px; font-weight: 600; color: #173C36;')
        layout.addWidget(title)
        self.instruction = QLabel()
        self.instruction.setObjectName('hint')
        self.instruction.setWordWrap(True)
        layout.addWidget(self.instruction)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr('文件顺序')), 1)
        self.select_all_button = QPushButton(tr('全选'))
        self.select_all_button.setProperty('compact', True)
        self.select_all_button.clicked.connect(lambda: self.select_all(
            len(self.selected_ids()) != sum(not source['reason'] for source in self.sources.values())))
        row.addWidget(self.select_all_button)
        layout.addLayout(row)
        self.files = QListWidget()
        self.files.setObjectName('mergeFiles')
        self.files.setItemDelegate(MergeFileDelegate(self.files))
        self.files.setUniformItemSizes(True)
        self.files.setMinimumHeight(132)
        self.files.setMaximumHeight(max(142, min(330, len(sources) * 60 + 16)))
        self.files.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.files.setAccessibleName(tr('待合并文件，勾选并拖动排序'))
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
        self.move_up = QPushButton(tr('上移'))
        self.move_down = QPushButton(tr('下移'))
        for button, delta in ((self.move_up, -1), (self.move_down, 1)):
            button.setProperty('compact', True)
            button.clicked.connect(lambda unused=False, step=delta: self.move_selected(step))
            move_row.addWidget(button)
        move_row.addStretch()
        layout.addLayout(move_row)
        self.note = QLabel(tr('拖动文件或选中后点上移／下移排序。\n包含未保存修改，原文件和原标签保持不变。'))
        self.note.setObjectName('hint')
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        form = self.save_form = QFormLayout()
        self.page_order = QComboBox()
        self.page_order.addItem(tr('按文件顺序合并'), False)
        self.page_order.addItem(tr('逐页交叉合并'), True)
        self.page_order.setCurrentIndex(int(interleave))
        form.addRow(tr('页面顺序'), self.page_order)
        self.order_hint = QLabel()
        self.order_hint.setTextFormat(Qt.TextFormat.PlainText)
        self.order_hint.setWordWrap(True)
        form.addRow(self.order_hint)
        self.save_mode = QComboBox()
        self.save_mode.setAccessibleName(tr('合并结果保存方式'))
        self.save_mode.addItem(tr('另存为新文件（默认）'), 'copy')
        self.save_mode.addItem(tr('覆盖指定文件'), 'overwrite')
        form.addRow(tr('保存方式'), self.save_mode)
        layout.addLayout(form)
        self.target_caption = QLabel(tr('覆盖到'))
        self.target = QComboBox()
        self.target.setAccessibleName(tr('合并覆盖目标'))
        self.target.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.target.setMinimumContentsLength(24)
        form.addRow(self.target_caption, self.target)
        self.target_path = QLabel()
        self.target_path.setTextFormat(Qt.TextFormat.PlainText)
        self.target_path.setWordWrap(True)
        self.target_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(self.target_path)
        layout.addStretch()
        preview_column = QVBoxLayout()
        columns.addLayout(preview_column, 6)
        preview_title = QLabel(tr('合并结果预览'))
        set_theme_style(preview_title, 'font-size: 16px; font-weight: 600; color: #173C36;')
        preview_header = QHBoxLayout()
        preview_header.addWidget(preview_title)
        preview_header.addStretch()
        preview_column.addLayout(preview_header)
        from app.pagepreviewlist import ScrollablePagePreview
        refs, captions = [], []
        self.source_rows = {}
        for source in sources:
            first = len(refs)
            refs.extend(source.get('refs', ()))
            self.source_rows[source['tab_id']] = range(first, len(refs))
            captions.extend((source['name'], n + 1) for n in range(len(refs) - first))
        self.preview = ScrollablePagePreview(parent.store, refs, page_caption=lambda row:
            tr('{name} · 第 {page} 页', name=captions[row][0], page=captions[row][1]))
        self.preview.setMinimumSize(250, 180)
        self.preview.set_fit_page(True)
        self.preview_stack = QStackedWidget()
        self.preview_stack.addWidget(self.preview)
        self.empty_preview = QLabel()
        self.empty_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_preview.setWordWrap(True)
        self.preview_stack.addWidget(self.empty_preview)
        preview_column.addWidget(self.preview_stack, 1)
        self.previous_page = QToolButton()
        self.previous_page.setIcon(toolbar_icon('chevron-left'))
        self.previous_page.setToolTip(tr('上一页'))
        self.previous_page.setAccessibleName(tr('上一页'))
        preview_header.addWidget(self.previous_page)
        self.preview_page = QSpinBox()
        self.preview_page.setAccessibleName(tr('合并结果页码'))
        self.preview_page.setKeyboardTracking(False)
        self.preview_page.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        preview_header.addWidget(self.preview_page)
        self.next_page = QToolButton()
        self.next_page.setIcon(toolbar_icon('chevron-right'))
        self.next_page.setToolTip(tr('下一页'))
        self.next_page.setAccessibleName(tr('下一页'))
        preview_header.addWidget(self.next_page)
        self.previous_page.clicked.connect(self.preview_page.stepDown)
        self.next_page.clicked.connect(self.preview_page.stepUp)
        self.preview_page.valueChanged.connect(self.go_to_preview_page)
        self.preview.page_changed.connect(self.preview_scrolled)
        self.preview_plan = None
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        set_theme_style(divider, 'color: #D7E8E4;')
        outer.addWidget(divider)
        footer = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        footer.addWidget(self.summary, 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Cancel).setText(tr('取消'))
        self.start = box.addButton(tr('合并并另存'), QDialogButtonBox.ButtonRole.ActionRole)
        self.start.setObjectName('primary')
        self.start.setDefault(True)
        self.start.clicked.connect(self.choose_output)
        box.rejected.connect(self.reject)
        footer.addWidget(box)
        outer.addLayout(footer)
        self.files.itemChanged.connect(self.refresh)
        self.files.currentRowChanged.connect(self.refresh)
        self.files.model().rowsMoved.connect(self.refresh)
        self.page_order.currentIndexChanged.connect(self.refresh_target)
        self.save_mode.currentIndexChanged.connect(self.refresh_target)
        self.target.currentIndexChanged.connect(self.refresh_target)
        first = next((row for row in range(self.files.count())
                      if self.files.item(row).flags() & Qt.ItemFlag.ItemIsEnabled), -1)
        self.files.setCurrentRow(first)
        self.refresh()

    def selected_ids(self):
        return [self.files.item(row).data(Qt.ItemDataRole.UserRole) for row in range(self.files.count())
                if self.files.item(row).checkState() == Qt.CheckState.Checked
                and self.files.item(row).flags() & Qt.ItemFlag.ItemIsEnabled]

    def refresh_preview(self, selected, interleave, valid):
        plan = tuple(selected), interleave
        if plan == self.preview_plan:
            return
        self.preview_plan = plan
        rows = merge_order([self.source_rows[tid] for tid in selected], interleave) if valid else []
        self.preview.set_rows(rows)
        self.preview_stack.setCurrentWidget(self.preview if rows else self.empty_preview)
        if not rows:
            self.empty_preview.setText(tr('交叉合并需要勾选两份 PDF。') if interleave else tr('至少选择 2 份 PDF。'))
        with QSignalBlocker(self.preview_page):
            self.preview_page.setRange(1 if rows else 0, len(rows))
            self.preview_page.setSuffix(f' / {len(rows)}')
            self.preview_page.setValue(1 if rows else 0)
        self.preview_page.setEnabled(bool(rows))
        self.go_to_preview_page(self.preview_page.value())

    def go_to_preview_page(self, number):
        if number:
            self.preview.scroll_to_page(self.preview.rows[number - 1])
        self.previous_page.setEnabled(number > 1)
        self.next_page.setEnabled(number < len(self.preview.rows))

    def preview_scrolled(self, row):
        number = self.preview.rows.index(row) + 1
        with QSignalBlocker(self.preview_page):
            self.preview_page.setValue(number)
        self.previous_page.setEnabled(number > 1)
        self.next_page.setEnabled(number < len(self.preview.rows))

    def done(self, result):
        self.preview.shutdown()
        super().done(result)

    def includes_signature(self, selected):
        return any(self.sources[tid].get('signed') for tid in selected)

    def refresh(self):
        selected = self.selected_ids()
        previous = self.target.currentData()
        first_target = self.target.count() == 0
        self.target.blockSignals(True)
        self.target.clear()
        self.target.addItem(tr('请选择要覆盖的文件'), None)
        for tid in selected:
            source = self.sources[tid]
            if source.get('save_path'):
                self.target.addItem(source['name'], tid)
        preferred = previous if previous in selected else (next(
            (tid for tid in selected if self.sources[tid].get('active')), None) if first_target else None)
        index = self.target.findData(preferred)
        self.target.setCurrentIndex(max(0, index))
        self.target.blockSignals(False)
        self.refresh_target()
        pages = sum(self.sources[tid]['pages'] for tid in selected)
        self.summary.setText(tr('已选择 {p0} 份 · 共 {p1} 页', p0=f'{len(selected)}', p1=f'{pages}')
                             + (tr('\n至少选择 2 份 PDF。') if len(selected) < 2 else '')
                             + ('\n' + tr('保留原文件，合并件不含原数字签名。') if self.includes_signature(selected) else ''))
        eligible = sum(not source['reason'] for source in self.sources.values())
        self.select_all_button.setText(tr('取消全选') if eligible and len(selected) == eligible else tr('全选'))
        self.select_all_button.setEnabled(eligible > 0)
        row = self.files.currentRow()
        self.move_up.setEnabled(row > 0)
        self.move_down.setEnabled(0 <= row < self.files.count() - 1)

    def refresh_target(self):
        # Updating a selection must not clear the combo's model in its own signal.
        selected = self.selected_ids()
        signed_copy = self.includes_signature(selected)
        interleave = self.page_order.currentData()
        valid_count = len(selected) == 2 if interleave else len(selected) >= 2
        self.refresh_preview(selected, interleave, valid_count)
        self.save_form.setRowVisible(self.order_hint, interleave)
        if interleave:
            self.order_hint.setText(tr('先取「{p0}」一页，再取「{p1}」一页，交替到末尾。多出的页接在最后。',
                                       p0=self.sources[selected[0]]['name'], p1=self.sources[selected[1]]['name'])
                                    if valid_count else tr('交叉合并需要勾选两份 PDF。'))
        copy_only = any(self.sources[tid].get('requires_copy') for tid in selected)
        self.instruction.setText(tr('选择文件并调整顺序。') if signed_copy or copy_only or self.files_from_disk else
                                 tr('选择文件并调整顺序。默认另存为新 PDF，也可选择覆盖其中一份文件。'))
        eligible_target = self.target.count() > 1 and not (signed_copy or copy_only)
        self.save_mode.model().item(1).setEnabled(eligible_target)
        if signed_copy and self.save_mode.currentData() != 'copy':
            blocked = self.save_mode.blockSignals(True)
            self.save_mode.setCurrentIndex(0)
            self.save_mode.blockSignals(blocked)
        self.save_form.setRowVisible(self.save_mode, not signed_copy and not self.files_from_disk)
        overwrite = self.save_mode.currentData() == 'overwrite'
        target = self.sources.get(self.target.currentData(), {})
        for widget in (self.target_caption, self.target, self.target_path):
            widget.setVisible(overwrite)
        self.target_path.setText(target.get('save_path', '') if eligible_target else
                                 tr('所选文件目前仅支持另存新文件，请切换保存方式。'))
        self.target_path.setToolTip(target.get('save_path', ''))
        self.note.setVisible(not self.files_from_disk and (not signed_copy or any(self.sources[tid]['dirty'] for tid in selected)))
        if signed_copy:
            self.start.setText(tr('合并为副本'))
            self.note.setText(tr('合并包含未保存修改。'))
            self.start.setEnabled(valid_count)
            return
        self.start.setText(tr('合并并覆盖') if overwrite else tr('合并并另存'))
        self.note.setText(tr('合并包含未保存修改。仅覆盖指定文件，其他文件和标签保留。') if overwrite else
                          tr('拖动文件或选中后点上移／下移排序。\n包含未保存修改，原文件和原标签保持不变。'))
        if not eligible_target:
            self.note.setText(self.note.text() + (tr('\n所选内容需要另存副本，暂不支持覆盖。') if copy_only else
                                                 tr('\n所选标签没有可覆盖的文件位置，请另存新文件。')))
        self.start.setEnabled(valid_count and (not overwrite or eligible_target and bool(target.get('save_path'))))

    def select_all(self, checked):
        with QSignalBlocker(self.files):
            for row in range(self.files.count()):
                item = self.files.item(row)
                if item.flags() & Qt.ItemFlag.ItemIsEnabled:
                    item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.refresh()

    def move_selected(self, delta):
        row = self.files.currentRow()
        if 0 <= row < self.files.count() and 0 <= row + delta < self.files.count():
            blocked = self.files.blockSignals(True)
            item = self.files.takeItem(row)
            self.files.insertItem(row + delta, item)
            self.files.setCurrentItem(item)
            self.files.blockSignals(blocked)
            self.refresh()

    def choose_output(self):
        selected = self.selected_ids()
        if not self.start.isEnabled():
            return
        signed_copy = self.includes_signature(selected)
        if not signed_copy and self.save_mode.currentData() == 'overwrite':
            from app.savesettings import confirm_overwrite
            tid = self.target.currentData()
            source = self.sources.get(tid, {})
            path = source.get('save_path')
            if not self.start.isEnabled() or tid not in selected or not path:
                return
            if source.get('saved_target') != path:
                path, remember = confirm_overwrite(self, [path], self.parent()._window_settings)
                if not path:
                    return
                self.remember_overwrite = remember
            self.output_path = path
            self.overwrite_tab_id = tid
            self.accept()
            return
        from app.exportdialogs import new_pdf_path
        path = new_pdf_path(self, self.parent().store, self.start.text(),
                            tr('合并副本.pdf') if signed_copy else tr('合并文件.pdf'))
        if path:
            self.output_path = path
            self.accept()


def merge_open_files(host, *, tabs=None, interleave=False):
    if host._saving or getattr(host, '_task_dialog', None) is not None:
        return False
    tools = getattr(host, 'annotation_tools', None)
    if tools is not None and not tools.finish_text_box():
        return False
    host._saving = True
    try:
        host._save_current_tab_state()
        open_ids = {tab.tab_id for tab in host._tabs}
        if tabs is None:
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
            save_path = ws.last_saved_path or ws.path
            reason = ('首期支持 PDF 标签，请先另存为 PDF。' if not is_pdf_workspace(ws) else
                      '没有可合并的页面。' if not ws.pages else '')
            reason = reason or host.store.operation_reason(ws.pages, 'merge')
            sources.append(dict(tab_id=tab.tab_id, name=workspace_name(ws), path=ws.path or workspace_name(ws),
                                from_disk=tab.tab_id not in open_ids,
                                refs=ws.pages if not reason else (),
                                pages=len(ws.pages), dirty=ws.dirty, reason=reason,
                                save_path=save_path, saved_target=ws.last_saved_path,
                                active=tab.tab_id == host._active_tab_id,
                                signed=host.store.has_signed_pages(ws.pages) or bool(
                                    save_path and host.store.is_source_destination(save_path, signed_only=True)),
                                requires_copy=tab.tab_id not in open_ids or any(host.store._source(page.source_id).requires_copy or
                                    any(edit.kind in ('page_geometry', 'seam_stamp', 'seam_remove', 'color_cleanup')
                                        for edit in page.edits) for page in ws.pages)))
        dialog = MergeOpenDialog(host, sources, interleave=interleave)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            ids, path = dialog.selected_ids(), dialog.output_path
            interleave = dialog.page_order.currentData()
            signed_copy = dialog.includes_signature(ids)
            target_id, remember = dialog.overwrite_tab_id, dialog.remember_overwrite
        finally:
            dialog.deleteLater()
        selected = [next(tab for tab in tabs if tab.tab_id == tid) for tid in ids]
        if len(selected) < 2 or not path:
            return False
        if any((tab.tab_id in open_ids and host._get_tab(tab.tab_id) is not tab)
               or tab.ws._fingerprint() != snapshots[tab.tab_id] for tab in selected):
            raise PdfError('来源工作区已改变，请重新选择合并文件。')
        target = next((tab for tab in selected if tab.tab_id == target_id), None)
        if target_id is not None and (target is None or path != (target.ws.last_saved_path or target.ws.path)):
            raise PdfError('覆盖目标已改变，请重新选择。')
        if target is None and host.store.is_source_destination(path):
            raise PdfError('不能覆盖已打开的来源文件，请选择新的文件名。')
        pages, outline = merge_pages(host.store, selected, interleave=interleave)
        host.store.require_operation(pages, 'merge')
        if signed_copy and target is not None:
            raise PdfError('签名文件只能另存副本，不能覆盖来源文件。')
        result = host._write_pdf(pages, path, '合并已打开的文件', options=WriteOptions(operation='merge', outline=outline),
                                 register_result=target is not None, allow_tag_overwrite=target is not None)
        if result is None:
            return False
        from app.savesettings import remember_overwrite_choice
        remember_overwrite_choice(host, host._window_settings, remember, path, result.path)
        replaced = target is not None and os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(result.path))
        try:
            if replaced:
                target.ws._push(pages, path, '合并已打开的文件', outline=outline)
                target.ws.rebase_to_saved_file(result.path, list(result.pages))
                host._activate_tab(target.tab_id)
                host.refresh()
                host.reading_history.touch(result.path)
                opened = True
            else:
                opened = host._open_files_in_tabs([result.path])
                if opened:
                    host.ws.last_saved_path = result.path
            host._release_unused_docs()
        except Exception:
            opened = False
        if result.warnings and not signed_copy:
            QMessageBox.information(host, tr('合并完成 · 请核对'), '\n'.join(result.warnings))
        if not opened:
            message = tr('已保存合并结果，可从“文件 → 打开”查看：{p0}', p0=result.path)
        else:
            message = tr('已合并并保存到指定文件。') if replaced else tr('已合并并打开新 PDF。')
        host.statusBar().showMessage(message, 7000)
        return True
    except OperationCancelled as exc:
        host.statusBar().showMessage(str(exc), 5000)
        return False
    except (PdfError, OSError) as exc:
        QMessageBox.warning(host, tr('未能完成合并'), str(exc))
        return False
    finally:
        host._saving = False
