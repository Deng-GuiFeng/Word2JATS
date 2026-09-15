import json
from types import SimpleNamespace

from PIL import Image, ImageChops

from scripts.review_web_public_tiny import main, fingerprint, COLS, ROWS, CELL


def test_tiny_review_preserves_pixels_and_requires_explicit_mark(tmp_path):
    folder=tmp_path/'visual-deltas'/'sample'
    (folder/'patches').mkdir(parents=True)
    patches=[]
    for index,size in enumerate([(41,34),(48,48),(49,10)]):
        im=Image.new('RGB',size,'#637eaa');im.putpixel((0,0),(0,255,128))
        file=f'patches/{index}.png';im.save(folder/file)
        patches.append({'id':index,'file':file,'size':size,'pixel_sha256':fingerprint(im),'reviewed_at':None})
    path=folder/'manifest.json';path.write_text(json.dumps({'patches':patches}))
    args=SimpleNamespace(root=tmp_path,name='sample',mark=None)
    main(args)
    assert not any(p['reviewed_at'] for p in json.loads(path.read_text())['patches'])
    with Image.open(folder/'tiny/tiny-00001.png') as sheet:
        assert sheet.size==(COLS*CELL,ROWS*CELL)
        for index,patch in enumerate(patches[:2]):
            width,height=patch['size'];left=index*CELL+(CELL-width)//2
            with Image.open(folder/patch['file']) as original:
                assert ImageChops.difference(sheet.crop((left,16,left+width,16+height)),original).getbbox() is None
    args.mark=[1];main(args)
    updated=json.loads(path.read_text())['patches']
    assert updated[0]['reviewed_at'] and updated[1]['reviewed_at']
    assert updated[2]['reviewed_at'] is None
