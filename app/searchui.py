"""Non-destructive document search with cancellable background scanning."""
from threading import Event

from PyQt6.QtCore import QEvent, QObject, QRunnable, QThreadPool, QSize, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QSizePolicy

from app.icons import toolbar_icon

MAX_MATCHES = 10_000


class SearchSignals(QObject):
    updated = pyqtSignal(int, object)


class SearchJob(QRunnable):
    def __init__(self, store, pages, query, token, cancelled, signals):
        super().__init__()
        self.store, self.pages, self.query = store, pages, query
        self.token, self.cancelled, self.signals = token, cancelled, signals

    def run(self):
        batch, count, text_pages, failures = [], 0, 0, 0
        total = len(self.pages)
        for index, ref in enumerate(self.pages):
            if self.cancelled.is_set():
                return
            try:
                areas, has_text = self.store.search_page(ref, self.query)
                text_pages += int(has_text)
            except Exception:
                areas = []
                failures += 1
            if self.cancelled.is_set():
                return
            room = MAX_MATCHES - count
            batch.extend((index, area) for area in areas[:room])
            count += min(len(areas), room)
            limited = count >= MAX_MATCHES
            done = index+1 == total or limited
            if done or (index+1) % 8 == 0:
                self.signals.updated.emit(self.token, (batch, index+1, total, text_pages, failures, done, limited))
                batch = []
            if done:
                return


