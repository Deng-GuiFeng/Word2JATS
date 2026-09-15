"""补齐期刊并通过校验后，不把已解决的格式问题误报为正文问题。"""
import pytest
from webapp.presentation import issues

XML=b'''<article><front><journal-meta><journal-id>JIN</journal-id>
<journal-title-group><journal-title>Journal</journal-title></journal-title-group>
<issn pub-type="epub">1758-6987</issn></journal-meta><article-meta>
<title-group><article-title>Title</article-title></title-group></article-meta></front><body><p>Text</p></body></article>'''


def result(gates, edited=True):
    return {'delivered':False, 'edited':edited, 'validation':{'dtd_valid':True, 'ok':True},
            'stats':{'verify':{'gates':gates}}}


def test_only_original_format_failure_no_longer_becomes_content_warning():
    gates=dict.fromkeys(['understanding','supported_ooxml','well_formed','dtd','id_unique',
                         'rid_closed','media_bytes','media_format','no_redundant_files',
                         'source_coverage','output_provenance'],True)
    gates['dtd']=False
    data=result(gates)
    assert issues(data,XML,{},[])==[]
    assert data['delivered'] is False  # 原始自动转换结论仍保留，不伪改历史结果。


@pytest.mark.parametrize('gates,edited',[
    ({},True),
    ({'dtd':False,'source_coverage':False},True),
    ({'dtd':False,'source_coverage':None},True),
    ({'dtd':True,'source_coverage':True},True),
    ({'dtd':False,'source_coverage':True},False),
    ({'dtd':False,'source_coverage':True},True),
])
def test_unresolved_or_unknown_content_state_keeps_notice(gates,edited):
    assert issues(result(gates,edited),XML,{},[])[0]['category']=='content'
