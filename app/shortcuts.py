"""Shared shortcut settings and Qt bindings, independent of MainWindow."""
import json
import re
import weakref
from collections import defaultdict

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QKeyCombination, QObject, QSettings, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QApplication, QAbstractButton, QAbstractSpinBox, QDialogButtonBox,
                             QLineEdit, QPlainTextEdit, QTextEdit, QWidget)

from app.runtime import preferences
from app.shortcutcommands import COMMANDS, LEGACY_IDS, scope_label, scopes_overlap

PORTABLE = QKeySequence.SequenceFormat.PortableText
NATIVE = QKeySequence.SequenceFormat.NativeText
SETTINGS_KEY = 'shortcuts/config'
_OWNERS = {command.owner for command in COMMANDS.values() if command.owner}


def canonical(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('请按下一个有效的按键组合。')
    sequence = QKeySequence(value, PORTABLE)
    if sequence.isEmpty() or sequence.count() != 1 or sequence[0].key() in (
            Qt.Key.Key_unknown, Qt.Key.Key_Shift, Qt.Key.Key_Control,
            Qt.Key.Key_Alt, Qt.Key.Key_Meta, Qt.Key.Key_AltGr):
        raise ValueError('每个快捷键需要一个完整组合，不支持连续组合键。')
    combination = sequence[0]
    modifiers = combination.keyboardModifiers() & ~Qt.KeyboardModifier.KeypadModifier
    key = combination.key()
    if key == Qt.Key.Key_Backtab:
        key = Qt.Key.Key_Tab
        modifiers |= Qt.KeyboardModifier.ShiftModifier
    sequence = QKeySequence(QKeyCombination(modifiers, key))
    return sequence.toString(PORTABLE)


def binding_error(command, value):
    sequence = QKeySequence(value, PORTABLE)
    combination = sequence[0]
    if combination.key() == Qt.Key.Key_Escape:
        return 'Esc 保留用于取消当前操作，请选择其他组合。'
    if combination.keyboardModifiers() & Qt.KeyboardModifier.MetaModifier:
        return 'Windows 系统按键不用于应用快捷键。'
    if value in ('Esc', 'Tab', 'Backtab', 'Shift+Tab', 'Alt+F4', 'Alt+Space', 'Alt+Tab',
                 'Alt+Shift+Tab', 'Ctrl+Esc', 'Ctrl+Alt+Del'):
        return '此按键用于取消、切换焦点或系统操作，请选择其他组合。'
    if value in ('Return', 'Enter') and command.scope != 'search':
        return '回车保留用于确认当前输入，请添加 Ctrl、Alt 或 Shift。'
    if command.scope in ('search', 'textbox') and text_editing_combination(sequence):
        if command.scope != 'search' or value not in ('Return', 'Enter', 'Shift+Return', 'Shift+Enter'):
            return '此操作在输入框内使用，请选择不占用文字输入和原生编辑的组合或功能键。'
    return ''


def text_editing_combination(sequence):
    combination = sequence[0]
    key, modifiers = combination.key(), combination.keyboardModifiers()
    if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F35:
        return False
    if not modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier):
        return True
    if sequence.toString(PORTABLE) in ('Ctrl+C', 'Ctrl+X', 'Ctrl+V', 'Ctrl+A', 'Ctrl+Z',
            'Ctrl+Y', 'Ctrl+Shift+Z', 'Ctrl+Ins', 'Shift+Ins', 'Shift+Del', 'Ctrl+Space'):
        return True
    return bool(modifiers & Qt.KeyboardModifier.ControlModifier
                and not modifiers & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)
                and key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down,
                            Qt.Key.Key_Home, Qt.Key.Key_End, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown,
                            Qt.Key.Key_Delete, Qt.Key.Key_Backspace))


def keep_native_text_input(event):
    focus = QApplication.focusWidget()
    return (isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox))
            and text_editing_combination(QKeySequence(event.keyCombination())))


