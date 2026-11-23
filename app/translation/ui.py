"""Native PyQt6 translation UI matching ZhiXu PDF's visual system and theme tokens."""
from __future__ import annotations

from dataclasses import replace
from html import escape
from itertools import groupby
from math import ceil
from threading import Event
from typing import Callable
from weakref import WeakKeyDictionary

from PyQt6.QtCore import (
    QElapsedTimer,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAbstractTextDocumentLayout,
    QColor,
    QDesktopServices,
    QFont,
    QPainter,
    QPen,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextOption,
)
from PyQt6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsItem,
    QGraphicsRectItem,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedLayout,
    QStyle,
    QStyleOptionComboBox,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.i18n import tr
from app.icons import toolbar_icon
from app.output import OperationCancelled
from app.pagetoolsui import ClearComboBox
from app.themes import set_theme_style, theme_manager, ui_color
from app.translation.client import OpenAICompatibleClient, selection_missing_chinese
from app.translation.reflow import ReflowedPage
from app.translation.config import (
    DEEPSEEK_MODELS,
    DEFAULT_PROVIDER,
    PRESETS,
    PRESETS_BY_KEY,
    TARGET_LANGUAGES,
    TARGET_LANGUAGE_MAP,
    TranslationSettings,
    endpoint_origin,
    load_settings,
    save_settings,
    validate_api_key,
    validate_endpoint,
)
from app.translation.extractor import (
    CachedPageTranslation,
    PageExtraction,
    PageSegment,
    RegionExtraction,
    TranslationCache,
    extract_page_segments,
    extract_region_for_translation,
    group_segments_into_batches,
    ocr_page_for_translation,
    selection_reference,
)


PANEL_STYLE = """
QFrame#translationSidebar {
    background: #FFFFFF;
    border: 1px solid #DFE8E3;
    border-radius: 10px;
}
QFrame#translationSidebar QLabel {
    color: #1E293B;
    background: transparent;
}
QFrame#translationSidebar QLabel#tpTitle {
    font-size: 14px;
    font-weight: 600;
    color: #1E293B;
}
QFrame#translationSidebar QLabel#tpHint {
    color: #526B65;
    font-size: 12px;
}
QFrame#translationSidebar QLabel#tpEmptyTitle {
    color: #1E293B;
    font-size: 13px;
    font-weight: 600;
}
QFrame#translationSidebar QToolButton#tpEmptyIcon {
    background: #EEF3F0;
    border: none;
    border-radius: 22px;
    min-width: 44px;
    min-height: 44px;
    padding: 0;
}
QFrame#translationSidebar QLabel#tpEmptyTag {
    background: #F7FAF9;
    border: 1px solid #E5EEEC;
    border-radius: 5px;
    color: #526B65;
    font-size: 11px;
    padding: 3px 7px;
}
QFrame#translationSidebar QToolButton#tpEnginePill {
    min-height: 24px;
    padding: 2px 8px;
    border-radius: 6px;
    background: #F7FAF9;
    border: 1px solid #E5EEEC;
    color: #526B65;
    font-size: 11px;
}
QFrame#translationSidebar QToolButton#tpEnginePill:hover {
    background: #EAF4EF;
    border-color: #99B2AC;
    color: #1E293B;
}
QFrame#translationSidebar QToolButton[iconAction="true"] {
    min-width: 26px;
    min-height: 26px;
    padding: 0;
    border: 1px solid transparent;
    border-radius: 6px;
    background: transparent;
}
QFrame#translationSidebar QToolButton[iconAction="true"]:hover {
    background: #F0F6F3;
    border-color: #E2EEEC;
}
QFrame#translationSidebar QToolButton[iconAction="true"]:checked {
    background: #E1EEE7;
    border-color: #D5E2DE;
}
QFrame#translationSidebar QToolButton:focus {
    border: 1.5px solid #0F766E;
}
QFrame#translationSidebar QToolButton[iconAction="true"]::menu-indicator {
    image: none;
}
QFrame#translationSidebar QPushButton {
    min-height: 22px;
    padding: 4px 10px;
    border-radius: 6px;
    font-size: 13px;
}
QFrame#translationSidebar QPushButton[translationAction="true"] {
    min-height: 22px;
    padding: 4px 10px;
    border-radius: 6px;
    font-size: 12px;
}
QFrame#translationSidebar QPushButton[secondaryTool="true"] {
    background: #F7FAF9;
    border: 1px solid #E5EEEC;
    color: #1E293B;
    font-size: 12px;
    min-height: 20px;
    padding: 4px 8px;
}
QFrame#translationSidebar QPushButton[secondaryTool="true"]:hover {
    background: #EAF4EF;
    border-color: #99B2AC;
}
QFrame#translationSidebar QPushButton[secondaryTool="true"]:disabled {
    background: #F7FAF9;
    border-color: #E5EEEC;
    color: #84958F;
    font-weight: 400;
}
QFrame#translationSidebar QPushButton[primary="true"] {
    background: #0F766E;
    color: #FFFFFF;
    border: 1px solid #0F766E;
    font-weight: 600;
    font-size: 13px;
}
QFrame#translationSidebar QPushButton[primary="true"]:hover {
    background: #0D9488;
    border-color: #0D9488;
}
QFrame#translationSidebar QPushButton[primary="true"]:pressed {
    background: #115E59;
    border-color: #115E59;
}
QFrame#translationSidebar QPushButton[primary="true"]:disabled {
    background: #E8F0ED;
    border-color: #D5E2DE;
    color: #84958F;
    font-weight: 500;
}
QFrame#translationSidebar QPushButton[secondaryTool="true"][primary="true"] {
    background: #EAF4EF;
    border: 1px solid #99B2AC;
    color: #134E4A;
    font-weight: 600;
    font-size: 12px;
}
QFrame#translationSidebar QPushButton[secondaryTool="true"][primary="true"]:hover {
    background: #E4F2ED;
    border-color: #0F766E;
}
QFrame#translationSidebar QPushButton[secondaryTool="true"][primary="true"]:disabled {
    background: #F7FAF9;
    border-color: #E5EEEC;
    color: #84958F;
    font-weight: 400;
}
QFrame#translationSidebar QPushButton[secondaryTool="true"]:checked {
    background: #E4F2ED;
    color: #0F766E;
    border: 1px solid #0F766E;
    font-weight: 600;
    font-size: 12px;
}
QFrame#translationSidebar QComboBox {
    min-height: 22px;
    padding: 3px 24px 3px 9px;
    border-radius: 6px;
    font-size: 12px;
}
QFrame#tpViewModes {
    background: #EEF3F0;
    border: none;
    border-radius: 7px;
}
QFrame#tpViewModes QPushButton {
    min-height: 24px;
    padding: 2px 8px;
    border: 1px solid transparent;
    border-radius: 5px;
    background: transparent;
    color: #526B65;
    font-size: 12px;
}
QFrame#tpViewModes QPushButton:hover {
    color: #1E293B;
    background: #E6F1EB;
}
QFrame#tpViewModes QPushButton:checked {
    background: #FFFFFF;
    border: 1px solid #D5E2DE;
    color: #1E293B;
    font-weight: 600;
}
QFrame#tpViewModes QPushButton:focus {
    border-color: #0F766E;
}
QScrollArea#tpScroll, QWidget#tpViewport, QWidget#tpContent, QWidget#tpEmptyBox {
    background: transparent;
    border: none;
}
QFrame#tpDivider {
    background: #E5EEEC;
    border: none;
}
QFrame#tpNoticeBox {
    background: #FFFBEB;
    border: 1px solid #FDE68A;
    border-radius: 8px;
}
QFrame#tpNoticeBox QLabel {
    color: #92400E;
    font-size: 12px;
}
QFrame#tpSegmentCard {
    background: transparent;
    border: 1px solid transparent;
    border-bottom: 1px solid #E5EEEC;
    border-radius: 6px;
}
QFrame#tpSegmentCard:hover {
    background: #F7FAF9;
    border-color: #E5EEEC;
}
QFrame#tpSegmentCard[linked="true"] {
    background: #E4F2ED;
    border-color: #99B2AC;
}
QFrame#tpSegmentCard QLabel#tpSegMeta {
    color: #526B65;
    font-size: 11px;
    font-weight: 600;
}
QFrame#tpSegmentCard QLabel#tpSegSrc {
    color: #526B65;
    font-size: 12px;
}
QFrame#tpSegmentCard QLabel#tpSegDst {
    color: #1E293B;
    font-size: 13px;
}
QFrame#tpSegmentCard QLabel#tpSegDst[heading="true"] {
    font-weight: 600;
}
QFrame#tpFooterWrap {
    background: transparent;
    border: none;
    border-top: 1px solid #E5EEEC;
}
QProgressBar#tpProgress {
    border: none;
    background: #DDEBE5;
    border-radius: 2px;
}
QProgressBar#tpProgress::chunk {
    background: #0F766E;
    border-radius: 2px;
}
"""

FLOATING_STYLE = """
QFrame#translationMiniBar {
    background: #FFFFFF;
    border: 1px solid #99B2AC;
    border-radius: 8px;
}
QFrame#translationMiniBar QPushButton {
    min-height: 24px;
    padding: 2px 9px;
    border-radius: 6px;
    border: 1px solid transparent;
    background: transparent;
    color: #134E4A;
    font-size: 12px;
}
QFrame#translationMiniBar QPushButton:hover {
    background: #F0FDFA;
}
QFrame#translationMiniBar QPushButton[primary="true"] {
    background: #0F766E;
    color: #FFFFFF;
    border-color: #0F766E;
    font-weight: 600;
}
QFrame#translationMiniBar QPushButton[primary="true"]:hover {
    background: #0D9488;
}
QFrame#translationBubble {
    background: #FFFFFF;
    border: 1px solid #99B2AC;
    border-radius: 10px;
}
QFrame#translationBubble QLabel#bubbleTitle {
    color: #134E4A;
    font-size: 12px;
    font-weight: 600;
}
QFrame#translationBubble QLabel#bubbleHint {
    color: #4D6B67;
    font-size: 11px;
}
QFrame#translationBubble QPlainTextEdit, QFrame#translationBubble QTextEdit {
    background: #F7FAF9;
    color: #134E4A;
    border: 1px solid #D7E8E4;
    border-radius: 7px;
    padding: 6px;
}
QFrame#translationBubble QPlainTextEdit, QFrame#translationBubble QTextEdit[floating="false"] {
    font-size: 13px;
}
QFrame#translationBubble QToolButton[iconAction="true"] {
    min-width: 24px;
    min-height: 24px;
    padding: 0;
    border: 1px solid transparent;
    border-radius: 6px;
    background: transparent;
}
QFrame#translationBubble QToolButton[iconAction="true"]:hover {
    background: #F0FDFA;
    border-color: #D7E8E4;
}
QFrame#translationBubble QToolButton[iconAction="true"]:focus {
    border: 1px solid #0F766E;
}
QFrame#translationBubble QPushButton {
    min-height: 26px;
    padding: 3px 9px;
    border-radius: 7px;
    font-size: 12px;
}
QFrame#translationBubble QTextEdit[floating="true"] {
    background: transparent;
    border: none;
    padding: 0px;
}
QFrame#translationBubble QWidget#bubbleFoot {
    background: transparent;
}
"""

DIALOG_STYLE = """
QDialog#translationSettings {
    background: #F3F6F5;
}
QDialog#translationSettings QLabel {
    color: #134E4A;
    background: transparent;
}
QDialog#translationSettings QLabel#dlgHint {
    color: #4D6B67;
    font-size: 12px;
}
QDialog#translationSettings QTabWidget::pane {
    border: 1px solid #D7E8E4; background: #FFFFFF; border-radius: 6px;
}
QDialog#translationSettings QTabBar::tab {
    background: transparent; color: #4D6B67; padding: 6px 12px; border: none;
}
QDialog#translationSettings QTabBar::tab:selected {
    color: #134E4A; border-bottom: 2px solid #0F766E; font-weight: 600;
}
QDialog#translationSettings QLineEdit {
    min-height: 30px;
    padding: 4px 10px;
    background: #FFFFFF;
    color: #134E4A;
    border: 1px solid #D7E8E4;
    border-radius: 8px;
    font-size: 13px;
}
QDialog#translationSettings QLineEdit:focus {
    border: 2px solid #0D9488;
}
QDialog#translationSettings QFrame#dlgPrivacyBox {
    background: #FFFBEB;
    border: 1px solid #FDE68A;
    border-radius: 9px;
}
QDialog#translationSettings QFrame#dlgPrivacyBox QLabel,
QDialog#translationSettings QFrame#dlgPrivacyBox QCheckBox {
    color: #92400E;
    background: transparent;
    font-size: 12px;
}
QDialog#translationSettings QFrame#dlgLocalBox {
    background: #F0FDFA;
    border: 1px solid #99B2AC;
    border-radius: 9px;
}
QDialog#translationSettings QFrame#dlgLocalBox QLabel {
    color: #134E4A;
    font-size: 12px;
}
QDialog#translationSettings QLabel#dlgFeedback[tone="ok"] {
    color: #059669;
    font-size: 12px;
}
QDialog#translationSettings QLabel#dlgFeedback[tone="error"] {
    color: #B91C1C;
    font-size: 12px;
}
"""


class _WorkerSignals(QObject):
    done = pyqtSignal(int, object, object)


class _ExtractionJob(QRunnable):
    def __init__(self, serial: int, fn: Callable, cancel: Event, signals: _WorkerSignals):
        super().__init__()
        self.serial = serial
        self.fn = fn
        self.cancel = cancel
        self.signals = signals

    def run(self) -> None:
        value = error = None
        try:
            if not self.cancel.is_set():
                value = self.fn()
        except OperationCancelled:
            self.cancel.set()
        except Exception as exc:
            error = exc
        self.signals.done.emit(self.serial, value, error)


def _near_selection(viewport: QRect, selection: QRect, size: QSize) -> QPoint | None:
    usable = viewport.adjusted(8, 8, -8, -8)
    if size.width() > usable.width() or size.height() > usable.height():
        return None
    gap = 8
    edge_x = usable.right() - size.width() + 1
    edge_y = usable.bottom() - size.height() + 1
    x = min(max(selection.left(), usable.left()), edge_x)
    y = min(max(selection.top(), usable.top()), edge_y)
    for point in (QPoint(x, selection.bottom() + gap),
                  QPoint(x, selection.top() - size.height() - gap),
                  QPoint(selection.right() + gap, y),
                  QPoint(selection.left() - size.width() - gap, y)):
        candidate = QRect(point, size)
        if usable.contains(candidate) and not candidate.intersects(selection):
            return point
    return None


