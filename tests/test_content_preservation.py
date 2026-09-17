"""内容组装回归：不因提取显示对象而吞掉同段文字。"""
import pytest

from word2jats.model.source import SourceDocument, SourceNode, ObjectOccurrence, ObjectAnchor, RunRef, RunSpan
from word2jats.semantic import model as sm
from word2jats.understand.assemble import _Assembler
from word2jats.understand.merge import Assignment, DocumentAssignment
from word2jats.model.source import SourcePart, SourceText
from word2jats.render.v2 import V2Renderer
from word2jats.understand.merge import project_body_to_assignment, MergeIssue
from word2jats.understand.serialize import serialize


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


def test_reclassified_graphical_abstract_does_not_leave_an_empty_body_figure():
    source=SourceDocument(parts=[SourcePart('document','document','/word/document.xml',node_ids=('p',))],
                          nodes=[SourceNode('p','document','para',None,0,'\ufffc',objects=[ObjectAnchor(0,'o')])],
                          occurrences=[ObjectOccurrence('o','image','p',0)])
    assignment=DocumentAssignment((Assignment('object','o','graphical-abstract',()),),(),())
    body={'figures':[{'graphics':['o']}], 'objects':[{'occurrence_id':'o','role':'graphical-abstract'}]}
    projected=project_body_to_assignment(serialize(source),body,assignment)
    assert projected['figures']==[]
    unknown=project_body_to_assignment(serialize(source),{'figures':[{'graphics':['missing']}]},assignment)
    assert len(unknown['figures'])==1


@pytest.mark.parametrize('title,role,expected',[('Graphical Abstract\xa0',None,True),
    ('图文摘要','front',True),('Ordinary discussion',None,False),('Graphical Abstract','body-paragraph',False)])
def test_standalone_graphical_heading_is_preserved_only_next_to_confirmed_object(title,role,expected):
    source=SourceDocument(parts=[SourcePart('document','document','/word/document.xml',node_ids=('heading','p'))],
        nodes=[SourceNode('heading','document','para',None,0,title),
        SourceNode('p','document','para',None,1,'\ufffc',objects=[ObjectAnchor(0,'o')])],
        occurrences=[ObjectOccurrence('o','image','p',0)])
    assignments=[Assignment('object','o','graphical-abstract',())]
    if role:assignments.append(Assignment('node','heading',role,()))
    assignment=DocumentAssignment(assignments=tuple(assignments),references=(),audit=(),issues=(MergeIssue('high','VISIBLE_NODE_UNCLAIMED','heading','not assigned'),))
    assembler=_Assembler(source,serialize(source),{}, {'objects':[{'occurrence_id':'o','role':'graphical-abstract'}]},(),[],assignment)
    abstracts=assembler._abstracts()
    assert bool(abstracts[0].title)==expected
    if expected:
        assert abstracts[0].title.plain_text(source)==title
        assert not assembler.issues
    else:assert assembler.issues


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


def figure_in_table(*, table_spec=False, extra_data=False):
    nodes=[SourceNode('table','document','table',None,0),
           SourceNode('row','document','row','table',1)]
    occurrences=[]
    for i,text in enumerate(['\ufffcPanel A','\ufffcPanel B']+(['data'] if extra_data else [])):
        cell=f'cell{i}'; para=f'p{i}'; oid=f'o{i}'
        nodes.extend([SourceNode(cell,'document','cell','row',2+i*2),
                      SourceNode(para,'document','para',cell,3+i*2,text,
                                 objects=[ObjectAnchor(0,oid)] if i<2 else [])])
        if i<2:
            occurrences.append(ObjectOccurrence(oid,'image',para,0))
    source=SourceDocument(nodes=nodes,occurrences=occurrences)
    assignment=DocumentAssignment(tuple([Assignment('node','table','table',())]+
        [Assignment('object',o.occ_id,'figure',()) for o in occurrences]),(),())
    body={'figures':[{'graphics':['o0','o1']}],
          'tables':[{'table_node':'table','header_rows':1}] if table_spec else []}
    return source,_Assembler(source,None,{},body,(),[],assignment)


@pytest.mark.parametrize('table_spec',[True,False])
def test_table_used_for_figure_layout_keeps_both_images_and_their_own_labels(table_spec):
    source,assembler=figure_in_table(table_spec=table_spec)
    blocks,_=assembler._body()
    assert len(blocks)==1
    assert isinstance(blocks[0],sm.FigureGroup)
    assert [f.graphics for f in blocks[0].figures]==[('o0',),('o1',)]
    assert [f.caption.paragraphs[0].content.plain_text(source) for f in blocks[0].figures]==['Panel A','Panel B']


def test_table_with_unrelated_data_is_not_consumed_as_pure_figure_layout():
    source,assembler=figure_in_table(table_spec=True,extra_data=True)
    blocks,_=assembler._body()
    assert any(isinstance(b,sm.Figure) for b in blocks)
    table=next(b for b in blocks if isinstance(b,sm.TableBlock))
    assert 'data' in ''.join(c.content.plain_text(source) for r in table.header_rows+table.body_rows for c in r.cells)


def test_header_only_table_keeps_header_cells_in_valid_direct_rows():
    source=SourceDocument(parts=[SourcePart('document','document','/word/document.xml',node_ids=('p',))],
                          nodes=[SourceNode('p','document','para',None,0,'Heading')])
    text=sm.RichText.from_source(SourceText((('p',0,7),)))
    table=sm.TableBlock('table:1',None,None,(),(sm.TableRow((sm.TableCell(text,cell_type='th'),)),),())
    node=V2Renderer(sm.SemanticDoc(source,body=(table,))).table(table)
    assert node.find('table/thead') is None
    assert node.findtext('table/tr/th')=='Heading'
    assert node.find('table/tbody') is None


