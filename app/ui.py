from __future__ import annotations

import ctypes
import math
import os
import sys
from collections import OrderedDict
from dataclasses import dataclass, replace
from itertools import count
from pathlib import Path
from bisect import bisect_left, bisect_right

from PyQt6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QEvent,
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
    QObject,
    QPoint,
    QPointF,
    QRectF,
    Qt,
    QSettings,
    QSize,
    QThreadPool,
    QThread,
    QTimer,
    QMimeData,
    QRect,
    pyqtSignal,
    QVariantAnimation,
    QEasingCurve,
)
from PyQt6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QPixmap,
    QShortcut,
    QTransform,
    QUndoGroup,
    QWheelEvent,
    QDrag,
)
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QMainWindow,
    QMenu,
    QMenuBar,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QStyle,
    QStyledItemDelegate,
    QSizePolicy,
    QToolButton,
    QToolBox,
    QVBoxLayout,
    QWidget,
)

from app import APP_NAME, APP_EXE_NAME, APP_ORGANIZATION, APP_SLUG, __version__
from app.icons import brand_icon, toolbar_icon
from app.progress import TaskProgressDialog, run_operation
from app.output import OperationCancelled
from app.pdfio import (NeedPassword, PdfError, PdfStore, WriteNeedsConfirmation,
                       WriteOptions, apply_output_conflicts, is_encrypted_file)
from app.workspace import Workspace, parse_page_range, parse_range_segments
from app.startup import mark
from app.storage import TemporaryWorkspace, export_path, output_directory
from app.rendering import RenderResult, RenderScheduler, RenderSignals

THUMB_W = 96
THUMB_MIN_W = 64
THUMB_RENDER_STEP = 64
THUMB_CACHE_BYTES = 128 * 1024 * 1024
READING_CACHE_BYTES = 64 * 1024 * 1024
THUMB_RATIO_DEFAULT = 1.35
THUMB_RATIO_MIN = 0.3
THUMB_RATIO_MAX = 3.0
THUMB_MIN_H = 28
THUMB_ITEM_W_PAD = 12
THUMB_ITEM_H_PAD = 36
THUMB_LIST_SPACING = 8
# 一条缩略图横向要占的地：条目比缩略图框宽出的部分，加上左右两份间距。
THUMB_PAD = THUMB_ITEM_W_PAD + 2 * THUMB_LIST_SPACING
PREVIEW_MAX_W = 4000
PREVIEW_REUSE_RATIO = 2.0
PREVIEW_UPSCALE_SLACK = 1.1
READING_SETTLE_MS = 120
READING_DRAFT_W = 640
RENDER_SHOW_DELAY_MS = 200
RENDER_HIDE_DELAY_MS = 120
ZOOM_MIN = 0.1
ZOOM_MAX = 64.0
ZOOM_STEP = 1.25
PDF_LOGICAL_SCALE = 96 / 72
SPREAD_MIN_WIDTH = 720
SPREAD_WIDTH_HYSTERESIS = 32
ARRANGE_ZOOM_MIN = 0.5
ARRANGE_ZOOM_MAX = 10.0
ARRANGE_ZOOM_STEP = 1.25
WHEEL_ZOOM_UNIT = 120
RESIZE_BORDER = 6


def resize_hit_test(x: int, y: int, width: int, height: int, border: int) -> int:
    """返回 Windows 缩放边缘编码；参数必须使用同一种像素坐标。"""
    if not (0 <= x < width and 0 <= y < height):
        return 0
    left, right = x < border, x >= width - border
    top, bottom = y < border, y >= height - border
    if top and left:
        return 13
    if top and right:
        return 14
    if bottom and left:
        return 16
    if bottom and right:
        return 17
    if left:
        return 10
    if right:
        return 11
    if top:
        return 12
    if bottom:
        return 15
    return 0
MIME = "application/x-pdfpage-index"
ICON_BTN = 40
ICON_PX = 22
# 标题栏按钮的字形只占 24px 画布的中间一半，名义尺寸要和工具栏图标同级，
# 落到屏幕上才是系统窗口按钮那种约 10px 的笔画。
CAPTION_PX = 22
WM_MOUSEMOVE = 0x0200
WM_NCHITTEST = 0x0084
WM_NCMOUSEMOVE = 0x00A0
WM_NCLBUTTONDOWN = 0x00A1
WM_NCLBUTTONUP = 0x00A2
WM_NCLBUTTONDBLCLK = 0x00A3
HTMAXBUTTON = 9


def sidebar_thumb_width(view_w: int, scrollbar_w: int) -> int:
    """侧栏缩略图框的宽度。

    宽度必须钉在视图控件宽度上，并且预留竖向滚动条的位置，原因见下：
    条目高度取决于缩略图宽度，而滚动条要不要出现又取决于所有条目加起来有多高。
    如果拿 viewport().width() 当依据，滚动条一出现 viewport 就变窄 → 缩略图变矮 →
    内容变矮 → 滚动条又消失 → 缩略图变回去，两边互相追，整个侧栏就一直在抖。
    用控件宽度就断掉了这个环；再减掉滚动条宽度，条目在滚动条出现时也放得下，
    不会另外冒出一条横向滚动条。
    """
    return max(THUMB_MIN_W, int(view_w) - int(scrollbar_w) - THUMB_PAD)


def thumb_ratio(width_pt: float, height_pt: float) -> float:
    """缩略图框的高宽比，横页出横卡片、竖页出竖卡片。取不到尺寸时用兜底比例。"""
    if width_pt <= 0 or height_pt <= 0:
        return THUMB_RATIO_DEFAULT
    return max(THUMB_RATIO_MIN, min(THUMB_RATIO_MAX, height_pt / width_pt))


def wheel_zoom_steps(acc: int, delta: int) -> tuple[int, int]:
    """滚轮角度累积成整数步长，返回 (剩余角度, 步数)；触控板的小幅滚动也走这里。"""
    acc += delta
    steps = 0
    while acc >= WHEEL_ZOOM_UNIT:
        acc -= WHEEL_ZOOM_UNIT
        steps += 1
    while acc <= -WHEEL_ZOOM_UNIT:
        acc += WHEEL_ZOOM_UNIT
        steps -= 1
    return acc, steps


def zoom_by_steps(zoom: float, steps: int, step: float) -> float:
    for _ in range(abs(steps)):
        zoom = zoom * step if steps > 0 else zoom / step
    return zoom


def pixmap_bytes(pix: QPixmap) -> int:
    return pix.width() * pix.height() * 4

STYLESHEET = """
QMainWindow {
    background: #F3F6F5;
    color: #134E4A;
    border: 1px solid #C5D5D2;
}
QMainWindow[maximized="true"] { border: none; }
QMainWindow, QWidget {
    background: #F3F6F5;
    color: #134E4A;
    font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
    font-size: 14px;
}
QFrame#card {
    background: #FFFFFF;
    border: 1px solid #E2EEEC;
    border-radius: 12px;
}
QLabel#hint {
    color: #4D6B67;
    font-size: 14px;
}
QLabel, QRadioButton { background: transparent; }
QLabel#documentName { font-size: 16px; font-weight: 600; }
QLabel#saveState { color: #4D6B67; padding: 4px 8px; }
QLabel#welcomeTitle { font-size: 26px; font-weight: 600; }
QSpinBox {
    background: #FFFFFF; border: 1px solid #D7E8E4;
    border-radius: 6px; padding: 6px; min-height: 24px;
}
QSpinBox:focus { border: 2px solid #0D9488; }
QToolBox { background: transparent; }
QToolBox::tab {
    background: #F3F6F5; border-radius: 6px;
    min-height: 32px; padding: 4px 8px; font-weight: 600;
}
QToolBox::tab:selected { background: #E1F3EF; color: #134E4A; }
QScrollArea { background: transparent; border: none; }
QLabel#caption {
    color: #134E4A;
    font-size: 14px;
    font-weight: 600;
    padding: 2px 0 4px 0;
}
QLabel#empty {
    color: #4D6B67;
    font-size: 16px;
}
QLabel#windowTitle {
    color: #134E4A;
    font-size: 13px;
    font-weight: 600;
    padding: 0 12px;
}
QPushButton {
    min-height: 30px;
    padding: 6px 14px;
    background: #FFFFFF;
    border: 1px solid #D7E8E4;
    border-radius: 10px;
    font-size: 14px;
}
QFrame#workbench {
    background: #FFFFFF; border: 1px solid #E2EEEC; border-radius: 12px;
}
QFrame#workbench QPushButton { min-height: 26px; padding: 4px 12px; border-radius: 7px; }
QFrame#modeSwitch {
    background: #EEF3F0; border: 1px solid #CBDCD4; border-radius: 9px;
}
QFrame#modeSwitch QToolButton {
    background: transparent; border: 1px solid transparent; border-radius: 6px;
}
QFrame#modeSwitch QToolButton:hover { background: #DDE9E2; }
QFrame#modeSwitch QToolButton:checked { background: #0F766E; border-color: #0F766E; }
QFrame#modeSwitch QToolButton:checked:hover { background: #115E59; }
QFrame#modeSwitch QToolButton:focus { border: 2px solid #134E4A; }
QFrame#modeSwitch QToolButton:checked:focus { border: 2px dotted #FFFFFF; }
QPushButton:hover { background: #F0FDFA; }
QPushButton:pressed { background: #CCFBF1; }
QPushButton:focus { border: 2px solid #0D9488; }
QPushButton:disabled { color: #94A3B8; border-color: #E5EEEC; }
QPushButton#primary, QPushButton[primary="true"] {
    background: #0F766E;
    color: #FFFFFF;
    border: 1px solid #0F766E;
    font-weight: 600;
}
QPushButton#primary:hover, QPushButton[primary="true"]:hover { background: #0D9488; border-color: #0D9488; }
QPushButton#primary:pressed, QPushButton[primary="true"]:pressed { background: #115E59; border-color: #115E59; }
QPushButton#primary:disabled, QPushButton[primary="true"]:disabled {
    background: #94A3B8;
    border-color: #94A3B8;
    color: #F1F5F9;
}
QPushButton[danger="true"] { color: #B91C1C; border-color: #FECACA; }
QPushButton[danger="true"]:hover { background: #FEF2F2; }
QSpinBox, QDoubleSpinBox {
    background: #FFFFFF;
    border: 1px solid #D7E8E4;
    border-radius: 6px;
    padding: 2px 4px;
    min-height: 24px;
    color: #134E4A;
}
QSpinBox:focus, QDoubleSpinBox:focus { border: 1.5px solid #0D9488; }
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    width: 14px;
    border: none;
    background: transparent;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
    background: #E1F3EF;
}
QComboBox {
    min-height: 32px;
    padding: 4px 10px;
    background: #FFFFFF;
    border: 1px solid #D7E8E4;
    border-radius: 8px;
    color: #134E4A;
    font-size: 13px;
}
QComboBox:hover { border-color: #0D9488; background: #F0FDFA; }
QComboBox:focus { border: 2px solid #0D9488; }
QComboBox:disabled { color: #94A3B8; background: #F8FBFA; border-color: #E2EEEC; }
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 24px;
    border-left: none;
}
QComboBox QAbstractItemView {
    background: #FFFFFF;
    border: 1px solid #D7E8E4;
    border-radius: 8px;
    padding: 4px;
    selection-background-color: #CCFBF1;
    selection-color: #134E4A;
    outline: none;
}
QCheckBox {
    spacing: 8px;
    color: #134E4A;
    font-size: 14px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 1px solid #C5D5D2;
    border-radius: 4px;
    background: #FFFFFF;
}
QCheckBox::indicator:hover {
    border-color: #0D9488;
    background: #F0FDFA;
}
QCheckBox::indicator:checked {
    border-color: #0F766E;
    background: #0F766E;
}
QTreeWidget {
    background: #FFFFFF;
    border: 1px solid #E2EEEC;
    border-radius: 10px;
    padding: 4px;
    color: #134E4A;
    outline: none;
}
QTreeWidget::item {
    height: 30px;
    padding: 2px 6px;
    border-radius: 6px;
}
QTreeWidget::item:hover {
    background: #F0FDFA;
    color: #0F766E;
}
QTreeWidget::item:selected {
    background: #CCFBF1;
    color: #0F766E;
    font-weight: 600;
}
QHeaderView::section {
    background: #F8FBFA;
    color: #4D6B67;
    font-size: 12px;
    font-weight: 600;
    border: none;
    border-bottom: 1px solid #E2EEEC;
    padding: 5px 8px;
}
QTableWidget {
    background: #FFFFFF;
    border: 1px solid #E2EEEC;
    border-radius: 10px;
    gridline-color: #F3F6F5;
    color: #134E4A;
    outline: none;
}
QTableWidget::item {
    padding: 6px 10px;
    border-bottom: 1px solid #F8FBFA;
}
QTableWidget::item:selected {
    background: #CCFBF1;
    color: #0F766E;
}
QTableWidget::item:hover {
    background: #F0FDFA;
}
QListWidget {
    background: #FFFFFF;
    border: 1px solid #E2EEEC;
    border-radius: 10px;
    padding: 6px;
    color: #134E4A;
    outline: none;
}
QListWidget::item {
    height: 32px;
    padding: 4px 8px;
    border-radius: 6px;
}
QListWidget::item:hover { background: #F0FDFA; }
QListWidget::item:selected { background: #CCFBF1; color: #0F766E; font-weight: 600; }
QScrollBar:vertical {
    border: none;
    background: transparent;
    width: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #D7E8E4;
    min-height: 24px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background: #99BDB7;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar:horizontal {
    border: none;
    background: transparent;
    height: 8px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: #D7E8E4;
    min-width: 24px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal:hover {
    background: #99BDB7;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}
QDialog {
    background: #F3F6F5;
}
QFrame#annotationBar {
    background: #F7FAF9;
    border: none;
    border-bottom: 1px solid #E2EEEC;
    border-radius: 0;
}
QFrame#annotationBar QToolButton {
    min-width: 26px;
    min-height: 30px;
    padding: 0 3px;
    color: #344D42;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    font-size: 13px;
}
QFrame#annotationBar QToolButton:hover { background: #EAF1EE; }
QFrame#annotationBar QToolButton:pressed { background: #DCECE4; }
QFrame#annotationBar QToolButton:checked {
    color: #0F6557; background: #E4F2ED; border-color: #BDD8CA; font-weight: 600;
}
QFrame#annotationBar QToolButton:focus { border-color: #0D9488; }
QFrame#annotationBar QToolButton#qt_toolbar_ext_button { min-width: 24px; padding: 0; }
QToolBar#reviewTools { background: transparent; border: 1px solid transparent; padding: 0; spacing: 2px; }
QToolBar#reviewTools::separator { background: #DCE5DF; width: 1px; margin: 8px 6px; }
QToolBar#reviewTools QMenu::item:checked { color: #0F6557; background: #E4F2ED; font-weight: 600; }
QFrame#annotationBar QDoubleSpinBox {
    min-height: 28px;
    padding: 0 7px;
    font-size: 12px;
    background: #FFFFFF;
    border: 1px solid #D7E3DF;
    border-radius: 6px;
}
QFrame#annotationBar QComboBox {
    min-height: 28px;
    max-height: 30px;
    padding: 2px 26px 2px 8px;
    font-size: 13px;
    border-radius: 6px;
}
QFrame#annotationBar QPushButton {
    min-height: 28px;
    max-height: 30px;
    padding: 2px 10px;
    font-size: 13px;
    border-radius: 6px;
}
QFrame#annotationBar QPushButton:disabled {
    color: #94A3B8;
    background: transparent;
    border: 1px solid #E2EEEC;
}
QToolButton {
    min-width: 40px;
    min-height: 40px;
    padding: 0;
    color: #0F766E;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 10px;
}
QToolButton:hover { background: #E6F7F4; }
QToolButton:pressed { background: #CCFBF1; }
QToolButton:checked { background: #CCFBF1; }
QToolButton:focus { border: 2px solid #0D9488; }
QToolButton:disabled { color: #94A3B8; }
QToolButton[primary="true"] {
    color: #FFFFFF;
    background: #0F766E;
    border: 1px solid #0F766E;
}
QToolButton[primary="true"]:hover { background: #0D9488; }
QToolButton[primary="true"]:pressed { background: #0F766E; }
QToolButton[danger="true"] { color: #B91C1C; }
QToolButton[danger="true"]:hover { background: #FEF2F2; }
QToolButton#winBtn {
    min-width: 46px;
    min-height: 40px;
    border: none;
    border-radius: 0;
    background: transparent;
}
QToolButton#winBtn:hover { background: #E6F7F4; }
QToolButton#winBtn[captionHover="true"] { background: #E6F7F4; }
QToolButton#winBtn:pressed { background: #CCFBF1; }
QToolButton#winBtn:focus { border: none; }
QToolButton#winClose {
    min-width: 46px;
    min-height: 40px;
    border: none;
    border-radius: 0;
    background: transparent;
}
QToolButton#winClose:hover { background: #E81123; }
QToolButton#winClose:pressed { background: #C50F1F; }
QToolButton#winClose:focus { border: none; }
QToolTip {
    background: #134E4A;
    color: #FFFFFF;
    border: none;
    padding: 6px 10px;
    font-size: 13px;
}
QLineEdit {
    min-height: 36px;
    padding: 6px 10px;
    background: #F8FBFA;
    border: 1px solid #D7E8E4;
    border-radius: 10px;
    font-size: 14px;
}
QLineEdit:focus {
    background: #FFFFFF;
    border: 2px solid #0D9488;
}
QRadioButton {
    spacing: 8px;
    color: #134E4A;
    font-size: 14px;
}
QRadioButton::indicator {
    width: 15px;
    height: 15px;
    border: 1.5px solid #99B2AC;
    border-radius: 8px;
    background: #FFFFFF;
}
QRadioButton::indicator:hover {
    border-color: #0D9488;
    background: #F0FDFA;
}
QRadioButton::indicator:checked {
    border: 2px solid #0F766E;
    border-radius: 8px;
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 #0F766E, stop:0.48 #0F766E, stop:0.52 #FFFFFF, stop:1 #FFFFFF);
}
QWidget#splitPanel, QWidget#splitPanel QWidget {
    font-family: "Microsoft YaHei UI";
}
QWidget#splitPanel QRadioButton {
    min-height: 36px;
    spacing: 10px;
    color: #334D49;
}
QWidget#splitPanel QRadioButton:checked {
    color: #134E4A;
    font-weight: 600;
}
QWidget#splitPanel QRadioButton::indicator {
    width: 14px;
    height: 14px;
    border: 2px solid #99B2AC;
    border-radius: 9px;
}
QWidget#splitPanel QRadioButton::indicator:hover {
    border-color: #0F766E;
}
QWidget#splitPanel QRadioButton::indicator:checked {
    border-color: #0F766E;
}
QWidget#splitPanel QSpinBox {
    min-height: 26px;
    padding: 2px 6px;
    border-radius: 8px;
    border-color: #C5D5D2;
}
QWidget#splitPanel QSpinBox:focus {
    border-color: #0F766E;
}
QWidget#splitPanel QLineEdit {
    min-height: 26px;
    border-radius: 8px;
}
QWidget#splitPanel QLineEdit:focus {
    border-color: #0F766E;
}
QLabel#splitFeedback {
    color: #4D6B67;
    font-size: 13px;
    font-weight: 400;
    padding: 0;
}
QPushButton#splitSubmit {
    min-height: 28px;
    padding: 6px 12px;
    border-radius: 8px;
}
QPushButton#splitSubmit:hover:enabled {
    background: #0B6F68;
    border-color: #0B6F68;
}
QPushButton#splitSubmit:pressed:enabled {
    background: #115E59;
    border-color: #115E59;
}
QListView {
    background: transparent;
    border: none;
    outline: none;
}
QListView::item:selected { background: transparent; color: #134E4A; }
QListView::item:hover { background: transparent; }
QListView::item:focus { border: none; }
QStatusBar {
    background: #F3F6F5;
    color: #4D6B67;
    border: none;
    font-size: 13px;
}
QSplitter::handle { background: transparent; }
QSplitter::handle:hover { background: #D1FAE5; }
QWidget#titleBar {
    background: #F3F6F5;
    border: none;
    border-bottom: 1px solid #E2EEEC;
}
QMenuBar {
    background: #F3F6F5;
    color: #134E4A;
    spacing: 0;
    padding: 0 4px;
    font-size: 14px;
}
QMenuBar::item {
    padding: 6px 10px;
    background: transparent;
    border-radius: 8px;
}
QMenuBar::item:selected,
QMenuBar::item:pressed {
    background: #CCFBF1;
    color: #134E4A;
}
QMenu {
    background: #FFFFFF;
    color: #134E4A;
    border: 1px solid #E2EEEC;
    padding: 6px;
    border-radius: 10px;
}
QMenu::item {
    padding: 8px 28px 8px 16px;
    background: transparent;
    border-radius: 8px;
}
QMenu::item:selected {
    background: #CCFBF1;
    color: #134E4A;
}
QMenu::item:disabled {
    color: #94A3B8;
    background: transparent;
}
QMenu::indicator:checked { color: #0F766E; }
QMenu::separator {
    height: 1px;
    background: #E2EEEC;
    margin: 4px 10px;
}
QFrame#documentTabBar {
    background: #EEF4F2;
    border: none;
    border-bottom: 1px solid #DCE8E5;
}
QFrame#docTabPill {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
}
QFrame#docTabPill:hover {
    background: #E2EEEB;
}
QFrame#docTabPill[active="true"] {
    background: #FFFFFF;
    border: 1px solid #C5D8D5;
}
QFrame#docTabPill[dropTarget="true"] {
    background: #F0FDFA;
    border: 1.5px dashed #0D9488;
}
QLabel#docTabTitle {
    color: #4D6B67;
    font-size: 12px;
    padding: 0 2px;
    background: transparent;
}
QLabel#docTabTitle[active="true"] {
    color: #134E4A;
    font-weight: 600;
}
QToolButton#docTabClose {
    min-width: 18px;
    max-width: 18px;
    min-height: 18px;
    max-height: 18px;
    padding: 0;
    border: none;
    border-radius: 4px;
    background: transparent;
}
QToolButton#docTabClose:hover {
    background: #DDECE8;
}
QToolButton#docTabNew {
    min-width: 24px;
    max-width: 24px;
    min-height: 24px;
    max-height: 24px;
    padding: 0;
    border: 1px solid transparent;
    border-radius: 6px;
    background: transparent;
}
QToolButton#docTabNew:hover {
    background: #E2EEEB;
}
"""


def _pdf_paths_from_mime(mime: QMimeData) -> list[str]:
    if not mime.hasUrls():
        return []
    return [
        u.toLocalFile()
        for u in mime.urls()
        if u.toLocalFile().lower().endswith((".pdf", ".ofd"))
    ]