def _set_line_height(document: QTextDocument, percentage: int) -> None:
    block_format = QTextBlockFormat()
    block_format.setLineHeight(percentage, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
    cursor = QTextCursor(document)
    cursor.select(QTextCursor.SelectionType.Document)
    cursor.mergeBlockFormat(block_format)


def _icon_button(parent: QWidget, icon: str, label: str, *, checkable: bool = False) -> QToolButton:
    button = QToolButton(parent)
    button.setProperty('iconAction', True)
    button.setFixedSize(28, 28)
    button.setIcon(toolbar_icon(icon, size=16))
    button.setIconSize(QSize(16, 16))
    button.setToolTip(label)
    button.setAccessibleName(label)
    button.setCheckable(checkable)
    button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    return button


class TranslationSettingsDialog(QDialog):
    """Configure DeepSeek (default) or any OpenAI-compatible translation service."""

    def __init__(self, config: TranslationSettings, client: OpenAICompatibleClient, parent=None):
        super().__init__(parent)
        self.setObjectName('translationSettings')
        self.setWindowTitle(tr('翻译设置'))
        self.setMinimumWidth(520)
        self.resize(560, min(620, self.screen().availableGeometry().height() - 48))
        set_theme_style(self, DIALOG_STYLE)

        self._initial = config
        self._client = client
        self._test_handle = None
        self._endpoint_origin = endpoint_origin(config.endpoint)
        self._clear_key_requested = False
        self.result_config: TranslationSettings | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        self.tabs = QTabWidget(self)
        outer.addWidget(self.tabs)
        service_scroll = QScrollArea(self.tabs)
        service_scroll.setFrameShape(QFrame.Shape.NoFrame)
        service_scroll.setWidgetResizable(True)
        service = QWidget()
        service_scroll.setWidget(service)
        self.tabs.addTab(service_scroll, tr('翻译服务'))
        layout = QVBoxLayout(service)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(10)

        # Provider preset
        provider_row = QHBoxLayout()
        provider_label = QLabel(tr('翻译服务'), self)
        provider_row.addWidget(provider_label)
        provider_row.addStretch()
        self.console_button = QPushButton(tr('获取密钥说明 ↗'), self)
        self.console_button.setAutoDefault(False)
        self.console_button.clicked.connect(self._open_console)
        provider_row.addWidget(self.console_button)
        layout.addLayout(provider_row)

        self.provider_combo = ClearComboBox(self)
        self.provider_combo.setAccessibleName(tr('选择翻译服务商'))
        for preset in PRESETS:
            self.provider_combo.addItem(tr(preset.name), preset.key)
        idx = self.provider_combo.findData(config.provider)
        self.provider_combo.setCurrentIndex(idx if idx >= 0 else 0)
        layout.addWidget(self.provider_combo)

        self.preset_desc = QLabel(self)
        self.preset_desc.setObjectName('dlgHint')
        self.preset_desc.setWordWrap(True)
        layout.addWidget(self.preset_desc)

        # Endpoint & Model
        layout.addWidget(QLabel(tr('接口地址'), self))
        self.endpoint_edit = QLineEdit(config.endpoint, self)
        self.endpoint_edit.setAccessibleName(tr('翻译接口地址'))
        layout.addWidget(self.endpoint_edit)

        model_lang_row = QHBoxLayout()
        model_col = QVBoxLayout()
        model_col.setSpacing(4)
        model_col.addWidget(QLabel(tr('模型名称'), self))
        self.deepseek_model_combo = ClearComboBox(self)
        self.deepseek_model_combo.setAccessibleName(tr('翻译模型名称'))
        self.deepseek_model_combo.addItems(DEEPSEEK_MODELS)
        self.deepseek_model_combo.setCurrentText(config.model)
        self.model_edit = QLineEdit(config.model, self)
        self.model_edit.setAccessibleName(tr('翻译模型名称'))
        self.model_edit.setPlaceholderText(tr('填入服务商提供的模型名称'))
        self.model_stack = QStackedLayout()
        self.model_stack.addWidget(self.deepseek_model_combo)
        self.model_stack.addWidget(self.model_edit)
        model_col.addLayout(self.model_stack)
        model_lang_row.addLayout(model_col, 3)

        lang_col = QVBoxLayout()
        lang_col.setSpacing(4)
        lang_col.addWidget(QLabel(tr('默认目标语言'), self))
        self.lang_combo = ClearComboBox(self)
        self.lang_combo.setAccessibleName(tr('默认目标语言'))
        for code, label, _ in TARGET_LANGUAGES:
            self.lang_combo.addItem(tr(label), code)
        lang_idx = self.lang_combo.findData(config.target_language)
        self.lang_combo.setCurrentIndex(lang_idx if lang_idx >= 0 else 0)
        lang_col.addWidget(self.lang_combo)
        model_lang_row.addLayout(lang_col, 2)
        layout.addLayout(model_lang_row)

        # API Key
        self.key_label = QLabel('API Key', self)
        layout.addWidget(self.key_label)
        key_row = QHBoxLayout()
        self.key_edit = QLineEdit(config.api_key, self)
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText(tr('粘贴从服务商获取的 API Key'))
        self.key_edit.setAccessibleName(tr('翻译 API Key'))
        key_row.addWidget(self.key_edit, 1)
        self.toggle_key_btn = QPushButton(tr('显示'), self)
        self.toggle_key_btn.setCheckable(True)
        self.toggle_key_btn.setAutoDefault(False)
        self.toggle_key_btn.toggled.connect(self._toggle_key_echo)
        key_row.addWidget(self.toggle_key_btn)
        layout.addLayout(key_row)

        self.remember_key_check = QCheckBox(tr('保存到 Windows 凭据管理器'), self)
        self.remember_key_check.setToolTip(tr('默认仅本次运行有效。'))
        self.remember_key_check.setChecked(not config.session_only_key)
        layout.addWidget(self.remember_key_check)

        # Privacy consent box (online endpoints)
        self.privacy_box = QFrame(self)
        self.privacy_box.setObjectName('dlgPrivacyBox')
        p_lay = QVBoxLayout(self.privacy_box)
        p_lay.setContentsMargins(12, 10, 12, 10)
        p_lay.setSpacing(6)
        p_text = QLabel(
            tr('API Key 仅通过 HTTPS 发给上方服务商用于认证。在线翻译会将待译文字发给此服务商。开启自动翻译后，划词或翻页也会发送对应文字。'),
            self.privacy_box,
        )
        p_text.setWordWrap(True)
        p_lay.addWidget(p_text)
        self.privacy_check = QCheckBox(tr('我同意将待译文字发送至上述服务商'), self.privacy_box)
        self.privacy_check.setChecked(config.privacy_accepted)
        p_lay.addWidget(self.privacy_check)
        self.context_check = QCheckBox(tr('划词时一并发送附近原文，帮助判断词义'), self.privacy_box)
        self.context_check.setChecked(config.online_selection_context and config.privacy_accepted)
        self.context_check.setEnabled(config.privacy_accepted)
        self.privacy_check.toggled.connect(self.context_check.setEnabled)
        p_lay.addWidget(self.context_check)
        layout.addWidget(self.privacy_box)

        # Local privacy reassurance box
        self.local_box = QFrame(self)
        self.local_box.setObjectName('dlgLocalBox')
        l_lay = QVBoxLayout(self.local_box)
        l_lay.setContentsMargins(12, 9, 12, 9)
        l_label = QLabel(
            tr('应用仅连接本机接口。离线使用请确认服务未开启云端转发。'),
            self.local_box,
        )
        l_label.setWordWrap(True)
        l_lay.addWidget(l_label)
        layout.addWidget(self.local_box)

        # Test connection + feedback
        test_row = QHBoxLayout()
        self.test_button = QPushButton(tr('测试连接'), self)
        self.test_button.setAutoDefault(False)
        self.test_button.clicked.connect(self._test_connection)
        test_row.addWidget(self.test_button)
        self.feedback = QLabel('', self)
        self.feedback.setObjectName('dlgFeedback')
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.feedback.setWordWrap(True)
        test_row.addWidget(self.feedback, 1)
        layout.addLayout(test_row)

        layout.addStretch()

        preferences = QWidget()
        pref_layout = QVBoxLayout(preferences)
        pref_layout.setContentsMargins(14, 16, 14, 16)
        pref_layout.setSpacing(12)
        self.auto_selection_check = QCheckBox(tr('划词后自动翻译'), preferences)
        self.auto_page_check = QCheckBox(tr('翻页后自动翻译当前页'), preferences)
        self.skip_hf_check = QCheckBox(tr('自动跳过页眉、页脚与页码'), preferences)
        self.auto_selection_check.setChecked(config.auto_translate_selection)
        self.auto_page_check.setChecked(config.auto_translate_on_page_turn)
        self.skip_hf_check.setChecked(config.skip_header_footer)
        for checkbox in (self.auto_selection_check, self.auto_page_check, self.skip_hf_check):
            pref_layout.addWidget(checkbox)
        pref_layout.addStretch()
        self.tabs.addTab(preferences, tr('阅读偏好'))

        # Footer buttons
        buttons = QHBoxLayout()
        self.clear_key_btn = QPushButton(tr('清除已存密钥'), self)
        self.clear_key_btn.setAutoDefault(False)
        self.clear_key_btn.clicked.connect(self._clear_key)
        buttons.addWidget(self.clear_key_btn)
        buttons.addStretch()
        cancel_btn = QPushButton(tr('取消'), self)
        cancel_btn.setAutoDefault(False)
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        self.save_button = QPushButton(tr('保存并启用'), self)
        self.save_button.setObjectName('primary')
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._save)
        buttons.addWidget(self.save_button)
        outer.addLayout(buttons)
        self.tabs.currentChanged.connect(self._tab_changed)

        self.provider_combo.currentIndexChanged.connect(self._on_preset_changed)
        self.endpoint_edit.textChanged.connect(self._sync_endpoint_mode)
        self.model_edit.textChanged.connect(self._invalidate_connection_test)
        self.deepseek_model_combo.currentTextChanged.connect(self._invalidate_connection_test)
        self.key_edit.textChanged.connect(self._invalidate_connection_test)
        self.lang_combo.currentIndexChanged.connect(self._invalidate_connection_test)
        self.privacy_check.toggled.connect(self._invalidate_connection_test)
        self.finished.connect(self._cancel_connection_test)
        self._apply_preset_meta(self.provider_combo.currentData(), update_fields=False)
        self._initial_service_form = self._draft_config()
        self.preferences_only = False

        from app.shortcuts import bind_window
        bind_window(self)

    def _tab_changed(self, index: int) -> None:
        self.clear_key_btn.setVisible(index == 0)
        self.save_button.setText(tr('保存并启用') if index == 0 else tr('保存'))
        if index == 1:
            self._service_height = self.height()
            self.resize(self.width(), max(240, self.minimumSizeHint().height()))
        else:
            self.resize(self.width(), self._service_height)

    def reading_preferences(self) -> dict[str, bool]:
        return dict(auto_translate_selection=self.auto_selection_check.isChecked(),
                    auto_translate_on_page_turn=self.auto_page_check.isChecked(),
                    skip_header_footer=self.skip_hf_check.isChecked())

    def _toggle_key_echo(self, shown: bool) -> None:
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password)
        self.toggle_key_btn.setText(tr('隐藏') if shown else tr('显示'))

    def _on_preset_changed(self) -> None:
        self._reset_credentials()
        key = self.provider_combo.currentData()
        self._apply_preset_meta(key, update_fields=True)

    def _apply_preset_meta(self, key: str, *, update_fields: bool) -> None:
        preset = PRESETS_BY_KEY.get(key)
        if preset is None:
            return
        deepseek = key == DEFAULT_PROVIDER
        self.preset_desc.setText(tr(preset.description))
        self.console_button.setVisible(bool(preset.console_url))
        self.endpoint_edit.setReadOnly(deepseek)
        self.endpoint_edit.setPlaceholderText(preset.endpoint or 'https://example.com/v1')
        self.model_stack.setCurrentWidget(self.deepseek_model_combo if deepseek else self.model_edit)
        if update_fields or deepseek:
            self.endpoint_edit.setText(preset.endpoint)
        if update_fields:
            self.deepseek_model_combo.setCurrentIndex(0)
            self.model_edit.setText(preset.model)
        self._sync_endpoint_mode()

    def _sync_endpoint_mode(self) -> None:
        raw = self.endpoint_edit.text().strip()
        origin = endpoint_origin(raw)
        if origin != self._endpoint_origin:
            self._endpoint_origin = origin
            self._reset_credentials()
        try:
            _, is_local = validate_endpoint(raw, provider=self.provider_combo.currentData())
        except ValueError:
            is_local = False
        self.privacy_box.setVisible(not is_local)
        self.local_box.setVisible(is_local)
        self.key_label.setText(tr('API Key（本机模型通常可留空）') if is_local else 'API Key')
        self._invalidate_connection_test()

    def _reset_credentials(self) -> None:
        self._clear_key_requested = False
        self.key_edit.clear()
        self.remember_key_check.setChecked(False)
        self.privacy_check.setChecked(False)
        self.context_check.setChecked(False)

    def _open_console(self) -> None:
        preset = PRESETS_BY_KEY.get(self.provider_combo.currentData())
        if preset and preset.console_url:
            QDesktopServices.openUrl(QUrl(preset.console_url))

    def _clear_key(self) -> None:
        self.key_edit.clear()
        self._clear_key_requested = True
        self._set_feedback(tr('已清空密钥输入框，点击“保存并启用”后生效。'), 'ok')

    def _set_feedback(self, message: str, tone: str) -> None:
        self.feedback.setText(message)
        self.feedback.setProperty('tone', tone)
        self.feedback.style().unpolish(self.feedback)
        self.feedback.style().polish(self.feedback)

    def _draft_config(self) -> TranslationSettings:
        provider = self.provider_combo.currentData() or DEFAULT_PROVIDER
        if provider == DEFAULT_PROVIDER:
            model = self.deepseek_model_combo.currentText()
        else:
            model = self.model_edit.text().strip()
        return replace(
            self._initial,
            provider=provider,
            endpoint=self.endpoint_edit.text().strip(),
            model=model,
            api_key=self.key_edit.text().strip(),
            session_only_key=not self.remember_key_check.isChecked(),
            privacy_accepted=self.privacy_check.isChecked(),
            online_selection_context=self.privacy_check.isChecked() and self.context_check.isChecked(),
            target_language=self.lang_combo.currentData() or 'zh-CN',
        )

    def _validate_draft(self, draft: TranslationSettings, *, clearing_key: bool = False) -> str:
        try:
            _, is_local = validate_endpoint(draft.endpoint, provider=draft.provider)
            validate_api_key(draft.api_key, draft.endpoint, draft.model)
        except ValueError as exc:
            return str(exc)
        if not draft.model:
            return tr('请填写模型名称。')
        if clearing_key:
            return ''
        if draft.requires_key and not draft.api_key:
            return tr('请填写 API Key。')
        if not is_local and not draft.privacy_accepted:
            return tr('请先勾选上方的在线翻译数据外发确认。')
        return ''

    def _cancel_connection_test(self, *_args) -> None:
        if self._test_handle is not None:
            self._test_handle.cancel()
            self._test_handle = None
        self.test_button.setEnabled(True)

    def _invalidate_connection_test(self, *_args) -> None:
        self._cancel_connection_test()
        self._set_feedback('', '')

    def _test_connection(self) -> None:
        self._cancel_connection_test()
        draft = self._draft_config()
        error = self._validate_draft(draft)
        if error:
            self._set_feedback(error, 'error')
            return
        self.test_button.setEnabled(False)
        self._set_feedback(tr('正在测试连接…'), '')

        def on_ok(sample: str, elapsed_ms: int):
            self._test_handle = None
            self.test_button.setEnabled(True)
            preview = sample.replace('\n', ' ')[:24]
            self._set_feedback(tr('连接成功（{p0} ms · 示例：{p1}）', p0=str(elapsed_ms), p1=preview), 'ok')

        def on_err(reason: str):
            self._test_handle = None
            self.test_button.setEnabled(True)
            self._set_feedback(reason, 'error')

        self._test_handle = self._client.test_connection(draft, on_success=on_ok, on_error=on_err)

    def _save(self) -> None:
        draft = self._draft_config()
        self.preferences_only = (self.tabs.currentIndex() == 1 and draft == self._initial_service_form
                                 and not self._clear_key_requested)
        if self.preferences_only:
            self.result_config = replace(self._initial, **self.reading_preferences())
            self.accept()
            return
        clearing_key = self._clear_key_requested and bool(self._initial.api_key) and not draft.api_key
        error = self._validate_draft(draft, clearing_key=clearing_key)
        if error:
            self.tabs.setCurrentIndex(0)
            self._set_feedback(error, 'error')
            return
        if clearing_key:
            draft = replace(draft, privacy_accepted=False)
        self.result_config = replace(draft, **self.reading_preferences())
        self.accept()


class SelectionMiniBar(QFrame):
    """Compact two-step floating toolbar shown near text selection so normal copy stays unobstructed."""

    copy_requested = pyqtSignal()
    translate_requested = pyqtSignal()

    def __init__(self, viewport: QWidget):
        super().__init__(viewport)
        self.setObjectName('translationMiniBar')
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        set_theme_style(self, FLOATING_STYLE)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 3, 4, 3)
        layout.setSpacing(3)

        self.copy_btn = QPushButton(tr('复制'), self)
        self.copy_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.clicked.connect(self.copy_requested.emit)

        self.translate_btn = QPushButton(tr('划词翻译'), self)
        self.translate_btn.setProperty('primary', True)
        self.translate_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.translate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.translate_btn.clicked.connect(self.translate_requested.emit)
        layout.addWidget(self.translate_btn)
        layout.addWidget(self.copy_btn)

        self.hide()

    def show_at(self, selection: QRect) -> bool:
        self.adjustSize()
        pos = _near_selection(self.parentWidget().rect(), selection, self.size())
        if pos is None:
            self.hide()
            return False
        self.move(pos)
        self.show()
        self.raise_()
        return True


