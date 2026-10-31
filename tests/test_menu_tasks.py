"""Task categories retain the real shared actions and independent entry points."""
import pytest
import sys
from dataclasses import replace
import pymupdf as p
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QApplication,QMenu
from app.ui import MainWindow


def all_commands(window):
    commands={}
    def walk(menu,path):
        for action in menu.actions():
            if action.isSeparator() or action is window.reading_fallback_notice:
                continue
            assert '…' not in action.text() and '...' not in action.text(),action.text()
            chain=(*path,action.text())
            if action.menu():walk(action.menu(),chain)
            else:
                assert chain not in commands,'Duplicate command path: '+str(chain)
                commands[chain]=action
    walk(window._menubar,())
    return commands


# Complete user-facing command contract, including nested reading options. This
# list was checked against the pre-migration runtime menu, not just toolbar IDs.
EXPECTED={
    ('文件',):['打开 PDF / OFD / 图片','保存','另存为','关闭当前文件','打印','最近阅读记录','退出'],
    ('文件','应用设置'):['存储管理','阅读快捷键'],
    ('编辑',):['撤销','重做','复制页面','粘贴为页面','全选','全文搜索','审阅与文字修正','框选导出图片'],
    ('页面',):['插入页面','合并文件','合并已打开文件','提取页面','拆分文件','分割单页','调整页面大小','删除页面'],
    ('页面','旋转页面'):['向左旋转 90°','向右旋转 90°'],
    ('文档',):['页码处理','水印','骑缝章','区域摘录','签名与保护信息'],
    ('文档','压缩'):['压缩 PDF','压缩图片'],
    ('文档','安全与副本'):['加密另存','解密另存','制作可编辑副本'],
    ('扫描',):['去除彩色笔迹','扫描件增强','扫描纠偏','文字识别（OCR）'],
    ('转换',):['Word 转 PDF','图片转 PDF','Markdown 转 PDF','PDF 转 Word','PDF 转 Excel','PDF 转 TXT','PDF 转图片','PDF 转图片版'],
    ('视图',):['阅读','整理页面','切换阅读／整理页面','全屏阅读','无边框模式','侧栏自动隐藏','大纲书签','页面栏','工具面板'],
    ('视图','阅读设置'):['单页连续','双页对开','四页预览（2×2）','适合页面','适合宽度','手动缩放','将当前视图设为默认','阅读配色','恢复文档原色','全屏阅读','无边框模式','侧栏自动隐藏','本次运行隐藏悬浮球','手形拖动','选择文字','阅读快捷键'],
    ('视图','界面主题'):['石墨（默认）','原版绿','远山','赤陶','夜航（深色）'],
    ('视图','缩放'):['放大','缩小','适合宽度'],
    ('视图','界面恢复'):['显示阅读悬浮球','恢复界面布局'],
    ():['关于'],
}


@pytest.mark.parametrize('portable',[False,True])
def test_complete_menu_contract_has_no_missing_or_unexpected_commands(qapp,tmp_path,monkeypatch,portable):
    import app.ui as ui
    monkeypatch.setattr(ui,'portable_root',lambda:tmp_path if portable else None)
    window=MainWindow()
    try:
        expected={(*parent,label) for parent,labels in EXPECTED.items() for label in labels}
        if sys.platform=='win32':
            expected.add(('文件','应用设置','设为默认 PDF 应用'))
            if portable:expected.add(('文件','应用设置','右键快捷工具'))
        actual=all_commands(window)
        assert set(actual)==expected,{'missing':expected-set(actual),'unexpected':set(actual)-expected}
    finally:
        window.close();qapp.processEvents()


@pytest.mark.parametrize('signature',['none','present','unknown'])
def test_dynamic_copy_menu_option_survives_refresh(qapp,tmp_path,monkeypatch,signature):
    path=tmp_path/'generated-state.pdf'
    with p.open() as doc:
        doc.new_page().insert_text((30,60),'Generated menu state');doc.save(path)
    window=MainWindow()
    try:
        pages=window.store.load(str(path));window.ws.open_replace(pages,str(path))
        source=window.store._source(pages[0].source_id)
        source.safety=replace(source.safety,signature=signature)
        window.refresh();window._sync_controls()
        label='制作影像副本' if signature=='unknown' else '制作可编辑副本'
        assert window.editable_action.text()==label
        assert all_commands(window)[('文档','安全与副本',label)] is window.editable_action
        assert window.editable_action.isEnabled()==(signature!='none')
        if signature!='none':
            assert not window.delete_action.isEnabled() and not window.whiten_action.isEnabled()
        if signature=='unknown':
            called=[]
            monkeypatch.setattr(window,'rasterize_document',lambda **kwargs:called.append(kwargs))
            window.editable_action.trigger()
            assert called==[{'recovery':True}]
    finally:
        window.ws.reset();window.close();qapp.processEvents()