class PageThumbDelegate(QStyledItemDelegate):
    """选中：青绿描边 + 角标；未选：淡边框并把缩略图压暗。"""

    def paint(self, painter, option, index) -> None:
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        focused = bool(option.state & QStyle.StateFlag.State_HasFocus)
        rect = option.rect.adjusted(3, 3, -3, -3)
        if rect.width() < 8 or rect.height() < 8:
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        if selected:
            painter.setPen(QPen(QColor("#0F766E"), 2))
            painter.setBrush(QColor("#F0FDFA"))
        elif hover:
            painter.setPen(QPen(QColor("#5EEAD4"), 2))
            painter.setBrush(QColor("#F0FDFA"))
        else:
            painter.setPen(QPen(QColor("#E1E7EB"), 1))
            painter.setBrush(QColor("#FFFFFF"))
        painter.drawRoundedRect(QRectF(rect), 10, 10)

        label_h = 22
        icon_area = QRect(
            rect.x() + 8,
            rect.y() + 8,
            max(1, rect.width() - 16),
            max(1, rect.height() - 16 - label_h),
        )
        pix = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(pix, QPixmap) and not pix.isNull():
            dpr = pix.devicePixelRatioF() or 1.0
            pw = pix.width() / dpr
            ph = pix.height() / dpr
            scale = min(icon_area.width() / pw, icon_area.height() / ph)
            dw, dh = pw * scale, ph * scale
            target = QRectF(
                icon_area.x() + (icon_area.width() - dw) / 2,
                icon_area.y() + (icon_area.height() - dh) / 2,
                dw,
                dh,
            )
            painter.drawPixmap(target, pix, QRectF(pix.rect()))
            painter.setOpacity(1.0)

        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        font = QFont(option.font)
        font.setPixelSize(12)
        font.setBold(selected)
        painter.setFont(font)
        painter.setPen(QColor("#0F766E") if selected else QColor("#4D6B67"))
        label_rect = QRect(rect.x() + 4, rect.bottom() - label_h, rect.width() - 8, label_h)
        painter.drawText(
            label_rect,
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
            text,
        )

        if selected:
            r = max(7.0, min(10.0, rect.width() / 10))
            cx = rect.right() - 12
            cy = rect.top() + 12
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#0F766E"))
            painter.drawEllipse(QPointF(cx, cy), r, r)
            pen = QPen(QColor("#FFFFFF"), 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            mark = QPainterPath()
            mark.moveTo(cx - r * 0.45, cy)
            mark.lineTo(cx - r * 0.12, cy + r * 0.38)
            mark.lineTo(cx + r * 0.48, cy - r * 0.35)
            painter.drawPath(mark)
        elif focused:
            painter.setPen(QPen(QColor("#94A3B8"), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(rect.adjusted(4, 4, -4, -4)), 8, 8)

        painter.restore()

    def sizeHint(self, option, index):
        hint = index.data(Qt.ItemDataRole.SizeHintRole)
        return hint if isinstance(hint, QSize) else super().sizeHint(option, index)


class PageListView(QListView):
    zoom_steps = pyqtSignal(int)

    def __init__(self, arrange: bool, parent=None) -> None:
        super().__init__(parent)
        self._arrange = arrange
        self._host = None
        self._acc = 0
        self._drop_row: int | None = None
        self._highlight_rect = QRectF()
        self._highlight_opacity = 1.0
        self._drop_motion = QVariantAnimation(self)
        self._drop_motion.setDuration(140)
        self._drop_motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._drop_motion.valueChanged.connect(self._move_highlight)
        self._drop_fade = QVariantAnimation(self)
        self._drop_fade.setDuration(240)
        self._drop_fade.valueChanged.connect(self._fade_highlight)
        self._drop_fade.finished.connect(self._clear_highlight)
        self.verticalScrollBar().valueChanged.connect(self._relocate_highlight)
        self.horizontalScrollBar().valueChanged.connect(self._relocate_highlight)
        # 网格槽位可以统一，但卡片必须保留各页尺寸；横竖混排时不能复用某一页的高度。
        self.setUniformItemSizes(False)
        self.setIconSize(QSize(THUMB_W, int(THUMB_W * THUMB_RATIO_DEFAULT)))
        self.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self.setItemDelegate(PageThumbDelegate(self))
        self.setMovement(QListView.Movement.Static)
        if arrange:
            self.setViewMode(QListView.ViewMode.IconMode)
            self.setFlow(QListView.Flow.LeftToRight)
            self.setWrapping(True)
            self.setResizeMode(QListView.ResizeMode.Adjust)
            self.setSpacing(12)
            self.setGridSize(QSize())
        else:
            self.setViewMode(QListView.ViewMode.IconMode)
            self.setFlow(QListView.Flow.TopToBottom)
            self.setWrapping(False)
            self.setResizeMode(QListView.ResizeMode.Adjust)
            self.setSpacing(THUMB_LIST_SPACING)
            # 竖条常驻：viewport 宽度就不会随内容变，缩略图宽度也就定得死。宽度里已经
            # 留出滚动条的位置，条目正好占满，不会再多出一条横向滚动条。
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        # 落点由 paintEvent 自己画：Qt 自带的指示器只在 dragMoveEvent 交给基类
        # 处理时才会更新，而这里的落点规则是自己定的。
        self.setDropIndicatorShown(False)
        self.setDragDropMode(QListView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def _drop_row_at(self, pos) -> int:
        """落点格：拖到空白处按「落到末尾」算，与 drop_target 的口径一致。"""
        hit = self.indexAt(pos)
        return hit.row() if hit.isValid() else self.model().rowCount() - 1

    def _set_drop_row(self, row: int | None) -> None:
        if row == self._drop_row:
            return
        self._drop_row = row
        self._drop_motion.stop()
        self._drop_fade.stop()
        self._highlight_opacity = 1.0
        if row is None or row < 0 or self.model() is None or row >= self.model().rowCount():
            self._clear_highlight()
            return
        target = QRectF(self.visualRect(self.model().index(row, 0)))
        start = self._highlight_rect if not self._highlight_rect.isEmpty() else target.adjusted(5, 5, -5, -5)
        self._drop_motion.setStartValue(start)
        self._drop_motion.setEndValue(target)
        self._drop_motion.start()

    def _move_highlight(self, rect) -> None:
        if not self.isVisible():
            return
        dirty = self._highlight_rect.united(rect).adjusted(-5, -5, 5, 5)
        self._highlight_rect = QRectF(rect)
        self.viewport().update(dirty.toAlignedRect())

    def _fade_highlight(self, opacity) -> None:
        if not self.isVisible():
            return
        self._highlight_opacity = float(opacity)
        self.viewport().update(self._highlight_rect.adjusted(-5, -5, 5, 5).toAlignedRect())

    def _clear_highlight(self) -> None:
        old = self._highlight_rect
        self._highlight_rect = QRectF()
        if self.isVisible():
            self.viewport().update(old.adjusted(-5, -5, 5, 5).toAlignedRect())

    def _relocate_highlight(self) -> None:
        if self._drop_row is not None and self.model() is not None and 0 <= self._drop_row < self.model().rowCount():
            self._drop_motion.stop()
            self._move_highlight(QRectF(self.visualRect(self.model().index(self._drop_row, 0))))

    def _show_landing(self, row) -> None:
        if not self.isVisible():
            return
        index = self.model().index(row, 0)
        if not index.isValid():
            return
        self.scrollTo(index)
        self._move_highlight(QRectF(self.visualRect(index)))
        self._drop_fade.setStartValue(1.0)
        self._drop_fade.setEndValue(0.0)
        self._drop_fade.start()

    def startDrag(self, supportedActions) -> None:
        indexes = self.selectedIndexes()
        index = self.currentIndex()
        if not indexes:
            return
        if index not in indexes:
            index = indexes[0]
        # 抓取已显示的缩略图，不为拖动重新解析或渲染 PDF。
        rect = self.visualRect(index).intersected(self.viewport().rect())
        if rect.isEmpty():
            return
        pix = self.viewport().grab(rect)
        if pix.width() > 56 or pix.height() > 72:
            pix = pix.scaled(56, 72, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        drag = QDrag(self)
        drag.setMimeData(self.model().mimeData(indexes))
        drag.setPixmap(pix)
        drag.setHotSpot(QPoint(pix.width() // 2, pix.height() // 2))
        self.model().drag_source_ws = self.model().ws
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self._set_drop_row(None)
            drag.deleteLater()

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME):
            self._set_drop_row(self._drop_row_at(event.position().toPoint()))
            event.acceptProposedAction()
            return
        if _pdf_paths_from_mime(event.mimeData()):
            model = self.model()
            if model is not None and not model.store.operation_reason(model.ws.pages, "modify"):
                self._set_drop_row(self._drop_row_at(event.position().toPoint()))
                event.acceptProposedAction()
                return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME):
            self._set_drop_row(self._drop_row_at(event.position().toPoint()))
            event.acceptProposedAction()
            return
        if _pdf_paths_from_mime(event.mimeData()):
            model = self.model()
            if model is not None and not model.store.operation_reason(model.ws.pages, "modify"):
                self._set_drop_row(self._drop_row_at(event.position().toPoint()))
                event.acceptProposedAction()
                return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._set_drop_row(None)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        model = self.model()
        pdf_paths = _pdf_paths_from_mime(event.mimeData())
        if pdf_paths:
            pos = event.position().toPoint()
            hit = self.indexAt(pos)
            at_index = hit.row() if hit.isValid() else (model.rowCount() if model is not None else 0)
            self._set_drop_row(None)
            host = self._host or self.window()
            if hasattr(host, "insert_dropped_files") and host.insert_dropped_files(
                pdf_paths, at_index=at_index, target_view=self
            ):
                event.acceptProposedAction()
                return
            event.ignore()
            return
        if not event.mimeData().hasFormat(MIME):
            event.ignore()
            return
        if model.store.operation_reason(model.ws.pages, "modify"):
            self._set_drop_row(None)
            event.ignore()
            return
        try:
            sources = [int(row) for row in bytes(event.mimeData().data(MIME)).decode("utf-8").split(",")]
        except (ValueError, UnicodeDecodeError):
            self._set_drop_row(None)
            event.ignore()
            return
        pos = event.position().toPoint()
        hit = self.indexAt(pos)
        dest = hit.row() if hit.isValid() else -1
        count = model.rowCount()
        self._set_drop_row(None)
        source_ws = getattr(model, "drag_source_ws", None) or model.ws
        model.drag_source_ws = None
        if source_ws is not model.ws:
            at_index = hit.row() if hit.isValid() else count
            host = self._host or self.window()
            if hasattr(host, "insert_pages_from_workspace") and host.insert_pages_from_workspace(
                source_ws, sources, at_index=at_index, target_view=self
            ):
                event.acceptProposedAction()
                return
            event.ignore()
            return
        if not sources or any(row < 0 or row >= count for row in sources):
            event.ignore()
            return
        model.ws.move_pages(sources, dest)
        event.acceptProposedAction()
        model.reordered.emit()
        landing = model.ws.selected_index
        if landing is not None:
            QTimer.singleShot(0, lambda: self._show_landing(landing))

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        rect = self._highlight_rect
        if rect.isEmpty():
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(self._highlight_opacity)
        painter.setPen(QPen(QColor("#0D9488"), 2))
        painter.setBrush(QColor(13, 148, 136, 56))
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 8, 8)
        painter.setPen(QPen(QColor("#0D9488"), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(rect.left() + 4, rect.top() + 12), QPointF(rect.left() + 4, rect.bottom() - 12))
        painter.end()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._arrange and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._acc, steps = wheel_zoom_steps(self._acc, event.angleDelta().y())
            if steps:
                self.zoom_steps.emit(steps)
            event.accept()
            return
        super().wheelEvent(event)


class PreviewView(QGraphicsView):
    zoom_steps = pyqtSignal(int)
    page_step = pyqtSignal(int)
    view_changed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._acc = 0
        self._scene = QGraphicsScene(self)
        self.page_rects = []
        self._page_tops = []
        self._page_bottoms = []
        self._rows = []
        self._row_rects = []
        self._page_rows = []
        self.spread = False
        self.page_columns = 1
        self._visibility_signature = None
        self._page_scroll_acc = 0
        self.layout_generation = 0
        self.page_signature = None
        self.active_page = 0
        self.neighbors = {}
        self.search_areas = {}
        self.current_search = -1
        self._item = QGraphicsPixmapItem()
        self._scene.addItem(self._item)
        self._detail = QGraphicsPixmapItem()
        self._detail.setZValue(1)
        self._scene.addItem(self._detail)
        self._other_details = {}
        self.setScene(self._scene)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setBackgroundBrush(QColor("#F4F7F6"))
        # 稳态下位图恰好是 1:1 设备像素，此提示只在缩放过渡期间生效，
        # 让等待重渲染的这段时间不再是锯齿块。
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setCacheMode(QGraphicsView.CacheModeFlag.CacheBackground)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setAcceptDrops(False)
        self.viewport().setAcceptDrops(False)
        self.horizontalScrollBar().valueChanged.connect(self.view_changed)
        self.verticalScrollBar().valueChanged.connect(self.view_changed)

    def photo(self) -> QPixmap:
        return self._item.pixmap()

    def set_pages(self, signature, sizes, *, spread=False, columns=None) -> bool:
        columns = columns if columns is not None else (2 if spread else 1)
        if signature == self.page_signature and columns == self.page_columns:
            return False
        self.page_signature = signature
        self.page_columns = columns
        self.spread = spread = columns > 1
        self.layout_generation += 1
        self.clear_photo()
        for item in self.neighbors.values():
            self._scene.removeItem(item)
        self.neighbors.clear()
        self.page_rects = []
        self._rows = []
        self._row_rects = []
        self._page_rows = []
        width = max((w for w, h in sizes), default=0)
        if columns == 4:
            cell_width = width
            cell_height = max((h for w, h in sizes), default=0)
            width = cell_width * 2 + 12 if sizes else 0
            height = cell_height * 2 + 16
            y = 0
            for start in range(0, len(sizes), 4):
                pages = range(start, min(start + 4, len(sizes)))
                for offset, index in enumerate(pages):
                    w, h = sizes[index]
                    x = (offset % 2) * (cell_width + 12) + (cell_width - w) / 2
                    top = y + (offset // 2) * (cell_height + 16) + (cell_height - h) / 2
                    self.page_rects.append(QRectF(x, top, w, h))
                    self._page_rows.append(len(self._rows))
                self._rows.append(pages)
                self._row_rects.append(QRectF(0, y, width, height))
                y += height + 16
            self._page_tops = [r.top() for r in self._row_rects]
            self._page_bottoms = [r.bottom() for r in self._row_rects]
            self._scene.setSceneRect(QRectF(0, 0, width, max(0, y - 16)))
            self.resetCachedContent()
            return True
        # 双页从第 1、2 页开始配对；只有一页时仍居中显示。
        paired = spread and len(sizes) > 1
        left = max((w for w, h in sizes[::2]), default=0)
        right = max((w for w, h in sizes[1::2]), default=left)
        if paired:
            if len(sizes) % 2:
                right = max(right, sizes[-1][0])
            width = max(width, left + 12 + right)
        spine = (width + left - right - 12) / 2
        y = 0
        index = 0
        while index < len(sizes):
            row = range(index, min(index + (2 if paired else 1), len(sizes)))
            height = max(sizes[i][1] for i in row)
            for i in row:
                w, h = sizes[i]
                x = (width - w) / 2
                if paired:
                    x = spine - w if i == index else spine + 12
                self.page_rects.append(QRectF(x, y, w, h))
                self._page_rows.append(len(self._rows))
            self._rows.append(row)
            bounds = self.page_rects[index].united(self.page_rects[row.stop - 1])
            if paired and len(row) == 1:
                # 末尾未配对页保留右侧空位，维持左右页的位置。
                w, h = sizes[index]
                bounds = bounds.united(QRectF(spine + 12, y, w, h))
            self._row_rects.append(bounds)
            y += height + 16
            index = row.stop
        # 按整行索引，混合横竖页的左右页底边不一定有序。
        self._page_tops = [r.top() for r in self._row_rects]
        self._page_bottoms = [r.bottom() for r in self._row_rects]
        self._scene.setSceneRect(QRectF(0, 0, width, max(0, y - 16)))
        self.resetCachedContent()
        return True

    def visible_pages(self, rect):
        start = bisect_left(self._page_bottoms, rect.top())
        end = bisect_right(self._page_tops, rect.bottom())
        return (i for row in self._rows[start:end] for i in row
                if self.is_page_shown(i) and self.page_rects[i].intersects(rect))

    def _row_at_y(self, y):
        index = min(bisect_left(self._page_bottoms, y), len(self._rows) - 1)
        if index > 0 and y < self._page_tops[index]:
            if y - self._page_bottoms[index - 1] <= self._page_tops[index] - y:
                index -= 1
        return index

    def page_at_y(self, y):
        row = self._row_at_y(y)
        if row < 0:
            return -1
        pages = self._rows[row]
        return self.active_page if self.active_page in pages else pages.start

    def page_at_point(self, point):
        row = self._row_at_y(point.y())
        if row >= 0:
            for index in self._rows[row]:
                if self.is_page_shown(index) and self.page_rects[index].contains(point):
                    return index
        return -1

    def pages_in_spread(self, index):
        return self._rows[self._page_rows[index]] if 0 <= index < len(self.page_rects) else range(0)

    def spread_rect(self, index):
        return self._row_rects[self._page_rows[index]]

    def is_page_shown(self, index):
        return (not (self.spread and getattr(self, 'fit_page', False))
                or index in self.pages_in_spread(self.active_page))

    def update_page_visibility(self):
        isolated = self.spread and getattr(self, 'fit_page', False)
        pages = self.pages_in_spread(self.active_page)
        signature = (self.layout_generation, isolated, pages.start if isolated else None)
        if signature == self._visibility_signature:
            return
        if self._visibility_signature is None or self._visibility_signature[:2] != signature[:2]:
            self._page_scroll_acc = 0
        self._visibility_signature = signature
        for index, item in self.neighbors.items():
            item.setVisible(self.is_page_shown(index))
        self.resetCachedContent()
        self.viewport().update()

    def drawBackground(self, painter, rect) -> None:
        super().drawBackground(painter, rect)
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        scale = max(self.transform().m11(), .001)
        for index in self.visible_pages(rect):
            page = self.page_rects[index]
            if page.intersects(rect):
                painter.fillRect(page.translated(2 / scale, 3 / scale), QColor("#E4EAE7"))
                painter.fillRect(page, Qt.GlobalColor.white)
                pen = QPen(QColor("#D7E3DF"))
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.drawRect(page)
        painter.restore()

    def jump_to_page(self, index) -> None:
        if 0 <= index < len(self.page_rects):
            rect = self.spread_rect(index)
            if getattr(self, 'fit_page', False):
                self.centerOn(rect.center())
                return
            height = self.viewport().height() / self.transform().m11()
            self.centerOn(rect.center().x(), rect.top() + height / 2 - 4)

    def set_neighbor(self, index, image) -> None:
        item = self.neighbors.get(index)
        if item is None:
            item = QGraphicsPixmapItem()
            self._scene.addItem(item)
            self.neighbors[index] = item
        rect = self.page_rects[index]
        item.setPixmap(QPixmap.fromImage(image))
        item.setTransform(QTransform.fromScale(rect.width() / image.width(), rect.height() / image.height()))
        item.setPos(rect.topLeft())
        item.setVisible(self.is_page_shown(index))

    def set_search_results(self, results, current):
        self.search_areas = {}
        for index, (page, points) in enumerate(results):
            self.search_areas.setdefault(page, []).append((index, points))
        self.current_search = current
        self.viewport().update()

    def jump_to_search(self, page, points):
        if 0 <= page < len(self.page_rects):
            origin = self.page_rects[page].topLeft()
            polygon = QPolygonF([origin + QPointF(x, y) for x, y in points])
            self.ensureVisible(polygon.boundingRect(), 48, 72)

    def drawForeground(self, painter, rect) -> None:
        super().drawForeground(painter, rect)
        painter.save()
        for page in self.visible_pages(rect):
            origin = self.page_rects[page].topLeft()
            for index, points in self.search_areas.get(page, ()):
                polygon = QPolygonF([origin + QPointF(x, y) for x, y in points])
                active = index == self.current_search
                painter.setBrush(QColor(255, 153, 0, 105) if active else QColor(255, 224, 70, 75))
                pen = QPen(QColor("#B45309") if active else QColor(220, 175, 0, 100))
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.drawPolygon(polygon)
        painter.restore()

    def set_photo(self, pix: QPixmap, dpr: float, page_size=None) -> None:
        self._item.setPixmap(pix)
        width, height = page_size or (pix.width() / max(dpr, 1), pix.height() / max(dpr, 1))
        self._item.setTransform(QTransform.fromScale(width / pix.width(), height / pix.height()))
        # 场景坐标固定为 PDF 点，重绘换分辨率不会改变滚动位置。
        if self.page_rects:
            self._item.setPos(self.page_rects[self.active_page].topLeft())
        else:
            self._scene.setSceneRect(QRectF(0, 0, width, height))
        self._item.setZValue(0.5)

    def detail_item(self, index):
        return self._detail if index == self.active_page else self._other_details.get(index)

    def set_detail(self, image: QImage, position: tuple[float, float], scale: float, index=None) -> None:
        if index is None:
            index = self.active_page
        item = self.detail_item(index)
        if item is None:
            item = QGraphicsPixmapItem()
            item.setZValue(1)
            self._scene.addItem(item)
            self._other_details[index] = item
        item.setPixmap(QPixmap.fromImage(image))
        item.setScale(1 / scale)
        item.setPos(*position)
        if self.page_rects:
            item.setPos(item.pos() + self.page_rects[index].topLeft())

    def clear_detail(self, index=None) -> None:
        if index is None or index == self.active_page:
            self._detail.setPixmap(QPixmap())
        for page in list(self._other_details):
            if index is None or index == page:
                self._scene.removeItem(self._other_details.pop(page))

    def content_width(self) -> float:
        return self._scene.sceneRect().width()

    def clear_photo(self) -> None:
        self.clear_detail()
        self._item.setPixmap(QPixmap())
        if not self.page_rects:
            self.resetTransform()
            self._scene.setSceneRect(QRectF())

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._acc, steps = wheel_zoom_steps(self._acc, event.angleDelta().y())
            if steps:
                self.zoom_steps.emit(steps)
            event.accept()
            return
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if (self.spread and getattr(self, 'fit_page', False) and delta
                and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            if not event.buttons():
                self._page_scroll_acc, steps = wheel_zoom_steps(self._page_scroll_acc, delta)
                for _ in range(abs(steps)):
                    self.page_step.emit(-1 if steps > 0 else 1)
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:
        if self.spread and not event.modifiers() and event.key() in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            self.page_step.emit(-1 if event.key() == Qt.Key.Key_PageUp else 1)
            event.accept()
            return
        super().keyPressEvent(event)


class ConversionThread(QThread):
    progress = pyqtSignal(int, int, str)

    def __init__(
        self,
        pdf_path: str | None,
        output_path: str,
        page_indices: list[int] | None,
        parent: QObject | None = None,
        *,
        store: PdfStore | None = None,
        pages=None,
        include_all_content=False,
    ) -> None:
        super().__init__(parent)
        self.pdf_path = pdf_path
        self.output_path = output_path
        self.page_indices = page_indices
        self._cancelled = False
        self.store = store
        self.pages = list(pages) if pages is not None else None
        self.include_all_content = include_all_content
        self.result = None
        self.report = None
        self.error_message = None

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        scratch = None
        try:
            from app.pdf2excel.converter import convert_pdf_to_excel

            def p_cb(cur: int, tot: int, msg: str) -> None:
                self.progress.emit(cur, tot, msg)

            def c_check() -> bool:
                return self._cancelled

            if c_check():
                raise InterruptedError()
            source_pdf = self.pdf_path
            indices = self.page_indices
            # 文件检查及工作区快照都属于耗时工作，不能放在 GUI 线程。
            if source_pdf is None or is_encrypted_file(source_pdf):
                if self.store is None or not self.pages:
                    raise PdfError("缺少转换所需的页面")
                p_cb(0, 0, "正在准备转换页面...")
                scratch = TemporaryWorkspace('excel')
                tmp_pdf = str(Path(scratch.name) / 'workspace.pdf')
                self.store.require_operation(self.pages, "copy")
                from app.pdfworker import write_pdf
                write_pdf(self.store, self.pages, tmp_pdf, progress_callback=p_cb,
                                 options=WriteOptions(preserve_encryption=False, operation="copy"), cancel=c_check)
                source_pdf = tmp_pdf
                indices = None
            if c_check():
                raise InterruptedError()
            self.result, self.report = convert_pdf_to_excel(
                source_pdf,
                self.output_path,
                page_indices=indices,
                progress_callback=p_cb,
                cancel_check=c_check,
                include_all_content=self.include_all_content,
            )
        except (InterruptedError, OperationCancelled):
            self.error_message = "转换已被取消"
        except Exception as exc:
            self.error_message = f"转换失败：{exc}"
        finally:
            if scratch:
                try:
                    scratch.cleanup()
                except OSError:
                    pass


class PageListModel(QAbstractListModel):
    reordered = pyqtSignal()
    busy = pyqtSignal()

    def __init__(self, ws: Workspace, store: PdfStore, parent=None, *, rendering=None) -> None:
        super().__init__(parent)
        self.ws = ws
        self.store = store
        self.rendering = rendering or RenderScheduler(store, self)
        self.thumb_visible = lambda key: True
        self.live_keys = lambda: {page.render_key for page in self.ws.pages}
        self.drag_source_ws: Workspace | None = None
        # 按最近使用排序，超出预算就从最久没用过的那头丢，防止放大后一直占着内存。
        self.thumbs: OrderedDict[tuple, tuple[QPixmap, int]] = OrderedDict()
        self._thumb_bytes = 0
        self._shown_bytes = 0
        self.shown: dict[tuple, QPixmap] = {}
        self.inflight: set[tuple] = set()
        self.signals = RenderSignals()
        self.signals.done.connect(self._on_thumb)
        self.icon_w = THUMB_W
        self.arrange = False
        self.dpr = 1.0
        # 比例同样按 (源文件, 原页码, 旋转) 缓存：转过之后 key 变了，比例自然重算。
        self._ratios: dict[tuple, float] = {}
        self._placeholders: dict[tuple[int, int], QPixmap] = {}
        self._rows_by_key = {}
        self._indexed_pages = None
        self._max_ratio = None

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return len(self.ws.pages)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        page = self.ws.pages[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return f"{index.row() + 1}"
        if role == Qt.ItemDataRole.DecorationRole:
            return self._thumb(page)
        if role == Qt.ItemDataRole.ToolTipRole:
            name = os.path.basename(page.source_path)
            try:
                source_rotation = self.store.source_rotation(page)
            except PdfError:
                source_rotation = 0
            note = f" · 源文件已旋转 {source_rotation}°" if source_rotation else ""
            return f"{name} 原第 {page.source_page + 1} 页 · 本次旋转 {page.rotation}°{note}"
        if role == Qt.ItemDataRole.SizeHintRole:
            return QSize(self._thumb_box(page).width() + THUMB_ITEM_W_PAD, self.item_height(page))
        return None

    def flags(self, index: QModelIndex):
        flag = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDropEnabled
        if index.isValid():
            flag |= Qt.ItemFlag.ItemIsDragEnabled
        return flag

    def supportedDropActions(self):
        return Qt.DropAction.MoveAction

    def mimeTypes(self):
        return [MIME]

    def mimeData(self, indexes):
        mime = QMimeData()
        rows = sorted({i.row() for i in indexes if i.isValid()})
        if rows:
            self.drag_source_ws = self.ws
            mime.setData(MIME, ",".join(map(str, rows)).encode("utf-8"))
        return mime

    def dropMimeData(self, data, action, row, column, parent):
        return False

    def reload(self) -> None:
        if self._indexed_pages is self.ws.pages:
            return
        self.beginResetModel()
        self._indexed_pages = self.ws.pages
        self.rendering.set_pages(self.ws.pages)
        self.inflight.clear()
        self._max_ratio = None
        self._rows_by_key = {}
        for row, page in enumerate(self.ws.pages):
            self._rows_by_key.setdefault(self._thumb_key(page), []).append(row)
        live = self.live_keys()
        if any(key not in live for key in self.thumbs):
            self.thumbs = OrderedDict(
                (k, v) for k, v in self.thumbs.items() if k in live
            )
            self.shown = {k: v for k, v in self.shown.items() if k in live}
            self._thumb_bytes = sum(pixmap_bytes(v[0]) for v in self.thumbs.values())
            self._shown_bytes = sum(pixmap_bytes(pix) for pix in self.shown.values())
        if len(self._ratios) > len(live):
            self._ratios = {k: v for k, v in self._ratios.items() if k in live}
        self.endResetModel()

    def set_icon_width(self, width: int, arrange: bool | None = None) -> None:
        width = max(THUMB_MIN_W, int(width))
        arrange = self.arrange if arrange is None else arrange
        if width == self.icon_w and arrange == self.arrange:
            return
        self.icon_w = width
        self.arrange = arrange
        self.shown.clear()
        self._shown_bytes = 0
        self._placeholders.clear()
        n = self.rowCount()
        if n <= 0:
            return
        last = self.index(n - 1)
        self.dataChanged.emit(
            self.index(0),
            last,
            [Qt.ItemDataRole.DecorationRole, Qt.ItemDataRole.SizeHintRole],
        )
        self.layoutChanged.emit()

    def set_device_pixel_ratio(self, dpr: float) -> None:
        """位图按物理像素出图，换了缩放比例的屏幕就得重新出一次。"""
        dpr = max(dpr, 1.0)
        if dpr == self.dpr:
            return
        self.dpr = dpr
        self.shown.clear()
        self._shown_bytes = 0
        n = self.rowCount()
        if n <= 0:
            return
        self.dataChanged.emit(self.index(0), self.index(n - 1), [Qt.ItemDataRole.DecorationRole])

    def _thumb_render_width(self, page=None) -> int:
        width = self._thumb_box(page).width() if page is not None else self.icon_w
        target = int(round(width * max(self.dpr, 1.0)))
        steps = -(-target // THUMB_RENDER_STEP)
        return min(1600, max(THUMB_MIN_W, steps * THUMB_RENDER_STEP))

    def _thumb_key(self, page) -> tuple:
        return page.render_key

    def _placeholder(self, page) -> QPixmap:
        box = self._thumb_box(page)
        if max(box.width(), box.height()) > 1600:
            box.scale(QSize(1600, 1600), Qt.AspectRatioMode.KeepAspectRatio)
        key = (box.width(), box.height())
        hit = self._placeholders.get(key)
        if hit is None:
            if len(self._placeholders) > 64:
                self._placeholders.clear()
            hit = QPixmap(box)
            hit.fill(QColor("#E8F1F4"))
            self._placeholders[key] = hit
        return hit

    def _ratio(self, page) -> float:
        """页面本身的高宽比（含旋转）。页尺寸在打开文档时已算好，这里只查表。"""
        key = self._thumb_key(page)
        hit = self._ratios.get(key)
        if hit is None:
            try:
                width_pt, height_pt = self.store.page_size(page)
            except PdfError:
                width_pt = height_pt = 0.0
            hit = thumb_ratio(width_pt, height_pt)
            self._ratios[key] = hit
        return hit

    def max_ratio(self) -> float:
        """编排视图共用一种格子，取工作区里最高的比例当格子高度。"""
        if not self.ws.pages:
            return THUMB_RATIO_DEFAULT
        if self._indexed_pages is not self.ws.pages:
            self.reload()
        if self._max_ratio is None:
            self._max_ratio = max(self._ratio(page) for page in self.ws.pages)
        return self._max_ratio

    def _thumb_box(self, page) -> QSize:
        if self.arrange:
            height = max(THUMB_MIN_H, int(round(self.icon_w * THUMB_RATIO_DEFAULT)))
            return QSize(max(1, int(round(height / self._ratio(page)))), height)
        height = max(THUMB_MIN_H, int(round(self.icon_w * self._ratio(page))))
        return QSize(self.icon_w, height)

    def item_height(self, page) -> int:
        """条目框 = 缩略图框 + 卡片内边距与页码标签。"""
        return self._thumb_box(page).height() + THUMB_ITEM_H_PAD

    def _thumb(self, page) -> QPixmap:
        key = self._thumb_key(page)
        shown = self.shown.get(key)
        if shown is not None:
            return shown
        cached = self.thumbs.get(key)
        if cached is not None:
            self.thumbs.move_to_end(key)
        want = self._thumb_render_width(page)
        page_w, page_h = self.store.page_size(page)
        want = min(want, max(1, int(math.sqrt(2_000_000 * page_w / max(page_h, 1)))))
        if cached is None or cached[1] < want:
            if key not in self.inflight and self.thumb_visible(key):
                self.inflight.add(key)
                self.busy.emit()
                self.rendering.request(page, want, key, self.signals,
                    lambda k=key: k not in self.inflight or not self.thumb_visible(k), 0)
            if cached is None:
                return self._placeholder(page)
        # 交给视图的位图必须自己缩到显示尺寸：Qt 拿 DecorationRole 位图的逻辑尺寸
        # 当绘制尺寸，直接丢一张大图过去会被条目框裁掉一部分。位图按物理像素缩放
        # 并打上 dpr 标记，逻辑尺寸才刚好等于下面的框——高分屏上就不必再被拉伸。
        box = self._thumb_box(page)
        dpr = max(self.dpr, 1.0)
        display_size = QSize(int(round(box.width() * dpr)), int(round(box.height() * dpr)))
        if max(display_size.width(), display_size.height()) > 1600:
            display_size.scale(QSize(1600, 1600), Qt.AspectRatioMode.KeepAspectRatio)
        pix = cached[0].scaled(
            display_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        pix.setDevicePixelRatio(dpr)
        self.shown[key] = pix
        self._shown_bytes += pixmap_bytes(pix)
        self._trim_thumbs()
        return pix

    def _drop_shown(self, key):
        pix = self.shown.pop(key, None)
        if pix is not None:
            self._shown_bytes -= pixmap_bytes(pix)

    def _cache_put(self, key: tuple, hit: tuple) -> None:
        old = self.thumbs.pop(key, None)
        if old is not None:
            self._thumb_bytes -= pixmap_bytes(old[0])
        self.thumbs[key] = hit
        self._thumb_bytes += pixmap_bytes(hit[0])
        self._trim_thumbs()

    def _trim_thumbs(self):
        while self._thumb_bytes + self._shown_bytes > THUMB_CACHE_BYTES and len(self.thumbs) > 1:
            dropped_key, dropped = self.thumbs.popitem(last=False)
            self._drop_shown(dropped_key)
            self._thumb_bytes -= pixmap_bytes(dropped[0])
        self.rendering.diagnostics.record("memory", thumb_cache_bytes=self._thumb_bytes,
                                         thumb_display_bytes=self._shown_bytes)

    def remap(self, before, after) -> dict[tuple, tuple]:
        """保存是把同样的内容写进了新文件，位图没变，缓存换个 key 接着用。"""
        mapping: dict[tuple, tuple] = {}
        for old, new in zip(before, after):
            old_key = self._thumb_key(old)
            new_key = self._thumb_key(new)
            if old_key != new_key:
                mapping[old_key] = new_key
        for old_key, new_key in mapping.items():
            self._ratios.pop(old_key, None)
            hit = self.thumbs.pop(old_key, None)
            if hit is None:
                continue
            self._thumb_bytes -= pixmap_bytes(hit[0])
            self._drop_shown(old_key)
            self._drop_shown(new_key)
            self._cache_put(new_key, hit)
        return mapping

    def _on_thumb(self, key: tuple, image: QImage | None) -> None:
        if isinstance(image, RenderResult):
            if image.generation != self.rendering.generation:
                return
            if image.cancelled and self.rendering.has_request(self.signals, key):
                return
            image = image.value
        self.inflight.discard(key)
        if self._indexed_pages is not self.ws.pages:
            self.reload()
        rows = self._rows_by_key.get(key, ())
        if not rows:
            return
        if image is None or image.isNull():
            return
        self._cache_put(key, (QPixmap.fromImage(image), image.width()))
        self._drop_shown(key)
        for row in rows:
            idx = self.index(row)
            self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])


class _HoverIconButton(QToolButton):
    def __init__(self, name: str, color: str, hover: str, parent=None) -> None:
        super().__init__(parent)
        self._name = name
        self._color = color
        self._hover = hover
        self._paint(color)

    def _paint(self, color: str) -> None:
        self.setIcon(toolbar_icon(self._name, color, CAPTION_PX))
        self.setIconSize(QSize(CAPTION_PX, CAPTION_PX))

    def enterEvent(self, event) -> None:
        self._paint(self._hover)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._paint(self._color)
        super().leaveEvent(event)


class ElidedTitleLabel(QLabel):
    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__("", parent)
        self._full_text = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)
        self._update_elided()

    def setFullText(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self._update_elided()

    def fullText(self) -> str:
        return self._full_text

    def _update_elided(self) -> None:
        avail_w = max(20, self.width() - 8)
        elided = self.fontMetrics().elidedText(self._full_text, Qt.TextElideMode.ElideMiddle, avail_w)
        super().setText(elided)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_elided()

    def paintEvent(self, event) -> None:
        self._update_elided()
        super().paintEvent(event)


class TitleBar(QWidget):
    def __init__(self, host: MainWindow) -> None:
        super().__init__(host)
        self._host = host
        self.setObjectName("titleBar")
        self.setFixedHeight(40)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 0, 0)
        lay.setSpacing(6)
        mark = QLabel()
        mark.setPixmap(brand_icon().pixmap(QSize(18, 18)))
        mark.setFixedWidth(28)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(mark)
        host._menubar = QMenuBar(self)
        host._menubar.setNativeMenuBar(False)
        host._menubar.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        lay.addWidget(host._menubar)
        host.title_label = ElidedTitleLabel(f"{APP_NAME} v{__version__}", self)
        host.title_label.setObjectName("windowTitle")
        host.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        host.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(host.title_label, 1)
        host.btn_min = self._win_btn("win-min", "最小化", host.showMinimized)
        host.btn_max = self._win_btn("win-max", "最大化", host._toggle_max)
        host.btn_close = _HoverIconButton("win-close", "#134E4A", "#FFFFFF", self)
        host.btn_close.setObjectName("winClose")
        host.btn_close.setFixedSize(46, 40)
        host.btn_close.setToolTip("关闭")
        host.btn_close.setStatusTip("关闭")
        host.btn_close.setAccessibleName("关闭")
        host.btn_close.clicked.connect(host.close)
        lay.addWidget(host.btn_min)
        lay.addWidget(host.btn_max)
        lay.addWidget(host.btn_close)

    def _win_btn(self, icon: str, label: str, slot) -> QToolButton:
        btn = QToolButton(self)
        btn.setObjectName("winBtn")
        btn.setIcon(toolbar_icon(icon, "#134E4A", CAPTION_PX))
        btn.setIconSize(QSize(CAPTION_PX, CAPTION_PX))
        btn.setFixedSize(46, 40)
        btn.setToolTip(label)
        btn.setStatusTip(label)
        btn.setAccessibleName(label)
        btn.clicked.connect(slot)
        return btn

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._host._toggle_max()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)


_TAB_ID_COUNTER = count(1)


@dataclass
class _DocumentTab:
    ws: Workspace
    tab_id: int = 0
    zoom: float = 1.0
    zoom_mode: str = "page"
    reading_layout: str = "single"
    arrange_zoom: float = 1.0
    mode_index: int = 0
    scroll_y: int | None = None

    def __post_init__(self) -> None:
        if not self.tab_id:
            self.tab_id = next(_TAB_ID_COUNTER)


class _TabPill(QFrame):
    def __init__(self, tab_id: int, bar: DocumentTabBar) -> None:
        super().__init__(bar)
        self.tab_id = tab_id
        self._bar = bar
        self.setObjectName("docTabPill")
        self.setFixedHeight(24)
        self.setMinimumWidth(96)
        self.setMaximumWidth(220)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAcceptDrops(True)
        self.setProperty("active", False)
        self.setProperty("dropTarget", False)
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(400)
        self._hover_timer.timeout.connect(self._on_drag_hover_timeout)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 4, 0)
        row.setSpacing(4)
        self.title_label = QLabel(self)
        self.title_label.setObjectName("docTabTitle")
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.title_label.setProperty("active", False)
        row.addWidget(self.title_label, 1)

        self.close_button = QToolButton(self)
        self.close_button.setObjectName("docTabClose")
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setFixedSize(18, 18)
        self.close_button.setIconSize(QSize(11, 11))
        self.close_button.setIcon(toolbar_icon("win-close", "#5E7B73", 11))
        self.close_button.setToolTip("关闭此标签页")
        self.close_button.setAccessibleName("关闭此标签页")
        self.close_button.clicked.connect(lambda: self._bar._host.close_tab(self.tab_id))
        row.addWidget(self.close_button)

    def _set_drop_target(self, on: bool) -> None:
        if bool(self.property("dropTarget")) == on:
            return
        self.setProperty("dropTarget", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def _restore_title(self) -> None:
        tab = self._bar._host._get_tab(self.tab_id)
        if tab is not None:
            self.update_content(tab.ws, tab.tab_id == self._bar._host._active_tab_id)

    def update_content(self, ws: Workspace, active: bool) -> None:
        raw_name = os.path.basename(ws.path) if ws.path else ("未命名文档" if ws.pages or ws.dirty else "新标签页")
        text = f"● {raw_name}" if ws.dirty else raw_name
        elided = self.title_label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideMiddle, 152)
        if not self.property("dropTarget"):
            self.title_label.setText(elided)
        tip = ws.path or raw_name
        if ws.dirty:
            tip = f"{tip}（有未保存修改）"
        self.setToolTip(tip)
        self.setAccessibleName(raw_name)
        if bool(self.property("active")) != active:
            self.setProperty("active", active)
            self.title_label.setProperty("active", active)
            for widget in (self, self.title_label):
                widget.style().unpolish(widget)
                widget.style().polish(widget)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._bar._host.activate_tab(self.tab_id)
            event.accept()
            return
        if event.button() == Qt.MouseButton.MiddleButton:
            self._bar._host.close_tab(self.tab_id)
            event.accept()
            return
        super().mousePressEvent(event)

    def _on_drag_hover_timeout(self) -> None:
        if self._bar._host._active_tab_id != self.tab_id:
            self._bar._host.activate_tab(self.tab_id)

    def dragEnterEvent(self, event) -> None:
        mime = event.mimeData()
        if mime.hasFormat(MIME):
            self._set_drop_target(True)
            try:
                count = len([row for row in bytes(mime.data(MIME)).decode("utf-8").split(",") if row])
            except (ValueError, UnicodeDecodeError):
                count = 1
            self.title_label.setText(f"+{max(1, count)}页到此文件")
            if self._bar._host._active_tab_id != self.tab_id:
                self._hover_timer.start()
            event.acceptProposedAction()
            return
        if _pdf_paths_from_mime(mime):
            self._set_drop_target(True)
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        mime = event.mimeData()
        if mime.hasFormat(MIME) or _pdf_paths_from_mime(mime):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._hover_timer.stop()
        self._set_drop_target(False)
        self._restore_title()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self._hover_timer.stop()
        self._set_drop_target(False)
        self._restore_title()
        mime = event.mimeData()
        if mime.hasFormat(MIME):
            try:
                sources = [int(row) for row in bytes(mime.data(MIME)).decode("utf-8").split(",") if row]
            except (ValueError, UnicodeDecodeError):
                event.ignore()
                return
            host = self._bar._host
            source_ws = getattr(host.model, "drag_source_ws", None) or host.ws
            host.model.drag_source_ws = None
            if host.drop_pages_onto_tab(self.tab_id, source_ws, sources):
                event.acceptProposedAction()
            else:
                event.ignore()
            return
        pdf_paths = _pdf_paths_from_mime(mime)
        if pdf_paths:
            self._bar._host.open_files_in_tabs(pdf_paths)
            event.acceptProposedAction()
            return
        event.ignore()


class DocumentTabBar(QFrame):
    def __init__(self, host: MainWindow) -> None:
        super().__init__(host)
        self._host = host
        self._pills: dict[int, _TabPill] = {}
        self.setObjectName("documentTabBar")
        self.setFixedHeight(32)
        self.setAcceptDrops(True)
        self.setVisible(False)

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 3, 12, 3)
        row.setSpacing(6)
        self._pills_layout = QHBoxLayout()
        self._pills_layout.setContentsMargins(0, 0, 0, 0)
        self._pills_layout.setSpacing(6)
        row.addLayout(self._pills_layout)

        self.new_tab_button = QToolButton(self)
        self.new_tab_button.setObjectName("docTabNew")
        self.new_tab_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_tab_button.setFixedSize(24, 24)
        self.new_tab_button.setIconSize(QSize(14, 14))
        self.new_tab_button.setIcon(toolbar_icon("plus", "#0F766E", 14))
        self.new_tab_button.setToolTip("打开新 PDF 标签页 (Ctrl+O)")
        self.new_tab_button.setAccessibleName("打开新 PDF 标签页")
        self.new_tab_button.clicked.connect(self._host.open_files)
        row.addWidget(self.new_tab_button)
        row.addStretch(1)

    @property
    def pills(self) -> list[_TabPill]:
        return [self._pills[tab.tab_id] for tab in self._host._tabs if tab.tab_id in self._pills]

    def pill_for(self, tab_id: int) -> _TabPill | None:
        return self._pills.get(tab_id)

    def sync_tabs(self, tabs: list[_DocumentTab], active_tab_id: int) -> None:
        live_ids = {tab.tab_id for tab in tabs}
        for tid in list(self._pills):
            if tid not in live_ids:
                pill = self._pills.pop(tid)
                self._pills_layout.removeWidget(pill)
                pill.hide()
                pill.deleteLater()
        for idx, tab in enumerate(tabs):
            pill = self._pills.get(tab.tab_id)
            if pill is None:
                pill = _TabPill(tab.tab_id, self)
                self._pills[tab.tab_id] = pill
                self._pills_layout.insertWidget(idx, pill)
            pill.update_content(tab.ws, tab.tab_id == active_tab_id)
        self.setVisible(len(tabs) > 1)

    def dragEnterEvent(self, event) -> None:
        if _pdf_paths_from_mime(event.mimeData()):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        if _pdf_paths_from_mime(event.mimeData()):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event) -> None:
        pdf_paths = _pdf_paths_from_mime(event.mimeData())
        if pdf_paths:
            self._host.open_files_in_tabs(pdf_paths)
            event.acceptProposedAction()
            return
        event.ignore()


class MainWindow(QMainWindow):
    first_painted = pyqtSignal()

    def __init__(self, *, opening_paths=()) -> None:
        mark("window_init_started")
        super().__init__()
        self._painted = False
        self._opening_paths = tuple(opening_paths)
        self.ws = Workspace()
        self.store = PdfStore()
        self._undo_refresh_timer = QTimer(self)
        self._undo_refresh_timer.setSingleShot(True)
        self._undo_refresh_timer.setInterval(0)
        self._undo_refresh_timer.timeout.connect(self.refresh)
        self.undo_group = QUndoGroup(self)
        self._register_workspace(self.ws)
        self.undo_group.setActiveStack(self.ws.undo_stack)
        self._tabs: list[_DocumentTab] = [_DocumentTab(ws=self.ws)]
        self._active_tab_id: int = self._tabs[0].tab_id
        self._selection_buttons = []
        self._syncing = False
        self._max_hover = False
        self.setAcceptDrops(True)
        self.setMinimumSize(980, 680)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )
        self.setWindowIcon(brand_icon())
        self.setStyleSheet(STYLESHEET)
        self.rendering = RenderScheduler(self.store, self)
        self.model = PageListModel(self.ws, self.store, rendering=self.rendering)
        self.model.live_keys = self._live_render_keys
        self.model.reordered.connect(self.refresh)
        self.preview_signals = RenderSignals()
        self.preview_signals.done.connect(self._on_preview)
        self.detail_signals = RenderSignals()
        self.detail_signals.done.connect(self._on_detail_preview)
        self._preview_key = None
        self._preview_image = None
        self._shown_page = None
        self._preview_busy = False
        self._detail_key = None
        self._detail_busy = False
        self._other_detail_keys = {}
        self._other_detail_pending = set()
        self._detail_timer = QTimer(self)
        self._detail_timer.setSingleShot(True)
        self._detail_timer.setInterval(90)
        self._detail_timer.timeout.connect(self._request_detail)
        self._reading_timer = QTimer(self)
        self._reading_timer.setSingleShot(True)
        self._reading_timer.setInterval(16)
        self._reading_timer.timeout.connect(self._reading_scrolled)
        self._scroll_navigation = False
        self._scrolling = False
        self._scroll_direction = 1
        self._scroll_y = None
        self._reader_updating = False
        self._controls_read_only = False
        self._reading_settle_timer = QTimer(self)
        self._reading_settle_timer.setSingleShot(True)
        self._reading_settle_timer.setInterval(READING_SETTLE_MS)
        self._reading_settle_timer.timeout.connect(self._reading_settled)
        self._neighbor_pending = set()
        self._neighbor_keys = {}
        self._layout_pages = None
        self._page_images = OrderedDict()
        self._page_image_bytes = 0
        self._neighbor_signals = RenderSignals()
        self._neighbor_signals.done.connect(self._on_neighbor)
        self._zoom = 1.0
        self._zoom_mode = 'page'
        self._reading_layout = 'single'
        self._arrange_zoom = 1.0
        self._sidebar_w = THUMB_W
        self._last_vp_w = 0
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(180)
        self._preview_timer.timeout.connect(self._update_preview)
        self._view_zoom_timer = QTimer(self)
        self._view_zoom_timer.setSingleShot(True)
        self._view_zoom_timer.setInterval(16)
        self._view_zoom_timer.timeout.connect(self._apply_view_zoom)
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(120)
        self._thumb_timer.timeout.connect(self._fit_thumbs)
        mark("window_state_ready")
        self._build()
        self.model.thumb_visible = self._thumb_visible
        for view in (self.left_view, self.arrange_view):
            view.verticalScrollBar().valueChanged.connect(self._prune_visible_thumbs)
        mark("window_build_ready")
        self._shortcuts()
        self._window_settings = QSettings(APP_ORGANIZATION, APP_SLUG)
        geometry = self._window_settings.value("mainWindow/geometry", QByteArray())
        if isinstance(geometry, QByteArray):
            self.restoreGeometry(geometry)
        self._restore_ui_preferences()
        from app.updates import UpdateChecker
        self.updates = UpdateChecker(self._window_settings, self)
        self.updates.changed.connect(self._update_release_notice)
        self._notified_update_tag = ""
        self._update_release_notice()
        for signal in (self.act_pages.toggled, self.act_tools.toggled, self.sidebar_mode.currentIndexChanged,
                       self.export_tools.currentChanged):
            signal.connect(self._save_ui_preferences)
        self.refresh()
        mark("window_ready")

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._painted:
            self._painted = True
            mark("window_painted")
            self.first_painted.emit()

    def _chrome_actions(self) -> None:
        self.find_action = self._act("全文搜索", self.open_search, QKeySequence.StandardKey.Find)
        self.find_action.setIcon(toolbar_icon("search"))
        self.find_action.setToolTip("全文搜索 (Ctrl+F)")
        self.act_pages = QAction("页面栏", self)
        self.act_pages.setCheckable(True)
        self.act_pages.setChecked(False)
        self.act_pages.setShortcut(QKeySequence("F8"))
        self.act_pages.setIcon(toolbar_icon("panel-left"))
        self.act_pages.setToolTip("页面栏 (F8)")
        self.act_pages.setStatusTip("显示或隐藏左侧页面栏 (F8)")
        self.act_pages.toggled.connect(self._set_pages_visible)
        self.act_tools = QAction("工具栏", self)
        self.act_tools.setCheckable(True)
        self.act_tools.setChecked(False)
        self.act_tools.setShortcut(QKeySequence("F9"))
        self.act_tools.setIcon(toolbar_icon("panel-right"))
        self.act_tools.setToolTip("工具栏 (F9)")
        self.act_tools.setStatusTip("显示或隐藏右侧工具栏 (F9)")
        self.act_tools.toggled.connect(self._set_tools_visible)

    def _set_pages_visible(self, on: bool) -> None:
        panel = getattr(self, "left_panel", None)
        if panel is not None:
            panel.setVisible(on and bool(self.ws.pages))

    def _set_tools_visible(self, on: bool) -> None:
        panel = getattr(self, "tools_panel", None)
        if panel is not None:
            panel.setVisible(on and bool(self.ws.pages))

    def _save_ui_preferences(self, *_):
        if not hasattr(self, '_window_settings'):
            return
        for key, value in (('pagesVisible', self.act_pages.isChecked()),
                           ('toolsVisible', self.act_tools.isChecked()),
                           ('sidebarMode', self.sidebar_mode.currentIndex()),
                           ('toolIndex', self.export_tools.currentIndex())):
            self._window_settings.setValue('mainWindow/' + key, value)

    def _restore_ui_preferences(self):
        self.act_pages.setChecked(self._window_settings.value('mainWindow/pagesVisible', False, type=bool))
        self.act_tools.setChecked(self._window_settings.value('mainWindow/toolsVisible', False, type=bool))
        for key, widget in (('sidebarMode', self.sidebar_mode), ('toolIndex', self.export_tools)):
            try:
                index = self._window_settings.value('mainWindow/' + key, 0, type=int)
            except (TypeError, ValueError):
                index = 0
            widget.setCurrentIndex(index if 0 <= index < widget.count() else 0)

    def reset_ui_preferences(self):
        self.act_pages.setChecked(False)
        self.act_tools.setChecked(False)
        self.sidebar_mode.setCurrentIndex(0)
        self.export_tools.setCurrentIndex(0)
        self._set_reading_layout('single')
        self.reading_orb.restore()
        self._save_ui_preferences()
        self.statusBar().showMessage('界面已恢复默认，文档与存储设置保留。', 5000)

    def _build(self) -> None:
        self._chrome_actions()
        self.stack = QStackedWidget()
        self.stack.addWidget(self._main_view())
        self.stack.addWidget(self._arrange_view())
        self.stack.currentChanged.connect(lambda *_: self._apply_thumb_size())
        self.setStatusBar(QStatusBar())
        status_widgets = QWidget(self.statusBar())
        status_layout = QHBoxLayout(status_widgets)
        status_layout.setContentsMargins(0, 0, 0, 0)
        self.render_progress = QProgressBar(status_widgets)
        self.render_progress.setRange(0, 0)
        self.render_progress.setTextVisible(False)
        self.render_progress.setFixedSize(88, 5)
        self.render_progress.setStyleSheet(
            "QProgressBar { border: none; background: #DDEBE5; border-radius: 2px; }"
            "QProgressBar::chunk { background: #149587; border-radius: 2px; }")
        self.render_label = QLabel("正在渲染页面", status_widgets)
        # Match the status message height so rendering does not resize the reader.
        self.render_label.setStyleSheet("font-size: 13px;")
        self.render_label.ensurePolished()
        messages = ("正在打开 PDF", "正在渲染页面", "正在生成缩略图")
        self.render_label.setFixedSize(
            max(self.render_label.fontMetrics().horizontalAdvance(text) for text in messages) + 2,
            self.render_label.sizeHint().height())
        for widget in (self.render_label, self.render_progress):
            policy = widget.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            widget.setSizePolicy(policy)
        # addPermanentWidget 会自动显示未显式隐藏的控件并触发布局和字体测量。
        # 空窗口先隐藏，渲染或保护提示真正需要时再显示。
        self.render_label.hide()
        self.render_progress.hide()
        status_layout.addWidget(self.render_label)
        status_layout.addWidget(self.render_progress)
        self.protection_label = QLabel(status_widgets)
        self.protection_label.setObjectName("hint")
        self.protection_label.setWordWrap(True)
        self.protection_label.hide()
        status_layout.addWidget(self.protection_label)
        self.protection_copy_button = QPushButton('制作影像副本…', status_widgets)
        self.protection_copy_button.setAccessibleName('为无法确认签名结构的原件制作影像副本')
        self.protection_copy_button.setStyleSheet('min-height: 0px; padding: 4px 8px;')
        self.protection_copy_button.setMinimumWidth(self.protection_copy_button.sizeHint().width())
        self.protection_copy_button.clicked.connect(lambda: self.rasterize_document(recovery=True))
        self.protection_copy_button.hide()
        status_layout.addWidget(self.protection_copy_button)
        # 一次注册，避免 QStatusBar 为每个控件重复重建整个布局。
        self.statusBar().addPermanentWidget(status_widgets)
        # 只在真的忙的时候轮询，空闲就停表。
        self._render_status_timer = QTimer(self)
        self._render_status_timer.setInterval(120)
        self._render_status_timer.timeout.connect(self._update_render_progress)
        self._render_show_timer = QTimer(self)
        self._render_show_timer.setSingleShot(True)
        self._render_show_timer.setInterval(RENDER_SHOW_DELAY_MS)
        self._render_show_timer.timeout.connect(self._show_render_progress)
        self._render_hide_timer = QTimer(self)
        self._render_hide_timer.setSingleShot(True)
        self._render_hide_timer.setInterval(RENDER_HIDE_DELAY_MS)
        self._render_hide_timer.timeout.connect(self._hide_render_progress)
        self.model.busy.connect(self._note_render_busy)
        self.setMenuWidget(TitleBar(self))
        file_menu = self._menubar.addMenu("文件")
        self.open_action = self._act("打开 PDF / OFD", self.open_files, QKeySequence.StandardKey.Open)
        file_menu.addAction(self.open_action)
        file_menu.addSeparator()
        self.save_action = self._act("保存", self.save, QKeySequence.StandardKey.Save)
        self.save_as_action = self._act("另存为", lambda: self.save(True), QKeySequence.StandardKey.SaveAs)
        self.close_file_action = self._act("关闭当前文件", self.close_current_file, QKeySequence("Ctrl+W"))
        self.close_file_action.setStatusTip("关闭当前文件并返回首页")
        for action in (self.save_action, self.save_as_action, self.close_file_action):
            file_menu.addAction(action)
        file_menu.addSeparator()
        self.print_action = self._act("打印", self.print_document, QKeySequence.StandardKey.Print)
        file_menu.addAction(self.print_action)
        file_menu.addSeparator()
        self.storage_action = self._act('存储管理…', self.manage_storage)
        self.storage_action.setStatusTip('查看 OCR、缓存和输出位置，选择保存目录或清理旧文件')
        file_menu.addAction(self.storage_action)
        if sys.platform == "win32":
            file_menu.addSeparator()
            file_menu.addAction(self._act("设为默认 PDF 应用…", self.choose_default_application))
        file_menu.addSeparator()
        file_menu.addAction(self._act("退出", self.close))
        edit = self._menubar.addMenu("编辑")
        self.undo_action = self.undo_group.createUndoAction(self, "撤销")
        self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.redo_action = self.undo_group.createRedoAction(self, "重做")
        self.redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        edit.addAction(self.undo_action)
        edit.addAction(self.redo_action)
        edit.addSeparator()
        edit.addAction(self._act("全选", self.select_all_pages, QKeySequence.StandardKey.SelectAll))
        edit.addAction(self.find_action)
        edit.addSeparator()
        edit.addAction(self._act("审阅与文字修正…", self.open_annotation_tools))
        self.capture_action = self._act("框选导出图片…", lambda: self.region_capture.begin(),
                                        QKeySequence("Ctrl+Shift+C"))
        self.capture_action.setIcon(toolbar_icon('annotation-select'))
        self.capture_action.setToolTip('框选原文，连同批注说明复制或保存为图片（Ctrl+Shift+C）')
        edit.addAction(self.capture_action)
        edit.addSeparator()
        self.insert_action = self._act("插入页面…", self.insert_files)
        edit.addAction(self.insert_action)
        self.append_action = self._act("合并文件…", self.append_files)
        edit.addAction(self.append_action)
        self.delete_action = self._act("删除页面", self.delete_pages)
        edit.addAction(self.delete_action)
        tools = self._menubar.addMenu("组织")
        self.extract_action = self._act("提取页面…", lambda: self.open_export_tool(0))
        self.split_action = self._act("拆分文件…", lambda: self.open_export_tool(1))
        tools.addAction(self.extract_action)
        tools.addAction(self.split_action)
        tools.addAction(self._act("页码处理…", self.open_page_numbers))
        self.watermark_action = self._act("水印…", self.open_watermarks)
        tools.addAction(self.watermark_action)
        self.ocr_action = self._act("文字识别…", self.open_ocr)
        tools.addAction(self.ocr_action)
        tools.addSeparator()
        self.compress_action = self._act("文档压缩…", lambda: self.open_export_tool(3))
        self.encrypt_action = self._act("加密另存…", self.encrypt_copy)
        self.decrypt_action = self._act("解密另存…", self.decrypt_copy)
        self.editable_action = self._act("编辑副本…", self.editable_copy)
        for action in (self.compress_action, self.encrypt_action, self.decrypt_action, self.editable_action):
            tools.addAction(action)
        convert = self._menubar.addMenu("转换")
        convert.addAction(self._act("Word 转 PDF…", self.open_word_converter))
        convert.addAction(self._act("图片转 PDF…", self.images_to_pdf))
        convert.addAction(self._act("Markdown 转 PDF…", self.convert_markdown))
        convert.addSeparator()
        convert.addAction(self._act("PDF 转 Word…", lambda: self.open_word_converter(direction="pdf_to_word")))
        self.convert_action = self._act("PDF 转 Excel…", lambda: self.open_export_tool(2))
        self.image_export_action = self._act("PDF 转图片…", lambda: self.open_export_tool(5))
        self.raster_action = self._act('PDF 转图片版…', self.rasterize_document)
        convert.addAction(self.convert_action)
        convert.addAction(self._act("PDF 转 TXT…", self.export_text))
        convert.addAction(self.image_export_action)
        convert.addAction(self.raster_action)
        view = self._menubar.addMenu("视图")
        view.addAction(self._act("阅读", lambda: self.stack.setCurrentIndex(0)))
        view.addAction(self._act("整理页面", lambda: self.stack.setCurrentIndex(1)))
        view.addAction(self._act("大纲书签", self.open_outline))
        view.addSeparator()
        view.addMenu(self.reading_settings_menu)
        view.addAction(self.reading_orb.restore_action)
        view.addSeparator()
        view.addAction(self.act_pages)
        view.addAction(self.act_tools)
        view.addAction(self._act('恢复界面', self.reset_ui_preferences))
        view.addSeparator()
        view.addAction(self._act("放大", self.zoom_in, QKeySequence.StandardKey.ZoomIn))
        view.addAction(self._act("缩小", self.zoom_out, QKeySequence.StandardKey.ZoomOut))
        view.addAction(self._act("适合宽度", self.zoom_fit, QKeySequence("Ctrl+0")))
        self.about_action = self._act("关于", self.show_about)
        self.about_action.setStatusTip("查看版本、检查更新、项目地址与赞赏信息")
        self._menubar.addAction(self.about_action)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._workbench_bar())
        self.tab_bar = DocumentTabBar(self)
        layout.addWidget(self.tab_bar)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.stack.currentChanged.connect(self._sync_mode)

    def show_about(self) -> None:
        from app.aboutui import AboutDialog
        dialog = AboutDialog(self, updates=self.updates)
        dialog.exec()
        dialog.deleteLater()

    def _update_release_notice(self) -> None:
        available = self.updates.update_available
        self.about_action.setText("关于 · 有新版本" if available else "关于")
        self.about_action.setStatusTip(
            f"发现新版本 {self.updates.latest_tag}，点击获取" if available
            else "查看版本、检查更新、项目地址与赞赏信息")
        if available and self._notified_update_tag != self.updates.latest_tag:
            self._notified_update_tag = self.updates.latest_tag
            self.statusBar().showMessage(f"发现新版本 {self.updates.latest_tag}，点击“关于 · 有新版本”获取。", 15000)

    def manage_storage(self) -> None:
        from app.storageui import StorageDialog
        try:
            dialog = StorageDialog(self)
            dialog.exec()
            dialog.deleteLater()
        except OSError as exc:
            QMessageBox.warning(self, '存储管理', str(exc))

    def _workbench_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("workbench")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(2)
        bar.setFixedHeight(40)
        bar.setStyleSheet("QFrame#workbench QToolButton { padding: 0; min-width: 26px; min-height: 26px; } QSpinBox { padding: 2px; min-height: 24px; }")
        actions = QHBoxLayout()
        actions.setSpacing(2)
        for action, icon in (
            (self.open_action, "folder-open"), (self.append_action, "folder-plus"),
            (self.save_action, "save"), (self.save_as_action, "save-as"),
            (self.print_action, "print"),
            (self.undo_action, "undo"), (self.redo_action, "redo"),
        ):
            button = self._icon_btn(icon, action.text(), action.trigger)
            button.setFixedSize(28, 28)
            actions.addWidget(button)
            def sync(a=action, b=button):
                b.setEnabled(a.isEnabled())
                shortcut = a.shortcut().toString()
                b.setToolTip(f"{a.text()} ({shortcut})" if shortcut else a.text())
            action.changed.connect(sync)
            sync()
        layout.addLayout(actions)
        layout.addSpacing(6)
        self.mode_switch = QFrame(bar)
        self.mode_switch.setObjectName('modeSwitch')
        self.mode_switch.setAccessibleName('工作模式')
        self.mode_switch.setFixedHeight(32)
        modes = QHBoxLayout(self.mode_switch)
        modes.setContentsMargins(2, 1, 2, 1)
        modes.setSpacing(2)
        document = QHBoxLayout()
        document.setSpacing(2)
        self.mode_group = QButtonGroup(self)
        self.mode_buttons = []
        for index, (label, icon) in enumerate((("阅读", "book-open"), ("整理页面", "layout-grid"))):
            button = self._icon_btn(icon, label, lambda checked=False, i=index: self.stack.setCurrentIndex(i))
            button.setFixedSize(28, 28)
            button.setCheckable(True)
            button.setIcon(toolbar_icon(icon, '#4D6B67'))
            button.setToolTip(f'{label}模式')
            button.toggled.connect(lambda checked, b=button, name=icon:
                                   b.setIcon(toolbar_icon(name, '#FFFFFF' if checked else '#4D6B67')))
            self.mode_group.addButton(button, index)
            self.mode_buttons.append(button)
            modes.addWidget(button)
        self.mode_buttons[0].setChecked(True)
        layout.addWidget(self.mode_switch)
        layout.addSpacing(6)
        self.review_button = self._icon_btn("pen", "审阅与文字修正", self.toggle_annotation_tools)
        self.review_button.setFixedSize(28, 28)
        self.review_button.setCheckable(True)
        document.addWidget(self.review_button)
        self.search_button = self._action_btn(self.find_action)
        self.search_button.setFixedSize(28, 28)
        document.addWidget(self.search_button)
        self.document_name = QLabel(bar)
        self.document_name.setObjectName("documentName")
        self.document_name.setMinimumWidth(80)
        self.document_name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.document_name.hide()
        self.save_state = QLabel(bar)
        self.save_state.setObjectName("saveState")
        self.save_state.hide()
        layout.addLayout(document)
        layout.addSpacing(6)
        for icon, label, slot in (
            ("rotate-ccw", "左转所选页", lambda: self.rotate(-90)),
            ("rotate-cw", "右转所选页", lambda: self.rotate(90)),
            ("trash", "删除所选页", self.delete_pages),
            ("scissors", "提取所选页", self.extract_selected),
        ):
            button = self._icon_btn(icon, label, slot, danger=icon == "trash")
            button.setFixedSize(28, 28)
            self._selection_buttons.append(button)
            layout.addWidget(button)
        self.split_tool_button = self._icon_btn("split", "拆分文件", lambda: self.open_export_tool(1))
        self.convert_tool_button = self._icon_btn("table", "PDF 转 Excel", lambda: self.open_export_tool(2))
        for button in (self.split_tool_button, self.convert_tool_button):
            button.setFixedSize(28, 28)
            layout.addWidget(button)
        layout.addStretch()
        layout.addWidget(self.navigation_bar)
        for action in (self.act_pages, self.act_tools):
            button = self._action_btn(action)
            button.setFixedSize(28, 28)
            layout.addWidget(button)
        return bar

    def open_export_tool(self, index: int) -> None:
        self.stack.setCurrentIndex(0)
        self.act_tools.setChecked(True)
        self.export_tools.setCurrentIndex(index)

    def _sync_mode(self, index: int) -> None:
        self.mode_buttons[index].setChecked(True)
        self.navigation_bar.setVisible(bool(self.ws.pages) and index == 0)
        if index == 1 and hasattr(self, "annotation_tools"):
            if self.annotation_tools.deactivate() is False:
                self.stack.setCurrentIndex(0)
                return
        if index == 0:
            self._reading_settle_timer.stop()
            self.rendering.scrolling = self._scrolling = False
            self._update_preview(lightweight=True)
            self.rendering._dispatch()
        else:
            self._reading_settle_timer.stop()
            self.rendering.scrolling = self._scrolling = False
            self.rendering.prune()
            self.rendering._dispatch()
        self._prune_visible_thumbs()
        self._sync_selection()
        view = self.arrange_view if index else self.left_view
        current = self.ws.preview_index()
        if current is not None:
            view.scrollTo(self.model.index(current))

    def _sync_controls(self) -> None:
        count = len(self.ws.pages)
        read_only = self.store.operation_reason(self.ws.pages, "modify")
        self._controls_read_only = bool(read_only)
        copy_reason = self.store.operation_reason(self.ws.pages, "copy")
        protection = read_only or copy_reason or self.store.operation_reason(self.ws.pages, "print")
        ofd_copy = any(ref.source_path.lower().endswith('.ofd') for ref in self.ws.pages)
        if ofd_copy and not protection:
            protection = 'OFD 工作副本 · 原文件保留 · 保存时另存为 PDF'
        self.protection_label.setText(protection)
        self.protection_label.setVisible(bool(protection))
        unknown_signature = any(self.store._source(ref.source_id).safety.signature == 'unknown' for ref in self.ws.pages)
        raster_reason = self.store.operation_reason(self.ws.pages, 'raster_copy')
        self.protection_copy_button.setVisible(unknown_signature)
        self.protection_copy_button.setEnabled(bool(count) and not raster_reason)
        self.protection_copy_button.setToolTip(raster_reason or '仅复制页面影像；原件不变，副本不保留签名效力。')
        self._sync_page_navigation()
        save_reason = read_only
        if read_only and any(page.edits for page in self.ws.pages):
            save_operation = self.store.write_operation(self.ws.pages, WriteOptions(outline=self.ws.outline))
            save_reason = self.store.operation_reason(self.ws.pages, save_operation)
        for action in (self.save_action, self.save_as_action):
            action.setEnabled(bool(count) and not save_reason)
        self.close_file_action.setEnabled(bool(count) or bool(self.ws.path) or self.ws.dirty)
        self.compress_action.setEnabled(bool(count) and not read_only and not copy_reason)
        self.ocr_action.setEnabled(bool(count) and not read_only and not copy_reason)
        self.watermark_action.setEnabled(bool(count) and not self.store.operation_reason(self.ws.pages, "annotate"))
        self.image_export_action.setEnabled(bool(count) and not copy_reason)
        self.capture_action.setEnabled(bool(count) and not copy_reason)
        self.raster_action.setEnabled(bool(count) and not raster_reason)
        self.encrypt_action.setEnabled(bool(count) and not read_only)
        sources = [self.store._source(sid) for sid in dict.fromkeys(ref.source_id for ref in self.ws.pages)]
        encrypted = any(source.safety.encrypted for source in sources)
        signature_reason = next((source.safety.read_only_reason for source in sources
                                 if source.safety.signature != "none"), "")
        decrypt_hint = ("请先打开加密 PDF。" if not count else
                        signature_reason or ("另存未加密副本；有权限限制时需输入管理密码。" if encrypted
                                             else "当前文档未加密，无需解密。"))
        self.decrypt_action.setEnabled(encrypted and not signature_reason)
        self.decrypt_action.setToolTip(decrypt_hint)
        self.editable_action.setText('影像副本…' if unknown_signature else '编辑副本…')
        self.editable_button.setText('制作影像副本…' if unknown_signature else '制作可编辑副本…')
        self.editable_action.setEnabled(bool(count) and not (raster_reason if unknown_signature
                                        else self.store.operation_reason(self.ws.pages, 'editable_copy')))
        self.compress_button.setEnabled(self.compress_action.isEnabled())
        self.image_export_button.setEnabled(self.image_export_action.isEnabled())
        self.encrypt_button.setEnabled(self.encrypt_action.isEnabled())
        self.decrypt_button.setEnabled(self.decrypt_action.isEnabled())
        self.decrypt_button.setToolTip(decrypt_hint)
        self.editable_button.setEnabled(self.editable_action.isEnabled())
        self.print_action.setEnabled(bool(count) and not self.store.operation_reason(self.ws.pages, "print"))
        self.append_action.setEnabled(not read_only)
        if hasattr(self, "insert_action"):
            self.insert_action.setEnabled(not read_only)
        if hasattr(self, "insert_button"):
            self.insert_button.setEnabled(not read_only)
        for view in (self.left_view, self.arrange_view):
            view.setDragEnabled(not read_only)
            view.setAcceptDrops(not read_only)
        selected = bool(self.ws.target_indices())
        self.delete_action.setEnabled(selected and not read_only)
        for button in self._selection_buttons:
            button.setEnabled(selected and not read_only)
        self.tool_selection.setText(read_only or self._selection_caption() or "选择页面后可旋转或删除")
        self.export_tools.setEnabled(bool(count))
        self.extract_action.setEnabled(bool(count) and not read_only and not copy_reason)
        self.split_action.setEnabled(bool(count) and not read_only and not copy_reason)

        self.convert_action.setEnabled(bool(count) and not copy_reason)
        self.split_tool_button.setEnabled(bool(count) and not read_only and not copy_reason)
        self.convert_tool_button.setEnabled(bool(count) and not copy_reason)
        self._set_pages_visible(self.act_pages.isChecked())
        self._set_tools_visible(self.act_tools.isChecked())
        self.navigation_bar.setVisible(bool(count) and not self._in_arrange())
        self.reading_orb.sync()
        self.mode_buttons[1].setEnabled(bool(count) or self._in_arrange())
        self.review_button.setEnabled(bool(count))
        self._update_export_controls()
        name = os.path.basename(self.ws.path) if self.ws.path else ("未命名文档" if count or self.ws.dirty else
            os.path.basename(self._opening_paths[0]) if self._opening_paths else "尚未打开文档")
        self.document_name.setText(name)
        self.document_name.setToolTip(self.ws.path or "保存时选择新文件位置")
        state = "未保存 · 保存为新文件" if self.ws.dirty and not self.ws.path else (
            "有未保存的修改" if self.ws.dirty else ("已保存" if self.ws.path else "本机处理"))
        if read_only:
            state = "只读"
        elif self._opening_paths and not count:
            state = "正在打开…"
        self.save_state.setText(state)
        is_ofd = bool(self.ws.path and self.ws.path.lower().endswith('.ofd'))
        if is_ofd:
            self.save_state.setText('OFD 工作副本 · 原件保留')
        self.save_action.setText('另存为 PDF' if is_ofd else '保存')
        self.save_state.setToolTip('当前显示为转换后的 PDF，不提供 OFD 原件签名验证。' if ofd_copy
                                  else read_only or self.ws.path or "文件仅在本机处理")

    def _sync_page_navigation(self):
        count = len(self.ws.pages)
        current = self.ws.preview_index()
        first = last = current
        if current is not None and self.preview_view.spread and current < len(self.preview_view.page_rects):
            pages = self.preview_view.pages_in_spread(current)
            first, last = pages.start, pages.stop - 1
        self.page_number.blockSignals(True)
        self.page_number.setRange(1 if count else 0, count)
        self.page_number.setValue(first + 1 if first is not None else 0)
        self.page_number.blockSignals(False)
        self.page_number.setEnabled(bool(count))
        paired = first is not None and last != first
        self.page_total.setText(f"–{last + 1} / {count} 页" if paired else f"/ {count} 页")
        self.previous_page.setEnabled(first is not None and first > 0)
        self.next_page.setEnabled(last is not None and last < count - 1)
        for button, label in ((self.previous_page, "上一"), (self.next_page, "下一")):
            name = label + ("组页面" if self.preview_view.spread else "页")
            button.setAccessibleName(name)
            button.setToolTip(name + (" (Alt+Left)" if button is self.previous_page else " (Alt+Right)"))
        self.page_number.setToolTip("输入任意页码，按 Enter 跳转到所在页面组" if self.preview_view.spread
                                    else "输入页码，按 Enter 跳转")

    def go_to_page(self, number: int, *, reveal=True) -> None:
        if not 1 <= number <= len(self.ws.pages):
            return
        self.ws.select_index(number - 1)
        self._sync_selection()
        self.left_view.scrollTo(self.model.index(number - 1))
        navigating = self._scroll_navigation
        self._scroll_navigation = navigating or not reveal
        try:
            self._update_preview()
        finally:
            self._scroll_navigation = navigating
        if reveal:
            self.preview_view.jump_to_page(number - 1)

    def step_page(self, delta: int) -> None:
        current = self.ws.preview_index()
        if current is not None:
            target = current + delta
            if self.preview_view.spread and not self._in_arrange():
                pages = self.preview_view.pages_in_spread(current)
                target = pages.stop if delta > 0 else pages.start - 1
                if 0 <= target < len(self.ws.pages):
                    target = self.preview_view.pages_in_spread(target).start
            self.go_to_page(target + 1)

    def _set_reading_layout(self, mode: str) -> None:
        if mode not in self.reading_layout_actions:
            return
        self._reading_layout = mode
        if mode in ('spread', 'four'):
            self._zoom_mode = 'page'
            self.reading_zoom_actions['page'].setChecked(True)
        action = self.reading_layout_actions[mode]
        action.setChecked(True)
        if self.ws.pages:
            self.stack.setCurrentIndex(0)
        self._update_preview(lightweight=True)
        if mode in ('spread', 'four'):
            self._apply_view_zoom()
        self.reading_orb.sync()

    def open_search(self) -> None:
        self.search_bar.open_search()

    def _update_export_controls(self) -> None:
        count = len(self.ws.pages)
        for kind, button, label in (
            ("extract", self.extract_button, self.extract_feedback),
            ("split", self.split_button, self.split_feedback),
            ("convert", self.convert_button, self.convert_feedback),
        ):
            error = ""
            summary = ""
            try:
                if not count:
                    raise ValueError("请先打开 PDF")
                reason = self.store.operation_reason(self.ws.pages, "copy")
                if kind != "convert":
                    reason = reason or self.store.operation_reason(self.ws.pages, "modify")
                if reason:
                    raise ValueError(reason)
                if kind == "extract":
                    self.ws.extract_indices(self.extract_range.text())
                elif kind == "split":
                    if self.split_range_mode.isChecked():
                        outputs = len(parse_range_segments(self.split_range.text(), count))
                    else:
                        step = self.split_count.value() if self.split_every.isChecked() else 1
                        outputs = (count + step - 1) // step
                    summary = f"预计输出 {outputs} 个文件"
                elif kind == "convert":
                    if self.convert_range_mode.isChecked():
                        parse_page_range(self.convert_range.text(), count)
                    elif self.convert_selected.isChecked() and not self.ws.target_indices():
                        raise ValueError("请先选择要转换的页面")
            except ValueError as exc:
                error = str(exc)
            button.setEnabled(not error)
            button.setToolTip(error)
            label.setText(error or summary)
            label.setVisible(bool(error or summary))

    def _act(self, text, slot, shortcut=None) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(shortcut)
        action.triggered.connect(slot)
        return action

    def _main_view(self) -> QWidget:
        split = QSplitter()
        split.setHandleWidth(8)
        split.setChildrenCollapsible(True)
        self.left_panel = QFrame()
        self.left_panel.setObjectName("card")
        self.left_panel.setMinimumWidth(138)
        left_l = QVBoxLayout(self.left_panel)
        left_l.setContentsMargins(10, 10, 10, 10)
        cap = QLabel("页面")
        cap.setObjectName("caption")
        self.left_view = self._list_view(False)
        self.sidebar_mode = QComboBox()
        self.sidebar_mode.addItems(["页面缩略图", "大纲书签"])
        self.sidebar_mode.setAccessibleName("侧栏视图")
        left_l.addWidget(self.sidebar_mode)
        self.sidebar_stack = QStackedWidget()
        self.sidebar_stack.addWidget(self.left_view)
        left_l.addWidget(self.sidebar_stack)
        self.sidebar_mode.currentIndexChanged.connect(self.show_outline)
        split.addWidget(self.left_panel)
        center = QFrame()
        center.setObjectName("card")
        c_l = QVBoxLayout(center)
        self._reader_layout = c_l
        c_l.setContentsMargins(0, 0, 0, 0)
        c_l.setSpacing(0)
        self.banner = QLabel("", center)
        self.banner.setObjectName("hint")
        self.banner.setWordWrap(True)
        page_controls = QWidget()
        page_bar = QHBoxLayout(page_controls)
        page_bar.setContentsMargins(0, 0, 0, 0)
        page_bar.setSpacing(4)
        self.previous_page = self._icon_btn("chevron-left", "上一页", lambda: self.step_page(-1))
        page_bar.addWidget(self.previous_page)
        self.page_number = QSpinBox()
        self.page_number.setAccessibleName("跳转到页码")
        self.page_number.setToolTip("输入页码，按 Enter 跳转")
        self.page_number.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.page_number.setKeyboardTracking(False)
        self.page_number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_number.setMinimumWidth(32)
        self.page_number.setMaximumWidth(64)
        self.page_number.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.page_number.setStyleSheet("padding: 1px 2px; min-height: 22px;")
        self.page_number.valueChanged.connect(self.go_to_page)
        page_bar.addWidget(self.page_number)
        self.page_total = QLabel("/ 0 页")
        page_bar.addWidget(self.page_total)
        self.next_page = self._icon_btn("chevron-right", "下一页", lambda: self.step_page(1))
        page_bar.addWidget(self.next_page)
        page_bar.addStretch()
        self._reader_zoom_controls = QWidget()
        zoom_bar = QHBoxLayout(self._reader_zoom_controls)
        zoom_bar.setContentsMargins(0, 0, 0, 0)
        zoom_bar.setSpacing(4)
        zoom_bar.addStretch()
        zoom_out_button = self._icon_btn("zoom-out", "缩小", self.zoom_out, shortcut="Ctrl+-")
        zoom_bar.addWidget(zoom_out_button)
        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("hint")
        self.zoom_label.setMinimumWidth(48)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        zoom_bar.addWidget(self.zoom_label)
        zoom_in_button = self._icon_btn("zoom-in", "放大", self.zoom_in, shortcut="Ctrl+=")
        zoom_bar.addWidget(zoom_in_button)
        zoom_fit_button = self._icon_btn("fit-width", "适合宽度", self.zoom_fit, shortcut="Ctrl+0")
        zoom_bar.addWidget(zoom_fit_button)
        self.preview_empty = QWidget()
        welcome = QVBoxLayout(self.preview_empty)
        welcome.addStretch()
        title = QLabel("从一份 PDF 或 OFD 开始")
        title.setObjectName("welcomeTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome.addWidget(title)
        hint = QLabel("阅读、整理页面、提取和打印\n文件在本机处理，无需上传")
        hint.setObjectName("hint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome.addWidget(hint)
        self.welcome_open = self._btn("打开 PDF / OFD", self.open_files, primary=True, icon="folder-open")
        welcome.addWidget(self.welcome_open, 0, Qt.AlignmentFlag.AlignHCenter)
        drop_hint = QLabel("也可以把文件拖到这里 · Ctrl+O")
        drop_hint.setObjectName("hint")
        drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome.addWidget(drop_hint)
        welcome.addStretch()
        self.preview_view = PreviewView()
        self.preview_view.zoom_steps.connect(self._on_zoom_steps)
        self.preview_view.page_step.connect(self.step_page)
        self.preview_view.view_changed.connect(self._detail_timer.start)
        self.preview_view.view_changed.connect(self._reading_view_changed)
        self.preview_view.viewport().installEventFilter(self)
        self.preview_stack = QStackedWidget()
        self.preview_stack.addWidget(self.preview_empty)
        self.preview_stack.addWidget(self.preview_view)
        self.preview_loading = QLabel("正在打开 PDF…")
        self.preview_loading.setObjectName("hint")
        self.preview_loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_loading.setTextFormat(Qt.TextFormat.PlainText)
        self.preview_loading.setWordWrap(True)
        self.preview_stack.addWidget(self.preview_loading)
        self.details = QLabel("", center)
        self.details.setObjectName("hint")
        self.details.setWordWrap(True)
        self.banner.hide()
        self.navigation_bar = QFrame()
        self.navigation_bar.setObjectName("readerToolbar")
        self.navigation_bar.setStyleSheet('''
            QFrame#readerToolbar { background: transparent; border: none; }
            QFrame#readerToolbar QWidget { background: transparent; }
            QFrame#readerToolbar QToolButton { min-width: 26px; min-height: 26px; padding: 0; border-radius: 6px; }
            QFrame#readerToolbar QLabel { font-size: 13px; }
        ''')
        navigation = QHBoxLayout(self.navigation_bar)
        navigation.setContentsMargins(0, 0, 0, 0)
        navigation.setSpacing(8)
        navigation.addWidget(page_controls)
        navigation.addWidget(self._reader_zoom_controls)
        self.navigation_bar.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.reading_settings_menu = layout_menu = QMenu('阅读设置', self)
        layout_menu.setObjectName('readingSettingsMenu')
        layout_menu.setAccessibleName('阅读设置')
        layout_menu.setToolTipsVisible(True)
        layout_menu.setStyleSheet('''
            QMenu#readingSettingsMenu {
                background: #FFFFFF; color: #29483B; border: 1px solid #D5E5DD;
                border-radius: 9px; padding: 6px; font-size: 13px;
            }
            QMenu#readingSettingsMenu::item { padding: 7px 18px 7px 10px; margin: 1px 3px; border-radius: 5px; }
            QMenu#readingSettingsMenu::item:selected { background: #EDF6F1; }
            QMenu#readingSettingsMenu::item:checked { background: #E3F1E9; color: #0F6557; font-weight: 600; }
            QMenu#readingSettingsMenu::separator { background: #E0EAE5; height: 1px; margin: 5px 7px; }
        ''')
        self.reading_fallback_notice = QAction('阅读区较窄，当前暂用单页', layout_menu)
        self.reading_fallback_notice.setEnabled(False)
        self.reading_fallback_notice.setVisible(False)
        layout_menu.addAction(self.reading_fallback_notice)
        layout_menu.addSection('页面布局')
        self.reading_layout_group = QActionGroup(self)
        self.reading_layout_actions = {}
        for mode, label, icon, key in (('single', '单页连续', 'page-single', 'Ctrl+1'),
                                       ('spread', '双页对开', 'page-spread', 'Ctrl+2'),
                                       ('four', '四页预览（2×2）', 'layout-grid', 'Ctrl+4')):
            action = self._act(label, lambda checked=False, m=mode: self._set_reading_layout(m), QKeySequence(key))
            action.setIcon(toolbar_icon(icon, '#526B63', 18))
            action.setCheckable(True)
            action.setChecked(mode == self._reading_layout)
            self.reading_layout_group.addAction(action)
            self.reading_layout_actions[mode] = action
            self.addAction(action)
            layout_menu.addAction(action)
        layout_menu.addSection('页面适配')
        self.reading_zoom_group = QActionGroup(self)
        self.reading_zoom_actions = {}
        for mode, label, icon in (('page', '适合页面', 'page-single'),
                                   ('width', '适合宽度', 'fit-width'),
                                   ('custom', '手动缩放', 'zoom-in')):
            action = self._act(label, lambda checked=False, m=mode: self._set_zoom_mode(m))
            action.setIcon(toolbar_icon(icon, '#526B63', 18))
            action.setCheckable(True)
            action.setChecked(mode == self._zoom_mode)
            action.setToolTip('使用顶部加减或 Ctrl+滚轮调整' if mode == 'custom' else label)
            self.reading_zoom_group.addAction(action)
            self.reading_zoom_actions[mode] = action
            layout_menu.addAction(action)
        from app.readingsettings import ReadingSettingsOrb
        self.reading_orb = ReadingSettingsOrb(self, layout_menu)
        self.page_number.setFixedHeight(28)
        for button in self.navigation_bar.findChildren(QToolButton):
            button.setFixedSize(28, 28)
        focus_order = (self.previous_page, self.page_number, self.next_page, zoom_out_button,
                       zoom_in_button, zoom_fit_button)
        for before, after in zip(focus_order, focus_order[1:]):
            QWidget.setTabOrder(before, after)
        from app.searchui import SearchBar
        self.search_bar = SearchBar(self)
        from app.textselection import TextSelection
        self.text_selection = TextSelection(self)
        from app.readerinteraction import ReaderInteraction
        self.reader_interaction = ReaderInteraction(self)
        from app.regioncapture import RegionCapture
        self.region_capture = RegionCapture(self)
        c_l.addWidget(self.preview_stack, 1)
        self.details.hide()
        split.addWidget(center)
        self.tools_panel = self._tools()
        split.addWidget(self.tools_panel)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 4)
        split.setStretchFactor(2, 0)
        split.setSizes([170, 760, 280])
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)
        return wrap

    def _arrange_view(self) -> QWidget:
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(12, 10, 12, 12)
        bar = QHBoxLayout()
        bar.setSpacing(4)
        for icon, label, slot in (
            ("rotate-ccw", "左转", lambda: self.rotate(-90)),
            ("rotate-cw", "右转", lambda: self.rotate(90)),
            ("trash", "删除页面", self.delete_pages),
        ):
            button = self._btn(label, slot, icon=icon, danger=icon == "trash")
            self._selection_buttons.append(button)
            bar.addWidget(button)
        self.insert_button = self._btn("插入页面…", self.insert_files, icon="folder-plus")
        self.insert_button.setToolTip("插入 PDF 页面（已选页面后或末尾）")
        bar.addWidget(self.insert_button)
        bar.addWidget(self._btn("全选", self.select_all_pages, icon="select-all"))
        selected_export = self._btn("提取所选页…", self.extract_selected, icon="scissors")
        self._selection_buttons.append(selected_export)
        bar.addWidget(selected_export)
        self.arrange_sel = QLabel("")
        self.arrange_sel.setObjectName("hint")
        bar.addWidget(self.arrange_sel)
        bar.addStretch()
        bar.addWidget(self._icon_btn("zoom-out", "缩小", self.arrange_zoom_out, shortcut="Ctrl+-"))
        self.arrange_zoom_label = QLabel("100%")
        self.arrange_zoom_label.setObjectName("hint")
        self.arrange_zoom_label.setMinimumWidth(48)
        self.arrange_zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bar.addWidget(self.arrange_zoom_label)
        bar.addWidget(self._icon_btn("zoom-in", "放大", self.arrange_zoom_in, shortcut="Ctrl+="))
        bar.addWidget(self._icon_btn("fit-width", "适合窗口", self.arrange_zoom_fit, shortcut="Ctrl+0"))
        hint = QLabel("拖动排序，双击阅读。按住 Alt 悬停可放大，或右键选择放大预览。Ctrl 加选，Shift 连选；Ctrl + 滚轮缩放。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        card = QFrame()
        card.setObjectName("card")
        c_l = QVBoxLayout(card)
        self.arrange_view = self._list_view(True)
        self.arrange_view.zoom_steps.connect(self._on_arrange_zoom_steps)
        self.arrange_view.doubleClicked.connect(self.read_arranged_page)
        from app.arrangepreview import ArrangePreview
        self.arrange_preview = ArrangePreview(self, self.arrange_view)
        c_l.addWidget(self.arrange_view)
        lay.addLayout(bar)
        lay.addWidget(hint)
        lay.addWidget(card, 1)
        return wrap

    def read_arranged_page(self, index: QModelIndex) -> None:
        if not index.isValid() or not 0 <= index.row() < len(self.ws.pages):
            return
        self.ws.select_index(index.row())
        self.stack.setCurrentIndex(0)
        self._update_preview()
        self.preview_view.jump_to_page(index.row())
        self.preview_view.setFocus()

    def _tools(self) -> QWidget:
        box = QFrame()
        box.setObjectName("card")
        box.setMinimumWidth(280)
        box.setMaximumWidth(320)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)
        self.tool_selection = self._caption("选择页面后可旋转或删除")
        self.tool_selection.setWordWrap(True)
        selection = QHBoxLayout()
        selection.addWidget(self.tool_selection, 1)
        self.select_pages_button = self._btn("全选", self.select_all_pages)
        self.select_pages_button.setStyleSheet("min-height: 26px; padding: 4px 8px;")
        selection.addWidget(self.select_pages_button)
        lay.addLayout(selection)
        page = QHBoxLayout()
        page.setSpacing(4)
        for icon, label, slot in (
            ("rotate-ccw", "左转", lambda: self.rotate(-90)),
            ("rotate-cw", "右转", lambda: self.rotate(90)),
            ("trash", "删除", self.delete_pages),
        ):
            button = self._btn(label, slot, icon=icon, danger=icon == "trash")
            button.setStyleSheet("padding: 4px 6px;")
            self._selection_buttons.append(button)
            page.addWidget(button)
        lay.addLayout(page)
        lay.addWidget(self._sep())
        lay.addWidget(self._caption("导出与转换"))
        self.export_tools = QToolBox()
        self.export_tools.setAccessibleName("导出与转换工具")
        lay.addWidget(self.export_tools, 1)

        def section(title):
            content = QWidget()
            inner = QVBoxLayout(content)
            inner.setContentsMargins(4, 12, 4, 12)
            inner.setSpacing(10)
            self.export_tools.addItem(content, title)
            return inner

        extract = section("提取页面 · 一个新文件")
        hint = QLabel("留空使用所选页面，或输入页码范围。")
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        extract.addWidget(hint)
        self.extract_range = QLineEdit()
        self.extract_range.setPlaceholderText("例如 1-3,5")
        self.extract_range.setAccessibleName("提取页码范围")
        extract.addWidget(self.extract_range)
        self.extract_feedback = self._caption("")
        self.extract_feedback.setWordWrap(True)
        extract.addWidget(self.extract_feedback)
        self.extract_button = self._btn("提取到新 PDF…", self.extract, icon="scissors")
        extract.addWidget(self.extract_button)
        extract.addStretch()
        split = section("拆分文件 · 多个 PDF")
        split.parentWidget().setObjectName("splitPanel")
        split.setContentsMargins(8, 12, 8, 12)
        split.setSpacing(8)
        self.split_each = QRadioButton("每页一个文件")
        self.split_every = QRadioButton("每几页一个文件")
        self.split_count = QSpinBox()
        self.split_count.setRange(1, 1_000_000)
        self.split_count.setValue(5)
        self.split_count.setSuffix(" 页")
        self.split_count.setAccessibleName("每个拆分文件的页数")
        self.split_count.setFixedWidth(104)
        self.split_count.setFixedHeight(36)
        self.split_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.split_count.hide()
        self.split_every.toggled.connect(self.split_count.setVisible)
        self.split_count.valueChanged.connect(self._update_export_controls)
        self.split_range_mode = QRadioButton("按页码范围")
        self.split_mode_group = QButtonGroup(self)
        self.split_mode_group.addButton(self.split_each)
        self.split_mode_group.addButton(self.split_every)
        self.split_mode_group.addButton(self.split_range_mode)
        self.split_each.setChecked(True)
        split.addWidget(self.split_each)
        interval = QHBoxLayout()
        interval.setContentsMargins(0, 0, 0, 0)
        interval.setSpacing(8)
        interval.addWidget(self.split_every, 1)
        interval.addWidget(self.split_count)
        split.addLayout(interval)
        split.addWidget(self.split_range_mode)
        self.split_range = QLineEdit()
        self.split_range.setPlaceholderText("如 1-3,5,7-9，每段一个文件")
        self.split_range.setAccessibleName("拆分页码范围")
        self.split_range.setVisible(False)
        self.split_range_mode.toggled.connect(self.split_range.setVisible)
        split.addWidget(self.split_range)
        split.addSpacing(4)
        self.split_feedback = QLabel("")
        self.split_feedback.setObjectName("splitFeedback")
        self.split_feedback.setWordWrap(True)
        split.addWidget(self.split_feedback)
        self.split_button = self._btn("拆分到文件夹…", self.split, icon="split", primary=True)
        self.split_button.setObjectName("splitSubmit")
        split.addWidget(self.split_button)
        split.addStretch()
        convert = section("PDF 转 Excel")
        content_label = QLabel("导出内容")
        self.convert_content = QComboBox()
        self.convert_content.addItem("全部内容（按页导出）", True)
        self.convert_content.addItem("仅表格", False)
        self.convert_content.setAccessibleName("Excel 导出内容")
        content_label.setBuddy(self.convert_content)
        convert.addWidget(content_label)
        convert.addWidget(self.convert_content)
        self.convert_all = QRadioButton("全部页面")
        self.convert_selected = QRadioButton("所选页面")
        self.convert_range_mode = QRadioButton("按页码范围")
        self.convert_mode_group = QButtonGroup(self)
        for radio in (self.convert_all, self.convert_selected, self.convert_range_mode):
            self.convert_mode_group.addButton(radio)
        self.convert_all.setChecked(True)
        convert.addWidget(self.convert_all)
        convert.addWidget(self.convert_selected)
        convert.addWidget(self.convert_range_mode)
        self.convert_range = QLineEdit()
        self.convert_range.setPlaceholderText("如 1-3,5；默认全部")
        self.convert_range.setAccessibleName("转换页码范围")
        self.convert_range.setVisible(False)
        self.convert_range_mode.toggled.connect(self.convert_range.setVisible)
        convert.addWidget(self.convert_range)
        hint = QLabel("全部内容按页保留文字、表格和图片。扫描文字需先完成 OCR；金额与行列请核对原件。")
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        convert.addWidget(hint)
        self.convert_feedback = self._caption("")
        self.convert_feedback.setWordWrap(True)
        convert.addWidget(self.convert_feedback)
        self.convert_button = self._btn("转换为 Excel…", self.convert_to_excel, icon="table", primary=True)
        convert.addWidget(self.convert_button)
        convert.addStretch()
        compress = section("压缩 PDF")
        hint = QLabel("无损、高质量、均衡或自定义压缩；查看大小与页面预览后另存。")
        hint.setWordWrap(True)
        compress.addWidget(hint)
        self.compress_button = self._btn("压缩并预览…", self.compress_pdf)
        compress.addWidget(self.compress_button)
        compress.addStretch()
        images = section("图片转 PDF")
        hint = QLabel("按列表顺序生成页面，多帧 TIFF 按帧展开。")
        hint.setWordWrap(True)
        images.addWidget(hint)
        images.addWidget(self._btn("选择与排列图片…", self.images_to_pdf))
        images.addStretch()
        export_images = section("PDF 转图片")
        hint = QLabel("当前页、所选页或页码范围；PNG / JPEG，150 / 300 DPI。")
        hint.setWordWrap(True)
        export_images.addWidget(hint)
        self.image_export_button = self._btn("选择范围并导出…", self.pdf_to_images)
        export_images.addWidget(self.image_export_button)
        export_images.addStretch()
        security = section("加密、解密与签名保护")
        self.encrypt_button = self._btn("加密另存…", self.encrypt_copy)
        self.decrypt_button = self._btn("解密另存…", self.decrypt_copy)
        self.editable_button = self._btn("制作可编辑副本…", self.editable_copy)
        security.addWidget(self.encrypt_button)
        security.addWidget(self.decrypt_button)
        security.addWidget(self.editable_button)
        hint = QLabel("解密后另存为无需密码的副本，原加密文件保留；解除权限限制需要管理密码。")
        hint.setWordWrap(True)
        security.addWidget(hint)
        hint = QLabel("签名原件只读。可编辑副本保留签名外观，原数字签名效力不会保留。")
        hint.setWordWrap(True)
        security.addWidget(hint)
        security.addStretch()
        for field in (self.extract_range, self.split_range, self.convert_range):
            field.textChanged.connect(self._update_export_controls)
        for radio in (self.split_each, self.split_every, self.split_range_mode, self.convert_all, self.convert_selected, self.convert_range_mode):
            radio.toggled.connect(self._update_export_controls)
        return box

    def extract_selected(self) -> None:
        # 编排视图的快捷操作始终使用当前选择，不受另一视图输入过的范围影响。
        previous = self.extract_range.text()
        self.extract_range.clear()
        try:
            self.extract()
        finally:
            self.extract_range.setText(previous)

    def _list_view(self, arrange: bool) -> QListView:
        view = PageListView(arrange)
        view._host = self
        view.setModel(self.model)
        view.clicked.connect(self._on_click)
        view.selectionModel().currentChanged.connect(self._on_current_changed)
        view.selectionModel().selectionChanged.connect(self._on_selection_changed)
        if not arrange:
            view.viewport().installEventFilter(self)
        return view

    def _caption(self, text: str) -> QLabel:
        cap = QLabel(text)
        cap.setObjectName("caption")
        return cap

    def _icon_color(self, primary: bool, danger: bool) -> str:
        if primary:
            return "#FFFFFF"
        if danger:
            return "#B91C1C"
        return "#0F766E"

    def _icon_btn(
        self,
        icon: str,
        label: str,
        slot,
        primary: bool = False,
        danger: bool = False,
        shortcut: str = "",
    ) -> QToolButton:
        tip = f"{label} ({shortcut})" if shortcut else label
        btn = QToolButton()
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        btn.setFixedSize(ICON_BTN, ICON_BTN)
        btn.setIconSize(QSize(ICON_PX, ICON_PX))
        btn.setIcon(toolbar_icon(icon, self._icon_color(primary, danger)))
        btn.setToolTip(tip)
        btn.setStatusTip(tip)
        btn.setAccessibleName(label)
        btn.setAutoRaise(False)
        if primary:
            btn.setProperty("primary", True)
        if danger:
            btn.setProperty("danger", True)
        btn.clicked.connect(slot)
        return btn

    def _btn(
        self,
        text: str,
        slot,
        primary: bool = False,
        danger: bool = False,
        icon: str | None = None,
    ) -> QPushButton:
        btn = QPushButton(text)
        btn.setAccessibleName(text)
        btn.setToolTip(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if primary:
            btn.setProperty("primary", True)
        if danger:
            btn.setProperty("danger", True)
        if icon:
            btn.setIcon(toolbar_icon(icon, self._icon_color(primary, danger)))
            btn.setIconSize(QSize(18, 18))
        btn.clicked.connect(slot)
        return btn

    def _action_btn(self, action: QAction) -> QToolButton:
        btn = QToolButton()
        btn.setDefaultAction(action)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        btn.setFixedSize(ICON_BTN, ICON_BTN)
        btn.setIconSize(QSize(ICON_PX, ICON_PX))
        btn.setAutoRaise(False)
        btn.setAccessibleName(action.text())
        return btn

    def _sep(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #E2EEEC;")
        return line

    def _shortcuts(self) -> None:
        # Ctrl+Shift+S 已由菜单「另存为」的 StandardKey.SaveAs 提供，再挂一条会互相打架。
        for view in (self.left_view, self.arrange_view):
            shortcut = QShortcut(QKeySequence.StandardKey.Delete, view, self.delete_pages)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence("Alt+Left"), self, lambda: self.step_page(-1))
        QShortcut(QKeySequence("Alt+Right"), self, lambda: self.step_page(1))
        QShortcut(QKeySequence("Ctrl+L"), self, self.focus_page_number)
        QShortcut(QKeySequence("Ctrl+="), self, self.zoom_in)
        QShortcut(QKeySequence("F3"), self, lambda: self.search_bar.step(1))
        QShortcut(QKeySequence("Shift+F3"), self, lambda: self.search_bar.step(-1))
        QShortcut(QKeySequence("Ctrl+Tab"), self, lambda: self.step_tab(1))
        QShortcut(QKeySequence("Ctrl+Shift+Tab"), self, lambda: self.step_tab(-1))

    def focus_page_number(self) -> None:
        if self.ws.pages:
            self.stack.setCurrentIndex(0)
            self.page_number.setFocus()
            self.page_number.selectAll()

    def refresh(self, *_args) -> None:
        if getattr(self, "_refreshing", False):
            return
        self.reader_interaction.reset()
        self._refreshing = True
        self._syncing = True
        try:
            self._sync_controls()
            self._sync_tab_bar()
            self.model.reload()
            self._fit_thumbs()
            self._sync_selection()
            self._update_preview(lightweight=True)
            if hasattr(self, "outline_panel") and self.sidebar_mode.currentIndex() == 1:
                self.outline_panel.refresh()
            name = os.path.basename(self.ws.path) if self.ws.path else (
                os.path.basename(self._opening_paths[0]) if self._opening_paths and not self.ws.pages else "未命名")
            star = " *" if self.ws.dirty else ""
            self.setWindowTitle(f"{name}{star} — {APP_NAME} v{__version__}")
            self.statusBar().showMessage(self._status_line())
        finally:
            self._syncing = False
            self._refreshing = False

    def _status_line(self) -> str:
        if self._opening_paths and not self.ws.pages:
            return f"正在打开 · {os.path.basename(self._opening_paths[0])}"
        n = len(self.ws.pages)
        sel = len(self.ws.target_indices())
        parts = [f"{n} 页"]
        if sel:
            parts.append(f"已选 {sel} 页")
        if self.ws.dirty:
            parts.append("未保存")
        if self.ws.last_error:
            parts.append(self.ws.last_error)
        reason = self.store.operation_reason(self.ws.pages, "modify")
        if reason:
            parts.append(reason)
        return " · ".join(parts)

    def _selection_caption(self) -> str:
        n = len(self.ws.pages)
        sel = len(self.ws.target_indices())
        if n == 0 or sel == 0:
            return ""
        if sel == n:
            return f"已选全部 {n} 页"
        if sel == 1:
            row = next(iter(self.ws.target_indices()))
            return f"已选第 {row + 1} 页"
        return f"已选 {sel} 页"

    def _sync_selection(self, *, reading_only=False) -> None:
        n = len(self.ws.pages)
        rows = sorted(i for i in self.ws.selected if 0 <= i < n)
        if not rows and self.ws.select_all and n:
            rows = list(range(n))
        for view in ((self.left_view,) if reading_only else (self.left_view, self.arrange_view)):
            sm = view.selectionModel()
            sm.blockSignals(True)
            sm.clearSelection()
            if rows:
                selection = QItemSelection()
                for row in rows:
                    idx = self.model.index(row)
                    selection.select(idx, idx)
                sm.select(selection, QItemSelectionModel.SelectionFlag.Select)
                current = self.ws.selected_index
                if current is not None and 0 <= current < n:
                    sm.setCurrentIndex(
                        self.model.index(current),
                        QItemSelectionModel.SelectionFlag.Current,
                    )
            sm.blockSignals(False)

    def _on_selection_changed(self, _selected: QItemSelection, _deselected: QItemSelection) -> None:
        if self._syncing:
            return
        sm = self.sender()
        if not isinstance(sm, QItemSelectionModel):
            return
        rows = [idx.row() for idx in sm.selectedIndexes() if idx.isValid()]
        cur = sm.currentIndex()
        current = cur.row() if cur.isValid() else None
        self.ws.set_selection(rows, current)
        self._sync_selection()
        self._update_preview()

    def _on_click(self, index: QModelIndex) -> None:
        if self._syncing or not index.isValid():
            return
        mods = QGuiApplication.keyboardModifiers()
        if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
            self._update_preview()
            return
        if self.ws.selected != {index.row()} or self.ws.selected_index != index.row():
            self.ws.select_index(index.row())
            self._sync_selection()
        self._update_preview()

    def _on_current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if self._syncing or not current.isValid():
            return
        mods = QGuiApplication.keyboardModifiers()
        if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
            if current.row() in self.ws.selected:
                self.ws.selected_index = current.row()
                self.ws.last_preview_index = current.row()
                self._update_preview()
            return
        sm = self.sender()
        if isinstance(sm, QItemSelectionModel) and len(sm.selectedIndexes()) != 1:
            if current.row() in self.ws.selected:
                self.ws.selected_index = current.row()
                self.ws.last_preview_index = current.row()
                self._update_preview()
            return
        if (
            not self.ws.select_all
            and self.ws.selected_index == current.row()
            and self.ws.selected == {current.row()}
        ):
            return
        self.ws.select_index(current.row())
        self._sync_selection()
        self._update_preview()

    def select_all_pages(self) -> None:
        # 输入框里的 Ctrl+A 由 QLineEdit 自己经 ShortcutOverride 吃掉，不会走到这里；
        # 而工具栏按钮和菜单项不抢焦点，照着焦点判断反而会把「全选」变成选中框里的文字。
        self.ws.select_every_page()
        self._sync_selection()
        self._update_preview()

    def eventFilter(self, obj, event) -> bool:
        left_vp = getattr(self, "left_view", None)
        if left_vp is not None and obj is left_vp.viewport() and event.type() == QEvent.Type.Resize:
            self._thumb_timer.start()
        preview = getattr(self, "preview_view", None)
        if (
            preview is not None
            and obj is preview.viewport()
            and event.type() == QEvent.Type.Resize
            and not getattr(self, '_applying_zoom', False)
        ):
            # 等 Qt 完成本轮滚动条布局再缩放，避免在 Resize 内改变场景后反复触发 Resize。
            # 连续拖动时每帧合并一次，不反复推迟到拖动结束。
            if not self._view_zoom_timer.isActive():
                self._view_zoom_timer.start()
            width = preview.viewport().width()
            if abs(width - self._last_vp_w) >= 40 or self._reading_layout != 'single':
                self._last_vp_w = width
                self._preview_timer.start()
        return super().eventFilter(obj, event)

    def setWindowTitle(self, title: str) -> None:
        super().setWindowTitle(title)
        label = getattr(self, "title_label", None)
        if label is not None:
            if hasattr(label, "setFullText"):
                label.setFullText(title)
            else:
                label.setToolTip(title)
                self._elide_title()

    def _elide_title(self) -> None:
        label = getattr(self, "title_label", None)
        if label is None:
            return
        if hasattr(label, "setFullText"):
            label.setFullText(self.windowTitle())
        else:
            label.setText(
                label.fontMetrics().elidedText(
                    self.windowTitle(),
                    Qt.TextElideMode.ElideMiddle,
                    max(48, label.width()),
                )
            )

    def _toggle_max(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self._sync_chrome()

    def _sync_chrome(self) -> None:
        maximized = self.isMaximized()
        self.setProperty("maximized", maximized)
        self.style().unpolish(self)
        self.style().polish(self)
        # 窗口状态一变，之前为非客户区按钮点亮的悬停底色就失效了。
        self._set_max_hover(False)
        btn = getattr(self, "btn_max", None)
        if btn is not None:
            name = "win-restore" if maximized else "win-max"
            label = "还原" if maximized else "最大化"
            btn.setIcon(toolbar_icon(name, "#134E4A", CAPTION_PX))
            btn.setToolTip(label)
            btn.setStatusTip(label)
            btn.setAccessibleName(label)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._enable_native_frame()
        self._sync_chrome()
        self._elide_title()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide_title()

    def _enable_native_frame(self) -> None:
        if sys.platform != "win32" or QGuiApplication.platformName() != "windows":
            return
        try:
            from ctypes import wintypes

            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
            user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
            user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user32.SetWindowPos.restype = wintypes.BOOL
            style = user32.GetWindowLongPtrW(hwnd, -16)
            # FramelessWindowHint 会去掉 WS_THICKFRAME；只返回边缘编码不足以启动系统拖拽。
            if not style & 0x00040000:
                user32.SetWindowLongPtrW(hwnd, -16, style | 0x00040000)
                user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0037)
            class MARGINS(ctypes.Structure):
                _fields_ = [
                    ("cxLeftWidth", ctypes.c_int),
                    ("cxRightWidth", ctypes.c_int),
                    ("cyTopHeight", ctypes.c_int),
                    ("cyBottomHeight", ctypes.c_int),
                ]

            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(
                int(self.winId()), ctypes.byref(MARGINS(1, 1, 1, 1))
            )
        except Exception:
            pass

    def nativeEvent(self, event_type: bytes, message: int) -> tuple[bool, int]:
        if sys.platform != "win32":
            return super().nativeEvent(event_type, message)
        if event_type == b"windows_generic_MSG":
            try:
                import ctypes.wintypes

                msg = ctypes.wintypes.MSG.from_address(int(message))
                # 最大化按钮交给系统做命中测试后，Qt 收不到这块区域的鼠标移动，
                # 悬停底色得按非客户区消息自己同步，否则三个按钮的反馈会不一致。
                if msg.message in (WM_MOUSEMOVE, WM_NCMOUSEMOVE):
                    self._set_max_hover(msg.message == WM_NCMOUSEMOVE and msg.wParam == HTMAXBUTTON)
                # 点击仍由自己执行：交给 DefWindowProc 会进入标题栏按钮的模态循环，
                # 也就无从确认最大化到底有没有发生。
                if msg.wParam == HTMAXBUTTON:
                    if msg.message in (WM_NCLBUTTONDOWN, WM_NCLBUTTONDBLCLK):
                        QTimer.singleShot(0, self._toggle_max)
                        return True, 0
                    if msg.message == WM_NCLBUTTONUP:
                        return True, 0
                if msg.message == WM_NCHITTEST:
                    if not self.isMaximized() and not self.isFullScreen():
                        # WM_NCHITTEST 使用原生屏幕像素；Qt mapFromGlobal 使用逻辑像素。
                        # 全程在 Win32 客户区物理像素中判断，避免 DPI 缩放把按钮判成边缘。
                        x = ctypes.c_short(msg.lParam & 0xFFFF).value
                        y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                        point = ctypes.wintypes.POINT(x, y)
                        rect = ctypes.wintypes.RECT()
                        user32 = ctypes.windll.user32
                        user32.ScreenToClient.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.POINT)]
                        user32.ScreenToClient.restype = ctypes.wintypes.BOOL
                        user32.GetClientRect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.RECT)]
                        user32.GetClientRect.restype = ctypes.wintypes.BOOL
                        if user32.ScreenToClient(msg.hWnd, ctypes.byref(point)) and user32.GetClientRect(msg.hWnd, ctypes.byref(rect)):
                            hit = resize_hit_test(
                                point.x, point.y, rect.right, rect.bottom,
                                max(1, round(RESIZE_BORDER * self.devicePixelRatioF())),
                            )
                            if hit:
                                return True, hit
                            if self._over_max_button(point.x, point.y):
                                # 命中最大化按钮：系统据此在悬停时弹出 Win11 贴靠布局，
                                # 这块区域的鼠标消息随之变成非客户区，见上方两个分支。
                                return True, HTMAXBUTTON
            except Exception:
                pass
        return False, 0

    def _over_max_button(self, x: int, y: int) -> bool:
        """x/y 为客户区物理像素，与 WM_NCHITTEST 的坐标系一致。"""
        btn = getattr(self, "btn_max", None)
        if btn is None:
            return False
        dpr = self.devicePixelRatioF()
        origin = btn.mapTo(self, QPoint(0, 0))
        left, top = round(origin.x() * dpr), round(origin.y() * dpr)
        right, bottom = left + round(btn.width() * dpr), top + round(btn.height() * dpr)
        return left <= x < right and top <= y < bottom

    def _set_max_hover(self, on: bool) -> None:
        if self._max_hover == on:
            return
        self._max_hover = on
        btn = getattr(self, "btn_max", None)
        if btn is None:
            return
        btn.setProperty("captionHover", on)
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.WindowStateChange:
            self._sync_chrome()
        if event.type() == QEvent.Type.DevicePixelRatioChange and hasattr(self, "arrange_view"):
            self._apply_thumb_size()
            self._preview_timer.start()
        super().changeEvent(event)

    def _fit_thumbs(self) -> None:
        view = getattr(self, "left_view", None)
        if view is None:
            return
        if not view.isVisible():
            # 收起的控件尺寸尚未参与布局，不能用于计算整理视图的缩略图大小。
            self._apply_thumb_size()
            return
        view_w = view.width()
        if view_w < 40:
            return
        # 按控件宽度算，不按 viewport 宽度：滚动条一出现 viewport 就变窄，宽度跟着变会
        # 和「滚动条要不要出现」互相追，侧栏会一直抖。详见 sidebar_thumb_width。
        self._sidebar_w = sidebar_thumb_width(
            view_w, view.verticalScrollBar().sizeHint().width()
        )
        self._apply_thumb_size()

    def _arrange_width(self) -> int:
        return max(THUMB_MIN_W, int(self._sidebar_w * self._arrange_zoom))

    def _active_width(self) -> int:
        """两个视图共用一份缩略图位图，渲染尺寸跟着当前看得见的那个视图走。"""
        return self._arrange_width() if self._in_arrange() else self._sidebar_w

    def _apply_thumb_size(self) -> None:
        self.model.set_device_pixel_ratio(max(self.devicePixelRatioF(), 1.0))
        # 侧栏等宽，整理等高；都使用各页 sizeHint，避免固定网格留出大片空白。
        max_ratio = self.model.max_ratio()
        left_size = QSize(self._sidebar_w, int(round(self._sidebar_w * max_ratio)))
        if self.left_view.iconSize() != left_size:
            self.left_view.setIconSize(left_size)
        width = self._arrange_width()
        arrange_size = QSize(width, int(round(width * THUMB_RATIO_DEFAULT)))
        if self.arrange_view.iconSize() != arrange_size:
            self.arrange_view.setIconSize(arrange_size)
        render_w = self._active_width()
        if abs(render_w - self.model.icon_w) >= 8 or self.model.arrange != self._in_arrange():
            self.model.set_icon_width(render_w, self._in_arrange())
        self._update_render_progress()

    def _in_arrange(self) -> bool:
        return self.stack.currentIndex() == 1

    def zoom_in(self) -> None:
        if self._in_arrange():
            self.arrange_zoom_in()
            return
        self._set_zoom(self._zoom * ZOOM_STEP)

    def zoom_out(self) -> None:
        if self._in_arrange():
            self.arrange_zoom_out()
            return
        self._set_zoom(self._zoom / ZOOM_STEP)

    def zoom_fit(self) -> None:
        if self._in_arrange():
            self.arrange_zoom_fit()
            return
        self._set_zoom_mode('width')

    def _set_zoom_mode(self, mode) -> None:
        self._zoom_mode = mode
        self.reading_zoom_actions[mode].setChecked(True)
        self.reading_orb.sync()
        self._apply_view_zoom()
        self._preview_timer.start()
        index = self.ws.preview_index()
        if index is not None and mode != 'custom':
            self.preview_view.jump_to_page(index)

    def _on_zoom_steps(self, steps: int) -> None:
        self._set_zoom(zoom_by_steps(self._zoom, steps, ZOOM_STEP))

    def _set_zoom(self, value: float) -> None:
        zoom = max(ZOOM_MIN, min(ZOOM_MAX, round(value, 3)))
        self._zoom = zoom
        self._set_zoom_mode('custom')

    def arrange_zoom_in(self) -> None:
        self._set_arrange_zoom(self._arrange_zoom * ARRANGE_ZOOM_STEP)

    def arrange_zoom_out(self) -> None:
        self._set_arrange_zoom(self._arrange_zoom / ARRANGE_ZOOM_STEP)

    def arrange_zoom_fit(self) -> None:
        self._set_arrange_zoom(1.0)

    def _on_arrange_zoom_steps(self, steps: int) -> None:
        self._set_arrange_zoom(zoom_by_steps(self._arrange_zoom, steps, ARRANGE_ZOOM_STEP))

    def _set_arrange_zoom(self, value: float) -> None:
        zoom = max(ARRANGE_ZOOM_MIN, min(ARRANGE_ZOOM_MAX, round(value, 3)))
        if abs(zoom - self._arrange_zoom) < 0.001:
            return
        self._arrange_zoom = zoom
        self.arrange_zoom_label.setText(f"{int(round(zoom * 100))}%")
        self._apply_thumb_size()

    def _preview_viewport_width(self) -> int:
        view = getattr(self, "preview_view", None)
        if view is None:
            return 640
        return max(view.viewport().width() - 24, 200)

    def _preview_render_width(self, index=None) -> int:
        dpr = max(self.devicePixelRatioF(), 1.0)
        if index is None:
            index = self.ws.preview_index()
        page_width = self.store.page_size(self.ws.pages[index])[0] if index is not None else 480
        raw = math.ceil(page_width * self._zoom * PDF_LOGICAL_SCALE * dpr)
        snapped = max(64, ((raw + 63) // 64) * 64)
        limit = PREVIEW_MAX_W
        if index is not None:
            width, height = self.store.page_size(self.ws.pages[index])
            limit = min(limit, int(math.sqrt(8_000_000 * width / max(height, 1))))
        return max(1, min(max(200, snapped), limit))

    def _update_preview(self, *, lightweight=False) -> None:
        updating = self._reader_updating
        self._reader_updating = True
        try:
            self._update_preview_content(lightweight=lightweight)
        finally:
            self._reader_updating = updating

    def _update_preview_content(self, *, lightweight=False) -> None:
        if not lightweight:
            self._sync_controls()
        else:
            self._sync_page_navigation()
            selected = bool(self.ws.target_indices()) and not self._controls_read_only
            self.delete_action.setEnabled(selected)
            for button in self._selection_buttons:
                button.setEnabled(selected)
        self.find_action.setEnabled(bool(self.ws.pages))
        self.search_bar.sync_document()
        self.text_selection.sync()
        self.region_capture.sync()
        if hasattr(self, 'image_tools'):
            self.image_tools.sync()
        if self.ws.pages:
            self._ensure_annotation_tools()
        if hasattr(self, "annotation_tools"):
            self.annotation_tools.sync_page()
        # Workspace 的编辑、撤销、打开和保存均替换页面列表；滚动/缩放不重建布局。
        layout_changed = self._layout_pages is not self.ws.pages
        view = self.preview_view
        threshold = SPREAD_MIN_WIDTH + (0 if view.spread else SPREAD_WIDTH_HYSTERESIS)
        requested_columns = {'single': 1, 'spread': 2, 'four': 4}[self._reading_layout]
        columns = requested_columns if view.viewport().width() >= threshold else 1
        spread = columns > 1
        mode_changed = columns != view.page_columns
        tools = getattr(self, 'annotation_tools', None)
        if mode_changed and (self.reader_interaction.busy_gesture()
                             or (tools and getattr(tools, '_inline_text_box', None))):
            # 拖动、绘制和输入结束后再适配，不能在手势中途移动纸张。
            spread = view.spread
            columns = view.page_columns
            mode_changed = False
            self._preview_timer.start()
        anchor = None
        idx = self.ws.preview_index()
        if mode_changed and idx is not None and idx < len(view.page_rects):
            old_rect = view.page_rects[idx]
            point = view.mapToScene(view.viewport().rect().center()) - old_rect.topLeft()
            anchor = QPointF(max(0, min(old_rect.width(), point.x())),
                             max(0, min(old_rect.height(), point.y())))
        self.reading_fallback_notice.setVisible(bool(self.ws.pages) and requested_columns > 1 and not spread)
        self.reading_orb.sync()
        geometry_changed = False
        if layout_changed:
            self.arrange_preview.hide()
            self._layout_pages = self.ws.pages
            self.rendering.set_pages(self.ws.pages)
        if layout_changed or mode_changed:
            signature = tuple((p.source_id, p.source_page, p.rotation) for p in self.ws.pages)
            geometry_changed = view.set_pages(signature, [self.store.page_size(p) for p in self.ws.pages], columns=columns)
            self._neighbor_pending.clear()
            if geometry_changed:
                self._neighbor_keys.clear()
                self._invalidate_detail_requests()
                self._scroll_y = None
                self.text_selection.sync()
                self.region_capture.sync()
                if tools:
                    tools.hover_bar.hide()
                if hasattr(self, 'image_tools') and self.image_tools.engaged:
                    self.image_tools.update_overlay()
            live = self._live_render_keys()
            for index in list(self.preview_view.neighbors):
                if index >= len(self.ws.pages) or self._neighbor_keys.get(index) != self.ws.pages[index].render_key:
                    self.preview_view._scene.removeItem(self.preview_view.neighbors.pop(index))
                    self._neighbor_keys.pop(index, None)
            for key in list(self._page_images):
                if key not in live:
                    self._page_image_bytes -= self._page_images.pop(key).sizeInBytes()
            self._sync_page_navigation()
        caption = self._selection_caption()
        arrange_sel = getattr(self, "arrange_sel", None)
        if arrange_sel is not None:
            arrange_sel.setText(caption)
        if not self.ws.pages:
            self._zoom_mode = 'page'
            self._preview_busy = False
            self._invalidate_detail_requests()
            self._preview_image = None
            self._shown_page = None
            self._preview_key = None
            self._detail_timer.stop()
            self._reading_settle_timer.stop()
            self._scrolling = self.rendering.scrolling = False
            self._scroll_y = None
            self.preview_view.clear_photo()
            if self._opening_paths:
                name = os.path.basename(self._opening_paths[0])
                self.preview_loading.setText(f"正在打开 PDF…\n{name}")
                self.preview_stack.setCurrentWidget(self.preview_loading)
            else:
                self.preview_stack.setCurrentIndex(0)
            self.details.setText("")
            self.banner.setText("")
            self._update_render_progress()
            self.statusBar().showMessage(self._status_line())
            return
        self.preview_stack.setCurrentIndex(1)
        if caption:
            self.banner.setText(f"{caption}。旋转、删除、抽取作用于已选页面。")
        else:
            self.banner.setText("")
        if not lightweight:
            self.statusBar().showMessage(self._status_line())
        idx = self.ws.preview_index()
        if idx is None:
            return
        page = self.ws.pages[idx]
        changed = geometry_changed or self.preview_view.active_page != idx
        self.preview_view.active_page = idx
        if changed:
            self._invalidate_detail_requests()
            view.clear_detail()
            if view.spread and getattr(view, 'fit_page', False):
                self.text_selection.clear()
        view.update_page_visibility()
        try:
            wpt, hpt = self.store.page_size(page)
            source_rotation = self.store.source_rotation(page)
            note = f" · 源文件已旋转 {source_rotation}°" if source_rotation else ""
            label = self.store.page_label(page)
            if label is not None:
                note += f" · 阅读器标签：{label or '未编号'}"
            self.details.setText(
                f"工作区第 {idx + 1} / {len(self.ws.pages)} 页 · "
                f"来自 {os.path.basename(page.source_path)} 原第 {page.source_page + 1} 页 · "
                f"{wpt:.0f}×{hpt:.0f} pt · 本次旋转 {page.rotation}°{note}"
            )
        except Exception:
            self.details.setText(f"工作区第 {idx + 1} 页")
        self._request_preview(page)
        if (layout_changed or changed) and self._preview_image is not None:
            self._show_preview_image()
        if changed and not self._scroll_navigation:
            self._apply_view_zoom()
            if anchor is not None and self._zoom_mode != 'page':
                rect = view.page_rects[idx]
                x = rect.left() + anchor.x() if self._zoom_mode == 'custom' else view.spread_rect(idx).center().x()
                view.centerOn(x, rect.top() + anchor.y())
            else:
                view.jump_to_page(idx)
        if geometry_changed:
            view.view_changed.emit()
        self._schedule_reading_update()

    def _schedule_reading_update(self) -> None:
        # 节流而非防抖：持续滚动时也更新，不能每次滚轮事件都把执行时间往后推。
        if not self._reading_timer.isActive():
            self._reading_timer.start()

    def _reading_view_changed(self) -> None:
        if not self._reader_updating and self.ws.pages and not self._in_arrange():
            if not self._scrolling:
                self._neighbor_pending.clear()
            self._scrolling = self.rendering.scrolling = True
            self._detail_timer.stop()
            self._invalidate_detail_requests()
            self._reading_settle_timer.start()
        self._schedule_reading_update()

    def _reading_settled(self) -> None:
        self._scrolling = self.rendering.scrolling = False
        if self.ws.pages and not self._in_arrange():
            self._update_preview(lightweight=True)
            self._reading_scrolled()
            self._detail_timer.start()
        self.rendering._dispatch()

    def _thumb_visible(self, key) -> bool:
        view = self.arrange_view if self._in_arrange() else self.left_view
        return view.isVisible() and any(
            view.visualRect(self.model.index(row)).intersects(view.viewport().rect())
            for row in self.model._rows_by_key.get(key, ()))

    def _prune_visible_thumbs(self):
        for key in list(self.model.shown):
            if not self.model.thumb_visible(key):
                self.model._drop_shown(key)
        self.rendering.prune()

    def _reading_scrolled(self) -> None:
        view = self.preview_view
        if not self.ws.pages or not view.isVisible() or self._in_arrange() or not view.page_rects:
            return
        visible = view.mapToScene(view.viewport().rect()).boundingRect()
        if self._scroll_y is not None and abs(visible.top() - self._scroll_y) > .1:
            self._scroll_direction = 1 if visible.top() > self._scroll_y else -1
        self._scroll_y = visible.top()
        # 整页居中时短页上方可能仍露出上一页，必须按中心判断当前页。
        y = (visible.center().y() if self._zoom_mode == 'page'
             else visible.top() + min(visible.height() / 4, 100))
        index = view.page_at_y(y)
        hit = view.page_at_point(QPointF(visible.center().x(), y))
        if hit >= 0 and not view.page_rects[index].intersects(visible):
            index = hit
        if index != self.ws.preview_index() and (self._scrolling or index != view.active_page):
            self._scroll_navigation = True
            try:
                self.ws.select_index(index)
                self._sync_selection(reading_only=True)
                self._update_preview(lightweight=True)
            finally:
                self._scroll_navigation = False
        candidates = list(view.visible_pages(visible))
        # 只保留可见页和相邻页，避免长文档的位图占满内存。
        wanted = set(candidates)
        if not self._scrolling:
            for step in (-1, 1, 2):
                wanted.update(view.pages_in_spread(index + step * self._scroll_direction))
        wanted = {i for i in wanted if 0 <= i < len(self.ws.pages)}
        wanted.discard(index)
        for i in list(view.neighbors):
            if i not in wanted:
                view._scene.removeItem(view.neighbors.pop(i))
                self._neighbor_keys.pop(i, None)
        self._neighbor_pending.intersection_update({key for key in self._neighbor_pending
            if key[0] == view.layout_generation and key[1] in wanted})
        for i in wanted:
            page = self.ws.pages[i]
            w, h = self.store.page_size(page)
            width = max(1, min(1200, max(64, int(w * view.transform().m11() * view.devicePixelRatioF())),
                               int(math.sqrt(1_500_000 * w / max(h, 1)))))
            if view.spread and i in candidates:
                width = self._preview_render_width(i)
            if self._scrolling:
                width = min(width, READING_DRAFT_W)
            key = (view.layout_generation, i, width)
            page_key = page.render_key
            existing = view.neighbors.get(i)
            if existing is not None and existing.pixmap().width() >= width:
                self._neighbor_pending.discard(key)
                continue
            cached = self._page_images.get(page_key)
            if cached is None:
                thumb = self.model.thumbs.get(page_key)
                if thumb is not None:
                    cached = thumb[0].toImage()
            if cached is not None and existing is None:
                view.set_neighbor(i, cached)
                self._neighbor_keys[i] = page_key
            if cached is not None and cached.width() >= width:
                if page_key in self._page_images:
                    self._page_images.move_to_end(page_key)
                view.set_neighbor(i, cached)
                self._neighbor_keys[i] = page_key
                self._neighbor_pending.discard(key)
                continue
            self._neighbor_pending.add(key)
            self.rendering.request(page, width, key, self._neighbor_signals,
                lambda k=key: k not in self._neighbor_pending,
                20 if i in candidates else (-1 if i == index + self._scroll_direction else -2))
        self._prune_visible_thumbs()
        self._update_render_progress()

    def _on_neighbor(self, key, image) -> None:
        if isinstance(image, RenderResult):
            if image.generation != self.rendering.generation:
                return
            if image.cancelled and self.rendering.has_request(self._neighbor_signals, key):
                return
            image = image.value
        if key not in self._neighbor_pending:
            return
        self._neighbor_pending.discard(key)
        if self._layout_pages is not self.ws.pages:
            return
        if image is not None and not image.isNull() and key[0] == self.preview_view.layout_generation:
            page = self.ws.pages[key[1]]
            self._cache_page_image(page.render_key, image)
            self._neighbor_keys[key[1]] = page.render_key
            best = self._page_images.get(page.render_key, image)
            self.preview_view.set_neighbor(key[1], best)
            if not self._scrolling and key[1] in self.preview_view.pages_in_spread(self.ws.preview_index() or 0):
                self._detail_timer.start()
        self._update_render_progress()

    def _cache_page_image(self, key, image) -> None:
        old = self._page_images.get(key)
        if old is not None and old.width() >= image.width():
            self._page_images.move_to_end(key)
            return
        if old is not None:
            self._page_image_bytes -= self._page_images.pop(key).sizeInBytes()
        self._page_images[key] = image
        self._page_image_bytes += image.sizeInBytes()
        while self._page_image_bytes > READING_CACHE_BYTES:
            _, dropped = self._page_images.popitem(last=False)
            self._page_image_bytes -= dropped.sizeInBytes()
        self.rendering.diagnostics.record("memory", reading_cache_bytes=self._page_image_bytes,
            displayed_pages=len(self.preview_view.neighbors) + 1)

    def _request_preview(self, page) -> None:
        if self._in_arrange():
            self._preview_busy = False
            return
        width = self._preview_render_width()
        if self._scrolling:
            width = min(width, READING_DRAFT_W)
        key = (*page.render_key, width)
        cached = self._page_images.get(key[:3])
        if cached is None:
            thumb = self.model.thumbs.get(key[:3])
            if thumb is not None:
                cached = thumb[0].toImage()
        if self._preview_key is None or key[:3] != self._preview_key[:3]:
            self._invalidate_detail_requests()
            self._preview_image = None
            self.preview_view.clear_detail()
            neighbor = self.preview_view.neighbors.get(self.preview_view.active_page)
            if neighbor is not None and not neighbor.pixmap().isNull():
                candidate = neighbor.pixmap().toImage()
                if cached is None or candidate.width() > cached.width():
                    cached = candidate
            if cached is None and self._shown_page != (page.page_id, page.rotation):
                self.preview_view.clear_photo()
        if self._preview_image is None and cached is not None:
            if key[:3] in self._page_images:
                self._page_images.move_to_end(key[:3])
            self._preview_key = (*key[:3], cached.width())
            self._preview_image = cached
            self._preview_busy = False
            self._show_preview_image()
            self.rendering.diagnostics.record("cache", hit=True, draft=self._scrolling)
        if self._preview_image is not None and self._preview_key is not None and key[:3] == self._preview_key[:3]:
            rendered = self._preview_image.width()
            if width > rendered:
                # 放大：只容忍极小的拉伸，否则画面会越来越糊。
                enough = rendered * PREVIEW_UPSCALE_SLACK >= width
            else:
                # 缩小：已有更高分辨率的图，直接缩着看即可——缩放在这一步是完全免费的。
                # 上界是为了不让一张超大图被反复重采样，超过就重渲染一张小图（小图很快）。
                enough = self._scrolling or rendered <= width * PREVIEW_REUSE_RATIO
            if enough:
                self._preview_key = (*key[:3], rendered)
                self._preview_busy = False
                self._apply_view_zoom()
                self._update_render_progress()
                return
        if key == self._preview_key and self._preview_busy:
            return
        self._preview_key = key
        self._preview_busy = True
        self._update_render_progress()
        self.rendering.diagnostics.record("cache", hit=False, draft=self._scrolling)
        self.rendering.request(page, width, key, self.preview_signals,
            lambda k=key: (self._preview_key != k or self._in_arrange()
                          or (self._scrolling and k[3] > READING_DRAFT_W)),
            20 if self._scrolling or self._preview_image is None else 10)

    def _on_preview(self, key: tuple, image: QImage | None) -> None:
        error = ""
        if isinstance(image, RenderResult):
            if image.generation != self.rendering.generation:
                return
            if image.cancelled and self.rendering.has_request(self.preview_signals, key):
                return
            error, image = image.error, image.value
        if self._preview_key != key or self._layout_pages is not self.ws.pages:
            return
        self._preview_busy = False
        self._update_render_progress()
        if image is None or image.isNull():
            if error:
                self.statusBar().showMessage("页面暂时无法加载，请重新选择该页重试。", 5000)
            return
        self._preview_image = image
        self._cache_page_image(key[:3], image)
        self._show_preview_image()

    def _invalidate_detail_requests(self) -> None:
        self._detail_key = None
        self._detail_busy = False
        self._other_detail_keys.clear()
        self._other_detail_pending.clear()

    def _page_detail_key(self, index):
        return self._detail_key if index == self.preview_view.active_page else self._other_detail_keys.get(index)

    def _request_detail(self) -> None:
        index = self.ws.preview_index()
        if index is None or self._preview_image is None or self._preview_busy or self._in_arrange() or self._scrolling:
            return
        if self._preview_key is None or self._preview_key[:3] != self.ws.pages[index].render_key:
            return
        view = self.preview_view
        scale = view.transform().m11() * max(view.devicePixelRatioF(), 1.0)
        visible = view.mapToScene(view.viewport().rect()).boundingRect()
        margin = 24 / max(scale, 0.001)
        visible_pages = set(view.visible_pages(visible))
        for page_index in list(view._other_details):
            if page_index not in visible_pages:
                view.clear_detail(page_index)
                self._other_detail_keys.pop(page_index, None)
                self._other_detail_pending.discard(page_index)
        for page_index in sorted(visible_pages):
            page_rect = view.page_rects[page_index]
            if not page_rect.intersects(visible):
                continue
            page_key = self.ws.pages[page_index].render_key
            primary = page_index == index
            neighbor = view.neighbors.get(page_index)
            width = self._preview_image.width() if primary else (neighbor.pixmap().width() if neighbor else 0)
            if not width:
                continue
            # A larger cached image also needs resampling. Only native-density
            # images can skip the final sharp region after scrolling settles.
            if math.isclose(page_rect.width() * scale, width, rel_tol=.01, abs_tol=1):
                view.clear_detail(page_index)
                if primary:
                    self._detail_key, self._detail_busy = None, False
                else:
                    self._other_detail_keys.pop(page_index, None)
                    self._other_detail_pending.discard(page_index)
                continue
            previous = self._page_detail_key(page_index)
            item = view.detail_item(page_index)
            busy = self._detail_busy if primary else page_index in self._other_detail_pending
            if (previous and not busy and previous[:3] == page_key
                    and math.isclose(previous[-1], scale, rel_tol=.001)
                    and item is not None and not item.pixmap().isNull()
                    and item.sceneBoundingRect().contains(visible.intersected(page_rect))):
                continue
            region = visible.adjusted(-margin, -margin, margin, margin).intersected(page_rect)
            region.translate(-page_rect.topLeft())
            if region.isEmpty():
                continue
            key = (*page_key, (region.left(), region.top(), region.right(), region.bottom()), scale)
            if key == previous:
                continue
            if primary:
                self._detail_key, self._detail_busy = key, True
            else:
                self._other_detail_keys[page_index] = key
                self._other_detail_pending.add(page_index)
            generation = view.layout_generation
            self.rendering.request(self.ws.pages[page_index], None, (generation, page_index, key), self.detail_signals,
                lambda i=page_index, k=key, g=generation: (self._page_detail_key(i) != k
                    or view.layout_generation != g or self._scrolling or self._in_arrange()),
                10, region=(key[3], key[4]))
        self._update_render_progress()

    def _on_detail_preview(self, key: tuple, result) -> None:
        if isinstance(result, RenderResult):
            if result.generation != self.rendering.generation:
                return
            if result.cancelled and self.rendering.has_request(self.detail_signals, key):
                return
            result = result.value
        generation, index, page_key = key
        if (self.preview_view.layout_generation != generation or self._page_detail_key(index) != page_key
                or self._layout_pages is not self.ws.pages):
            return
        primary = index == self.preview_view.active_page
        if primary:
            self._detail_busy = False
        else:
            self._other_detail_pending.discard(index)
        if result is not None:
            image, position, scale = result
            self.preview_view.set_detail(image, position, scale, index)
        else:
            if primary:
                self._detail_key = None
            else:
                self._other_detail_keys.pop(index, None)
        self._update_render_progress()

    def _note_render_busy(self) -> None:
        self._update_render_progress()

    def _render_busy_message(self) -> str:
        if self._opening_paths and not self.ws.pages:
            return "正在打开 PDF"
        if not self.ws.pages:
            return ""
        if self._in_arrange():
            view = self.arrange_view
            if any(view.visualRect(self.model.index(row)).intersects(view.viewport().rect())
                   for key in self.model.inflight for row in self.model._rows_by_key.get(key, ())):
                return "正在生成缩略图"
            return ""
        index = self.ws.preview_index()
        if index is None:
            return ""
        key = self.ws.pages[index].render_key
        if ((self._preview_busy and self._preview_key is not None and self._preview_key[:3] == key)
                or (self._detail_busy and self._detail_key is not None and self._detail_key[:3] == key)):
            return "正在渲染页面"
        if self.preview_view.spread:
            visible = set(self.preview_view.visible_pages(
                self.preview_view.mapToScene(self.preview_view.viewport().rect()).boundingRect()))
            if (self._other_detail_pending & visible or any(k[0] == self.preview_view.layout_generation
                    and k[1] in visible for k in self._neighbor_pending)):
                return "正在渲染页面"
        return ""

    def _update_render_progress(self) -> None:
        message = self._render_busy_message()
        if message:
            self.render_label.setText(message)
            self._render_hide_timer.stop()
            if self.render_label.isHidden() and not self._render_show_timer.isActive():
                self._render_show_timer.start()
            if self._in_arrange() and not self._render_status_timer.isActive():
                self._render_status_timer.start()
        else:
            self._render_show_timer.stop()
            self._render_status_timer.stop()
            if not self.render_label.isHidden() and not self._render_hide_timer.isActive():
                self._render_hide_timer.start()

    def _show_render_progress(self) -> None:
        if self._render_busy_message():
            self.render_label.show()
            self.render_progress.show()
        else:
            self._update_render_progress()

    def _hide_render_progress(self) -> None:
        if self._render_busy_message():
            self._update_render_progress()
        else:
            self.render_label.hide()
            self.render_progress.hide()

    def _show_preview_image(self) -> None:
        if self._preview_image is None or self._preview_image.isNull():
            return
        pix = QPixmap.fromImage(self._preview_image)
        index = self.ws.preview_index()
        if index is None:
            return
        self.preview_view.set_photo(pix, max(self.devicePixelRatioF(), 1.0), self.store.page_size(self.ws.pages[index]))
        page = self.ws.pages[index]
        self._shown_page = (page.page_id, page.rotation)
        mark("first_page_ready")
        self._apply_view_zoom()
        if not self._scrolling:
            self._detail_timer.start()

    def _apply_view_zoom(self) -> None:
        view = self.preview_view
        index = self.ws.preview_index()
        if (getattr(self, '_applying_zoom', False) or index is None
                or not 0 <= index < len(view.page_rects)):
            return
        self._applying_zoom = True
        updating = self._reader_updating
        self._reader_updating = True
        try:
            mode = self._zoom_mode
            one_page = len(view.page_rects) == 1
            one_page_fit = mode == 'page' and one_page
            one_page_width = mode == 'width' and one_page
            # 连续双页按整份布局适宽，跨组刷新预览时不改变缩放与横向范围。
            rect = view._scene.sceneRect() if view.spread and mode == 'width' else view.spread_rect(index)
            view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff
                if one_page_fit or one_page_width or (view.spread and mode in ('page', 'width'))
                else Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff
                if one_page_fit else Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            factor = self._zoom * PDF_LOGICAL_SCALE
            if mode in ('width', 'page'):
                available_width = view.viewport().width() - 24
                if one_page_width and not view.verticalScrollBar().isVisible():
                    # 先预留可能出现的竖条宽度，避免适宽后竖条显隐反过来改变缩放。
                    available_width -= view.verticalScrollBar().sizeHint().width()
                factor = max(1, available_width) / max(1, rect.width())
                if mode == 'page':
                    factor = min(factor, max(1, view.viewport().height() - 24) / max(1, rect.height()))
                self._zoom = factor / PDF_LOGICAL_SCALE
            view.fit_page = mode == 'page'
            view.update_page_visibility()
            self.zoom_label.setText(f"{self._zoom * 100:.0f}%")
            self.reading_zoom_actions[mode].setChecked(True)
            self.reading_orb.sync()
            center = view.mapToScene(view.viewport().rect().center())
            bounds = view._scene.sceneRect()
            if view.spread and mode in ('page', 'width'):
                bounds = QRectF(rect.left(), bounds.top(), rect.width(), bounds.height())
            # 留出取整余量，避免“刚好适宽”也出现横向滚动条。
            horizontal_padding = 10 / max(factor, .001)
            # 单页完整显示由视图居中；按视口高度反算留白会让场景始终比视口高 4 像素。
            vertical_padding = 10 if one_page_fit else 12
            if mode == 'page' and not one_page_fit:
                vertical_padding = max(vertical_padding, (view.viewport().height() - rect.height() * factor) / 2 + 2)
            vertical_padding /= max(factor, .001)
            bounds = bounds.adjusted(-horizontal_padding, -vertical_padding,
                                     horizontal_padding, vertical_padding)
            bounds_changed = view.sceneRect() != bounds
            if bounds_changed:
                view.setSceneRect(bounds)
            if abs(view.transform().m11() - factor) <= 0.00001:
                if bounds_changed:
                    view.centerOn(rect.center() if mode == 'page' else center)
                return
            view.clear_detail()
            self._invalidate_detail_requests()
            view.setTransform(QTransform.fromScale(factor, factor))
            view.centerOn(rect.center() if mode == 'page' else center)
            view.view_changed.emit()
        finally:
            self._reader_updating = updating
            self._applying_zoom = False
        if not self._scrolling:
            self._detail_timer.start()

    def rotate(self, delta: int) -> None:
        if not self._allow_operation("modify"):
            return
        if not self.ws.target_indices():
            self.statusBar().showMessage("请先选择页面")
            return
        self.ws.rotate(delta)

    def delete_pages(self) -> None:
        if not self._allow_operation("modify"):
            return
        if not self.ws.target_indices():
            self.statusBar().showMessage("请先选择页面")
            return
        self.ws.delete_selected()

    def confirm_leave(self) -> bool:
        tools = getattr(self, "annotation_tools", None)
        if tools is not None and not tools.finish_text_box():
            return False
        if not self.ws.dirty:
            return True
        box = QMessageBox(self)
        box.setWindowTitle("未保存的更改")
        box.setText("当前工作区有未保存的更改。")
        box.setInformativeText("关闭或打开其他文件前，选择是否写入当前文档。")
        save_btn = box.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("不保存", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save_btn:
            return self.save()
        if clicked is cancel:
            return False
        return True

    def _register_workspace(self, ws: Workspace) -> None:
        self.undo_group.addStack(ws.undo_stack)
        ws.undo_stack.indexChanged.connect(lambda *_: self._undo_refresh_timer.start())

    def _live_render_keys(self) -> set[tuple]:
        keys: set[tuple] = set()
        for tab in getattr(self, "_tabs", ()):
            for page in tab.ws.pages:
                keys.add(page.render_key)
        for page in self.ws.pages:
            keys.add(page.render_key)
        return keys

    def _get_tab(self, tab_id: int) -> _DocumentTab | None:
        for tab in self._tabs:
            if tab.tab_id == tab_id:
                return tab
        return None

    def _active_tab(self) -> _DocumentTab | None:
        tab = self._get_tab(self._active_tab_id)
        if tab is not None and tab.ws is not self.ws:
            # 某些单测直接替换 window.ws 时同步回当前标签页引用
            tab.ws = self.ws
            self.model.ws = self.ws
        return tab

    def _sync_tab_bar(self) -> None:
        tab = self._active_tab()
        if tab is not None and hasattr(self, "tab_bar"):
            self.tab_bar.sync_tabs(self._tabs, self._active_tab_id)

    def _save_current_tab_state(self) -> None:
        cur = self._active_tab()
        if cur is None:
            return
        cur.zoom = self._zoom
        cur.zoom_mode = self._zoom_mode
        cur.reading_layout = self._reading_layout
        cur.arrange_zoom = self._arrange_zoom
        cur.mode_index = self.stack.currentIndex()
        if hasattr(self, "preview_view"):
            cur.scroll_y = self.preview_view.verticalScrollBar().value()

    def activate_tab(self, tab_id: int) -> bool:
        target = self._get_tab(tab_id)
        if target is None:
            return False
        if tab_id == self._active_tab_id and self.ws is target.ws:
            self._sync_tab_bar()
            return True
        tools = getattr(self, "annotation_tools", None)
        if tools is not None and not tools.finish_text_box():
            return False
        self._save_current_tab_state()
        self.search_bar.cancel()
        self._reading_timer.stop()
        self._reading_settle_timer.stop()
        self._preview_timer.stop()
        self._view_zoom_timer.stop()
        self._detail_timer.stop()
        self.arrange_view._drop_motion.stop()
        self.arrange_view._drop_fade.stop()
        self._neighbor_pending.clear()
        self._invalidate_detail_requests()
        self._preview_key = None
        self._preview_image = None
        self._shown_page = None
        self._active_tab_id = target.tab_id
        self.ws = target.ws
        self.model.ws = target.ws
        self.undo_group.setActiveStack(target.ws.undo_stack)
        self._zoom = target.zoom
        self._zoom_mode = target.zoom_mode
        if target.zoom_mode in self.reading_zoom_actions:
            self.reading_zoom_actions[target.zoom_mode].setChecked(True)
        self._reading_layout = target.reading_layout
        if target.reading_layout in self.reading_layout_actions:
            self.reading_layout_actions[target.reading_layout].setChecked(True)
        self._arrange_zoom = target.arrange_zoom
        self.arrange_zoom_label.setText(f"{int(round(self._arrange_zoom * 100))}%")
        target_mode = target.mode_index if target.ws.pages else 0
        if self.stack.currentIndex() != target_mode:
            self.stack.setCurrentIndex(target_mode)
        self.refresh()
        if target.scroll_y is not None and target.ws.pages:
            self.preview_view.verticalScrollBar().setValue(target.scroll_y)
        elif target.ws.preview_index() is not None:
            self.preview_view.jump_to_page(target.ws.preview_index())
        return True

    def step_tab(self, delta: int) -> None:
        if len(self._tabs) <= 1:
            return
        ids = [tab.tab_id for tab in self._tabs]
        try:
            cur_idx = ids.index(self._active_tab_id)
        except ValueError:
            cur_idx = 0
        next_idx = (cur_idx + delta) % len(ids)
        self.activate_tab(ids[next_idx])

    def _confirm_leave_all_tabs(self) -> bool:
        if not self.confirm_leave():
            return False
        for tab in list(self._tabs):
            if tab.tab_id != self._active_tab_id and tab.ws.dirty:
                if not self.activate_tab(tab.tab_id):
                    return False
                if not self.confirm_leave():
                    return False
        return True

    def close_tab(self, tab_id: int) -> bool:
        if getattr(self, "_task_dialog", None) is not None:
            return False
        if getattr(self, "_conv_thread", None) is not None:
            self.statusBar().showMessage("请结束或取消转换后再关闭当前文件", 5000)
            return False
        tab = self._get_tab(tab_id)
        if tab is None:
            return False
        if tab.ws.dirty:
            if self._active_tab_id != tab_id and not self.activate_tab(tab_id):
                return False
            if not self.confirm_leave():
                return False
        if len(self._tabs) <= 1:
            self.search_bar.cancel()
            self._reading_timer.stop()
            self._reading_settle_timer.stop()
            self._preview_timer.stop()
            self._view_zoom_timer.stop()
            self._detail_timer.stop()
            self.arrange_view._drop_motion.stop()
            self.arrange_view._drop_fade.stop()
            if hasattr(self, "annotation_tools"):
                self.annotation_tools.deactivate()
            self.ws.reset()
            tab.zoom = 1.0
            tab.zoom_mode = "page"
            tab.reading_layout = "single"
            tab.arrange_zoom = 1.0
            tab.mode_index = 0
            tab.scroll_y = None
            self.refresh()
            self.stack.setCurrentIndex(0)
            run_operation(self, "关闭文档", lambda progress: self.store.close_all())
            self.welcome_open.setFocus()
            self.statusBar().showMessage("已关闭当前文件，返回首页", 5000)
            return True

        idx = self._tabs.index(tab)
        neighbor = self._tabs[idx - 1] if idx > 0 else self._tabs[1]
        self.undo_group.removeStack(tab.ws.undo_stack)
        self._tabs.remove(tab)
        if tab_id == self._active_tab_id:
            self.activate_tab(neighbor.tab_id)
        else:
            self._sync_tab_bar()
        self._release_unused_docs()
        self.statusBar().showMessage("已关闭标签页", 4000)
        return True

    def close_current_file(self) -> None:
        self.close_tab(self._active_tab_id)

    def open_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "打开 PDF / OFD", "", "PDF / OFD (*.pdf *.ofd)")
        if paths:
            self.open_files_in_tabs(paths)

    def open_files_in_tabs(self, paths: list[str]) -> bool:
        valid_paths = [os.path.abspath(p) for p in paths if p and p.lower().endswith((".pdf", ".ofd"))]
        if not valid_paths:
            return False
        opened_any = False
        for index, path in enumerate(valid_paths, start=1):
            norm = os.path.normcase(path)
            existing = next(
                (
                    t
                    for t in self._tabs
                    if t.ws.path and os.path.normcase(os.path.abspath(t.ws.path)) == norm
                ),
                None,
            )
            if existing is not None:
                self.activate_tab(existing.tab_id)
                opened_any = True
                continue
            pages = self._try_load(path, index, len(valid_paths))
            if pages is None:
                self._release_unused_docs()
                break
            cur_tab = self._active_tab()
            if cur_tab is not None and not self.ws.pages and not self.ws.dirty and not self.ws.path:
                self.ws.reset()
                self._zoom_mode = "page"
                self.ws.open_replace(pages, path)
                self.refresh()
                opened_any = True
            else:
                ws = Workspace()
                self._register_workspace(ws)
                ws.open_replace(pages, path)
                new_tab = _DocumentTab(ws=ws)
                self._tabs.append(new_tab)
                self.activate_tab(new_tab.tab_id)
                opened_any = True
        self._release_unused_docs()
        self._sync_tab_bar()
        return opened_any

    def insert_dropped_files(
        self,
        paths: list[str],
        *,
        at_index: int | None = None,
        target_view: PageListView | None = None,
    ) -> bool:
        valid_paths = [os.path.abspath(p) for p in paths if p and p.lower().endswith((".pdf", ".ofd"))]
        if not valid_paths:
            return False
        if not self.ws.pages:
            return self.open_files_in_tabs(valid_paths)
        if not self._allow_operation("modify"):
            return False
        base_pos = len(self.ws.pages) if at_index is None else max(0, min(at_index, len(self.ws.pages)))
        inserted_count = 0
        cur_pos = base_pos
        for index, path in enumerate(valid_paths, start=1):
            pages = self._load_one(path, index, len(valid_paths), insert_at=cur_pos)
            if not pages:
                break
            cur_pos += len(pages)
            inserted_count += len(pages)
        if inserted_count > 0:
            inserted_range = list(range(base_pos, base_pos + inserted_count))
            self.ws.set_selection(inserted_range, current=base_pos)
            self.refresh()
            self._sync_selection()
            view = target_view or (self.arrange_view if self._in_arrange() else self.left_view)
            view.scrollTo(self.model.index(base_pos, 0))
            view._show_landing(base_pos)
            self.statusBar().showMessage(f"已插入 {inserted_count} 页 · 可拖动调整顺序或 Ctrl+Z 撤销", 4000)
            return True
        return False

    def insert_pages_from_workspace(
        self,
        source_ws: Workspace,
        sources: list[int],
        *,
        at_index: int | None = None,
        target_view: PageListView | None = None,
    ) -> bool:
        if source_ws is self.ws:
            return False
        if self.ws.pages and not self._allow_operation("modify"):
            return False
        rows = sorted({row for row in sources if 0 <= row < len(source_ws.pages)})
        if not rows:
            return False
        source_pages = [source_ws.pages[row] for row in rows]
        reason = self.store.operation_reason(source_pages, "modify")
        if reason:
            self._warn_merge_readonly(source_ws.path or "来源文档", source_pages, reason)
            return False
        incoming = Workspace._unique_pages(source_pages, source_pages)
        incoming_outline = (
            self.store.outline_entries(incoming, group_sources=True) if self.ws.outline is not None else None
        )
        base_pos = len(self.ws.pages) if at_index is None else max(0, min(at_index, len(self.ws.pages)))
        self.ws.insert_pages(incoming, at_index=base_pos, outline=incoming_outline)
        inserted_range = list(range(base_pos, base_pos + len(incoming)))
        self.ws.set_selection(inserted_range, current=base_pos)
        self.refresh()
        self._sync_selection()
        view = target_view or (self.arrange_view if self._in_arrange() else self.left_view)
        view.scrollTo(self.model.index(base_pos, 0))
        view._show_landing(base_pos)
        self.statusBar().showMessage(f"已跨文档并入 {len(incoming)} 页 · 原文档保留不变", 4000)
        return True

    def drop_pages_onto_tab(self, target_tab_id: int, source_ws: Workspace, sources: list[int]) -> bool:
        target_tab = self._get_tab(target_tab_id)
        if target_tab is None or target_tab.ws is source_ws:
            return False
        if not self.activate_tab(target_tab_id):
            return False
        return self.insert_pages_from_workspace(source_ws, sources, at_index=len(self.ws.pages))

    def append_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "追加 PDF", "", "PDF (*.pdf)")
        if paths:
            self._ingest(paths, replace=False)

    def open_startup_files(self, paths: list[str], mode: str = "") -> None:
        try:
            if mode:
                self.open_shell_files(paths, mode)
            else:
                self._ingest(paths, replace=True)
        finally:
            self._opening_paths = ()
            if not self.ws.pages:
                self.refresh()
            else:
                self._update_render_progress()

    def open_shell_files(self, paths: list[str], mode: str) -> bool:
        if mode not in ("split", "merge"):
            QMessageBox.warning(self, "无法打开右键入口", "无法识别此操作，请重新选择拆分或合并。")
            return False
        paths = [os.path.abspath(path) for path in paths]
        invalid = [path for path in paths if not path.lower().endswith(".pdf") or not os.path.isfile(path)]
        if not paths or invalid or (mode == "split" and len(paths) != 1):
            message = "请选择一个 PDF 进行拆分。" if mode == "split" else "请选择有效的 PDF 文件进行合并。"
            if invalid:
                message += "\n无法打开：" + "、".join(os.path.basename(path) for path in invalid[:4])
            QMessageBox.warning(self, "无法打开右键入口", message)
            return False
        if not self._ingest(paths, replace=True):
            return False
        if mode == "split":
            self.split_each.setChecked(True)
            self.open_export_tool(1)
        else:
            self.stack.setCurrentIndex(1)
            self.statusBar().showMessage(f"已载入 {len(paths)} 个 PDF，共 {len(self.ws.pages)} 页 · 检查顺序后保存", 8000)
        return True

    def insert_files(self) -> None:
        if self.ws.pages and not self._allow_operation("modify"):
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "插入 PDF", "", "PDF (*.pdf)")
        if not paths:
            return
        selected = self.ws.target_indices()
        has_selection = bool(selected)
        base_pos = (max(selected) + 1) if has_selection else len(self.ws.pages)

        inserted_count = 0
        cur_pos = base_pos
        for index, path in enumerate(paths, start=1):
            pages = self._load_one(path, index, len(paths), insert_at=cur_pos)
            if not pages:
                break
            cur_pos += len(pages)
            inserted_count += len(pages)

        if inserted_count > 0:
            inserted_range = list(range(base_pos, base_pos + inserted_count))
            self.ws.set_selection(inserted_range, current=base_pos)
            self.refresh()
            self._sync_selection()
            self.arrange_view.scrollTo(self.model.index(base_pos, 0))
            self.arrange_view._show_landing(base_pos)
            if has_selection:
                self.statusBar().showMessage(f"已在第 {max(selected) + 1} 页后插入 {inserted_count} 页 · 可拖动调整顺序", 4000)
            else:
                self.statusBar().showMessage(f"已在末尾插入 {inserted_count} 页 · 可拖动调整顺序", 4000)

    def _ingest(self, paths: list[str], replace: bool) -> bool:
        if not paths:
            return False
        if not replace and self.ws.pages and not self._allow_operation("modify"):
            return False
        if replace:
            if not self.confirm_leave():
                return False
            batches: list[tuple[str, list]] = []
            for index, path in enumerate(paths, start=1):
                pages = self._try_load(path, index, len(paths))
                if pages is None:
                    self._release_unused_docs()
                    return False
                batches.append((os.path.abspath(path), pages))
            if len(batches) > 1:
                for path, pages in batches:
                    reason = self.store.operation_reason(pages, "modify")
                    if reason:
                        self._warn_merge_readonly(path, pages, reason)
                        self._release_unused_docs()
                        return False
            self.ws.reset()
            self._zoom_mode = 'page'
            for path, pages in batches:
                self.ws.load_first_or_merge(pages, path)
            self.refresh()
            # 关闭旧文档时按文档加锁，最多等这一次渲染跑完，而不是整个线程池排空。
            self._release_unused_docs()
            return True
        loaded = True
        for index, path in enumerate(paths, start=1):
            if not self._load_one(path, index, len(paths)):
                loaded = False
                break
        self.refresh()
        return loaded

    def _try_load(self, path: str, index: int = 1, total: int = 1):
        password = None
        is_ofd = path.lower().endswith('.ofd')
        # 多选打开时把批次信息放进标题，否则用户不知道进度条管的是第几个文件。
        title = "打开 OFD" if is_ofd else "打开 PDF"
        if total > 1:
            title += f" · 文件 {index} / {total}"
        while True:
            try:
                if is_ofd:
                    pages = run_operation(self, title, lambda progress, cancel:
                        self.store.load(path, progress_callback=progress, cancel=cancel), cancellable=True)
                    warnings = self.store._source(pages[0].source_id).import_warnings
                    if warnings:
                        QMessageBox.information(self, 'OFD 转换提示', '\n'.join(warnings))
                    return pages
                return run_operation(self, title, lambda progress:
                    self.store.load(path, password, progress_callback=progress))
            except OperationCancelled:
                self.statusBar().showMessage('已取消打开，原文件未修改', 5000)
                return None
            except NeedPassword:
                text, ok = QInputDialog.getText(
                    self,
                    "加密 PDF",
                    f"{os.path.basename(path)} 需要密码",
                    QLineEdit.EchoMode.Password,
                )
                if not ok:
                    return None
                password = text
            except PdfError as exc:
                if "密码不正确" in str(exc):
                    QMessageBox.warning(self, "无法打开", str(exc))
                    password = None
                    continue
                QMessageBox.warning(self, "无法打开", str(exc))
                return None

    def _load_one(self, path: str, index: int = 1, total: int = 1, insert_at: int | None = None) -> list | None:
        if self.ws.pages and not self._allow_operation("modify"):
            return None
        pages = self._try_load(path, index, total)
        if pages is None:
            return None
        reason = self.store.operation_reason(pages, "modify") if self.ws.pages else ""
        if reason:
            self._warn_merge_readonly(path, pages, reason)
            self._release_unused_docs()
            return None
        incoming_outline = self.store.outline_entries(pages, group_sources=True) if self.ws.outline is not None else None
        if not self.ws.pages:
            self._zoom_mode = 'page'
            self.ws.open_replace(pages, os.path.abspath(path))
        elif insert_at is not None:
            self.ws.insert_pages(pages, at_index=insert_at, outline=incoming_outline)
        else:
            self.ws.load_first_or_merge(pages, os.path.abspath(path), outline=incoming_outline)
        return pages

    def _warn_merge_readonly(self, path, pages, reason) -> None:
        message = f"{os.path.basename(path)}\n{reason}\n\n请单独打开阅读。"
        if not self.store.operation_reason(pages, "editable_copy"):
            message += "符合条件时，可在单独打开后制作可编辑副本，再合并副本。"
        QMessageBox.warning(self, "不能合并只读文档", message)

    def _release_unused_docs(self) -> None:
        keep = set(self.ws.live_sources())
        for tab in getattr(self, "_tabs", ()):
            keep |= tab.ws.live_sources()
        run_operation(self, "整理工作区", lambda progress: self.store.close_except(keep))

    def _allow_operation(self, operation: str) -> bool:
        reason = self.store.operation_reason(self.ws.pages, operation)
        if reason:
            QMessageBox.information(self, "文件保护", reason)
            return False
        return True

    def _write_pdf(self, pages, path, title, *, register_result=False, groups=None, dests=None,
                   options=None, cancellable=True):
        options = options or WriteOptions()
        if options.outline is None and self.ws.outline is not None:
            options = replace(options, outline=self.ws.outline)
        while True:
            try:
                def work(progress, cancel=None):
                    from app.pdfworker import write_pdf, write_pdf_groups
                    if groups is not None:
                        return write_pdf_groups(self.store, pages, groups, dests,
                            progress_callback=progress, options=options, cancel=cancel)
                    return write_pdf(self.store, pages, path, progress_callback=progress, options=options,
                                     register_result=register_result, cancel=cancel)
                return run_operation(self, title, work, cancellable=cancellable)
            except OperationCancelled as exc:
                self.statusBar().showMessage(str(exc), 8000)
                return None
            except WriteNeedsConfirmation as exc:
                if exc.kind == "encryption":
                    from app.exportdialogs import EncryptionDialog
                    dialog = EncryptionDialog(self, allow_plain=True)
                    dialog.setWindowTitle("原加密不能沿用 · 请选择输出保护")
                    if dialog.exec() != QDialog.DialogCode.Accepted:
                        return None
                    setting = dialog.setting
                    dialog.setting = None
                    dialog.deleteLater()
                    options = replace(options, preserve_encryption=False, encryption=setting)
                else:
                    box = QMessageBox(QMessageBox.Icon.Warning, "另存副本前确认", str(exc), parent=self)
                    proceed = box.addButton("确认并另存副本", QMessageBox.ButtonRole.AcceptRole)
                    cancel_button = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
                    box.setDefaultButton(cancel_button)
                    box.exec()
                    if box.clickedButton() is not proceed:
                        return None
                    options = replace(options, allow_structure_loss=True)
                if groups is None and self.store.is_source_destination(path):
                    from app.exportdialogs import new_pdf_path
                    path = new_pdf_path(self, self.store, "另存副本", os.path.splitext(path)[0] + "_副本.pdf")
                    if not path:
                        return None

    def compress_pdf(self):
        if not self._allow_operation("modify") or not self._allow_operation("copy") or not self.ws.pages:
            return
        from app.compression import CompressionDialog
        try:
            dialog = CompressionDialog(self)
        except OSError as exc:
            QMessageBox.warning(self, '无法创建压缩缓存', str(exc))
            return
        dialog.exec()
        dialog.deleteLater()

    def open_watermarks(self):
        if not self.ws.pages or not self._allow_operation("annotate"):
            return
        from app.watermarkui import WatermarkDialog
        dialog = WatermarkDialog(self)
        dialog.exec()
        dialog.deleteLater()

    def open_ocr(self):
        from app.ocrui import open_ocr
        open_ocr(self)

    def export_text(self):
        if not self.ws.pages or not self._allow_operation('copy'):
            return
        if self.ws.path and self.ws.path.lower().endswith('.ofd') and not self.save(True):
            return
        if self.ws.dirty or not self.ws.path:
            QMessageBox.information(self, '先保存当前文档', '请先保存或另存当前 PDF，再导出文字。')
            return
        from app.documentlayout import pdf_to_text
        path, _ = QFileDialog.getSaveFileName(self, '导出 TXT', export_path(os.path.splitext(self.ws.path)[0]+'.txt'),
                                             '文本文件 (*.txt)', options=QFileDialog.Option.DontConfirmOverwrite)
        if not path:
            return
        if not path.lower().endswith('.txt'):
            path += '.txt'
        try:
            result = run_operation(self, '导出文字', lambda progress,cancel:
                pdf_to_text(self.ws.path,path,progress,cancel), cancellable=True)
            self.statusBar().showMessage(f'文字已导出：{result}', 6000)
        except OperationCancelled:
            self.statusBar().showMessage('文字导出已取消。', 4000)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, '文字未导出', str(exc))

    def images_to_pdf(self):
        from app.exportdialogs import ImageImportDialog, new_pdf_path
        from app.imaging import images_to_pdf
        dialog = ImageImportDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            dialog.deleteLater()
            return
        paths = dialog.paths()
        dialog.deleteLater()
        path = new_pdf_path(self, self.store, "图片另存为 PDF", "图片合并.pdf")
        if not path:
            return
        try:
            result = run_operation(self, "图片转 PDF", lambda progress, cancel:
                images_to_pdf(paths, path, self.store, progress, cancel), cancellable=True)
            QMessageBox.information(self, "已完成", f"已完整导出 1 个 PDF：\n{result.path}")
        except OperationCancelled:
            QMessageBox.information(self, "已取消", "已取消，完成 0 个导出文件，原件保留。")
        except (PdfError, OSError) as exc:
            QMessageBox.warning(self, "图片转换未完成", str(exc))

    def pdf_to_images(self):
        if not self._allow_operation("copy") or not self.ws.pages:
            return
        from app.exportdialogs import ImageExportDialog
        from app.imaging import pdf_to_images
        dialog = ImageExportDialog(len(self.ws.pages), self.ws.preview_index() or 0,
                                   sorted(self.ws.selected), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            dialog.deleteLater()
            return
        indices = dialog.indices()
        fmt = "png" if dialog.format.currentIndex() == 0 else "jpeg"
        dpi = 150 if dialog.dpi.currentIndex() == 0 else 300
        overwrite = dialog.overwrite.isChecked()
        dialog.deleteLater()
        folder = QFileDialog.getExistingDirectory(self, "选择图片输出文件夹", str(output_directory() or ''))
        if not folder:
            return
        prefix = Path(self.ws.path or "文档.pdf").stem
        try:
            done, skipped, failed, cancelled = run_operation(self, "PDF 转图片", lambda progress, cancel:
                pdf_to_images(self.store, [self.ws.pages[i] for i in indices], indices, folder, prefix,
                              fmt, dpi, overwrite, progress, cancel), cancellable=True)
            box = QMessageBox(QMessageBox.Icon.Information, "已取消" if cancelled else "导出结束",
                f"已完整输出 {len(done)} 张图片，跳过 {len(skipped)} 张，失败 {len(failed)} 张。\n{folder}", parent=self)
            box.setDetailedText("\n".join([*("成功：" + p for p in done), *("跳过：" + p for p in skipped), *failed]))
            box.exec()
        except PdfError as exc:
            QMessageBox.warning(self, "导出未完成", str(exc))

    def encrypt_copy(self):
        if not self._allow_operation("modify") or not self.ws.pages:
            return
        from app.exportdialogs import EncryptionDialog, new_pdf_path
        dialog = EncryptionDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            dialog.deleteLater()
            return
        setting = dialog.setting
        dialog.setting = None
        dialog.deleteLater()
        path = new_pdf_path(self, self.store, "加密另存", Path(self.ws.path or "文档.pdf").stem + "_加密.pdf")
        if not path:
            return
        try:
            result = self._write_pdf(list(self.ws.pages), path, "加密另存", cancellable=True,
                                     options=WriteOptions(preserve_encryption=False, encryption=setting))
            if result is not None:
                QMessageBox.information(self, "加密另存完成", f"已使用 {setting.label} 加密，原件保留：\n{result.path}")
                self._show_write_notes((result,))
        except OperationCancelled:
            QMessageBox.information(self, "已取消", "已取消，完成 0 个输出文件。")
        except PdfError as exc:
            QMessageBox.warning(self, "加密未完成", str(exc))

    def decrypt_copy(self):
        if not self.ws.pages:
            return
        sources = [self.store._source(sid) for sid in dict.fromkeys(ref.source_id for ref in self.ws.pages)]
        if not any(source.safety.encrypted for source in sources):
            QMessageBox.information(self, "无需解密", "当前文档未加密。")
            return
        for source in sources:
            if source.safety.signature != "none":
                QMessageBox.information(self, "文件保护", source.safety.read_only_reason)
                return
        for sid in dict.fromkeys(ref.source_id for ref in self.ws.pages):
            source = self.store._source(sid)
            while source.safety.needs_owner_for_decryption:
                password, ok = QInputDialog.getText(self, "解密需要管理密码",
                    f"{Path(source.path).name} 限制了文档权限，请输入管理密码：", QLineEdit.EchoMode.Password)
                if not ok:
                    return
                try:
                    run_operation(self, "验证管理密码", lambda progress:
                                  self.store.authenticate_owner(sid, password))
                except PdfError as exc:
                    QMessageBox.warning(self, "密码验证未通过", str(exc))
                finally:
                    password = None
                self.refresh()
        if not self._allow_operation("decrypt"):
            return
        from app.exportdialogs import new_pdf_path
        path = new_pdf_path(self, self.store, "解密另存", Path(self.ws.path or "文档.pdf").stem + "_解密.pdf")
        if not path:
            return
        try:
            result = self._write_pdf(list(self.ws.pages), path, "解密另存", cancellable=True,
                                     options=WriteOptions(preserve_encryption=False, operation="decrypt"))
            if result is not None:
                QMessageBox.information(self, "解密另存完成", f"已保存无需密码的 PDF 副本，原加密文件保留：\n{result.path}")
                self._show_write_notes((result,))
        except OperationCancelled:
            QMessageBox.information(self, "已取消", "已取消解密，原加密文件保留。")
        except PdfError as exc:
            QMessageBox.warning(self, "解密未完成", str(exc))

    def editable_copy(self):
        if any(self.store._source(ref.source_id).safety.signature == 'unknown' for ref in self.ws.pages):
            self.rasterize_document(recovery=True)
            return
        if not self._allow_operation("editable_copy") or not self.ws.pages:
            return
        if not self.confirm_leave():
            return
        if QMessageBox.warning(self, "制作可编辑副本", "副本将冻结签名外观并移除签名验证结构。\n"
                "原数字签名效力不会保留；其他表单仍可填写。原件不会覆盖。",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Ok:
            return
        from app.exportdialogs import new_pdf_path
        path = new_pdf_path(self, self.store, "另存可编辑副本", Path(self.ws.path or "文档.pdf").stem + "_可编辑副本.pdf")
        if not path:
            return
        try:
            result = self._write_pdf(list(self.ws.pages), path, "制作可编辑副本", register_result=True,
                options=WriteOptions(operation="editable_copy", editable_copy=True), cancellable=True)
            if result is None:
                return
            try:
                self.ws.reset()
                self.ws.load_first_or_merge(list(result.pages), result.path)
                self.refresh()
                self._release_unused_docs()
                self._show_write_notes((result,))
            except Exception:
                QMessageBox.warning(self, "副本已保存", f"副本已保存到：\n{result.path}\n界面更新未完成，请重新打开。")
        except OperationCancelled:
            QMessageBox.information(self, "已取消", "已取消，完成 0 个输出文件。")
        except PdfError as exc:
            QMessageBox.warning(self, "副本未完成", str(exc))

    def open_word_converter(self, *, direction="word_to_pdf"):
        from app.wordui import WordConverterWindow
        if direction == 'pdf_to_word' and self.ws.path and self.ws.path.lower().endswith('.ofd'):
            if not self.save(True):
                return
        # 方向以用户点选的菜单为准，不用当前打开的文件类型改写。
        if direction == "pdf_to_word" and self.ws.path and self.ws.dirty and not self.confirm_leave():
            return
        paths = [self.ws.path] if direction == "pdf_to_word" and self.ws.path else []
        window = WordConverterWindow(paths, direction=direction)
        if not hasattr(self, "_word_windows"):
            self._word_windows = []
        self._word_windows.append(window)
        window.destroyed.connect(lambda: self._word_windows.remove(window) if window in self._word_windows else None)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        window.show()

    def convert_markdown(self):
        from app.outputui import MarkdownDialog
        dialog = MarkdownDialog(self)
        dialog.exec()
        dialog.deleteLater()

    def open_page_numbers(self):
        from app.outputui import PageNumbersDialog
        dialog = PageNumbersDialog(self)
        dialog.exec()
        dialog.deleteLater()

    def rasterize_document(self, checked=False, *, recovery=False):
        from app.rasterui import open_raster
        open_raster(self, recovery=recovery)

    def show_outline(self, index):
        if index == 1:
            if not hasattr(self, "outline_panel"):
                from app.editingui import OutlinePanel
                self.outline_panel = OutlinePanel(self)
                self.sidebar_stack.addWidget(self.outline_panel)
            self.outline_panel.refresh()
        self.sidebar_stack.setCurrentIndex(index)

    def open_outline(self):
        self.stack.setCurrentIndex(0)
        self.act_pages.setChecked(True)
        self.sidebar_mode.setCurrentIndex(1)
        self.show_outline(1)

    def _ensure_annotation_tools(self):
        if not hasattr(self, "annotation_tools"):
            from app.editingui import AnnotationTools
            self.annotation_tools = AnnotationTools(self)
            from app.imageui import ImageTools
            self.image_tools = ImageTools(self)
            self._reader_layout.insertWidget(0, self.annotation_tools)
            self.annotation_tools.hide()
        return self.annotation_tools

    def toggle_annotation_tools(self):
        if hasattr(self, "annotation_tools") and self.annotation_tools.isVisible():
            self.annotation_tools.deactivate()
        else:
            self.open_annotation_tools()

    def open_annotation_tools(self):
        if not self.ws.pages:
            QMessageBox.information(self, "请先打开 PDF", "打开 PDF 后即可高亮、备注、绘图或修正文字。")
            return
        self.stack.setCurrentIndex(0)
        tools = self._ensure_annotation_tools()
        tools._ensure_filter()
        tools.show()
        self.review_button.setChecked(True)

    def _show_write_notes(self, results):
        warnings = list(dict.fromkeys(note for result in results for note in result.warnings))
        if warnings:
            box = QMessageBox(QMessageBox.Icon.Information, "文件已写出", "保存完成，请留意以下信息：\n" +
                              "\n".join(warnings[:5]), parent=self)
            if len(warnings) > 5:
                box.setDetailedText("\n".join(warnings))
            box.exec()

    def _open_output_folder(self, folder: str) -> None:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        if not QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(folder))):
            saved = self.statusBar().currentMessage()
            self.statusBar().showMessage(f"{saved}\n无法自动打开文件夹：{folder}", 10000)

    def save(self, save_as: bool = False) -> bool:
        tools = getattr(self, "annotation_tools", None)
        if tools is not None and not tools.finish_text_box():
            return False
        operation = self.store.write_operation(self.ws.pages, WriteOptions(outline=self.ws.outline))
        if not self._allow_operation(operation):
            return False
        if not self.ws.can_save():
            QMessageBox.information(self, "无法保存", "至少保留 1 页才能保存。")
            return False
        path = self.ws.path
        is_ofd = bool(path and path.lower().endswith('.ofd'))
        merged_output = path is None and bool(self.ws.protected_paths)
        if save_as or not path or is_ofd:
            suggested = os.path.splitext(path)[0] + '.pdf' if is_ofd else path or "未命名.pdf"
            path, _ = QFileDialog.getSaveFileName(self, "另存为 PDF" if is_ofd else "保存 PDF", export_path(suggested), "PDF (*.pdf)")
            if not path:
                return False
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            if self.ws.blocks_save_to(path):
                QMessageBox.warning(
                    self,
                    "不能覆盖原件",
                    "合并后不能保存到本次用过的源文件上，请另选一个新文件。",
                )
                return False
        try:
            before = list(self.ws.pages)
            result = self._write_pdf(before, path, "保存 PDF", register_result=True)
        except PdfError as exc:
            self.ws.last_error = str(exc)
            QMessageBox.warning(self, "保存失败", str(exc))
            self.refresh()
            return False
        if result is None:
            return False
        # 文件已经提交成功。界面或缓存更新出错时，不能再把本次写入报告为失败。
        try:
            after = list(result.pages)
            self.ws.rebase_to_saved_file(result.path, after)
            mapping = self.model.remap(before, after)
            self.store.remap_annotations(mapping)
            if self._preview_key is not None and self._preview_key[:3] in mapping:
                self._preview_key = (*mapping[self._preview_key[:3]], self._preview_key[3])
            self.refresh()
            self._release_unused_docs()
            self.statusBar().showMessage(f"已保存到 {result.path}", 8000)
            self._show_write_notes((result,))
        except Exception:
            QMessageBox.warning(self, "文件已保存", f"文件已成功保存到：\n{result.path}\n界面刷新未完成，请重新打开该文件查看。")
        if merged_output:
            self._open_output_folder(os.path.dirname(result.path))
        return True

    def print_document(self) -> None:
        if not self._allow_operation("print"):
            return
        if not self.ws.pages:
            self.statusBar().showMessage("工作区没有页面可供打印")
            return
        # 打印模块包含系统打印支持，按需加载可明显缩短日常双击打开 PDF 的启动时间。
        from app.printing import PrintDialog

        dlg = PrintDialog(self, self.ws, self.store)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.statusBar().showMessage("已发送打印任务")

    def choose_default_application(self) -> None:
        if not getattr(sys, "frozen", False):
            QMessageBox.information(self, "默认 PDF 应用", f"请运行打包后的 {APP_EXE_NAME}，再从此菜单设置默认应用。")
            return
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        from app.windows_integration import DEFAULT_APPS_URL, register_pdf_application

        try:
            register_pdf_application(sys.executable)
            if not QDesktopServices.openUrl(QUrl(DEFAULT_APPS_URL)):
                raise OSError(f"无法打开 Windows 设置，请在系统设置的「默认应用」中选择{APP_NAME}。")
            self.statusBar().showMessage(f"请在 Windows 设置中将 .pdf 的默认应用选择为「{APP_NAME}」", 12000)
        except OSError as exc:
            QMessageBox.warning(self, "默认应用设置", str(exc))

    def extract(self) -> None:
        if not self._allow_operation("modify") or not self._allow_operation("copy"):
            return
        try:
            indices = self.ws.extract_indices(self.extract_range.text())
        except ValueError as exc:
            QMessageBox.information(self, "抽取", str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(self, "抽取为", export_path("抽取.pdf"), "PDF (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        if self.ws.blocks_export_to(path):
            QMessageBox.warning(
                self,
                "不能覆盖来源",
                "抽取写出的是另一个文件，不能覆盖当前文档或本次用过的源文件。请另选位置。",
            )
            return
        try:
            pages = [self.ws.pages[i] for i in indices]
            result = self._write_pdf(pages, path, "抽取页面")
            if result is not None:
                self.statusBar().showMessage(f"已抽取到 {os.path.basename(result.path)}")
                self._show_write_notes((result,))
        except PdfError as exc:
            QMessageBox.warning(self, "抽取失败", str(exc))

    def _split_groups(self) -> list[list[int]]:
        count = len(self.ws.pages)
        if self.split_range_mode.isChecked():
            return parse_range_segments(self.split_range.text(), count)
        step = self.split_count.value() if self.split_every.isChecked() else 1
        return [list(range(start, min(start + step, count))) for start in range(0, count, step)]

    def split(self) -> None:
        if not self._allow_operation("modify") or not self._allow_operation("copy"):
            return
        if not self.ws.pages:
            QMessageBox.information(self, "拆分", "工作区没有页面")
            return
        try:
            groups = self._split_groups()
        except ValueError as exc:
            QMessageBox.information(self, "拆分", str(exc))
            return
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录", str(output_directory() or ''))
        if not folder:
            return
        stem = os.path.splitext(os.path.basename(self.ws.path or "拆分"))[0]
        dests = [os.path.join(folder, f"{stem}_{i}.pdf") for i in range(1, len(groups) + 1)]
        blocked = [d for d in dests if self.ws.blocks_export_to(d)]
        if blocked:
            names = "、".join(os.path.basename(p) for p in blocked[:4])
            QMessageBox.warning(
                self,
                "不能覆盖来源",
                f"拆分写出的是新文件，不能覆盖当前文档或本次用过的源文件。\n受阻：{names}",
            )
            return
        existing = [d for d in dests if os.path.exists(d)]
        if existing:
            mode = self._ask_split_conflicts(existing)
            planned = apply_output_conflicts(groups, dests, mode)
            if planned is None:
                return
            groups, dests = planned
        try:
            pages = list(self.ws.pages)
            results = self._write_pdf(pages, None, "拆分 PDF", groups=groups, dests=dests)
            if results is not None:
                self.statusBar().showMessage(f"已写出 {len(results)} 个文件，工作区未改动")
                self._show_write_notes(results)
                if results:
                    self._open_output_folder(os.path.dirname(results[0].path))
        except PdfError as exc:
            QMessageBox.warning(self, "拆分失败", str(exc) + "\n已完整写出的文件会保留。")

    def _ask_split_conflicts(self, existing: list[str]) -> str:
        box = QMessageBox(self)
        box.setWindowTitle("文件已存在")
        names = "、".join(os.path.basename(p) for p in existing[:6])
        extra = "…" if len(existing) > 6 else ""
        box.setText(f"目标位置已有 {len(existing)} 个同名文件。")
        box.setInformativeText(f"{names}{extra}\n覆盖会替换这些文件；签名与权限检查不通过的目标不会覆盖。")
        overwrite = box.addButton("覆盖", QMessageBox.ButtonRole.AcceptRole)
        skip = box.addButton("跳过", QMessageBox.ButtonRole.ActionRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(skip)
        box.exec()
        clicked = box.clickedButton()
        if clicked is overwrite:
            return "overwrite"
        if clicked is skip:
            return "skip"
        return "cancel"

    def convert_to_excel(self) -> None:
        if not self._allow_operation("copy"):
            return
        if getattr(self, "_conv_thread", None) is not None:
            return
        if not self.ws.pages:
            QMessageBox.information(self, "转换为 Excel", "工作区没有页面，请先打开或拖入 PDF。")
            return

        # 确定要转换的页码索引 (0-based)
        if self.convert_selected.isChecked():
            indices = self.ws.target_indices()
            if not indices:
                QMessageBox.information(self, "转换为 Excel", "请先在页面列表中选择需要转换的页面。")
                return
        elif self.convert_range_mode.isChecked():
            text = self.convert_range.text().strip()
            if not text:
                QMessageBox.information(self, "转换为 Excel", "请输入要转换的页码范围。")
                return
            try:
                indices = parse_page_range(text, len(self.ws.pages))
            except ValueError as exc:
                QMessageBox.warning(self, "页码错误", str(exc))
                return
        else:
            indices = list(range(len(self.ws.pages)))

        # 建议的输出文件名
        include_all_content = bool(self.convert_content.currentData())
        default_name = "内容导出.xlsx" if include_all_content else "表格导出.xlsx"
        if self.ws.path:
            base = os.path.splitext(os.path.basename(self.ws.path))[0]
            default_name = f"{base}.xlsx"

        out_path, _ = QFileDialog.getSaveFileName(
            self, "导出 Excel", export_path(default_name), "Excel 工作簿 (*.xlsx)",
        )
        if not out_path:
            return
        if not out_path.lower().endswith(".xlsx"):
            out_path += ".xlsx"
            if os.path.exists(out_path) and QMessageBox.question(
                    self, "覆盖现有 Excel？", "补全扩展名后的 Excel 文件已存在，是否覆盖？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return

        # 如果工作区未改动、且非加密文件、且是单文档全选，直接使用源文件；
        # 否则（有旋转/删页/重排/合并/原文件加密），先将当前所见页面写入临时解密 PDF 进行分析
        source_pdf = None

        can_use_source = (
            not self.ws.dirty
            and self.ws.path
            and not self.ws.path.lower().endswith('.ofd')
            and len(self.ws.pages) == len(indices)
            and indices == list(range(len(self.ws.pages)))
        )

        if can_use_source:
            source_pdf = self.ws.path
        target_pages = [self.ws.pages[i] for i in indices]

        prog = TaskProgressDialog("PDF 转 Excel", self, cancellable=True)
        prog.show()

        thread = ConversionThread(source_pdf, out_path, None, self,
                                  store=self.store, pages=target_pages, include_all_content=include_all_content)
        self._conv_thread = thread
        self._conv_prog = prog

        def on_progress(cur: int, tot: int, msg: str):
            if hasattr(self, "_conv_prog") and self._conv_prog is not None:
                self._conv_prog.update_progress(cur, tot, msg)

        def cleanup():
            if hasattr(self, "_conv_prog") and self._conv_prog is not None:
                self._conv_prog.complete()
                self._conv_prog.deleteLater()
                self._conv_prog = None
            held = getattr(self, "_conv_thread", None)
            if held is not None:
                held.deleteLater()
                self._conv_thread = None

        def on_finished():
            result, report = thread.result, thread.report
            error_message = thread.error_message
            cleanup()
            if error_message:
                QMessageBox.warning(self, "转换提示", error_message)
                return
            if not result.tables and not result.pages_exported:
                QMessageBox.warning(
                    self,
                    "未检测到表格",
                    f"所选页面中未检测到可提取的表格，未生成或覆盖 Excel 文件。\n"
                    f"跳过页面：{result.pages_skipped}。\n"
                    f"可选择“全部内容”保留正文与图片；扫描文字需先完成 OCR。",
                )
                return

            page_summary = f"共导出页面：{result.pages_exported} 页\n" if result.include_all_content else ""
            msg = (
                f"转换完成！\n"
                f"已生成：{os.path.basename(out_path)}\n"
                f"{page_summary}"
                f"共提取表格：{len(result.tables)} 个\n"
                f"转换摘要：{report.summary_text}"
            )
            if report.warning_count > 0:
                msg += f"\n提示：有 {report.warning_count} 条提示，可展开详细信息查看。"
            box = QMessageBox(QMessageBox.Icon.Information, "转换成功", msg, parent=self)
            details = list(result.warnings)
            for table_report in report.tables:
                details.extend(
                    f"第 {table_report.page_no} 页 / 表格 {table_report.table_index + 1}：{warning}"
                    for warning in table_report.warnings
                )
            if details:
                box.setDetailedText("\n".join(details))
            box.exec()
            self.statusBar().showMessage(f"已导出 Excel: {os.path.basename(out_path)}")

        def on_cancel():
            thread.cancel()

        thread.progress.connect(on_progress, Qt.ConnectionType.QueuedConnection)
        thread.finished.connect(on_finished, Qt.ConnectionType.QueuedConnection)
        prog.canceled.connect(on_cancel)

        thread.start()

    def dragEnterEvent(self, event) -> None:
        if _pdf_paths_from_mime(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = _pdf_paths_from_mime(event.mimeData())
        if paths:
            pos = event.position().toPoint()
            if self._in_arrange() and self.ws.pages and self.stack.geometry().contains(pos):
                self.insert_dropped_files(paths, at_index=len(self.ws.pages), target_view=self.arrange_view)
            else:
                self.open_files_in_tabs(paths)
        event.acceptProposedAction()

    def closeEvent(self, event) -> None:
        if getattr(self, "_task_dialog", None) is not None:
            event.ignore()
            return
        if getattr(self, "_conv_thread", None) is not None:
            self._conv_prog.request_cancel()
            self.statusBar().showMessage("正在取消转换，结束后可关闭窗口")
            event.ignore()
            return
        if self._confirm_leave_all_tabs():
            self.updates.stop()
            self.reading_orb.shutdown()
            self._save_ui_preferences()
            self.search_bar.cancel()
            self._undo_refresh_timer.stop()
            self._reading_timer.stop()
            self._reading_settle_timer.stop()
            self._preview_timer.stop()
            self._view_zoom_timer.stop()
            self._detail_timer.stop()
            self._render_status_timer.stop()
            self._render_show_timer.stop()
            self._render_hide_timer.stop()
            if hasattr(self, "arrange_view"):
                self.arrange_view._drop_motion.stop()
                self.arrange_view._drop_fade.stop()
            self._neighbor_pending.clear()
            self._preview_key = None
            self._invalidate_detail_requests()
            self.rendering.close()
            self.region_capture.cancel()
            self.text_selection.clear()
            for handler in (self.text_selection, self.search_bar):
                self.preview_view.removeEventFilter(handler)
                self.preview_view.viewport().removeEventFilter(handler)
            for handler in (self.reader_interaction, getattr(self, 'image_tools', None)):
                if handler is not None:
                    QGuiApplication.instance().removeEventFilter(handler)
            if hasattr(self, "annotation_tools"):
                self.annotation_tools.deactivate()
                self.annotation_tools.remove_view_filters()
            QThreadPool.globalInstance().waitForDone(1000)
            run_operation(self, "关闭文档", lambda progress: self.store.close_all())
            self._window_settings.setValue("mainWindow/geometry", self.saveGeometry())
            self._window_settings.sync()
            event.accept()
        else:
            event.ignore()
