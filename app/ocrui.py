"""OCR 操作设置与结果反馈对话框。

遵循 PRD §4.2 / §4.4 与《LEE_skills 工作原则》：
- 零学习成本、自解释界面；
- 防错优于报错：页码输入即时校验，默认不覆盖原件；
- 场景化默认：默认全部页面、跳过已有文字、_OCR.pdf；
- 识别完成后提供“打开识别后的 PDF”与“打开文件夹”。
"""
from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from app import APP_NAME
from app.ocr.contracts import OCRJobResult, OCRRunOptions
from app.ocr.model_manager import check_environment
from app.ocr.service import resolve_default_output_path, run_ocr
from app.progress import run_operation


def parse_page_range(text: str, total_pages: int) -> list[int]:
    """解析如 "1-3, 5, 8" 的页码字符串为 0-based 索引列表。"""
    cleaned = text.strip()
    if not cleaned:
        return []

    pages: set[int] = set()
    parts = cleaned.replace("，", ",").split(",")
    for part in parts:
        chunk = part.strip()
        if not chunk:
            continue
        if "-" in chunk:
            bounds = chunk.split("-", 1)
            try:
                start = int(bounds[0].strip())
                end = int(bounds[1].strip())
                if start > end:
                    start, end = end, start
                for p in range(start, end + 1):
                    if 1 <= p <= total_pages:
                        pages.add(p - 1)
            except ValueError:
                continue
        else:
            try:
                p = int(chunk)
                if 1 <= p <= total_pages:
                    pages.add(p - 1)
            except ValueError:
                continue

    return sorted(pages)


