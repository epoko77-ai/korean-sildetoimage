#!/usr/bin/env python3
"""Recheck the small, published stroke examples without models, OCR or fonts.

This verifies the frozen interpolation and pixels, not Korean reading or design.
"""
import hashlib
import json
import sys
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/korean-slide-image/scripts'))
import stroke_repair as stroke


def verify(root=ROOT/'docs/evidence'):
    root = Path(root).resolve()
    data = json.loads((root/'examples.json').read_text())
    results = []
    for row in data['examples']:
        paths = {}
        for key in ('before','after','mask'):
            ref = row[key]
            path = (root/ref['path']).resolve()
            if not path.is_relative_to(root):
                raise ValueError('Example path escapes evidence directory')
            if hashlib.sha256(path.read_bytes()).hexdigest() != ref['sha256']:
                raise ValueError('Example file changed: '+key)
            paths[key] = path
        base = Image.open(paths['before']).convert('RGBA')
        final = Image.open(paths['after']).convert('RGBA')
        mask = Image.open(paths['mask']).convert('L')
        if base.size != final.size or base.size != mask.size:
            raise ValueError('Image dimensions differ')
        operations = stroke.pixel_operations(base,row['glyph_box'],row['segments'])
        expected = base.copy()
        seen = set()
        for op in operations:
            xy = tuple(op['xy'])
            if xy in seen or mask.getpixel(xy) != 255:
                raise ValueError('Unexpected mask pixels')
            seen.add(xy)
            expected.putpixel(xy,tuple(op['after']))
        if mask.tobytes().count(255) != len(seen) or expected.tobytes() != final.tobytes():
            raise ValueError('Output does not equal the frozen interpolation plan')
        # Whole-image equality above proves no unplanned/outside pixel changed.
        changed = sum(op['before'] != op['after'] for op in operations)
        if changed != row['changed_pixels']:
            raise ValueError('Changed-pixel count differs')
        results.append({'id':row['id'],'changed_pixels':changed,
                        'outside_mask_changed_pixels':0,'planned_pixels_match':True})
    return {'examples':results,'note':'Pixel checks only. Examples are selected illustrations, not a success-rate sample.'}


if __name__ == '__main__':
    print(json.dumps(verify(),ensure_ascii=False,indent=2))
