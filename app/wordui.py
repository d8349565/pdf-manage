"""Standalone Word batch window, separate from the PDF reading workspace."""
from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox, QFileDialog, QHeaderView, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app import APP_NAME
from app.exportdialogs import hint
from app.output import OperationCancelled
from app.progress import run_operation
from app.wordconvert import collect_files, convert_batch


class WordConverterWindow(QWidget):
    def __init__(self, paths=()):
        super().__init__()
        self.setWindowTitle(f"批量 Word 转 PDF · {APP_NAME}")
        self.resize(1000, 650)
        self.setMinimumSize(720, 460)
        self.inputs = []
        self._task_dialog = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)
        title = QLabel("批量 Word 转 PDF")
        title.setStyleSheet("font-size: 22px; font-weight: 600; color: #173C36;")
        layout.addWidget(title)
        hint(layout, "使用本机已安装的 Microsoft Word，离线原生导出。每份文件默认在原目录生成同名 PDF，已有 PDF 跳过。")
        row = QHBoxLayout()
        for text, slot in (("添加 Word 文件…", self.add_files), ("添加文件夹…", self.add_folder),
                           ("移除所选", self.remove_selected), ("清空", self.clear)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["源 Word 文件", "输出 PDF（可修改）", "状态"])
        self.table.setAccessibleName("Word 转换清单，第二列可以调整目标文件名")
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(2, 210)
        layout.addWidget(self.table, 1)
        self.advanced = QCheckBox("高级选项")
        layout.addWidget(self.advanced)
        self.options = QWidget()
        options = QVBoxLayout(self.options)
        options.setContentsMargins(0, 0, 0, 0)
        self.recursive = QCheckBox("文件夹包含子文件夹（默认仅当前层）")
        self.overwrite = QCheckBox("明确覆盖已有 PDF（签名和权限保护仍生效）")
        options.addWidget(self.recursive)
        options.addWidget(self.overwrite)
        hint(options, "单份超时 180 秒；失败继续下一份。源 Word 只读打开、不保存修改，禁用宏和外链自动更新。")
        self.options.hide()
        self.advanced.toggled.connect(self.options.setVisible)
        self.recursive.toggled.connect(self.rescan)
        layout.addWidget(self.options)
        self.summary = hint(layout, "添加文件或文件夹后，确认输出位置并开始转换。")
        footer = QHBoxLayout()
        hint(footer, "复杂排版和缺失字体请核对转换结果。")
        footer.addStretch()
        self.start = QPushButton("开始转换")
        self.start.setObjectName("primary")
        self.start.setEnabled(False)
        self.start.clicked.connect(self.convert)
        footer.addWidget(self.start)
        layout.addLayout(footer)
        if paths:
            self.inputs = list(paths)
            QTimer.singleShot(0, self.rescan)

    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "添加 Word 文件", "", "Word 文档 (*.doc *.docx)")
        if paths:
            self.inputs.extend(paths)
            self.rescan()

    def add_folder(self):
        path = QFileDialog.getExistingDirectory(self, "添加待转换文件夹")
        if path:
            self.inputs.append(path)
            self.rescan()

    def clear(self):
        self.inputs.clear()
        self.table.setRowCount(0)
        self.start.setEnabled(False)
        self.summary.setText("添加文件或文件夹后开始转换。")

    def rescan(self):
        old_targets = {self.table.item(i, 0).text(): self.table.item(i, 1).text()
                       for i in range(self.table.rowCount())}
        recursive = self.recursive.isChecked()
        inputs = list(self.inputs)
        try:
            files, errors = run_operation(self, "收集 Word 文件", lambda progress, cancel:
                collect_files(inputs, recursive, cancel), cancellable=True)
        except OperationCancelled:
            self.summary.setText("已取消收集，原清单保留。")
            return
        self.table.setRowCount(len(files))
        for row, source in enumerate(files):
            target = old_targets.get(source, str(Path(source).with_suffix(".pdf")))
            self._set_item(row, 0, source, editable=False)
            self._set_item(row, 1, target, editable=True)
            self._set_item(row, 2, "已有 PDF · 默认跳过" if os.path.exists(target) else "待转换", editable=False)
        self.start.setEnabled(bool(files))
        self.summary.setText(f"已收集 {len(files)} 份 Word。输出位置可在第二列修改。")
        if errors:
            box = QMessageBox(QMessageBox.Icon.Warning, "部分路径未加入", f"{len(errors)} 个路径未能读取。", parent=self)
            box.setDetailedText("\n".join(errors))
            box.exec()

    def _set_item(self, row, column, text, *, editable):
        item = QTableWidgetItem(text)
        item.setToolTip(text)
        if not editable:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, column, item)

    def remove_selected(self):
        for row in sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(row)
        # Treat the remaining explicit list as the new inputs, so rescanning won't re-add removed files.
        self.inputs = [self.table.item(i, 0).text() for i in range(self.table.rowCount())]
        self.start.setEnabled(bool(self.inputs))

    def convert(self):
        items = [(self.table.item(i, 0).text(), self.table.item(i, 1).text().strip())
                 for i in range(self.table.rowCount())]
        for source, target in items:
            if not target or Path(target).suffix.lower() != ".pdf" or not Path(target).is_absolute():
                QMessageBox.warning(self, "请调整输出位置", f"输出需要完整的 PDF 文件路径：\n{target or source}")
                return
            if not Path(target).parent.is_dir():
                QMessageBox.warning(self, "输出文件夹不存在", f"请选择已有文件夹：\n{Path(target).parent}")
                return
        keys = [os.path.normcase(os.path.realpath(target)) for _, target in items]
        counts = Counter(keys)
        collisions = [i for i, key in enumerate(keys) if counts[key] > 1]
        if collisions:
            box = QMessageBox(QMessageBox.Icon.Warning, "输出文件名冲突", "多个 Word 将写入同一 PDF。\n"
                "可采用包含 Word 扩展名的独立文件名，或取消后修改第二列。", parent=self)
            box.setDetailedText("\n".join(f"{items[i][0]} → {items[i][1]}" for i in collisions))
            rename = box.addButton("采用独立文件名", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("返回调整", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is not rename:
                return
            for index in collisions:
                source, target = items[index]
                target = str(Path(target).with_name(Path(source).name + ".pdf"))
                items[index] = source, target
                self.table.item(index, 1).setText(target)
            keys = [os.path.normcase(os.path.realpath(target)) for _, target in items]
            if len(set(keys)) != len(keys):
                QMessageBox.warning(self, "仍存在冲突", "请在第二列为每份 Word 分别指定不同的 PDF 文件名。")
                return
        overwrite = self.overwrite.isChecked()
        try:
            results, cancelled, version = run_operation(self, "批量 Word 转 PDF", lambda progress, cancel:
                convert_batch(items, overwrite, progress, cancel), cancellable=True)
        except Exception as exc:
            QMessageBox.warning(self, "转换未启动", str(exc))
            return
        labels = {"success": "成功", "skipped": "跳过", "failed": "失败"}
        for row in range(self.table.rowCount()):
            self._set_item(row, 2, "取消后未处理" if cancelled else "未处理", editable=False)
        for row, result in enumerate(results):
            self._set_item(row, 2, labels[result["state"]] + " · " + result["message"], editable=False)
        counts = Counter(result["state"] for result in results)
        summary = f"{'已取消' if cancelled else '转换结束'} · 成功 {counts['success']} · 跳过 {counts['skipped']} · 失败 {counts['failed']}"
        self.summary.setText(summary)
        box = QMessageBox(QMessageBox.Icon.Information, "转换清单", summary + "\n已完成的 PDF 已保留。", parent=self)
        details = [f"{labels[r['state']]}：{r['source']} → {r['target']}\n{r['message']}" for r in results]
        if version:
            details.insert(0, "Microsoft Word 版本：" + version)
        box.setDetailedText("\n\n".join(details))
        box.exec()

    def closeEvent(self, event):
        if self._task_dialog is not None:
            self._task_dialog.request_cancel()
            event.ignore()
        else:
            event.accept()
