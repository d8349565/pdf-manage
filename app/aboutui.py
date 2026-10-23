"""Product information and optional support, opened from the About menu."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSettings, QSize, Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QToolButton, QVBoxLayout, QWidget,
)

from app import APP_NAME, APP_ORGANIZATION, APP_PROJECT_URL, APP_SLUG, __version__
from app.icons import brand_icon, toolbar_icon
from app.updates import UpdateChecker

LANZOU_URL = "https://leezfy.lanzouq.com/b02qtfd77g"
LANZOU_PASSWORD = "atxq"


class AboutDialog(QDialog):
    def __init__(self, parent=None, updates=None):
        super().__init__(parent)
        self.updates = updates if updates is not None else UpdateChecker(QSettings(APP_ORGANIZATION, APP_SLUG), self)
        self.setObjectName("aboutDialog")
        self.setWindowTitle(f"关于 {APP_NAME}")
        self.setWindowIcon(brand_icon())
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("""
            QDialog#aboutDialog, QDialog#aboutDialog QWidget {
                background: transparent; color: #173C36; border: none;
                font-family: "Segoe UI", "Microsoft YaHei UI"; font-size: 14px;
            }
            QDialog#aboutDialog QFrame#aboutPanel {
                background: #FBFCFA; border: 1px solid #D6E3DD; border-radius: 16px;
            }
            QDialog#aboutDialog QLabel#aboutTitle { font-size: 24px; font-weight: 600; }
            QDialog#aboutDialog QLabel#aboutName { font-size: 22px; font-weight: 600; }
            QDialog#aboutDialog QLabel#aboutMuted { color: #65796E; }
            QDialog#aboutDialog QLabel#aboutSection { font-size: 18px; font-weight: 600; }
            QDialog#aboutDialog QLabel#aboutVersion { font-size: 16px; font-weight: 600; }
            QDialog#aboutDialog QFrame#aboutCard {
                background: #F0F4EF; border: 1px solid #DCE4D8; border-radius: 10px;
            }
            QDialog#aboutDialog QFrame#aboutDivider { background: #DCE4D8; }
            QDialog#aboutDialog QToolButton {
                min-width: 32px; min-height: 32px; padding: 0; border-radius: 6px;
            }
            QDialog#aboutDialog QToolButton:hover { background: #E6F1EB; }
            QDialog#aboutDialog QToolButton:focus { border: 2px solid #0F766E; }
            QDialog#aboutDialog QPushButton {
                background: #0F766E; color: white; border: 1px solid #0F766E;
                border-radius: 8px; padding: 6px 24px; min-height: 28px;
                font-weight: 600;
            }
            QDialog#aboutDialog QPushButton:hover { background: #0D9488; }
            QDialog#aboutDialog QPushButton:focus { border: 2px solid #173C36; }
            QDialog#aboutDialog QPushButton:disabled {
                background: #E3EBE6; color: #65796E; border-color: #D6E3DD;
            }
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        panel = QFrame()
        panel.setObjectName("aboutPanel")
        root.addWidget(panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(24, 14, 18, 14)
        title = QLabel("关于")
        title.setObjectName("aboutTitle")
        header.addWidget(title, 1)
        close_icon = QToolButton()
        close_icon.setIcon(toolbar_icon("win-close", "#536762"))
        close_icon.setIconSize(QSize(20, 20))
        close_icon.setToolTip("关闭")
        close_icon.setAccessibleName("关闭关于窗口")
        close_icon.clicked.connect(self.reject)
        header.addWidget(close_icon)
        layout.addLayout(header)
        divider = QFrame()
        divider.setObjectName("aboutDivider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        content = QVBoxLayout(body)
        content.setContentsMargins(24, 18, 24, 18)
        content.setSpacing(14)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        brand = QHBoxLayout()
        brand.setSpacing(14)
        logo = QLabel()
        logo.setPixmap(brand_icon().pixmap(QSize(56, 56), self.devicePixelRatioF()))
        brand.addWidget(logo)
        identity = QVBoxLayout()
        identity.setSpacing(4)
        name = QLabel(f"{APP_NAME} · {APP_SLUG}")
        name.setObjectName("aboutName")
        identity.addWidget(name)
        tagline = QLabel("让散落的页面有处可归。")
        tagline.setObjectName("aboutMuted")
        identity.addWidget(tagline)
        brand.addLayout(identity, 1)
        content.addLayout(brand)

        info = QFrame()
        info.setObjectName("aboutCard")
        details = QGridLayout(info)
        details.setContentsMargins(16, 12, 16, 12)
        details.setHorizontalSpacing(20)
        details.setVerticalSpacing(10)
        details.setColumnStretch(1, 1)
        for row, caption in enumerate(("当前版本", "项目地址")):
            label = QLabel(caption)
            label.setObjectName("aboutMuted")
            details.addWidget(label, row, 0)
        version = QLabel(f"v{__version__}")
        version.setObjectName("aboutVersion")
        details.addWidget(version, 0, 1, Qt.AlignmentFlag.AlignRight)
        project = QLabel(
            f'<a href="{APP_PROJECT_URL}" style="color: #0F766E; text-decoration: none;">'
            f'{APP_PROJECT_URL.removeprefix("https://")} ↗</a>'
        )
        project.setObjectName("aboutProjectLink")
        project.setAlignment(Qt.AlignmentFlag.AlignRight)
        project.setWordWrap(True)
        project.setOpenExternalLinks(True)
        project.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        project.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        project.setToolTip(APP_PROJECT_URL)
        project.setAccessibleName(f"打开{APP_NAME} 项目地址")
        details.addWidget(project, 1, 1)
        content.addWidget(info)

        updates_card = QFrame()
        updates_card.setObjectName("aboutCard")
        updates_layout = QVBoxLayout(updates_card)
        updates_layout.setContentsMargins(16, 12, 16, 12)
        updates_layout.setSpacing(10)
        self.update_status = QLabel()
        self.update_status.setObjectName("aboutUpdateStatus")
        self.update_status.setTextFormat(Qt.TextFormat.PlainText)
        self.update_status.setWordWrap(True)
        updates_layout.addWidget(self.update_status)
        update_buttons = QHBoxLayout()
        self.check_update_button = QPushButton("检查更新")
        self.check_update_button.setObjectName("aboutCheckUpdates")
        self.check_update_button.clicked.connect(self.updates.check)
        update_buttons.addWidget(self.check_update_button)
        self.get_update_button = QPushButton("打开发布页")
        self.get_update_button.setObjectName("aboutGetUpdate")
        self.get_update_button.clicked.connect(self.updates.open_release_page)
        update_buttons.addWidget(self.get_update_button)
        self.lanzou_button = QPushButton("蓝奏云 ↗")
        self.lanzou_button.setObjectName("aboutLanzouDownload")
        self.lanzou_button.setIcon(toolbar_icon("cloud-download", "#FFFFFF"))
        self.lanzou_button.setIconSize(QSize(20, 20))
        self.lanzou_button.setToolTip(f"打开蓝奏云下载，自动复制密码 {LANZOU_PASSWORD}")
        self.lanzou_button.setAccessibleName("蓝奏云下载，复制密码并打开网盘")
        self.lanzou_button.clicked.connect(self._open_lanzou_download)
        update_buttons.addWidget(self.lanzou_button)
        update_buttons.addStretch()
        updates_layout.addLayout(update_buttons)
        self.lanzou_status = QLabel(f"蓝奏云密码：{LANZOU_PASSWORD}（点击“蓝奏云”自动复制）")
        self.lanzou_status.setObjectName("aboutMuted")
        self.lanzou_status.setTextFormat(Qt.TextFormat.PlainText)
        self.lanzou_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                                  | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.lanzou_status.setWordWrap(True)
        updates_layout.addWidget(self.lanzou_status)
        self.auto_updates = QCheckBox("自动检查更新（每天最多一次）")
        self.auto_updates.setObjectName("aboutAutoUpdates")
        self.auto_updates.setChecked(self.updates.automatic)
        self.auto_updates.toggled.connect(self.updates.set_automatic)
        updates_layout.addWidget(self.auto_updates)
        update_note = QLabel("自动检查更新默认开启，可在上方关闭。仅向 GitHub 查询版本，不上传文档。下载后请关闭软件，再运行安装包。")
        update_note.setObjectName("aboutMuted")
        update_note.setWordWrap(True)
        updates_layout.addWidget(update_note)
        content.addWidget(updates_card)
        self.updates.changed.connect(self._refresh_updates)
        self._refresh_updates()

        support = QVBoxLayout()
        support.setSpacing(8)
        heading = QLabel("♡  赞赏项目")
        heading.setObjectName("aboutSection")
        support.addWidget(heading)
        note = QLabel(f"如果{APP_NAME} 帮到了你，欢迎支持项目的持续维护。赞赏完全自愿。")
        note.setWordWrap(True)
        note.setObjectName("aboutMuted")
        support.addWidget(note)
        content.addLayout(support)

        donation = QFrame()
        donation.setObjectName("aboutCard")
        donation.setFixedWidth(300)
        donation_layout = QVBoxLayout(donation)
        donation_layout.setContentsMargins(20, 14, 20, 14)
        donation_layout.setSpacing(10)
        donation_layout.addWidget(QLabel("微信赞赏"), 0, Qt.AlignmentFlag.AlignHCenter)
        code = QLabel()
        code.setObjectName("aboutDonationCode")
        code.setAccessibleName("微信赞赏码，使用微信扫一扫")
        pixmap = QPixmap(str(Path(__file__).resolve().parent / "assets" / "donation.png"))
        if not pixmap.isNull():
            ratio = self.devicePixelRatioF()
            pixmap = pixmap.scaled(
                QSize(round(256 * ratio), round(256 * ratio)),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
            )
            pixmap.setDevicePixelRatio(ratio)
        code.setPixmap(pixmap)
        donation_layout.addWidget(code, 0, Qt.AlignmentFlag.AlignHCenter)
        scan_hint = QLabel("使用微信扫一扫")
        scan_hint.setObjectName("aboutMuted")
        donation_layout.addWidget(scan_hint, 0, Qt.AlignmentFlag.AlignHCenter)
        content.addWidget(donation, 0, Qt.AlignmentFlag.AlignHCenter)
        # 私人赞赏图片不随源码发布；缺少图片时隐藏整块，不显示空白卡片。
        if pixmap.isNull():
            heading.hide()
            note.hide()
            donation.hide()
            support.setSpacing(0)
        content.addStretch()

        divider = QFrame()
        divider.setObjectName("aboutDivider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)
        footer = QHBoxLayout()
        footer.setContentsMargins(24, 14, 24, 14)
        footer.addStretch()
        close_button = QPushButton("关闭")
        close_button.setObjectName("aboutClose")
        close_button.setDefault(True)
        close_button.clicked.connect(self.accept)
        footer.addWidget(close_button)
        layout.addLayout(footer)
        close_button.setFocus()
        height = 960 if not pixmap.isNull() else 610
        self.resize(620, min(height, self.screen().availableGeometry().height() - 48))

    def _refresh_updates(self) -> None:
        message = self.updates.message
        if self.updates.update_available and not self.updates.busy and not message.startswith("发现新版本"):
            message = f"可获取新版本 {self.updates.latest_tag}。\n{message}"
        self.update_status.setText(message)
        self.check_update_button.setEnabled(not self.updates.busy)
        self.check_update_button.setText("正在检查…" if self.updates.busy else "检查更新")
        self.get_update_button.setText("GitHub 下载 ↗" if self.updates.update_available else "GitHub 发布页 ↗")

    def _open_lanzou_download(self) -> None:
        clipboard = QApplication.clipboard()
        clipboard.setText(LANZOU_PASSWORD)
        if clipboard.text() == LANZOU_PASSWORD:
            message = f"密码 {LANZOU_PASSWORD} 已复制，可在蓝奏云网页中粘贴。"
        else:
            message = f"未能复制密码，请在蓝奏云网页中手动输入：{LANZOU_PASSWORD}。"
        self.lanzou_status.setText(message)
        if not QDesktopServices.openUrl(QUrl(LANZOU_URL)):
            self.lanzou_status.setText(f"{message}\n无法打开浏览器，请手动访问：{LANZOU_URL}")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 68:
            handle = self.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)
