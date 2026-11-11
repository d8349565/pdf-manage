"""Language preferences take effect next launch, preserving open documents."""
from PyQt6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QMessageBox, QVBoxLayout

from app.exportdialogs import COMPACT_QSS
from app.i18n import LANGUAGE_KEY, LANGUAGES, tr
from app.savesettings import save_preferences
from app.themes import set_theme_style


class LanguageDialog(QDialog):
    def __init__(self, parent, settings):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle(tr('界面语言 / Language'))
        self.setMinimumWidth(420)
        set_theme_style(self, COMPACT_QSS)
        layout = QVBoxLayout(self)
        self.language = QComboBox()
        self.language.setAccessibleName(tr('界面语言'))
        for code, name in LANGUAGES.items():
            self.language.addItem(tr(name) if code == 'auto' else name, code)
        value = settings.value(LANGUAGE_KEY, 'auto')
        self.language.setCurrentIndex(max(0, self.language.findData(value)))
        form = QFormLayout()
        form.addRow(tr('界面语言'), self.language)
        layout.addLayout(form)
        note = QLabel(tr('下次启动时生效。跟随系统会自动选择支持的语言，其他语言使用英语。'))
        note.setWordWrap(True)
        layout.addWidget(note)
        coverage = QLabel(tr('部分高级工具和错误提示可能显示英语或简体中文。'))
        coverage.setWordWrap(True)
        layout.addWidget(coverage)
        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        value = self.language.currentData()
        if value != self.settings.value(LANGUAGE_KEY, 'auto'):
            if not save_preferences(self.settings, {LANGUAGE_KEY: value}):
                self.feedback.setText(tr('语言设置未保存，请检查设置目录是否可写。'))
                return
            QMessageBox.information(self, tr('语言设置已保存'), tr('重新打开纸序 PDF 后使用所选语言，当前文档可继续编辑和保存。'))
        super().accept()
