"""In-app release notes; remote text never loads HTML, images or local resources."""
from __future__ import annotations

from datetime import datetime
import re

from PyQt6.QtCore import QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from app import APP_IS_PRERELEASE, APP_NAME, __version__
from app.icons import brand_icon, toolbar_icon
from app.runtime import portable_root
from app.themes import current_theme, set_theme_style, theme_manager
from app.updates import UpdateChecker

LANZOU_URL = "https://leezfy.lanzouq.com/b02qtfd77g"
LANZOU_PASSWORD = "atxq"


def open_lanzou_download() -> str:
    clipboard = QApplication.clipboard()
    clipboard.setText(LANZOU_PASSWORD)
    if clipboard.text() == LANZOU_PASSWORD:
        message = f"密码 {LANZOU_PASSWORD} 已复制，可在蓝奏云网页中粘贴。"
    else:
        message = f"未能复制密码，请手动输入：{LANZOU_PASSWORD}。"
    if not QDesktopServices.openUrl(QUrl(LANZOU_URL)):
        message += f"\n无法打开浏览器，请手动访问：{LANZOU_URL}"
    return message


def release_entries(notes: str) -> list[tuple[str, str]]:
    # Keep unfamiliar Markdown and continuation lines readable instead of dropping them.
    blocks = re.split(r"\n(?=\s*(?:[-*+]\s+|#{1,6}\s+))|\n\s*\n", notes.strip(), maxsplit=199)
    entries = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        match = re.fullmatch(r"(?:[-*+]\s+)?(新增|优化|修复|说明)[：:]\s*(.*)", block, re.S)
        if match:
            entries.append((match[1], match[2]))
        else:
            entries.append(("", re.sub(r"^(?:[-*+]\s+|#{1,6}\s+)", "", block)))
    return entries


def show_update_dialog(parent, updates: UpdateChecker) -> None:
    dialog = UpdateDialog(updates, parent)
    if not updates.release_notes and not updates.busy:
        QTimer.singleShot(0, updates.check)
    dialog.exec()
    dialog.deleteLater()


