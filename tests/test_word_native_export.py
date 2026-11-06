"""Opt-in native Word export of generated formatting samples, without user files."""
import os
import uuid
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pymupdf
import pytest

from app.wordconvert import convert_batch, word_available


def create_formatted_docx(path, *, template_updates=False):
    ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    paragraphs = [
        '<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>Large title</w:t></w:r></w:p>',
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>First heading</w:t></w:r></w:p>',
        '<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>Second heading</w:t></w:r></w:p>',
    ]
    runs = []
    for text, font, size, bold in (
            ('Regular', 'Arial', 11, False), ('Bold', 'Arial', 11, True),
            ('PlainAgain', 'Arial', 11, False), ('Serif', 'Times New Roman', 15, False),
            ('SerifBold', 'Times New Roman', 15, True), ('CalibriText', 'Calibri', 13, False),
            ('中文宋体', 'SimSun', 12, False), ('中文黑体', 'SimHei', 16, False),
            ('中文加粗', 'SimSun', 12, True)):
        runs.append(f'<w:r><w:rPr><w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:eastAsia="{font}"/>'
                    f'<w:sz w:val="{size * 2}"/><w:b w:val="{int(bold)}"/></w:rPr>'
                    f'<w:t xml:space="preserve">{escape(text)} </w:t></w:r>')
    paragraphs.append('<w:p>' + ''.join(runs) + '</w:p>')
    styles = f'''<w:styles xmlns:w="{ns}">
        <w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="SimSun"/>
        <w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>
        <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
        <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/>
        <w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:sz w:val="56"/></w:rPr></w:style>
        <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>
        <w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/>
        <w:b/><w:sz w:val="40"/></w:rPr></w:style>
        <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Heading1"/>
        <w:pPr><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>
        <w:b w:val="0"/><w:sz w:val="32"/></w:rPr></w:style></w:styles>'''
    with ZipFile(path, 'w') as archive:
        archive.writestr('[Content_Types].xml', '''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
            <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
            <Default Extension="xml" ContentType="application/xml"/>
            <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
            <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
            </Types>''')
        archive.writestr('_rels/.rels', '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
            <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
            </Relationships>''')
        archive.writestr('word/_rels/document.xml.rels', '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
            <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
            </Relationships>''')
        archive.writestr('word/styles.xml', styles)
        archive.writestr('word/document.xml', f'<w:document xmlns:w="{ns}"><w:body>' + ''.join(paragraphs)
                         + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                         + '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>'
                         + '</w:sectPr></w:body></w:document>')
    if template_updates:
        template = path.with_suffix('.dotx')
        create_formatted_docx(template)
        for package in (path, template):
            with ZipFile(package) as archive:
                parts = {name: archive.read(name) for name in archive.namelist()}
            if package == template:
                parts['[Content_Types].xml'] = parts['[Content_Types].xml'].replace(
                    b'wordprocessingml.document.main+xml', b'wordprocessingml.template.main+xml')
                parts['word/styles.xml'] = parts['word/styles.xml'].replace(b'w:val="56"', b'w:val="18"')
            else:
                parts['[Content_Types].xml'] = parts['[Content_Types].xml'].replace(b'</Types>',
                    b'<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/></Types>')
                parts['word/_rels/document.xml.rels'] = parts['word/_rels/document.xml.rels'].replace(
                    b'</Relationships>', b'<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/></Relationships>')
                parts['word/settings.xml'] = (f'<w:settings xmlns:w="{ns}" '
                    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                    '<w:attachedTemplate r:id="rId1"/><w:updateStyles/></w:settings>').encode()
                parts['word/_rels/settings.xml.rels'] = (
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate" '
                    f'Target="{escape(template.as_uri())}" TargetMode="External"/></Relationships>').encode()
            with ZipFile(package, 'w') as archive:
                for name, data in parts.items():
                    archive.writestr(name, data)


@pytest.mark.skipif(os.environ.get('ZHIXU_WORD_FIDELITY_OFFICE') != '1',
                    reason='Requires explicit Office sample validation')
