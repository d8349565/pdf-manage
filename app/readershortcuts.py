"""Configurable reader actions using Qt's existing shortcut dispatch."""
import json

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHeaderView, QKeySequenceEdit, QLabel, QPushButton,
    QLineEdit, QTableWidget, QTableWidgetItem, QVBoxLayout,
)
from app.themes import set_theme_style

PORTABLE = QKeySequence.SequenceFormat.PortableText


class ReadingShortcuts:
    def __init__(self, host, definitions):
        self.host = host
        self.definitions = definitions
        self.actions = {key: action for key, label, default, action in definitions}
        self.defaults = {key: default for key, label, default, action in definitions}
        try:
            saved = json.loads(host._window_settings.value('reading/shortcuts', '{}', type=str))
        except (TypeError, ValueError):
            saved = {}
        values = dict(self.defaults)
        if isinstance(saved, dict):
            values.update({key: value for key, value in saved.items() if key in values and isinstance(value, str)})
        if self.error(values):
            values = self.defaults
        self.apply(values, save=False)

    def values(self):
        return {key: action.shortcut().toString(PORTABLE) for key, action in self.actions.items()}

    def uses_space(self, event=None):
        sequence = QKeySequence(event.keyCombination()) if event is not None else QKeySequence('Space')
        return any(action.shortcut() == sequence for action in self.actions.values())

    def error(self, values):
        reserved = [QKeySequence(key) for key in ('Esc', 'Tab', 'Backtab', 'Return', 'Enter', 'Delete', 'Backspace',
                    'Ctrl+C', 'Ctrl+V', 'Ctrl+X', 'Ctrl+A', 'Ctrl+Z', 'Ctrl+Y', 'Alt+F4')]
        managed = set(self.actions.values())
        for action in self.host.findChildren(QAction):
            if action not in managed:
                reserved.extend(action.shortcuts())
        reserved.extend(shortcut.key() for shortcut in self.host.findChildren(QShortcut))
        used = []
        for key, label, default, action in self.definitions:
            value = values.get(key, '')
            sequence = QKeySequence(value, PORTABLE)
            if not value:
                continue
            if sequence.isEmpty() or sequence.count() != 1:
                return f'“{label}”需要一个有效按键组合。'
            if any(sequence.matches(other) != QKeySequence.SequenceMatch.NoMatch
                   or other.matches(sequence) != QKeySequence.SequenceMatch.NoMatch for other in reserved if not other.isEmpty()):
                return f'“{label}”与已有操作或保留按键冲突，请换一个按键。'
            for other_label, other in used:
                if sequence == other:
                    return f'“{label}”与“{other_label}”使用了同一按键。'
            used.append((label, sequence))
        return ''

    def apply(self, values, *, save=True):
        error = self.error(values)
        if error:
            return error
        for key, label, default, action in self.definitions:
            action.setShortcut(QKeySequence(values.get(key, ''), PORTABLE))
            action.setAutoRepeat(key in ('previous', 'next'))
            binding = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
            action.setToolTip(f'{label} ({binding})' if binding else label)
        if save:
            overrides = {key: value for key, value in self.values().items() if value != self.defaults[key]}
            self.host._window_settings.setValue('reading/shortcuts', json.dumps(overrides))
            self.host.reader_interaction.reset()
            self.host.preview_view.setAccessibleDescription(
                '选择文字；Ctrl+C 复制；Alt＋拖动页面。' + ('' if self.uses_space() else '也可按住空格拖动。'))
            self.host._sync_page_navigation()
            self.host.reading_orb.sync()
        return ''


class ReadingShortcutsDialog(QDialog):
    def __init__(self, controller):
        super().__init__(controller.host)
        self.controller = controller
        self.setWindowTitle('阅读快捷键')
        self.resize(580, 580)
        layout = QVBoxLayout(self)
        tip = QLabel('点击按键栏后按下新组合；点 × 清除，留空表示不设置快捷键。\n'
                     '空格默认临时拖动；将它绑定给其他动作后，改用 Alt＋拖动。\n'
                     '手形和文字选择快捷键只在阅读区生效，输入框仍可正常打字。')
        tip.setObjectName('hint')
        tip.setWordWrap(True)
        layout.addWidget(tip)
        self.table = QTableWidget(len(controller.definitions), 2)
        self.table.setHorizontalHeaderLabels(['操作', '快捷键'])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setShowGrid(False)
        self.editors = {}
        values = controller.values()
        for row, (key, label, default, action) in enumerate(controller.definitions):
            item = QTableWidgetItem(label)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 0, item)
            editor = QKeySequenceEdit(QKeySequence(values[key]))
            editor.setMaximumSequenceLength(1)
            editor.setClearButtonEnabled(True)
            editor.setAccessibleName(label + '快捷键')
            set_theme_style(editor, '''
                QLineEdit { min-height: 20px; padding: 2px 8px; border-radius: 6px; }
                QAbstractButton { min-width: 16px; min-height: 16px; max-width: 18px; max-height: 18px;
                                  padding: 0; border: none; border-radius: 3px; }
            ''')
            if editor.layout() is not None:
                editor.layout().setContentsMargins(0, 0, 0, 0)
            editor.findChild(QLineEdit).setPlaceholderText('点击后按下快捷键')
            editor.keySequenceChanged.connect(self.validate)
            self.table.setCellWidget(row, 1, editor)
            editor.ensurePolished()
            self.table.setRowHeight(row, max(40, editor.sizeHint().height() + 4))
            self.editors[key] = editor
        layout.addWidget(self.table)
        self.feedback = QLabel('')
        self.feedback.setWordWrap(True)
        self.feedback.setAccessibleName('快捷键冲突提示')
        layout.addWidget(self.feedback)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Save).setText('保存')
        box.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.save_button = box.button(QDialogButtonBox.StandardButton.Save)
        restore = QPushButton('恢复默认')
        restore.clicked.connect(self.restore_defaults)
        box.addButton(restore, QDialogButtonBox.ButtonRole.ResetRole)
        box.accepted.connect(self.save)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        self.validate()

    def values(self):
        return {key: editor.keySequence().toString(PORTABLE) for key, editor in self.editors.items()}

    def validate(self, *_):
        # Editors emit while the dialog is being built as well as during edits.
        if not hasattr(self, 'feedback'):
            return
        error = self.controller.error(self.values())
        self.feedback.setText(error)
        self.save_button.setEnabled(not error)

    def restore_defaults(self):
        for key, editor in self.editors.items():
            editor.setKeySequence(QKeySequence(self.controller.defaults[key]))
        self.validate()

    def save(self):
        error = self.controller.apply(self.values())
        self.feedback.setText(error)
        if not error:
            self.accept()
