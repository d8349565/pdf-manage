"""The document-menu comparison flow: meaningful changes first, source evidence second."""
from collections import OrderedDict
from html import escape
from pathlib import Path

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QPen
from PyQt6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton, QSplitter,
    QVBoxLayout, QWidget)

from app.output import OperationCancelled, check_cancel
from app.pagetoolsui import ClearComboBox
from app.pdfcompare import check_sources
from app.pdfcompareui import COMPARE_STYLE, CompareView, PdfFileDrop
from app.pdfworker import compare_pdf_contents, render_content_change
from app.progress import run_operation
from app.themes import set_theme_style, theme_manager, ui_color


CONTENT_STYLE = COMPARE_STYLE + '''
QLabel#contentSummary { font-size: 16px; font-weight: 600; }
QLabel#contentHeading { font-size: 16px; font-weight: 600; }
QLabel#contentContext { font-size: 16px; background: #EEF3F0; border-radius: 6px; padding: 8px 10px; }
QLabel#contentNotice { font-size: 12px; color: #4D6B67; background: #FFFBEB; border: 1px solid #FDE68A; border-radius: 6px; padding: 7px 10px; }
QWidget#contentControls { background: transparent; }
'''
KINDS = dict(modified='文字修改', added='新增条款', deleted='删除条款', moved='位置调整',
             image_modified='图片内容变化', ambiguous='对应关系待确认', review='需要核对')


def page_numbers(refs):
    return sorted({ref['page'] for ref in refs})


def page_text(refs):
    return '、'.join(map(str, page_numbers(refs)))


def change_description(change):
    kind = change['kind']
    if kind == 'moved':
        return f"位置从旧第 {page_text(change['old'])} 页 → 新第 {page_text(change['new'])} 页"
    if kind == 'ambiguous':
        return '发现文字差异，对应位置需要确认'
    if kind == 'image_modified':
        return '图片的实际内容发生变化'
    if kind in ('added', 'deleted'):
        return (change['after'] if kind == 'added' else change['before'])[:75]
    return f"{change['before'] or '（无）'} → {change['after'] or '（删除）'}"


class PdfContentCompareDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('pdfCompare'); self.setWindowTitle('两份 PDF 对比')
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setMinimumSize(940, 660); self.resize(1280, 840)
        self.summary = None; self.items = []; self.passwords = ('', '')
        self.busy = False; self.stale = False; self.focused = True
        self.cache = OrderedDict(); self.preview = None; self.images = None
        set_theme_style(self, CONTENT_STYLE)
        layout = QVBoxLayout(self); layout.setContentsMargins(18, 16, 18, 14); layout.setSpacing(12)
        heading = QHBoxLayout()
        title = QLabel('两份 PDF 对比'); title.setObjectName('compareTitle'); heading.addWidget(title)
        subtitle = QLabel('查看文字修改、增删与位置调整'); subtitle.setObjectName('compareHint')
        heading.addSpacing(10); heading.addWidget(subtitle); heading.addStretch()
        help_button = QPushButton('使用说明'); help_button.clicked.connect(self.show_help); heading.addWidget(help_button)
        layout.addLayout(heading)
        source = QFrame(); source.setObjectName('workbench')
        source_layout = QVBoxLayout(source); source_layout.setContentsMargins(14, 12, 14, 12); source_layout.setSpacing(9)
        file_row = QHBoxLayout(); file_row.setSpacing(18)
        self.paths, self.password_inputs, self.file_buttons, self.file_targets = [], [], [], []
        for name in ('旧版', '新版'):
            target = QWidget(); target.setObjectName('pdfDropTarget')
            group = QVBoxLayout(target); group.setContentsMargins(0, 0, 0, 0); group.setSpacing(5)
            self.file_targets.append(target)
            label = QLabel(name + ' PDF'); label.setObjectName('compareSection'); group.addWidget(label)
            row = QHBoxLayout(); path = QLineEdit(); path.setPlaceholderText('选择或拖入' + name + ' PDF')
            path.setAccessibleName(name + ' PDF 路径')
            path.setToolTip('可拖入 PDF；一次拖两份时按旧版、新版顺序填入。只比较已保存文件。')
            choose = QPushButton('选择文件'); choose.setAccessibleName('选择' + name + ' PDF')
            choose.clicked.connect(lambda _, p=path: self.choose(p))
            row.addWidget(path, 1); row.addWidget(choose); group.addLayout(row); file_row.addWidget(target, 1)
            password = QLineEdit(); password.setEchoMode(QLineEdit.EchoMode.Password)
            password.setPlaceholderText(name + '密码（如有）'); password.setAccessibleName(name + '密码')
            self.paths.append(path); self.file_buttons.append(choose); self.password_inputs.append(password)
        source_layout.addLayout(file_row)
        actions = QHBoxLayout()
        self.advanced = QPushButton('更多设置'); self.advanced.setCheckable(True); self.advanced.setObjectName('compareMore')
        actions.addWidget(self.advanced)
        hint = QLabel('按正文内容核对 · 原文件只读'); hint.setObjectName('compareHint'); actions.addWidget(hint)
        actions.addStretch(); self.start = QPushButton('开始比较'); self.start.setObjectName('primary')
        self.start.clicked.connect(self.compare); actions.addWidget(self.start); source_layout.addLayout(actions)
        self.options = QWidget(); self.options.setObjectName('contentControls'); grid = QGridLayout(self.options)
        grid.setContentsMargins(0, 4, 0, 0); grid.setHorizontalSpacing(14)
        grid.addWidget(self.password_inputs[0], 0, 0); grid.addWidget(self.password_inputs[1], 0, 1)
        self.use_ocr = QCheckBox('自动识别扫描页文字'); self.use_ocr.setChecked(True)
        self.use_ocr.setToolTip('使用已安装的离线 OCR 增强包；未识别页面会明确提示，不能当作无修改。')
        grid.addWidget(self.use_ocr, 1, 0)
        self.visual = QPushButton('版式与图形检查'); self.visual.clicked.connect(self.open_visual); grid.addWidget(self.visual, 1, 1)
        self.options.hide(); self.advanced.toggled.connect(self.options.setVisible); source_layout.addWidget(self.options)
        layout.addWidget(source)
        self.result_summary = QLabel('选择两份文件后，点击“开始比较”')
        self.result_summary.setObjectName('contentSummary'); self.result_summary.setWordWrap(True); layout.addWidget(self.result_summary)
        notice_row = QHBoxLayout(); self.notice = QLabel(); self.notice.setObjectName('contentNotice'); self.notice.setWordWrap(True)
        self.install = QPushButton('安装 OCR 增强包'); self.install.clicked.connect(self.install_ocr)
        notice_row.addWidget(self.notice, 1); notice_row.addWidget(self.install)
        self.notice.hide(); self.install.hide(); layout.addLayout(notice_row)
        splitter = QSplitter(); splitter.setChildrenCollapsible(False); splitter.setHandleWidth(12)
        sidebar = QFrame(); sidebar.setObjectName('comparePages'); sidebar.setMinimumWidth(285)
        left = QVBoxLayout(sidebar); left.setContentsMargins(12, 12, 12, 12); left.setSpacing(9)
        caption = QLabel('内容变化'); caption.setObjectName('compareSection'); left.addWidget(caption)
        self.rows = QListWidget(); self.rows.setObjectName('compareList'); self.rows.setSpacing(4)
        self.rows.setAccessibleName('具体内容变化'); self.rows.currentRowChanged.connect(self.select_change)
        self.rows.itemClicked.connect(lambda _: self.load_selected_pages()
            if self.preview is None and self.summary is not None and not self.busy and not self.stale else None)
        self.no_changes = QLabel('比较完成后，这里列出具体修改。'); self.no_changes.setObjectName('compareHint')
        self.no_changes.setAlignment(Qt.AlignmentFlag.AlignCenter); self.no_changes.setWordWrap(True)
        left.addWidget(self.rows); left.addWidget(self.no_changes, 1); self.rows.hide()
        nav = QHBoxLayout(); self.previous = QPushButton('上一项'); self.next = QPushButton('下一项')
        self.previous.clicked.connect(lambda: self.navigate(-1)); self.next.clicked.connect(lambda: self.navigate(1))
        nav.addWidget(self.previous); nav.addWidget(self.next); left.addLayout(nav); splitter.addWidget(sidebar)
        detail = QFrame(); detail.setObjectName('workbench'); right = QVBoxLayout(detail)
        right.setContentsMargins(14, 12, 14, 12); right.setSpacing(10)
        detail_head = QHBoxLayout(); self.detail_title = QLabel('原文依据'); self.detail_title.setObjectName('contentHeading')
        self.detail_title.setTextFormat(Qt.TextFormat.PlainText); self.detail_title.setWordWrap(True)
        self.whole = QPushButton('查看整页'); self.whole.clicked.connect(self.toggle_focus)
        self.whole.hide()
        self.zoom_buttons = []
        detail_head.addWidget(self.detail_title, 1)
        for label, factor in [('缩小', 1/1.2), ('放大', 1.2)]:
            button = QPushButton(label); button.setToolTip(label + '原页，也可用 Ctrl+滚轮缩放')
            button.clicked.connect(lambda _, f=factor: self.zoom_evidence(f))
            self.zoom_buttons.append(button); detail_head.addWidget(button)
        detail_head.addWidget(self.whole); right.addLayout(detail_head)
        self.location = QLabel('比较后直接列出修改前后的内容，选中一项可查看原页。')
        self.location.setObjectName('compareHint'); self.location.setWordWrap(True); right.addWidget(self.location)
        contexts = QHBoxLayout(); contexts.setSpacing(14)
        self.text_labels, self.contexts = [], []
        for name in ('原文', '修改后'):
            column = QVBoxLayout(); label = QLabel(name); label.setObjectName('compareHint')
            context = QLabel('等待比较结果'); context.setObjectName('contentContext'); context.setWordWrap(True)
            context.setTextFormat(Qt.TextFormat.RichText); context.setMinimumHeight(52); context.setMaximumHeight(140)
            context.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            column.addWidget(label); column.addWidget(context); contexts.addLayout(column, 1)
            self.text_labels.append(label); self.contexts.append(context)
        self.context_panel = QWidget(); self.context_panel.setObjectName('contentControls')
        self.context_panel.setLayout(contexts); contexts.setContentsMargins(0, 0, 0, 0)
        right.addWidget(self.context_panel); self.context_panel.hide()
        evidence = QHBoxLayout(); evidence.setSpacing(14)
        self.views, self.page_selectors, self.page_labels, self.empty = [], [], [], []
        for name in ('旧版原页', '新版原页'):
            column = QVBoxLayout(); column.setSpacing(6); line = QHBoxLayout()
            label = QLabel(name); label.setObjectName('compareHint'); label.setWordWrap(True)
            selector = ClearComboBox(); selector.setAccessibleName(name + '位置'); selector.setMaximumWidth(125)
            selector.hide()
            line.addWidget(label, 1); line.addWidget(selector); column.addLayout(line)
            view = CompareView(); view.setMinimumSize(160, 160); column.addWidget(view, 1)
            empty = QLabel('比较后显示对应原页'); empty.setObjectName('compareHint')
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter); empty.setWordWrap(True); column.addWidget(empty, 1); view.hide()
            evidence.addLayout(column, 1)
            self.views.append(view); self.page_selectors.append(selector); self.page_labels.append(label); self.empty.append(empty)
            selector.currentIndexChanged.connect(self.load_selected_pages)
        right.addLayout(evidence, 1); splitter.addWidget(detail); splitter.setStretchFactor(1, 1)
        splitter.setSizes([335, 900]); layout.addWidget(splitter, 1)
        self.status = QLabel('每份最多 200 页 · 复杂版式和文字识别结果请结合原页核对')
        self.status.setObjectName('compareHint'); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.file_drop = PdfFileDrop(self)
        for path in self.paths:
            path.textChanged.connect(self._refresh_start)
        self._set_busy(False)
        theme_manager().changed.connect(self._theme_changed)

    def _refresh_start(self):
        self.start.setEnabled(not self.busy and all(path.text().strip() for path in self.paths))

    def _set_busy(self, busy):
        self.busy = busy
        for widget in [*self.paths, *self.file_buttons, *self.password_inputs, self.advanced, self.options, self.install]:
            widget.setEnabled(not busy)
        self.rows.setEnabled(not busy and not self.stale)
        for widget in [self.previous, self.next, self.whole, *self.page_selectors]:
            widget.setEnabled(not busy and not self.stale and self.summary is not None)
        self.previous.setEnabled(not busy and not self.stale and len(self.items) > 1)
        self.next.setEnabled(self.previous.isEnabled())
        self.whole.setEnabled(not busy and not self.stale and self.preview is not None)
        for button in self.zoom_buttons:
            button.setEnabled(not busy and not self.stale and self.preview is not None)
        for selector in self.page_selectors:
            selector.setEnabled(not busy and not self.stale and selector.count() > 1)
        self._refresh_start()

    def choose(self, target):
        path, _ = QFileDialog.getOpenFileName(self, '选择 PDF', '', 'PDF (*.pdf)')
        if path:
            target.setText(path)

    def compare(self):
        if self.busy:
            return
        paths = [widget.text().strip() for widget in self.paths]
        passwords = tuple(widget.text() for widget in self.password_inputs)
        use_ocr = self.use_ocr.isChecked(); self._set_busy(True)
        try:
            summary = run_operation(self, '核对两份 PDF 的内容', lambda progress, cancel:
                compare_pdf_contents(*paths, progress, cancel, passwords=passwords, use_ocr=use_ocr), cancellable=True)
            self.summary, self.passwords = summary, passwords
            self.stale = False; self.cache.clear(); self.preview = self.images = None
            raw = summary['changes']
            self.items = [c for c in raw if not (c['kind'] == 'moved' and any(
                other['kind'] in ('modified', 'review') and other['heading'] == c['heading'] for other in raw))]
            self.rows.blockSignals(True); self.rows.clear()
            for change in self.items:
                self.rows.addItem(f"{change['heading']}\n{change_description(change)}\n{KINDS[change['kind']]}")
                self.rows.item(self.rows.count()-1).setSizeHint(QSize(0, 88))
                self.rows.item(self.rows.count()-1).setToolTip(change_description(change))
            self.rows.blockSignals(False)
            self.rows.setVisible(bool(self.items)); self.no_changes.setVisible(not self.items)
            self.no_changes.setText('没有需要逐项核对的文字修改。' if summary['complete'] else '尚有页面未识别，请先完成识别。')
            counts = summary['counts']; parts = []
            for key, label in [('modified', '项文字修改'), ('added', '条新增'), ('deleted', '条删除'),
                               ('image_modified', '处图片变化'), ('moved', '条位置调整')]:
                if counts.get(key):
                    parts.append(f'{counts[key]} {label}')
            pending = counts.get('review', 0) + counts.get('ambiguous', 0)
            if pending:
                parts.append(f'{pending} 项待确认')
            self.result_summary.setText(' · '.join(parts) if parts else
                ('本次未检出正文修改' if summary['complete'] else '有页面尚未识别，比较未完成'))
            self._show_notices()
            self.status.setText('当前结果：' + Path(summary['paths'][0]).name + ' → ' + Path(summary['paths'][1]).name)
        except OperationCancelled:
            previous_files = (' 当前结果：' + Path(self.summary['paths'][0]).name + ' → ' + Path(self.summary['paths'][1]).name) if self.summary else ''
            self.status.setText('已取消；请重新比较已变化的来源文件。' if self.stale else '已取消，保留上次完成的结果。' + previous_files)
        except Exception as exc:
            if self.summary is not None:
                self.status.setText('本次比较失败，仍显示上次结果：' + Path(self.summary['paths'][0]).name + ' → ' + Path(self.summary['paths'][1]).name)
            else:
                self.status.setText('比较未完成，请调整文件或设置后重试。')
            QMessageBox.warning(self, '无法比较', str(exc))
        finally:
            self._set_busy(False)
        if self.summary is not None and self.images is None and not self.stale:
            if self.items:
                self.rows.setCurrentRow(0)
            else:
                self.select_change(-1)

    def _show_notices(self):
        messages = []
        for index, info in enumerate(self.summary['extraction']):
            if info['unread_pages']:
                messages.append(('旧版' if index == 0 else '新版') + '第 ' + '、'.join(map(str, info['unread_pages'])) + ' 页尚未识别')
        if messages:
            action = '请在更多设置开启扫描页文字识别后重新比较。' if not self.summary.get('ocr_enabled') else '请安装 OCR 增强包后重新比较。'
            messages.insert(0, '比较未完成，不能确认整份文件的修改。' + action)
        for info in self.summary.get('quality_review', []):
            messages.append(('旧版' if info['side'] == 'old' else '新版') + '第 ' + '、'.join(map(str, info['pages'])) + ' 页有低质量识别文字，需要复核')
        self.notice.setText('；'.join(messages)); self.notice.setVisible(bool(messages))
        self.install.setVisible(not self.summary['complete'] and self.summary.get('ocr_enabled', False)
                                and not self.summary.get('ocr_available'))

    def select_change(self, index):
        if self.busy or self.summary is None or self.stale:
            return
        change = self.items[index] if 0 <= index < len(self.items) else None
        self.focused = bool(change)
        self.context_panel.setVisible(change is not None)
        self.whole.setVisible(change is not None)
        if change is None:
            self.detail_title.setText('未检出正文修改' if self.summary['complete'] else '请先完成未识别页面')
            self.location.setText('字号、换行和页码变化没有作为正文修改列出。可结合两份原文件核对。')
        else:
            self.detail_title.setText(change['heading'] + ' · ' + KINDS[change['kind']])
            movement = next((c for c in self.summary['changes'] if c['kind'] == 'moved' and c['heading'] == change['heading']), None)
            note = change.get('reason', '')
            if movement:
                note = f"该条款从旧版第 {page_text(movement['old'])} 页移至新版第 {page_text(movement['new'])} 页。"
                if change['kind'] == 'review':
                    note += change.get('reason') or '文字差异仍需核对。'
            if change['kind'] == 'ambiguous':
                note = '存在多个候选位置，尚未确认唯一对应关系。请切换候选页核对。'
            self.location.setText(note)
        self.location.setVisible(bool(self.location.text()))
        for i, side in enumerate(('old', 'new')):
            refs = change[side] if change else []
            pages = page_numbers(refs)
            selector = self.page_selectors[i]; selector.blockSignals(True); selector.clear()
            for page in pages or [1]:
                selector.addItem(('候选第 ' if len(pages) > 1 else '第 ') + str(page) + ' 页', page)
            selector.blockSignals(False); selector.setVisible(len(pages) > 1)
            label = '原文' if i == 0 else ('相同内容' if change and change['kind'] == 'moved' else '修改后')
            if change and change['kind'] in ('review', 'ambiguous'):
                label = '旧版候选文字' if i == 0 else '新版文字（待核对）'
            elif change and change['kind'] == 'moved':
                label = '旧版内容' if i == 0 else '新版内容'
            self.text_labels[i].setText(label + (' · 第 ' + '、'.join(map(str, pages)) + ' 页' if pages else ''))
            text = change.get(side + '_context') or change['before' if i == 0 else 'after'] if change else ''
            target = change['before' if i == 0 else 'after'] if change and change['kind'] != 'moved' else ''
            self._set_context(i, text, target, change.get(side + '_context_range') if change else None)
        self.load_selected_pages()

    def _set_context(self, index, text, target, bounds=None):
        if not text:
            message = ('本次未检出正文修改' if self.summary['complete'] else '尚有未识别页面，请先完成识别') if not self.items else ('旧版' if index == 0 else '新版') + '未找到对应内容'
            self.contexts[index].setText(message)
            return
        valid = (isinstance(bounds, (list, tuple)) and len(bounds) == 2
                 and all(type(value) is int for value in bounds)
                 and 0 <= bounds[0] <= bounds[1] <= len(text)
                 and text[bounds[0]:bounds[1]] == target)
        start, end = bounds if valid else ((text.index(target), text.index(target)+len(target))
                                          if target and text.count(target) == 1 else (-1, -1))
        left = max(0, start-55) if start >= 0 else 0
        right = min(len(text), left+180)
        prefix = '…' if left else ''; suffix = '…' if right < len(text) else ''
        if target and left <= start < right:
            color = ui_color('danger' if index == 0 else 'accent')
            stop = min(end, right)
            safe = escape(text[left:start]) + f'<b style="color:{color}">{escape(text[start:stop])}</b>' + escape(text[stop:right])
        else:
            safe = escape(text[left:right])
        self.contexts[index].setText(prefix + safe + suffix)
        self.contexts[index].setToolTip(escape(text))

    def _theme_changed(self, *_):
        index = self.rows.currentRow()
        if self.stale or not 0 <= index < len(self.items):
            return
        change = self.items[index]
        for i, side in enumerate(('old', 'new')):
            text = change.get(side + '_context') or change['before' if i == 0 else 'after']
            target = change['before' if i == 0 else 'after'] if change['kind'] != 'moved' else ''
            self._set_context(i, text, target, change.get(side + '_context_range'))

    def load_selected_pages(self, *_):
        if self.busy or self.summary is None or self.stale:
            return
        index = self.rows.currentRow(); change = self.items[index] if 0 <= index < len(self.items) else None
        pages = {side: selector.currentData() for side, selector in zip(('old', 'new'), self.page_selectors)}
        key = (index, pages['old'], pages['new'])
        self.preview = self.images = None
        for view, empty in zip(self.views, self.empty):
            view.scene().clear(); view.marker = None; view.hide()
            empty.setText('正在加载对应原页'); empty.show()
        self._set_busy(True)
        def work(progress, cancel):
            check_cancel(cancel); check_sources(self.summary)
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            value = render_content_change(self.summary, change, pages, progress, cancel, passwords=self.passwords)
            self.cache[key] = value
            while len(self.cache) > 2 or sum(image.width * image.height * 4 for _, images in self.cache.values()
                                            for image in images.values()) > 32 * 1024 * 1024:
                self.cache.popitem(last=False)
            return value
        try:
            self.preview, self.images = run_operation(self, '定位修改处的原文', work, cancellable=True)
            self.show_evidence(change)
        except OperationCancelled:
            self.status.setText('原文加载已取消，可重新选择这一项。')
            for empty in self.empty:
                empty.setText('加载已取消，点击左侧这一项可重试。')
        except Exception as exc:
            self.stale = True; self.cache.clear(); self.images = self.preview = None
            for view, empty in zip(self.views, self.empty):
                view.scene().clear(); view.marker = None; view.hide(); empty.setText('预览已停止，请重新比较'); empty.show()
            self.result_summary.setText('来源或预览状态已变化，请重新比较')
            self.detail_title.setText('请重新比较来源文件')
            for label in self.contexts:
                label.clear()
            self.status.setText(str(exc))
            QMessageBox.warning(self, '无法定位原文', str(exc))
        finally:
            self._set_busy(False)

    def show_evidence(self, change):
        for i, side in enumerate(('old', 'new')):
            meta = self.preview[side]; view, empty = self.views[i], self.empty[i]
            self.page_labels[i].setText(('旧版' if i == 0 else '新版') + ' · ' +
                (f"第 {meta['page']} 页" if meta['available'] else '未找到对应内容'))
            view.scene().clear(); view.marker = None
            if not meta['available']:
                view.hide(); empty.setText(('旧版' if i == 0 else '新版') + '未找到对应条款'); empty.show()
                continue
            empty.hide(); view.show(); view.show_image(self.images[side])
            boxes = meta['boxes']
            if boxes:
                union = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]
                if self.focused:
                    width = min(meta['width'], max(520, union[2]-union[0]+180))
                    height = min(meta['height'], max(300, union[3]-union[1]+150))
                    x = max(0, min(meta['width']-width, (union[0]+union[2]-width)/2))
                    y = max(0, min(meta['height']-height, (union[1]+union[3]-height)/2))
                    view.focus_region([x, y, x+width, y+height])
                    if view.marker is not None:
                        view.scene().removeItem(view.marker); view.marker = None
                if change and change['kind'] != 'moved' and not meta.get('context_only'):
                    # PDF paper stays white in every UI theme, so keep these outlines dark.
                    pen = QPen(QColor('#B42332' if i == 0 else '#0F766E')); pen.setCosmetic(True); pen.setWidth(2)
                    for box in boxes:
                        view.scene().addRect(QRectF(box[0], box[1], box[2]-box[0], box[3]-box[1]), pen)
        self.whole.setText('查看整页' if self.focused else '定位修改处')

    def toggle_focus(self):
        if self.busy or self.preview is None:
            return
        self.focused = not self.focused
        index = self.rows.currentRow()
        self.show_evidence(self.items[index] if 0 <= index < len(self.items) else None)

    def zoom_evidence(self, factor):
        if self.busy or self.stale or self.preview is None:
            return
        for view in self.views:
            if not view.isHidden() and .05 <= view.transform().m11() * factor <= 6:
                view.fit_mode = False
                view.scale(factor, factor)

    def navigate(self, direction):
        if self.items and not self.busy and not self.stale:
            self.rows.setCurrentRow((self.rows.currentRow() + direction) % len(self.items))

    def open_visual(self):
        from app.pdfcompareui import PdfCompareDialog
        dialog = PdfCompareDialog(self); dialog.setWindowTitle('版式与图形检查')
        for old, new in zip(self.paths, dialog.paths):
            new.setText(old.text())
        for old, new in zip(self.password_inputs, dialog.password_inputs):
            new.setText(old.text())
        dialog.mode.setCurrentIndex(1)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def install_ocr(self):
        from app.ocrui import install_local_runtime
        install_local_runtime(self)

    def show_help(self):
        QMessageBox.information(self, '内容核对说明',
            '选择或拖入旧版和新版 PDF，点击“开始比较”。分别拖到对应文件框可指定版本；一次拖入两份时，按拖放顺序填入，请先核对旧、新顺序。'
            '结果先列出具体文字修改、增删和位置调整；选中一项查看原文和两边页码。\n\n'
            '右侧可以放大、缩小或拖动原页，Ctrl+滚轮也可缩放。点击“查看整页”可回到页面上下文。\n\n'
            '扫描页使用已安装的离线 OCR 增强包。未识别页面和无法唯一对应的重复条款会提示核对，不能当成确定修改。\n\n'
            '每份限 200 页、512 MiB。只比较已保存文件，原文件保留。复杂多栏、表格和文字识别结果仍需核对。'
            '遇到容量或条款长度上限时，可先用页面工具提取需要核对的部分。'
            '需要核对颜色、图形或排版外观时，从“更多设置 → 版式与图形检查”进入。')

    def done(self, result):
        if self.busy:
            return
        self.summary = None; self.items = []; self.preview = self.images = None; self.passwords = ('', '')
        self.cache.clear()
        for view in self.views:
            view.scene().clear(); view.marker = None
        for field in self.password_inputs:
            field.clear()
        super().done(result)

    def reject(self):
        if not self.busy:
            super().reject()

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
        else:
            self.done(QDialog.DialogCode.Rejected); event.accept()
