"""Shortcut catalogue. Metadata only: opening settings never constructs a tool.

IDs are the persisted contract. Labels/selectors may change without changing IDs.
Manual commands are dispatched by their existing, state-aware event handlers.
"""
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Command:
    id: str
    label: str
    category: str
    scope: str = 'main'
    defaults: tuple[str, ...] = ()
    menu: tuple[str, ...] = ()
    owner: str = ''
    target: str = ''
    repeat: bool = False
    manual: bool = False


COMMANDS: dict[str, Command] = {}


def _add(key, label, category, **kwargs):
    if key in COMMANDS:
        raise ValueError('Duplicate shortcut command: ' + key)
    COMMANDS[key] = Command(key, label, category, **kwargs)


def _menu(key, path, keys=(), *, scope='main', repeat=False):
    parts = tuple(path.split('/'))
    # The open-file label itself contains slashes.
    if key == 'file.open':
        parts = ('文件', '打开 PDF / OFD / 图片')
    _add(key, parts[-1], '应用设置' if key.startswith('app.') else parts[0],
         defaults=keys, menu=parts, scope=scope, repeat=repeat)


for _key, _path, _keys in (
    ('file.open', '文件/打开 PDF / OFD / 图片', ('Ctrl+O',)),
    ('file.recent', '文件/最近阅读记录', ()),
    ('file.save', '文件/保存', ('Ctrl+S',)),
    ('file.save_as', '文件/另存为', ('Ctrl+Shift+S',)),
    ('file.close', '文件/关闭当前文件', ('Ctrl+W',)),
    ('file.print', '文件/打印', ('Ctrl+P',)),
    ('file.exit', '文件/退出', ()),
    ('app.storage', '文件/应用设置/存储管理', ()),
    ('app.shortcuts', '文件/应用设置/快捷键管理', ()),
    ('app.shell_tools', '文件/应用设置/右键快捷工具', ()),
    ('app.default_pdf', '文件/应用设置/设为默认 PDF 应用', ()),
    ('edit.undo', '编辑/撤销', ('Ctrl+Z',)),
    ('edit.redo', '编辑/重做', ('Ctrl+Y',)),
    ('edit.paste_pages', '编辑/粘贴为页面', ('Ctrl+V',)),
    ('edit.select_all', '编辑/全选', ('Ctrl+A',)),
    ('search.open', '编辑/全文搜索', ('Ctrl+F',)),
    ('review.open', '编辑/审阅与文字修正', ()),
    ('reader.capture', '编辑/框选导出图片', ('Ctrl+Shift+C',)),
    ('pages.insert', '页面/插入页面', ()),
    ('pages.append', '页面/合并文件', ()),
    ('pages.merge_open', '页面/合并已打开文件', ()),
    ('pages.extract', '页面/提取页面', ()),
    ('pages.split', '页面/拆分文件', ()),
    ('pages.split_page', '页面/分割单页', ()),
    ('pages.resize', '页面/调整页面大小', ()),
    ('pages.rotate_left', '页面/旋转页面/向左旋转 90°', ()),
    ('pages.rotate_right', '页面/旋转页面/向右旋转 90°', ()),
    ('document.compare', '文档/两份 PDF 对比（实验）', ()),
    ('document.signature', '文档/签名与保护信息', ()),
    ('document.numbers', '文档/页码处理', ()),
    ('document.watermark', '文档/水印', ()),
    ('document.seam_stamp', '文档/骑缝章', ()),
    ('document.crop', '文档/区域摘录', ()),
    ('document.compress', '文档/压缩/压缩 PDF', ()),
    ('document.compress_image', '文档/压缩/压缩图片', ()),
    ('document.encrypt', '文档/安全与副本/加密另存', ()),
    ('document.decrypt', '文档/安全与副本/解密另存', ()),
    ('document.editable_copy', '文档/安全与副本/制作可编辑副本', ()),
    ('scan.cleanup', '扫描/去除彩色笔迹', ()),
    ('scan.whiten', '扫描/扫描件增强', ()),
    ('scan.deskew', '扫描/扫描纠偏', ()),
    ('scan.ocr', '扫描/文字识别（OCR）', ()),
    ('convert.word_pdf', '转换/Word 转 PDF', ()),
    ('convert.images_pdf', '转换/图片转 PDF', ()),
    ('convert.markdown_pdf', '转换/Markdown 转 PDF', ()),
    ('convert.pdf_word', '转换/PDF 转 Word', ()),
    ('convert.pdf_excel', '转换/PDF 转 Excel', ()),
    ('convert.pdf_text', '转换/PDF 转 TXT', ()),
    ('convert.pdf_images', '转换/PDF 转图片', ()),
    ('convert.pdf_raster', '转换/PDF 转图片版', ()),
    ('reader.read', '视图/阅读', ()),
    ('reader.arrange', '视图/整理页面', ()),
    ('reader.mode', '视图/切换阅读／整理页面', ('F6',)),
    ('view.outline', '视图/大纲书签', ('Ctrl+Shift+B',)),
    ('view.pages', '视图/页面栏', ('F8',)),
    ('view.tools', '视图/工具面板', ('F9',)),
    ('view.fullscreen', '视图/全屏阅读', ('F11',)),
    ('view.borderless', '视图/无边框模式', ('Ctrl+F11',)),
    ('view.auto_sidebar', '视图/侧栏自动隐藏', ()),
    ('reader.single', '视图/阅读设置/单页连续', ('Ctrl+1',)),
    ('reader.spread', '视图/阅读设置/双页对开', ('Ctrl+2',)),
    ('reader.four', '视图/阅读设置/四页预览（2×2）', ('Ctrl+4',)),
    ('reader.fit_page', '视图/阅读设置/适合页面', ()),
    ('reader.fit_width', '视图/阅读设置/适合宽度', ()),
    ('reader.zoom_manual', '视图/阅读设置/手动缩放', ()),
    ('reader.save_view', '视图/阅读设置/将当前视图设为默认', ()),
    ('reader.colors', '视图/阅读设置/阅读配色', ()),
    ('reader.original_colors', '视图/阅读设置/恢复文档原色', ()),
    ('reader.hide_orb', '视图/阅读设置/本次运行隐藏悬浮球', ()),
    ('reader.show_orb', '视图/界面恢复/显示阅读悬浮球', ()),
    ('view.reset', '视图/界面恢复/恢复界面布局', ()),
    ('view.fit', '视图/缩放/适合宽度', ('Ctrl+0',)),
    ('theme.graphite', '视图/界面主题/石墨（默认）', ()),
    ('theme.forest', '视图/界面主题/原版绿', ()),
    ('theme.mountain', '视图/界面主题/远山', ()),
    ('theme.terracotta', '视图/界面主题/赤陶', ()),
    ('theme.night', '视图/界面主题/夜航（深色）', ()),
    ('app.about', '关于', ()),
):
    _menu(_key, _path, _keys)