@pytest.mark.parametrize('template_updates', [False, True])
def test_native_word_export_preserves_headings_and_mixed_run_formatting(tmp_path, template_updates):
    assert word_available(), 'Word must be installed for the opt-in rendering check'
    source, target = tmp_path / 'formatted.docx', tmp_path / 'formatted.pdf'
    create_formatted_docx(source, template_updates=template_updates)
    original = source.read_bytes()
    results, cancelled, version = convert_batch([(str(source), str(target))], False,
        lambda *args: print(args[-1], flush=True), lambda: False)
    assert results[0]['state'] == 'success', results
    assert not cancelled and source.read_bytes() == original
    print('Office version:', version, 'Artifacts:', tmp_path, flush=True)
    with pymupdf.open(target) as pdf:
        spans = [span for page in pdf for block in page.get_text('dict')['blocks'] if 'lines' in block
                 for line in block['lines'] for span in line['spans']]
        for span in spans:
            print({key: span[key] for key in ('text', 'font', 'size', 'flags')}, flush=True)
        for text, font, size, bold in (('Large title', 'ArialMT', 28, False), ('First heading', 'Arial-BoldMT', 20, True),
                ('Second heading', 'TimesNewRomanPSMT', 16, False), ('Regular', 'ArialMT', 11, False), ('Bold', 'Arial-BoldMT', 11, True),
                ('PlainAgain', 'ArialMT', 11, False), ('Serif', 'TimesNewRomanPSMT', 15, False), ('SerifBold', 'TimesNewRomanPS-BoldMT', 15, True),
                ('CalibriText', 'Calibri', 13, False), ('中文宋体', 'SimSun', 12, False), ('中文黑体', 'SimHei', 16, False)):
            span = next(span for span in spans if text in span['text'])
            assert span['font'] == font, (text, span)
            assert span['size'] == pytest.approx(size, abs=.1), (text, span)
            assert bool(span['flags'] & 16) == bold, (text, span)
        stroked = ''.join(chr(char[0]) for page in pdf for trace in page.get_texttrace()
                          if trace['type'] == 1 and trace['linewidth'] > 0 for char in trace['chars'])
        assert '中文加粗' in ''.join(stroked.split())
        for page in pdf:
            page.get_pixmap(dpi=110).save(tmp_path / f'formatted-{page.number + 1}.png')


@pytest.fixture
def native_word_document():
    """Own one isolated Word process; never attach to the user's documents."""
    import pythoncom
    import win32api
    import win32con
    import win32job
    import win32process
    from win32com.client import DispatchEx

    pythoncom.CoInitialize()
    word = document = job = None
    owned = False
    try:
        before = set(win32process.EnumProcesses())
        word = DispatchEx('Word.Application')
        document = word.Documents.Add(Visible=False)
        pid = win32process.GetWindowThreadProcessId(int(document.Windows.Item(1).Hwnd))[1]
        assert pid not in before, 'The test must own a new Word process'
        owned = True
        job = win32job.CreateJobObject(None, 'ZhiXu-WordFormatTest-' + uuid.uuid4().hex)
        info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        info['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
        handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, pid)
        try:
            win32job.AssignProcessToJobObject(job, handle)
        finally:
            handle.Close()
        word.Visible = False
        word.DisplayAlerts = 0
        word.AutomationSecurity = 3
        yield word, document
    finally:
        if document is not None and owned:
            try:
                document.Close(SaveChanges=False)
            except pythoncom.com_error:
                pass
        if word is not None and owned:
            try:
                word.Quit(SaveChanges=False)
            except pythoncom.com_error:
                pass
        document = word = None
        if job:
            job.Close()
        pythoncom.CoUninitialize()


@pytest.mark.skipif(os.environ.get('ZHIXU_WORD_FIDELITY_OFFICE') != '1',
                    reason='Requires explicit Office sample validation')