def default_values():
    return {key: list(dict.fromkeys(canonical(value) for value in command.defaults))
            for key, command in COMMANDS.items()}


def conflicts(values):
    by_key = defaultdict(list)
    found = []
    for identifier, bindings in values.items():
        if identifier not in COMMANDS:
            continue
        for value in bindings:
            try:
                key = canonical(value)
            except ValueError:
                continue
            for other in by_key[key]:
                if other != identifier and scopes_overlap(COMMANDS[identifier], COMMANDS[other]):
                    found.append((other, identifier, key))
            by_key[key].append(identifier)
    return found


def decode_config(raw):
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise ValueError('配置内容无法读取，请选择有效的快捷键 JSON 文件。') from error
    if not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1:
        raise ValueError('不支持此快捷键配置版本。')
    if not isinstance(data.get('overrides'), dict):
        raise ValueError('配置缺少有效的快捷键设置。')
    return data['overrides']


def clean_hint(text):
    while match := re.search(r'\s*[（(]([^()（）]+)[)）]$', text):
        try:
            for key in match[1].split(' / '):
                canonical(key)
        except ValueError:
            break
        text = text[:match.start()]
    return text.strip()


def _fresh_settings(previous):
    settings = (QSettings(previous.format(), previous.scope(), previous.organizationName(), previous.applicationName())
                if previous.organizationName() else QSettings(previous.fileName(), previous.format()))
    settings.setFallbacksEnabled(previous.fallbacksEnabled())
    return settings