def test_existing_shortcuts_survive_nested_menu_relocation(qapp):
    window=MainWindow()
    try:
        for name,key in (('open_action','Open'),('save_action','Save'),('save_as_action','SaveAs'),
                         ('print_action','Print'),
                         ('undo_action','Undo'),('redo_action','Redo'),('find_action','Find')):
            standard=getattr(QKeySequence.StandardKey,key)
            assert getattr(window,name).shortcut()==QKeySequence(standard),name
        commands=all_commands(window)
        expected={('文件','关闭当前文件'):'Ctrl+W',
                  ('编辑','全选'):'Ctrl+A',('编辑','粘贴为页面'):'Ctrl+V',
                  ('编辑','框选导出图片'):'Ctrl+Shift+C',
                  ('视图','页面栏'):'F8',('视图','工具面板'):'F9',
                  ('视图','缩放','适合宽度'):'Ctrl+0',
                  ('视图','阅读设置','单页连续'):'Ctrl+1',
                  ('视图','阅读设置','双页对开'):'Ctrl+2',
                  ('视图','阅读设置','四页预览（2×2）'):'Ctrl+4'}
        for path,key in expected.items():assert commands[path].shortcut()==QKeySequence(key)
    finally:
        window.close();qapp.processEvents()


def test_arrange_context_menu_retains_preview_copy_and_positioned_paste(qapp,tmp_path,monkeypatch):
    path=tmp_path/'context-pages.pdf'
    with p.open() as doc:
        doc.new_page().insert_text((30,60),'Generated context menu');doc.save(path)
    window=MainWindow();captured=[]
    try:
        window.ws.open_replace(window.store.load(str(path)),str(path))
        window.resize(1180,800);window.show();window.stack.setCurrentIndex(1);window.refresh();qapp.processEvents()
        def capture(menu,*_):
            captured.extend(menu.actions());return None
        monkeypatch.setattr(QMenu,'exec',capture)
        point=window.arrange_view.visualRect(window.model.index(0)).center()
        window.arrange_preview.context_menu(point)
        assert all('…' not in action.text() and '...' not in action.text() for action in captured)
        assert any(action.text()=='放大预览（Esc 关闭）' for action in captured)
        assert window.copy_pages_action in captured
        assert any('粘贴为页面' in action.text() for action in captured)
    finally:
        window.ws.reset();window.close();qapp.processEvents()


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


def test_top_level_task_menus_share_actions_with_existing_tools(qapp):
    window=MainWindow()
    try:
        root=window._menubar.actions()
        assert [action.text() for action in root[:7]]==['文件','编辑','页面','文档','扫描','转换','视图']
        assert root[-1] is window.about_action and window.about_action.menu() is None
        menus={action.text():action.menu() for action in root if action.menu()}
        assert window.copy_pages_action in menus['编辑'].actions()
        assert window.paste_pages_action in menus['编辑'].actions()
        assert window.insert_action in menus['页面'].actions()
        assert window.delete_action in menus['页面'].actions()
        assert window.page_numbers_action in menus['文档'].actions()
        assert window.whiten_action.text()=='扫描件增强'
        assert menus['扫描'].actions()==[window.color_cleanup_action,window.whiten_action,window.deskew_action,window.ocr_action]
        compression=next(action.menu() for action in menus['文档'].actions() if action.text()=='压缩')
        assert compression.actions()==[window.compress_action,window.image_compress_action]
        assert window.image_compress_action.isEnabled() and not window.compress_action.isEnabled()
        settings=next(action.menu() for action in menus['文件'].actions() if action.text()=='应用设置')
        assert window.storage_action in settings.actions()
        window._ensure_arrange()
        assert window.arrange_size_button.defaultAction() is window.page_size_action
    finally:
        window.ws.reset();window.store.close_all();window.close();qapp.processEvents()
