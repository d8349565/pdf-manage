"""List and use attached files from the reading sidebar."""
from pathlib import Path
from tempfile import NamedTemporaryFile

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
                             QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from app.i18n import tr
from app.output import OperationCancelled, check_cancel, commit_file, stamp
from app.pdfattachments import EmbeddedPdfLink
from app.pdfio import PdfError
from app.progress import OperationThread, run_operation
from app.storage import TemporaryWorkspace


OPENABLE = frozenset({'.pdf', '.txt', '.csv', '.tsv', '.rtf', '.doc', '.docx', '.xls', '.xlsx',
                      '.ppt', '.pptx', '.odt', '.ods', '.odp', '.png', '.jpg', '.jpeg', '.gif',
                      '.bmp', '.webp', '.tif', '.tiff'})


def open_attachment(host, ref, key):
    if getattr(host, '_task_dialog', None):
        return False
    import pymupdf

    pages = host.ws.pages

    def extract(progress, cancel):
        if isinstance(key, EmbeddedPdfLink):
            name, data = host.store.read_linked_pdf(ref, key, pages, cancel)
            if Path(name).suffix.lower() != '.pdf':
                name = Path(name).with_suffix('.pdf').name
        else:
            name, data = host.store.read_attachment(ref, key, cancel)
            if Path(name).suffix.lower() not in OPENABLE:
                raise PdfError(tr('此类型的附件请先另存。'))
        check_cancel(cancel)
        workspace = TemporaryWorkspace('attachment')
        try:
            path = Path(workspace.name) / name
            path.write_bytes(data)
            check_cancel(cancel)
            return workspace, path
        except Exception:
            workspace.cleanup()
            raise

    try:
        host._finish_document_checks([ref])
        workspace, path = run_operation(host, tr('打开附件'), extract, cancellable=True)
        if path.suffix.lower() == '.pdf':
            return host.open_files_in_tabs([str(path)], temporary=workspace)
        if QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            host._opened_attachments.append(workspace)
            host.statusBar().showMessage(tr('已交给默认应用打开附件。'), 5000)
            return True
        raise PdfError(tr('未能打开附件，请检查系统默认应用，或先另存附件。'))
    except OperationCancelled:
        return False
    except (PdfError, OSError, ValueError, RuntimeError, pymupdf.mupdf.FzErrorBase) as exc:
        QMessageBox.warning(host, tr('打开附件'), str(exc))
        return False


def save_attachment(host, attachment):
    if getattr(host, '_task_dialog', None):
        return False
    ref = attachment.ref
    title = tr('另存附件')
    try:
        host._finish_document_checks([ref])
        host.store.require_operation([ref], 'copy')
        suggested = Path(ref.source_path).with_name(attachment.name)
        path, _ = QFileDialog.getSaveFileName(host, title, str(suggested))
        if not path:
            return False
        target = Path(path)
        if host.store.is_source_destination(path):
            raise PdfError(tr('附件不能覆盖已打开的文档，请选择其他位置。'))
        expected = stamp(target)

        def write(progress, cancel):
            _, data = host.store.read_attachment(ref, attachment.key, cancel)
            check_cancel(cancel)
            temporary = NamedTemporaryFile(prefix='.zhixu-attachment-', dir=target.parent, delete=False)
            try:
                with temporary:
                    temporary.write(data)
                commit_file(temporary.name, target, expected, cancel=cancel)
            finally:
                Path(temporary.name).unlink(missing_ok=True)

        run_operation(host, title, write, cancellable=True)
        host.statusBar().showMessage(tr('附件已另存。'), 5000)
        return True
    except OperationCancelled:
        return False
    except (PdfError, OSError, ValueError, RuntimeError) as exc:
        QMessageBox.warning(host, title, str(exc))
        return False


class AttachmentPanel(QWidget):
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self._pages = None
        self._loading_pages = None
        self._worker = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.message = QLabel()
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        self.tree = QTreeWidget()
        self.tree.setAccessibleName(tr('附件'))
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.tree, 1)

        actions = QHBoxLayout()
        self.open_button = QPushButton(tr('打开'))
        self.save_button = QPushButton(tr('另存为…'))
        actions.addWidget(self.open_button)
        actions.addWidget(self.save_button)
        layout.addLayout(actions)
        self.open_button.clicked.connect(self.open_selected)
        self.save_button.clicked.connect(self.save_selected)
        self.tree.itemActivated.connect(lambda *_: self.open_selected())
        self.tree.currentItemChanged.connect(self._update_actions)
        self._update_actions()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        pages = self.host.ws.pages
        if pages is self._pages:
            self._update_actions()
            return
        if pages is self._loading_pages:
            return
        self._pages = None
        self.tree.clear()
        self.message.setText(tr('正在查找附件…') if pages else tr('当前文档没有附件。'))
        self.message.show()
        self._update_actions()
        if self._worker:
            self._worker.cancel_event.set()
            return
        if self.host._closing or not pages or not self.isVisible():
            return
        self._loading_pages = pages
        self._worker = OperationThread(lambda progress, cancel:
            self.host.store.list_attachments(pages, progress, cancel), self, cancellable=True)
        self._worker.progress.connect(self._show_progress, Qt.ConnectionType.QueuedConnection)
        self._worker.finished.connect(self._finish_load, Qt.ConnectionType.QueuedConnection)
        self._worker.start()

    def _show_progress(self, current, total, message):
        if self._loading_pages is self.host.ws.pages:
            self.message.setText(f'{message} {current}/{total}')

    def _finish_load(self):
        worker, pages = self._worker, self._loading_pages
        self._worker = self._loading_pages = None
        worker.deleteLater()
        if self.host._closing:
            return
        if self.host.ws.pages is not pages:
            self.refresh()
            return
        if isinstance(worker.error, OperationCancelled):
            self.refresh()
            return
        if worker.error:
            self.message.setText(tr('无法读取附件。') + '\n' + str(worker.error))
            return
        files = worker.result
        self._pages = pages
        if not files:
            self.message.setText(tr('当前文档没有附件。'))
            return
        for file in files:
            location = tr('第 {p0} 页', p0=file.page_number) if file.page_number is not None else tr('文档')
            item = QTreeWidgetItem([file.name, location])
            item.setData(0, Qt.ItemDataRole.UserRole, file)
            item.setToolTip(0, file.name)
            self.tree.addTopLevelItem(item)
        self.message.hide()
        self.tree.setCurrentItem(self.tree.topLevelItem(0))

    def stop_loading(self):
        if self._worker:
            self._worker.cancel_event.set()
            self._worker.wait()

    def _selected(self):
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item and self._pages is self.host.ws.pages else None

    def _update_actions(self, *_):
        selected = self._selected()
        reason = self.host.store.operation_reason([selected.ref], 'copy') if selected else ''
        openable = bool(selected and Path(selected.name).suffix.lower() in OPENABLE)
        allowed = bool(selected and not reason)
        self.open_button.setEnabled(allowed and openable)
        self.open_button.setToolTip(reason or (tr('此类型的附件请先另存。') if allowed and not openable else ''))
        self.save_button.setEnabled(allowed)
        self.save_button.setToolTip(reason)

    def open_selected(self):
        selected = self._selected()
        if selected and self.open_button.isEnabled():
            open_attachment(self.host, selected.ref, selected.key)

    def save_selected(self):
        selected = self._selected()
        if selected and self.save_button.isEnabled():
            save_attachment(self.host, selected)