class ShortcutManager(QObject):
    changed = pyqtSignal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        # Keep our I/O error status independent of the window's other preferences.
        self.settings = _fresh_settings(settings)
        self.defaults = default_values()
        self.notices = []
        self._bindings = {}
        self._hints = {}
        self._updating_hints = False
        self._values = self._load()

    def _load(self):
        values = {key: keys[:] for key, keys in self.defaults.items()}
        migrate = not self.settings.contains(SETTINGS_KEY) and self.settings.contains('reading/shortcuts')
        try:
            if migrate:
                old = json.loads(self.settings.value('reading/shortcuts', '{}', type=str))
                if not isinstance(old, dict):
                    raise ValueError('旧阅读快捷键配置无法读取。')
                overrides = {LEGACY_IDS[key]: [value] if value else [] for key, value in old.items()
                             if key in LEGACY_IDS and isinstance(value, str)}
            elif self.settings.contains(SETTINGS_KEY):
                overrides = decode_config(self.settings.value(SETTINGS_KEY, '', type=str))
            else:
                overrides = {}
        except (TypeError, ValueError) as error:
            self.notices.append(f'{error} 原配置已保留，本次使用默认快捷键。')
            return values
        explicit = set()
        for identifier, saved in overrides.items():
            if identifier not in COMMANDS:
                self.notices.append('已跳过无法识别的命令：' + str(identifier))
                continue
            explicit.add(identifier)
            values[identifier] = []
            if not isinstance(saved, list):
                self.notices.append(f'“{COMMANDS[identifier].label}”的异常绑定已停用。')
                continue
            for value in saved:
                try:
                    key = canonical(value)
                    error = binding_error(COMMANDS[identifier], key)
                    if error:
                        raise ValueError(error)
                    if key not in values[identifier]:
                        values[identifier].append(key)
                except ValueError:
                    self.notices.append(f'“{COMMANDS[identifier].label}”的一个异常绑定已停用。')
        for left, right, key in conflicts(values):
            # Explicit choices win over new defaults; two invalid saved choices
            # are both suspended rather than silently picking a winner.
            losers = [right] if left in explicit and right not in explicit else (
                [left] if right in explicit and left not in explicit else [left, right])
            for identifier in losers:
                if key in values[identifier]:
                    values[identifier].remove(key)
                    self.notices.append(f'“{COMMANDS[identifier].label}”的冲突绑定 {key} 已停用。')
        if migrate:
            error = self._write(values)
            if error:
                self.notices.append('旧配置本次已读取，但迁移未保存：' + error)
        return values

    def values(self):
        return {key: values[:] for key, values in self._values.items()}

    def normalize(self, values):
        result = {}
        for identifier in COMMANDS:
            bindings = values.get(identifier, self.defaults[identifier])
            if not isinstance(bindings, (list, tuple)):
                raise ValueError(f'“{COMMANDS[identifier].label}”的快捷键需要是一个列表。')
            result[identifier] = []
            for value in bindings:
                key = canonical(value)
                error = binding_error(COMMANDS[identifier], key)
                if error:
                    raise ValueError(f'“{COMMANDS[identifier].label}”：{error}')
                if key not in result[identifier]:
                    result[identifier].append(key)
        return result

    def error(self, values):
        try:
            normalized = self.normalize(values)
        except ValueError as error:
            return str(error)
        collisions = conflicts(normalized)
        if collisions:
            left, right, key = collisions[0]
            return (f'{key} 同时用于“{COMMANDS[left].label}”和“{COMMANDS[right].label}”，'
                    f'生效位置重叠（{scope_label(COMMANDS[left])}／{scope_label(COMMANDS[right])}）。')
        return ''

    def encode(self, values=None):
        current = self._values if values is None else values
        return json.dumps({'version': 1, 'overrides': {
            key: bindings for key, bindings in current.items() if bindings != self.defaults[key]
        }}, ensure_ascii=False, indent=2)

    def import_config(self, raw):
        overrides = decode_config(raw)
        unknown = [str(key) for key in overrides if key not in COMMANDS]
        values = {key: keys[:] for key, keys in self.defaults.items()}
        values.update({key: keys for key, keys in overrides.items() if key in COMMANDS})
        # Conflicts remain in the draft, so the user can resolve them explicitly.
        return self.normalize(values), unknown

    def _write(self, values):
        old = self.settings.value(SETTINGS_KEY) if self.settings.contains(SETTINGS_KEY) else None
        try:
            self.settings.setValue(SETTINGS_KEY, self.encode(values))
            self.settings.sync()
            if self.settings.status() != QSettings.Status.NoError:
                raise OSError('设置位置无法写入')
        except (OSError, RuntimeError) as error:
            if old is None:
                self.settings.remove(SETTINGS_KEY)
            else:
                self.settings.setValue(SETTINGS_KEY, old)
            self.settings.sync()
            # QSettings keeps the first error status. A fresh instance allows
            # retrying after permissions/disk space have been repaired.
            self.settings = _fresh_settings(self.settings)
            return f'快捷键未保存，仍使用原设置。请检查设置目录的写入权限。{error}'
        return ''

    def apply(self, values):
        error = self.error(values)
        if error:
            return error
        normalized = self.normalize(values)
        error = self._write(normalized)
        if error:
            return error
        self._values = normalized
        self.notices.clear()
        for token in list(self._bindings):
            self._sync_binding(token)
        for (identifier, _), (reference, text) in list(self._hints.items()):
            target = reference()
            if target is not None and not sip.isdeleted(target):
                target.setToolTip(self.hint(identifier, text))
                target.setStatusTip(self.hint(identifier, text))
        self.changed.emit()
        return ''

    def sequences(self, identifier):
        return [QKeySequence(value, PORTABLE) for value in self._values[identifier]]

    def label(self, identifier, *, all_keys=False):
        sequences = self.sequences(identifier)
        return ' / '.join(key.toString(NATIVE) for key in (sequences if all_keys else sequences[:1]))

    def hint(self, identifier, text=None):
        text = COMMANDS[identifier].label if text is None else text
        binding = self.label(identifier, all_keys=True)
        return f'{text} ({binding})' if binding else text

    def describe(self, text):
        for phrase, identifier, label in (
            ('Ctrl+Z 可撤销', 'edit.undo', '可撤销'), ('Ctrl+Z 撤销', 'edit.undo', '撤销'),
            ('Ctrl+S 保存', 'file.save', '保存'), ('Ctrl+Enter 完成', 'textbox.apply', '完成文本框'),
        ):
            text = text.replace(phrase, self.hint(identifier, label))
        return text

    def watch_hint(self, identifier, target, text):
        token = (identifier, id(target))
        if token not in self._hints:
            target.destroyed.connect(lambda _=None, t=token: self._hints.pop(t, None))
        self._hints[token] = (weakref.ref(target), text)
        target.setToolTip(self.hint(identifier, text))
        target.setStatusTip(self.hint(identifier, text))

    def matches(self, identifier, event):
        if event.isAutoRepeat() and not COMMANDS[identifier].repeat:
            return False
        try:
            sequence = canonical(QKeySequence(event.keyCombination()).toString(PORTABLE))
        except ValueError:
            return False
        return sequence in self._values[identifier]

    def uses_reader_space(self, event=None):
        value = (QKeySequence(event.keyCombination()).toString(PORTABLE) if event is not None else 'Space')
        return any(value in keys and COMMANDS[key].scope in ('main', 'reader', 'spread')
                   for key, keys in self._values.items())

    def bind_action(self, identifier, action, widgets=None, *, context=None):
        token = (identifier, id(action))
        if token in self._bindings:
            return action
        command = COMMANDS[identifier]
        if widgets is not None:
            for widget in widgets if isinstance(widgets, (tuple, list)) else [widgets]:
                widget.addAction(action)
        action.setProperty('shortcutCommand', identifier)
        if context is not None:
            action.setShortcutContext(context)
        action.setAutoRepeat(command.repeat)
        self._bindings[token] = (weakref.ref(action), None, None)
        action.destroyed.connect(lambda _=None, t=token: self._bindings.pop(t, None))
        action.changed.connect(lambda t=token: self._action_hint(t))
        self._sync_binding(token)
        return action

    def bind_callback(self, identifier, owner, callback, *, enabled=None):
        action = QAction(COMMANDS[identifier].label, owner)
        action.triggered.connect(lambda checked=False: callback() if enabled is None or enabled() else None)
        return self.bind_action(identifier, action, owner)

    def bind_shortcut(self, identifier, shortcut):
        token = (identifier, id(shortcut))
        if token not in self._bindings:
            self._bindings[token] = (weakref.ref(shortcut), None, None)
            shortcut.setAutoRepeat(COMMANDS[identifier].repeat)
            shortcut.setProperty('shortcutCommand', identifier)
            shortcut.destroyed.connect(lambda _=None, t=token: self._bindings.pop(t, None))
            self._sync_binding(token)

    def bind_button(self, identifier, button, owner):
        token = (identifier, id(button))
        if token in self._bindings:
            return
        button.setShortcut(QKeySequence())
        button.setProperty('shortcutCommand', identifier)
        shortcut = QShortcut(owner)
        shortcut.setAutoRepeat(COMMANDS[identifier].repeat)
        shortcut.activated.connect(button.click)
        self._bindings[token] = (weakref.ref(button), weakref.ref(shortcut), weakref.ref(owner))
        button.destroyed.connect(lambda _=None, t=token: self._remove_button(t))
        button.installEventFilter(self)
        self._sync_binding(token)

    def _remove_button(self, token):
        binding = self._bindings.pop(token, None)
        shortcut = binding[1]() if binding and binding[1] else None
        if shortcut is not None and not sip.isdeleted(shortcut):
            shortcut.deleteLater()

    def _binding(self, token):
        binding = self._bindings.get(token)
        if binding is None:
            return None
        target, shortcut, owner = (reference() if reference else None for reference in binding)
        if target is None or sip.isdeleted(target) or (binding[1] and (
                shortcut is None or sip.isdeleted(shortcut) or owner is None or sip.isdeleted(owner))):
            self._bindings.pop(token, None)
            return None
        return target, shortcut, owner

    def _action_hint(self, token):
        if self._updating_hints or token not in self._bindings:
            return
        binding = self._binding(token)
        if binding is None:
            return
        action = binding[0]
        self._updating_hints = True
        try:
            base = clean_hint(action.toolTip()) or action.text()
            action.setToolTip(self.hint(token[0], base))
            if action.statusTip():
                action.setStatusTip(self.hint(token[0], clean_hint(action.statusTip())))
        finally:
            self._updating_hints = False

    def _sync_binding(self, token):
        binding = self._binding(token)
        if binding is None:
            return
        target, shortcut, owner = binding
        identifier = token[0]
        if isinstance(target, QShortcut):
            target.setKeys(self.sequences(identifier))
        elif isinstance(target, QAction):
            target.setShortcuts(self.sequences(identifier))
            self._action_hint(token)
        else:
            shortcut.setKeys([] if COMMANDS[identifier].manual else self.sequences(identifier))
            shortcut.setEnabled(target.isEnabled() and (COMMANDS[identifier].scope == 'main' or target.isVisibleTo(owner)))
            target.setToolTip(self.hint(identifier, clean_hint(target.toolTip()) or COMMANDS[identifier].label))

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.EnabledChange, QEvent.Type.Show, QEvent.Type.Hide):
            identifier = watched.property('shortcutCommand')
            if identifier:
                self._sync_binding((identifier, id(watched)))
        return False


