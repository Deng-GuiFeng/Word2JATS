"""关键字段公网验收器的独立判定与输入计划自测。"""
from copy import deepcopy

import pytest

from scripts.web_public_metadata import article_plan, assert_fields, field_value, xml_fields
from webapp import editor


XML = '''<article><front><journal-meta><journal-id>J</journal-id>
<journal-title-group><journal-title>期刊</journal-title></journal-title-group>
<issn pub-type="epub">2049-3630</issn><publisher><publisher-name>出版方</publisher-name></publisher>
</journal-meta><article-meta><article-id pub-id-type="doi">10.9999/test</article-id>
<title-group><article-title>带<italic>格式</italic>题名</article-title></title-group>
<contrib-group><contrib contrib-type="author"><name><surname>张</surname><given-names>甲</given-names></name>
<xref ref-type="aff" rid="a1 a2">1,2</xref><xref ref-type="corresp" rid="c1">*</xref></contrib>
<contrib contrib-type="author"><collab>协作组</collab></contrib></contrib-group>
<aff id="a1"><label>1</label>单位<institution>甲</institution></aff><aff id="a2"><label>2</label>单位乙</aff>
<author-notes><corresp id="c1"><label>*</label>联系地址<email>before@example.org</email>尾注</corresp></author-notes>
</article-meta></front><body><p>不改正文</p></body><back/></article>'''


def test_independent_xml_reader_preserves_metadata_semantics():
    actual = xml_fields(XML)
    assert actual['title'] == '带格式题名'
    assert actual['authors'][0]['affiliations'] == ['a1','a2']
    assert actual['authors'][0]['corresponding'] is True
    assert actual['authors'][1]['name'] == '协作组'
    assert actual['affiliations'][0]['text'] == '单位甲'
    assert actual['contacts'][0] == {'text':'联系地址尾注', 'emails':[{'value':'before@example.org'}]}
    assert_fields(actual, editor.extract(XML.encode()))


def test_article_plan_changes_every_exposed_field_without_changing_baseline():
    source = editor.extract(XML.encode())
    frozen = deepcopy(source)
    plan = article_plan(source)
    assert source == frozen
    assert plan['title'] != source['title']
    for old, new in zip(source['authors'], plan['authors']):
        for field in ('surname','given_names') if old['kind'] == 'name' else ('name',):
            assert new[field] != old[field]
        assert editor._orcid(new['orcid'])
        assert new['corresponding'] is not old['corresponding']
        assert set(new['affiliations']) == {'a1','a2'} - set(old['affiliations'])
    for old, new in zip(source['affiliations'], plan['affiliations']):
        assert new['text'] != old['text'] and new['key'] == old['key']
    assert plan['contacts'][0]['text'] != source['contacts'][0]['text']
    assert editor._email(field_value(plan, 'contacts.0.emails.0.value'))
    plan['authors'][0]['orcid'] = '0000-0002-1825-0097'
    assert editor._orcid(article_plan(plan)['authors'][0]['orcid'])


@pytest.mark.parametrize('path', ['title','authors.0.surname','authors.0.orcid',
    'authors.0.corresponding','authors.0.affiliations','affiliations.0.text',
    'contacts.0.emails.0.value','publication.publisher'])
def test_comparison_rejects_saved_field_loss(path):
    expected = xml_fields(XML)
    actual = deepcopy(expected)
    keys = path.split('.')
    node = actual
    for key in keys[:-1]:
        node = node[int(key)] if isinstance(node,list) else node[key]
    old = node[keys[-1]]
    node[keys[-1]] = not old if isinstance(old,bool) else ['other'] if isinstance(old,list) else old+'丢失'
    with pytest.raises(AssertionError, match=path.replace('.', r'\.')):
        assert_fields(actual, expected)


def test_comparison_ignores_xpath_renumbering_but_not_author_order():
    fields = editor.extract(XML.encode())
    renumbered = deepcopy(fields)
    renumbered['authors'][0]['key'] = '/different-position'
    assert_fields(renumbered, fields)
    renumbered['authors'].reverse()
    with pytest.raises(AssertionError):
        assert_fields(renumbered, fields)


def test_absent_metadata_is_not_invented_and_publication_values_are_valid():
    fields = {'title':'题名', 'authors':[], 'affiliations':[], 'contacts':[], 'publication':{}}
    actual = article_plan(fields)
    assert actual['authors'] == actual['affiliations'] == actual['contacts'] == []
    assert editor._issn('1530-6550') and editor._issn('2049-3630')
