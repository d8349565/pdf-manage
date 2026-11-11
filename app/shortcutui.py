"""One searchable shortcut editor with an unapplied, recoverable draft."""
from pathlib import Path

from PyQt6.QtCore import QEvent, QIODevice, QSaveFile, Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QKeySequenceEdit, QLabel, QLineEdit, QMenu,
    QMessageBox, QPushButton, QScrollArea, QSplitter, QTableWidget, QTableWidgetItem,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from app.shortcutcommands import COMMANDS, scope_label
from app.i18n import tr
from app.shortcuts import NATIVE, PORTABLE, canonical, conflicts
from app.themes import set_theme_style

CONTROL_HEIGHT = 44


def recorded_value(sequence):
    value = sequence.toString(PORTABLE)
    if value:
        try:
            return canonical(value)
        except ValueError:
            pass  # Keep invalid input in the draft so validation can explain it.
    return value


class ShortcutEdit(QKeySequenceEdit):
    """Escape abandons this recording, without dismissing the whole editor."""
    def __init__(self, sequence=None, parent=None):
        super().__init__(QKeySequence() if sequence is None else sequence, parent)
        self.setMaximumSequenceLength(1)
        self.setClearButtonEnabled(True)
        # Qt stylesheet heights describe the content box, excluding padding/borders.
        set_theme_style(self, f'''
            QLineEdit {{ min-height: {CONTROL_HEIGHT - 14}px; max-height: {CONTROL_HEIGHT - 14}px;
                        padding: 6px 10px; border-width: 1px; }}
            QLineEdit:focus {{ border-width: 1px; }}
            QAbstractButton {{ min-width: 16px; min-height: 16px; max-width: 18px;
                               max-height: 18px; padding: 0; border: none; }}
        ''')
        self.setFixedHeight(CONTROL_HEIGHT)
        self._before = self.keySequence()
        if self.layout() is not None:
            self.layout().setContentsMargins(0, 0, 0, 0)
        self.keySequenceChanged.connect(self._sync_placeholder)
        self.editingFinished.connect(self._sync_placeholder)
        self._sync_placeholder()

    def _sync_placeholder(self, *_):
        line = self.findChild(QLineEdit)
        if line is not None:
            line.setPlaceholderText(tr('点击后按下快捷键'))

    def focusInEvent(self, event):
        self._before = self.keySequence()
        super().focusInEvent(event)
        self._sync_placeholder()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._sync_placeholder()

    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride and event.key() == Qt.Key.Key_Escape:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and not event.modifiers():
            self.setKeySequence(self._before)
            self.clearFocus()
            event.accept()
            return
        super().keyPressEvent(event)