def get_manager(settings=None):
    application = QApplication.instance()
    settings = preferences() if settings is None else settings
    identity = settings.fileName()
    if application is None:
        return ShortcutManager(settings)
    managers = getattr(application, '_shortcut_managers', None)
    if managers is None:
        application._shortcut_managers = managers = {}
        application._shortcut_installer = _WindowInstaller(application)
    if identity not in managers:
        managers[identity] = ShortcutManager(settings, application)
    return managers[identity]


def manager_for(widget):
    current = widget
    while current is not None:
        manager = getattr(current, 'shortcut_manager', None)
        if manager is not None:
            return manager
        current = current.parent() if isinstance(current, QObject) else None
    host = getattr(widget, 'host', None)
    if host is not None and host is not widget:
        return manager_for(host)
    return get_manager()


def _caption(text):
    return clean_hint(text).replace('…', '').replace('...', '').strip()


def _target(owner, selector):
    if selector.startswith('attr:'):
        target = owner
        for name in selector[5:].split('.'):
            target = target.get(name) if isinstance(target, dict) else getattr(target, name, None)
        return target
    buttons = [button for button in owner.findChildren(QAbstractButton) if button.window() is owner.window()]
    if selector == 'accept':
        for box in owner.findChildren(QDialogButtonBox):
            if box.window() is not owner.window():
                continue
            for button in box.buttons():
                if box.buttonRole(button) in (QDialogButtonBox.ButtonRole.AcceptRole,
                        QDialogButtonBox.ButtonRole.ApplyRole, QDialogButtonBox.ButtonRole.YesRole):
                    return button
        return None
    kind, _, text = selector.partition(':')
    index = 0
    if '#' in text:
        text, position = text.rsplit('#', 1)
        index = int(position)
    matches = []
    for button in buttons:
        caption = _caption(button.toolTip() if kind == 'tip' else
                           button.accessibleName() if kind == 'name' else button.text())
        if kind == 'text' and not caption:
            caption = _caption(button.toolTip() or button.accessibleName())
        if caption == text or (kind == 'prefix' and caption.startswith(text)):
            matches.append(button)
    return matches[index] if index < len(matches) else None