class TranslationBubble(QFrame):
    """One scrollable result, anchored to a selection or docked for region details."""

    pin_requested = pyqtSignal(str, str, str)  # label, source, translated
    retranslate_requested = pyqtSignal(str)
    settings_requested = pyqtSignal()
    ocr_install_requested = pyqtSignal()
    closed = pyqtSignal()
    content_changed = pyqtSignal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName('translationBubble')
        set_theme_style(self, FLOATING_STYLE)

        self._source_text = ''
        self._translated_text = ''
        self._label = ''
        self.floating = False

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        head = QHBoxLayout()
        self._head = head
        head.setSpacing(6)
        self.title_label = QLabel(tr('划词翻译'), self)
        self.title_label.setObjectName('bubbleTitle')
        self.title_label.setWordWrap(False)
        head.addWidget(self.title_label, 1)
        self.engine_label = QLabel('', self)
        self.engine_label.setObjectName('bubbleHint')
        head.addWidget(self.engine_label)
        self.close_btn = _icon_button(self, 'x', tr('关闭译文详情'))
        self.close_btn.clicked.connect(self._close)
        head.addWidget(self.close_btn)
        root.addLayout(head)

        # Collapsible OCR source editor (for region OCR tolerance & re-translation)
        self.ocr_toggle_btn = QPushButton(tr('查看／微调识别原文'), self)
        self.ocr_toggle_btn.setCheckable(True)
        self.ocr_toggle_btn.toggled.connect(self._toggle_ocr_editor)
        self.ocr_toggle_btn.hide()
        root.addWidget(self.ocr_toggle_btn)

        self.ocr_box = QWidget(self)
        ocr_lay = QVBoxLayout(self.ocr_box)
        ocr_lay.setContentsMargins(0, 0, 0, 0)
        ocr_lay.setSpacing(4)
        self.ocr_editor = QPlainTextEdit(self.ocr_box)
        self.ocr_editor.setFixedHeight(68)
        ocr_lay.addWidget(self.ocr_editor)
        ocr_apply_row = QHBoxLayout()
        ocr_apply_row.addStretch()
        self.ocr_retranslate_btn = QPushButton(tr('按修改后原文重译'), self.ocr_box)
        self.ocr_retranslate_btn.setProperty('primary', True)
        self.ocr_retranslate_btn.clicked.connect(self._emit_retranslate)
        ocr_apply_row.addWidget(self.ocr_retranslate_btn)
        ocr_lay.addLayout(ocr_apply_row)
        self.ocr_box.hide()
        root.addWidget(self.ocr_box)

        self.result_edit = QTextEdit(self)
        self.result_edit.setProperty('floating', False)
        self.result_edit.setAcceptRichText(False)
        self.result_edit.setReadOnly(True)
        self.result_edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.result_edit.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.result_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.result_edit.setAccessibleName(tr('翻译结果'))
        self.result_edit.setMinimumHeight(24)
        root.addWidget(self.result_edit, 1)

        self.status_hint = QLabel('', self)
        self.status_hint.setObjectName('bubbleHint')
        self.status_hint.setWordWrap(True)
        self.status_hint.hide()
        root.addWidget(self.status_hint)

        self._foot_bar = QWidget(self)
        self._foot_bar.setObjectName('bubbleFoot')
        foot = QHBoxLayout(self._foot_bar)
        self._foot = foot
        foot.setContentsMargins(0, 0, 0, 0)
        foot.setSpacing(5)
        self.copy_btn = _icon_button(self, 'save-as', tr('复制译文'))
        self.copy_btn.clicked.connect(self._copy)
        foot.addWidget(self.copy_btn)

        self.pin_btn = _icon_button(self, 'bookmark', tr('固定到侧栏'))
        self.pin_btn.clicked.connect(self._pin)
        foot.addWidget(self.pin_btn)

        self.retry_btn = QPushButton(tr('重试'), self._foot_bar)
        self.retry_btn.clicked.connect(lambda: self.retranslate_requested.emit(self._source_text))
        self.retry_btn.hide()
        foot.addWidget(self.retry_btn)

        self.settings_btn = QPushButton(tr('翻译设置'), self._foot_bar)
        self.settings_btn.setProperty('primary', True)
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        self.settings_btn.hide()
        foot.addWidget(self.settings_btn)

        self.ocr_install_btn = QPushButton(tr('安装 OCR 增强包'), self._foot_bar)
        self.ocr_install_btn.setProperty('primary', True)
        self.ocr_install_btn.clicked.connect(self.ocr_install_requested.emit)
        self.ocr_install_btn.hide()
        foot.addWidget(self.ocr_install_btn)

        foot.addStretch()
        root.addWidget(self._foot_bar)
        self.hide()

    def _sync_footer_visibility(self) -> None:
        buttons = (self.retry_btn, self.settings_btn, self.ocr_install_btn)
        if not self.floating:
            buttons = (self.copy_btn, self.pin_btn, *buttons)
        self._foot_bar.setVisible(any(not btn.isHidden() for btn in buttons))

    def set_floating_font_size(self, pixel_size: int | None) -> None:
        size = max(12, min(17, int(pixel_size))) if pixel_size else 13
        font = QFont(self.font())
        font.setPixelSize(size)
        self.result_edit.setFont(font)
        self.result_edit.document().setDefaultFont(font)

    def set_floating(self, floating: bool) -> None:
        self.floating = floating
        self.setProperty('floating', floating)
        self.result_edit.setProperty('floating', floating)
        self.layout().setContentsMargins(12 if floating else 10, 8, 12 if floating else 10, 8)
        self.layout().setSpacing(4 if floating else 6)
        self.result_edit.document().setDocumentMargin(1 if floating else 4)
        self.setStyleSheet(self.styleSheet())
        for widget in (self, self.result_edit):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        self.result_edit.updateGeometry()
        if floating:
            for button in (self.copy_btn, self.pin_btn):
                shown = not button.isHidden()
                button.setParent(self)
                self._head.insertWidget(self._head.indexOf(self.close_btn), button)
                button.setVisible(shown)
            if self.result_edit.font().pixelSize() <= 0:
                self.set_floating_font_size(13)
        else:
            self.result_edit.document().setDefaultFont(self.result_edit.font())
            for index, button in enumerate((self.copy_btn, self.pin_btn)):
                shown = not button.isHidden()
                button.setParent(self._foot_bar)
                self._foot.insertWidget(index, button)
                button.setVisible(shown)
        self._sync_footer_visibility()

    def place_near(self, selection: QRect) -> bool:
        viewport = self.parentWidget().rect()
        usable_w = viewport.width() - 16
        usable_h = viewport.height() - 16
        self._sync_footer_visibility()
        self.ensurePolished()
        self.result_edit.ensurePolished()
        self.layout().activate()
        layout_min_w = self.layout().minimumSize().width()
        if usable_w < layout_min_w or usable_h <= 0:
            return False
        margins = self.layout().contentsMargins()
        insets = self.result_edit.contentsMargins()
        edit_frame = self.result_edit.frameWidth()
        h_chrome = (margins.left() + margins.right() + 2 * self.frameWidth()
                    + insets.left() + insets.right() + 2 * edit_frame)
        v_edit_extra = insets.top() + insets.bottom() + 2 * edit_frame + 4
        probe = self.result_edit.document().clone()
        probe.setDefaultFont(self.result_edit.document().defaultFont())
        probe.setDocumentMargin(self.result_edit.document().documentMargin())
        probe.setTextWidth(-1)
        ideal_text_w = ceil(probe.idealWidth())

        min_w = min(usable_w, max(layout_min_w, 260))
        if selection.width() >= min_w:
            # Match the source selection width directly so wrapped lines align with the original column.
            width = min(usable_w, selection.width())
        else:
            # Short or narrow selections expand only as needed without crowding the header.
            cap_narrow = min(460, usable_w)
            width = min(cap_narrow, max(min_w, ideal_text_w + h_chrome + 12))

        text_w = max(1, width - h_chrome)
        chrome = self.layout().minimumSize().height() - self.result_edit.minimumHeight()
        minimum = chrome + 28
        max_h = min(420, max(200, int(usable_h * 0.55)))

        probe.setTextWidth(text_w)
        editor_height = ceil(probe.size().height()) + v_edit_extra
        if chrome + editor_height > max_h:
            vsb_w = max(self.result_edit.verticalScrollBar().sizeHint().width(), 10)
            probe.setTextWidth(max(1, text_w - vsb_w))
            editor_height = ceil(probe.size().height()) + v_edit_extra

        preferred = max(minimum, min(max_h, chrome + editor_height))
        available = max(viewport.bottom() - selection.bottom(), selection.top() - viewport.top()) - 16
        for height in (preferred, min(preferred, available)):
            if height < minimum:
                continue
            size = QSize(width, height)
            point = _near_selection(viewport, selection, size)
            if point is not None:
                self.setGeometry(QRect(point, size))
                self.show()
                self.raise_()
                self.layout().activate()
                return True
        return False

    def _toggle_ocr_editor(self, checked: bool) -> None:
        self.ocr_box.setVisible(checked)

    def _emit_retranslate(self) -> None:
        edited = self.ocr_editor.toPlainText().strip()
        if edited:
            self._source_text = edited
            self.retranslate_requested.emit(edited)

    def _copy(self) -> None:
        if self._translated_text:
            QApplication.clipboard().setText(self._translated_text)
            self.status_hint.setText(tr('已复制译文。'))
            self.status_hint.show()
            self.content_changed.emit()

    def _pin(self) -> None:
        if self._translated_text:
            self.pin_requested.emit(self._label or tr('划词译文'), self._source_text, self._translated_text)
            self._close()

    def _close(self) -> None:
        self.closed.emit()

    def set_result_text(self, text: str) -> None:
        self.result_edit.setPlainText(text)
        document = self.result_edit.document()
        _set_line_height(document, 145)
        # Preserve paragraph breathing room on multi-block translations without altering toPlainText().
        if document.blockCount() > 1 and '\n\n' not in text:
            block = document.firstBlock()
            while block.isValid() and block.next().isValid():
                if block.text().strip():
                    fmt = QTextBlockFormat()
                    fmt.setBottomMargin(5)
                    cursor = QTextCursor(block)
                    cursor.mergeBlockFormat(fmt)
                block = block.next()
        self.result_edit.moveCursor(QTextCursor.MoveOperation.Start)

    def show_loading(self, title: str, engine: str, message: str, *, source_text: str = '', is_ocr: bool = False) -> None:
        self._label = title
        self._source_text = source_text
        self._translated_text = ''
        self.title_label.setText(title)
        self.engine_label.setText(engine)
        self.set_result_text(message)
        self.status_hint.hide()
        self.ocr_toggle_btn.setVisible(is_ocr and bool(source_text))
        self.ocr_toggle_btn.setChecked(False)
        self.ocr_box.hide()
        if is_ocr and source_text:
            self.ocr_editor.setPlainText(source_text)
        self.copy_btn.setEnabled(False)
        self.pin_btn.setEnabled(False)
        self.copy_btn.hide()
        self.pin_btn.hide()
        self.retry_btn.hide()
        self.settings_btn.hide()
        self.ocr_install_btn.hide()
        self._sync_footer_visibility()

    def show_result(self, translated: str, *, source_text: str | None = None, is_ocr: bool = False, elapsed_ms: int = 0) -> None:
        if source_text is not None:
            self._source_text = source_text
        self._translated_text = translated
        self.set_result_text(translated)
        self.copy_btn.setEnabled(bool(translated))
        self.pin_btn.setEnabled(bool(translated))
        self.copy_btn.setVisible(bool(translated))
        self.pin_btn.setVisible(bool(translated))
        self.retry_btn.hide()
        self.settings_btn.hide()
        self.ocr_install_btn.hide()
        if is_ocr and self._source_text:
            self.ocr_editor.setPlainText(self._source_text)
            self.ocr_toggle_btn.show()
        if elapsed_ms > 0 and not self.floating:
            self.status_hint.setText(tr('翻译完成 · {p0} ms', p0=str(elapsed_ms)))
            self.status_hint.show()
        else:
            self.status_hint.hide()
        self._sync_footer_visibility()
        self.content_changed.emit()

    def show_error(self, message: str, *, need_settings: bool = False, need_ocr: bool = False, can_retry: bool = True) -> None:
        self._translated_text = ''
        self.set_result_text(message)
        self.copy_btn.setEnabled(False)
        self.pin_btn.setEnabled(False)
        self.copy_btn.hide()
        self.pin_btn.hide()
        self.retry_btn.setVisible(can_retry and bool(self._source_text) and not need_settings and not need_ocr)
        self.settings_btn.setVisible(need_settings)
        self.ocr_install_btn.setVisible(need_ocr)
        self.status_hint.hide()
        self._sync_footer_visibility()
        self.content_changed.emit()


class RegionTranslateTool(QObject):
    """Crosshair marquee selection on PreviewView for translating text or scanned/diagram regions."""

    region_selected = pyqtSignal(object, int, tuple)  # ref, page_index, region_tuple

    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.view = host.preview_view
        self.active = False
        self.start = self.overlay = self.ref = self.index = None
        self.workspace = self.generation = None

    def begin(self) -> None:
        if not self.host.ws.pages or not self.host._allow_operation('copy'):
            return
        tools = getattr(self.host, 'annotation_tools', None)
        if tools and tools.deactivate() is False:
            return
        if hasattr(self.host, 'image_tools'):
            self.host.image_tools.clear()
        self.host.region_capture.cancel()
        self.host.reader_interaction.reset()
        self.host.text_selection.clear()
        self.cancel()
        self.host.stack.setCurrentIndex(0)
        self.active = True
        self.workspace = self.host.ws
        self.view.setFocus(Qt.FocusReason.OtherFocusReason)
        self.view.viewport().setCursor(Qt.CursorShape.CrossCursor)
        QApplication.instance().installEventFilter(self)
        self.host.statusBar().showMessage(tr('在一页内拖动框选要翻译的区域（文字、图表或扫描区均可）；Esc 或右键取消。'))

    def cancel(self) -> None:
        if self.overlay is not None and self.overlay.scene() is not None:
            self.overlay.scene().removeItem(self.overlay)
        self.start = self.overlay = self.ref = self.index = None
        self.workspace = self.generation = None
        if self.active:
            QApplication.instance().removeEventFilter(self)
            self.view.viewport().unsetCursor()
            self.host.statusBar().clearMessage()
        self.active = False

    def sync(self) -> None:
        if self.active and (
            self.workspace is not self.host.ws
            or not self.host.ws.pages
            or not self.view.isVisible()
            or (
                self.ref is not None
                and (
                    self.index >= len(self.host.ws.pages)
                    or self.host.ws.pages[self.index].render_key != self.ref.render_key
                    or self.view.layout_generation != self.generation
                )
            )
        ):
            self.cancel()

    def eventFilter(self, watched, event) -> bool:
        if not self.active:
            return False
        self.sync()
        if not self.active:
            return False
        kind = event.type()
        if (
            (kind == QEvent.Type.MouseButtonPress and isinstance(watched, QWidget)
             and watched not in (self.view, self.view.viewport()))
            or (kind == QEvent.Type.WindowDeactivate and watched is self.host)
        ):
            self.cancel()
            return False
        if watched in (self.view, self.view.viewport()):
            if kind == QEvent.Type.ShortcutOverride and (
                self.start is not None or event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Space)
            ):
                event.accept()
                return True
            if kind == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self.cancel()
                self.host.statusBar().showMessage(tr('已取消框选翻译。'), 3000)
                return True
            if kind == QEvent.Type.Hide:
                self.cancel()
                return False
        if watched is not self.view.viewport():
            return False
        if kind == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.RightButton:
                self.cancel()
                self.host.statusBar().showMessage(tr('已取消框选翻译。'), 3000)
            elif event.button() == Qt.MouseButton.LeftButton:
                point = self.view.mapToScene(event.position().toPoint())
                index = self.view.page_at_point(point)
                if 0 <= index < len(self.view.page_rects) and self.view.page_rects[index].contains(point):
                    self.host.go_to_page(index + 1, reveal=False)
                    self.index, self.ref = index, self.host.ws.pages[index]
                    self.generation = self.view.layout_generation
                    self.start = point
                    self.overlay = QGraphicsRectItem()
                    accent = QColor(ui_color('#0F766E'))
                    pen = QPen(accent, 1.5, Qt.PenStyle.DashLine)
                    pen.setCosmetic(True)
                    self.overlay.setPen(pen)
                    fill = QColor(accent)
                    fill.setAlpha(28)
                    self.overlay.setBrush(fill)
                    self.overlay.setZValue(10)
                    self.overlay.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                    self.view.scene().addItem(self.overlay)
            return True
        if kind in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease):
            if self.start is not None:
                bounds = self.view.page_rects[self.index]
                point = self.view.mapToScene(event.position().toPoint())
                point = QPointF(
                    max(bounds.left(), min(bounds.right(), point.x())),
                    max(bounds.top(), min(bounds.bottom(), point.y())),
                )
                rect = QRectF(self.start, point).normalized()
                self.overlay.setRect(rect)
                if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                    ref, index = self.ref, self.index
                    local = rect.translated(-bounds.topLeft())
                    self.cancel()
                    if local.width() < 4 or local.height() < 4:
                        self.host.statusBar().showMessage(tr('框选区域过小，请重新框选。'), 3000)
                        return True
                    region = (local.left(), local.top(), local.right(), local.bottom())
                    self.region_selected.emit(ref, index, region)
            return True
        return kind in (QEvent.Type.MouseButtonDblClick, QEvent.Type.ContextMenu)


