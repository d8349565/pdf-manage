"""Shared split settings for the sidebar and the toolbar popup."""
from PyQt6.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QLineEdit, QRadioButton, QVBoxLayout, QWidget

from app.i18n import tr
from app.pagetoolsui import ClearSpinBox
from app.workspace import parse_range_segments


class SplitOptions(QWidget):
    changed = pyqtSignal()

    def __init__(self, button):
        super().__init__()
        self._page_count, self._reason = 0, ''
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.each = QRadioButton(tr('每页一个文件'))
        self.every = QRadioButton(tr('每几页一个文件'))
        self.count = ClearSpinBox()
        self.count.setRange(1, 1_000_000)
        self.count.setValue(5)
        self.count.setSuffix(tr(' 页'))
        self.count.setAccessibleName(tr('每个拆分文件的页数'))
        self.count.setFixedSize(88, 36)
        self.count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.count.hide()
        self.every.toggled.connect(self.count.setVisible)
        self.range_mode = QRadioButton(tr('按页码范围'))
        self.mode_group = QButtonGroup(self)
        for index, radio in enumerate((self.each, self.every, self.range_mode)):
            self.mode_group.addButton(radio, index)
        self.each.setChecked(True)
        layout.addWidget(self.each)
        interval = QHBoxLayout()
        interval.setContentsMargins(0, 0, 0, 0)
        interval.setSpacing(8)
        interval.addWidget(self.every, 1)
        interval.addWidget(self.count)
        layout.addLayout(interval)
        layout.addWidget(self.range_mode)
        self.range = QLineEdit()
        self.range.setPlaceholderText(tr('如 1-3,5,7-9，每段一个文件'))
        self.range.setAccessibleName(tr('拆分页码范围'))
        self.range.hide()
        self.range_mode.toggled.connect(self.range.setVisible)
        layout.addWidget(self.range)
        layout.addSpacing(4)
        self.feedback = QLabel('')
        self.feedback.setObjectName('exportFeedback')
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        self.button = button
        layout.addWidget(button)
        for radio in self.mode_group.buttons():
            radio.toggled.connect(self._changed)
        self.count.valueChanged.connect(self._changed)
        self.range.textChanged.connect(self._changed)
        self._validate()

    def copy_from(self, other):
        # Keep a settings copy from feeding partial radio/field changes back.
        with QSignalBlocker(self):
            self.count.setValue(other.count.value())
            self.range.setText(other.range.text())
            self.mode_group.button(other.mode_group.checkedId()).setChecked(True)
        self._validate()

    def set_context(self, count, reason):
        self._page_count, self._reason = count, reason
        self._validate()

    def groups(self, count):
        if self.range_mode.isChecked():
            return parse_range_segments(self.range.text(), count)
        step = self.count.value() if self.every.isChecked() else 1
        return [list(range(start, min(start + step, count))) for start in range(0, count, step)]

    def _changed(self):
        self._validate()
        self.changed.emit()

    def _validate(self):
        error, summary = '', ''
        try:
            if not self._page_count:
                raise ValueError('请先打开 PDF')
            if self._reason:
                raise ValueError(self._reason)
            if self.range_mode.isChecked():
                outputs = len(parse_range_segments(self.range.text(), self._page_count))
            else:
                step = self.count.value() if self.every.isChecked() else 1
                outputs = (self._page_count + step - 1) // step
            summary = tr('预计输出 {p0} 个文件', p0=f'{outputs}')
        except ValueError as exc:
            error = str(exc)
        self.button.setEnabled(not error)
        self.button.setToolTip(error)
        self.feedback.setText(error or summary)
        self.feedback.setVisible(bool(error or summary))