_menu('edit.copy_pages', '编辑/复制页面', ('Ctrl+C',), scope='pages')
_menu('pages.delete', '页面/删除页面', ('Del',), scope='pages')
_menu('reader.select', '视图/阅读设置/选择文字', ('V',), scope='reader')
_menu('reader.hand', '视图/阅读设置/手形拖动', ('H',), scope='reader')
_menu('view.zoom_in', '视图/缩放/放大', ('Ctrl++', 'Ctrl+='), repeat=True)
_menu('view.zoom_out', '视图/缩放/缩小', ('Ctrl+-',), repeat=True)

for _key, _label, _keys, _repeat in (
    ('reader.previous', '上一页／上一组', ('Alt+Left',), True),
    ('reader.next', '下一页／下一组', ('Alt+Right',), True),
    ('reader.first', '第一页', (), False),
    ('reader.last', '最后一页', (), False),
    ('reader.page_number', '定位页码输入框', ('Ctrl+L',), False),
    ('search.next', '下一处匹配', ('F3',), True),
    ('search.previous', '上一处匹配', ('Shift+F3',), True),
    ('tabs.next', '下一个标签页', ('Ctrl+Tab',), True),
    ('tabs.previous', '上一个标签页', ('Ctrl+Shift+Tab',), True),
    ('pages.extract_selected', '提取所选页', (), False),
    ('tools.extract', '执行提取页面', (), False),
    ('tools.split', '执行拆分文件', (), False),
    ('tools.excel', '执行转换为 Excel', (), False),
    ('tools.compress', '执行压缩并预览', (), False),
):
    _category = {'reader': '视图', 'search': '编辑', 'tabs': '文件', 'pages': '页面', 'tools': '工具面板'}[_key.split('.')[0]]
    _add(_key, _label, _category, defaults=_keys, repeat=_repeat)

