"""Standalone Word batch window, separate from the PDF reading workspace."""
from __future__ import annotations
from app.themes import set_theme_style, ui_color

import os
from collections import Counter
from pathlib import Path

from PyQt6.QtCore import QEvent, QUrl, Qt, QTimer
from PyQt6.QtGui import QColor, QDesktopServices, QFontMetrics
from PyQt6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QHeaderView,
    QHBoxLayout, QLabel, QMenu, QMessageBox, QPushButton, QSizePolicy,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app import APP_NAME
from app.exportdialogs import hint
from app.icons import brand_icon
from app.output import OperationCancelled, check_cancel
from app.progress import run_operation
from app.ui import STYLESHEET
from app.wordconvert import DIRECTIONS, collect_files, convert_batch, word_available, pdf_import_needs_confirmation, allow_pdf_import
from app.ocr.export_input import needs_text_export

# 样式扩展：分段开关、徽标与紧凑路径框
MODE_QSS = """
QFrame#segmentedControl {
    background: #E8F3F1;
    border: 1px solid #D7E8E4;
    border-radius: 9px;
    padding: 2px;
}
QPushButton[mode="true"] {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 5px 14px;
    color: #4D6B67;
    font-size: 13px;
    font-weight: 500;
}
QPushButton[mode="true"]:hover {
    color: #134E4A;
    background: #F0FDFA;
}
QPushButton[mode="true"]:checked {
    background: #FFFFFF;
    color: #0F766E;
    font-weight: 600;
    border: 1px solid #C5D5D2;
}
QPushButton[mode="true"]:checked:hover {
    background: #FFFFFF;
}
QLabel#badge {
    background: #F0FDFA;
    color: #0F766E;
    border: 1px solid #CCFBF1;
    border-radius: 9px;
    padding: 1px 7px;
    font-size: 12px;
    font-weight: 600;
}
QLabel#pathField {
    background: #FFFFFF;
    border: 1px solid #D7E8E4;
    border-radius: 8px;
    padding: 4px 10px;
    color: #134E4A;
    font-size: 13px;
}
"""