class _InlineBlockItem(QGraphicsItem):
    """Typeset one complete passage or cell in page coordinates."""

    def __init__(self, rect: QRectF, segment: PageSegment, translated: str, view):
        super().__init__()
        self._rect = rect
        self.segment = segment
        self.translated = translated
        self._view = view
        self._text_rect = rect.adjusted(3, 1, -3, -1)
        self._doc = self._fitted_document()
        self.paper = QColor(segment.cell.style.fill_color or '#FFFFFF') if segment.cell else QColor('#FFFFFF')
        # Keep all paper masks below the text, including for overlapping line boxes.
        self.paper_item = QGraphicsRectItem(rect)
        self.paper_item.setPen(QPen(Qt.PenStyle.NoPen))
        self.paper_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.paper_item.setZValue(3.9)
        self.setZValue(4.0)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setToolTip(tr('点击在侧栏查看完整译文'))

    def _fitted_document(self) -> QTextDocument | None:
        if self._text_rect.isEmpty():
            return None
        document = QTextDocument()
        document.setUseDesignMetrics(True)
        document.setDocumentMargin(0)
        option = document.defaultTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        document.setDefaultTextOption(option)
        document.setPlainText(self.translated)
        # Let Qt shape, wrap and draw the glyph outlines at the final zoom.
        # The transparent contour selects its path renderer without adding ink.
        cursor = QTextCursor(document)
        cursor.select(QTextCursor.SelectionType.Document)
        outline = QTextCharFormat()
        outline.setTextOutline(QPen(Qt.GlobalColor.transparent))
        cursor.mergeCharFormat(outline)
        _set_line_height(document, 112 if self.segment.font_size else 125)
        document.setTextWidth(self._text_rect.width())

        heading = self.segment.role == 'heading'
        font = QFont('Microsoft YaHei UI')
        font.setBold(heading or bool(self.segment.cell and self.segment.cell.style.bold))
        target = round(self.segment.font_size) if self.segment.font_size else (18 if heading else 13)
        target = max(8, min(48, target))
        minimum = min(target, max(9, round(target * .78))) if self.segment.font_size else 10
        for pixels in range(target, minimum - 1, -1):
            font.setPixelSize(pixels)
            document.setDefaultFont(font)
            size = document.size()
            if size.width() <= self._text_rect.width() and size.height() <= self._text_rect.height():
                return document
        return None

    def boundingRect(self) -> QRectF:
        return self._rect

    def text_origin(self):
        doc_h = self._doc.size().height()
        gap = self._text_rect.height() - doc_h
        font_px = max(8, self._doc.defaultFont().pixelSize())
        if self.segment.cell:
            offset = max(0.0, gap / 2)
        elif 0.0 < gap <= max(10.0, font_px * 0.9):
            # Align compact / single-line translated blocks vertically with the source baseline.
            offset = gap / 2
        else:
            offset = 0.0
        return self._text_rect.topLeft() + QPointF(0, offset)

    def refresh_paper(self):
        colors = getattr(self._view, 'reading_colors', None)
        self.paper_item.setBrush(QColor(colors.paper) if colors and colors.enabled else self.paper)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        if self._doc is None:
            return
        colors = getattr(self._view, 'reading_colors', None)
        fg = QColor(colors.ink if colors and colors.enabled else self.segment.ink or '#1E293B')
        painter.save()
        painter.setClipRect(self._text_rect, Qt.ClipOperation.IntersectClip)
        painter.translate(self.text_origin())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        ctx = QAbstractTextDocumentLayout.PaintContext()
        palette = QApplication.palette()
        palette.setColor(palette.ColorRole.Text, fg)
        ctx.palette = palette
        self._doc.documentLayout().draw(painter, ctx)
        painter.restore()

def _relative_luminance(color: QColor) -> float:
    components = color.getRgbF()[:3]
    linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in components]
    return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722


def _contrast_ratio(first: QColor, second: QColor) -> float:
    low, high = sorted((_relative_luminance(first), _relative_luminance(second)))
    return (high + 0.05) / (low + 0.05)


def _sample_paper_color(paper, bounds: QRectF, rect: QRectF, ink_hex: str | None) -> QColor:
    """Sample background around a passage and guarantee readable foreground contrast."""
    fg = QColor(ink_hex or '#1E293B')
    if not fg.isValid():
        fg = QColor('#1E293B')
    fallback = QColor('#FFFFFF') if _relative_luminance(fg) < 0.35 else QColor('#1E293B')
    if paper is None or paper.isNull() or bounds.width() <= 0 or bounds.height() <= 0:
        return fallback
    pw, ph = paper.width(), paper.height()
    if pw <= 0 or ph <= 0:
        return fallback
    left = rect.left() - bounds.left()
    right = rect.right() - bounds.left()
    top = rect.top() - bounds.top()
    bottom = rect.bottom() - bounds.top()
    cx = (left + right) / 2
    cy = (top + bottom) / 2
    probes = (
        (left - 2.0, cy),
        (right + 2.0, cy),
        (left + 2.0, top - 2.0),
        (cx, top - 2.0),
        (right - 2.0, top - 2.0),
        (left + 2.0, bottom + 2.0),
        (cx, bottom + 2.0),
        (right - 2.0, bottom + 2.0),
    )
    passing: list[tuple[float, QColor]] = []
    for sx, sy in probes:
        px = max(0, min(pw - 1, int(sx / bounds.width() * pw)))
        py = max(0, min(ph - 1, int(sy / bounds.height() * ph)))
        color = paper.pixelColor(px, py)
        if _contrast_ratio(color, fg) >= 4.5:
            passing.append((_relative_luminance(color), color))
    if not passing:
        return fallback
    passing.sort(key=lambda pair: pair[0])
    return passing[len(passing) // 2][1]


def _free_text_rect(rect, expanded, occupied, index):
    """Trim proposed padding to the adjacent blocks, keeping the original ink box intact."""
    for i, other in enumerate(occupied):
        if i == index or not expanded.intersects(other):
            continue
        if other.bottom() <= rect.top():
            expanded.setTop(other.bottom())
        elif other.top() >= rect.bottom():
            expanded.setBottom(other.top())
        elif other.right() <= rect.left():
            expanded.setLeft(other.right())
        elif other.left() >= rect.right():
            expanded.setRight(other.left())
        else:
            return QRectF()
    return expanded


class InlineOverlayLayer:
    """Manages read-only inline translation overlays and bidirectional highlight rectangles in PreviewView."""

    def __init__(self, view):
        self.view = view
        self.reflow = ReflowedPage(view)
        self._items: list[_InlineBlockItem] = []
        self._highlight: QGraphicsRectItem | None = None
        self._peeking = False
        self._hidden = False
        self._selecting_source = False
        self._page_index = None
        self._snapshot = None
        self._source_snapshot = None
        self._unchanged = ()

    def clear_overlays(self) -> None:
        scene = self.view.scene()
        for item in self._items:
            for layer in (item.paper_item, item):
                if layer.scene() is scene:
                    scene.removeItem(layer)
        self._items.clear()
        self._page_index = None
        self._snapshot = self._source_snapshot = None
        self._unchanged = ()

    def clear_highlight(self) -> None:
        if self._highlight is not None:
            scene = self.view.scene()
            if self._highlight.scene() is scene:
                scene.removeItem(self._highlight)
            self._highlight = None

    def clear_all(self) -> None:
        self.clear_overlays()
        self.clear_highlight()
        self.reflow.set_page(None)

    def visible_segment_at(self, scene_point):
        if not self._items or self._hidden or self._peeking or self._selecting_source:
            return None
        return next((item.segment for item in self.view.scene().items(scene_point)
                     if isinstance(item, _InlineBlockItem)), None)

    def set_selecting_source(self, active: bool) -> None:
        if self._selecting_source != active:
            self._selecting_source = active
            self._apply_visibility()

    def showing_translation(self, page_index):
        return (self._page_index == page_index and bool(self._items)
                and not (self._hidden or self._peeking or self._selecting_source))

    def selection_snapshot(self, page_index, source):
        if not self.showing_translation(page_index):
            return source
        if self._snapshot is None or source is not self._source_snapshot:
            from app.translation.visibletext import visible_snapshot
            origin = self.view.page_rects[page_index].topLeft()
            self._snapshot = visible_snapshot(source, self._items, origin, self._unchanged)
            self._source_snapshot = source
        return self._snapshot

    def set_peeking(self, peeking: bool) -> None:
        self._peeking = peeking
        self._apply_visibility()

    def set_hidden(self, hidden: bool) -> None:
        self._hidden = hidden
        self._apply_visibility()

    def _apply_visibility(self) -> None:
        if self.reflow.page is not None:
            visible = not (self._hidden or self._peeking or self._selecting_source)
            if not visible:
                self.reflow.cancel_region()
            self.reflow.setVisible(visible)
        opacity = 0.0 if self._peeking or self._selecting_source else 1.0
        for item in self._items:
            for layer in (item.paper_item, item):
                layer.setVisible(not self._hidden)
                layer.setOpacity(opacity)
        if self._highlight is not None:
            self._highlight.setVisible(not self._selecting_source)

    def render_page(self, page_index: int, segments: tuple[PageSegment, ...], translations: dict[str, str],
                    *, enabled: bool, via_ocr: bool = False) -> bool:
        """Publish overlays together only when all translated passages fit."""
        self.clear_overlays()
        if not enabled or not (0 <= page_index < len(self.view.page_rects)):
            return True
        bounds = self.view.page_rects[page_index]
        scene = self.view.scene()
        regions = []
        for segment in segments:
            x0, y0, x1, y1 = segment.bbox
            rect = QRectF(x0, y0, x1 - x0, y1 - y0).translated(bounds.topLeft()).intersected(bounds)
            if not rect.isEmpty():
                regions.append((segment, rect))
            elif translations.get(segment.segment_id, segment.text).split() != segment.text.split():
                return False
        occupied = [rect for _, rect in regions]
        items = []
        page_item = getattr(self.view, '_item', None)
        paper = (page_item.pixmap().toImage() if page_item is not None and self.view.active_page == page_index
                 else None)
        for index, (seg, rect) in enumerate(regions):
            text = translations.get(seg.segment_id)
            if not text or text.split() == seg.text.split():
                continue
            if seg.text.lstrip().startswith('•') and not text.lstrip().startswith(('•', '▪', '●', '- ', '* ')):
                text = '•  ' + text
            if seg.cell:
                rect = rect.adjusted(.6, .6, -.6, -.6)
            elif not via_ocr:
                # Include the antialiased edge of the source glyphs in the paper mask.
                mask = rect.adjusted(-.6, -.6, .6, .6).intersected(bounds)
                if not any(mask.intersects(other) for i, other in enumerate(occupied) if i != index):
                    rect = occupied[index] = mask
            item = _InlineBlockItem(rect, seg, text, self.view)
            if item._doc is None and not seg.cell:
                right_field = not via_ocr and rect.left() > bounds.left() + bounds.width() * .75
                for width, up, down in ((0, 4, 4), (12, 4, 4), (24, 4, 4), (48, 4, 4), (0, 0, 8), (0, 8, 0)):
                    left, right = (-width, 0) if right_field else (0, width)
                    expanded = rect.adjusted(left, -up, right, down).intersected(bounds)
                    expanded = _free_text_rect(rect, expanded, occupied, index)
                    if expanded.isEmpty() or expanded == rect:
                        continue
                    item = _InlineBlockItem(expanded, seg, text, self.view)
                    if item._doc is not None:
                        occupied[index] = expanded
                        break
            if item._doc is None:
                return False
            if not seg.cell and paper is not None and not paper.isNull():
                item.paper = _sample_paper_color(paper, bounds, rect, seg.ink)
            items.append(item)
        for item in items:
            item.refresh_paper()
            scene.addItem(item.paper_item)
            scene.addItem(item)
        self._items = items
        self._page_index = page_index
        self._unchanged = tuple(seg.bbox for seg, _ in regions if seg.segment_id in translations
                                and translations[seg.segment_id].split() == seg.text.split())
        self._apply_visibility()
        return True

    def highlight_segments(self, page_index: int, segments, *, ensure_visible: bool = False) -> None:
        self.clear_highlight()
        if not (0 <= page_index < len(self.view.page_rects)):
            return
        bounds = self.view.page_rects[page_index]
        rect = QRectF()
        for segment in segments:
            x0, y0, x1, y1 = segment.bbox
            rect = rect.united(QRectF(x0, y0, x1 - x0, y1 - y0))
        if rect.isEmpty():
            return
        rect.translate(bounds.topLeft())
        accent = QColor(ui_color('#0F766E'))
        fill = QColor(accent)
        fill.setAlpha(35)
        item = QGraphicsRectItem(rect)
        item.setPen(QPen(Qt.PenStyle.NoPen))
        item.setBrush(fill)
        item.setZValue(5)
        item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        bar = QGraphicsRectItem(QRectF(rect.left(), rect.top(), 1.6, rect.height()), item)
        bar.setPen(QPen(Qt.PenStyle.NoPen))
        bar.setBrush(accent)
        bar.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        item.setVisible(not self._selecting_source)
        self.view.scene().addItem(item)
        self._highlight = item
        if ensure_visible:
            self.view.ensureVisible(rect, 48, 72)

    def repaint(self) -> None:
        if self.reflow.page is not None:
            self.reflow.sync_geometry()
        for item in self._items:
            item.refresh_paper()
            item.update()
        if self._highlight is not None:
            self._highlight.update()


def _table_content(segments, values):
    """Return a tab-separated grid for copying into a document or spreadsheet."""
    width = max(seg.cell.col + seg.cell.col_span for seg in segments)
    lines = []
    for _, row in groupby(segments, key=lambda seg: seg.cell.row):
        fields = [''] * width
        for seg in row:
            fields[seg.cell.col] = values.get(seg.segment_id, '').replace('\n', ' ')
        lines.append('\t'.join(fields))
    return '\n'.join(lines)


def _sidebar_rows(segments):
    for _, group in groupby(segments, key=lambda seg: seg.table_id or seg.segment_id):
        entries = list(group)
        first = entries[0]
        if not first.cell:
            yield entries, {}, first.label if first.role in ('header', 'footer') else ''
            continue
        rows = [list(row) for _, row in groupby(entries, key=lambda seg: seg.cell.row)]
        headers = {seg.cell.col: seg for seg in rows[0] if seg.cell.style.bold}
        for index, row in enumerate(rows):
            yield row, headers if index else {}, first.label if not index else ''


def _table_row_html(row, headers, values):
    parts = []
    for entry in row:
        header = headers.get(entry.cell.col)
        title = escape(values.get(header.segment_id, header.text)) if header else ''
        value = escape(values.get(entry.segment_id, ''))
        parts.append(f'<b>{title}</b>　{value}' if title else value)
    return ('<br>' if headers else '　·　').join(parts)


class _SegmentCard(QFrame):
    """One translated paragraph card in the right-hand side panel with hover/click sync."""

    hovered = pyqtSignal(str)
    unhovered = pyqtSignal()
    clicked = pyqtSignal(str)
    copied = pyqtSignal(str)
    unpin_requested = pyqtSignal()

    def __init__(self, segment_id: str, label: str, source: str, translated: str, *, show_bilingual: bool,
                 heading: bool = False, pinned: bool = False, parent=None):
        super().__init__(parent)
        self.segment_id = segment_id
        self.translated = translated
        self.setObjectName('tpSegmentCard')
        self.setProperty('linked', False)
        self._press_point = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 9, 4, 9)
        layout.setSpacing(6)
        text = QVBoxLayout()
        text.setSpacing(4)
        layout.addLayout(text, 1)
        if pinned or label:
            title = QLabel(tr('已固定 · {p0}', p0=label) if pinned else label, self)
            title.setObjectName('tpSegMeta')
            title.setWordWrap(True)
            text.addWidget(title)

        self.src_label = QLabel(source, self)
        self.src_label.setObjectName('tpSegSrc')
        self.src_label.setWordWrap(True)
        self.src_label.setTextFormat(Qt.TextFormat.PlainText)
        self.src_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.src_label.setVisible(show_bilingual and bool(source))

        self.dst_label = QLabel(translated, self)
        self.dst_label.setObjectName('tpSegDst')
        self.dst_label.setProperty('heading', heading)
        self.dst_label.setWordWrap(True)
        self.dst_label.setTextFormat(Qt.TextFormat.PlainText)
        self.dst_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text.addWidget(self.dst_label)
        text.addWidget(self.src_label)
        if segment_id:
            for surface in (self, self.src_label, self.dst_label):
                surface.installEventFilter(self)

        copy_btn = _icon_button(self, 'save-as', tr('复制译文'))
        copy_btn.clicked.connect(self._copy)
        layout.addWidget(copy_btn, 0, Qt.AlignmentFlag.AlignTop)
        if pinned:
            remove_btn = _icon_button(self, 'x', tr('移除'))
            remove_btn.clicked.connect(self.unpin_requested.emit)
            layout.addWidget(remove_btn, 0, Qt.AlignmentFlag.AlignTop)

    def set_bilingual(self, show: bool) -> None:
        self.src_label.setVisible(show and bool(self.src_label.text()))

    def set_linked(self, linked: bool) -> None:
        if bool(self.property('linked')) != linked:
            self.setProperty('linked', linked)
            self.style().unpolish(self)
            self.style().polish(self)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.translated)
        self.copied.emit(self.segment_id)

    def enterEvent(self, event) -> None:
        if self.segment_id:
            self.hovered.emit(self.segment_id)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if self.segment_id:
            self.unhovered.emit()
        super().leaveEvent(event)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._press_point = event.globalPosition().toPoint()
        elif self._press_point is not None and kind in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease):
            moved = (event.globalPosition().toPoint() - self._press_point).manhattanLength()
            if moved >= QApplication.startDragDistance():
                self._press_point = None
            elif kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                self._press_point = None
                self.clicked.emit(self.segment_id)
        return super().eventFilter(watched, event)