for _key, _label, _scope, _keys in (
    ('reader.copy_text', '复制正文文字', 'reader', ('Ctrl+C',)),
    ('reader.previous_group', '对开阅读：上一组', 'spread', ('PgUp',)),
    ('reader.next_group', '对开阅读：下一组', 'spread', ('PgDown',)),
    ('search.confirm', '搜索框：确认搜索／下一处', 'search', ('Return', 'Enter')),
    ('search.input_previous', '搜索框：上一处匹配', 'search', ('Shift+Return', 'Shift+Enter')),
    ('textbox.apply', '提交页面文本框', 'textbox', ('Ctrl+Return', 'Ctrl+Enter')),
    ('image.delete', '删除当前页面图片', 'image', ('Del', 'Backspace')),
):
    _add(_key, _label, '视图' if _scope == 'spread' else '编辑', scope=_scope, defaults=_keys,
         manual=_key != 'reader.copy_text', repeat=_scope in ('spread', 'search'))

for _kind, _name in (
    ('browse', '浏览／移动'), ('select', '选择页面批注'), ('highlight', '文字高亮'),
    ('underline', '下划线'), ('ink', '自由画笔'), ('note', '便签备注'),
    ('freetext', '页面文本框'), ('rect', '矩形框'), ('ellipse', '椭圆'),
    ('arrow', '箭头'), ('replace', '单行文字修正'), ('mosaic', '马赛克'),
    ('image', '插入图片'),
):
    _add('review.' + _kind, _name, '审阅工具', target='review:' + _kind)

for _key, _label in (
    ('style', '应用选中批注样式'), ('note_text', '查看／修改批注备注'),
    ('delete', '删除选中批注'), ('move', '移动当前批注'),
    ('choose_color', '选择批注颜色'), ('deactivate', '收起审阅工具'),
):
    _add('annotation.' + _key, _label, '审阅工具', scope='reader')
for _key, _label in (
    ('add', '在当前页添加书签'), ('rename', '重命名书签'), ('delete', '删除书签（含子项）'),
    ('child', '设为子项'), ('raise', '提升层级'), ('expand', '展开全部书签'), ('collapse', '折叠全部书签'),
):
    _add('outline.' + _key, _label, '大纲书签', scope='outline')


def _window(prefix, owner, title, rows):
    for row in rows:
        key, label, target, *options = row
        defaults = options[0] if options else ()
        _add(prefix + '.' + key, label, title, owner=owner, target=target,
             scope='window:' + owner, defaults=defaults,
             repeat=key in ('zoom_in', 'zoom_out', 'previous', 'next', 'undo', 'redo'))