class SearchBar(QFrame):
    def __init__(self, host):
        super().__init__(host.preview_view.viewport())
        self.host = host
        self.setObjectName("searchBar")
        self.setFixedHeight(40)
        self.setMaximumWidth(520)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("""
            QFrame#searchBar {
                background: #FFFFFF; border: 1px solid #D7E3DF; border-radius: 7px;
            }
            QLineEdit#searchInput, QLineEdit#searchInput:focus {
                min-height: 0; padding: 0 4px; border: none;
                border-radius: 0; background: transparent; color: #263F39; font-size: 13px;
                selection-background-color: #D9EEEA; selection-color: #263F39;
            }
            QLineEdit#searchInput:focus {
                border-bottom: 1px solid #91B7A9;
            }
            QFrame#searchBar QLabel { color: #526B63; font-size: 12px; }
            QFrame#searchBar QToolButton {
                min-width: 28px; min-height: 28px; padding: 0;
                border: 1px solid transparent; border-radius: 5px; background: transparent;
            }
            QFrame#searchBar QToolButton:hover { background: #EDF4F1; }
            QFrame#searchBar QToolButton:pressed { background: #DBEAE4; }
            QFrame#searchBar QToolButton:focus { border-color: #72A699; }
            QFrame#searchBar QLineEdit#searchInput QToolButton {
                min-width: 18px; min-height: 18px; max-width: 18px; max-height: 18px;
                padding: 0; margin: 0; border: none; border-radius: 3px; background: transparent;
            }
        """)
        self.pages = None
        self.token = 0
        self.cancelled = Event()
        self.results = []
        self.current = -1
        self.busy = False
        self.scanned = self.total = self.text_pages = self.failures = 0
        self.limited = False
        self.start_page = 0
        self.signals = SearchSignals()
        self.signals.updated.connect(self.receive, Qt.ConnectionType.QueuedConnection)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.start)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 5, 4)
        layout.setSpacing(3)
        self.input = QLineEdit()
        self.input.setObjectName("searchInput")
        self.input.setPlaceholderText("搜索全文")
        self.input.setToolTip("输入关键词搜索全文；Enter 下一处，Shift+Enter 上一处")
        self.input.setAccessibleName("全文搜索关键词")
        self.input.setFixedHeight(30)
        leading = self.input.addAction(toolbar_icon("search", "#6A8178", 16), QLineEdit.ActionPosition.LeadingPosition)
        leading.triggered.connect(self.input.setFocus)
        self.clear_action = self.input.addAction(toolbar_icon("x", "#72847D", 14), QLineEdit.ActionPosition.TrailingPosition)
        self.clear_action.setToolTip("清空关键词")
        self.clear_action.setText("清空关键词")
        self.clear_action.triggered.connect(self.input.clear)
        self.clear_action.setVisible(False)
        self.input.textChanged.connect(lambda value: self.clear_action.setVisible(bool(value)))
        self.input.setMaxLength(256)
        self.input.setMinimumWidth(120)
        self.input.textChanged.connect(self.changed)
        self.input.returnPressed.connect(self.enter)
        layout.addWidget(self.input, 1)
        self.feedback = QLabel()
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.feedback.setAccessibleName("全文搜索结果")
        self.feedback.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.feedback.setMinimumWidth(56)
        self.feedback.setMaximumWidth(130)
        layout.addWidget(self.feedback)
        self.previous = host._icon_btn("chevron-left", "上一处匹配 (Shift+Enter / Shift+F3)", lambda: self.step(-1))
        self.next = host._icon_btn("chevron-right", "下一处匹配 (Enter / F3)", lambda: self.step(1))
        self.close_button = host._icon_btn("x", "关闭搜索 (Esc)", self.close_search)
        for button in (self.previous, self.next, self.close_button):
            button.setFixedSize(28, 28)
            button.setIconSize(QSize(16, 16))
            button.setIcon(toolbar_icon("x" if button is self.close_button else
                                       "chevron-left" if button is self.previous else "chevron-right", "#536F65", 16))
        for button in (self.previous, self.next):
            layout.addWidget(button)
        divider = QFrame()
        divider.setFixedSize(1, 16)
        divider.setStyleSheet("background: #DFE8E3; border: none;")
        layout.addWidget(divider)
        layout.addWidget(self.close_button)
        for key, slot in (("Shift+Return", lambda: self.step(-1)), ("Shift+Enter", lambda: self.step(-1)), ("Escape", self.close_search)):
            shortcut = QShortcut(QKeySequence(key), self, slot)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut if key == "Escape"
                                else Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.update_feedback()
        host.preview_view.viewport().installEventFilter(self)
        host.preview_view.view_changed.connect(self.position_bar)
        self.position_bar()
        self.hide()

    def position_bar(self):
        viewport = self.host.preview_view.viewport()
        self.setFixedWidth(min(420, max(0, viewport.width() - 24)))
        self.move(viewport.width() - self.width() - 12, 10)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            self.position_bar()
        return False

    def open_search(self):
        if not self.host.ws.pages:
            return
        self.host.stack.setCurrentIndex(0)
        self.position_bar()
        self.show()
        self.raise_()
        self.sync_document()
        if self.input.text().strip() and not self.results and not self.busy:
            self.changed()
        self.input.setFocus()
        self.input.selectAll()

    def cancel(self):
        self.timer.stop()
        self.cancelled.set()
        self.token += 1
        self.busy = False

    def close_search(self):
        self.cancel()
        self.hide()
        self.results = []
        self.current = -1
        self.host.preview_view.set_search_results([], -1)
        self.host.preview_view.setFocus()

    def sync_document(self):
        # Content changes replace the page list; scrolling does not.
        if self.pages is self.host.ws.pages:
            return
        self.pages = self.host.ws.pages
        self.changed()
        if not self.pages:
            self.input.clear()
            self.close_search()

    def changed(self, *_):
        self.cancel()
        self.results = []
        self.current = -1
        self.scanned = self.text_pages = self.failures = 0
        self.limited = False
        self.host.preview_view.set_search_results([], -1)
        if self.input.text().strip() and not self.isHidden() and self.host.ws.pages:
            self.busy = True
            self.total = len(self.host.ws.pages)
            self.start_page = self.host.ws.preview_index() or 0
            self.timer.start()
        self.update_feedback()

    def start(self):
        if not self.busy:
            return
        self.cancelled = Event()
        QThreadPool.globalInstance().start(SearchJob(self.host.store, tuple(self.host.ws.pages),
            self.input.text().strip(), self.token, self.cancelled, self.signals))

    def receive(self, token, update):
        if token != self.token:
            return
        batch, self.scanned, self.total, self.text_pages, self.failures, done, self.limited = update
        self.results.extend(batch)
        self.busy = not done
        self.host.preview_view.set_search_results(self.results, self.current)
        if done and self.current < 0 and self.results and self.isVisible():
            index = next((i for i, (page, _) in enumerate(self.results) if page >= self.start_page), 0)
            self.jump(index)
        self.update_feedback()

    def update_feedback(self):
        count = len(self.results)
        position = f"{self.current+1} / {count}" if self.current >= 0 else f"{count} 处"
        if self.busy:
            text = f"检索 {self.scanned}/{self.total}"
            detail = f"正在搜索第 {self.scanned}/{self.total} 页 · 已找到 {count} 处"
        elif not self.input.text().strip():
            text = "Ctrl+F"
            detail = "输入关键词搜索全文"
        elif not count:
            text = "未找到" if self.text_pages or self.failures else "无文字层"
            detail = "未找到匹配" if self.text_pages or self.failures else "没有可搜索的文字层；扫描图片需要 OCR 才能搜索"
        else:
            text = position
            detail = f"第 {self.current+1} 处，共 {count} 处" if self.current >= 0 else f"共 {count} 处"
        if self.limited:
            text += "+"
            detail += " · 已达 10,000 处，请缩小关键词范围"
        if self.failures:
            text = text + " · 部分异常" if count else "检索异常"
            detail += f" · {self.failures} 页无法检索"
        self.feedback.setText(text)
        self.feedback.setToolTip(detail)
        self.feedback.setAccessibleDescription(detail)
        self.previous.setEnabled(bool(count))
        self.next.setEnabled(bool(count))

    def enter(self):
        if self.timer.isActive():
            self.timer.stop()
            self.start()
        elif self.results:
            self.step(1)

    def step(self, delta):
        if not self.isVisible():
            self.open_search()
        if self.results:
            self.jump((self.current + delta) % len(self.results) if self.current >= 0 else (0 if delta > 0 else len(self.results)-1))

    def jump(self, index):
        self.current = index
        page, area = self.results[index]
        self.host.stack.setCurrentIndex(0)
        self.host.go_to_page(page+1)
        self.host.preview_view.set_search_results(self.results, index)
        self.host.preview_view.jump_to_search(page, area)
        self.update_feedback()
