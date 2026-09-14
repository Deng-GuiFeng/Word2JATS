"""验证文档导航是实际链接，字体及正文颜色可随 Word/PDF 导出。"""
import zipfile

from lxml import etree

from scripts import render_finals_documents as render


def test_document_links_and_typography(tmp_path, monkeypatch):
    source = tmp_path / 'proposal.md'
    source.write_text('# 方案\n\n在线原型：[打开](https://example.org)\n\n'
                      '## 一、架构\n\n参见表1。\n\n表1　数据\n\n'
                      '| 配置 | 结果 |\n|---|---|\n| A | 1 |\n\n'
                      '## 二、结果\n\n文字。\n', encoding='utf-8')
    monkeypatch.setattr(render, 'OUT', tmp_path)
    render.document(source)
    ns = {'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
          'r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
    with zipfile.ZipFile(tmp_path / '技术方案说明书.docx') as archive:
        body = etree.fromstring(archive.read('word/document.xml'))
        footer = etree.fromstring(archive.read('word/footer1.xml'))
        styles = etree.fromstring(archive.read('word/styles.xml'))
        relations = archive.read('word/_rels/document.xml.rels')
    bookmarks = body.xpath('//w:bookmarkStart/@w:name', namespaces=ns)
    anchors = body.xpath('//w:hyperlink/@w:anchor', namespaces=ns) + footer.xpath('//w:hyperlink/@w:anchor', namespaces=ns)
    assert len(bookmarks) == len(set(bookmarks))
    assert set(anchors) <= set(bookmarks)
    assert 'table_1' in anchors and 'contents' in bookmarks
    assert len(body.xpath('//w:hyperlink[@r:id]', namespaces=ns)) == 1
    assert b'Target="https://example.org"' in relations
    normal = styles.xpath('//w:style[@w:styleId="Normal"]/w:rPr', namespaces=ns)[0]
    assert normal.xpath('w:rFonts/@w:ascii', namespaces=ns) == ['Times New Roman']
    assert normal.xpath('w:rFonts/@w:eastAsia', namespaces=ns) == ['Noto Serif CJK SC']
    assert normal.xpath('w:color/@w:val', namespaces=ns) == ['000000']
