"""Recent files on the existing welcome page and a small history manager."""
from app.i18n import tr
import os

from PyQt6.QtCore import QRect, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QFrame, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSizePolicy,
    QStyle, QStyledItemDelegate, QVBoxLayout, QWidget,
)
from app.icons import toolbar_icon
from app.settingwidgets import SettingSwitch
from app.themes import set_theme_style, ui_color
from app import APP_NAME


HOME_STYLE = '''
QWidget#readingHome { background: #F3F6F5; }
QWidget#homeContent { background: transparent; }
QLabel#homeTitle { font-size: 26px; font-weight: 600; color: #134E4A; }
QLabel#homeSubtitle { font-size: 13px; color: #4D6B67; }
QPushButton#homeOpen {
    min-height: 24px; padding: 9px 18px; border-radius: 8px;
    background: #0F766E; color: #FFFFFF; border: 1px solid #0F766E; font-weight: 600;
}
QPushButton#homeOpen:hover { background: #0D9488; border-color: #0D9488; }
QPushButton#homeOpen:pressed { background: #115E59; border-color: #115E59; }
QPushButton#homeOpen:focus { border: 2px solid #99B2AC; padding: 8px 17px; }
QFrame#homeRecentPanel {
    background: #FFFFFF; border: 1px solid #D7E8E4; border-radius: 12px;
}
QLabel#homeSectionTitle { font-size: 16px; font-weight: 600; color: #134E4A; }
QPushButton#homeManage {
    min-height: 20px; padding: 4px 8px; border: 1px solid transparent;
    border-radius: 6px; color: #4D6B67; background: transparent; font-size: 13px;
}
QPushButton#homeManage:hover { background: #F0FDFA; color: #134E4A; }
QPushButton#homeManage:focus { border-color: #0F766E; }
QPushButton#homeManage:disabled { color: #94A3B8; }
QListWidget#homeRecentList {
    background: transparent; border: none; border-radius: 0; padding: 0;
}
QListWidget#homeRecentList::item { padding: 0; border: none; height: 64px; }
QWidget#homeEmpty { background: transparent; }
QLabel#homeEmptyTitle { font-size: 16px; font-weight: 600; color: #134E4A; }
QLabel#homeEmptyHint { font-size: 13px; color: #4D6B67; }
QFrame#homeFooterLine { border: none; background: #D7E8E4; }
QLabel#homePrivacy { font-size: 12px; color: #4D6B67; }
'''

HISTORY_STYLE = '''
QDialog#readingHistory { background: #F3F6F5; }
QLabel#historyTitle { font-size: 22px; font-weight: 600; color: #134E4A; }
QLabel#historyEmptyTitle { font-size: 16px; font-weight: 600; color: #134E4A; }
QLabel#historyHint { font-size: 13px; color: #4D6B67; }
QFrame#historyPanel {
    background: #FFFFFF; border: 1px solid #D7E8E4; border-radius: 12px;
}
QWidget#historyEmpty { background: transparent; }
QListWidget#historyList {
    background: #FFFFFF; border: none; padding: 0; outline: none;
}
QListWidget#historyList::item { padding: 0; border: none; }
QListWidget#historyList QScrollBar::handle:vertical { background: #4D6B67; }
QListWidget#historyList QScrollBar::handle:vertical:hover { background: #0F766E; }
QPushButton#historyClear { background: transparent; border-color: transparent; color: #B91C1C; }
QPushButton#historyClear:hover:enabled { background: #FEF2F2; }
QPushButton#historyClear:pressed:enabled, QPushButton#historyClear:focus:enabled { border-color: #B91C1C; }
QPushButton#historyClear:disabled { color: #94A3B8; }
QPushButton#primary:hover:enabled { background: #115E59; border-color: #115E59; }
QPushButton#primary:pressed:enabled { border-color: #134E4A; }
QPushButton#primary:focus:enabled { border: 2px solid #134E4A; padding: 5px 13px; }
QPushButton#primary:disabled { background: #EEF3F0; border-color: #D7E8E4; color: #94A3B8; }
'''