class PageOriginalButton(QToolButton):
    """Click to switch the document, or hold to glance at the original."""
    peek_changed = pyqtSignal(bool)

    def __init__(self, parent):
        super().__init__(parent)
        self._press_time = QElapsedTimer()
        self.setObjectName('pageOriginalButton')
        self.setFixedHeight(24)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pressed.connect(self._begin_peek)
        self.released.connect(lambda: self.peek_changed.emit(False))
        self.toggled.connect(self._update_label)
        self._update_label(False)
        set_theme_style(self, """
            QToolButton#pageOriginalButton {
                color: #134E4A; background: #F0FDFA; border: 1px solid #99B2AC;
                border-radius: 5px; min-height: 0; padding: 0 9px; font-size: 12px;
            }
            QToolButton#pageOriginalButton:checked, QToolButton#pageOriginalButton:hover {
                background: #D9EEEA; border-color: #0F766E;
            }
            QToolButton#pageOriginalButton:focus, QToolButton#pageOriginalButton:pressed {
                border: 2px solid #0D9488;
            }
        """)
        self.hide()

    def _update_label(self, original):
        label = tr('返回译文') if original else tr('查看原文')
        self.setText(label)
        self.setAccessibleName(label)
        self.setToolTip(label if original else tr('单击切换到原文，按住可临时对照'))

    def _begin_peek(self):
        self._press_time.start()
        self.peek_changed.emit(True)

    def nextCheckState(self):
        if not self._press_time.isValid() or self._press_time.elapsed() < QApplication.startDragTime():
            super().nextCheckState()
        self._press_time.invalidate()

    def reset_view(self):
        self.setChecked(False)
        self.peek_changed.emit(False)
        self.hide()


class TranslationPanel(QFrame):
    """Right-side translation panel styled consistently with ZhiXu PDF's exportSidebar."""

    translate_page_requested = pyqtSignal()
    ocr_translate_page_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    region_translate_requested = pyqtSignal()
    selection_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    ocr_install_requested = pyqtSignal()
    close_requested = pyqtSignal()
    target_language_changed = pyqtSignal(str)
    view_mode_changed = pyqtSignal(str)
    options_changed = pyqtSignal(dict)
    segment_hovered = pyqtSignal(str)
    segment_unhovered = pyqtSignal()
    segment_clicked = pyqtSignal(str)
    copy_all_requested = pyqtSignal()
    unpin_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('translationSidebar')
        self.setMinimumWidth(300)
        self.setMaximumWidth(460)
        set_theme_style(self, PANEL_STYLE)

        self._cards: dict[str, _SegmentCard] = {}
        self._busy = False
        self._primary_action = self.translate_page_requested
        self._page_translated = False
        self._primary_label = tr('翻译当前页')
        self._error_banner = None
        self._empty_tags_layout: QBoxLayout | None = None
        self._empty_tags: list[QLabel] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 10)
        root.setSpacing(8)

        # Row 1: Calm Header (Title + Provider Pill + Close) ---------------------
        head = QHBoxLayout()
        head.setSpacing(6)
        title = QLabel(tr('翻译'), self)
        self._title = title
        title.setObjectName('tpTitle')
        head.addWidget(title)
        head.addStretch()

        self.engine_pill = QToolButton(self)
        self.engine_pill.setObjectName('tpEnginePill')
        self.engine_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self.engine_pill.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.engine_pill.setToolTip(tr('翻译设置'))
        self.engine_pill.clicked.connect(self.settings_requested.emit)
        head.addWidget(self.engine_pill)
        self.close_btn = _icon_button(self, 'x', tr('收起翻译面板'))
        self.close_btn.clicked.connect(self.close_requested.emit)
        head.addWidget(self.close_btn)
        root.addLayout(head)

        # Row 2: Target Language + Single Primary CTA ----------------------------
        primary_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        primary_row.setSpacing(6)
        self.lang_combo = ClearComboBox(self)
        self.lang_combo.setAccessibleName(tr('翻译目标语言'))
        self.lang_combo.setToolTip(tr('翻译目标语言'))
        self.lang_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        for code, label, _ in TARGET_LANGUAGES:
            self.lang_combo.addItem(tr(label), code)
        self.lang_combo.currentIndexChanged.connect(
            lambda: (self._fit_controls(), self.target_language_changed.emit(self.lang_combo.currentData()))
        )
        primary_row.addWidget(self.lang_combo)

        self.primary_btn = QPushButton(self._primary_label, self)
        self.primary_btn.setProperty('primary', True)
        self.primary_btn.setProperty('translationAction', True)
        self.primary_btn.setAutoDefault(False)
        self.primary_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.primary_btn.clicked.connect(self._on_primary_clicked)
        primary_row.addWidget(self.primary_btn, 1)
        root.addLayout(primary_row)

        # Row 3: Lightweight Secondary Excerpt Tools -----------------------------
        self._actions = primary_row
        self._excerpts = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._excerpts.setSpacing(6)
        self.selection_btn = QPushButton(tr('划词翻译'), self)
        self.selection_btn.setCheckable(True)
        self.selection_btn.setProperty('secondaryTool', True)
        self.selection_btn.toggled.connect(
            lambda armed: self.selection_btn.setText(tr('请划选文字') if armed else tr('划词翻译'))
        )
        self.selection_btn.toggled.connect(self._fit_controls)
        self.selection_btn.setToolTip(tr('拖动选择要翻译的文字'))
        self.selection_btn.setAccessibleDescription(self.selection_btn.toolTip())
        self.selection_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.selection_btn.clicked.connect(self.selection_requested.emit)
        self.region_btn = QPushButton(tr('框选翻译'), self)
        self.region_btn.setProperty('secondaryTool', True)
        self.region_btn.setToolTip(tr('框选区域翻译'))
        self.region_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.region_btn.clicked.connect(self.region_translate_requested.emit)
        for button in (self.selection_btn, self.region_btn):
            button.setProperty('translationAction', True)
            button.setAutoDefault(False)
        self._excerpts.addWidget(self.selection_btn, 1)
        self._excerpts.addWidget(self.region_btn, 1)
        root.addLayout(self._excerpts)

        # Row 4: Recessed Segmented Display Mode Switch --------------------------
        mode_lay = QHBoxLayout()
        mode_lay.setSpacing(4)
        mode_switch = QFrame(self)
        mode_switch.setObjectName('tpViewModes')
        switch_lay = QHBoxLayout(mode_switch)
        switch_lay.setContentsMargins(3, 3, 3, 3)
        switch_lay.setSpacing(3)

        self.side_mode_btn = QPushButton(toolbar_icon('page-spread', size=14), tr('侧栏对照'), mode_switch)
        self.side_mode_btn.setToolTip(tr('在右侧栏显示译文'))
        self.overlay_mode_btn = QPushButton(toolbar_icon('text-box', size=14), tr('页内译文'), mode_switch)
        self.overlay_mode_btn.setToolTip(tr('在原文位置显示译文'))
        for button in (self.side_mode_btn, self.overlay_mode_btn):
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.setIconSize(QSize(14, 14))
            button.setAccessibleDescription(button.toolTip())
            switch_lay.addWidget(button, 1)
        self.side_mode_btn.setChecked(True)
        self.side_mode_btn.clicked.connect(lambda: self._select_mode('side'))
        self.overlay_mode_btn.clicked.connect(lambda: self._select_mode('overlay'))
        mode_lay.addWidget(mode_switch, 1)
        self.chk_bilingual = QCheckBox(tr('同时显示原文'), self)
        self.chk_bilingual.setToolTip(tr('对照栏同时显示原文'))
        self.chk_bilingual.toggled.connect(lambda checked: self.options_changed.emit({'show_bilingual': checked}))
        self.chk_bilingual.hide()
        root.addLayout(mode_lay)

        divider = QFrame(self)
        divider.setObjectName('tpDivider')
        divider.setFixedHeight(1)
        root.addWidget(divider)

        self.layout_hint = QLabel(tr('本页译文已按段落重排。'), self)
        self.layout_hint.setObjectName('tpHint')
        self.layout_hint.setWordWrap(True)
        self.layout_hint.hide()
        root.addWidget(self.layout_hint)

        # Progress bar
        self.progress_widget = QWidget(self)
        prog_lay = QVBoxLayout(self.progress_widget)
        prog_lay.setContentsMargins(0, 0, 0, 0)
        prog_lay.setSpacing(4)
        self.progress_label = QLabel('', self.progress_widget)
        self.progress_label.setObjectName('tpHint')
        prog_lay.addWidget(self.progress_label)
        self.progress_bar = QProgressBar(self.progress_widget)
        self.progress_bar.setObjectName('tpProgress')
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setTextVisible(False)
        prog_lay.addWidget(self.progress_bar)
        self.progress_widget.hide()
        root.addWidget(self.progress_widget)

        # Scroll area for cards / onboarding / scan guidance
        self.scroll = QScrollArea(self)
        self.scroll.setObjectName('tpScroll')
        self.scroll.viewport().setObjectName('tpViewport')
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content = QWidget()
        self.content.setObjectName('tpContent')
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 4, 2, 4)
        self.content_layout.setSpacing(4)
        self.content_layout.addStretch()
        self.scroll.setWidget(self.content)
        self.detail = TranslationBubble(self)
        self._content_stack = QStackedLayout()
        self._content_stack.addWidget(self.scroll)
        self._content_stack.addWidget(self.detail)
        root.addLayout(self._content_stack, 1)

        # Footer
        self.footer_hint = QLabel(self)
        self.footer_hint.setObjectName('tpHint')
        self.footer_hint.setWordWrap(True)
        self.footer_hint.hide()
        self.result_footer = QFrame(self)
        self.result_footer.setObjectName('tpFooterWrap')
        footer = QHBoxLayout(self.result_footer)
        footer.setContentsMargins(0, 8, 0, 0)
        footer.setSpacing(6)
        footer.addWidget(self.chk_bilingual)
        footer.addStretch()
        footer.addWidget(self.footer_hint)
        self.copy_all_btn = _icon_button(self, 'save-as', tr('复制整页'))
        self.copy_all_btn.clicked.connect(self.copy_all_requested.emit)
        self.copy_all_btn.setEnabled(False)
        self.copy_all_btn.hide()
        footer.addWidget(self.copy_all_btn)
        self.result_footer.hide()
        root.addWidget(self.result_footer)

    @property
    def detail_visible(self) -> bool:
        return self._content_stack.currentWidget() is self.detail

    def _has_footer_content(self) -> bool:
        return not (self.chk_bilingual.isHidden() and self.copy_all_btn.isHidden() and self.footer_hint.isHidden())

    def set_detail_visible(self, visible: bool) -> None:
        if self.detail.floating:
            self.detail.hide()
            self.detail.setParent(self)
            self._content_stack.addWidget(self.detail)
            self.detail.set_floating(False)
        self._content_stack.setCurrentWidget(self.detail if visible else self.scroll)
        self.result_footer.setVisible((not visible) and self._has_footer_content())

    def float_detail(self, viewport) -> None:
        if not self.detail.floating:
            self._content_stack.setCurrentWidget(self.scroll)
            self.result_footer.setVisible(self._has_footer_content())
            self._content_stack.removeWidget(self.detail)
            self.detail.setParent(viewport)
            self.detail.set_floating(True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'primary_btn'):
            self._fit_controls()

    def _fit_controls(self):
        margins = self.layout().contentsMargins()
        available = max(self.contentsRect().width(), self.minimumWidth()) - margins.left() - margins.right()
        horizontal = QBoxLayout.Direction.LeftToRight
        vertical = QBoxLayout.Direction.TopToBottom
        combo = self.lang_combo
        option = QStyleOptionComboBox()
        combo.initStyleOption(option)
        option.rect = QRect(QPoint(), combo.sizeHint())
        text_field = combo.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                                  QStyle.SubControl.SC_ComboBoxEditField, combo)
        frame_and_arrow = option.rect.width() - text_field.width()
        # Leave a small inset on both sides of the painted text.
        lang_w = max(100, combo.fontMetrics().horizontalAdvance(combo.currentText()) + frame_and_arrow + 4)
        prim_w = max(
            self.primary_btn.minimumSizeHint().width(),
            self.primary_btn.fontMetrics().horizontalAdvance(self.primary_btn.text()) + 24,
        )
        if lang_w + prim_w + 6 <= available:
            self._actions.setDirection(horizontal)
            self.lang_combo.setMinimumWidth(lang_w)
            self.lang_combo.setMaximumWidth(lang_w)
        else:
            self._actions.setDirection(vertical)
            self.lang_combo.setMinimumWidth(0)
            self.lang_combo.setMaximumWidth(16777215)
        word, region = (max(b.sizeHint().width(), b.fontMetrics().horizontalAdvance(b.text()) + 16)
                        for b in (self.selection_btn, self.region_btn))
        self._excerpts.setDirection(horizontal if word + region + 6 <= available else vertical)
        if self._empty_tags_layout is not None and self._empty_tags:
            tags_total = sum(tag.sizeHint().width() for tag in self._empty_tags) + 6 * (len(self._empty_tags) - 1)
            self._empty_tags_layout.setDirection(horizontal if tags_total <= available - 36 else vertical)
        if hasattr(self, '_provider_text'):
            remaining = available - self._title.sizeHint().width() - self.close_btn.width() - 24
            full_width = self.engine_pill.fontMetrics().horizontalAdvance(self._provider_text) + 20
            self.engine_pill.setText(self._provider_text if full_width <= remaining else tr('设置'))

    def _on_primary_clicked(self) -> None:
        if self._busy:
            self.stop_requested.emit()
        else:
            self._primary_action.emit()

    def _select_mode(self, mode: str) -> None:
        self.side_mode_btn.setChecked(mode == 'side')
        self.overlay_mode_btn.setChecked(mode == 'overlay')
        self.view_mode_changed.emit(mode)

    def sync_config(self, config: TranslationSettings) -> None:
        self._provider_text = (tr(config.provider_label) + ' · ' + tr('设置')
                               if config.is_ready or config.needs_api_key else tr('翻译设置'))
        self.engine_pill.setToolTip(tr('翻译设置') + ' · ' + tr(config.provider_label))
        self._fit_controls()
        self.engine_pill.setAccessibleName(tr('翻译设置'))
        self.lang_combo.blockSignals(True)
        idx = self.lang_combo.findData(config.target_language)
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)
        self.lang_combo.blockSignals(False)
        self._fit_controls()

        self.side_mode_btn.setChecked(config.view_mode == 'side')
        self.overlay_mode_btn.setChecked(config.view_mode == 'overlay')

        self.chk_bilingual.blockSignals(True)
        self.chk_bilingual.setChecked(config.show_bilingual)
        self.chk_bilingual.blockSignals(False)

        for index in range(self.content_layout.count()):
            card = self.content_layout.itemAt(index).widget()
            if isinstance(card, _SegmentCard):
                card.set_bilingual(config.show_bilingual)

    def set_busy(self, busy: bool, *, label: str = '', done: int = 0, total: int = 0) -> None:
        self._busy = busy
        if busy:
            self._clear_error_banner()
            self.primary_btn.setText(tr('停止翻译'))
            self.progress_widget.show()
            self.progress_label.setText(label or tr('正在翻译…'))
            if total > 0:
                self.progress_bar.setRange(0, total)
                self.progress_bar.setValue(min(done, total))
            else:
                self.progress_bar.setRange(0, 0)
        else:
            self.primary_btn.setText(self._primary_label)
            self.progress_widget.hide()
        self._update_action_emphasis()
        self._fit_controls()

    def _update_action_emphasis(self):
        for button, primary, danger in (
            (self.primary_btn, not self._page_translated and not self._busy, self._busy),
            (self.selection_btn, self._page_translated and not self._busy, False),
        ):
            if button.property('primary') == primary and button.property('danger') == danger:
                continue
            button.setProperty('primary', primary)
            button.setProperty('danger', danger)
            button.style().unpolish(button)
            button.style().polish(button)

    def set_primary_label(self, text: str, action=None, *, translated: bool = False) -> None:
        self._primary_label = text
        self._page_translated = translated
        self._primary_action = self.translate_page_requested if action is None else action
        if not self._busy:
            self.primary_btn.setText(text)
        self._update_action_emphasis()
        self._fit_controls()

    def _clear_error_banner(self) -> None:
        if self._error_banner is not None:
            self.content_layout.removeWidget(self._error_banner)
            self._error_banner.hide()
            self._error_banner.deleteLater()
            self._error_banner = None

    def _clear_content(self) -> None:
        self.chk_bilingual.hide()
        self.copy_all_btn.hide()
        self.result_footer.hide()
        self._clear_error_banner()
        self._cards.clear()
        self._empty_tags_layout = None
        self._empty_tags.clear()
        self.footer_hint.hide()
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()

    def _show_empty_hint(self, text: str, *, title: str = '', show_modes: bool = False) -> None:
        if self.content_layout.count() == 0:
            self.content_layout.addStretch(2)
        box = QWidget(self.content)
        box.setObjectName('tpEmptyBox')
        lay = QVBoxLayout(box)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(8)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        badge = QToolButton(box)
        badge.setObjectName('tpEmptyIcon')
        badge.setFixedSize(44, 44)
        badge.setIcon(toolbar_icon('translate', '#0F766E', 20))
        badge.setIconSize(QSize(20, 20))
        badge.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(badge, 0, Qt.AlignmentFlag.AlignHCenter)

        if title:
            heading = QLabel(title, box)
            heading.setObjectName('tpEmptyTitle')
            heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
            heading.setWordWrap(True)
            lay.addWidget(heading)

        hint = QLabel(text, box)
        hint.setObjectName('tpHint')
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        lay.addWidget(hint)

        if show_modes:
            tags_wrap = QWidget(box)
            tags_wrap.setStyleSheet('background: transparent;')
            tags_row = QBoxLayout(QBoxLayout.Direction.LeftToRight, tags_wrap)
            tags_row.setContentsMargins(0, 6, 0, 0)
            tags_row.setSpacing(6)
            self._empty_tags = []
            for tag_text in (tr('划词即译'), tr('框选区域'), tr('整页对照')):
                tag = QLabel(tag_text, tags_wrap)
                tag.setObjectName('tpEmptyTag')
                tag.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
                tags_row.addWidget(tag, 0, Qt.AlignmentFlag.AlignHCenter)
                self._empty_tags.append(tag)
            self._empty_tags_layout = tags_row
            lay.addWidget(tags_wrap, 0, Qt.AlignmentFlag.AlignHCenter)

        self.content_layout.addWidget(box)
        self.content_layout.addStretch(3)
        self._fit_controls()

    def show_unconfigured(self, *, key_missing: bool = False) -> None:
        self._clear_content()
        self.copy_all_btn.setEnabled(False)
        if key_missing:
            self.set_primary_label(tr('补填 API Key'), self.settings_requested)
            self._show_empty_hint(tr('服务商和模型已保存。'), title=tr('还差一步即可使用'))
        else:
            self.set_primary_label(tr('去配置…'), self.settings_requested)
            self._show_empty_hint(tr('尚未配置翻译服务。'), title=tr('配置翻译引擎'))

    def show_scan_guidance(self, page_number: int, *, ocr_ready: bool) -> None:
        self._clear_content()
        if ocr_ready:
            self.set_primary_label(tr('OCR 并译本页'), self.ocr_translate_page_requested)
        else:
            self.set_primary_label(tr('安装 OCR 增强包…'), self.ocr_install_requested)
        self.copy_all_btn.setEnabled(False)
        self._show_empty_hint(tr('第 {p0} 页为扫描图片页', p0=str(page_number)), title=tr('需要先识别页面文字'))

    def show_ready_empty(self, page_number: int, pinned: list[tuple[str, str, str]], *, show_bilingual: bool) -> None:
        self._clear_content()
        self.set_primary_label(tr('翻译第 {p0} 页', p0=str(page_number)))
        self.copy_all_btn.setEnabled(False)
        self._append_pinned(pinned, show_bilingual=show_bilingual)
        self._show_empty_hint(tr('可划词、框选或翻译本页。'), title=tr('准备翻译第 {p0} 页', p0=str(page_number)), show_modes=not pinned)

    def show_error_banner(self, message: str, *, use_ocr: bool = False) -> None:
        self._clear_error_banner()
        action = self.ocr_translate_page_requested if use_ocr else self.translate_page_requested
        self.set_primary_label(tr('重试'), action)
        box = QFrame(self.content)
        self._error_banner = box
        box.setObjectName('tpNoticeBox')
        lay = QVBoxLayout(box)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)
        lbl = QLabel(message, box)
        lbl.setTextFormat(Qt.TextFormat.PlainText)
        lbl.setWordWrap(True)
        lay.addWidget(lbl)
        self.content_layout.insertWidget(0, box)

    def _append_pinned(self, pinned: list[tuple[str, str, str]], *, show_bilingual: bool) -> None:
        if pinned:
            self.chk_bilingual.show()
            self.result_footer.show()
        for idx, (label, src, dst) in enumerate(pinned):
            card = _SegmentCard('', label, src, dst, show_bilingual=show_bilingual, pinned=True, parent=self.content)
            card.unpin_requested.connect(lambda i=idx: self.unpin_requested.emit(i))
            self.content_layout.addWidget(card)

    def render_translations(
        self,
        page_number: int,
        cached: CachedPageTranslation,
        pinned: list[tuple[str, str, str]],
        *,
        show_bilingual: bool,
        from_cache: bool = False,
    ) -> None:
        self._clear_content()
        action = self.ocr_translate_page_requested if cached.extraction.via_ocr else self.translate_page_requested
        self.set_primary_label(tr('重新翻译') if cached.complete else tr('重试'), action, translated=cached.complete)
        self._append_pinned(pinned, show_bilingual=show_bilingual)

        ext = cached.extraction
        done_count = sum(1 for seg in ext.segments if seg.segment_id in cached.translations)
        if ext.via_ocr:
            info_text = tr('已通过本机离线 OCR 临时识别 {p0} 段并翻译（原文件未改动）', p0=str(done_count))
        elif ext.skipped_count > 0:
            info_text = tr('第 {p0} 页共 {p1} 段 · 已跳过 {p2} 处页眉／页脚', p0=str(page_number), p1=str(len(ext.segments)), p2=str(ext.skipped_count))
        else:
            info_text = tr('第 {p0} 页按阅读顺序共 {p1} 段', p0=str(page_number), p1=str(len(ext.segments)))
        if from_cache and cached.complete:
            info_text += tr(' · 已命中缓存')

        for segments, headers, label in _sidebar_rows(ext.segments):
            seg = segments[0]
            if not any(entry.segment_id in cached.translations for entry in segments):
                continue
            if seg.cell:
                source = {entry.segment_id: entry.text for entry in (*headers.values(), *segments)}
                card = _SegmentCard(seg.segment_id, label, _table_content(segments, source),
                                    _table_content(segments, cached.translations),
                                    show_bilingual=show_bilingual, parent=self.content)
                for widget, values in ((card.src_label, source), (card.dst_label, cached.translations)):
                    widget.setTextFormat(Qt.TextFormat.RichText)
                    widget.setText(_table_row_html(segments, headers, values))
            else:
                card = _SegmentCard(seg.segment_id, label, '' if seg.role == 'code' else seg.text, cached.translations[seg.segment_id],
                                    show_bilingual=show_bilingual, heading=seg.role == 'heading', parent=self.content)
            card.hovered.connect(self.segment_hovered.emit)
            card.unhovered.connect(self.segment_unhovered.emit)
            card.clicked.connect(self.segment_clicked.emit)
            for entry in segments:
                self._cards[entry.segment_id] = card
            self.content_layout.addWidget(card)

        self.content_layout.addStretch()
        has_footer = done_count > 0 or bool(pinned)
        self.chk_bilingual.setVisible(has_footer)
        self.copy_all_btn.setVisible(done_count > 0)
        self.result_footer.setVisible(has_footer)
        self.copy_all_btn.setEnabled(done_count > 0)
        codes = sum(seg.role == 'code' for seg in ext.segments)
        preserved = tr('已保留 {p0} 段代码', p0=str(codes))
        if codes:
            info_text += ' · ' + preserved
        text_count = len(ext.segments) - codes
        self.footer_hint.setText(tr('已译 {p0}/{p1} 段', p0=str(done_count - codes), p1=str(text_count))
                                 if text_count else preserved)
        self.footer_hint.setToolTip(info_text)
        self.footer_hint.setVisible(done_count > 0)
        if cached.error:
            self.show_error_banner(cached.error, use_ocr=ext.via_ocr)

    def highlight_card(self, segment_id: str | None, *, align_y: int | None = None) -> None:
        selected = self._cards.get(segment_id)
        for card in set(self._cards.values()):
            card.set_linked(card is selected)
        if selected is None or align_y is None:
            return

        layout = self.content_layout
        layout.activate()
        viewport = self.scroll.viewport()
        label = selected.dst_label
        tail = layout.itemAt(layout.count() - 1).spacerItem()
        # A newly visible scrollbar narrows the labels and can wrap earlier paragraphs.
        for _ in range(2):
            width = viewport.width()
            top = label.mapTo(self.content, QPoint()).y()
            fits = align_y <= top and align_y + label.height() + 8 <= viewport.height()
            offset = max(0, top - (max(12, align_y) if fits else 12))
            # Leave just enough space below the last card for the selected text to move up.
            padding = max(0, offset + viewport.height() - tail.geometry().top() - layout.contentsMargins().bottom())
            if padding == tail.sizeHint().height():
                break
            tail.changeSize(0, padding, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.MinimumExpanding)
            layout.invalidate()
            layout.activate()
            QApplication.sendPostedEvents(viewport, QEvent.Type.LayoutRequest)
            if viewport.width() == width:
                break
        self.scroll.verticalScrollBar().setValue(offset)