class OCRDialog(QDialog):
    """OCR 文字识别参数配置对话框。"""

    def __init__(
        self,
        parent,
        source_path: str,
        total_pages: int,
        current_page_index: int = 0,
    ) -> None:
        super().__init__(parent)
        self.source_path = source_path
        self.total_pages = total_pages
        self.current_page_index = max(0, min(current_page_index, total_pages - 1))
        self.result: OCRJobResult | None = None

        self.setWindowTitle("OCR 文字识别")
        self.setFixedWidth(480)
        self.setStyleSheet("""
            QDialog { background: #FFFFFF; font-family: "Microsoft YaHei UI"; }
            QLabel { color: #2D3748; font-size: 13px; }
            QLabel#heading { color: #173C36; font-size: 18px; font-weight: bold; }
            QLabel#subheading { color: #536762; font-size: 12px; }
            QRadioButton, QCheckBox { color: #2D3748; font-size: 13px; spacing: 6px; }
            QLineEdit { border: 1px solid #C9D9D3; border-radius: 6px; padding: 6px 10px; font-size: 13px; }
            QLineEdit:focus { border-color: #0F766E; }
            QPushButton { background: #FFFFFF; color: #36564F; border: 1px solid #C9D9D3;
                          border-radius: 6px; padding: 6px 16px; font-size: 13px; min-height: 22px; }
            QPushButton:hover { background: #F0F7F4; border-color: #0F766E; }
            QPushButton#primaryBtn { background: #0F766E; color: #FFFFFF; border: none; font-weight: bold; }
            QPushButton#primaryBtn:hover { background: #0D655E; }
            QFrame#card { background: #F8FAF9; border: 1px solid #E5EBE8; border-radius: 8px; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        # 头部
        header_layout = QVBoxLayout()
        header_layout.setSpacing(4)
        heading = QLabel("OCR 文字识别")
        heading.setObjectName("heading")
        subheading = QLabel("为扫描件与图片 PDF 添加隐藏文字层，支持搜索与复制")
        subheading.setObjectName("subheading")
        header_layout.addWidget(heading)
        header_layout.addWidget(subheading)
        layout.addLayout(header_layout)

        # 卡片 1：识别范围
        range_card = QFrame()
        range_card.setObjectName("card")
        range_layout = QVBoxLayout(range_card)
        range_layout.setContentsMargins(16, 12, 16, 12)
        range_layout.setSpacing(8)

        range_title = QLabel("识别范围")
        range_title.setStyleSheet("font-weight: bold; color: #173C36;")
        range_layout.addWidget(range_title)

        self.btn_group = QButtonGroup(self)
        self.radio_all = QRadioButton(f"全部页面（共 {total_pages} 页）")
        self.radio_all.setChecked(True)
        self.radio_current = QRadioButton(f"仅当前页（第 {self.current_page_index + 1} 页）")
        self.radio_custom = QRadioButton("指定页码：")

        self.btn_group.addButton(self.radio_all)
        self.btn_group.addButton(self.radio_current)
        self.btn_group.addButton(self.radio_custom)

        range_layout.addWidget(self.radio_all)
        range_layout.addWidget(self.radio_current)

        custom_row = QHBoxLayout()
        custom_row.setSpacing(8)
        custom_row.addWidget(self.radio_custom)
        self.edit_range = QLineEdit()
        self.edit_range.setPlaceholderText(f"例如：1-3, 5（1 ~ {total_pages}）")
        self.edit_range.setEnabled(False)
        self.radio_custom.toggled.connect(self.edit_range.setEnabled)
        custom_row.addWidget(self.edit_range, 1)
        range_layout.addLayout(custom_row)

        layout.addWidget(range_card)

        # 卡片 2：识别策略与输出
        opts_card = QFrame()
        opts_card.setObjectName("card")
        opts_layout = QVBoxLayout(opts_card)
        opts_layout.setContentsMargins(16, 12, 16, 12)
        opts_layout.setSpacing(10)

        opts_title = QLabel("识别选项与输出")
        opts_title.setStyleSheet("font-weight: bold; color: #173C36;")
        opts_layout.addWidget(opts_title)

        self.check_skip_existing = QCheckBox("跳过已有可用文字层的页面（推荐）")
        self.check_skip_existing.setChecked(True)
        opts_layout.addWidget(self.check_skip_existing)

        dest_label = QLabel("保存路径：")
        opts_layout.addWidget(dest_label)

        dest_row = QHBoxLayout()
        dest_row.setSpacing(6)
        self.edit_dest = QLineEdit()
        default_out = resolve_default_output_path(source_path)
        self.edit_dest.setText(default_out)
        self.btn_browse = QPushButton("浏览…")
        self.btn_browse.clicked.connect(self._browse_dest)
        dest_row.addWidget(self.edit_dest, 1)
        dest_row.addWidget(self.btn_browse)
        opts_layout.addLayout(dest_row)

        layout.addWidget(opts_card)

        # 底部按钮
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_start = QPushButton("开始识别")
        self.btn_start.setObjectName("primaryBtn")
        self.btn_start.clicked.connect(self._start_ocr)

        btn_row.addWidget(self.btn_cancel)
        btn_row.addWidget(self.btn_start)
        layout.addLayout(btn_row)

    def _browse_dest(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "选择 OCR 输出路径",
            self.edit_dest.text(),
            "PDF 文件 (*.pdf)",
        )
        if path:
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            self.edit_dest.setText(path)

    def _start_ocr(self) -> None:
        # 1. 检查运行环境
        env = check_environment()
        if not env.is_ready:
            QMessageBox.warning(self, "OCR 增强包未安装", env.message)
            return

        # 2. 检查页码范围
        target_indices: list[int] | None = None
        if self.radio_current.isChecked():
            target_indices = [self.current_page_index]
        elif self.radio_custom.isChecked():
            parsed = parse_page_range(self.edit_range.text(), self.total_pages)
            if not parsed:
                QMessageBox.warning(
                    self,
                    "页码格式无效",
                    f"请输入有效的页码或范围，例如：1-3, 5（有效范围 1 ~ {self.total_pages}）。",
                )
                self.edit_range.setFocus()
                return
            target_indices = parsed

        dest_path = self.edit_dest.text().strip()
        if not dest_path:
            QMessageBox.warning(self, "路径无效", "请指定输出文件路径。")
            return

        # 3. 构造选项
        options = OCRRunOptions(
            page_indices=target_indices,
            skip_existing_text_layer=self.check_skip_existing.isChecked(),
            render_dpi=200,
            output_path=dest_path,
            overwrite_existing=False,
        )

        # 4. 执行后台任务并提供进度弹窗
        def work(progress, cancel):
            return run_ocr(
                self.source_path,
                destination_path=dest_path,
                page_indices=target_indices,
                options=options,
                progress_callback=progress,
                cancel_check=cancel,
            )

        try:
            job_result = run_operation(self, "OCR 文字识别", work, cancellable=True)
            if job_result is None:
                # 用户主动取消
                return

            self.result = job_result
            self.accept()
            _show_completion_dialog(self.parent(), job_result)
        except Exception as exc:
            QMessageBox.critical(self, "OCR 识别失败", str(exc))


def _show_completion_dialog(parent, result: OCRJobResult) -> None:
    """展示识别完成摘要弹窗，提供一键打开文件或文件夹。"""
    dlg = QDialog(parent)
    dlg.setWindowTitle("OCR 识别完成")
    dlg.setFixedWidth(460)
    dlg.setStyleSheet("""
        QDialog { background: #FFFFFF; font-family: "Microsoft YaHei UI"; }
        QLabel { color: #2D3748; font-size: 13px; }
        QLabel#title { color: #173C36; font-size: 18px; font-weight: bold; }
        QPushButton { background: #FFFFFF; color: #36564F; border: 1px solid #C9D9D3;
                      border-radius: 6px; padding: 6px 16px; font-size: 13px; min-height: 22px; }
        QPushButton:hover { background: #F0F7F4; border-color: #0F766E; }
        QPushButton#primaryBtn { background: #0F766E; color: #FFFFFF; border: none; font-weight: bold; }
        QPushButton#primaryBtn:hover { background: #0D655E; }
        QFrame#summaryCard { background: #F8FAF9; border: 1px solid #E5EBE8; border-radius: 8px; }
    """)

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(24, 20, 24, 20)
    layout.setSpacing(16)

    title = QLabel("OCR 文字识别已完成")
    title.setObjectName("title")
    layout.addWidget(title)

    card = QFrame()
    card.setObjectName("summaryCard")
    card_layout = QVBoxLayout(card)
    card_layout.setContentsMargins(16, 14, 16, 14)
    card_layout.setSpacing(6)

    p_count = len(result.processed_pages)
    s_count = len(result.skipped_pages)
    f_count = len(result.failed_pages)

    card_layout.addWidget(QLabel(f"• 成功识别页数：<b>{p_count}</b> 页"))
    if s_count > 0:
        card_layout.addWidget(QLabel(f"• 跳过已有文字：<b>{s_count}</b> 页"))
    if f_count > 0:
        card_layout.addWidget(QLabel(f"• 识别失败页数：<b style='color:#E53E3E;'>{f_count}</b> 页"))

    out_name = os.path.basename(result.output_path)
    card_layout.addWidget(QLabel(f"• 输出文件：{out_name}"))
    layout.addWidget(card)

    btn_row = QHBoxLayout()
    btn_row.setSpacing(8)

    btn_open_file = QPushButton("打开识别后的 PDF")
    btn_open_file.setObjectName("primaryBtn")
    btn_open_file.clicked.connect(lambda: (
        QDesktopServices.openUrl(QUrl.fromLocalFile(result.output_path)),
        dlg.accept(),
    ))

    btn_open_folder = QPushButton("打开文件夹")
    btn_open_folder.clicked.connect(lambda: (
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(result.output_path))),
        dlg.accept(),
    ))

    btn_close = QPushButton("关闭")
    btn_close.clicked.connect(dlg.accept)

    btn_row.addWidget(btn_open_file)
    btn_row.addWidget(btn_open_folder)
    btn_row.addStretch()
    btn_row.addWidget(btn_close)
    layout.addLayout(btn_row)

    dlg.exec()
    dlg.deleteLater()
