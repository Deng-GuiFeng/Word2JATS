"""验收辅助脚本不得忽略像素差异或自动标记视觉通过。"""
from PIL import Image, ImageChops

from scripts.review_web_public_deltas import category, delta
from scripts.audit_web_public_round import reviewed_delta_frames


def test_delta_preserves_all_changed_pixels():
    before=Image.new('RGB',(300,200),'white')
    after=before.copy()
    after.putpixel((2,3),(254,255,255))
    after.putpixel((270,198),(255,255,254))
    box,patch=delta(before,after)
    assert box==(2,3,271,199)
    restored=before.copy();restored.paste(patch,box)
    assert ImageChops.difference(after,restored).getbbox() is None


def test_same_frame_and_context_change():
    im=Image.new('RGB',(300,200),'white')
    assert delta(im,im)==(None,None)
    box,patch=delta(im,im,full=True)
    assert box==(0,0,300,200)
    assert patch.tobytes()==im.tobytes()


def test_first_frame_and_resized_frame_are_full():
    im=Image.new('RGB',(300,200),'white')
    for previous in [None,Image.new('RGB',(390,844),'white')]:
        box,patch=delta(previous,im)
        assert box==(0,0,300,200)
        assert patch.size==im.size


def test_review_grouping_does_not_drop_large_changes():
    assert category((128,128))=='micro'
    assert category((850,180))=='strip'
    assert category((851,180))=='full'
    assert category((500,181))=='full'


def test_delta_review_requires_base_and_every_patch():
    data={'patches':[{'id':0,'reviewed_at':1},{'id':1,'reviewed_at':None}],
          'frames':[
              {'file':'a','previous_file':None,'source_sha256':'A','patch':0,
               'box':[0,0,10,10],'size':[10,10]},
              {'file':'b','previous_file':'a','source_sha256':'B','patch':1,
               'box':[1,1,2,2],'size':[10,10]},
              {'file':'c','previous_file':'b','source_sha256':'C','patch':0,
               'box':[1,1,2,2],'size':[10,10]},
              {'file':'a','source_sha256':'A','same_as':0}]}
    assert reviewed_delta_frames(data)=={'A'}
    data['patches'][1]['reviewed_at']=2
    assert reviewed_delta_frames(data)=={'A','B','C'}


def test_full_review_does_not_require_unreviewed_preceding_frame():
    data={'patches':[{'id':0,'reviewed_at':None},{'id':1,'reviewed_at':1}],
          'frames':[
              {'file':'a','previous_file':None,'source_sha256':'A','patch':0,
               'box':[0,0,10,10],'size':[10,10]},
              {'file':'b','previous_file':'a','source_sha256':'B','patch':1,
               'box':[0,0,10,10],'size':[10,10]}]}
    assert reviewed_delta_frames(data)=={'B'}