class TranslationController(QObject):
    """Coordinates translation settings, floating selection UI, region OCR, side panel, and inline overlay."""

    def __init__(self, host, *, network_manager=None):
        super().__init__(host)
        self.host = host
        self.view = host.preview_view
        self.config = load_settings(host._window_settings)
        self.client = OpenAICompatibleClient(self, network_manager=network_manager)
        self.cache = TranslationCache(max_entries=64)
        self._workspace_pins = WeakKeyDictionary()
        self._pinned: list[tuple[str, str, str]] = self._workspace_pins.setdefault(host.ws, [])
        self._job_serial = 0
        self._active_cancel = Event()
        self._page_requests = {}
        self._page_pump = QTimer(self)
        self._page_pump.setSingleShot(True)
        self._page_pump.timeout.connect(self._dispatch_next_batch)
        self._bubble_handle = None
        self._selection_reference = ''
        self._region_cancel: Event | None = None
        self._active_page_key = None
        self._active_page_index = None
        self._pending_batches: list[list[PageSegment]] = []
        self._active_cached: CachedPageTranslation | None = None
        self._total_segments = 0
        self._bubble_source = None
        self._rendered_signature = None
        self._overlay_generation = None
        self._preview_page_index = None
        self._scan_page_key = None

        self._worker_signals = _WorkerSignals(self)
        self._worker_signals.done.connect(self._on_worker_done, Qt.ConnectionType.QueuedConnection)
        self._worker_callbacks: dict[int, tuple[Event, Callable]] = {}

        # Compact selection controls stay on the document viewport.
        viewport = self.view.viewport()
        self.mini_bar = SelectionMiniBar(viewport)
        self.mini_bar.copy_requested.connect(self._mini_copy)
        self.mini_bar.translate_requested.connect(self.translate_current_selection)

        self.panel = TranslationPanel(host)
        self.page_original = PageOriginalButton(host.tab_bar)
        tab_row = host.tab_bar.layout()
        tab_row.insertWidget(tab_row.indexOf(host.tab_bar.new_tab_button) + 1, self.page_original,
                             0, Qt.AlignmentFlag.AlignVCenter)
        self.bubble = self.panel.detail
        self.bubble.pin_requested.connect(self._pin_excerpt)
        self.bubble.retranslate_requested.connect(self._retranslate_bubble_text)
        self.bubble.settings_requested.connect(self.open_settings)
        self.bubble.ocr_install_requested.connect(self._open_ocr_installer)
        self.bubble.closed.connect(self._dismiss_bubble)
        self.bubble.content_changed.connect(self._position_selection_popup)

        # Region translate tool
        self.region_tool = RegionTranslateTool(host)
        self.region_tool.region_selected.connect(self._on_region_selected)

        # Scene overlay layer
        self.inline_layer = InlineOverlayLayer(self.view)
        self.inline_layer.reflow.page_step.connect(host.step_page)
        self.inline_layer.reflow.segment_clicked.connect(self._on_reflow_clicked)
        self.inline_layer.reflow.region_selected.connect(self._on_reflow_region)
        self._linked_segment_id = None

        # Right-side panel (attached into MainWindow's reader_split)
        self.panel.sync_config(self.config)
        self.panel.translate_page_requested.connect(lambda: self.translate_current_page(use_ocr=False))
        self.panel.ocr_translate_page_requested.connect(lambda: self.translate_current_page(use_ocr=True))
        self.panel.stop_requested.connect(self.stop_page_translation)
        self.panel.region_translate_requested.connect(self.begin_region_translate)
        self.panel.settings_requested.connect(self.open_settings)
        self.panel.ocr_install_requested.connect(self._open_ocr_installer)
        self.panel.close_requested.connect(self._close_panel)
        self.panel.target_language_changed.connect(self._on_target_language_changed)
        self.panel.view_mode_changed.connect(self._on_view_mode_changed)
        self.panel.options_changed.connect(self._on_options_changed)
        self.page_original.peek_changed.connect(self._peek_original)
        self.page_original.toggled.connect(self._switch_original)
        self.panel.selection_requested.connect(self.begin_selection_translate)
        self.panel.segment_hovered.connect(self._on_segment_hovered)
        self.panel.segment_unhovered.connect(lambda: self._highlight_source(self._linked_segment_id))
        self.panel.segment_clicked.connect(self._on_segment_clicked)
        self.panel.copy_all_requested.connect(self._copy_all_page_translations)
        self.panel.unpin_requested.connect(self._unpin_excerpt)
        self.panel.hide()

        host.reader_split.addWidget(self.panel)
        host.reader_split.setStretchFactor(host.reader_split.count() - 1, 0)

        self.view.view_changed.connect(self._on_view_changed)
        viewport.installEventFilter(self)
        self.view.installEventFilter(self)
        self.view.horizontalScrollBar().valueChanged.connect(self._position_selection_popup)
        self.view.verticalScrollBar().valueChanged.connect(self._position_selection_popup)
        theme_manager().changed.connect(self._on_theme_changed)

    @property
    def region_active(self) -> bool:
        return self.region_tool.active or self.inline_layer.reflow.region_active

    # --- Settings ---

    def open_settings(self) -> None:
        self._cancel_selection_mode()
        self.mini_bar.hide()
        self._dismiss_bubble()
        dialog = TranslationSettingsDialog(self.config, self.client, self.host)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_config is not None:
                if dialog.preferences_only:
                    self._on_options_changed(dialog.reading_preferences())
                    self.host.statusBar().showMessage(tr('翻译偏好已保存。'), 3000)
                    return
                draft = dialog.result_config
                if draft != self.config:
                    self.stop_page_translation()
                try:
                    self.config = save_settings(self.host._window_settings, draft)
                except ValueError as exc:
                    self.host.statusBar().showMessage(str(exc), 6000)
                    return
                self.panel.sync_config(self.config)
                self.sync_page(force=True)
                if self.config.session_only_key and not draft.session_only_key:
                    message = tr('系统加密暂不可用，API Key 仅在本次运行有效。')
                elif self.config.api_key and self.config.session_only_key:
                    message = tr('翻译设置已保存。API Key 仅本次运行有效。')
                elif self.config.api_key:
                    message = tr('翻译设置已保存，下次打开会自动恢复。')
                else:
                    message = tr('翻译设置已保存：{p0}。', p0=tr(self.config.provider_label))
                self.host.statusBar().showMessage(message, 6000)
        finally:
            dialog.deleteLater()

    def _on_target_language_changed(self, code: str) -> None:
        if code == self.config.target_language or code not in TARGET_LANGUAGE_MAP:
            return
        self.stop_page_translation()
        self._dismiss_bubble()
        self.config = save_settings(self.host._window_settings, replace(self.config, target_language=code), update_key=False)
        self.panel.sync_config(self.config)
        self.sync_page(force=True)

    def _on_view_mode_changed(self, mode: str) -> None:
        if mode not in ('side', 'overlay') or mode == self.config.view_mode:
            return
        if self.host.text_selection.selecting_translation:
            self.host.text_selection.clear()
        self.config = save_settings(self.host._window_settings, replace(self.config, view_mode=mode), update_key=False)
        self.panel.sync_config(self.config)
        self._refresh_inline_layer()

    def _on_options_changed(self, values: dict) -> None:
        draft = replace(self.config, **values)
        skip_changed = self.config.skip_header_footer != draft.skip_header_footer
        if skip_changed:
            self.stop_page_translation()
        self.config = save_settings(self.host._window_settings, draft, update_key=False)
        self.panel.sync_config(self.config)
        if skip_changed:
            self.sync_page(force=True)

    # --- Panel visibility & document lifecycle ---

    def _immersive_reading(self) -> bool:
        immersive = getattr(self.host, 'immersive', None)
        return bool(immersive and (immersive.fullscreen or immersive.borderless))

    def _close_panel(self) -> None:
        if self.host.act_translation.isChecked():
            self.host.act_translation.setChecked(False)
        else:
            self.set_panel_visible(False)

    def set_panel_visible(self, visible: bool) -> None:
        blocked = (
            not self.host.ws.pages
            or self.host._in_arrange()
            or not self.host.act_translation.isEnabled()
            or (self._immersive_reading() and not self.panel.detail_visible)
        )
        show = visible and not blocked
        if show and self.host.act_tools.isChecked():
            # Keep right-hand side clean: translation panel and export tools are mutually exclusive.
            self.host.act_tools.setChecked(False)
        self.host._set_right_panel_visible(self.panel, show)
        if not show:
            self._cancel_selection_mode()
        if show:
            self.sync_page()
        else:
            self.inline_layer.clear_highlight()
            if not self.bubble.floating:
                self._dismiss_bubble()

    def _reveal_sidebar(self) -> None:
        if not self.host.act_translation.isChecked():
            self.host.act_translation.setChecked(True)
        elif not self.panel.isVisible():
            self.set_panel_visible(True)
        if self.panel.isVisible():
            self.host._set_right_panel_visible(self.panel, True, expand=True)

    def _selection_font_px(self) -> int | None:
        selection = self.host.text_selection
        if selection.snapshot is None or selection.area is None:
            return None
        from statistics import median
        from app.pdfedits import selected_lines

        sizes = [
            float(span['size'])
            for _, chars in selected_lines(selection.snapshot['raw'], selection.area)
            for _, span in chars
            if span.get('size')
        ]
        if not sizes:
            return None
        scale = max(self.view.transform().m11(), 0.5)
        # CJK glyphs occupy more of the em-square than Latin text; 0.88 matches optical height.
        return max(12, min(17, round(median(sizes) * scale * 0.88)))

    def _show_detail(self, title: str, engine: str, message: str, *, source_text: str = '',
                     is_ocr: bool = False, near_selection: bool = False) -> None:
        selection = self.host.text_selection
        if near_selection and selection.page_index is not None and selection.area is not None:
            anchor = self._selection_viewport_rect(selection.page_index, selection.area)
            if anchor.intersects(self.view.viewport().rect()):
                self.panel.float_detail(self.view.viewport())
                self.bubble.set_floating_font_size(self._selection_font_px())
                self.bubble.show_loading(title, engine, message, source_text=source_text, is_ocr=is_ocr)
                if self.bubble.place_near(anchor):
                    return
        self.panel.set_detail_visible(True)
        self.bubble.show_loading(title, engine, message, source_text=source_text, is_ocr=is_ocr)
        self._reveal_sidebar()

    def sync_document(self) -> None:
        self.page_original.reset_view()
        self._cancel_selection_mode()
        self.mini_bar.hide()
        self._dismiss_bubble()
        self.region_tool.cancel()
        self.stop_page_translation()
        self.inline_layer.clear_all()
        self._linked_segment_id = None
        self._rendered_signature = None
        self._overlay_generation = None
        self._preview_page_index = None
        self._scan_page_key = None
        self._pinned = self._workspace_pins.setdefault(self.host.ws, [])
        if not self.host.ws.pages:
            self.cache.clear()
            self._pinned.clear()
            self.panel.hide()
            return
        self.sync_page(force=True)

    def sync_page(self, *, force: bool = False) -> None:
        self.mini_bar.hide()
        if not self.host.ws.pages or self.host._in_arrange():
            self.inline_layer.clear_all()
            self._set_overlay_controls_visible(False)
            self._rendered_signature = None
            return
        page_index = self.host.ws.preview_index()
        if page_index is None or not (0 <= page_index < len(self.host.ws.pages)):
            return
        page_changed = self._preview_page_index is not None and page_index != self._preview_page_index
        self._preview_page_index = page_index
        if page_changed:
            self.page_original.reset_view()
            self._dismiss_bubble()
        ref = self.host.ws.pages[page_index]
        cache_key = self._page_cache_key(ref)
        if self._active_page_key is not None and self._active_page_key != cache_key:
            self.stop_page_translation()

        cached = self.cache.get(cache_key)
        cached_count = len(cached.translations) if cached is not None else -1
        signature = (
            page_index,
            cache_key,
            self.config.is_ready,
            self.config.view_mode,
            self.config.show_bilingual,
            tuple((label, len(dst)) for label, _, dst in self._pinned),
            cached_count,
            bool(cached.complete) if cached is not None else False,
            cached.error if cached is not None else '',
        )
        layout_gen = self.view.layout_generation
        if not force and signature == self._rendered_signature:
            if layout_gen != self._overlay_generation:
                self._overlay_generation = layout_gen
                self._refresh_inline_layer()
            return
        if self._rendered_signature is None or self._rendered_signature[:2] != signature[:2]:
            self._linked_segment_id = None
        self._rendered_signature = signature
        self._overlay_generation = layout_gen
        self.inline_layer.clear_highlight()
        self.panel.sync_config(self.config)
        if cached is not None:
            self.panel.render_translations(
                page_index + 1,
                cached,
                self._pinned,
                show_bilingual=self.config.show_bilingual,
                from_cache=True,
            )
            self._refresh_inline_layer()
            if self._linked_segment_id:
                self.panel.highlight_card(self._linked_segment_id)
                self._highlight_source(self._linked_segment_id)
            return

        self.inline_layer.clear_overlays()
        self._set_overlay_controls_visible(False)
        if not self.config.is_ready:
            self.panel.show_unconfigured(key_missing=self.config.needs_api_key)
            return
        if cache_key == self._scan_page_key:
            self.panel.show_scan_guidance(page_index + 1, ocr_ready=self._is_ocr_installed())
            return
        self.panel.show_ready_empty(page_index + 1, self._pinned, show_bilingual=self.config.show_bilingual)
        if page_changed and self.panel.isVisible() and self.config.auto_translate_on_page_turn:
            self.translate_current_page(use_ocr=False)

    def _page_cache_key(self, ref) -> tuple:
        return (ref.render_key, self.config.cache_signature, self.config.skip_header_footer)

    def _on_view_changed(self) -> None:
        self.mini_bar.hide()
        if self.inline_layer.reflow.page is not None:
            self.inline_layer.reflow.sync_geometry()
        self._position_selection_popup()

    def eventFilter(self, watched, event):
        if (event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress) and event.key() == Qt.Key.Key_Escape
                and self.panel.selection_btn.isChecked()):
            event.accept()
            if event.type() == QEvent.Type.KeyPress:
                self._cancel_selection_mode()
            return True
        if event.type() == QEvent.Type.Resize and watched is self.view.viewport():
            if self.inline_layer.reflow.page is not None:
                self.inline_layer.reflow.sync_geometry()
            self._position_selection_popup()
        return False

    def _position_selection_popup(self, *_args) -> None:
        if not self.bubble.floating:
            return
        selection = self.host.text_selection
        if selection.page_index is None or selection.area is None:
            self._dismiss_bubble()
            return
        anchor = self._selection_viewport_rect(selection.page_index, selection.area)
        if not anchor.intersects(self.view.viewport().rect()):
            self._dismiss_bubble()
        else:
            self.bubble.set_floating_font_size(self._selection_font_px())
            if not self.bubble.place_near(anchor):
                self.panel.set_detail_visible(True)
                self._reveal_sidebar()

    def _on_theme_changed(self, *_args) -> None:
        self.inline_layer.repaint()

    def refresh_colors(self) -> None:
        self.inline_layer.repaint()

    # --- Selection translation (two-step mini-bar + context menu + auto mode) ---

    def on_text_selected(self, text: str, page_index: int, area) -> None:
        snippet = (text or '').strip()
        if not snippet or self.region_tool.active or not self.host.ws.pages:
            self.mini_bar.hide()
            return
        if self.panel.selection_btn.isChecked() or (self.config.auto_translate_selection and self.config.is_ready):
            self._cancel_selection_mode()
            self.mini_bar.hide()
            self._translate_selection_text(snippet)
            return
        selection = self._selection_viewport_rect(page_index, area)
        if not self.mini_bar.show_at(selection):
            self.host.statusBar().showMessage(tr('可右键选择“翻译所选文字”。'), 3500)

    def on_text_cleared(self) -> None:
        self.mini_bar.hide()
        self.inline_layer.set_selecting_source(self.panel.selection_btn.isChecked())
        if self._linked_segment_id is not None:
            self._linked_segment_id = None
            self.inline_layer.clear_highlight()
            self.panel.highlight_card(None)
        if self.bubble.floating:
            self._dismiss_bubble()

    def _selection_viewport_rect(self, page_index: int, area) -> QRect:
        if 0 <= page_index < len(self.view.page_rects) and area is not None:
            bounds = self.view.page_rects[page_index]
            snapshot = self.host.text_selection.snapshot
            if snapshot is not None:
                import pymupdf as p
                mapped = p.Rect(area) * p.Matrix(snapshot['matrix'])
                scene_rect = QRectF(mapped.x0, mapped.y0, mapped.width, mapped.height).translated(bounds.topLeft())
                return self.view.mapFromScene(scene_rect).boundingRect()
        return QRect(self.view.viewport().width() // 2 - 90, 48, 1, 1)

    def _mini_copy(self) -> None:
        self.mini_bar.hide()
        self.host.text_selection.copy()

    def _cancel_selection_mode(self):
        if self.panel.selection_btn.isChecked():
            self.panel.selection_btn.setChecked(False)
            if not self.host.text_selection.text:
                self.inline_layer.set_selecting_source(False)
            self.host.statusBar().clearMessage()

    def begin_selection_translate(self):
        self.inline_layer.reflow.cancel_region()
        if not self.panel.selection_btn.isChecked():
            self.inline_layer.set_selecting_source(False)
            self.host.statusBar().clearMessage()
            return
        if not self.host.ws.pages or not self.host._allow_operation('copy'):
            self._cancel_selection_mode()
            return
        self.region_tool.cancel()
        selected = self.host.text_selection
        selected.sync()
        if selected.text and not selected.selecting_translation:
            self._cancel_selection_mode()
            self.translate_current_selection()
            return
        self._dismiss_bubble()
        self.host.set_reading_tool('select')
        if not selected.active():
            self._cancel_selection_mode()
            return
        self.inline_layer.set_selecting_source(True)
        self.view.setFocus(Qt.FocusReason.OtherFocusReason)
        self.host.statusBar().showMessage(tr('拖动选择要翻译的文字，Esc 取消。'))

    def translate_current_selection(self) -> None:
        self.mini_bar.hide()
        sel = self.host.text_selection
        sel.sync()
        if sel.selecting_translation:
            return
        text = (sel.text or '').strip()
        if not text:
            self.host.statusBar().showMessage(tr('请先在页面上拖动选择要翻译的文字。'), 3500)
            return
        self._translate_selection_text(text)

    def _translate_selection_text(self, text: str) -> None:
        self._cancel_bubble_request()
        self._selection_reference = ''
        self._bubble_source = 'selection'
        lang_label = tr(TARGET_LANGUAGE_MAP.get(self.config.target_language, ('简体中文', ''))[0])
        title = tr('划词翻译 · {p0}', p0=lang_label)
        cached = self._matching_excerpt(text)
        if cached:
            self._show_detail(title, tr(self.config.provider_label), '', source_text=text, near_selection=True)
            self.bubble.show_result(cached)
            return
        if not self.config.is_ready:
            self._show_detail(title, tr('未配置'), '', source_text=text, near_selection=True)
            self.bubble.show_error(
                tr('请先在“翻译设置”中配置翻译服务。'),
                need_settings=True,
                can_retry=False,
            )
            return
        self._show_detail(title, tr(self.config.provider_label), tr('正在翻译…'), source_text=text, near_selection=True)
        selection = self.host.text_selection
        if self.config.use_selection_context and not selection.selecting_translation and text == selection.text.strip():
            self._selection_reference = selection_reference(selection.snapshot, selection.area, text)
        self._request_selection_text(text)

    def _request_selection_text(self, text: str, *, correct_language=False) -> None:
        config = self.config

        def received(translated: str, elapsed_ms: int) -> None:
            if not selection_missing_chinese(text, translated, config.target_language):
                self._on_bubble_success(translated, elapsed_ms, False)
            elif correct_language:
                self._on_bubble_error(tr('翻译服务未返回中文译文，请重试或更换模型。'))
            else:
                self.bubble.set_result_text(tr('正在重新翻译…'))
                self._request_selection_text(text, correct_language=True)

        self._bubble_handle = self.client.translate_text(
            config, text, on_success=received, on_error=self._on_bubble_error, correct_language=correct_language,
            selection_context=self._selection_reference,
        )

    def _matching_excerpt(self, text: str) -> str:
        selection = self.host.text_selection
        page_index = selection.page_index
        if (page_index is None or selection.area is None or selection.snapshot is None
                or not 0 <= page_index < len(self.host.ws.pages)):
            return ''
        cached = self.cache.get(self._page_cache_key(self.host.ws.pages[page_index]))
        if cached is None:
            return ''
        import pymupdf as p
        area = p.Rect(selection.area) * p.Matrix(selection.snapshot['matrix'])
        center = (area.tl + area.br) / 2
        words = text.split()
        for seg in cached.extraction.segments:
            if p.Rect(seg.bbox).contains(center) and seg.text.split() == words:
                translated = cached.translations.get(seg.segment_id, '')
                if seg.role != 'code' and selection_missing_chinese(text, translated, self.config.target_language):
                    return ''
                return translated
        return ''

    def _retranslate_bubble_text(self, text: str) -> None:
        self._cancel_bubble_request()
        if not self.config.is_ready:
            self.open_settings()
            return
        self.bubble.set_result_text(tr('正在重新翻译…'))
        self.bubble.copy_btn.setEnabled(False)
        self.bubble.pin_btn.setEnabled(False)
        if self._bubble_source == 'selection':
            self._request_selection_text(text)
            return
        is_ocr = self._bubble_source == 'ocr'
        self._bubble_handle = self.client.translate_text(
            self.config,
            text,
            on_success=lambda dst, ms: self._on_bubble_success(dst, ms, is_ocr),
            on_error=self._on_bubble_error,
        )

    def _on_bubble_success(self, dst: str, elapsed_ms: int, is_ocr: bool) -> None:
        self._bubble_handle = None
        self.bubble.show_result(dst, is_ocr=is_ocr, elapsed_ms=elapsed_ms)

    def _on_bubble_error(self, message: str) -> None:
        self._bubble_handle = None
        self.bubble.show_error(message, can_retry=True)

    def _cancel_bubble_request(self) -> None:
        if self._region_cancel is not None:
            self._region_cancel.set()
            self._region_cancel = None
        if self._bubble_handle is not None:
            self._bubble_handle.cancel()
            self._bubble_handle = None

    def _dismiss_bubble(self) -> None:
        self._cancel_bubble_request()
        self._selection_reference = ''
        if self.bubble.floating:
            self.panel.set_detail_visible(False)
            return
        if self.panel.detail_visible:
            self.panel.set_detail_visible(False)
            if self._immersive_reading():
                self.panel.hide()

    # --- Region translate (text layer or local OCR) ---

    def begin_region_translate(self) -> None:
        self._cancel_selection_mode()
        self.mini_bar.hide()
        self._dismiss_bubble()
        if self.inline_layer.reflow.isVisible():
            if self.host._allow_operation('copy'):
                self.inline_layer.reflow.begin_region()
            return
        self.region_tool.begin()

    def _on_reflow_region(self, text):
        self._cancel_bubble_request()
        self._bubble_source = 'region'
        self._show_detail(tr('页内译文'), tr(self.config.provider_label), '')
        if text:
            self.bubble.show_result(text)
        else:
            self.bubble.show_error(tr('框选区域内未找到可翻译文字。'), can_retry=False)

    def _on_reflow_clicked(self, segment_id):
        pair = self._current_cached_page()
        if pair:
            index, cached = pair
            segment = next((s for s in cached.extraction.segments if s.segment_id == segment_id), None)
            if segment:
                self.activate_page_segment(index, segment)

    def _on_region_selected(self, ref, page_index: int, region: tuple) -> None:
        self._cancel_bubble_request()
        if self.inline_layer.showing_translation(page_index):
            source = (self.ocr_selection_snapshot(ref) or self.host.store.cached_text_snapshot(ref)
                      or self.host.store.cached_text_snapshot(ref, include_tables=False))
            if source is None:
                cancel = Event()
                self._region_cancel = cancel
                self._show_detail(tr('页内译文'), tr(self.config.provider_label), tr('正在准备当前页文字…'))
                self._run_worker(lambda: self.host.store.text_snapshot(ref), cancel,
                                 lambda snapshot, error: self._region_snapshot_ready(ref, page_index, region, snapshot, error))
                return
            if self._show_painted_region(page_index, region, source):
                return
        self._extract_original_region(ref, page_index, region)

    def _region_snapshot_ready(self, ref, page_index, region, source, error):
        self._region_cancel = None
        if error or source is None:
            self.bubble.show_error(str(error) if error else tr('页面文字提取失败，请重试。'), can_retry=False)
        elif not self.inline_layer.showing_translation(page_index):
            self._dismiss_bubble()
        elif not self._show_painted_region(page_index, region, source):
            self._extract_original_region(ref, page_index, region)

    def _show_painted_region(self, page_index, region, source):
        import pymupdf as p
        from app.translation.visibletext import excerpt
        snapshot = self.inline_layer.selection_snapshot(page_index, source)
        area = p.Rect(region)
        text = excerpt(snapshot['raw'], area, snapshot['tables'], cached_only=True, unchanged=snapshot['unchanged'])
        local = QRectF(area.x0, area.y0, area.width, area.height)
        scene_rect = local.translated(self.view.page_rects[page_index].topLeft())
        if not text and not any(item.boundingRect().contains(scene_rect) for item in self.inline_layer._items):
            return False
        self._bubble_source = 'region'
        self._show_detail(tr('页内译文'), tr(self.config.provider_label), '')
        if text:
            self.bubble.show_result(text)
        else:
            self.bubble.show_error(tr('框选区域内未找到可翻译文字。'), can_retry=False)
        return True

    def _extract_original_region(self, ref, page_index, region):
        lang_label = tr(TARGET_LANGUAGE_MAP.get(self.config.target_language, ('简体中文', ''))[0])
        title = tr('框选翻译 · {p0}', p0=lang_label)
        self._show_detail(
            title, tr(self.config.provider_label) if self.config.is_ready else tr('未配置'),
            tr('正在提取框选区域文字（扫描区将自动尝试本地 OCR）…'),
        )
        cancel = Event()
        self._region_cancel = cancel
        self._run_worker(
            lambda: extract_region_for_translation(self.host.store, ref, page_index, region, cancel=cancel.is_set),
            cancel,
            lambda result, exc: self._on_region_extracted(result, exc, title),
        )

    def _on_region_extracted(self, result: RegionExtraction | None, exc: Exception | None, title: str) -> None:
        self._region_cancel = None
        if exc is not None or result is None:
            self.bubble.show_error(str(exc) if exc else tr('无法读取框选区域，请重试。'), can_retry=False)
            return
        if not result.text:
            self.bubble.show_error(
                result.message or tr('框选区域内未找到可翻译文字。'),
                need_ocr=result.ocr_missing,
                can_retry=False,
            )
            return
        is_ocr = result.source == 'ocr'
        self._bubble_source = result.source
        if not self.config.is_ready:
            self.bubble.show_loading(title, tr('未配置'), '', source_text=result.text, is_ocr=is_ocr)
            self.bubble.show_error(
                tr('请先在“翻译设置”中配置翻译服务。'),
                need_settings=True,
                can_retry=False,
            )
            return
        self.bubble.show_loading(
            tr('框选 OCR 翻译') if is_ocr else title,
            tr(self.config.provider_label),
            tr('本地 OCR 识别完成，正在翻译…') if is_ocr else tr('正在翻译…'),
            source_text=result.text,
            is_ocr=is_ocr,
        )
        self._bubble_handle = self.client.translate_text(
            self.config,
            result.text,
            on_success=lambda dst, ms: self._on_bubble_success(dst, ms, is_ocr),
            on_error=self._on_bubble_error,
        )

    # --- Full-page translation (ordered layout batching + streaming display + scan OCR) ---

    def translate_current_page(self, *, use_ocr: bool = False) -> None:
        self._cancel_selection_mode()
        if self.host.text_selection.selecting_translation:
            self.host.text_selection.clear()
        if not self.host.ws.pages:
            return
        if not self.host._allow_operation('copy'):
            return
        if not self.config.is_ready:
            self.open_settings()
            return
        self._dismiss_bubble()
        page_index = self.host.ws.preview_index()
        if page_index is None or not (0 <= page_index < len(self.host.ws.pages)):
            return
        ref = self.host.ws.pages[page_index]

        self.stop_page_translation()
        self._active_page_index = page_index
        self._active_page_key = self._page_cache_key(ref)
        self._active_cancel = Event()
        cancel = self._active_cancel
        cached = self.cache.get(self._active_page_key)
        if cached is not None and not cached.complete and cached.extraction.via_ocr == use_ocr:
            self._start_page_batches(cached)
            return
        self.panel.set_busy(
            True,
            label=tr('正在用本地 OCR 识别第 {p0} 页…', p0=str(page_index + 1)) if use_ocr else tr('正在分析第 {p0} 页版面与段落…', p0=str(page_index + 1)),
        )

        if use_ocr:
            fn = lambda: ocr_page_for_translation(self.host.store, ref, page_index, cancel=cancel.is_set)
        else:
            skip_header_footer = self.config.skip_header_footer
            fn = lambda: extract_page_segments(
                self.host.store, ref, skip_header_footer=skip_header_footer, cancel=cancel.is_set
            )
        self._run_worker(fn, cancel, lambda ext, exc: self._on_page_extracted(page_index, ref, ext, exc, use_ocr=use_ocr))

    def _on_page_extracted(self, page_index: int, ref, extraction: PageExtraction | None, exc: Exception | None, *, use_ocr: bool) -> None:
        if self._active_page_key != self._page_cache_key(ref):
            return
        if exc is not None or extraction is None:
            self.panel.set_busy(False)
            self.panel.show_error_banner(str(exc) if exc else tr('页面文字提取失败，请重试。'), use_ocr=use_ocr)
            return
        if not extraction.segments:
            self.panel.set_busy(False)
            if extraction.is_scan and not extraction.via_ocr:
                self._scan_page_key = self._active_page_key
                ocr_ready = self._is_ocr_installed()
                self.panel.show_scan_guidance(page_index + 1, ocr_ready=ocr_ready)
            else:
                self.panel.show_error_banner(tr('当前页未检测到可翻译的文字段落。'), use_ocr=use_ocr)
            return

        cached = CachedPageTranslation(extraction=extraction, translations={}, complete=False)
        self.cache.put(self._active_page_key, cached)
        self._start_page_batches(cached)

    def _start_page_batches(self, cached: CachedPageTranslation) -> None:
        cached.error = ''
        self._active_cached = cached
        for seg in cached.extraction.segments:
            if seg.role == 'code' or (seg.cell and not any(ch.isalpha() for ch in seg.text)):
                cached.translations[seg.segment_id] = seg.text
        remaining = [seg for seg in cached.extraction.segments if seg.segment_id not in cached.translations]
        self._pending_batches = group_segments_into_batches(remaining)
        self._total_segments = len(cached.extraction.segments)
        self._refresh_inline_layer()
        self._dispatch_next_batch()

    def _dispatch_next_batch(self) -> None:
        cached = self._active_cached
        if cached is None or self._active_page_index is None:
            return
        page_number = self._active_page_index + 1
        done = len(cached.translations)
        codes = sum(seg.role == 'code' for seg in cached.extraction.segments)
        if not self._pending_batches and not self._page_requests:
            if done != self._total_segments and not cached.error:
                cached.error = tr('模型未按分段格式返回译文，请重试或更换兼容模型。')
            cached.complete = done == self._total_segments and not cached.error
            self.panel.set_busy(False)
            self.panel.render_translations(page_number, cached, self._pinned, show_bilingual=self.config.show_bilingual)
            self._refresh_inline_layer()
            if cached.complete and done == codes:
                self.host.statusBar().showMessage(tr('已保留 {p0} 段代码', p0=str(codes)), 4000)
            elif cached.complete:
                self.host.statusBar().showMessage(
                    tr('第 {p0} 页翻译完成（共 {p1} 段）。', p0=str(page_number), p1=str(done - codes)), 4000)
            elif cached.error:
                self.host.statusBar().showMessage(cached.error, 5000)
            return

        self.panel.render_translations(page_number, cached, self._pinned, show_bilingual=self.config.show_bilingual)
        self.panel.set_busy(True, label=tr('正在翻译第 {p0} 页（{p1}/{p2} 段）…',
                                           p0=str(page_number), p1=str(done - codes), p2=str(self._total_segments - codes)),
                            done=done - codes, total=self._total_segments - codes)
        while self._pending_batches and len(self._page_requests) < 3:
            self._send_page_batch(cached, self._pending_batches.pop(0))

    def _send_page_batch(self, cached, batch):
        # Reserve the slot first: configuration/transport errors can call back
        # synchronously, and a retry on the same page has the same cache key.
        ticket = object()
        self._page_requests[ticket] = None

        def failed(reason):
            if ticket not in self._page_requests:
                return
            del self._page_requests[ticket]
            cached.error = cached.error or reason
            self._pending_batches.clear()
            self._page_pump.start(0)

        def received(mapping, _ms):
            if ticket not in self._page_requests:
                return
            missing = []
            for seg in batch:
                text = mapping.get(seg.segment_id)
                if text and text.strip():
                    cached.translations[seg.segment_id] = text
                else:
                    missing.append(seg)
            if missing and len(batch) == 1:
                failed(tr('模型未按分段格式返回译文，请重试或更换兼容模型。'))
                return
            del self._page_requests[ticket]
            if not cached.error:
                self._pending_batches[0:0] = [[seg] for seg in missing]
            self._page_pump.start(0)

        items = [(seg.segment_id, seg.text) for seg in batch]
        handle = self.client.translate_batch(self.config, items, reference=cached.extraction.reference_for(batch),
                                             on_success=received, on_error=failed)
        if ticket in self._page_requests:
            self._page_requests[ticket] = handle

    def stop_page_translation(self) -> None:
        self._active_cancel.set()
        self._page_pump.stop()
        handles = list(self._page_requests.values())
        self._page_requests.clear()
        self._pending_batches.clear()
        for handle in handles:
            if handle is not None:
                handle.cancel()
        self._active_page_key = None
        self._active_page_index = None
        self._active_cached = None
        self.panel.set_busy(False)

    # --- Inline overlay & bidirectional highlighting ---

    def _peek_original(self, peek):
        if peek and self.host.text_selection.selecting_translation:
            self.host.text_selection.clear()
        self.inline_layer.set_peeking(peek)

    def _switch_original(self, original):
        self.host.text_selection.clear()
        self.inline_layer.set_hidden(original)

    def _current_cached_page(self) -> tuple[int, CachedPageTranslation] | None:
        if not self.host.ws.pages or self.host._in_arrange():
            return None
        page_index = self.host.ws.preview_index()
        if page_index is None or not (0 <= page_index < len(self.host.ws.pages)):
            return None
        cached = self.cache.get(self._page_cache_key(self.host.ws.pages[page_index]))
        if cached is None:
            return None
        return page_index, cached

    def _set_overlay_controls_visible(self, visible: bool, *, layout_fallback: bool = False):
        if not layout_fallback:
            self.inline_layer.reflow.set_page(None)
        self.panel.layout_hint.setVisible(layout_fallback)
        if visible:
            self.page_original.show()
        else:
            self.page_original.reset_view()

    def _refresh_inline_layer(self) -> None:
        pair = self._current_cached_page()
        if pair is None:
            self.inline_layer.clear_overlays()
            self._set_overlay_controls_visible(False)
            return
        page_index, cached = pair
        via_ocr = cached.extraction.via_ocr
        enabled = (self.config.view_mode == 'overlay' and bool(cached.translations)
                   and cached.complete)
        fitted = self.inline_layer.render_page(
            page_index, cached.extraction.segments, cached.translations, enabled=enabled, via_ocr=via_ocr
        )
        if not fitted:
            self.inline_layer.reflow.set_page(cached)
            self.inline_layer.reflow.sync_geometry()
            self.inline_layer._apply_visibility()
        self._set_overlay_controls_visible(bool(self.inline_layer._items) or not fitted, layout_fallback=not fitted)

    def ocr_selection_snapshot(self, ref):
        cached = self.cache.get(self._page_cache_key(ref))
        return cached.extraction.selection_snapshot if cached else None

    def segment_at(self, page_index: int, position: QPoint) -> PageSegment | None:
        if not (self.panel.isVisible() or self.inline_layer._items):
            return None
        if not 0 <= page_index < min(len(self.host.ws.pages), len(self.view.page_rects)):
            return None
        cached = self.cache.get(self._page_cache_key(self.host.ws.pages[page_index]))
        if cached is None:
            return None
        point = self.view.mapToScene(position)
        if page_index == self._preview_page_index:
            visible = self.inline_layer.visible_segment_at(point)
            if visible is not None:
                return visible
        local = point - self.view.page_rects[page_index].topLeft()
        for seg in cached.extraction.segments:
            x0, y0, x1, y1 = seg.bbox
            if x0 <= local.x() <= x1 and y0 <= local.y() <= y1 and seg.segment_id in cached.translations:
                return seg
        return None

    def _highlight_source(self, segment_id, *, ensure_visible=False):
        pair = self._current_cached_page()
        if pair is None or not segment_id:
            self.inline_layer.clear_highlight()
            if self.inline_layer.reflow.isVisible():
                self.inline_layer.reflow.highlight_segments([])
            return
        page_index, cached = pair
        segments = cached.extraction.segments
        hit = next((seg for seg in segments if seg.segment_id == segment_id), None)
        if hit is None:
            self.inline_layer.clear_highlight()
            return
        row = [hit]
        if hit.cell:
            row = [seg for seg in segments if seg.table_id == hit.table_id and seg.cell.row == hit.cell.row]
        if self.inline_layer.reflow.isVisible():
            self.inline_layer.reflow.highlight_segments([seg.segment_id for seg in row], reveal=ensure_visible)
        else:
            self.inline_layer.highlight_segments(page_index, row, ensure_visible=ensure_visible)

    def _on_segment_hovered(self, segment_id: str) -> None:
        self._highlight_source(segment_id)

    def _on_segment_clicked(self, segment_id: str) -> None:
        self.host.text_selection.clear()
        self._linked_segment_id = segment_id
        self._highlight_source(segment_id, ensure_visible=True)
        self.panel.highlight_card(segment_id)

    def activate_page_segment(self, page_index: int, segment: PageSegment) -> None:
        if page_index != self.host.ws.preview_index():
            self.host.go_to_page(page_index + 1, reveal=False)
        self._dismiss_bubble()
        if self._immersive_reading():
            pair = self._current_cached_page()
            if pair is None:
                return
            self._bubble_source = 'page'
            self._show_detail(tr('{p0} · 完整译文', p0=segment.label),
                              tr(self.config.provider_label), '', source_text=segment.text)
            self.bubble.show_result(pair[1].translations[segment.segment_id])
        else:
            self._reveal_sidebar()
            self._linked_segment_id = segment.segment_id
            self._highlight_source(segment.segment_id)
            align_y = 0
            if not self.inline_layer.reflow.isVisible() and 0 <= page_index < len(self.view.page_rects):
                start = self.view.page_rects[page_index].topLeft() + QPointF(*segment.bbox[:2])
                source_top = self.view.viewport().mapToGlobal(self.view.mapFromScene(start))
                align_y = self.panel.scroll.viewport().mapFromGlobal(source_top).y()
            self.panel.highlight_card(segment.segment_id, align_y=align_y)

    # --- Helpers ---

    def _pin_excerpt(self, label: str, source: str, translated: str) -> None:
        self._pinned.insert(0, (label, source, translated))
        del self._pinned[12:]
        if not self.host.act_translation.isChecked():
            self.host.act_translation.setChecked(True)
        else:
            self.sync_page(force=True)

    def _unpin_excerpt(self, index: int) -> None:
        if 0 <= index < len(self._pinned):
            self._pinned.pop(index)
            self.sync_page(force=True)

    def _copy_all_page_translations(self) -> None:
        pair = self._current_cached_page()
        if pair is None:
            return
        _, cached = pair
        parts = []
        for _, group in groupby(cached.extraction.segments, key=lambda seg: seg.table_id or seg.segment_id):
            segments = list(group)
            if not any(seg.segment_id in cached.translations for seg in segments):
                continue
            parts.append(_table_content(segments, cached.translations) if segments[0].cell
                         else cached.translations[segments[0].segment_id])
        if parts:
            QApplication.clipboard().setText('\n\n'.join(parts))
            self.host.statusBar().showMessage(tr('已复制当前页全部译文（{p0} 段）。', p0=str(len(parts))), 3500)

    def _open_ocr_installer(self) -> None:
        from app.ocrui import install_local_runtime

        self._dismiss_bubble()
        if install_local_runtime(self.host):
            self.sync_page(force=True)

    def _is_ocr_installed(self) -> bool:
        try:
            from app.ocr.contracts import OCRError
            from app.ocr.model_manager import default_runtime_dir

            return (default_runtime_dir() / 'manifest.json').is_file()
        except (OCRError, OSError):
            return False

    def _run_worker(self, fn: Callable, cancel: Event, callback: Callable) -> None:
        self._job_serial += 1
        serial = self._job_serial
        self._worker_callbacks[serial] = (cancel, callback)
        QThreadPool.globalInstance().start(_ExtractionJob(serial, fn, cancel, self._worker_signals))

    def _on_worker_done(self, serial: int, result, exc) -> None:
        entry = self._worker_callbacks.pop(serial, None)
        if entry is None:
            return
        cancel, callback = entry
        if not cancel.is_set() and not getattr(self.host, '_closing', False):
            callback(result, exc)

    def shutdown(self) -> None:
        self.view.removeEventFilter(self)
        self.view.viewport().removeEventFilter(self)
        try:
            theme_manager().changed.disconnect(self._on_theme_changed)
        except (TypeError, RuntimeError):
            pass
        self.page_original.reset_view()
        self.stop_page_translation()
        self._dismiss_bubble()
        self.client.cancel_all()
        self.region_tool.cancel()
        self.mini_bar.hide()
        self.inline_layer.clear_all()
        self.cache.clear()
        self._worker_callbacks.clear()
