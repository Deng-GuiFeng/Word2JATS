"""混合著录的显式文献编号也能为多目标引用提供准确的预览标签。"""
import pytest
from lxml import etree
from webapp.render import _expand_multi_xrefs


@pytest.mark.parametrize('prefix',['[19]','19. ','19) '])
def test_mixed_reference_printed_number_can_label_multilink(prefix):
    root=etree.fromstring(f'<article><body><p>[<xref ref-type="bibr" rid="rA rB">19-20</xref>]</p></body><back><ref-list><ref id="rA"><mixed-citation>{prefix}Praz F. Guidelines.</mixed-citation></ref><ref id="rB"><label>[20]</label></ref></ref-list></back></article>')
    before=etree.tostring(root.find('back'))
    _expand_multi_xrefs(root)
    links=root.findall('.//xref')
    assert [x.get('rid') for x in links]==['rA','rB']
    assert [x.text for x in links]==['19','20']
    assert ''.join(root.find('body').itertext())=='[19, 20]'
    assert etree.tostring(root.find('back'))==before


@pytest.mark.parametrize('citation',['2025. Guidelines.','Author 19. Guidelines.','19.5 is a measurement.','Unnumbered reference.'])
def test_unlabeled_or_ambiguous_reference_not_numbered_from_id(citation):
    root=etree.fromstring(f'<article><body><p><xref ref-type="bibr" rid="b19 b20">19-20</xref></p></body><back><ref-list><ref id="b19"><mixed-citation>{citation}</mixed-citation></ref><ref id="b20"><label>[20]</label></ref></ref-list></back></article>')
    _expand_multi_xrefs(root)
    assert root.find('.//xref').get('rid')=='b19 b20'
