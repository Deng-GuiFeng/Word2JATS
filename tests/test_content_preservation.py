"""内容组装回归：不因提取显示对象而吞掉同段文字。"""
import pytest

from word2jats.model.source import SourceDocument, SourceNode, ObjectOccurrence, ObjectAnchor, RunRef, RunSpan
from word2jats.semantic import model as sm
from word2jats.understand.assemble import _Assembler
from word2jats.understand.merge import Assignment, DocumentAssignment


def assembled(text, *, specs=True, label=None, role='body-paragraph'):
    anchors=[ObjectAnchor(i,f'o{n+1}') for n,i in enumerate(i for i,c in enumerate(text) if c=='\ufffc')]
    source=SourceDocument(nodes=[SourceNode('doc/p1','document','para',None,0,text,
        [RunSpan(0,len(text),RunRef('r1','document','/p[1]'))],objects=anchors)],
        occurrences=[ObjectOccurrence(a.occ_id,'image','doc/p1',a.pos) for a in anchors])
    assignment=DocumentAssignment(tuple([Assignment('node','doc/p1',role,())]+
        [Assignment('object',a.occ_id,'display-formula',()) for a in anchors]),(),())
    body={'formulas':[{'occurrence_id':a.occ_id,'display':True,
        'label_quote':{'quote':label,'node_hint':'doc/p1'} if label else None} for a in anchors] if specs else []}
    assembler=_Assembler(source,None,{},body,(),[],assignment)
    blocks,_=assembler._body()
    return source,blocks


@pytest.mark.parametrize('text', ['Before \ufffc after.','\ufffc after.','Before \ufffc',
    ' before \ufffc between \ufffc after ','α = \ufffc；说明文字','\ufffc',' \ufffc '])
def test_display_formula_preserves_surrounding_text_and_order(text):
    source,blocks=assembled(text)
    observed=''.join('\ufffc' if isinstance(b,sm.Formula) else b.content.plain_text(source) for b in blocks)
    assert observed.strip()==text.strip()
    assert sum(isinstance(b,sm.Formula) for b in blocks)==text.count('\ufffc')


def test_display_object_role_is_sufficient_without_redundant_formula_spec():
    source,blocks=assembled('Before \ufffc after.',specs=False)
    assert [type(b).__name__ for b in blocks]==['Paragraph','Formula','Paragraph']
    assert ''.join(b.content.plain_text(source) for b in blocks if isinstance(b,sm.Paragraph))=='Before  after.'


def test_formula_label_is_not_repeated_in_surrounding_text():
    source,blocks=assembled('Before \ufffc (1) after.',label='(1)')
    text=''.join(b.label.plain_text(source) if isinstance(b,sm.Formula) and b.label else
                 b.content.plain_text(source) if isinstance(b,sm.Paragraph) else '' for b in blocks)
    assert text.count('(1)')==1
    assert 'Before' in text and 'after.' in text


def test_multiple_objects_sharing_one_source_label_do_not_duplicate_it():
    source,blocks=assembled('\ufffc \ufffc (6)',label='(6)')
    labels=[b.label.plain_text(source) for b in blocks if isinstance(b,sm.Formula) and b.label]
    assert labels==['(6)']
    assert sum(isinstance(b,sm.Formula) for b in blocks)==2


@pytest.mark.parametrize('text',['Swelling (%) = (Ws - Wd) x 100','             Wd','P = (W2 – W1)','ρV1'])
def test_text_typeset_formula_without_binary_object_is_not_discarded(text):
    source,blocks=assembled(text,role='display-formula')
    assert ''.join(b.content.plain_text(source) for b in blocks if isinstance(b,sm.Paragraph))==text


def test_nested_display_formula_stays_in_its_cell():
    source=SourceDocument(nodes=[SourceNode('p','document','para','cell',0,'left \ufffc right',
        objects=[ObjectAnchor(5,'o1')])],occurrences=[ObjectOccurrence('o1','image','p',5)])
    assignment=DocumentAssignment((Assignment('object','o1','display-formula',()),),(),())
    assembler=_Assembler(source,None,{}, {'formulas':[{'occurrence_id':'o1','display':True}]},(),[],assignment)
    assert assembler._display_formulas()==[]
    content=assembler.rich_node('p')
    assert content.plain_text(source)=='left \ufffc right'
    assert isinstance(content.parts[1],sm.InlineFormula)
    assert not assembler.inline_formulas['o1'].display
