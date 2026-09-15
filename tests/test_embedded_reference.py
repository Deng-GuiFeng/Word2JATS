"""字面 XML 文献必须转为著录内容，拒绝残缺或含未支持正文的片段。"""
import pytest
from dataclasses import asdict,replace
from lxml import etree

from word2jats.model.source import SourceDocument, SourceNode, SourcePart, SourceText
from word2jats.semantic import model as sm
from word2jats.understand.assemble import _Assembler
from word2jats.understand.embedded_reference import embedded_reference
from word2jats.understand.merge import Assignment, DocumentAssignment, ReferenceSpan, MergeIssue
from word2jats.render.v2 import render_v2
from word2jats.verify.audit import audit_provenance, audit_source_coverage


XML='''<ref id=“b1”><label>[1]</label><element-citation publication-type=“journal”><person-group person-group-type=“author”><name><surname>Smith</surname><given-names>J</given-names></name><etal>et al</etal></person-group><article-title>Parkinson&apos;s &amp; gut</article-title><source>Journal</source><year>2025</year><volume>12</volume><fpage>31</fpage><lpage>40</lpage><ext-link ext-link-type=“uri” xlink:href=“https://doi.org/10.1000/test”>https://doi.org/10.1000/test</ext-link></element-citation></ref>'''


def fixture(xml=XML, *, split=False):
    texts=xml.replace('><','>\n<').splitlines() if split else [xml]
    nodes=[SourceNode(f'p{i}','document','para',None,i,text) for i,text in enumerate(texts)]
    source=SourceDocument(parts=[SourcePart('document','document','/word/document.xml',node_ids=tuple(n.node_id for n in nodes))],nodes=nodes)
    spans=tuple((n.node_id,0,len(n.text)) for n in nodes)
    head=next(((n.node_id,n.text.index('[1]'),n.text.index('[1]')+3) for n in nodes if '[1]' in n.text),spans[0])
    return source,ReferenceSpan(1,head,SourceText(spans))


@pytest.mark.parametrize('split',[True,False])
def test_word_embedded_xml_becomes_structured_reference_with_exact_provenance(split):
    source,span=fixture(split=split)
    assignment=DocumentAssignment(tuple(Assignment('node',n.node_id,'reference-entry',()) for n in source.nodes),(span,),())
    assembler=_Assembler(source,None,{}, {},(span,),[],assignment)
    references=assembler._references()
    ref=references.references[0]
    assert isinstance(ref.citation,sm.StructuredCitation)
    assert ref.citation.person_groups[0].persons[0].surname.text(source)=='Smith'
    assert ref.citation.article_title.plain_text(source)=="Parkinson's & gut"
    document=sm.SemanticDoc(source,reference_list=references)
    result=render_v2(document)
    root=etree.fromstring(result.xml_bytes)
    assert '<ref' not in ''.join(root.itertext())
    assert root.findtext('.//article-title')=="Parkinson's & gut"
    assert audit_provenance(result.xml_bytes,result.provenance,source).ok
    coverage=audit_source_coverage(source,result.provenance,
        assignments=[asdict(a) for a in assignment.assignments],
        explicit_uses=[asdict(a) for a in assembler.source_uses])
    assert not coverage.issues
    changed=[replace(p,value='fabricated') if p.transform=='xml-entity-decode' else p for p in result.provenance]
    assert any(i.code=='TRANSFORM_VALUE_INVALID' for i in audit_provenance(result.xml_bytes,changed,source).issues)


@pytest.mark.parametrize('replacement',[
    ('</ref>',''),('<year>2025</year>','<year>2025<source>extra</source></year>'),
    ('<source>Journal</source>','<unknown>Journal</unknown>'),
    ('<person-group','extra<person-group'),('<ref id=“b1”>','<!DOCTYPE ref><ref id=“b1”>'),
    ('<surname>Smith</surname>','<surname>Smith</surname><surname>Another</surname>'),
])
def test_incomplete_or_unexpressed_content_is_not_silently_removed(replacement):
    source,span=fixture(XML.replace(*replacement))
    assert embedded_reference(source,span) is None


def test_plain_reference_is_not_reinterpreted_as_xml():
    source,span=fixture('[1] Smith J. A < B in an ordinary title. Journal. 2025.')
    assert embedded_reference(source,span) is None


def test_source_authored_xml_can_be_located_when_model_head_starts_at_label():
    source,span=fixture(split=True)
    shortened=ReferenceSpan(1,span.head,SourceText(tuple(r for r in span.source.ranges if r[0]!='p0')))
    value=embedded_reference(source,shortened)
    assert value is not None and any(r[0]=='p0' for r in value.markup)


def test_reference_markup_provenance_cannot_hide_ordinary_text():
    source,span=fixture('ordinary content')
    report=audit_source_coverage(source,(),explicit_uses=[{
        'source_id':'p0','start':0,'end':16,'usage_id':'fake','role':'reference-xml-markup'}])
    assert any(i.code=='REFERENCE_MARKUP_HAS_CONTENT' for i in report.issues)


def test_inline_markup_inside_title_keeps_text_order_and_style():
    source,span=fixture(XML.replace('Parkinson&apos;s &amp; gut','Before <italic>Clostridium</italic> after'))
    value=embedded_reference(source,span)
    assert value.citation.article_title.plain_text(source)=='Before Clostridium after'
    assert isinstance(value.citation.article_title.parts[1],sm.Styled)
    assert value.citation.article_title.parts[1].style=='italic'


def test_only_resolved_markup_can_clear_an_unclaimed_node_issue():
    source,span=fixture(split=True)
    issues=(MergeIssue('review_blocking','VISIBLE_NODE_UNCLAIMED','p0','ref shell'),
            MergeIssue('review_blocking','VISIBLE_NODE_UNCLAIMED','body','real paragraph'))
    assembler=_Assembler(source,None,{}, {},(span,),[],DocumentAssignment((),(),issues))
    assembler._references()
    assert [i.source_id for i in assembler.issues]==['body']
