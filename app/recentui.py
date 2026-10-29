"""Recent files on the existing welcome page and a small history manager."""
import os

from PyQt6.QtCore import QRect, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSizePolicy,
    QStyle, QStyledItemDelegate, QVBoxLayout, QWidget,
)
from app.icons import toolbar_icon
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
QCheckBox#homeResume { background: transparent; font-size: 13px; color: #4D6B67; spacing: 8px; }
QCheckBox#homeResume:focus { color: #134E4A; }
QLabel#homePrivacy { font-size: 12px; color: #4D6B67; }
'''


class RecentFileDelegate(QStyledItemDelegate):
    """Paint cached file metadata, with no thumbnail or document loading."""
    def paint(self, painter, option, index):
        path = index.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        bounds = option.rect.adjusted(0, 1, -1, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(ui_color('selected' if selected else 'hover')))
            painter.drawRoundedRect(QRectF(bounds), 8, 8)
        if option.state & QStyle.StateFlag.State_HasFocus:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(ui_color('accent')), 1.5))
            painter.drawRoundedRect(QRectF(bounds.adjusted(1, 1, -1, -1)), 8, 8)
        icon_box = QRect(bounds.left() + 12, bounds.center().y() - 18, 36, 36)
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
        page_box = QRect(bounds.right() - 99, bounds.center().y() - 13, 88, 26)
        text_x = icon_box.right() + 13
        text_width = max(0, page_box.left() - text_x - 16)
        painter.setFont(title_font)
        painter.setPen(QColor(ui_color('text')))
        name = QFontMetrics(title_font).elidedText(os.path.basename(path), Qt.TextElideMode.ElideMiddle, text_width)
        painter.drawText(QRect(text_x, bounds.top() + 11, text_width, 22), Qt.AlignmentFlag.AlignVCenter, name)
        painter.setFont(small_font)
        painter.setPen(QColor(ui_color('muted')))
        directory = QFontMetrics(small_font).elidedText(os.path.dirname(path), Qt.TextElideMode.ElideMiddle, text_width)
        painter.drawText(QRect(text_x, bounds.top() + 35, text_width, 17), Qt.AlignmentFlag.AlignVCenter, directory)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ui_color('subtle')))
        painter.drawRoundedRect(QRectF(page_box), 5, 5)
        painter.setPen(QColor(ui_color('muted')))
        page = index.data(Qt.ItemDataRole.UserRole + 1) or 0
        painter.drawText(page_box, Qt.AlignmentFlag.AlignCenter, f'第 {page + 1} 页')
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
        subtitle = QLabel('打开或拖入 PDF、OFD 和图片')
        subtitle.setObjectName('homeSubtitle')
        headings.addWidget(subtitle)
        header.addLayout(headings)
        header.addStretch()
        self.open_button = QPushButton('打开文件')
        self.open_button.setObjectName('homeOpen')
        self.open_button.setIcon(toolbar_icon('folder-open', '#FFFFFF', 18))
        self.open_button.setIconSize(QSize(18, 18))
        self.open_button.setAccessibleName('打开 PDF、OFD 或图片')
        self.open_button.setToolTip('打开 PDF、OFD 或图片（Ctrl+O），也可直接拖入窗口')
        self.open_button.clicked.connect(host.open_files)
        header.addWidget(self.open_button, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)
        self.recent = RecentFiles(host)
        layout.addWidget(self.recent, 1)
        outer.addStretch()
        outer.addWidget(content, 1)
        outer.addStretch()


def fill_recent_list(widget, entries, *, row_height=34):
    widget.clear()
    for entry in entries:
        path = entry['path']
        item = QListWidgetItem(f"{os.path.basename(path)}  ·  第 {entry['page'] + 1} 页")
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
        title = QLabel('最近阅读')
        title.setObjectName('homeSectionTitle')
        header.addWidget(title)
        header.addStretch()
        self.manage = QPushButton('全部记录')
        self.manage.setObjectName('homeManage')
        self.manage.setToolTip('查看和管理最近 50 条记录')
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
        self.files.setAccessibleName('最近阅读，单击打开，最多显示 10 条')
        self.files.setMinimumHeight(78)
        self.files.itemClicked.connect(self.open_item)
        self.files.itemActivated.connect(self.open_item)
        layout.addWidget(self.files, 1)
        self.empty = QWidget()
        self.empty.setObjectName('homeEmpty')
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setSpacing(10)
        empty_layout.addStretch()
        empty_title = QLabel('还没有阅读记录')
        empty_title.setObjectName('homeEmptyTitle')
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)
        empty_hint = QLabel('打开一份文档，下次可以从这里继续。')
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
        self.resume = QCheckBox('启动时继续上次阅读')
        self.resume.setObjectName('homeResume')
        self.resume.setChecked(host._window_settings.value('reading/resumeLast', False, type=bool))
        self.resume.toggled.connect(lambda enabled: host._window_settings.setValue('reading/resumeLast', enabled))
        footer.addWidget(self.resume)
        footer.addStretch()
        privacy = QLabel('文件仅在本机处理')
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
        self.setWindowTitle('最近阅读记录')
        self.resize(620, 460)
        layout = QVBoxLayout(self)
        caption = QLabel('最多保留 50 条记录；移除记录不会删除原文件。')
        caption.setObjectName('hint')
        layout.addWidget(caption)
        self.files = QListWidget()
        self.files.setAccessibleName('全部最近阅读记录')
        self.files.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.files.itemActivated.connect(self.open_item)
        layout.addWidget(self.files)
        row = QHBoxLayout()
        self.open_button = QPushButton('打开')
        self.open_button.setObjectName('primary')
        self.open_button.clicked.connect(lambda: self.open_item(self.files.currentItem()))
        self.remove_button = QPushButton('移除所选')
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button = QPushButton('清空记录')
        self.clear_button.clicked.connect(self.clear_history)
        for button in (self.open_button, self.remove_button, self.clear_button):
            row.addWidget(button)
        row.addStretch()
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.button(QDialogButtonBox.StandardButton.Close).setText('关闭')
        box.rejected.connect(self.reject)
        row.addWidget(box)
        layout.addLayout(row)
        self.files.itemSelectionChanged.connect(self.sync_buttons)
        host.reading_history.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        fill_recent_list(self.files, self.host.reading_history.entries)
        self.sync_buttons()

    def sync_buttons(self):
        self.open_button.setEnabled(bool(self.files.currentItem()))
        self.remove_button.setEnabled(bool(self.files.selectedItems()))
        self.clear_button.setEnabled(bool(self.files.count()))

    def open_item(self, item):
        if item is not None:
            path = item.data(Qt.ItemDataRole.UserRole)
            self.accept()
            self.host.open_recent_file(path)

    def remove_selected(self):
        self.host.reading_history.remove([item.data(Qt.ItemDataRole.UserRole) for item in self.files.selectedItems()])

    def clear_history(self):
        if QMessageBox.question(self, '清空阅读记录', '只清空本机阅读记录，原文件保留。是否继续？') == QMessageBox.StandardButton.Yes:
            self.host.reading_history.remove([entry['path'] for entry in self.host.reading_history.entries])