class RecentFileDelegate(QStyledItemDelegate):
    """Paint cached file metadata, with no thumbnail or document loading."""
    def paint(self, painter, option, index):
        path = index.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        painter.save()
        painter.setClipRect(option.rect)
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        bounds = option.rect.adjusted(0, 1, -1, -1)
        center_y = bounds.center().y()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(ui_color('selected' if selected else 'hover')))
            painter.drawRoundedRect(QRectF(bounds), 8, 8)
        if selected:
            painter.setBrush(QColor(ui_color('accent')))
            painter.drawRoundedRect(QRectF(bounds.left() + 3, center_y - 10, 3, 20), 1.5, 1.5)
        if option.state & QStyle.StateFlag.State_HasFocus:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(ui_color('accent')), 2))
            painter.drawRoundedRect(QRectF(bounds.adjusted(1, 1, -1, -1)), 8, 8)
        icon_box = QRect(bounds.left() + 12, center_y - 18, 36, 36)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ui_color('subtle')))
        painter.drawRoundedRect(QRectF(icon_box), 8, 8)
        toolbar_icon('folder-open', '#526B63', 20).paint(painter, icon_box.adjusted(8, 8, -8, -8))
        title_font = QFont(option.font)
        title_font.setPixelSize(14)
        title_font.setWeight(QFont.Weight.Medium)
        small_font = QFont(option.font)
        small_font.setPixelSize(12)
        small_font.setWeight(QFont.Weight.Normal)
        small_metrics = QFontMetrics(small_font)
        page = index.data(Qt.ItemDataRole.UserRole + 1) or 0
        page_text = tr('第 {p0} 页', p0=page + 1)
        page_width = min(max(76, small_metrics.horizontalAdvance(page_text) + 16), bounds.width() // 3)
        page_box = QRect(bounds.right() - page_width - 11, center_y - 13, page_width, 26)
        text_x = icon_box.right() + 13
        text_width = max(0, page_box.left() - text_x - 16)
        painter.setFont(title_font)
        painter.setPen(QColor(ui_color('text')))
        name = QFontMetrics(title_font).elidedText(os.path.basename(path), Qt.TextElideMode.ElideMiddle, text_width)
        painter.drawText(QRect(text_x, center_y - 19, text_width, 22), Qt.AlignmentFlag.AlignVCenter, name)
        painter.setFont(small_font)
        painter.setPen(QColor(ui_color('text' if selected or hovered else 'muted')))
        directory = small_metrics.elidedText(os.path.dirname(path), Qt.TextElideMode.ElideMiddle, text_width)
        painter.drawText(QRect(text_x, center_y + 5, text_width, 17), Qt.AlignmentFlag.AlignVCenter, directory)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ui_color('subtle')))
        painter.drawRoundedRect(QRectF(page_box), 5, 5)
        painter.setPen(QColor(ui_color('muted')))
        page_text = small_metrics.elidedText(page_text, Qt.TextElideMode.ElideRight, max(0, page_width - 16))
        painter.drawText(page_box, Qt.AlignmentFlag.AlignCenter, page_text)
        painter.restore()


class ReadingHome(QWidget):
    def __init__(self, host):
        super().__init__()
        self.setObjectName('readingHome')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_theme_style(self, HOME_STYLE)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(32, 28, 32, 24)
        content = QWidget()
        content.setObjectName('homeContent')
        content.setMaximumWidth(920)
        content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(22)
        header = QHBoxLayout()
        header.setSpacing(24)
        headings = QVBoxLayout()
        headings.setSpacing(7)
        title = QLabel(APP_NAME)
        title.setObjectName('homeTitle')
        headings.addWidget(title)
        subtitle = QLabel(tr('打开或拖入 PDF、OFD 和图片'))
        subtitle.setObjectName('homeSubtitle')
        headings.addWidget(subtitle)
        header.addLayout(headings)
        header.addStretch()
        self.open_button = QPushButton(tr('打开文件'))
        self.open_button.setObjectName('homeOpen')
        self.open_button.setIcon(toolbar_icon('folder-open', '#FFFFFF', 18))
        self.open_button.setIconSize(QSize(18, 18))
        self.open_button.setAccessibleName(tr('打开 PDF、OFD 或图片'))
        host.shortcut_manager.watch_hint('file.open', self.open_button, '打开 PDF、OFD 或图片，也可直接拖入窗口')
        self.open_button.clicked.connect(host.open_files)
        header.addWidget(self.open_button, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)
        self.recent = RecentFiles(host)
        layout.addWidget(self.recent, 1)
        outer.addStretch()
        outer.addWidget(content, 1)
        outer.addStretch()


