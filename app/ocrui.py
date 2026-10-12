"""OCR 文字识别 UI：设置弹窗 + 进度复用 + 完成统计。

设计要点（PRD §4.2 / §4.4）：

* **不修改原 PDF**：输出 ``<原文件名>_OCR.pdf``；
* **不自动覆盖**：文件存在时按规则自动重命名，避免覆盖；
* **OCR 增强包未安装**：清晰提示，不崩溃；
* **取消不留尾巴**：复用 :func:`app.progress.run_operation` 的取消机制；
* **未保存修改检查**：调用前提示用户保存，避免 OCR 过期磁盘文件。
"""
from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QButtonGroup,
)

from app import APP_NAME
from app.ocr.contracts import OCRRunOptions
from app.ocr.model_manager import check_environment
from app.ocr.service import OCROperationError, run_ocr as _run_ocr_service
from app.progress import run_operation


log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 设置弹窗
# --------------------------------------------------------------------------- #


class OCRDialog(QDialog):
    """OCR 文字识别设置弹窗。"""

    def __init__(self, parent, *, source_path: str, page_count: int,
                 current_page: int | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ocrDialog")
        self.setWindowTitle(f"{APP_NAME} · OCR 文字识别")
        self.setModal(True)
        self.setMinimumWidth(480)
        self._source_path = source_path
        self._page_count = page_count

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)

        # 标题
        heading = QLabel("OCR 文字识别")
        heading.setStyleSheet("font-size: 18px; font-weight: 600; color: #173C36;")
        layout.addWidget(heading)

        # 环境检查提示
        env = check_environment()
        if not env.is_ready:
            warn = QLabel(
                f"未检测到 OCR 增强包。\n请先安装到：{env.install_dir}\n"
                f"缺失文件：{', '.join(env.missing_files) or 'manifest.json'}"
            )
            warn.setStyleSheet(
                "background: #FFF7E0; border: 1px solid #F2C66A; border-radius: 8px;"
                "padding: 12px; color: #6B4E00;"
            )
            warn.setWordWrap(True)
            layout.addWidget(warn)

        # 表单：识别范围
        form = QFormLayout()
        form.setSpacing(8)
        self.scope_group = QButtonGroup(self)
        self.radio_current = QRadioButton("当前页")
        self.radio_all = QRadioButton("全部页面")
        self.radio_range = QRadioButton("指定页码")
        self.scope_group.addButton(self.radio_current, 0)
        self.scope_group.addButton(self.radio_all, 1)
        self.scope_group.addButton(self.radio_range, 2)
        self.radio_all.setChecked(True)
        scope_row = QHBoxLayout()
        for r in (self.radio_current, self.radio_all, self.radio_range):
            scope_row.addWidget(r)
        scope_row.addStretch(1)
        self.range_edit = QLineEdit()
        self.range_edit.setPlaceholderText("例如 1-3,5,8")
        self.range_edit.setEnabled(False)
        self.radio_range.toggled.connect(self.range_edit.setEnabled)
        scope_row.addWidget(QLabel("页码："))
        scope_row.addWidget(self.range_edit)
        form.addRow("识别范围：", scope_row)

        self.skip_checkbox = QCheckBox("跳过已有文字的页面（推荐）")
        self.skip_checkbox.setChecked(True)
        form.addRow("", self.skip_checkbox)

        # 输出
        output_row = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_edit.setText(self._default_output())
        self.output_edit.setMinimumWidth(280)
        output_row.addWidget(self.output_edit, 1)
        browse_btn = QPushButton("选择…")
        browse_btn.clicked.connect(self._pick_output)
        output_row.addWidget(browse_btn)
        form.addRow("输出文件：", output_row)
        self.overwrite_check = QCheckBox("已存在时自动重命名（不覆盖原文件）")
        self.overwrite_check.setChecked(True)
        form.addRow("", self.overwrite_check)

        layout.addLayout(form)

        # 操作按钮
        self.button_box = QDialogButtonBox()
        self.start_btn = QPushButton("开始识别")
        self.start_btn.setDefault(True)
        self.cancel_btn = QPushButton("取消")
        self.button_box.addButton(self.start_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        self.button_box.addButton(self.cancel_btn, QDialogButtonBox.ButtonRole.RejectRole)
        self.start_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)
        layout.addWidget(self.button_box)

        if not env.is_ready:
            self.start_btn.setEnabled(False)

        if page_count <= 0:
            self.radio_current.setEnabled(False)
            self.radio_range.setEnabled(False)
            self.range_edit.setEnabled(False)

    # ------------------------------------------------------------------ #

    def _default_output(self) -> str:
        p = Path(self._source_path)
        if p.is_file():
            return str(p.with_name(f"{p.stem}_OCR.pdf"))
        return str(Path.home() / f"{p.stem}_OCR.pdf")

    def _pick_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "选择输出文件",
            self.output_edit.text(),
            "PDF (*.pdf)",
        )
        if path:
            self.output_edit.setText(path)

    # ------------------------------------------------------------------ #

    def build_options(self) -> OCRRunOptions:
        source = Path(self._source_path)
        page_indices: list[int] = []
        scope = self.scope_group.checkedId()
        if scope == 0:  # 当前页
            page_indices = [0]  # service 会结合 current_page 修正
        elif scope == 2:  # 指定页码
            page_indices = self._parse_range(self.range_edit.text())
        return OCRRunOptions(
            page_indices=page_indices,
            skip_existing_text_layer=self.skip_checkbox.isChecked(),
            render_dpi=200,
            output_path=self.output_edit.text().strip() or None,
            overwrite_existing=not self.overwrite_check.isChecked(),
        )

    @staticmethod
    def _parse_range(text: str) -> list[int]:
        if not text.strip():
            return []
        result: list[int] = []
        for part in re.split(r"[,，]", text):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                try:
                    a, b = part.split("-", 1)
                    a_i, b_i = int(a), int(b)
                    if a_i > b_i:
                        a_i, b_i = b_i, a_i
                    result.extend(range(a_i, b_i + 1))
                except ValueError:
                    continue
            else:
                try:
                    result.append(int(part))
                except ValueError:
                    continue
        # 转换为 0-based
        return [p - 1 for p in result if p > 0]


