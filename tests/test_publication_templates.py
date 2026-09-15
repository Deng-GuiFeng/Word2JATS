from dataclasses import asdict
import pytest

from word2jats.semantic.templates import is_unfilled_publication_history
from word2jats.model.source import SourceDocument,SourceNode,SourcePart
from word2jats.understand.assemble import _Assembler,SemanticSourceUse
from word2jats.understand.merge import Assignment,DocumentAssignment,MergeIssue
from word2jats.verify.audit import audit_source_coverage


@pytest.mark.parametrize('text', ['(Received ………………………………….)',
    '(Received March --, 2025; Accepted July --, 2026)',' (Submitted: ____; Revised: ……) '])
def test_only_empty_date_templates_match(text):
    assert is_unfilled_publication_history(text)


@pytest.mark.parametrize('text',['(Received March 12, 2025)','Received a grant',
    '(Received ……) additional research content','(Received … and approved)',
    '(Received March --, 2025; Accepted July 12, 2026)','(Received 2025)',
    'The sentence mentions (Received ……).'])
def test_actual_dates_and_article_content_are_not_empty_templates(text):
    assert not is_unfilled_publication_history(text)


@pytest.mark.parametrize('order,role,handled',[(0,None,True),(0,'front',False),(2,None,False),(0,'body-paragraph',False)])
def test_template_handling_requires_unclaimed_front_position(order,role,handled):
    text='(Received ………………………………….)'
    source=SourceDocument(nodes=[SourceNode('date','document','para',None,order,text),
        SourceNode('body','document','para',None,1,'Introduction')])
    assignments=[Assignment('node','body','section-title',())]
    if role:assignments.append(Assignment('node','date',role,()))
    assignment=DocumentAssignment(tuple(assignments),(),(MergeIssue('high','VISIBLE_NODE_UNCLAIMED','date','unclaimed'),))
    assembler=_Assembler(source,None,{}, {},(),[],assignment)
    assembler._publication_templates()
    assert bool(assembler.source_uses)==handled
    assert bool(assembler.issues)!=handled


@pytest.mark.parametrize('text,valid',[('(Received ………………………………….)',True),
    ('(Received March 12, 2025)',False),('(Received ……) Real content.',False)])
def test_independent_audit_rejects_nonempty_template_claim(text,valid):
    source=SourceDocument(parts=[SourcePart('document','document','/word/document.xml',node_ids=('date',))],
                          nodes=[SourceNode('date','document','para',None,0,text)])
    use=SemanticSourceUse('date',0,len(text),'test','unfilled-publication-template')
    result=audit_source_coverage(source,(),explicit_uses=[asdict(use)])
    assert result.ok==valid
    if not valid:assert any(i.code=='PUBLICATION_TEMPLATE_HAS_CONTENT' for i in result.issues)
