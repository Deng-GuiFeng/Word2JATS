"""仅对实际编号相符且方括号位置唯一的数字引文补足链接。"""
import pytest

from word2jats.model.source import SourceDocument, SourceNode, SourcePart, SourceText
from word2jats.semantic import model as sm
from word2jats.understand.serialize import serialize
from word2jats.understand.xrefs import _numeric_citation_range


def locate(body,quote,labels,targets=None,contexts=None):
    nodes=[SourceNode('p','document','para',None,0,body)]
    refs=[]
    for index,label in enumerate(labels,1):
        key='r'+str(index);nodes.append(SourceNode(key,'document','para',None,index,label))
        refs.append(sm.Reference(key,sm.RichText.from_source(SourceText(((key,0,len(label)),))),
                                 sm.MixedCitation(None,sm.RichText())))
    source=SourceDocument(parts=[SourcePart('document','document','/word/document.xml',node_ids=tuple(n.node_id for n in nodes))],nodes=nodes)
    raw={'citation_quote':{'record_key':'p','quote':quote,'left_context':'incorrect','right_context':'wrong'},
         'target_reference_ids':targets or [r.entity_id for r in refs]}
    if contexts:raw['citation_quote'].update(contexts)
    found=_numeric_citation_range(raw,serialize(source),refs,source)
    return source.slice_text(found) if found else None


@pytest.mark.parametrize('body,quote,labels,expected',[
    ('The 5 values are reported [5].','5',['5'],'5'),
    ('The BRCA1/2 mechanism is described [2].','2',['[2]'],'2'),
    ('See [1–3]; tests 1, 2, 3.','1-3',['1','2','3'],'1–3'),
    ('See [19,34,108]; 19 mg.','19',['19'],'19'),
    ('See [19,34,108]; 34 mg.','34',['34'],'34'),
    ('See [5] and [5].','5',['5'],None),
    ('The 5 values are measured.','5',['5'],None),
    ('See [15].','5',['5'],None),
    ('See [18F].','18',['18'],None),
    ('See [1,3].','1,3',['1','2'],None),
    ('See [1-100].','1-100',['1','2'],None),
    ('See [1].','1',['Smith'],None),
    ('See Smith (2020).','Smith (2020)',['1'],None),
    ('See [3-1].','3-1',['3','2','1'],None),
])
def test_numeric_fallback_is_bounded(body,quote,labels,expected):
    assert locate(body,quote,labels)==expected


def test_printed_number_not_entity_position_determines_compatibility():
    assert locate('5 values [5].','5',['5'])=='5'
    assert locate('5 values [5].','5',['6']) is None
    assert locate('5 values [5].','5',['5'],['unknown']) is None


@pytest.mark.parametrize('contexts,expected',[
    ({'left_context':'wrong [','right_context':'] adopted'},'5'),
    ({'left_context':'Earlier [','right_context':'] wrong'},'5'),
    ({'left_context':'Earlier [','right_context':'] adopted'},None),
    ({'left_context':'[','right_context':']'},None),
])
def test_repeated_number_requires_consistent_unique_literal_context(contexts,expected):
    assert locate('Earlier [5] reported it; later [5] adopted it.','5',['5'],contexts=contexts)==expected


def test_repeated_citation_can_use_other_independently_located_occurrence():
    from word2jats.understand.xrefs import link_bibliographic_citations
    text='Park [39] adopted a model. Park [39] adopted another model.'
    nodes=[SourceNode('p','document','para',None,0,text),SourceNode('r','document','para',None,1,'39')]
    source=SourceDocument(parts=[SourcePart('document','document','/word/document.xml',node_ids=('p','r'))],nodes=nodes)
    reference=sm.Reference('ref',sm.RichText.from_source(SourceText((('r',0,2),))),sm.MixedCitation(None,sm.RichText()))
    paragraph=sm.Paragraph(None,sm.RichText.from_source(SourceText((('p',0,len(text)),))))
    def citation(right):
        return {'citation_quote':{'record_key':'p','quote':'39','left_context':'Park [','right_context':right},'target_reference_ids':['ref']}
    linked,issues=link_bibliographic_citations((paragraph,),sm.ReferenceList(None,(reference,)),(),source,
                                             [citation('] adopted'),citation('] adopted another')])
    assert not issues
    assert len([p for p in linked[0].content.parts if isinstance(p,sm.CrossReference)])==2
    assert linked[0].content.plain_text(source)==text
