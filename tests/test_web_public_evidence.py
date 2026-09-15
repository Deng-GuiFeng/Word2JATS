"""验收辅助脚本不得忽略像素差异或自动标记视觉通过。"""
from PIL import Image, ImageChops

from scripts.review_web_public_deltas import category, delta


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
