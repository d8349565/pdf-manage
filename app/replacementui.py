"""Small native controls for editing existing text on the PDF page."""
from PyQt6.QtCore import QRect, QSignalBlocker, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontDatabase, QPainter, QSyntaxHighlighter, QTextCharFormat
from PyQt6.QtWidgets import QApplication, QComboBox, QCompleter, QDoubleSpinBox, QFrame, QProxyStyle, QSizePolicy, QStyle, QTextEdit, QToolButton

from app.themes import set_theme_style, ui_color
from app.icons import toolbar_icon


class _FontMenuStyle(QProxyStyle):
    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_ComboBox_Popup:
            return 0  # A scrollable list, rather than the screen-height native menu.
        return super().styleHint(hint, option, widget, returnData)


class FontChoice(QComboBox):
    """Search system families using UI type, with a bounded eight-row list."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._menu_style = _FontMenuStyle(QApplication.style().objectName())
        self._menu_style.setParent(self)
        self.setStyle(self._menu_style)
        self.setStyleSheet('QComboBox::down-arrow { width: 0; height: 0; image: none; }')
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(12)
        self.setMaxVisibleItems(8)
        self.addItems([family for family in QFontDatabase.families() if not family.startswith('@')])
        for index in range(self.count()):
            self.setItemData(index, QSize(0, 28), Qt.ItemDataRole.SizeHintRole)
        self.lineEdit().setPlaceholderText('搜索字体')
        set_theme_style(self.lineEdit(),
            'QLineEdit { min-height: 0; padding: 0; border: none; background: transparent; '
            'font-size: 13px; color: #1F2937; placeholder-text-color: #4D6B67; }')
        completer = self.completer()
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setMaxVisibleItems(8)
        for view in (self.view(), completer.popup()):
            view.setTextElideMode(Qt.TextElideMode.ElideRight)
            view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            view.setMaximumHeight(234)
            view.setMinimumWidth(240)
            view.setMaximumWidth(320)
            view.setUniformItemSizes(True)
            set_theme_style(view,
                'QAbstractItemView { background: #FFFFFF; color: #1F2937; font-size: 13px; '
                'border: 1px solid #CBD5E1; padding: 4px 0; outline: 0; } '
                'QAbstractItemView::item { height: 28px; padding: 0 8px; border: none; } '
                'QAbstractItemView::item:hover { background: #F0FDFA; } '
                'QAbstractItemView::item:selected { background: #CCFBF1; color: #134E4A; }')

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.translate(self.width() - 12, self.height() / 2)
        painter.rotate(90)
        toolbar_icon('chevron-right', '#4D6B67', 14).paint(painter, -7, -7, 14, 14)
        painter.end()


class FontSize(QDoubleSpinBox):
    def textFromValue(self, value):
        return f'{value:g}'


class TextColorButton(QToolButton):
    _color = QColor(Qt.GlobalColor.black)

    def setColor(self, color):
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        bar = QRect(8, self.height() - 8, self.width() - 16, 4)
        painter.fillRect(bar, self._color)
        painter.setPen(QColor(ui_color('muted')))
        painter.drawRect(bar.adjusted(0, 0, -1, -1))
        painter.end()


class _RunPreview(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.runs = []

    def highlightBlock(self, text):
        offset = 0
        for value, font, color in self.runs:
            length = len(value.encode('utf-16-le')) // 2
            fmt = QTextCharFormat()
            fmt.setFont(font)
            fmt.setForeground(color)
            self.setFormat(offset, length, fmt)
            offset += length


class ReplacementInput(QTextEdit):
    """One-line native text input; highlighting never enters the typing undo stack."""
    returnPressed = pyqtSignal()

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.document().setDocumentMargin(0)
        self.setText(text)
        self._preview = _RunPreview(self.document())
        self._preedit = False

    def text(self):
        return self.document().toRawText()

    def setText(self, text):
        self.setPlainText(text.replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' '))

    def preview(self, runs):
        blocker = QSignalBlocker(self)
        self._preview.runs = runs
        self._preview.rehighlight()

    def insertFromMimeData(self, source):
        self.insertPlainText(source.text().replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' '))

    def inputMethodEvent(self, event):
        self._preedit = bool(event.preeditString())
        super().inputMethodEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not self._preedit:
                self.returnPressed.emit()
            event.accept()
        else:
            super().keyPressEvent(event)