def fill_recent_list(widget, entries, *, row_height):
    widget.clear()
    for entry in entries:
        path = entry['path']
        item = QListWidgetItem(tr('{p0}  ·  第 {p1} 页', p0=f'{os.path.basename(path)}', p1=f"{entry['page'] + 1}"))
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setData(Qt.ItemDataRole.UserRole + 1, entry['page'])
        item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, path)
        item.setToolTip(path)
        item.setSizeHint(QSize(0, row_height))
        widget.addItem(item)


class RecentFiles(QFrame):
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.setObjectName('homeRecentPanel')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 12)
        layout.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel(tr('最近阅读'))
        title.setObjectName('homeSectionTitle')
        header.addWidget(title)
        header.addStretch()
        self.manage = QPushButton(tr('全部记录'))
        self.manage.setObjectName('homeManage')
        self.manage.setToolTip(tr('查看和管理最近 50 条记录'))
        self.manage.clicked.connect(host.manage_reading_history)
        header.addWidget(self.manage)
        layout.addLayout(header)
        self.files = QListWidget()
        self.files.setObjectName('homeRecentList')
        self.files.setItemDelegate(RecentFileDelegate(self.files))
        self.files.setUniformItemSizes(True)
        self.files.setMouseTracking(True)
        self.files.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.files.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.files.setAccessibleName(tr('最近阅读，单击打开，最多显示 10 条'))
        self.files.setMinimumHeight(78)
        self.files.itemClicked.connect(self.open_item)
        self.files.itemActivated.connect(self.open_item)
        layout.addWidget(self.files, 1)
        self.empty = QWidget()
        self.empty.setObjectName('homeEmpty')
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setSpacing(10)
        empty_layout.addStretch()
        empty_title = QLabel(tr('还没有阅读记录'))
        empty_title.setObjectName('homeEmptyTitle')
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)
        empty_hint = QLabel(tr('打开一份文档，下次可以从这里继续。'))
        empty_hint.setObjectName('homeEmptyHint')
        empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_hint)
        empty_layout.addStretch()
        layout.addWidget(self.empty, 1)
        line = QFrame()
        line.setObjectName('homeFooterLine')
        line.setFixedHeight(1)
        layout.addWidget(line)
        footer = QHBoxLayout()
        self.resume = SettingSwitch('启动时继续上次阅读')
        self.resume.setObjectName('homeResume')
        self.resume.setChecked(host._window_settings.value('reading/resumeLast', False, type=bool))
        self.resume.toggled.connect(lambda enabled: host._window_settings.setValue('reading/resumeLast', enabled))
        footer.addWidget(self.resume)
        footer.addStretch()
        privacy = QLabel(tr('文件仅在本机处理'))
        privacy.setObjectName('homePrivacy')
        footer.addWidget(privacy)
        layout.addLayout(footer)
        host.reading_history.changed.connect(self.refresh_if_visible)
        self.refresh()

    def open_item(self, item):
        self.host.open_recent_file(item.data(Qt.ItemDataRole.UserRole))

    def refresh_if_visible(self):
        if self.isVisible():
            self.refresh()

    def refresh(self):
        entries = self.host.reading_history.entries[:10]
        fill_recent_list(self.files, entries, row_height=64)
        self.files.setVisible(bool(entries))
        self.empty.setVisible(not entries)
        self.manage.setEnabled(bool(entries))

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)


