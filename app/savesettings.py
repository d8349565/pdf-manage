"""Default save policy and the explicit, source-only overwrite confirmation."""
from app.i18n import tr
import os
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QTextOption
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout,
)

from app.exportdialogs import COMPACT_QSS, hint
from app.themes import set_theme_style

MODE_KEY = 'saving/default_mode'
SKIP_CONFIRMATION_KEY = 'saving/skip_overwrite_confirmation'


def default_save_mode(settings):
    mode = settings.value(MODE_KEY, 'copy')
    return mode if mode in ('copy', 'overwrite') else 'copy'


def skips_overwrite_confirmation(settings):
    return str(settings.value(SKIP_CONFIRMATION_KEY, False)).lower() == 'true'


def save_preferences(settings, values):
    old = {key: settings.value(key) for key in values}
    for key, value in values.items():
        settings.setValue(key, value)
    settings.sync()
    if settings.status() == QSettings.Status.NoError:
        return True
    for key, value in old.items():
        if value is None:
            settings.remove(key)
        else:
            settings.setValue(key, value)
    settings.sync()
    return False


class SaveSettingsDialog(QDialog):
    def __init__(self, parent, settings):
        super().__init__(parent)
        self.settings = settings
        self.restore_requested = False
        self.setWindowTitle(tr('保存设置'))
        self.setMinimumWidth(500)
        set_theme_style(self, COMPACT_QSS)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        self.mode = QComboBox()
        self.mode.setAccessibleName(tr('默认保存方式'))
        self.mode.addItem(tr('另存为新文件（默认）'), 'copy')
        self.mode.addItem(tr('覆盖源文件'), 'overwrite')
        self.mode.setCurrentIndex(self.mode.findData(default_save_mode(settings)))
        form = QFormLayout()
        form.addRow(tr('默认保存方式'), self.mode)
        layout.addLayout(form)
        hint(layout, tr('用于每份文档第一次保存。另存成功后，后续保存沿用该副本的位置。'))
        hint(layout, tr('把其他文件插入当前文档时，覆盖保存到当前文档。“合并已打开文件”默认另存，也可选择覆盖目标；其他源文件保留。'))
        row = QHBoxLayout()
        self.reminder = QLabel()
        self.restore = QPushButton(tr('重新开启覆盖提醒'))
        self.restore.setEnabled(skips_overwrite_confirmation(settings))
        self.restore.clicked.connect(self.restore_confirmation)
        self.reminder.setText(tr('首次覆盖提醒已关闭') if self.restore.isEnabled() else tr('首次覆盖前会提醒'))
        row.addWidget(self.reminder, 1)
        row.addWidget(self.restore)
        layout.addLayout(row)
        hint(layout, tr('同一文档在同一位置保存成功后，后续保存不再提醒。勾选“下次不再提示”并保存成功，还可关闭其他文档及重启后的首次覆盖提醒。'))
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr('保存设置'))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr('取消'))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def restore_confirmation(self):
        self.restore_requested = True
        self.reminder.setText(tr('首次覆盖前会提醒（保存设置后生效）'))
        self.restore.setEnabled(False)

    def accept(self):
        values = {MODE_KEY: self.mode.currentData()}
        if self.restore_requested:
            values[SKIP_CONFIRMATION_KEY] = False
        if not save_preferences(self.settings, values):
            QMessageBox.warning(self, tr('设置未保存'), tr('无法保存设置，请检查当前用户的写入权限。'))
            return
        super().accept()