class PathField(QLabel):
    """只读路径框：过长时中间省略，悬停显示完整路径，不撑破布局。"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setObjectName("pathField")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(30)
        self.setMinimumWidth(240)
        self.setMaximumWidth(460)
        self.setFullText(text)

    def setFullText(self, text: str):
        self._full = text
        self.setToolTip(text)
        self._apply()

    def _apply(self):
        width = max(self.width() - 8, 60)
        self.setText(QFontMetrics(self.font()).elidedText(self._full, Qt.TextElideMode.ElideMiddle, width))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply()


class WordConverterWindow(QWidget):
    def __init__(self, paths=(), *, direction="word_to_pdf"):
        super().__init__()
        self.setAcceptDrops(True)
        self.direction = direction
        self.source_kind = "PDF" if direction == "pdf_to_word" else "Word"
        self.target_kind = "Word" if direction == "pdf_to_word" else "PDF"
        self.output_suffix = ".docx" if direction == "pdf_to_word" else ".pdf"
        from app.storage import output_directory
        self.custom_output_dir: Path | None = output_directory()
        # 逐份手动指定的输出位置，切换统一目录时保留，不会被覆盖。
        self._manual_targets: dict[str, str] = {}
        self._message = ""
        self.setWindowTitle(f"批量 {self.source_kind} 转 {self.target_kind} · {APP_NAME}")
        self.setWindowIcon(brand_icon())
        set_theme_style(self, STYLESHEET + MODE_QSS)
        self.resize(1030, 693)
        self.setMinimumSize(720, 460)
        self.inputs = []
        self._task_dialog = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        self._build_header(layout)
        self._build_workspace_card(layout)
        self._build_footer(layout)

        self._update_direction_labels()
        if paths:
            self.inputs = list(paths)
            QTimer.singleShot(0, self.rescan)

    # ------------------------------------------------------------------ 布局
    def _build_header(self, layout):
        header_card = QFrame()
        header_card.setObjectName("card")
        header_layout = QVBoxLayout(header_card)
        header_layout.setContentsMargins(18, 14, 18, 14)
        header_layout.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        self.title = QLabel(f"批量 {self.source_kind} 转 {self.target_kind}")
        set_theme_style(self.title, "font-size: 18px; font-weight: 700; color: #134E4A;")
        top_row.addWidget(self.title)
        top_row.addStretch()

        self.mode_group = QButtonGroup(self)
        self.btn_word_to_pdf = QPushButton("Word 转 PDF")
        self.btn_pdf_to_word = QPushButton("PDF 转 Word")
        for button in (self.btn_word_to_pdf, self.btn_pdf_to_word):
            button.setProperty("mode", True)
            button.setCheckable(True)
            button.setFixedHeight(28)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.mode_group.addButton(button)
        self.btn_pdf_to_word.setChecked(self.direction == "pdf_to_word")
        self.btn_word_to_pdf.setChecked(not self.btn_pdf_to_word.isChecked())
        self.btn_word_to_pdf.clicked.connect(lambda: self.switch_direction("word_to_pdf"))
        self.btn_pdf_to_word.clicked.connect(lambda: self.switch_direction("pdf_to_word"))

        seg_frame = QFrame()
        seg_frame.setObjectName("segmentedControl")
        seg_layout = QHBoxLayout(seg_frame)
        seg_layout.setContentsMargins(2, 2, 2, 2)
        seg_layout.setSpacing(2)
        seg_layout.addWidget(self.btn_word_to_pdf)
        seg_layout.addWidget(self.btn_pdf_to_word)
        top_row.addWidget(seg_frame)
        self.ocr_install = QPushButton("安装识别组件…")
        self.ocr_install.setFixedHeight(28)
        self.ocr_install.clicked.connect(self.install_ocr)
        top_row.addWidget(self.ocr_install)

        header_layout.addLayout(top_row)
        self.intro = hint(header_layout, "")
        layout.addWidget(header_card)

    def _build_workspace_card(self, layout):
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(10)

        # 1. 标题行：标题 + 紧跟徽标在左侧，添加按钮在右侧
        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        caption = QLabel("待转换文件")
        set_theme_style(caption, "font-size: 15px; font-weight: 600; color: #134E4A;")
        caption.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        header_row.addWidget(caption)

        self.count_label = QLabel("0 份")
        self.count_label.setObjectName("badge")
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        header_row.addWidget(self.count_label)
        header_row.addStretch()

        self.add_button = QPushButton(f"添加 {self.source_kind} 文件…")
        self.add_button.setFixedHeight(30)
        self.add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_button.clicked.connect(self.add_files)
        header_row.addWidget(self.add_button)

        self.add_folder_button = QPushButton("添加文件夹…")
        self.add_folder_button.setFixedHeight(30)
        self.add_folder_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_folder_button.clicked.connect(self.add_folder)
        header_row.addWidget(self.add_folder_button)
        card_layout.addLayout(header_row)

        # 2. 清单表格
        self.table = QTableWidget(0, 3)
        self.table.setAcceptDrops(True)
        self.table.viewport().setAcceptDrops(True)
        self.table.installEventFilter(self)
        self.table.viewport().installEventFilter(self)
        self.table.setHorizontalHeaderLabels([f"源 {self.source_kind} 文件", f"输出 {self.target_kind} 文件", "状态"])
        self.table.setAccessibleName(f"{self.source_kind} 转换清单，双击第二列可选择输出位置")
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setDragEnabled(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(2, 170)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        card_layout.addWidget(self.table, 1)

        # 3. 表格下方操作与提示
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.remove_button = QPushButton("移除所选")
        self.remove_button.setFixedHeight(28)
        self.remove_button.setEnabled(False)
        self.remove_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button = QPushButton("清空")
        self.clear_button.setFixedHeight(28)
        self.clear_button.setEnabled(False)
        self.clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_button.clicked.connect(self.clear)
        action_row.addWidget(self.remove_button)
        action_row.addWidget(self.clear_button)
        action_row.addStretch()

        tip = QLabel("双击第二列可单独指定保存位置 · 选中按 Delete 移除")
        tip.setObjectName("hint")
        set_theme_style(tip, "font-size: 12px; color: #64748B;")
        tip.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        action_row.addWidget(tip)
        card_layout.addLayout(action_row)

        # 4. 微浅分割线
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        set_theme_style(divider, "background: #E2EEEC; min-height: 1px; max-height: 1px; border: none;")
        card_layout.addWidget(divider)

        # 5. 输出位置控制行（融入卡片内，防止无节制横向拉伸）
        output_row = QHBoxLayout()
        output_row.setSpacing(10)
        out_caption = QLabel("输出位置")
        set_theme_style(out_caption, "font-weight: 600; color: #134E4A; font-size: 13px;")
        out_caption.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        output_row.addWidget(out_caption)

        self.output_path = PathField("与源文件相同文件夹（默认）")
        output_row.addWidget(self.output_path)

        self.btn_choose_dir = QPushButton("选择目录…")
        self.btn_choose_dir.setFixedHeight(30)
        self.btn_choose_dir.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_choose_dir.setToolTip("统一选择所有输出文件的保存目录")
        self.btn_choose_dir.clicked.connect(self.choose_output_dir)
        output_row.addWidget(self.btn_choose_dir)

        self.btn_reset_dir = QPushButton("使用源目录")
        self.btn_reset_dir.setFixedHeight(30)
        self.btn_reset_dir.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset_dir.setToolTip("取消统一目录，并把单独指定过的位置也恢复成与源文件相同文件夹")
        self.btn_reset_dir.clicked.connect(self.reset_output_dir)
        self.btn_reset_dir.setVisible(False)
        output_row.addWidget(self.btn_reset_dir)

        output_row.addStretch()
        card_layout.addLayout(output_row)

        self.output_hint = hint(card_layout, "")
        set_theme_style(self.output_hint, "font-size: 12px; color: #64748B;")

        # 6. 高级选项（收纳入卡片底部，消灭孤立控件）
        self.advanced = QCheckBox("高级选项")
        set_theme_style(self.advanced, "font-size: 13px; color: #4D6B67;")
        card_layout.addWidget(self.advanced)

        self.options = QWidget()
        options_layout = QVBoxLayout(self.options)
        options_layout.setContentsMargins(18, 2, 0, 4)
        options_layout.setSpacing(6)
        self.recursive = QCheckBox("文件夹包含子文件夹（默认仅当前层）")
        self.overwrite = QCheckBox(f"明确覆盖已有 {self.target_kind}（源文件保留）")
        options_layout.addWidget(self.recursive)
        options_layout.addWidget(self.overwrite)
        self.pdf_mode_row=QWidget()
        mode_layout=QHBoxLayout(self.pdf_mode_row)
        mode_layout.setContentsMargins(0,0,0,0)
        mode_label=QLabel('Word 排版方式')
        self.pdf_mode=QComboBox()
        self.pdf_mode.addItem('保留原版式（推荐）','layout')
        self.pdf_mode.addItem('Word 重排（适合大幅修改正文）','word')
        self.pdf_mode.setAccessibleName('Word 排版方式')
        mode_label.setBuddy(self.pdf_mode)
        mode_layout.addWidget(mode_label)
        mode_layout.addWidget(self.pdf_mode)
        mode_layout.addStretch()
        options_layout.addWidget(self.pdf_mode_row)
        self.pdf_mode.currentIndexChanged.connect(self._update_direction_labels)
        self.engine_hint = hint(options_layout, "")
        set_theme_style(self.engine_hint, "font-size: 12px; color: #64748B;")
        self.options.hide()
        self.advanced.toggled.connect(self.options.setVisible)
        self.recursive.toggled.connect(self.rescan)
        card_layout.addWidget(self.options)

        layout.addWidget(card, 1)

    def _build_footer(self, layout):
        footer = QHBoxLayout()
        footer.setSpacing(16)
        footer.setContentsMargins(2, 2, 2, 0)

        info_box = QVBoxLayout()
        info_box.setSpacing(2)
        self.summary = QLabel()
        self.summary.setObjectName("hint")
        self.summary.setWordWrap(True)
        set_theme_style(self.summary, "color: #134E4A; font-size: 13px; font-weight: 600;")
        self.summary.setMinimumWidth(360)
        self.notice = QLabel("复杂排版和缺失字体请核对转换结果。已有同名输出默认跳过。")
        self.notice.setObjectName("hint")
        self.notice.setWordWrap(True)
        set_theme_style(self.notice, "color: #64748B; font-size: 12px;")
        self.notice.setMinimumWidth(360)
        info_box.addWidget(self.summary)
        info_box.addWidget(self.notice)
        footer.addLayout(info_box, 1)

        self.start = QPushButton("开始转换")
        self.start.setProperty("primary", True)
        self.start.setEnabled(False)
        self.start.setFixedHeight(36)
        self.start.setFixedWidth(120)
        self.start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start.clicked.connect(self.convert)
        footer.addWidget(self.start)
        layout.addLayout(footer)

    # ------------------------------------------------------------ 方向与文案
    def _update_direction_labels(self):
        self.setWindowTitle(f"{self.source_kind} 转 {self.target_kind} · {APP_NAME}")
        self.title.setText(f"{self.source_kind} 转 {self.target_kind}")
        self.add_button.setText(f"添加 {self.source_kind} 文件…")
        self.table.setHorizontalHeaderLabels([f"源 {self.source_kind} 文件", f"输出 {self.target_kind} 文件", "状态"])
        self.table.setAccessibleName(f"{self.source_kind} 转换清单，双击第二列可选择输出位置")
        self.overwrite.setText(f"明确覆盖已有 {self.target_kind}（源文件保留）")
        self.ocr_install.setVisible(self.direction == "pdf_to_word")
        self.pdf_mode_row.setVisible(self.direction == "pdf_to_word")
        preserve_layout=self.pdf_mode.currentData()=='layout'
        pdf_hint=('按原位置保留文字、标题和表格，色块与插图保留为图像，无需安装 Word。可在更多选项切换为 Word 重排。'
                  if preserve_layout else '普通 PDF 使用本机 Microsoft Word 重排，适合连续修改正文；已有 OCR 文字直接导出可编辑内容。')
        primary_hint = (pdf_hint
                        if self.direction == "pdf_to_word" else "使用本机 Microsoft Word 离线转换 · 已有同名输出默认跳过")
        self.engine_hint.setText("扫描页需安装离线识别组件；缺失字体、竖排文字和复杂图文请核对。保留原版式按位置编辑，Word 重排需本机 Word，单份最多等待 180 秒。"
                                 if self.direction == "pdf_to_word" else "单份超时 180 秒；失败继续下一份。源文件只读打开，禁用宏和外链自动更新。")
        sub_hint = f"可直接拖入 {self.source_kind} 文件或文件夹，重复文件自动忽略。"
        self.intro.setText(f"{primary_hint}\n{sub_hint}")
        self._update_output_field()
        self._update_summary()

    def install_ocr(self):
        if self._task_dialog is not None:
            return
        from app.ocrui import install_local_runtime
        if install_local_runtime(self):
            self._set_message("识别组件已安装，可以开始转换扫描 PDF。")

    def switch_direction(self, direction: str):
        if self._task_dialog is not None or self.direction == direction:
            return
        self.direction = direction
        self.source_kind = "PDF" if direction == "pdf_to_word" else "Word"
        self.target_kind = "Word" if direction == "pdf_to_word" else "PDF"
        self.output_suffix = ".docx" if direction == "pdf_to_word" else ".pdf"
        self.btn_word_to_pdf.setChecked(direction == "word_to_pdf")
        self.btn_pdf_to_word.setChecked(direction == "pdf_to_word")
        # 换方向等于换一批输出，清空清单并回到默认输出位置。
        from app.storage import output_directory
        self.custom_output_dir = output_directory()
        self.clear()
        self._update_direction_labels()

    # ---------------------------------------------------------------- 输出位置
    def _target_for(self, source: str) -> str:
        manual = self._manual_targets.get(source)
        if manual:
            return manual
        if self.custom_output_dir is not None:
            return str(self.custom_output_dir / (Path(source).stem + self.output_suffix))
        return str(Path(source).with_suffix(self.output_suffix))

    def _status_for(self, target: str) -> str:
        return f"已有 {self.target_kind} · 默认跳过" if os.path.exists(target) else "待转换"

    def _update_output_field(self):
        if self.custom_output_dir is not None:
            self.output_path.setFullText(f"统一输出到：{self.custom_output_dir}")
            self.btn_reset_dir.setVisible(True)
            manual_count = len(self._manual_targets)
            if manual_count:
                self.output_hint.setText(
                    f"未单独指定的文件统一保存在此文件夹（已单独指定 {manual_count} 份），已有同名文件默认跳过。"
                )
            else:
                self.output_hint.setText("所有输出文件统一保存在这个文件夹，已有同名文件默认跳过。")
        else:
            self.output_path.setFullText("与源文件相同文件夹")
            self.btn_reset_dir.setVisible(bool(self._manual_targets))
            self.output_hint.setText(
                f"每份 {self.source_kind} 与生成的 {self.target_kind} 放在一起，文件名保持一致。"
            )

    def _refresh_targets(self):
        for row in range(self.table.rowCount()):
            source = self.table.item(row, 0).text()
            target = self._target_for(source)
            self._set_item(row, 1, target)
            self._set_item(row, 2, self._status_for(target))
        self._update_output_field()
        self._update_summary()

    def choose_output_dir(self):
        if self._task_dialog is not None:
            return
        path = QFileDialog.getExistingDirectory(self, f"选择 {self.target_kind} 输出目录")
        if not path:
            return
        self.custom_output_dir = Path(path)
        self._message = ""
        self._refresh_targets()

    def reset_output_dir(self):
        if self._task_dialog is not None:
            return
        self.custom_output_dir = None
        self._manual_targets.clear()
        self._refresh_targets()

    def browse_row_target(self, row: int):
        if self._task_dialog is not None:
            return
        if row < 0 or row >= self.table.rowCount():
            return
        source = self.table.item(row, 0).text()
        current = self.table.item(row, 1).text() if self.table.item(row, 1) else self._target_for(source)
        # 目录和文件名都已预填，用户最多点两下即可确认，不需要手动输入。
        path, _ = QFileDialog.getSaveFileName(
            self, f"选择输出 {self.target_kind} 文件", current, f"{self.target_kind} 文件 (*{self.output_suffix})",
        )
        if not path:
            return
        path = str(Path(path).with_suffix(self.output_suffix))
        self._manual_targets[source] = path
        self._set_item(row, 1, path)
        self._set_item(row, 2, self._status_for(path))
        self._update_output_field()
        self._update_summary()

    def _on_cell_double_clicked(self, row: int, column: int):
        if column == 1:
            self.browse_row_target(row)
        elif column == 0:
            item = self.table.item(row, 0)
            if item and item.text():
                path = Path(item.text())
                target_dir = path.parent if path.is_file() else path
                if target_dir.exists():
                    QDesktopServices.openUrl(QUrl.fromLocalFile(str(target_dir)))

    def _on_selection_changed(self):
        self.remove_button.setEnabled(bool(self.table.selectedIndexes()))

    def _show_context_menu(self, pos):
        item = self.table.itemAt(pos)
        if not item:
            return
        row = item.row()
        menu = QMenu(self)
        act_change = menu.addAction(f"选择输出 {self.target_kind} 位置")
        act_open_src = menu.addAction(f"打开源 {self.source_kind} 所在目录")
        act_open_dest = menu.addAction(f"打开输出 {self.target_kind} 所在目录")
        menu.addSeparator()
        act_remove = menu.addAction("移除此项")
        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if action == act_change:
            self.browse_row_target(row)
        elif action == act_open_src:
            source = self.table.item(row, 0).text() if self.table.item(row, 0) else ""
            if source and Path(source).parent.is_dir():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(source).parent)))
        elif action == act_open_dest:
            target = self.table.item(row, 1).text() if self.table.item(row, 1) else ""
            target_dir = Path(target).parent if target else None
            if target_dir and target_dir.is_dir():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(target_dir)))
        elif action == act_remove:
            self.table.selectRow(row)
            self.remove_selected()

    # -------------------------------------------------------------------- 汇总
    def _update_summary(self):
        count = self.table.rowCount()
        self.count_label.setText(f"{count} 份")
        self.clear_button.setEnabled(count > 0)
        if self._message:
            self.summary.setText(self._message)
        elif count == 0:
            self.summary.setText(f"拖入 {self.source_kind} 文件或文件夹，或点击上方按钮添加。")
        else:
            manual = len([s for s in (self.table.item(r, 0).text() for r in range(count))
                          if s in self._manual_targets])
            extra = f"，其中 {manual} 份已单独指定位置" if manual else ""
            self.summary.setText(f"已收集 {count} 份 {self.source_kind}{extra}，确认输出位置后点击“开始转换”。")

    def _set_message(self, text: str):
        self._message = text
        self._update_summary()

    # -------------------------------------------------------------------- 输入
    def add_files(self):
        if self._task_dialog is not None:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, f"添加 {self.source_kind} 文件", "",
            "PDF 文档 (*.pdf)" if self.direction == "pdf_to_word" else "Word 文档 (*.doc *.docx)")
        if paths:
            self.inputs.extend(paths)
            self.rescan()

    def add_folder(self):
        if self._task_dialog is not None:
            return
        path = QFileDialog.getExistingDirectory(self, "添加待转换文件夹")
        if path:
            self.inputs.append(path)
            self.rescan()

    def _drop_paths(self, mime_data):
        if self._task_dialog is not None:
            return []
        allowed = DIRECTIONS[self.direction][0]
        paths = []
        for url in mime_data.urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_dir() or (path.is_file() and path.suffix.lower() in allowed
                                 and not path.name.startswith("~$")):
                paths.append(str(path))
        return paths

    def _accept_drag(self, event):
        if self._drop_paths(event.mimeData()):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            return True
        event.ignore()
        return False

    def dragEnterEvent(self, event):
        self._accept_drag(event)

    def dragMoveEvent(self, event):
        # 不处理会被系统判定为禁止拖放，所以拖动过程中必须持续响应。
        self._accept_drag(event)

    def dropEvent(self, event):
        paths = self._drop_paths(event.mimeData())
        if not paths:
            if event.mimeData().hasUrls():
                self._set_message(f"当前是“{self.source_kind} 转 {self.target_kind}”，只接收 {self.source_kind} 文件或文件夹。")
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        self.inputs.extend(paths)
        QTimer.singleShot(0, self.rescan)

    def eventFilter(self, watched, event):
        if watched is self.table or watched is self.table.viewport():
            if event.type() == QEvent.Type.KeyPress:
                if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                    if self.table.selectedIndexes():
                        self.remove_selected()
                        return True
            if event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
                return self._accept_drag(event)
            if event.type() == QEvent.Type.Drop:
                self.dropEvent(event)
                return True
        return super().eventFilter(watched, event)

    # ------------------------------------------------------------------ 清单维护
    def clear(self):
        """清空清单时保留已选的统一输出目录。"""
        if self._task_dialog is not None:
            return
        self.inputs.clear()
        self._manual_targets.clear()
        self._message = ""
        self.table.setRowCount(0)
        self.start.setEnabled(False)
        self.remove_button.setEnabled(False)
        self.clear_button.setEnabled(False)
        self._update_output_field()
        self._update_summary()

    def rescan(self):
        if self._task_dialog is not None:
            return
        recursive = self.recursive.isChecked()
        inputs = list(self.inputs)
        try:
            files, errors = run_operation(self, f"收集 {self.source_kind} 文件", lambda progress, cancel:
                collect_files(inputs, recursive, cancel, direction=self.direction), cancellable=True)
        except OperationCancelled:
            self._set_message("已取消收集，原清单保留。")
            return
        self._message = ""
        found = set(files)
        self._manual_targets = {k: v for k, v in self._manual_targets.items() if k in found}
        self.table.setRowCount(len(files))
        for row, source in enumerate(files):
            target = self._target_for(source)
            self._set_item(row, 0, source)
            self._set_item(row, 1, target)
            self._set_item(row, 2, self._status_for(target))
        self.start.setEnabled(bool(files))
        self._on_selection_changed()
        self._update_output_field()
        self._update_summary()
        if errors:
            box = QMessageBox(QMessageBox.Icon.Warning, "部分路径未加入", f"{len(errors)} 个路径未能读取。", parent=self)
            box.setDetailedText("\n".join(errors))
            box.exec()

    def _set_item(self, row, column, text):
        item = QTableWidgetItem(text)
        if column == 1:
            manual = "已单独指定保存位置" if self.table.item(row, 0) and \
                self.table.item(row, 0).text() in self._manual_targets else "双击可选择保存位置"
            item.setToolTip(f"{text}\n\n{manual}")
            if self.table.item(row, 0) and self.table.item(row, 0).text() in self._manual_targets:
                item.setForeground(QColor(ui_color("accent")))
        elif column == 2:
            item.setToolTip(text)
            if "跳过" in text:
                item.setForeground(QColor(ui_color("warning")))
            elif "成功" in text:
                item.setForeground(QColor(ui_color("success")))
            elif "失败" in text:
                item.setForeground(QColor(ui_color("danger")))
            elif "待转换" in text:
                item.setForeground(QColor(ui_color("accent")))
        else:
            item.setToolTip(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, column, item)

    def remove_selected(self):
        if self._task_dialog is not None:
            return
        for row in sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True):
            src_item = self.table.item(row, 0)
            if src_item:
                self._manual_targets.pop(src_item.text(), None)
            self.table.removeRow(row)
        # 把剩余清单当作新的输入，避免下次重扫又把删掉的文件加回来。
        self.inputs = [self.table.item(i, 0).text() for i in range(self.table.rowCount()) if self.table.item(i, 0)]
        self.start.setEnabled(bool(self.inputs))
        self._on_selection_changed()
        # 删除行不改变其它行的输出位置，刷新汇总即可，不必重建整表。
        self._update_output_field()
        self._update_summary()

    # -------------------------------------------------------------------- 转换
    def convert(self):
        if self._task_dialog is not None:
            return
        if self.direction == "word_to_pdf" and not word_available():
            QMessageBox.information(self, "需要 Microsoft Word", "未检测到本机 Microsoft Word。请安装并完成首次启动后再转换。")
            return
        items = [(self.table.item(i, 0).text(), self.table.item(i, 1).text().strip())
                 for i in range(self.table.rowCount())]
        if not items:
            return
        for source, target in items:
            if not target or Path(target).suffix.lower() != self.output_suffix or not Path(target).is_absolute():
                QMessageBox.warning(self, "请调整输出位置",
                    f"这份 {self.source_kind} 的输出位置不完整，请双击第二列重新选择：\n{target or source}")
                return
            if not Path(target).parent.is_dir():
                QMessageBox.warning(self, "输出文件夹不存在", f"请选择已有文件夹：\n{Path(target).parent}")
                return
        keys = [os.path.normcase(os.path.realpath(target)) for _, target in items]
        counts = Counter(keys)
        collisions = [i for i, key in enumerate(keys) if counts[key] > 1]
        if collisions:
            box = QMessageBox(QMessageBox.Icon.Warning, "输出文件名冲突",
                f"多份 {self.source_kind} 会写入同一个 {self.target_kind} 文件。\n"
                f"可采用带源扩展名的独立文件名，或返回后逐条调整输出位置。", parent=self)
            box.setDetailedText("\n".join(f"{items[i][0]} → {items[i][1]}" for i in collisions))
            rename = box.addButton("采用独立文件名", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("返回调整", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is not rename:
                return
            for index in collisions:
                source, target = items[index]
                target = str(Path(target).with_name(Path(source).name + self.output_suffix))
                items[index] = source, target
                self._manual_targets[source] = target
                self._set_item(index, 1, target)
            keys = [os.path.normcase(os.path.realpath(target)) for _, target in items]
            if len(set(keys)) != len(keys):
                QMessageBox.warning(self, "仍存在冲突",
                    f"请为每份 {self.source_kind} 分别选择不同的 {self.target_kind} 保存位置。")
                return
        needs_native_import = False
        pdf_mode=self.pdf_mode.currentData()
        if self.direction == "pdf_to_word" and pdf_mode=='word' and word_available():
            def inspect_inputs(progress, cancel):
                for index, (source, _) in enumerate(items):
                    check_cancel(cancel)
                    progress(index, len(items), f"正在检查文字层 · {Path(source).name}")
                    if not needs_text_export(source, cancel):
                        return True
                return False
            try:
                needs_native_import = run_operation(self, "检查 PDF 文字层", inspect_inputs, cancellable=True)
            except OperationCancelled:
                self._set_message("检查已取消，未开始转换。")
                return
        if needs_native_import:
            try:
                if pdf_import_needs_confirmation():
                    accepted = QMessageBox.question(self, "首次启用 PDF 转 Word",
                        "Word 的 PDF 导入确认会使后台转换等待。\n是否让 Word 不再重复显示这项确认？\n"
                        "这与 Word 提示框中的“不再显示”选项相同，会记住此选择。",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No)
                    if accepted != QMessageBox.StandardButton.Yes:
                        return
                    allow_pdf_import()
            except (RuntimeError, OSError) as exc:
                QMessageBox.information(self, "Word 尚未准备好", str(exc))
                return
        overwrite = self.overwrite.isChecked()
        try:
            results, cancelled, version = run_operation(self, f"批量 {self.source_kind} 转 {self.target_kind}", lambda progress, cancel:
                convert_batch(items, overwrite, progress, cancel, direction=self.direction, pdf_mode=pdf_mode), cancellable=True)
        except Exception as exc:
            QMessageBox.warning(self, "转换未启动", str(exc))
            return
        labels = {"success": "成功", "skipped": "跳过", "failed": "失败"}
        for row in range(self.table.rowCount()):
            self._set_item(row, 2, "取消后未处理" if cancelled else "未处理")
        for row, result in enumerate(results):
            self._set_item(row, 2, labels[result["state"]] + " · " + result["message"])
        counts = Counter(result["state"] for result in results)
        summary = f"{'已取消' if cancelled else '转换结束'} · 成功 {counts['success']} · 跳过 {counts['skipped']} · 失败 {counts['failed']}"
        self.summary.setText(summary)
        box = QMessageBox(QMessageBox.Icon.Information, "转换清单", summary + "\n已完成的输出已保留。", parent=self)
        details = [f"{labels[r['state']]}：{r['source']} → {r['target']}\n{r['message']}" for r in results]
        if version:
            details.insert(0, "Microsoft Word 版本：" + version)
        box.setDetailedText("\n\n".join(details))
        btn_open = box.addButton("打开输出目录", QMessageBox.ButtonRole.ActionRole)
        box.addButton("完成", QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        if box.clickedButton() == btn_open:
            dest = self.custom_output_dir or (Path(items[0][1]).parent if items else None)
            if dest and Path(dest).is_dir():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(dest)))

    def closeEvent(self, event):
        if self._task_dialog is not None:
            self._task_dialog.request_cancel()
            event.ignore()
        else:
            event.accept()