class UpdateDialog(QDialog):
    def __init__(self, updates: UpdateChecker, parent=None):
        super().__init__(parent)
        self.updates = updates
        self._rendered_notes = None
        self.setObjectName("releaseDialog")
        self.setWindowTitle(f"{APP_NAME} · 版本更新")
        self.setWindowIcon(brand_icon())
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # Use the application's source palette so open dialogs follow theme switches.
        colors = dict(
            paper="#FBFCFA", ink="#173C36", muted="#65796E", edge="#DCE5DF",
            soft="#F0F4EF", accent="#0F766E", hover="#0D9488", on_accent="#FFFFFF", error="#B91C1C",
        )
        set_theme_style(self, f"""
            QDialog#releaseDialog, QDialog#releaseDialog QWidget {{
                background: transparent; color: {colors['ink']}; border: none;
                font-family: "Segoe UI", "Microsoft YaHei UI"; font-size: 14px;
            }}
            QDialog#releaseDialog QFrame#releasePanel {{
                background: {colors['paper']}; border: 1px solid {colors['edge']}; border-radius: 16px;
            }}
            QDialog#releaseDialog QLabel#releaseTitle {{ font-size: 24px; font-weight: 600; }}
            QDialog#releaseDialog QLabel#releaseState {{ color: {colors['accent']}; font-size: 12px; }}
            QDialog#releaseDialog QLabel[muted="true"] {{ color: {colors['muted']}; font-size: 12px; }}
            QDialog#releaseDialog QLabel#releaseLatest {{ font-size: 14px; font-weight: 600; }}
            QDialog#releaseDialog QLabel#releaseSection {{ font-size: 15px; font-weight: 600; }}
            QDialog#releaseDialog QLabel#releaseEntry {{
                color: {colors['ink']}; font-size: 13px; font-weight: 400;
            }}
            QDialog#releaseDialog QLabel#releaseBadge {{
                border-radius: 5px; font-size: 12px; font-weight: 500;
            }}
            QDialog#releaseDialog QLabel#releaseError {{ color: {colors['error']}; font-size: 12px; }}
            QDialog#releaseDialog QFrame#releaseDivider {{ background: {colors['edge']}; }}
            QDialog#releaseDialog QFrame#releasePrecautions {{
                background: transparent; border-top: 1px solid {colors['edge']};
            }}
            QDialog#releaseDialog QScrollBar:vertical {{ background: transparent; width: 6px; margin: 4px 0; }}
            QDialog#releaseDialog QScrollBar::handle:vertical {{
                background: {colors['edge']}; border-radius: 3px; min-height: 28px;
            }}
            QDialog#releaseDialog QScrollBar::add-line:vertical,
            QDialog#releaseDialog QScrollBar::sub-line:vertical {{ height: 0; }}
            QDialog#releaseDialog QScrollBar::add-page:vertical,
            QDialog#releaseDialog QScrollBar::sub-page:vertical {{ background: transparent; }}
            QDialog#releaseDialog QPushButton {{
                background: {colors['paper']}; border: 1px solid {colors['edge']};
                border-radius: 8px; padding: 7px 17px; min-height: 24px; font-size: 13px; font-weight: 600;
            }}
            QDialog#releaseDialog QPushButton:hover {{ background: {colors['soft']}; }}
            QDialog#releaseDialog QPushButton:pressed {{ border-color: {colors['accent']}; }}
            QDialog#releaseDialog QPushButton#releaseDownload {{
                background: {colors['accent']}; color: {colors['on_accent']}; border-color: {colors['accent']};
            }}
            QDialog#releaseDialog QPushButton#releaseDownload:hover {{ background: {colors['hover']}; }}
            QDialog#releaseDialog QPushButton#releaseLater {{
                color: {colors['muted']}; border-color: transparent; background: transparent; font-weight: 400;
            }}
            QDialog#releaseDialog QToolButton {{
                color: {colors['accent']}; padding: 5px 7px; min-height: 24px; border-radius: 6px; font-size: 12px;
            }}
            QDialog#releaseDialog QToolButton:hover {{ background: {colors['soft']}; }}
            QDialog#releaseDialog QToolButton:disabled {{ color: {colors['muted']}; }}
            QDialog#releaseDialog QToolButton#releasePrecautionsToggle {{ padding: 0; }}
            QDialog#releaseDialog QToolButton:focus,
            QDialog#releaseDialog QPushButton:focus {{ border: 2px solid {colors['accent']}; }}
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        panel = QFrame()
        panel.setObjectName("releasePanel")
        root.addWidget(panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(28, 16, 28, 22)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setSpacing(9)
        logo = QLabel()
        logo.setPixmap(brand_icon().pixmap(QSize(24, 24), self.devicePixelRatioF()))
        header.addWidget(logo)
        header.addWidget(self._label(APP_NAME, muted=True), 1)
        self.state_label = self._label("", "releaseState")
        header.addWidget(self.state_label)
        close = QToolButton()
        close.setIcon(toolbar_icon("win-close", colors["muted"]))
        close.setIconSize(QSize(18, 18))
        close.setToolTip("关闭")
        close.setAccessibleName("关闭版本更新窗口")
        close.clicked.connect(self.reject)
        header.addWidget(close)
        layout.addLayout(header)
        layout.addSpacing(17)
        heading = QHBoxLayout()
        self.title_label = self._label("正式版更新" if APP_IS_PRERELEASE else "更新内容", "releaseTitle")
        heading.addWidget(self.title_label, 1)
        self.date_label = self._label("", muted=True)
        heading.addWidget(self.date_label, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(heading)
        layout.addSpacing(7)
        versions = QHBoxLayout()
        versions.setSpacing(12)
        self.current_label = self._label(f"当前 v{__version__}", muted=True)
        versions.addWidget(self.current_label)
        self.version_arrow = self._label("→", muted=True)
        versions.addWidget(self.version_arrow)
        self.latest_label = self._label("", "releaseLatest")
        versions.addWidget(self.latest_label)
        versions.addStretch()
        self.refresh_button = QToolButton()
        self.refresh_button.clicked.connect(self.updates.check)
        versions.addWidget(self.refresh_button)
        full_notes = QToolButton()
        full_notes.setText("完整发布说明")
        full_notes.clicked.connect(self.updates.open_release_page)
        versions.addWidget(full_notes)
        layout.addLayout(versions)
        layout.addSpacing(12)
        self._divider(layout)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 18, 10, 18)
        body_layout.setSpacing(21)
        entries_widget = QWidget()
        self.entries_layout = QVBoxLayout(entries_widget)
        self.entries_layout.setContentsMargins(0, 0, 0, 0)
        self.entries_layout.setSpacing(22)
        body_layout.addWidget(entries_widget)
        self.precautions = QFrame()
        self.precautions.setObjectName("releasePrecautions")
        precautions_layout = QVBoxLayout(self.precautions)
        precautions_layout.setContentsMargins(0, 10, 0, 0)
        precautions_layout.setSpacing(6)
        self.precautions_button = QToolButton()
        self.precautions_button.setObjectName("releasePrecautionsToggle")
        self.precautions_button.setText("使用前须知")
        self.precautions_button.setCheckable(True)
        self.precautions_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.precautions_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.precautions_button.toggled.connect(self._toggle_precautions)
        precautions_layout.addWidget(self.precautions_button)
        self.precautions_preview = self._label("", muted=True)
        precautions_layout.addWidget(self.precautions_preview)
        self.precautions_body = QWidget()
        self.precautions_layout = QVBoxLayout(self.precautions_body)
        self.precautions_layout.setContentsMargins(0, 0, 0, 0)
        self.precautions_layout.setSpacing(13)
        precautions_layout.addWidget(self.precautions_body)
        self._toggle_precautions(False)
        body_layout.addWidget(self.precautions)
        body_layout.addStretch()
        self.scroll.setWidget(body)
        layout.addWidget(self.scroll, 1)

        self.empty = QWidget()
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setContentsMargins(20, 36, 20, 36)
        empty_layout.setSpacing(12)
        empty_layout.addStretch()
        self.empty_title = self._label("", "releaseSection")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_title)
        self.empty_detail = self._label("", muted=True)
        self.empty_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_detail)
        empty_layout.addStretch()
        layout.addWidget(self.empty, 1)

        self.error_label = self._label("", "releaseError")
        layout.addWidget(self.error_label)
        layout.addSpacing(11)
        self._divider(layout)
        layout.addSpacing(14)
        portable = portable_root() is not None
        footer_note = ("绿色版 · 下载 ZIP，退出软件后解压到新文件夹。" if portable
                       else "安装版 · 下载完成后，请关闭软件再运行安装包。")
        layout.addWidget(self._label(footer_note, muted=True))
        layout.addSpacing(12)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        later = QPushButton("稍后再说")
        later.setObjectName("releaseLater")
        later.clicked.connect(self.reject)
        actions.addWidget(later)
        actions.addStretch()
        github = QPushButton("GitHub 下载")
        github.setAccessibleName("打开 GitHub 绿色版发布页" if portable else "打开 GitHub 安装版发布页")
        github.clicked.connect(self.updates.open_release_page)
        actions.addWidget(github)
        self.lanzou_button = QPushButton("蓝奏云下载")
        self.lanzou_button.setObjectName("releaseDownload")
        self.lanzou_button.setToolTip(f"打开蓝奏云，自动复制密码 {LANZOU_PASSWORD}")
        self.lanzou_button.clicked.connect(self._open_lanzou)
        actions.addWidget(self.lanzou_button)
        layout.addLayout(actions)
        self.lanzou_status = self._label("", muted=True)
        layout.addWidget(self.lanzou_status)
        self.lanzou_status.hide()
        theme_manager().changed.connect(self._refresh_badge_colors)
        self.updates.changed.connect(self._refresh)
        self._refresh()
        bounds = self.screen().availableGeometry()
        self.resize(min(720, bounds.width() - 40), min(720, bounds.height() - 48))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    @staticmethod
    def _label(text: str, name: str = "", *, muted=False) -> QLabel:
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if name:
            label.setObjectName(name)
        if muted:
            label.setProperty("muted", True)
        return label

    @staticmethod
    def _divider(layout) -> None:
        line = QFrame()
        line.setObjectName("releaseDivider")
        line.setFixedHeight(1)
        layout.addWidget(line)

    def _render_notes(self, notes: str) -> None:
        for layout in (self.entries_layout, self.precautions_layout):
            while layout.count():
                item = layout.takeAt(0)
                if item.widget() is not None:
                    item.widget().deleteLater()
        precautions = []
        groups = {kind: [] for kind in ("新增", "优化", "修复")}
        for kind, text in release_entries(notes):
            if kind == "说明":
                precautions.append(text)
                self.precautions_layout.addWidget(self._label(text, "releaseEntry"))
            elif kind in groups:
                groups[kind].append(text)
            else:
                self.entries_layout.addWidget(self._label(text, "releaseEntry"))
        for kind, texts in groups.items():
            if not texts:
                continue
            row = QWidget()
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(9)
            badge = self._label(kind, "releaseBadge")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedSize(50, 28)
            row_layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignLeft)
            for text in texts:
                label = self._label(text, "releaseEntry")
                label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                row_layout.addWidget(label)
            self.entries_layout.addWidget(row)
        self._refresh_badge_colors()
        self.precautions.setVisible(bool(precautions))
        if precautions:
            first_sentence = precautions[0].split("。", 1)[0]
            preview = first_sentence[:110] + ("…" if len(first_sentence) > 110 else "。")
            self.precautions_preview.setText(preview)
        self.precautions_button.setChecked(False)
        self._toggle_precautions(False)
        self.scroll.verticalScrollBar().setValue(0)

    def _refresh_badge_colors(self, *_args) -> None:
        dark = QColor(current_theme().colors["surface"]).lightness() < 128
        colors = {
            "新增": ("#B3C7BE", "#2E3D37"),
            "优化": ("#BCC6D8", "#313B4B"),
            "修复": ("#CEBDB3", "#403731"),
        } if dark else {
            "新增": ("#4E6D63", "#EDF3F0"),
            "优化": ("#5B6C88", "#EEF1F6"),
            "修复": ("#786258", "#F4EFEC"),
        }
        for badge in self.findChildren(QLabel, "releaseBadge"):
            foreground, background = colors[badge.text()]
            # Category colors carry meaning and stay distinct across application themes.
            badge.setStyleSheet(f"QLabel#releaseBadge {{ color: {foreground}; background: {background}; }}")

    def _toggle_precautions(self, expanded: bool) -> None:
        self.precautions_button.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.precautions_button.setAccessibleDescription("收起使用说明" if expanded else "展开完整使用说明")
        self.precautions_body.setVisible(expanded)
        self.precautions_preview.setVisible(not expanded)

    def _refresh(self) -> None:
        updates = self.updates
        has_notes = bool(updates.release_notes)
        if updates.busy:
            state = "正在检查更新…"
        elif updates.error:
            state = "当前显示已保存的更新说明" if has_notes else "更新内容尚未获取"
        elif updates.update_available:
            state = "有新版本可下载"
        elif APP_IS_PRERELEASE:
            state = "预览版：仅检查正式版更新"
        elif updates.latest_tag:
            state = "当前已是最新版本" if updates.last_check_succeeded else "已保存的发布说明"
        else:
            state = "查看正式版更新"
        self.state_label.setText(state)
        self.current_label.setVisible(updates.update_available)
        self.version_arrow.setVisible(updates.update_available)
        self.latest_label.setText(("新版 " if updates.update_available else "") + updates.latest_tag
                                 if updates.latest_tag else f"当前 v{__version__}")
        try:
            date = datetime.fromisoformat(updates.published_at.replace("Z", "+00:00"))
            self.date_label.setText(date.astimezone().strftime("%Y-%m-%d"))
            self.date_label.show()
        except (ValueError, OverflowError, OSError):
            self.date_label.hide()
        self.refresh_button.setEnabled(not updates.busy)
        self.refresh_button.setText("正在获取…" if updates.busy else
                                    "检查更新" if has_notes and not updates.error else "重新获取")
        if self._rendered_notes != updates.release_notes:
            self._render_notes(updates.release_notes)
            self._rendered_notes = updates.release_notes
        self.scroll.setVisible(has_notes)
        self.empty.setVisible(not has_notes)
        if updates.busy:
            self.empty_title.setText("正在获取更新内容")
            self.empty_detail.setText("正在读取 GitHub 发布说明，请稍候。")
        elif updates.error:
            self.empty_title.setText("暂时无法获取更新内容")
            self.empty_detail.setText(updates.error)
        else:
            self.empty_title.setText("暂未获取到更新说明")
            self.empty_detail.setText("可点击“重新获取”，或打开“完整发布说明”查看。")
        self.error_label.setText(updates.error + "\n当前显示已保存的同版更新说明。" if updates.error and has_notes else "")
        self.error_label.setVisible(bool(updates.error and has_notes))

    def _open_lanzou(self) -> None:
        self.lanzou_status.setText(open_lanzou_download())
        self.lanzou_status.show()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 62:
            handle = self.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)