class OverwriteDialog(QDialog):
    def __init__(self, parent, paths, *, show_reminder=True):
        super().__init__(parent)
        self.setWindowTitle(tr('确认覆盖源文件'))
        self.setMinimumWidth(520)
        self.resize(620, 260)
        set_theme_style(self, COMPACT_QSS)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        hint(layout, tr('此次保存将覆盖源文件，原文件内容将被修改。其他源文件保留。'))
        self.target = QComboBox(self)
        self.target.setAccessibleName(tr('要覆盖的源文件'))
        self.target.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.target.setMinimumContentsLength(24)
        self.target.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.target.setVisible(len(paths) > 1)
        if len(paths) > 1:
            self.target.addItem(tr('请选择要覆盖的源文件'), None)
        for path in paths:
            self.target.addItem(path, path)
        form = QFormLayout()
        if len(paths) > 1:
            form.addRow(tr('目标文件'), self.target)
        else:
            form.addRow(QLabel(tr('目标文件')))
        layout.addLayout(form)
        self.path_label = QLabel()
        self.path_label.setTextFormat(Qt.TextFormat.PlainText)
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label)
        self.skip = QCheckBox(tr('下次不再提示'))
        self.skip.setToolTip(tr('同一文档保存成功后，本次打开期间不再重复确认。勾选后，其他文档及重启后的首次覆盖也不再提示。'))
        self.skip.setVisible(show_reminder)
        layout.addWidget(self.skip)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.cancel = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self.cancel.setText(tr('取消'))
        self.confirm = self.buttons.addButton(tr('确认覆盖'), QDialogButtonBox.ButtonRole.AcceptRole)
        self.confirm.setAutoDefault(False)
        self.cancel.setDefault(True)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.target.currentIndexChanged.connect(self.update_target)
        self.update_target()

    def update_target(self):
        path = self.target.currentData()
        self.path_label.setText(path or tr('本次仅覆盖您明确选择的一个文件。'))
        self.target.setToolTip(path or '')
        self.confirm.setEnabled(bool(path))

    def accept(self):
        if self.target.currentData():
            super().accept()


def confirm_overwrite(parent, paths, settings):
    skip = skips_overwrite_confirmation(settings)
    # Even after opting out, an ambiguous merge destination must be selected.
    if len(paths) == 1 and skip:
        return paths[0], False
    dialog = OverwriteDialog(parent, paths, show_reminder=not skip)
    try:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None, False
        return dialog.target.currentData(), dialog.skip.isChecked() and not skip
    finally:
        dialog.deleteLater()


class TagLossDialog(QDialog):
    def __init__(self, parent, path, allow_overwrite):
        super().__init__(parent)
        self.choice = 'cancel'
        self.setWindowTitle(tr('辅助阅读信息可能丢失'))
        self.setMinimumWidth(520)
        self.resize(620, 320)
        set_theme_style(self, COMPACT_QSS)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        self.message = hint(layout, tr('辅助朗读信息帮助屏幕阅读器识别标题、段落和阅读顺序。\n这次合并可能无法完整保留这些信息。\n这不是正文损坏提示。'))
        caption = QLabel(tr('保存位置：') + os.path.basename(path))
        caption.setTextFormat(Qt.TextFormat.PlainText)
        caption.setWordWrap(True)
        layout.addWidget(caption)
        self.full_path = QPlainTextEdit(path)
        self.full_path.setAccessibleName(tr('完整保存路径'))
        self.full_path.setReadOnly(True)
        self.full_path.setWordWrapMode(QTextOption.WrapMode.WrapAnywhere)
        self.full_path.setTabChangesFocus(True)
        self.full_path.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.full_path.setFixedHeight(self.full_path.fontMetrics().lineSpacing() * 3 + 12)
        layout.addWidget(self.full_path)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.cancel = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self.cancel.setText(tr('取消'))
        self.buttons.rejected.connect(self.reject)
        self.copy = self.buttons.addButton(tr('改为另存副本') if allow_overwrite else tr('确认并保存副本'),
                                           QDialogButtonBox.ButtonRole.ActionRole)
        self.copy.setAutoDefault(False)
        self.copy.clicked.connect(lambda: self.select('copy'))
        self.overwrite = None
        if allow_overwrite:
            self.overwrite = self.buttons.addButton(tr('仍覆盖原文件'), QDialogButtonBox.ButtonRole.DestructiveRole)
            self.overwrite.setAutoDefault(False)
            self.overwrite.clicked.connect(lambda: self.select('overwrite'))
        self.cancel.setDefault(True)
        self.cancel.setFocus()
        layout.addWidget(self.buttons)

    def select(self, choice):
        self.choice = choice
        self.accept()


def confirm_tag_loss(parent, path, allow_overwrite):
    dialog = TagLossDialog(parent, path, allow_overwrite)
    try:
        return dialog.choice if dialog.exec() == QDialog.DialogCode.Accepted else 'cancel'
    finally:
        dialog.deleteLater()


def remember_overwrite_choice(parent, settings, remember, requested_path, result_path):
    if (remember and os.path.normcase(os.path.abspath(requested_path)) == os.path.normcase(os.path.abspath(result_path))
            and not save_preferences(settings, {SKIP_CONFIRMATION_KEY: True})):
        QMessageBox.warning(parent, tr('文件已保存'), tr('PDF 已保存，但未能记住“不再提示”的选择，下次首次覆盖仍会提醒。'))