class ReadingHistoryDialog(QDialog):
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.setWindowTitle(tr('最近阅读记录'))
        self.setObjectName('readingHistory')
        self.resize(660, 520)
        set_theme_style(self, HISTORY_STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)

        header = QVBoxLayout()
        header.setSpacing(4)
        heading = QHBoxLayout()
        title = QLabel(tr('继续阅读'))
        title.setObjectName('historyTitle')
        heading.addWidget(title, 1)
        self.count = QLabel()
        self.count.setObjectName('historyHint')
        heading.addWidget(self.count)
        header.addLayout(heading)
        self.hint = QLabel(tr('双击文件，或选中后按 Enter，回到上次阅读的位置。'))
        self.hint.setObjectName('historyHint')
        self.hint.setWordWrap(True)
        header.addWidget(self.hint)
        layout.addLayout(header)

        panel = QFrame()
        panel.setObjectName('historyPanel')
        body = QVBoxLayout(panel)
        body.setContentsMargins(7, 7, 7, 7)
        self.files = QListWidget()
        self.files.setObjectName('historyList')
        self.files.setAccessibleName(tr('全部最近阅读记录'))
        self.files.setItemDelegate(RecentFileDelegate(self.files))
        self.files.setUniformItemSizes(True)
        self.files.setMouseTracking(True)
        self.files.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.files.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.files.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.files.itemActivated.connect(self.open_item)
        body.addWidget(self.files, 1)

        self.empty = QWidget()
        self.empty.setObjectName('historyEmpty')
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setSpacing(6)
        empty_layout.addStretch()
        empty_title = QLabel(tr('还没有阅读记录'))
        empty_title.setObjectName('historyEmptyTitle')
        empty_hint = QLabel(tr('打开一份文档，下次可以从这里继续。'))
        empty_hint.setObjectName('historyHint')
        for label in (empty_title, empty_hint):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            empty_layout.addWidget(label)
        empty_layout.addStretch()
        body.addWidget(self.empty, 1)
        layout.addWidget(panel, 1)

        footer = QVBoxLayout()
        footer.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.remove_button = QPushButton(tr('移除所选'))
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button = QPushButton(tr('清空记录'))
        self.clear_button.setObjectName('historyClear')
        self.clear_button.clicked.connect(self.clear_history)
        row.addWidget(self.remove_button)
        row.addWidget(self.clear_button)
        row.addStretch()
        close_button = QPushButton(tr('关闭'))
        close_button.clicked.connect(self.reject)
        row.addWidget(close_button)
        self.open_button = QPushButton(tr('打开'))
        self.open_button.setObjectName('primary')
        self.open_button.setMinimumWidth(92)
        self.open_button.setDefault(True)
        self.open_button.clicked.connect(lambda: self.open_item())
        row.addWidget(self.open_button)
        footer.addLayout(row)
        self.note = QLabel(tr('清理记录后仍可打开原文件。'))
        self.note.setObjectName('historyHint')
        self.note.setWordWrap(True)
        footer.addWidget(self.note)
        layout.addLayout(footer)

        self.files.itemSelectionChanged.connect(self.sync_buttons)
        host.reading_history.changed.connect(self.refresh)
        self.refresh()
        if self.files.count():
            self.files.setCurrentRow(0)
            self.files.setFocus()

    def refresh(self):
        history = self.host.reading_history
        fill_recent_list(self.files, history.entries, row_height=52)
        count = self.files.count()
        has_entries = count > 0
        self.count.setText(tr('{p0} / {p1} 条记录', p0=count, p1=history.LIMIT))
        self.files.setVisible(has_entries)
        self.empty.setVisible(not has_entries)
        self.hint.setVisible(has_entries)
        self.note.setVisible(has_entries)
        self.sync_buttons()

    def sync_buttons(self):
        selected_count = len(self.files.selectedItems())
        self.open_button.setEnabled(selected_count == 1)
        self.remove_button.setEnabled(selected_count > 0)
        self.clear_button.setEnabled(bool(self.files.count()))

    def open_item(self, item=None):
        selected = self.files.selectedItems()
        if len(selected) == 1 and (item is None or item.isSelected()):
            path = selected[0].data(Qt.ItemDataRole.UserRole)
            self.accept()
            self.host.open_recent_file(path)

    def remove_selected(self):
        self.host.reading_history.remove([item.data(Qt.ItemDataRole.UserRole) for item in self.files.selectedItems()])

    def clear_history(self):
        decision = QMessageBox.question(
            self, tr('清空阅读记录'), tr('只清空本机阅读记录，原文件保留。是否继续？'),
            defaultButton=QMessageBox.StandardButton.No,
        )
        if decision == QMessageBox.StandardButton.Yes:
            self.host.reading_history.remove([entry['path'] for entry in self.host.reading_history.entries])