# Selectors resolve only existing controls; clicks retain their original checks.
# attr: is preferred for dynamic captions. text: accepts a zero-based # suffix.
_window('print', 'PrintDialog', '打印窗口', [
    ('zoom_in', '放大打印预览', 'tip:放大', ('Ctrl+=',)),
    ('zoom_out', '缩小打印预览', 'tip:缩小', ('Ctrl+-',)),
    ('fit', '适应窗口', 'attr:btn_fit', ('Ctrl+0',)),
    ('submit', '开始打印', 'attr:btn_print'),
    ('advanced', '展开／收起更多设置', 'attr:advanced_toggle'),
    ('previous', '上一张纸', 'tip:上一张纸'), ('next', '下一张纸', 'tip:下一张纸'),
])
_window('crop', 'AnnotationCropDialog', '区域摘录窗口', [
    ('delete', '删除所选区域', 'attr:delete', ('Del',)),
    ('undo', '撤销选区操作', 'attr:undo_button', ('Ctrl+Z',)),
    ('export', '导出新 PDF', 'attr:start'),
])
_window('cleanup', 'ColorCleanupDialog', '去除彩色笔迹窗口', [
    ('undo', '撤销保护区操作', 'attr:undo_zone', ('Ctrl+Z',)),
    ('redo', '重做保护区操作', 'attr:redo_zone', ('Ctrl+Y',)),
    ('fit', '适应窗口', 'special:cleanup.fit', ('Ctrl+0',)),
    ('zoom_in', '放大预览', 'attr:zoom_in', ('Ctrl++',)),
    ('zoom_out', '缩小预览', 'attr:zoom_out', ('Ctrl+-',)),
    ('previous', '上一页', 'text:上一页'), ('next', '下一页', 'text:下一页'),
    ('red', '清理红色笔迹', 'attr:red'), ('blue', '清理蓝色笔迹', 'attr:blue'),
    ('pick', '从页面取色', 'attr:pick'), ('adjust', '调整效果', 'attr:advanced'),
    ('original', '查看原图', 'attr:view_buttons.original'), ('cleaned', '查看清理效果', 'attr:view_buttons.cleaned'),
    ('move', '移动页面', 'attr:tool_buttons.move'), ('erase', '擦除笔迹', 'attr:tool_buttons.erase'),
    ('keep', '保留内容', 'attr:tool_buttons.keep'), ('brush', '画笔方式', 'attr:shape_buttons.brush'),
    ('rectangle', '矩形方式', 'attr:shape_buttons.rect'), ('ellipse', '圆形方式', 'attr:shape_buttons.ellipse'),
    ('region', '只清理框选区域', 'attr:region_button'), ('whole', '恢复整页清理范围', 'attr:whole'),
    ('copy_regions', '范围与保护区用于所选页', 'attr:copy_zones'),
    ('clear_manual', '清空本页手工修改', 'attr:clear_manual'),
    ('reset', '恢复默认清理参数', 'attr:reset'), ('help', '处理说明', 'attr:help'),
    ('select_annotations', '勾选筛选结果', 'attr:select_annotations'),
    ('clear_annotations', '取消本页勾选', 'attr:clear_annotations'),
    ('raster', '选择转图片处理', 'attr:fallback'),
])
_window('capture', 'CaptureDialog', '框选图片窗口', [
    ('copy', '复制图片', 'text:复制图片', ('Ctrl+C',)),
    ('save', '保存图片', 'text:保存图片'),
    ('again', '重新框选', 'text:重新框选'),
])
_window('word', 'WordConverterWindow', 'Word 转换窗口', [
    ('word_pdf', '切换 Word 转 PDF', 'attr:btn_word_to_pdf'),
    ('pdf_word', '切换 PDF 转 Word', 'attr:btn_pdf_to_word'),
    ('add', '添加待转换文件', 'attr:add_button'),
    ('add_folder', '添加文件夹', 'attr:add_folder_button'),
    ('remove', '移除所选文件', 'attr:remove_button'),
    ('clear', '清空文件列表', 'attr:clear_button'),
    ('choose_output', '选择输出目录', 'attr:btn_choose_dir'),
    ('source_output', '使用源目录', 'attr:btn_reset_dir'),
    ('convert', '开始转换', 'attr:start'),
    ('install_ocr', '安装识别组件', 'attr:ocr_install'),
])
_window('markdown', 'MarkdownDialog', 'Markdown 转换窗口', [
    ('add', '添加文件', 'text:添加文件'),
    ('output', '选择输出目录', 'attr:output_button'),
    ('convert', '开始转换', 'attr:start'),
])
_window('quick_image', 'ImageQuickDialog', '图片快捷工具', [
    ('up', '图片上移', 'text:上移'), ('down', '图片下移', 'text:下移'),
    ('smaller', '生成更小体积图片', 'attr:smaller'),
    ('open_pdf', '打开 PDF', 'attr:open_pdf'),
    ('open_folder', '打开输出文件夹', 'attr:open_folder'),
    ('finish', '完成／取消当前操作', 'attr:finish'),
])
_window('import_images', 'ImageImportDialog', '图片转 PDF 窗口', [
    ('add', '添加图片', 'text:添加图片'),
    ('up', '图片上移', 'attr:btn_move_up'), ('down', '图片下移', 'attr:btn_move_down'),
    ('remove', '移除图片', 'attr:btn_remove'), ('clear', '清空图片', 'attr:btn_clear'),
])
_window('compare', 'PdfContentCompareDialog', '内容对比窗口', [
    ('choose_left', '选择第一份 PDF', 'text:选择文件#0'),
    ('choose_right', '选择第二份 PDF', 'text:选择文件#1'),
    ('start', '开始比较', 'attr:start'), ('previous', '上一项差异', 'attr:previous'),
    ('next', '下一项差异', 'attr:next'), ('whole', '查看整页／差异', 'attr:whole'),
    ('visual', '版式与图形检查', 'attr:visual'), ('more', '更多设置', 'attr:advanced'),
    ('ocr', '安装 OCR 增强包', 'attr:install'), ('help', '使用说明', 'text:使用说明'),
    ('zoom_in', '放大原页', 'text:放大'), ('zoom_out', '缩小原页', 'text:缩小'),
])
_window('visual_compare', 'PdfCompareDialog', '版式对比窗口', [
    ('choose_left', '选择第一份 PDF', 'text:选择文件#0'),
    ('choose_right', '选择第二份 PDF', 'text:选择文件#1'),
    ('start', '开始比较', 'attr:start'), ('pairs', '调整页面配对', 'attr:adjust_pairs'),
    ('fit', '适合整页', 'text:适合整页'), ('previous', '上一处差异', 'text:上一处差异'),
    ('next', '下一处差异', 'text:下一处差异'), ('more', '更多设置', 'attr:advanced'),
    ('help', '使用说明', 'text:使用说明'),
])
_window('pairing', 'PairingDialog', '页面配对窗口', [
    ('add', '添加对应页', 'text:添加对应页'), ('remove', '移除选中配对', 'text:移除选中'),
])
_window('whitening', 'WhiteningDialog', '扫描件增强窗口', [
    ('more', '更多调整', 'text:更多调整'), ('detect', '重新识别纸边', 'text:重新识别纸边'),
    ('whole', '保留整页', 'text:保留整页'), ('inset', '向内裁一点', 'text:向内裁一点'),
    ('coordinates', '四角坐标', 'text:四角坐标'), ('help', '处理说明', 'text:处理说明'),
    ('compare', '原图对比', 'text:原图对比'), ('corners', '调整纸边', 'text:调整纸边'),
    ('detail', '放大检查', 'text:放大检查'), ('png', '导出当前页 PNG', 'text:导出当前页 PNG'),
])
_window('compression', 'CompressionDialog', '压缩 PDF 窗口', [
    ('start', '开始压缩', 'text:开始压缩'), ('save', '另存压缩结果', 'text:另存压缩结果'),
    ('zoom_in', '放大预览', 'text:+'), ('zoom_out', '缩小预览', 'text:−'),
])
_window('numbers', 'PageNumbersDialog', '页码处理窗口', [
    ('export', '当前文档另存', 'text:当前文档另存'), ('batch', '批量处理文件', 'text:批量文件'),
])
_window('merge', 'MergeOpenDialog', '合并已打开文件窗口', [
    ('all', '全选／取消全选文件', 'attr:select_all_button'), ('up', '文件上移', 'text:上移'), ('down', '文件下移', 'text:下移'),
])
_window('note', 'NoteViewerDialog', '便签窗口', [
    ('save', '保存修改', 'attr:save_btn'), ('delete', '删除便签', 'attr:delete_btn'),
])
_window('history', 'ReadingHistoryDialog', '最近阅读记录窗口', [
    ('open', '打开选中文件', 'text:打开'), ('remove', '移除所选记录', 'text:移除所选'),
    ('clear', '清空记录', 'text:清空记录'),
])
_window('deskew', 'DeskewDialog', '扫描纠偏窗口', [('analyze', '自动分析角度', 'text:自动分析角度')])
_window('seam', 'SeamStampDialog', '骑缝章窗口', [('image', '选择印章图片', 'text:选择图片')])
_window('ocr', 'OCRDialog', '文字识别窗口', [
    ('install', '安装本地 OCR 增强包', 'text:安装本地 OCR 增强包'),
    ('storage', '存储位置', 'text:存储位置'), ('output', '选择输出位置', 'text:选择位置'),
])
_window('raster', 'RasterDialog', '图片版 PDF 窗口', [('output', '选择输出位置', 'text:选择位置')])
_window('colors', 'ReadingColorsDialog', '阅读配色窗口', [
    ('schemes', '返回配色方案', 'text:返回配色方案'), ('reset', '恢复原色', 'text:恢复原色'),
])
_window('signature', 'SignatureInfoDialog', '签名与保护信息窗口', [
    ('locate', '定位签名页', 'text:定位签名页'),
    ('editable', '制作可编辑副本', 'text:制作可编辑副本'),
    ('raster', '制作影像副本', 'text:制作影像副本'),
])
_window('about', 'AboutDialog', '关于窗口', [
    ('updates', '查看更新内容', 'text:查看更新内容'), ('check', '检查更新', 'text:检查更新'),
    ('lanzou', '打开蓝奏云下载', 'text:蓝奏云 ↗'),
])
_window('updates', 'UpdateDialog', '更新内容窗口', [
    ('github', 'GitHub 下载', 'text:GitHub 下载'), ('lanzou', '蓝奏云下载', 'text:蓝奏云下载'),
])
_window('storage', 'StorageDialog', '存储管理窗口', [
    ('usage', '统计用量', 'text:统计用量'), ('backups', '查看旧备份', 'text:查看旧备份'),
    ('clean_backups', '清理旧备份', 'text:清理旧备份'),
    ('clean_runtime', '清理以前组件', 'text:清理以前组件'),
    ('install', '安装增强包', 'text:安装增强包'), ('clean_cache', '清理缓存', 'text:清理缓存'),
    ('previous', '打开所选旧组件目录', 'text:打开'), ('details', '其他文件位置', 'prefix:其他文件位置'),
])
for _kind, _title in (('ocr', 'OCR 组件'), ('cache', '任务缓存'), ('output', '默认输出')):
    _window('storage', 'StorageDialog', '存储管理窗口', [
        (_kind + '_choose', '选择' + _title + '位置', 'name:' + _title + '选择位置'),
        (_kind + '_reset', '恢复' + _title + '默认位置', 'name:' + _title + '恢复默认'),
        (_kind + '_open', '打开' + _title + '目录', 'name:' + _title + '打开目录'),
    ])