class ShortcutDialog(QDialog):
    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.draft = manager.values()
        self._saved = manager.values()
        self._selected = None
        self._building = False
        self._editors = []
        self._row_by_id = {}
        self._last_binding = None
        self.setWindowTitle(tr('快捷键管理'))
        self.setObjectName('shortcutManager')
        set_theme_style(self, f'''
            QLineEdit#shortcutSearch, QComboBox#shortcutFilter {{
                min-height: {CONTROL_HEIGHT - 14}px; max-height: {CONTROL_HEIGHT - 14}px;
                padding: 6px 10px; border-width: 1px; font-size: 14px;
            }}
            QLineEdit#shortcutSearch:focus, QComboBox#shortcutFilter:focus {{ border-width: 1px; }}
            QPushButton[shortcutBindingAction="true"], QPushButton#shortcutMore {{
                min-height: {CONTROL_HEIGHT - 2}px; max-height: {CONTROL_HEIGHT - 2}px;
                padding: 0 14px; border-width: 1px;
            }}
            QPushButton[shortcutBindingAction="true"]:focus, QPushButton#shortcutMore:focus {{ border-width: 1px; }}
            QPushButton#shortcutMore {{ min-width: 72px; padding: 0 32px 0 14px; }}
            QPushButton#shortcutMore::menu-indicator {{
                subcontrol-origin: padding; subcontrol-position: center right;
                right: 12px; width: 10px; height: 10px;
            }}
        ''')
        self.resize(1050, 710)
        if self.screen():
            screen = self.screen().availableGeometry()
            self.resize(min(1050, int(screen.width() * .94)), min(710, int(screen.height() * .9)))
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)
        intro = QLabel(tr('集中查看和修改按键；所有改动在点击“保存”后生效。'))
        intro.setObjectName('hint')
        intro.setWordWrap(True)
        root.addWidget(intro)
        if manager.notices:
            notice = QLabel('\n'.join(manager.notices[:3]))
            notice.setTextFormat(Qt.TextFormat.PlainText)
            notice.setWordWrap(True)
            notice.setToolTip('\n'.join(manager.notices))
            notice.setAccessibleName(tr('快捷键配置恢复提示'))
            root.addWidget(notice)
        tools = QHBoxLayout()
        self.search = QLineEdit(self)
        self.search.setObjectName('shortcutSearch')
        self.search.setPlaceholderText(tr('搜索操作、菜单路径或快捷键'))
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName(tr('搜索快捷键命令'))
        tools.addWidget(self.search, 1)
        self.key_search = ShortcutEdit(parent=self)
        self.key_search.setAccessibleName(tr('按下快捷键查找占用'))
        self.key_search.setToolTip(tr('按下一个组合，查找使用它的所有操作；点清除恢复列表。'))
        self.key_search.setFixedWidth(190)
        tools.addWidget(QLabel(tr('按键查找')))
        tools.addWidget(self.key_search)
        self.filter = QComboBox(self)
        self.filter.setObjectName('shortcutFilter')
        self.filter.addItems([tr('全部命令'), tr('已设置'), tr('已修改'), tr('未设置'), tr('有冲突')])
        self.filter.setAccessibleName(tr('快捷键状态筛选'))
        for control in (self.search, self.key_search, self.filter):
            control.ensurePolished()
            control.setFixedHeight(CONTROL_HEIGHT)
        tools.addWidget(self.filter)
        root.addLayout(tools)
        splitter = QSplitter()
        self.categories = QTreeWidget()
        self.categories.setHeaderHidden(True)
        self.categories.setAccessibleName(tr('快捷键功能分类'))
        self.categories.setMinimumWidth(115)
        self.categories.setMaximumWidth(240)
        all_item = QTreeWidgetItem([tr('全部功能')])
        all_item.setData(0, Qt.ItemDataRole.UserRole, '')
        self.categories.addTopLevelItem(all_item)
        tool_root = None
        seen = set()
        for command in COMMANDS.values():
            if command.category in seen:
                continue
            seen.add(command.category)
            item = QTreeWidgetItem([tr(command.category)])
            item.setData(0, Qt.ItemDataRole.UserRole, command.category)
            if command.owner:
                if tool_root is None:
                    tool_root = QTreeWidgetItem([tr('工具窗口')])
                    tool_root.setData(0, Qt.ItemDataRole.UserRole, '@windows')
                    self.categories.addTopLevelItem(tool_root)
                tool_root.addChild(item)
            else:
                self.categories.addTopLevelItem(item)
        self.categories.setCurrentItem(all_item)
        splitter.addWidget(self.categories)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([tr('操作'), tr('当前快捷键'), tr('默认快捷键'), tr('生效位置')])
        self.table.setAccessibleName(tr('全部可自定义快捷键'))
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 210)
        self.table.setColumnWidth(1, 170)
        self.table.setColumnWidth(2, 160)
        splitter.addWidget(self.table)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([165, 825])
        root.addWidget(splitter, 1)
        self.summary = QLabel()
        self.summary.setObjectName('hint')
        root.addWidget(self.summary)
        self.detail = QFrame()
        detail = QVBoxLayout(self.detail)
        detail.setContentsMargins(0, 4, 0, 0)
        detail.setSpacing(6)
        self.detail_label = QLabel(tr('选择操作后修改快捷键'))
        self.detail_label.setWordWrap(True)
        detail.addWidget(self.detail_label)
        self.editor_host = QWidget()
        self.editor_layout = QGridLayout(self.editor_host)
        self.editor_layout.setContentsMargins(0, 0, 0, 0)
        self.editor_layout.setHorizontalSpacing(12)
        self.editor_layout.setVerticalSpacing(8)
        self.editor_layout.setColumnStretch(1, 1)
        self.editor_layout.setColumnStretch(4, 1)
        self.editor_scroll = editor_scroll = QScrollArea()
        editor_scroll.setFrameShape(QFrame.Shape.NoFrame)
        editor_scroll.setWidgetResizable(True)
        editor_scroll.setMinimumHeight(48)
        editor_scroll.setMaximumHeight(180)
        editor_scroll.setWidget(self.editor_host)
        detail.addWidget(editor_scroll)
        actions = QHBoxLayout()
        self.add_binding = QPushButton(tr('添加备用快捷键'))
        self.add_binding.clicked.connect(self.add_alternative)
        self.reset_one = QPushButton(tr('恢复此操作默认'))
        self.reset_one.clicked.connect(self.restore_selected)
        actions.addWidget(self.add_binding)
        actions.addWidget(self.reset_one)
        actions.addStretch()
        detail.addLayout(actions)
        root.addWidget(self.detail)
        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.feedback.setAccessibleName(tr('快捷键冲突及保存提示'))
        root.addWidget(self.feedback)
        collision_actions = QHBoxLayout()
        self.locate_button = QPushButton(tr('定位冲突操作'))
        self.locate_button.clicked.connect(self.locate_conflict)
        self.transfer_button = QPushButton(tr('将此按键用于当前操作'))
        self.transfer_button.clicked.connect(self.transfer_binding)
        collision_actions.addWidget(self.locate_button)
        collision_actions.addWidget(self.transfer_button)
        collision_actions.addStretch()
        root.addLayout(collision_actions)
        bottom = QHBoxLayout()
        self.more_button = more = QPushButton(tr('更多操作'), self)
        more.setObjectName('shortcutMore')
        more.setAccessibleName(tr('更多快捷键操作'))
        menu = QMenu(more)
        for label, callback in (
            ('恢复当前分类默认', lambda: self.batch(reset=True, category=True)),
            ('恢复全部默认', lambda: self.batch(reset=True)),
            ('清空当前分类快捷键', lambda: self.batch(category=True)),
            ('清空全部快捷键', lambda: self.batch()),
        ):
            menu.addAction(label).triggered.connect(callback)
        menu.addSeparator()
        menu.addAction(tr('导入快捷键配置')).triggered.connect(self.import_file)
        menu.addAction(tr('导出已保存的快捷键配置')).triggered.connect(self.export_file)
        menu.addSeparator()
        menu.addAction(tr('基础按键说明')).triggered.connect(self.show_basics)
        more.setMenu(menu)
        more.ensurePolished()
        more.setFixedHeight(CONTROL_HEIGHT)
        bottom.addWidget(more)
        bottom.addStretch()
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.save_button = box.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText(tr('保存'))
        self.save_button.setProperty('primary', True)
        box.button(QDialogButtonBox.StandardButton.Cancel).setText(tr('取消'))
        box.accepted.connect(self.save)
        box.rejected.connect(self.reject)
        bottom.addWidget(box)
        root.addLayout(bottom)
        self.search.textChanged.connect(self.rebuild)
        self.key_search.keySequenceChanged.connect(self.rebuild)
        self.filter.currentIndexChanged.connect(self.rebuild)
        self.categories.currentItemChanged.connect(self.rebuild)
        self.table.itemSelectionChanged.connect(self.select_row)
        self.table.cellClicked.connect(self.edit_clicked)
        self.table.cellDoubleClicked.connect(lambda *_: self.focus_editor())
        self.rebuild()

    def category_ids(self):
        item = self.categories.currentItem()
        category = item.data(0, Qt.ItemDataRole.UserRole) if item else ''
        return [key for key, command in COMMANDS.items() if not category
                or (category == '@windows' and command.owner) or command.category == category]

    @staticmethod
    def display(bindings):
        return ' / '.join(QKeySequence(key, PORTABLE).toString(NATIVE) for key in bindings) or '未设置'

    def rebuild(self, *_):
        previous = self._selected
        query = self.search.text().strip().casefold()
        wanted = recorded_value(self.key_search.keySequence())
        collided = {key for left, right, _ in conflicts(self.draft) for key in (left, right)}
        identifiers = []
        for key in self.category_ids():
            command, bindings = COMMANDS[key], self.draft[key]
            text = ' '.join((tr(command.label), ' → '.join(tr(part) for part in command.menu), tr(command.category),
                             self.display(bindings), self.display(self.manager.defaults[key])))
            if query and query not in text.casefold():
                continue
            if wanted and wanted not in bindings:
                continue
            state = self.filter.currentIndex()
            if (state == 1 and not bindings or state == 2 and bindings == self.manager.defaults[key]
                    or state == 3 and bindings or state == 4 and key not in collided):
                continue
            identifiers.append(key)
        self._building = True
        self.table.setRowCount(len(identifiers))
        self._row_by_id = {key: row for row, key in enumerate(identifiers)}
        for row, key in enumerate(identifiers):
            command = COMMANDS[key]
            for column, text in enumerate((tr(command.label), self.display(self.draft[key]),
                    self.display(self.manager.defaults[key]), tr(scope_label(command)))):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, key)
                item.setToolTip((' → '.join(tr(part) for part in command.menu) or f'{tr(command.category)} → {tr(command.label)}')
                                if column == 0 else text)
                self.table.setItem(row, column, item)
            self.table.setRowHeight(row, 36)
        self._building = False
        if identifiers:
            self.table.selectRow(self._row_by_id.get(previous, 0))
            self.select_row()
        else:
            self._selected = None
            self.show_editors()
        self.validate()

    def select_row(self):
        if self._building:
            return
        items = self.table.selectedItems()
        identifier = items[0].data(Qt.ItemDataRole.UserRole) if items else None
        if identifier != self._selected:
            self._selected = identifier
            self._last_binding = None
            self.show_editors()
            self.validate()

    def show_editors(self):
        self._building = True
        while item := self.editor_layout.takeAt(0):
            if item.widget():
                item.widget().blockSignals(True)
                item.widget().hide()
                item.widget().deleteLater()
        self._editors = []
        identifier = self._selected
        self.add_binding.setEnabled(identifier is not None)
        self.reset_one.setEnabled(identifier is not None)
        if identifier is None:
            self.detail_label.setText(tr('没有匹配的操作，请调整搜索或筛选。'))
            self.editor_scroll.hide()
        else:
            self.editor_scroll.show()
            command = COMMANDS[identifier]
            self.detail_label.setText(f'{tr(command.label)} · {tr(scope_label(command))}')
            for value in self.draft[identifier] or ['']:
                self.append_editor(value)
        self._building = False

    def append_editor(self, value=''):
        index = len(self._editors)
        label = QLabel(tr('主快捷键') if index == 0 else tr('备用快捷键'))
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.editor_layout.addWidget(label, index, 0)
        label.ensurePolished()
        self.editor_layout.setColumnMinimumWidth(0, label.fontMetrics().horizontalAdvance(tr('备用快捷键')))
        editor = ShortcutEdit(QKeySequence(value, PORTABLE))
        editor.setAccessibleName(tr(COMMANDS[self._selected].label) + ' · ' + tr('主快捷键' if index == 0 else '备用快捷键'))
        label.setBuddy(editor)
        editor.setMinimumWidth(180)
        editor.setMaximumWidth(300)
        self.editor_layout.addWidget(editor, index, 1)
        if index:
            primary = QPushButton(tr('设为主键'))
            primary.setProperty('shortcutBindingAction', True)
            primary.clicked.connect(lambda checked=False, position=index: self.make_primary(position))
            self.editor_layout.addWidget(primary, index, 2)
        remove = QPushButton(tr('清空') if index == 0 else tr('移除'))
        remove.setProperty('shortcutBindingAction', True)
        remove.clicked.connect(lambda checked=False, position=index: self.remove_binding(position))
        self.editor_layout.addWidget(remove, index, 3 if index else 2)
        self._editors.append(editor)
        height = len(self._editors) * CONTROL_HEIGHT + max(0, len(self._editors) - 1) * self.editor_layout.verticalSpacing()
        self.editor_host.setMinimumHeight(height)
        self.editor_scroll.setFixedHeight(min(180, height + 4))
        self.editor_host.updateGeometry()
        self.detail.updateGeometry()
        self.layout().activate()
        editor.keySequenceChanged.connect(lambda sequence, e=editor: self.edited(e, sequence))
        return editor

    def edited(self, editor, sequence):
        if self._building or self._selected is None:
            return
        value = recorded_value(sequence)
        if value and value != sequence.toString(PORTABLE):
            editor.blockSignals(True)
            editor.setKeySequence(QKeySequence(value, PORTABLE))
            editor.blockSignals(False)
        self.draft[self._selected] = [recorded_value(e.keySequence()) for e in self._editors
                                     if not e.keySequence().isEmpty()]
        self._last_binding = (self._selected, value)
        self.update_row(self._selected)
        self.validate()

    def update_row(self, identifier):
        row = self._row_by_id.get(identifier)
        if row is not None:
            self.table.item(row, 1).setText(self.display(self.draft[identifier]))

    def add_alternative(self):
        if self._selected:
            editor = self.append_editor()
            editor.setFocus()
            self.editor_scroll.ensureWidgetVisible(editor)

    def make_primary(self, position):
        values = [e.keySequence().toString(PORTABLE) for e in self._editors]
        value = values.pop(position)
        self.draft[self._selected] = [key for key in [value, *values] if key]
        self.show_editors()
        self.update_row(self._selected)
        self.validate()

    def remove_binding(self, position):
        values = [e.keySequence().toString(PORTABLE) for e in self._editors]
        values.pop(position)
        self.draft[self._selected] = [key for key in values if key]
        self.show_editors()
        self.update_row(self._selected)
        self.validate()

    def restore_selected(self):
        if self._selected:
            self.draft[self._selected] = self.manager.defaults[self._selected][:]
            self.show_editors()
            self.update_row(self._selected)
            self.validate()

    def focus_editor(self):
        if self._editors:
            self._editors[0].setFocus()

    def edit_clicked(self, row, column):
        if column == 1:
            self.focus_editor()

    def validate(self):
        error = self.manager.error(self.draft)
        self.feedback.setText(error)
        self.save_button.setEnabled(not error)
        collisions = conflicts(self.draft)
        self.locate_button.setVisible(bool(collisions))
        self.transfer_button.setVisible(bool(self.selected_conflicts()))
        modified = sum(values != self.manager.defaults[key] for key, values in self.draft.items())
        self.summary.setText(tr('显示 {p0} 项，共 {p1} 项 · {p2} 项已自定义', p0=f'{self.table.rowCount()}', p1=f'{len(COMMANDS)}', p2=f'{modified}'))

    def selected_conflicts(self):
        return [(left, right, key) for left, right, key in conflicts(self.draft)
                if self._selected in (left, right)
                and (self._last_binding is None or key == self._last_binding[1])]

    def locate_conflict(self):
        collisions = self.selected_conflicts() or conflicts(self.draft)
        if not collisions:
            return
        left, right, _ = collisions[0]
        target = right if self._selected == left else left
        self.search.clear()
        self.key_search.clear()
        self.filter.setCurrentIndex(0)
        self.categories.setCurrentItem(self.categories.topLevelItem(0))
        self._selected = target
        self.rebuild()
        self.show_editors()

    def transfer_binding(self):
        for left, right, key in self.selected_conflicts():
            other = right if self._selected == left else left
            self.draft[other] = [value for value in self.draft[other] if value != key]
            self.update_row(other)
        self.validate()

    def batch(self, *, reset=False, category=False):
        identifiers = self.category_ids() if category else list(COMMANDS)
        affected = sum(bool(self.draft[key]) for key in identifiers)
        if not reset and QMessageBox.question(self, tr('清空快捷键'),
                tr('将清空此范围内 {p0} 项操作的快捷键。菜单和基础按键仍可使用。\n点击“保存”后生效；保存前可以取消。', p0=f'{affected}'),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        for key in identifiers:
            self.draft[key] = self.manager.defaults[key][:] if reset else []
            self.update_row(key)
        self.show_editors()
        self.validate()

    def import_file(self):
        filename, _ = QFileDialog.getOpenFileName(self, tr('导入快捷键配置'), '', tr('快捷键配置 (*.json)'))
        if not filename:
            return
        try:
            path = Path(filename)
            if path.stat().st_size > 1024 * 1024:
                raise ValueError('配置文件过大，请选择导出的快捷键配置文件。')
            values, unknown = self.manager.import_config(path.read_text(encoding='utf-8-sig'))
        except (OSError, UnicodeError, ValueError) as error:
            QMessageBox.warning(self, tr('未导入'), str(error))
            return
        changed = sum(values[key] != self.draft[key] for key in values)
        self.draft = values
        self.rebuild()
        self.show_editors()
        detail = f'已载入草稿，{changed} 项操作发生变化。点击“保存”后生效。'
        if unknown:
            detail += '\n已跳过无法识别的命令：' + '、'.join(unknown)
        if self.manager.error(values):
            detail += '\n请先解决列表中的快捷键冲突。'
        message = QMessageBox(QMessageBox.Icon.Information, tr('导入预览'), detail,
                              QMessageBox.StandardButton.Ok, self)
        message.setTextFormat(Qt.TextFormat.PlainText)
        message.exec()

    def export_file(self):
        filename, _ = QFileDialog.getSaveFileName(self, tr('导出已保存的快捷键配置'),
                                                 tr('纸序PDF-快捷键.json'), tr('快捷键配置 (*.json)'))
        if not filename:
            return
        output = QSaveFile(filename)
        data = self.manager.encode().encode('utf-8')
        if not output.open(QIODevice.OpenModeFlag.WriteOnly):
            QMessageBox.warning(self, tr('未导出'), output.errorString())
            return
        if output.write(data) != len(data) or not output.commit():
            output.cancelWriting()
            QMessageBox.warning(self, tr('未导出'), output.errorString())
            return
        self.feedback.setText(tr('已导出保存过的设置；本窗口尚未保存的改动不在导出文件中。'))

    def show_basics(self):
        QMessageBox.information(self, tr('基础按键说明'),
            tr('Esc：取消当前操作或关闭弹窗。\nTab / Shift+Tab：移动输入焦点。\nEnter：确认输入或对话框默认操作；搜索框的确认按键可单独设置。\n输入框中的复制、粘贴、撤销、文字选择及中文输入继续使用原生行为。\nWindows 系统快捷键和鼠标组合手势不在此处更改。\n阅读区空格绑定给命令后，该按法不再临时拖动；仍可使用 Alt＋拖动。\n工具窗口内的快捷键只在对应窗口生效。'))

    def save(self):
        error = self.manager.apply(self.draft)
        if error:
            self.feedback.setText(error)
            return False
        self._saved = self.manager.values()
        self.draft = self.manager.values()
        self.accept()
        return True

    def closeEvent(self, event):
        if self.draft != self._saved:
            answer = QMessageBox.question(self, tr('保存快捷键修改'), tr('快捷键有未保存的修改。'),
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if answer == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.StandardButton.Save:
                if self.save():
                    event.accept()
                else:
                    event.ignore()
                return
        super().closeEvent(event)