def bind_window(owner):
    name = type(owner).__name__
    if name not in _OWNERS:
        return
    manager = manager_for(owner)
    owner.shortcut_manager = manager
    bound = getattr(owner, '_bound_shortcut_commands', None)
    if bound is None:
        owner._bound_shortcut_commands = bound = set()
    for identifier, command in COMMANDS.items():
        if command.owner != name or not command.target or identifier in bound:
            continue
        if command.target == 'special:cleanup.fit':
            manager.bind_callback(identifier, owner, lambda: owner.fit.setCurrentIndex(0),
                                  enabled=lambda: owner.fit.isEnabled())
        else:
            button = _target(owner, command.target)
            if not isinstance(button, QAbstractButton):
                continue
            manager.bind_button(identifier, button, owner)
        bound.add(identifier)


class _WindowInstaller(QObject):
    """Attach declared tool commands on Show; never intercept application keys."""
    def __init__(self, application):
        super().__init__(application)
        application.installEventFilter(self)
        self.pending = set()

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.Show or not isinstance(watched, QWidget):
            return False
        window = watched.window()
        if type(window).__name__ not in _OWNERS:
            return False
        if watched is window:
            bind_window(window)
        token = id(window)
        if token not in self.pending:
            self.pending.add(token)
            reference = weakref.ref(window)
            def finish():
                self.pending.discard(token)
                owner = reference()
                if owner is not None and not sip.isdeleted(owner):
                    bind_window(owner)
            QTimer.singleShot(0, finish)
        return False


