"""An explicit opt-in control for this copy's Explorer shortcuts."""
from app.themes import set_theme_style
from pathlib import Path
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from app.contextmenus import ContextMenus
from app.imagequick import STYLE
from app.progress import run_operation


class ContextMenuDialog(QDialog):
    def __init__(self, parent=None, *, manager=None):
        super().__init__(parent)
        self.setWindowTitle('右键快捷工具 - 纸序 PDF')
        set_theme_style(self, STYLE)
        self.resize(580, 350)
        self.setMinimumWidth(500)
        self.manager = manager
        if self.manager is None and getattr(sys, 'frozen', False):
            self.manager = ContextMenus(sys.executable)
        self._task_dialog = None
        self.busy = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)
        heading = QLabel('右键快捷工具')
        heading.setObjectName('heading')
        layout.addWidget(heading)
        edition = ('绿色版' if self.manager.plan.portable else '安装版') if self.manager else '源码运行'
        layout.addWidget(QLabel('当前版本：' + edition))
        self.path = QLineEdit(self.manager.plan.executable if self.manager else str(Path(sys.argv[0]).resolve()))
        self.path.setReadOnly(True)
        self.path.setAccessibleName('此版本的程序位置')
        self.path.setCursorPosition(0)
        self.path.setToolTip(self.path.text())
        layout.addWidget(self.path)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        description = QLabel('图片转 PDF、图片压缩、PDF 拆分／合并、Word 与 Markdown 转 PDF。\n'
                             '仅管理这份程序的菜单，其他版本独立；Windows 11 中可能在“显示更多选项”内。')
        description.setObjectName('detail')
        description.setWordWrap(True)
        layout.addWidget(description)
        self.feedback = QLabel()
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        buttons = QHBoxLayout()
        self.actions = {}
        for action, title in (('enable', '启用'), ('repair', '修复'), ('remove', '移除')):
            button = QPushButton(title)
            button.setAutoDefault(False)
            button.setAccessibleName(title + '此版本的右键菜单')
            button.clicked.connect(lambda checked=False, command=action: self.apply(command))
            self.actions[action] = button
            buttons.addWidget(button)
        self.actions['enable'].setObjectName('primary')
        buttons.addStretch()
        close = QPushButton('关闭')
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self):
        for button in self.actions.values():
            button.setEnabled(False)
        if self.manager is None:
            self.status.setText('请运行安装版或绿色版程序，再管理右键菜单。')
            return
        try:
            state = self.manager.status()
            self.status.setText({'off': '当前状态：未启用。需要时点击“启用”。',
                                 'enabled': '当前状态：已启用。移动程序或菜单失效时可点“修复”。',
                                 'repair': '当前状态：登记不完整或程序位置已改变，请点击“修复”。'}[state])
            self.actions['enable'].setEnabled(state == 'off' and not self.busy)
            self.actions['repair'].setEnabled(state != 'off' and not self.busy)
            self.actions['remove'].setEnabled(state != 'off' and not self.busy)
        except OSError as exc:
            self.status.setText('无法读取右键菜单状态：' + str(exc))

    def apply(self, action):
        if self.busy or self.manager is None:
            return
        self.busy = True
        self.feedback.setText('正在更新此版本的菜单…')
        self.refresh()
        try:
            run_operation(self, '更新右键快捷工具', lambda report: getattr(self.manager, action)())
            self.feedback.setText({'enable': '已启用此版本的右键菜单。', 'repair': '已修复此版本的右键菜单。',
                                   'remove': '已移除此版本的右键菜单。'}[action])
        except (OSError, ValueError) as exc:
            self.feedback.setText('操作未完成：' + str(exc))
        finally:
            self.busy = False
            self.refresh()

    def done(self, result):
        if not self.busy:
            super().done(result)