_window('runtime', 'RuntimeInstallDialog', '组件安装窗口', [('location', '选择安装位置', 'text:选择位置')])
_window('progress', 'TaskProgressDialog', '任务进度窗口', [('cancel', '取消任务', 'text:取消任务')])
_window('watermark', 'WatermarkDialog', '水印窗口', [
    ('delete', '删除所选水印', 'attr:delete_button'), ('image', '选择水印图片', 'attr:image_button'),
    ('color', '选择文字水印颜色', 'attr:color_button'), ('reset', '复位水印位置', 'attr:reset_button'),
    ('preview', '刷新预览', 'attr:preview_button'),
])
_window('seam', 'SeamStampDialog', '骑缝章窗口', [
    ('advanced', '展开／收起更多设置', 'attr:advanced_toggle'), ('remove', '删除这组骑缝章', 'attr:remove'),
])
_window('mosaic', 'MosaicDialog', '马赛克设置窗口', [('color', '选择遮盖颜色', 'attr:color_button')])
_window('context', 'ContextMenuDialog', '右键快捷工具窗口', [
    ('enable', '启用此版本右键菜单', 'attr:actions.enable'),
    ('repair', '修复此版本右键菜单', 'attr:actions.repair'),
    ('remove', '移除此版本右键菜单', 'attr:actions.remove'),
])

