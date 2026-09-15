"""Word 中空白链接片段不应在预览中额外展开成网址。"""
import pytest
from lxml import etree

from webapp.render import render_html


@pytest.mark.parametrize('content', [' ', '\t', '<underline> </underline>'])
def test_whitespace_link_run_does_not_duplicate_the_following_url(content):
    xml=f'''<article xmlns:xlink="http://www.w3.org/1999/xlink"><body><p>Source:<ext-link xlink:href="https://example.org/path">{content}</ext-link><ext-link xlink:href="https://example.org/path"><underline>https://example.org/path</underline></ext-link></p></body></article>'''
    tree=etree.HTML(render_html(xml.encode(),'test'))
    links=tree.xpath('//a[@href="https://example.org/path"]')
    assert len(links)==2
    assert ''.join(links[0].itertext()).strip()==''
    assert ''.join(links[1].itertext())=='https://example.org/path'


def test_genuinely_empty_link_keeps_attribute_url_as_display_text():
    xml=b'<article xmlns:xlink="http://www.w3.org/1999/xlink"><body><p><ext-link xlink:href="https://example.org/path"/></p></body></article>'
    tree=etree.HTML(render_html(xml,'test'))
    assert tree.xpath('//a[@href="https://example.org/path"]/text()')==['https://example.org/path']
