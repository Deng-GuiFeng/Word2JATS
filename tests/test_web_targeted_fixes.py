"""公网已确认问题的定向回归；使用真实产物与隔离 API，不调用模型。"""
from lxml import etree, html
import pytest

from tests.test_web_editor import api, frozen, KEYS, xml  # noqa: F401
from webapp import editor
from webapp.render import render_html, _expand_multi_xrefs
from word2jats.parse.docx_reader import _logical_textboxes
from word2jats.parse.ooxml import NS


@pytest.mark.parametrize('path,value,fragment', [
    ('title', '', '不能为空'),
    ('title', 'x'*10001, '10000'),
    ('authors.1.orcid', 'invalid', '作者 2'),
    ('authors.0.surname', 'x'*1001, '1000'),
    ('publication.doi', 'bad', 'DOI'),
    ('publication.issn_print', '2049-3631', '印刷版'),
    ('publication.publisher', 'x'*1001, '1000'),
    ('affiliations.0.text', 'x'*10001, '单位 1'),
])
def test_validation_identifies_field_without_changing_result(api, path, value, fragment):
    client, tid, _ = api
    before = client.get('/api/result/'+tid).json()
    work = client.get('/api/workbench/'+tid).json()
    target = work['fields']
    keys = [int(k) if k.isdigit() else k for k in path.split('.')]
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value
    response = client.post('/api/edit/'+tid, json={'version':work['version'], 'fields':work['fields']})
    assert response.status_code == 400
    assert response.json()['field'] == path
    assert fragment in response.json()['detail']
    after = client.get('/api/result/'+tid).json()
    assert after['version'] == before['version']
    assert after['stats']['llm'] == before['stats']['llm']


@pytest.mark.parametrize('provider', ['deepseek', 'qwen'])
@pytest.mark.parametrize('sample', KEYS)
def test_real_preview_multi_target_links_have_individual_targets(sample, provider):
    path = frozen(sample, provider).candidate_xml
    if path is None or not path.exists():
        pytest.skip('分发包不包含实验产物')
    original = path.read_bytes()
    doc = html.fromstring(render_html(original, 'test'))
    assert doc.xpath('//body[@data-w2j-preview="true"]')
    assert not doc.xpath('//a[starts-with(@href,"#") and contains(@href," ")]')
    assert path.read_bytes() == original


def test_multi_reference_retains_single_missing_targets_and_input():
    xml = b'<article><body><p>A<xref id="x1" ref-type="bibr" rid="b1 b2">[1-2]</xref>tail<xref rid="missing b1">unknown</xref><xref rid="b1">single</xref></p></body><back><ref-list><ref id="b1"><label>[1]</label></ref><ref id="b2"><label>[2]</label></ref></ref-list></back></article>'
    root = etree.fromstring(xml)
    _expand_multi_xrefs(root)
    refs = root.findall('.//xref')
    assert [r.get('rid') for r in refs] == ['b1','b2','missing b1','b1']
    assert refs[0].get('id') == 'x1' and refs[1].get('id') is None
    assert refs[0].text == '[1' and refs[1].text == '2]'
    assert refs[1].tail == 'tail'
    assert refs[2].text == 'unknown' and refs[3].text == 'single'


@pytest.mark.parametrize('bracketed_labels', [True, False])
@pytest.mark.parametrize('before,citation,after,expected', [
    ('[', '1–2', ']', '[1, 2]'),
    ('', '[1–2]', '', '[1, 2]'),
    ('', '1–2', '', '1, 2'),
])
def test_multi_reference_uses_original_bracket_position(bracketed_labels, before, citation, after, expected):
    labels = ['[1]', '[2]'] if bracketed_labels else ['1', '2']
    root = etree.fromstring(f'<article><body><p>{before}<xref ref-type="bibr" rid="b1 b2">{citation}</xref>{after}</p></body><back><ref-list><ref id="b1"><label>{labels[0]}</label></ref><ref id="b2"><label>{labels[1]}</label></ref></ref-list></back></article>')
    _expand_multi_xrefs(root)
    assert ''.join(root.find('.//p').itertext()) == expected
    assert len(root.findall('.//xref')) == 2


def textbox(text):
    return f'<w:txbxContent><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:txbxContent>'


@pytest.mark.parametrize('requires,expected', [('wps','Choice'), ('unknown','Fallback')])
def test_textbox_compatibility_branch_is_selected_once(requires, expected):
    namespaces = ' '.join(f'xmlns:{k}="{v}"' for k,v in NS.items())
    root = etree.fromstring(f'<w:r {namespaces} xmlns:unknown="urn:unknown"><mc:AlternateContent><mc:Choice Requires="{requires}">{textbox("Choice")}</mc:Choice><mc:Fallback>{textbox("Fallback")}</mc:Fallback></mc:AlternateContent></w:r>')
    boxes = list(_logical_textboxes(root))
    assert len(boxes) == 1
    assert ''.join(boxes[0].itertext()) == expected
    assert boxes[0].getparent() is not None  # 保留原 XPath，不重建源节点。


def test_distinct_identical_textboxes_are_not_deduplicated():
    namespaces = ' '.join(f'xmlns:{k}="{v}"' for k,v in NS.items())
    root = etree.fromstring(f'<w:r {namespaces}>{textbox("Continued")}{textbox("Continued")}</w:r>')
    assert len(list(_logical_textboxes(root))) == 2


def test_nested_compatibility_branches_and_first_supported_choice():
    namespaces = ' '.join(f'xmlns:{k}="{v}"' for k,v in NS.items())
    nested = f'<mc:AlternateContent><mc:Choice Requires="wps">{textbox("one")}</mc:Choice><mc:Fallback>{textbox("duplicate")}</mc:Fallback></mc:AlternateContent>'
    root = etree.fromstring(f'<w:r {namespaces}><mc:AlternateContent><mc:Choice Requires="wps">{nested}</mc:Choice><mc:Choice Requires="w">{textbox("two")}</mc:Choice><mc:Fallback>{textbox("three")}</mc:Fallback></mc:AlternateContent></w:r>')
    assert [''.join(n.itertext()) for n in _logical_textboxes(root)] == ['one']