def test_formula_number_on_separate_source_line_is_not_duplicated():
    source=SourceDocument(nodes=[SourceNode('p','document','para',None,0,'\ufffc',objects=[ObjectAnchor(0,'o')]),
                                 SourceNode('label','document','para',None,1,'    (8)')],
                          occurrences=[ObjectOccurrence('o','image','p',0)])
    assignment=DocumentAssignment(tuple([Assignment('node',n.node_id,'display-formula',()) for n in source.nodes]+
                                       [Assignment('object','o','display-formula',())]),(),())
    assembler=_Assembler(source,None,{}, {'formulas':[{'occurrence_id':'o','display':True,
        'label_quote':{'quote':'(8)','node_hint':'label'}}]},(),[],assignment)
    blocks,_=assembler._body()
    assert len(blocks)==1 and isinstance(blocks[0],sm.Formula)
    assert blocks[0].label.plain_text(source)=='(8)'


def test_overlapping_caption_quotes_do_not_repeat_source_text():
    source=SourceDocument(nodes=[SourceNode('p','document','para',None,0,'Figure 1. Main caption. Extra detail.')])
    assembler=_Assembler(source,None,{}, {},(),[],DocumentAssignment((),(),()))
    q=lambda text:{'quote':text,'node_hint':'p'}
    spec={'caption_nodes':['p'],'label_quote':q('Figure 1.'),
          'caption_title_quote':q('Main caption.'),
          'caption_paragraph_quotes':[q('Figure 1. Main caption. Extra detail.'),q('Extra detail.')]}
    caption=assembler._caption(spec)
    text=caption.title.plain_text(source)+''.join(p.content.plain_text(source) for p in caption.paragraphs)
    assert text=='Main caption.  Extra detail.'


def test_source_underline_is_preserved_in_rendered_body():
    source=SourceDocument(parts=[SourcePart('document','document','/word/document.xml',node_ids=('p',))],
        nodes=[SourceNode('p','document','para',None,0,'numerator',
        run_spans=[RunSpan(0,9,RunRef('r','document','/p/r',underline=True))])])
    rich=sm.RichText.from_source(SourceText((('p',0,9),)))
    renderer=V2Renderer(sm.SemanticDoc(source))
    element=renderer.paragraph(sm.Paragraph(None,rich))
    assert element.findtext('underline')=='numerator'


def test_native_table_role_is_sufficient_without_redundant_table_spec():
    source,assembler=figure_in_table(table_spec=False,extra_data=True)
    assembler.body_json['figures']=[]
    assembler.object_roles={oid:'inline-graphic' for oid in assembler.object_roles}
    blocks,_=assembler._body()
    table=next(b for b in blocks if isinstance(b,sm.TableBlock))
    assert [c.content.plain_text(source) for c in table.body_rows[0].cells]==['\ufffcPanel A','\ufffcPanel B','data']


def test_unmapped_caption_text_is_kept_at_its_source_position():
    source=SourceDocument(nodes=[SourceNode('p','document','para',None,0,'Unmapped caption')])
    assignment=DocumentAssignment((Assignment('node','p','table-caption',()),),(),())
    assembler=_Assembler(source,None,{}, {},(),[],assignment)
    blocks,_=assembler._body()
    assert len(blocks)==1 and blocks[0].content.plain_text(source)=='Unmapped caption'


def test_image_inside_body_sentence_is_not_moved_to_a_distant_figure_group():
    source=SourceDocument(nodes=[
        SourceNode('p','document','para',None,0,'Before \ufffc after',objects=[ObjectAnchor(7,'inline')]),
        SourceNode('fig','document','para',None,1,'\ufffc',objects=[ObjectAnchor(0,'graphic')])],
        occurrences=[ObjectOccurrence('inline','image','p',7),ObjectOccurrence('graphic','image','fig',0)])
    assignment=DocumentAssignment((Assignment('node','p','body-paragraph',()),
        Assignment('object','inline','figure',()),Assignment('object','graphic','figure',())),(),())
    assembler=_Assembler(source,None,{}, {'figures':[{'graphics':['inline','graphic']}]},(),[],assignment)
    blocks,_=assembler._body()
    assert isinstance(blocks[0],sm.Paragraph)
    assert blocks[0].content.plain_text(source)=='Before \ufffc after'
    assert isinstance(blocks[0].content.parts[1],sm.InlineGraphic)
    assert blocks[1].graphics==('graphic',)


def test_caption_inline_formula_is_not_rendered_twice_as_unmapped_caption():
    source=SourceDocument(nodes=[SourceNode('pic','document','para',None,0,'\ufffc',objects=[ObjectAnchor(0,'o1')]),
        SourceNode('caption','document','para',None,1,'Caption \ufffc',objects=[ObjectAnchor(8,'o2')])],
        occurrences=[ObjectOccurrence('o1','image','pic',0),ObjectOccurrence('o2','image','caption',8)])
    assignment=DocumentAssignment((Assignment('node','caption','figure-caption',()),
        Assignment('object','o1','figure',()),Assignment('object','o2','inline-formula',())),(),())
    body={'figures':[{'graphics':['o1'],'caption_nodes':['caption'],
        'caption_paragraph_quotes':[{'quote':'Caption ⟦公式#o2⟧','node_hint':'caption'}]}]}
    assembler=_Assembler(source,None,{},body,(),[],assignment)
    blocks,_=assembler._body()
    assert len(blocks)==1 and isinstance(blocks[0],sm.Figure)
    assert blocks[0].caption.paragraphs[0].content.plain_text(source)=='Caption \ufffc'
