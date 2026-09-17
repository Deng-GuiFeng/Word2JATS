"""显式空格与省略空格应等价，不能放过真实内容遗漏或格网错误。"""
from copy import deepcopy
import pytest

from word2jats.model.source import SourceDocument,SourceNode,SourcePart
from word2jats.understand.serialize import serialize
from word2jats.understand.passes import flattened_rows,validate_flattened_layout


def fixture():
    nodes=[SourceNode('p1','document','para',None,0,'A\tB'),SourceNode('p2','document','para',None,1,'C\t')]
    source=SourceDocument(parts=[SourcePart('document','document','/word/document.xml',node_ids=('p1','p2'))],nodes=nodes)
    rows=flattened_rows(serialize(source),['p1','p2'])
    cell=lambda r,c,ids:{'row':r,'column':c,'rowspan':1,'colspan':1,'row_header':False,'segment_ids':ids}
    response={'resolved':True,'n_rows':2,'n_cols':2,'header_rows':1,'cells':[
        cell(1,1,['r0s0']),cell(1,2,['r0s1']),cell(2,1,['r1s0']),cell(2,2,[])]}
    return rows,response


def test_explicit_empty_cell_is_equivalent_to_omitted_empty_cell():
    rows,response=fixture()
    omitted=deepcopy(response);omitted['cells'].pop()
    expected,failures=validate_flattened_layout(rows,omitted)
    actual,errors=validate_flattened_layout(rows,response)
    assert failures==errors==[]
    assert actual==expected


@pytest.mark.parametrize('dimension',['n_rows','n_cols'])
def test_redundant_count_can_be_recovered_from_complete_cell_coordinates(dimension):
    rows,response=fixture()
    response[dimension]=1
    layout,errors=validate_flattened_layout(rows,response)
    assert errors==[] and layout['valid']
    assert layout['n_rows']==layout['n_cols']==2
    assert layout['declared_dimensions'][dimension]==1


@pytest.mark.parametrize('fault',['missing','duplicate','unknown','order','range','empty-column','empty-row'])
def test_accepting_empty_cells_does_not_accept_real_mapping_errors(fault):
    rows,response=fixture()
    if fault=='missing':response['cells'][2]['segment_ids']=[]
    if fault=='duplicate':response['cells'][2]['segment_ids']=['r0s0','r1s0']
    if fault=='unknown':response['cells'][2]['segment_ids']=['unknown','r1s0']
    if fault=='order':response['cells'][0]['segment_ids'],response['cells'][1]['segment_ids']=['r0s1'],['r0s0']
    if fault=='range':response['cells'][2]['row']=3
    if fault=='empty-column':response['cells'][0]['segment_ids']=['r0s0','r0s1'];response['cells'][1]['segment_ids']=[]
    if fault=='empty-row':response['cells'][1]['segment_ids'].append('r1s0');response['cells'][2]['segment_ids']=[]
    layout,errors=validate_flattened_layout(rows,response)
    assert not layout['valid'] and errors
    assert any('必须给出源片段编号' in e for e in errors) # 真正有错时保留原重问契约。


def numeric_fixture(lines=None):
    lines=lines or ['\t1 month\t60 months\t120 months\t180 months',
                   '0\t279\t\t242\t   \t136\t  \t43',
                   '1\t476\t\t399\t   \t245\t\t71']
    nodes=[SourceNode(f'p{i}','document','para',None,i,text) for i,text in enumerate(lines)]
    source=SourceDocument(parts=[SourcePart('document','document','/word/document.xml',node_ids=tuple(n.node_id for n in nodes))],nodes=nodes)
    rows=flattened_rows(serialize(source),[n.node_id for n in nodes])
    cells=[]
    for row in rows:
        for i in range(min(4,len(row.segments))):
            segments=[s.segment_id for s in (row.segments[i:] if i==3 else [row.segments[i]])]
            cells.append({'row':row.index+1,'column':i+1,'rowspan':1,'colspan':1,'row_header':False,'segment_ids':segments})
    return rows,{'resolved':True,'n_rows':len(rows),'n_cols':4,'header_rows':1,'cells':cells}


def test_numeric_rectangular_rows_keep_blank_header_and_separate_values():
    rows,response=numeric_fixture()
    layout,errors=validate_flattened_layout(rows,response)
    assert not errors
    assert layout['n_cols']==5
    assert not layout['rows'][0]['cells'][0]['ranges']
    for row,expected in zip(layout['rows'][1:],[['0','279','242','136','43'],['1','476','399','245','71']]):
        actual=[''.join(rows[int(n[1:])].source_text[a:b] for n,a,b in c['ranges']).strip() for c in row['cells']]
        assert actual==expected


@pytest.mark.parametrize('change',['missing-value','non-numeric','no-blank-header','multi-header','missing-segment'])
def test_numeric_alignment_does_not_guess_sparse_or_non_rectangular_data(change):
    lines=['\t1 month\t60 months\t120 months\t180 months',
           '0\t279\t242\t136\t43','1\t476\t399\t245\t71']
    if change=='missing-value':lines[-1]='1\t476\t\t245\t71'
    if change=='non-numeric':lines[-1]='1\t476\t399\tnot available\t71'
    if change=='no-blank-header':lines[0]=lines[0].lstrip('\t')
    rows,response=numeric_fixture(lines)
    if change=='multi-header':response['header_rows']=2
    if change=='missing-segment':response['cells'][-1]['segment_ids'].pop()
    layout,_=validate_flattened_layout(rows,response)
    assert not layout.get('source_rectangular_grid')