# Dialog confirmation is a business command; Esc/Tab and native input editing
# remain available independently of these optional bindings.
for _prefix, _owner, _title in (
    ('cleanup', 'ColorCleanupDialog', '去除彩色笔迹窗口'),
    ('whitening', 'WhiteningDialog', '扫描件增强窗口'),
    ('deskew', 'DeskewDialog', '扫描纠偏窗口'),
    ('encrypt', 'EncryptionDialog', '加密与权限窗口'),
    ('import_images', 'ImageImportDialog', '图片转 PDF 窗口'),
    ('export_images', 'ImageExportDialog', 'PDF 转图片窗口'),
    ('merge', 'MergeOpenDialog', '合并已打开文件窗口'),
    ('mosaic', 'MosaicDialog', '马赛克设置窗口'),
    ('ocr', 'OCRDialog', '文字识别窗口'),
    ('split_page', 'PageSplitDialog', '分割单页窗口'),
    ('page_size', 'PageSizeDialog', '页面大小窗口'),
    ('pairing', 'PairingDialog', '页面配对窗口'),
    ('raster', 'RasterDialog', '图片版 PDF 窗口'),
    ('colors', 'ReadingColorsDialog', '阅读配色窗口'),
    ('seam', 'SeamStampDialog', '骑缝章窗口'),
    ('watermark', 'WatermarkDialog', '水印窗口'),
    ('runtime', 'RuntimeInstallDialog', '组件安装窗口'),
):
    _window(_prefix, _owner, _title, [('apply', '确认／应用当前设置', 'accept')])

