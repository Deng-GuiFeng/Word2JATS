"""EMF 原图只生成有界预览，下载资源和源字节保持不变。"""
import hashlib
import io
from pathlib import Path
from zipfile import ZipFile
from lxml import html
from PIL import Image
import pytest

from webapp import images
from webapp.render import render_html


def source_emf():
    word=Path(__file__).resolve().parents[1]/'样例数据/X02/初始文件.docx'
    with ZipFile(word) as archive:
        return archive.read(next(n for n in archive.namelist() if n.endswith('.emf')))


def test_real_emf_rasterization_is_cached_without_modifying_original(tmp_path,monkeypatch):
    original=source_emf(); digest=hashlib.sha256(original).hexdigest()
    result=images.emf_preview_png(original,tmp_path)
    assert result is not None
    with Image.open(io.BytesIO(result)) as image:
        assert image.format=='PNG' and image.width>200 and image.height>10
        assert image.width*image.height<10_000_000
    assert hashlib.sha256(original).hexdigest()==digest
    assert list(tmp_path.iterdir())==[tmp_path/(digest+'.png')]
    monkeypatch.setattr(images.shutil,'which',lambda name:None)
    assert images.emf_preview_png(original,tmp_path)==result


def test_emf_decoder_absent_keeps_fallback(tmp_path,monkeypatch):
    monkeypatch.setattr(images.shutil,'which',lambda name:None)
    assert images.emf_preview_png(source_emf(),tmp_path/'cache') is None
    assert not (tmp_path/'cache').exists()


@pytest.mark.parametrize('blob',[b'',b'not emf',b'\x01\0\0\0'+b'\0'*100,b'x'*(16*1024*1024+1)])
def test_invalid_or_excessive_input_does_not_start_decoder(blob,tmp_path,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('decoder must not start')
    monkeypatch.setattr(images.subprocess,'Popen',forbidden)
    assert images.emf_preview_png(blob,tmp_path) is None


def test_preview_uses_png_capability_only_for_exact_resource():
    xml=b'<article xmlns:xlink="http://www.w3.org/1999/xlink"><body><p><inline-graphic xlink:href="a/plot.emf"/><inline-graphic xlink:href="b/plot.emf"/></p></body></article>'
    page=html.fromstring(render_html(xml,'example',previewable_images={'/api/figure/example/a/plot.emf'}))
    assert page.xpath('//img/@src')==['/api/figure/example/a/plot.emf']
    assert page.xpath('//a[@class="w2j-media-fallback"]/@href')==['/api/figure/example/b/plot.emf']


def test_decoder_timeout_kills_its_own_process_group_and_cleans_temp(tmp_path,monkeypatch):
    killed=[]
    class Process:
        pid=123456789
        def wait(self,timeout=None):
            if timeout: raise images.subprocess.TimeoutExpired('libreoffice',timeout)
    monkeypatch.setattr(images.shutil,'which',lambda name:'/test/libreoffice')
    monkeypatch.setattr(images.subprocess,'Popen',lambda *a,**k:Process())
    monkeypatch.setattr(images.os,'killpg',lambda pid,sig:killed.append((pid,sig)))
    assert images.emf_preview_png(source_emf(),tmp_path) is None
    assert killed==[(123456789,images.signal.SIGKILL)]
    assert not list(tmp_path.iterdir())
