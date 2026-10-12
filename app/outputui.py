"""Small output dialogs, loaded only when a conversion is requested."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QListWidget, QMessageBox, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)

from app.exportdialogs import hint, new_pdf_path
from app.output import OperationCancelled
from app.pdfio import PdfError, PdfStore, WriteOptions
from app.progress import run_operation


class MarkdownDialog(QDialog):
    def __init__(self, host=None, paths=()):
        super().__init__(host)
        self.host = host
        self.paths = []
        self.setAcceptDrops(True)
        self.setWindowTitle("Markdown 转 PDF")
        self.resize(680, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        hint(layout, "离线排版标题、列表、表格、代码块和中文。仅加载 Markdown 目录内图片；公式和 Mermaid 按源码显示。")

        files_card = QFrame()
        files_card.setObjectName("card")
        files_layout = QVBoxLayout(files_card)
        files_layout.setContentsMargins(14, 12, 14, 12)
        files_layout.setSpacing(8)

        files_header = QHBoxLayout()
        files_title = QLabel("待转换 Markdown 文件 · 可直接拖入")
        files_title.setStyleSheet("font-weight: 600; color: #134E4A;")
        files_header.addWidget(files_title)
        files_header.addStretch()

        add = QPushButton("添加文件…")
        add.setFixedHeight(28)
        add.clicked.connect(self.add)
        files_header.addWidget(add)
        files_layout.addLayout(files_header)

        self.files = QListWidget()
        self.files.setFrameShape(QListWidget.Shape.NoFrame)
        self.files.setAcceptDrops(False)
        self.files.viewport().setAcceptDrops(False)
        files_layout.addWidget(self.files, 1)
        layout.addWidget(files_card, 1)

        options_card = QFrame()
        options_card.setObjectName("card")
        options_layout = QVBoxLayout(options_card)
        options_layout.setContentsMargins(14, 12, 14, 12)
        options_layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)
        self.orientation = QComboBox()
        self.orientation.addItems(["A4 纵向", "A4 横向（宽表格）"])
        form.addRow("纸张方向", self.orientation)
        self.font_size = QSpinBox()
        self.font_size.setRange(5, 36)
        self.font_size.setValue(11)
        form.addRow("正文字号", self.font_size)
        self.margin = QSpinBox()
        self.margin.setRange(5, 40)
        self.margin.setValue(15)
        self.margin.setSuffix(" mm")
        form.addRow("页边距", self.margin)

        self.folder = None
        folder = QPushButton("输出到各源文件目录 · 点击可统一选择目录")
        def choose_folder():
            path = QFileDialog.getExistingDirectory(self, "选择 PDF 输出目录")
            if path:
                self.folder = Path(path)
                folder.setText("输出目录：" + path)
        folder.clicked.connect(choose_folder)
        form.addRow("输出目录", folder)
        options_layout.addLayout(form)

        self.overwrite = QCheckBox("明确覆盖已有 PDF（签名与权限保护仍生效）")
        options_layout.addWidget(self.overwrite)
        layout.addWidget(options_card)

        self.feedback = hint(layout, "已有同名 PDF 默认跳过。")

        footer = QHBoxLayout()
        footer.addStretch()
        self.start = QPushButton("开始转换")
        self.start.setProperty("primary", True)
        self.start.setFixedHeight(34)
        self.start.setEnabled(False)
        self.start.clicked.connect(self.convert)
        footer.addWidget(self.start)
        layout.addLayout(footer)
        if paths:
            self.add_paths(paths)

    def add(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "添加 Markdown", "", "Markdown (*.md *.markdown)")
        if paths:
            self.add_paths(paths)

    def add_paths(self, paths):
        seen = {path.casefold() for path in self.paths}
        added = 0
        for value in paths:
            path = Path(value)
            if path.suffix.lower() not in (".md", ".markdown") or not path.is_file():
                continue
            path = str(path.resolve())
            if path.casefold() not in seen:
                seen.add(path.casefold())
                self.paths.append(path)
                self.files.addItem(path)
                added += 1
        self.start.setEnabled(bool(self.paths))
        self.feedback.setText(f"已添加 {added} 份，共 {len(self.paths)} 份。重复文件自动忽略，已有同名 PDF 默认跳过。"
                              if self.paths else "请拖入或选择 .md、.markdown 文件。")

    def _drop_paths(self, mime_data):
        if getattr(self, "_task_dialog", None) is not None:
            return []
        return [url.toLocalFile() for url in mime_data.urls()
                if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in (".md", ".markdown")
                and Path(url.toLocalFile()).is_file()]

    def dragEnterEvent(self, event):
        if self._drop_paths(event.mimeData()):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = self._drop_paths(event.mimeData())
        if paths:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self.add_paths(paths)
        else:
            event.ignore()

    def convert(self):
        from app.markdownconvert import MarkdownPdfOptions, markdown_font, markdown_to_pdf
        if not self.paths:
            self.feedback.setText("请先添加 Markdown 文件。")
            return
        options = MarkdownPdfOptions(bool(self.orientation.currentIndex()), self.margin.value(), self.font_size.value())
        items = [(source, (self.folder or Path(source).parent) / (Path(source).stem + ".pdf")) for source in self.paths]
        if len({str(target).casefold() for _, target in items}) != len(items):
            self.feedback.setText("输出目录中存在同名文件，请分别转换或保留各源文件目录。")
            return
        markdown_font()
        overwrite = self.overwrite.isChecked()
        def work(progress, cancel):
            results = []
            for index, (source, target) in enumerate(items):
                if cancel():
                    break
                try:
                    if self.host is not None and self.host.store.is_source_destination(str(target)):
                        raise PdfError("不能覆盖主窗口中已打开的来源文件。")
                    if target.exists() and not overwrite:
                        results.append(f"跳过：{target.name}（已存在）")
                    else:
                        result = markdown_to_pdf(source, target, options=options, cancel=cancel,
                            progress_callback=lambda current, total, text: progress(index, len(items), f"{Path(source).name} · {text}"))
                        results.append(f"成功：{result.path}" + ("\n" + "\n".join(result.warnings) if result.warnings else ""))
                except OperationCancelled:
                    break
                except (ValueError, PdfError, OSError) as exc:
                    results.append(f"失败：{Path(source).name} · {exc}")
                progress(index + 1, len(items), "正在转换 Markdown")
            return results, cancel()
        results, cancelled = run_operation(self, "Markdown 转 PDF", work, cancellable=True)
        self.feedback.setText(("已取消，完整输出已保留。" if cancelled else "转换结束。") + "\n" + "\n".join(results))

    def reject(self):
        task = getattr(self, "_task_dialog", None)
        if task is not None:
            task.request_cancel()
        else:
            super().reject()

    def closeEvent(self, event):
        task = getattr(self, "_task_dialog", None)
        if task is not None:
            task.request_cancel()
            event.ignore()
        else:
            super().closeEvent(event)


class _PreviewSignals(QObject):
    ready = pyqtSignal(int, object, str)


class _NumberPreview(QRunnable):
    def __init__(self, store, pages, index, options, generation, signals):
        super().__init__()
        self.store, self.pages, self.index, self.options = store, pages, index, options
        self.generation, self.signals = generation, signals

    def run(self):
        try:
            image = self.store.number_preview(self.pages, self.index, self.options, 550)
            self.signals.ready.emit(self.generation, image, "")
        except Exception as exc:
            self.signals.ready.emit(self.generation, None, str(exc))


class PageNumbersDialog(QDialog):
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.generation = 0
        self.setWindowTitle("页码处理 · 另存副本")
        self.resize(900, 620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        hint(layout, "可见页码会新增到页面；旧页码不会自动擦除。阅读器标签只改变页码显示。原文件保留。")

        row = QHBoxLayout()
        row.setSpacing(14)

        # Left options card
        left_card = QFrame()
        left_card.setObjectName("card")
        left_card_layout = QVBoxLayout(left_card)
        left_card_layout.setContentsMargins(14, 14, 14, 14)
        left_card_layout.setSpacing(10)

        card_title = QLabel("页码规则设置")
        card_title.setStyleSheet("font-weight: 600; color: #134E4A;")
        left_card_layout.addWidget(card_title)

        form = QFormLayout()
        form.setSpacing(8)
        self.mode = QComboBox()
        self.mode.addItem("添加可见页码", "visible")
        self.mode.addItem("仅设置阅读器页码标签", "labels")
        form.addRow("用途", self.mode)
        self.skip = QSpinBox()
        self.skip.setRange(0, 1000000)
        form.addRow("跳过前几页", self.skip)
        self.start = QSpinBox()
        self.start.setRange(1, 1000000)
        form.addRow("起始编号", self.start)
        self.format = QComboBox()
        for name, value in (("1", "number"), ("1 / 总数", "fraction"), ("第 1 页，共 N 页", "chinese"), ("Page 1 of N", "english"), ("- 1 -", "dash")):
            self.format.addItem(name, value)
        form.addRow("可见格式", self.format)
        self.position = QComboBox()
        for name, value in (("页脚居中", "bottom-center"), ("页脚左侧", "bottom-left"), ("页脚右侧", "bottom-right"),
                            ("页眉居中", "top-center"), ("页眉左侧", "top-left"), ("页眉右侧", "top-right")):
            self.position.addItem(name, value)
        form.addRow("位置", self.position)
        self.mirror = QCheckBox("奇数页右侧、偶数页左侧")
        form.addRow("双面装订", self.mirror)
        self.font_size = QSpinBox()
        self.font_size.setRange(5, 36)
        self.font_size.setValue(10)
        form.addRow("字号", self.font_size)
        self.margin = QSpinBox()
        self.margin.setRange(2, 40)
        self.margin.setValue(10)
        self.margin.setSuffix(" mm")
        form.addRow("距页边", self.margin)
        self.label_style = QComboBox()
        for name, value in (("1, 2, 3", "D"), ("i, ii, iii", "r"), ("I, II, III", "R"), ("a, b, c", "a"), ("A, B, C", "A")):
            self.label_style.addItem(name, value)
        form.addRow("标签格式", self.label_style)

        left_card_layout.addLayout(form)
        left_card_layout.addStretch()
        row.addWidget(left_card, 0)

        # Right preview card
        preview_card = QFrame()
        preview_card.setObjectName("card")
        preview_card_layout = QVBoxLayout(preview_card)
        preview_card_layout.setContentsMargins(14, 14, 14, 14)
        preview_card_layout.setSpacing(10)

        prev_title = QLabel("实时效果预览")
        prev_title.setStyleSheet("font-weight: 600; color: #134E4A;")
        preview_card_layout.addWidget(prev_title)

        preview_viewport = QFrame()
        preview_viewport.setStyleSheet("background: #E8EFEB; border: 1px solid #D5E2DE; border-radius: 8px;")
        pv_layout = QVBoxLayout(preview_viewport)
        pv_layout.setContentsMargins(12, 12, 12, 12)

        self.preview = QLabel("正在预览当前页…")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet("background: transparent;")
        pv_layout.addWidget(self.preview)

        preview_card_layout.addWidget(preview_viewport, 1)
        row.addWidget(preview_card, 1)

        layout.addLayout(row, 1)
        self.feedback = hint(layout, "总数为实际参与编号的页数。奇偶页按输出文件的物理页序判断。")

        footer = QHBoxLayout()
        footer.addStretch()

        export_btn = QPushButton("当前文档另存…")
        export_btn.setProperty("primary", True)
        export_btn.setFixedHeight(34)
        export_btn.clicked.connect(self.export)
        footer.addWidget(export_btn)

        batch_btn = QPushButton("批量文件…")
        batch_btn.setFixedHeight(34)
        batch_btn.clicked.connect(self.batch)
        footer.addWidget(batch_btn)

        close_btn = QPushButton("关闭")
        close_btn.setFixedHeight(34)
        close_btn.clicked.connect(self.reject)
        footer.addWidget(close_btn)

        layout.addLayout(footer)
        self.signals = _PreviewSignals()
        self.signals.ready.connect(self.preview_ready)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(180)
        self.timer.timeout.connect(self.request_preview)
        for widget in (self.mode, self.format, self.position, self.label_style):
            widget.currentIndexChanged.connect(self.schedule_preview)
        for widget in (self.skip, self.start, self.font_size, self.margin):
            widget.valueChanged.connect(self.schedule_preview)
        self.mirror.toggled.connect(self.schedule_preview)
        self.schedule_preview()

    def options(self):
        from app.pagenumbers import PageNumberOptions
        return PageNumberOptions(self.mode.currentData(), self.skip.value(), self.start.value(), self.format.currentData(),
            self.position.currentData(), self.mirror.isChecked(), self.font_size.value(), self.margin.value(), self.label_style.currentData())

    def schedule_preview(self):
        self.generation += 1
        visible = self.mode.currentData() == "visible"
        for widget in (self.format, self.position, self.mirror, self.font_size, self.margin):
            widget.setEnabled(visible)
        self.label_style.setEnabled(not visible)
        self.timer.start()

    def request_preview(self):
        index = self.host.ws.preview_index()
        if index is None:
            self.preview.setText("请先打开 PDF；也可直接批量选择文件。")
            return
        options = self.options()
        if options.mode == "labels":
            import pymupdf
            with pymupdf.open() as doc:
                doc.new_page()
                doc.set_page_labels([{"startpage": 0, "prefix": "", "style": options.label_style,
                                      "firstpagenum": options.start + max(0, index - options.skip)}])
                self.preview.setText("当前页跳过编号" if index < options.skip else "当前页阅读器标签：" + doc[0].get_label())
            return
        QThreadPool.globalInstance().start(_NumberPreview(self.host.store, tuple(self.host.ws.pages), index, options, self.generation, self.signals))

    def preview_ready(self, generation, image, error):
        if generation != self.generation:
            return
        if error:
            self.preview.setText(error)
        else:
            self.preview.setPixmap(QPixmap.fromImage(image).scaled(500, 430, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def export(self):
        if not self.host.ws.pages:
            self.feedback.setText("请先打开 PDF，或使用批量文件按钮。")
            return
        path = new_pdf_path(self, self.host.store, "页码另存副本", str(Path(self.host.ws.path or "文档.pdf").with_name("添加页码.pdf")))
        if not path:
            return
        try:
            result = self.host._write_pdf(list(self.host.ws.pages), path, "添加页码", options=WriteOptions(page_numbers=self.options()), cancellable=True)
            if result:
                self.feedback.setText("已输出：" + result.path + ("\n" + "\n".join(result.warnings) if result.warnings else ""))
        except (PdfError, ValueError, OperationCancelled) as exc:
            self.feedback.setText(str(exc))

    def batch(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "批量添加页码", "", "PDF (*.pdf)")
        if not paths:
            return
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if not folder:
            return
        options = self.options()
        targets = [Path(folder) / (Path(path).stem + "_页码.pdf") for path in paths]
        if len({str(path).casefold() for path in targets}) != len(targets):
            self.feedback.setText("存在同名源文件，请分批处理。")
            return
        def work(progress, cancel):
            results = []
            for index, (source, target) in enumerate(zip(paths, targets)):
                if cancel():
                    break
                store = PdfStore()
                try:
                    if target.exists() or self.host.store.is_source_destination(str(target)):
                        results.append(f"跳过：{target.name}（已存在或正在打开）")
                        continue
                    pages = store.load(source)
                    store.write(pages, str(target), options=WriteOptions(page_numbers=options), cancel=cancel,
                                progress_callback=lambda current, total, text: progress(index, len(paths), text))
                    results.append(f"成功：{target}")
                except OperationCancelled:
                    break
                except (PdfError, ValueError) as exc:
                    results.append(f"失败：{Path(source).name} · {exc}")
                finally:
                    store.close_all()
                progress(index + 1, len(paths), "正在批量编号")
            return results
        results = run_operation(self, "批量页码处理", work, cancellable=True)
        self.feedback.setText("\n".join(results))

    def done(self, result):
        self.generation += 1
        self.timer.stop()
        super().done(result)