COMMANDS['cleanup.apply'] = replace(COMMANDS['cleanup.apply'], label='另存清理副本', target='attr:apply')
COMMANDS['whitening.corners'] = replace(COMMANDS['whitening.corners'], target='attr:crop_button')
for _id in ('edit.undo', 'edit.redo'):
    COMMANDS[_id] = replace(COMMANDS[_id], repeat=True)

for _prefix, _owner, _category in (
    ('whitening', 'WhiteningDialog', '扫描件增强窗口'), ('watermark', 'WatermarkDialog', '水印窗口'),
):
    for _direction, _label in (('Left', '左移'), ('Right', '右移'), ('Up', '上移'), ('Down', '下移')):
        for _fast in (False, True):
            _add(f'{_prefix}.nudge_{_direction.lower()}' + ('_fast' if _fast else ''),
                 ('快速' if _fast else '微调') + _label, _category, owner=_owner,
                 scope='window:' + _owner, defaults=(('Shift+' if _fast else '') + _direction,),
                 manual=True, repeat=True)

for _id in ('word.remove', 'import_images.remove'):
    COMMANDS[_id] = replace(COMMANDS[_id], defaults=('Del', 'Backspace'), manual=True)

LEGACY_IDS = {key: 'reader.' + key for key in ('mode', 'read', 'arrange', 'select', 'hand', 'previous', 'next')}

SCOPE_LABELS = {
    'main': '主窗口', 'reader': '阅读区', 'pages': '页面列表',
    'spread': '阅读区的对开／四页布局', 'search': '搜索输入框',
    'textbox': '正在编辑的页面文本框', 'image': '阅读区正在选中的图片',
    'outline': '大纲书签面板',
}


def scope_label(command):
    if command.id == 'app.shell_tools':
        return 'Windows 绿色版主窗口'
    if command.id == 'app.default_pdf':
        return 'Windows 主窗口'
    if command.id == 'word.remove':
        return 'Word 转换窗口的文件列表'
    if command.id == 'import_images.remove':
        return '图片导入窗口的图片列表'
    if command.id.startswith('whitening.nudge_'):
        return '扫描件增强：正在调整的纸角'
    if command.id.startswith('watermark.nudge_'):
        return '水印预览：已有水印时'
    return SCOPE_LABELS.get(command.scope, command.category)


def scopes_overlap(left, right):
    a, b = left.scope, right.scope
    if a.startswith('window:') or b.startswith('window:'):
        return a == b
    if a == 'main' or b == 'main':
        return True
    # Page lists and the reader have disjoint focus ownership. All reader
    # submodes are conservatively overlapping, including child text editors.
    if a != b and (a in ('pages', 'outline') or b in ('pages', 'outline')):
        return False
    return True