# --------------------------------------------------------------------------- #
# 入口函数：被 app/ui.py 调用
# --------------------------------------------------------------------------- #


def open_ocr_for(parent, source_path: str, *, current_page: int | None = None,
                 on_complete: Callable | None = None) -> bool:
    """从主窗口触发 OCR 流程。

    返回 True 表示 OCR 已成功发起；False 表示用户取消或前置条件不通过。
    """
    if not source_path:
        QMessageBox.warning(parent, APP_NAME, "请先打开一个 PDF 文件。")
        return False
    source = Path(source_path)
    if not source.is_file():
        QMessageBox.warning(parent, APP_NAME, f"找不到文件：{source_path}")
        return False

    # 未保存修改检查（PRD §4.3 步骤 3 / §7.1）
    ws = getattr(parent, "ws", None)
    if ws is not None and getattr(ws, "dirty", False):
        choice = QMessageBox.question(
            parent,
            APP_NAME,
            "当前文档存在未保存的修改。\n"
            "OCR 会基于磁盘上的文件处理，建议先保存或另存为。\n\n"
            "是否继续 OCR？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return False

    # 弹窗
    doc = _open_quiet(source_path)
    page_count = doc.page_count if doc else 0
    if doc is not None:
        doc.close()

    dialog = OCRDialog(parent, source_path=source_path, page_count=page_count,
                        current_page=current_page)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False

    options = dialog.build_options()
    if current_page is not None and options.page_indices == [0]:
        options.page_indices = [current_page]

    def _task(report, cancel):
        return _run_ocr_service(
            source_path=source_path,
            destination_path=options.output_path,
            page_indices=options.page_indices or None,
            options=options,
            progress_callback=lambda c, t, m: report(c, t, m),
            cancel_check=cancel,
        )

    try:
        job = run_operation(parent, "OCR 文字识别", _task, cancellable=True)
    except OCROperationError as exc:
        QMessageBox.critical(parent, APP_NAME, f"OCR 失败：{exc}")
        return False
    except Exception as exc:  # noqa: BLE001
        log.exception("OCR unhandled error")
        QMessageBox.critical(parent, APP_NAME, f"OCR 异常：{exc}")
        return False

    _show_completion(parent, job)
    if on_complete:
        try:
            on_complete(job)
        except Exception:
            log.exception("OCR on_complete callback error")
    return True


def _open_quiet(path: str):
    try:
        import pymupdf
        doc = pymupdf.open(path)
        return doc
    except Exception:
        return None


def _show_completion(parent, job) -> None:
    lines = [
        f"输出文件：{job.output_path}",
        f"已处理：{len(job.processed_pages)} 页",
        f"跳过：{len(job.skipped_pages)} 页",
        f"失败：{len(job.failed_pages)} 页",
    ]
    if job.failed_pages:
        for f in job.failed_pages[:3]:
            lines.append(f"  · 第 {f.page_index + 1} 页：{f.reason}")
        if len(job.failed_pages) > 3:
            lines.append(f"  · 其余 {len(job.failed_pages) - 3} 页省略…")
    if job.warnings:
        lines.append("")
        lines.append("提示：")
        for w in job.warnings:
            lines.append(f"  · {w}")
    text = "\n".join(lines)

    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle(f"{APP_NAME} · OCR 完成")
    box.setText("OCR 文字识别完成。")
    box.setInformativeText(text)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    open_btn = box.addButton("打开识别后的 PDF", QMessageBox.ButtonRole.ActionRole)
    folder_btn = box.addButton("打开文件夹", QMessageBox.ButtonRole.ActionRole)
    box.exec()
    if box.clickedButton() is open_btn:
        try:
            os.startfile(job.output_path)  # type: ignore[attr-defined]
        except Exception:
            pass
    elif box.clickedButton() is folder_btn:
        try:
            os.startfile(str(Path(job.output_path).parent))  # type: ignore[attr-defined]
        except Exception:
            pass