@pytest.mark.parametrize('suffix, file_format', [('.docx', 16), ('.doc', 0)])
@pytest.mark.parametrize('track_changes', [False, True])
def test_saved_word_style_and_bold_edit_history(tmp_path, native_word_document, suffix, file_format, track_changes):
    _, document = native_word_document
    source = tmp_path / ('saved' + suffix)
    samples = [('宋体标记', '宋体', 'Arial', 12), ('黑体标记', '黑体', 'Arial', 14),
               ('仿宋标记', '仿宋', 'Times New Roman', 13), ('楷体标记', '楷体', 'Calibri', 15),
               ('微软雅黑标记', '微软雅黑', 'Arial', 12), ('等线标记', '等线', 'Calibri', 11),
               ('RegularEnglish', '宋体', 'Arial', 11), ('BoldEnglish', '微软雅黑', 'Times New Roman', 16),
               ('ResetEnglish', '仿宋', 'Calibri', 14), ('AlternateEnglish', '等线', 'Arial', 18),
               # These also compare substitution with Word's own output if it cannot use Noto.
               ('思源字体标记', 'Noto Sans SC', 'Noto Sans SC', 15),
               ('思源中等标记', 'Noto Sans SC Medium', 'Noto Sans SC Medium', 14)]
    document.Content.Text = ('大标题 Title\r第一章 Heading1\r1.1 小标题 Heading2\r'
                             + ' '.join(sample[0] for sample in samples) + '\r')
    for paragraph, style in ((1, -63), (2, -2), (3, -3)):
        document.Paragraphs.Item(paragraph).Range.Style = style
    document.SaveAs2(FileName=str(source), FileFormat=file_format, AddToRecentFiles=False)
    document.TrackRevisions = track_changes
    for stage in range(3):
        for style, east_asian, latin, size in ((-63, '微软雅黑', 'Calibri', 28),
                (-2, '黑体', 'Arial', 22), (-3, '宋体', 'Times New Roman', 18)):
            font = document.Styles.Item(style).Font
            font.Name = latin
            font.NameFarEast = east_asian
            font.Size = size + stage * 2
            font.Bold = stage % 2 == 0
        font = None
        for index, (text, east_asian, latin, size) in enumerate(samples):
            selected = document.Content.Duplicate
            assert selected.Find.Execute(FindText=text)
            selected.Font.Name = latin
            selected.Font.NameFarEast = east_asian
            selected.Font.Size = size + stage
            for toggle in range(6 + stage):
                selected.Font.Bold = toggle % 2 == 0
            selected.Font.Bold = (index + stage) % 2 == 0
            selected = None
        document.Repaginate()
        document.Save()
        assert document.Saved, 'Formatting must be saved before conversion'
        original = source.read_bytes()
        reference = tmp_path / f'reference-{stage}.pdf'
        output = tmp_path / f'converted-{stage}.pdf'
        document.ExportAsFixedFormat(OutputFileName=str(reference), ExportFormat=17, OpenAfterExport=False,
            OptimizeFor=0, Range=0, Item=0, IncludeDocProps=True, KeepIRM=True, CreateBookmarks=1,
            DocStructureTags=True, BitmapMissingFonts=True, UseISO19005_1=False)
        results, cancelled, _ = convert_batch([(str(source), str(output))], False,
            lambda *args: print(args[-1], flush=True), lambda: False)
        assert results[0]['state'] == 'success', results
        assert not cancelled and source.read_bytes() == original
        with pymupdf.open(reference) as before, pymupdf.open(output) as after:
            assert len(before) == len(after)
            for index, (expected, actual) in enumerate(zip(before, after)):
                expected.get_pixmap(dpi=110).save(tmp_path / f'reference-{stage}-{index + 1}.png')
                actual.get_pixmap(dpi=110).save(tmp_path / f'converted-{stage}-{index + 1}.png')
                assert expected.get_text('dict') == actual.get_text('dict'), (stage, index, tmp_path)
                assert expected.get_pixmap().digest == actual.get_pixmap().digest, (stage, index, tmp_path)
        print('Saved format stage matched:', stage, suffix, 'tracked:', track_changes, flush=True)


@pytest.mark.skipif(os.environ.get('ZHIXU_WORD_FIDELITY_OFFICE') != '1',
                    reason='Requires explicit Office sample validation')
def test_saved_word_reconversion_refreshes_the_existing_pdf_tab(tmp_path, monkeypatch):
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication
    import app.ui as ui

    monkeypatch.setattr(ui, 'QSettings', lambda *args:
        QSettings(str(tmp_path / 'reader.ini'), QSettings.Format.IniFormat))
    qapp = QApplication.instance() or QApplication([])
    source, output = tmp_path / 'saved.docx', tmp_path / 'converted.pdf'
    create_formatted_docx(source)
    report = lambda *args: print(args[-1], flush=True)
    results, cancelled, _ = convert_batch([(str(source), str(output))], False, report, lambda: False)
    assert not cancelled and results[0]['state'] == 'success', results
    window = ui.MainWindow()
    window.show()
    try:
        assert window.open_files_in_tabs([str(output)])
        tab_id = window._active_tab_id
        with ZipFile(source) as archive:
            parts = {name: archive.read(name) for name in archive.namelist()}
        parts['word/styles.xml'] = parts['word/styles.xml'].replace(
            b'<w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:sz w:val="56"/>',
            b'<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/><w:b/><w:sz w:val="72"/>')
        with ZipFile(source, 'w') as archive:
            for name, data in parts.items():
                archive.writestr(name, data)
        original = source.read_bytes()
        results, cancelled, _ = convert_batch([(str(source), str(output))], True, report, lambda: False)
        assert not cancelled and results[0]['state'] == 'success', results
        assert source.read_bytes() == original
        assert window.open_files_in_tabs([str(output)])
        qapp.processEvents()
        assert len(window._tabs) == 1 and window._active_tab_id == tab_id
        with window.store._render_page(window.ws.pages[0]) as page, pymupdf.open(output) as pdf:
            title = page.get_text('dict')['blocks'][0]['lines'][0]['spans'][0]
            assert title['font'] == 'TimesNewRomanPS-BoldMT'
            assert title['size'] == pytest.approx(36, abs=.1)
            assert title['flags'] & 16
            assert page.get_pixmap().digest == pdf[0].get_pixmap().digest
            page.get_pixmap(dpi=110).save(tmp_path / 'reader-latest-format.png')
    finally:
        for tab in window._tabs:
            tab.ws.reset()
        window.close()
        qapp.processEvents()