def install_main_shortcuts(host):
    manager = host.shortcut_manager
    menu_actions = {}
    def walk(menu, path=()):
        for action in menu.actions():
            if action.isSeparator():
                continue
            chain = (*path, action.text().removesuffix('…').removesuffix('...'))
            if action.menu():
                walk(action.menu(), chain)
            else:
                menu_actions[chain] = action
    walk(host._menubar)
    host._shortcut_actions = actions = {}
    for identifier, command in COMMANDS.items():
        if not command.menu or command.menu not in menu_actions:
            continue
        action = menu_actions[command.menu]
        widgets, context = host, Qt.ShortcutContext.WindowShortcut
        if command.scope == 'pages':
            widgets = (host.left_view, host.arrange_view)
            context = Qt.ShortcutContext.WidgetWithChildrenShortcut
        elif command.scope == 'reader':
            widgets = host.preview_view
            context = Qt.ShortcutContext.WidgetWithChildrenShortcut
        actions[identifier] = manager.bind_action(identifier, action, widgets, context=context)
    for identifier, callback in (
        ('reader.previous', lambda: host.step_page(-1)),
        ('reader.next', lambda: host.step_page(1)),
        ('reader.page_number', host.focus_page_number),
        ('search.next', lambda: host.search_bar.step(1)),
        ('search.previous', lambda: host.search_bar.step(-1)),
        ('tabs.next', lambda: host.step_tab(1)),
        ('tabs.previous', lambda: host.step_tab(-1)),
        ('pages.extract_selected', host.extract_selected),
        ('tools.extract', lambda: _run_panel_command(host, 0, 'extract_button')),
        ('tools.split', lambda: _run_panel_command(host, 1, 'split_button')),
        ('tools.excel', lambda: _run_panel_command(host, 2, 'convert_button')),
        ('tools.compress', lambda: _run_panel_command(host, 3, 'compress_button')),
    ):
        actions[identifier] = manager.bind_callback(identifier, host, callback)
    for identifier, button in (('reader.first', host.first_page), ('reader.last', host.last_page)):
        manager.bind_button(identifier, button, host)
    for identifier, command in COMMANDS.items():
        if command.target.startswith('review:'):
            kind = command.target.split(':', 1)[1]
            actions[identifier] = manager.bind_callback(identifier, host,
                lambda value=kind: _select_review_tool(host, value))
        elif identifier.startswith('annotation.'):
            name = identifier.split('.', 1)[1]
            action = QAction(command.label, host)
            action.triggered.connect(lambda checked=False, value=name: _annotation_operation(host, value))
            actions[identifier] = manager.bind_action(identifier, action, host.preview_view,
                context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
    manager.changed.connect(host._sync_shortcut_hints)
    host._sync_shortcut_hints()


def _run_panel_command(host, index, attribute):
    if not host.ws.pages or host._saving or getattr(host, '_task_dialog', None):
        return
    host.open_export_tool(index)
    button = getattr(host, attribute)
    if button.isEnabled():
        button.click()


def _select_review_tool(host, kind):
    if not host.ws.pages or host._saving or getattr(host, '_task_dialog', None):
        return
    host.open_annotation_tools()
    tools = host.annotation_tools
    if kind == 'image':
        tools.insert_image_action.trigger()
        return
    for action, definition in zip(tools.tool_actions, tools.TOOL_DEFS):
        if definition[0] == kind:
            action.trigger()
            return


def _annotation_operation(host, name):
    tools = getattr(host, 'annotation_tools', None)
    if tools is None or host._saving or getattr(host, '_task_dialog', None):
        return
    hover = tools.hover_bar
    if name in ('style', 'note_text', 'delete', 'move') and hover.isVisible():
        button = getattr(hover, {'style': 'style_btn', 'note_text': 'edit_btn',
                                'delete': 'delete_btn', 'move': 'move_btn'}[name])
        if button.isVisible() and button.isEnabled():
            button.click()
        return
    if name == 'move' or not tools.isVisible():
        return
    if name in ('style', 'note_text', 'delete'):
        button = tools.annotation_actions[('style', 'note_text', 'delete').index(name)]
        if not button.isEnabled():
            return
    if name == 'choose_color' and not tools.color_button.isVisible():
        return
    getattr(tools, name)()


def bind_outline(host):
    panel, manager = host.outline_panel, host.shortcut_manager
    for name, callback in (
        ('add', panel.add), ('add_child', panel.add_child), ('rename', panel.rename), ('delete', panel.delete),
        ('move_up', lambda: panel.move(-1)), ('move_down', lambda: panel.move(1)),
        ('child', lambda: panel.shift(1)), ('raise', lambda: panel.shift(-1)),
        ('expand', lambda: panel.set_all_expanded(True)), ('collapse', lambda: panel.set_all_expanded(False)),
    ):
        identifier = 'outline.' + name
        action = (panel.expand_all_action if name == 'expand' else panel.collapse_all_action
                  if name == 'collapse' else QAction(COMMANDS[identifier].label, panel))
        if name not in ('expand', 'collapse'):
            action.triggered.connect(lambda checked=False, fn=callback: fn())
        host._shortcut_actions[identifier] = manager.bind_action(identifier, action, panel,
            context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
    panel.sync_actions()


def nudge_for(widget, prefix, event, small=1):
    manager = manager_for(widget)
    for direction, dx, dy in (('left', -1, 0), ('right', 1, 0), ('up', 0, -1), ('down', 0, 1)):
        for suffix, step in (('', small), ('_fast', 10)):
            if manager.matches(f'{prefix}.nudge_{direction}{suffix}', event):
                return dx * step, dy * step
    return None


_MAIN_BUTTON_IDS = {command.label: key for key, command in COMMANDS.items() if command.menu}
_MAIN_BUTTON_IDS.update({
    '适合宽度': 'view.fit', '适合窗口': 'view.fit', '左转所选页': 'pages.rotate_left',
    '右转所选页': 'pages.rotate_right', '删除所选页': 'pages.delete', '提取所选页': 'pages.extract_selected',
    '上一页': 'reader.previous', '下一页': 'reader.next', '第一页': 'reader.first', '最后一页': 'reader.last',
    '上一处匹配': 'search.previous', '下一处匹配': 'search.next',
    '提取到新 PDF': 'tools.extract', '拆分到文件夹': 'tools.split', '转换为 Excel': 'tools.excel',
    '压缩并预览': 'tools.compress', '选择与排列图片': 'convert.images_pdf', '选择范围并导出': 'convert.pdf_images',
})


def link_main_button_hint(host, button, label):
    text = _caption(label)
    identifier = _MAIN_BUTTON_IDS.get(text)
    if identifier:
        host.shortcut_manager.watch_hint(identifier, button, text)
