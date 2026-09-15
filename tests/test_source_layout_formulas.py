"""复杂公式仅在源版式明确、输出边界闭合时保留为原生排版图。"""
from dataclasses import replace
import io
from pathlib import Path
from zipfile import ZipFile

from lxml import etree
from PIL import Image
import pytest

from word2jats.parse.docx_reader import read_source_docx
from word2jats.semantic import model as sm
from word2jats.model.source import SourceText
from word2jats.semantic.source_layout import candidate_groups, prepare_source_layout, fragment_docx
from word2jats.render.v2 import render_v2
from word2jats.verify.audit import audit_provenance, audit_source_coverage
from word2jats.semantic import source_layout as layout_module

ROOT=Path(__file__).resolve().parents[1]


def sample(name):
    path=ROOT/'样例数据'/name/'初始文件.docx'
    return path,read_source_docx(path)


def png():
    buffer=io.BytesIO();Image.new('RGB',(200,50),'black').save(buffer,format='PNG')
    return buffer.getvalue()


def paragraphs(source,ids):
    return tuple(sm.Paragraph(None,sm.RichText.from_source(SourceText(((i,0,len(source.node(i).text)),)))) for i in ids)


def test_only_explicit_layouts_are_selected_across_all_samples():
    observed={}
    for path in sorted((ROOT/'样例数据').glob('*/初始文件.docx')):
        groups=candidate_groups(read_source_docx(path))
        if groups:observed[path.parent.name]=groups
    assert observed=={'X02':[('doc/p42',)],'X04':[('doc/p62','doc/p63'),('doc/p68','doc/p69')]}


@pytest.mark.parametrize('change',['ordinary prose','no underline','no indentation','extra objects'])
def test_fraction_selection_does_not_guess(change):
    _,source=sample('X04');first=source.node('doc/p62');second=source.node('doc/p63')
    if change=='ordinary prose':second.text='             Further discussion.'
    elif change=='no underline':first.run_spans=[replace(s,run=replace(s.run,underline=False)) for s in first.run_spans]
    elif change=='no indentation':second.text=second.text.strip()
    else:second.objects=source.node('doc/p57').objects or [object()]
    assert ('doc/p62','doc/p63') not in candidate_groups(source)


def test_fragment_keeps_equation_runs_and_resources_but_not_line_numbers_or_headers():
    path,source=sample('X02')
    blob=fragment_docx(path.read_bytes(),('doc/p42',),source)
    ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    with ZipFile(path) as original,ZipFile(io.BytesIO(blob)) as fragment:
        src=etree.fromstring(original.read('word/document.xml'))
        dst=etree.fromstring(fragment.read('word/document.xml'))
        assert etree.tostring(src.xpath('/w:document/w:body/w:p',namespaces=ns)[41])==etree.tostring(dst.xpath('/w:document/w:body/w:p',namespaces=ns)[0])
        assert not dst.xpath('//w:lnNumType|//w:headerReference|//w:footerReference',namespaces=ns)
        for name in original.namelist():
            if name.startswith('word/media/') or name.startswith('word/embeddings/'):
                assert fragment.read(name)==original.read(name)


def test_text_fraction_uses_exact_source_alt_text_and_one_generated_graphic(tmp_path):
    path,source=sample('X04');ids=('doc/p62','doc/p63')
    doc=sm.SemanticDoc(source,body=paragraphs(source,ids))
    records=prepare_source_layout(doc,path,tmp_path,rasterizer=lambda *_:png())
    assert len(records)==1 and len(doc.body)==1
    rendered=render_v2(doc)
    root=etree.fromstring(rendered.xml_bytes)
    assert len(root.findall('.//disp-formula'))==1
    assert ''.join(root.find('.//graphic/alt-text').itertext())==''.join(source.node(i).text for i in ids)
    assert list(rendered.media.values())==[png()]
    assert audit_provenance(rendered.xml_bytes,rendered.provenance,source).ok


def test_failed_rasterization_keeps_existing_output(tmp_path):
    path,source=sample('X04');doc=sm.SemanticDoc(source,body=paragraphs(source,('doc/p62','doc/p63')))
    before=doc.body
    assert prepare_source_layout(doc,path,tmp_path,rasterizer=lambda *_:None)==[]
    assert doc.body==before


def test_never_consumes_unrelated_text_or_noncontiguous_blocks(tmp_path):
    path,source=sample('X04');p1,p2=paragraphs(source,('doc/p62','doc/p63'))
    unrelated=paragraphs(source,('doc/p64',))[0]
    for blocks in [(p1,unrelated,p2),(replace(p1,content=sm.RichText(p1.content.parts+unrelated.content.parts)),p2)]:
        doc=sm.SemanticDoc(source,body=blocks)
        assert prepare_source_layout(doc,path,tmp_path,rasterizer=lambda *_:png())==[]
        assert doc.body==blocks


