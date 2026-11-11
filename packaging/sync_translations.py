"""Extract explicit tr() calls and report changed/missing translations (no network)."""
import argparse
import ast
import json
import re
import sys
from pathlib import Path
from string import Formatter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CATALOGUES = ROOT / 'app' / 'locales'

DISPLAY_CALLS = {
    '_field', '_tool_btn', '_caption', '_act', '_btn', '_icon_btn',
    'QLabel', 'QPushButton', 'QCheckBox', 'QRadioButton', 'QGroupBox', 'QAction',
    'QMenu', 'QMessageBox', 'QListWidgetItem', 'QTableWidgetItem', 'QTreeWidgetItem',
    'hint', 'buttons', 'setText', 'setWindowTitle', 'setToolTip', 'setStatusTip',
    'setAccessibleName', 'setAccessibleDescription', 'setPlaceholderText', 'showMessage',
    'setLabelText', 'setPrefix', 'setSuffix', 'setFormat', 'setTitle',
    'setInformativeText', 'setDetailedText', 'addMenu', 'addAction', 'addItem',
    'addItems', 'addRow', 'addButton', 'addTab', 'setHeaderLabels',
    'setHorizontalHeaderLabels', 'setVerticalHeaderLabels', 'setTabText', 'setItemText',
}


def unmarked_messages():
    """Catch newly hard-coded Chinese display text without touching document values."""
    result = []
    def inspect(node, path):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if re.search('[\u4e00-\u9fff]', node.value):
                result.append(f'{path}:{node.lineno}: {node.value!r}')
        elif isinstance(node, ast.JoinedStr):
            for value in node.values:
                if isinstance(value, ast.Constant):
                    inspect(value, path)
        elif isinstance(node, (ast.IfExp, ast.BoolOp, ast.BinOp, ast.List, ast.Tuple)):
            for value in ast.iter_child_nodes(node):
                inspect(value, path)
    for path in sorted((ROOT / 'app').rglob('*.py')):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ''
            standard_dialog = (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                               and node.func.value.id in ('QMessageBox', 'QFileDialog', 'QInputDialog'))
            if name in DISPLAY_CALLS or standard_dialog:
                for value in node.args:
                    inspect(value, path.relative_to(ROOT).as_posix())
                for value in node.keywords:
                    if value.arg in ('text', 'title', 'label', 'caption', 'filter'):
                        inspect(value.value, path.relative_to(ROOT).as_posix())
    return sorted(set(result))


def messages():
    result = {}
    for path in sorted((ROOT / 'app').rglob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ('tr', 'mark_text') and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                result.setdefault(node.args[0].value, []).append(f'{path.relative_to(ROOT).as_posix()}:{node.lineno}')
    # Metadata remains untranslated so persisted IDs and selectors stay stable.
    from app.shortcutcommands import COMMANDS, SCOPE_LABELS, scope_label
    for command in COMMANDS.values():
        texts = [command.label, command.category, scope_label(command), *command.menu]
        kind, _, text = command.target.partition(':')
        if kind in ('text', 'tip', 'name', 'prefix'):
            texts.append(text.rsplit('#', 1)[0])
        for text in texts:
            result.setdefault(text, []).append(f'app/shortcutcommands.py:{command.id}')
    for text in SCOPE_LABELS.values():
        result.setdefault(text, []).append('app/shortcutcommands.py:SCOPE_LABELS')
    from app.themes import THEMES
    for theme in THEMES:
        for text in (theme.name, theme.description):
            result.setdefault(text, []).append(f'app/themes.py:{theme.key}')
    from app import APP_NAME
    result.setdefault(APP_NAME, []).append('app/__init__.py:APP_NAME')
    result.setdefault('跟随系统 / System default', []).append('app/i18n.py:LANGUAGES')
    return dict(sorted(result.items()))


def fields(text):
    return sorted((field, spec, conversion or '') for _, field, spec, conversion in Formatter().parse(text) if field is not None)


def validate(source, catalogue):
    errors = []
    for key, value in catalogue.items():
        if not isinstance(value, str) or not value.strip():
            errors.append(f'empty/invalid translation: {key!r}')
            continue
        try:
            if fields(key) != fields(value):
                errors.append(f'placeholder mismatch: {key!r}')
        except ValueError:
            errors.append(f'invalid format string: {key!r}')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='check the source index and catalogue placeholders')
    parser.add_argument('--require-complete', action='store_true', help='also fail on missing translations')
    args = parser.parse_args()
    source = messages()
    index = CATALOGUES / 'source.json'
    CATALOGUES.mkdir(exist_ok=True)
    failed = False
    unmarked = unmarked_messages()
    if unmarked:
        print('Unmarked interface text (wrap display text with tr):')
        for entry in unmarked:
            print(f'  {entry}')
        failed |= args.check
    if args.check:
        if not index.is_file() or json.loads(index.read_text(encoding='utf-8')) != source:
            print('Source index is out of date. Run: python packaging/sync_translations.py')
            failed = True
    else:
        index.write_text(json.dumps(source, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for path in sorted(CATALOGUES.glob('*.json')):
        if path.name == 'source.json':
            continue
        catalogue = json.loads(path.read_text(encoding='utf-8'))
        errors = validate(source, catalogue)
        missing = sorted(source.keys() - catalogue.keys())
        obsolete = sorted(catalogue.keys() - source.keys())
        print(f'{path.stem}: {len(source) - len(missing)}/{len(source)} translated, {len(missing)} missing, {len(obsolete)} unused')
        for error in errors:
            print(f'  {error}')
        if not args.check:
            pending = CATALOGUES.parent.parent / 'build' / 'translations' / f'{path.stem}.missing.json'
            pending.parent.mkdir(parents=True, exist_ok=True)
            pending.write_text(json.dumps({key: '' for key in missing}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        failed |= bool(errors) or bool(missing and args.require_complete)
    raise SystemExit(1 if failed else 0)


if __name__ == '__main__':
    main()