def test_wrong_source_file_is_rejected_before_rendering(tmp_path):
    _,source=sample('X04');path,_=sample('X02')
    doc=sm.SemanticDoc(source,body=paragraphs(source,('doc/p62','doc/p63')))
    with pytest.raises(ValueError,match='摘要'):
        prepare_source_layout(doc,path,tmp_path,rasterizer=lambda *_:png())


@pytest.mark.parametrize('mixed',[False,True])
def test_floating_equation_objects_merge_in_display_and_mixed_representations(tmp_path,mixed):
    path,source=sample('X02');node=source.node('doc/p42');ids=[a.occ_id for a in node.objects]
    label=sm.RichText.from_source(SourceText(((node.node_id,2,len(node.text)),)))
    left=sm.Formula('left','image',image_occurrence=ids[0])
    right=sm.Formula('right','image',image_occurrence=ids[1],label=label,display=not mixed)
    second=(sm.Paragraph(None,sm.RichText((sm.InlineFormula('right'),)+label.parts)) if mixed else right)
    doc=sm.SemanticDoc(source,body=(left,second),inline_formulas=(right,) if mixed else ())
    assert len(prepare_source_layout(doc,path,tmp_path,rasterizer=lambda *_:png()))==1
    assert len(doc.body)==1 and not doc.inline_formulas
    rendered=render_v2(doc)
    assert audit_provenance(rendered.xml_bytes,rendered.provenance,source).ok
    records=[r for r in rendered.provenance if r.transform=='word-layout-to-png']
    assert {r.source_object for r in records}==set(ids)
    assert all(r.origin_kind=='transform' for r in records)
    coverage=audit_source_coverage(source,rendered.provenance)
    assert not [i for i in coverage.issues if i.source_id in {node.node_id,*ids}]


def test_cross_reference_target_is_not_removed(tmp_path):
    path,source=sample('X04');first,second=paragraphs(source,('doc/p62','doc/p63'))
    first=replace(first,entity_id='target')
    reference=sm.Paragraph(None,sm.RichText((sm.CrossReference('other',('target',),sm.RichText()),)))
    doc=sm.SemanticDoc(source,body=(first,second,reference))
    assert prepare_source_layout(doc,path,tmp_path,rasterizer=lambda *_:png())==[]
    assert doc.body[0]==first


@pytest.mark.parametrize('field,value',[('source_sha256','wrong'),('png_sha256','wrong'),
    ('nodes',['doc/p64']),('recipe','unapproved')])
def test_derived_media_evidence_is_independently_checked(tmp_path,field,value):
    path,source=sample('X04');doc=sm.SemanticDoc(source,body=paragraphs(source,('doc/p62','doc/p63')))
    prepare_source_layout(doc,path,tmp_path,rasterizer=lambda *_:png())
    rendered=render_v2(doc)
    records=tuple(replace(r,derivation={**r.derivation,field:value}) if r.derivation else r for r in rendered.provenance)
    assert not audit_provenance(rendered.xml_bytes,records,source).ok


def test_no_optional_tools_means_no_conversion_or_files(tmp_path,monkeypatch):
    monkeypatch.setattr(layout_module.shutil,'which',lambda _:None)
    assert layout_module.rasterize_fragment(b'not a file',tmp_path/'cache') is None
    assert not (tmp_path/'cache').exists()


def test_native_renderer_cache_and_temporary_cleanup(tmp_path,monkeypatch):
    if not layout_module.shutil.which('libreoffice') or not layout_module.shutil.which('pdftoppm'):
        pytest.skip('原生版式工具未安装')
    path,source=sample('X04');fragment=fragment_docx(path.read_bytes(),('doc/p62','doc/p63'),source)
    result=layout_module.rasterize_fragment(fragment,tmp_path)
    assert result and result.startswith(b'\x89PNG')
    with Image.open(io.BytesIO(result)) as image:
        assert image.width>300 and 25<image.height<250
    assert len(list(tmp_path.iterdir()))==1
    monkeypatch.setattr(layout_module,'_run',lambda *_:pytest.fail('缓存命中不应再次启动排版进程'))
    assert layout_module.rasterize_fragment(fragment,tmp_path)==result


def test_timeout_stops_only_own_process_group(monkeypatch):
    import subprocess
    calls=[]
    class Process:
        pid=987654
        def wait(self,timeout=None):
            if timeout is not None:raise subprocess.TimeoutExpired('lo',timeout)
            calls.append(('wait',))
    monkeypatch.setattr(layout_module.subprocess,'Popen',lambda *a,**kw:Process())
    monkeypatch.setattr(layout_module.os,'killpg',lambda *a:calls.append(a))
    assert not layout_module._run(['lo'],1)
    assert calls==[(987654,layout_module.signal.SIGKILL),('wait',)]
