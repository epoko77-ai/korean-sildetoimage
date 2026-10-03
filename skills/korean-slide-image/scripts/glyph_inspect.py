#!/usr/bin/env python3
"""Make read-only glyph cards; compare visual transcriptions after reading pixels.

This helper does not recognize text, decide errors, choose masks, or edit inputs.
The answer key is separate from the cards to limit word/context-based guessing.
"""
import argparse
import math
import random
import secrets
from pathlib import Path
from PIL import Image, ImageDraw

import patch_image as patch
import slide_text as text
import stroke_repair as stroke

CELL = 512


def expanded_box(box, size):
    x0, y0, x1, y1 = box
    height = y1-y0
    # Vision's per-character boxes can shift into a neighbor or clip a vowel.
    # A halo preserves evidence; the box is never used as a deletion boundary.
    dx, dy = max(6, math.ceil(height*.45)), max(4, math.ceil(height*.2))
    return [max(0,x0-dx), max(0,y0-dy), min(size[0],x1+dx), min(size[1],y1+dy)]


def card(base, hint, crop_box, label):
    crop = base.crop(crop_box).convert('RGB')
    scale = min(8, (CELL-32)//crop.width, (CELL-64)//crop.height)
    if scale < 1:
        raise ValueError('Glyph hint is too large; locate visually instead of shrinking pixels')
    enlarged = crop.resize((crop.width*scale,crop.height*scale), Image.Resampling.NEAREST)
    output = Image.new('RGB',(CELL,CELL),(225,228,232))
    x, y = (CELL-enlarged.width)//2, 40+(CELL-48-enlarged.height)//2
    output.paste(enlarged,(x,y))
    draw = ImageDraw.Draw(output)
    draw.text((16,12), label, fill=(15,25,40))
    # Indicate the approximate target horizontally, outside the image pixels.
    center = x+round(((hint[0]+hint[2])/2-crop_box[0])*scale)
    draw.line((center,y-7,center,y-2), fill=(20,70,150), width=2)
    draw.line((center,y+enlarged.height+1,center,y+enlarged.height+6), fill=(20,70,150), width=2)
    return output, scale


def prepare(image_path, source_path, ocr_path, out_dir):
    root = Path(out_dir).resolve()
    if root.exists():
        raise ValueError('Use a new inspection directory; do not overwrite readings')
    root.mkdir(parents=True)
    locations_path = root/'locations.json'
    locations = stroke.locate(image_path,source_path,ocr_path,locations_path)
    base = patch.load_image(image_path)
    entries, unmapped = [], []
    for item in locations['items']:
        for target in item['inspection_targets']:
            entry = {'item_id': item['id'], **target}
            if not target['glyph_bbox']:
                unmapped.append(entry)
                continue
            hint = target['glyph_bbox']
            crop_box = expanded_box(hint,base.size)
            if crop_box[2]-crop_box[0] > CELL-32 or crop_box[3]-crop_box[1] > CELL-64:
                unmapped.append({**entry,'reason':'hint_too_large'})
                continue
            entries.append({**entry,'id':secrets.token_hex(6),'crop_box':crop_box})
    random.SystemRandom().shuffle(entries)
    blind_entries, sheets = [], []
    for start in range(0,len(entries),4):
        sheet = Image.new('RGB',(CELL*2,CELL*2),(225,228,232))
        sheet_path = root/f'sheet-{start//4+1:03d}.png'
        for slot, entry in enumerate(entries[start:start+4]):
            rendered, scale = card(base,entry['glyph_bbox'],entry['crop_box'],entry['id'])
            path = root/(entry['id']+'.png')
            patch.save_png(rendered,path)
            sheet.paste(rendered,((slot%2)*CELL,(slot//2)*CELL))
            entry.update(card=stroke.bound(path),scale=scale,sheet=sheet_path.name,slot=slot)
            blind_entries.append({'id':entry['id'],'card':entry['card'],'sheet':sheet_path.name,'slot':slot})
        patch.save_png(sheet,sheet_path)
        sheets.append(stroke.bound(sheet_path))
    blind_path = root/'blind.json'
    patch.save_json({'schema_version':1,'kind':'glyph-inspection-blind',
                     'instructions':'Read the whole glyph near the blue ticks from its pixels. The ticks are only a position hint; include visible strokes in the surrounding halo. Do not guess from neighboring words. If the target or its strokes are unclear, record uncertain. No expected text is included.',
                     'entries':blind_entries,'sheets':sheets},blind_path)
    packet = {'schema_version':1,'kind':'glyph-inspection-packet','status':'needs_review',
              'image':stroke.bound(image_path),'source':stroke.bound(source_path),
              'ocr':stroke.bound(ocr_path),'locations':stroke.bound(locations_path),
              'blind':stroke.bound(blind_path),'entries':entries,'unmapped':unmapped,
              'scope':'Source vowels ㅡ/ㅓ and individually aligned OCR differences; not all text.',
              'automatic_error_detection':False,'automatic_mask_detection':False}
    patch.save_json(packet,root/'packet.json')
    return packet


def compare(packet_path, readings_path, out):
    packet = stroke.read(packet_path)
    if packet.get('kind') != 'glyph-inspection-packet':
        raise ValueError('Expected a glyph inspection packet')
    for key in ('image','source','ocr','locations','blind'):
        stroke.check(packet[key])
    blind = stroke.read(packet['blind']['path'])
    entries = {row['id']:row for row in packet['entries']}
    if len(entries) != len(packet['entries']) or set(entries) != {row['id'] for row in blind['entries']}:
        raise ValueError('Inspection ids do not agree')
    for row in blind['entries']:
        if row['card'] != entries[row['id']]['card']:
            raise ValueError('Card binding does not agree')
        stroke.check(row['card'])
    for sheet in blind['sheets']:
        stroke.check(sheet)
    readings = stroke.read(readings_path)
    if readings.get('blind_sha256') != packet['blind']['sha256']:
        raise ValueError('Readings belong to different cards')
    if not isinstance(readings.get('reviewer'),str) or not readings['reviewer'].strip():
        raise ValueError('Name the actual visual reader')
    observed = {}
    for row in readings.get('entries',[]):
        rid = row.get('id')
        if rid not in entries or rid in observed:
            raise ValueError('Unknown or duplicate reading id')
        status = row.get('status')
        value = row.get('observed_text')
        if status not in ('readable','uncertain'):
            raise ValueError('A reading must be readable or uncertain')
        if status == 'readable' and (not isinstance(value,str) or len(text.canonical(value)) != 1 or value.isspace()):
            raise ValueError('Transcribe exactly one visible character or mark uncertain')
        observed[rid] = row
    results = []
    for rid, entry in entries.items():
        reading = observed.get(rid)
        row = {**entry,'reading':reading,'status':'needs_visual_review'}
        if reading and reading['status'] == 'readable':
            actual = text.canonical(reading['observed_text'])
            row.update(status='agrees' if actual == entry['expected'] else 'possible_difference',
                       jamo=stroke.difference(actual,entry['expected']))
        results.append(row)
    result = {'schema_version':1,'kind':'glyph-inspection-findings','status':'needs_review',
              'packet':stroke.bound(packet_path),'readings':stroke.bound(readings_path),
              'context_confirmation_required':True,'automatic_repair':False,'items':results,
              'unmapped':packet['unmapped'],
              'counts':{status:sum(row['status']==status for row in results)
                        for status in ('agrees','possible_difference','needs_visual_review')}}
    patch.fresh(out)
    patch.save_json(result,out)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command',required=True)
    p = commands.add_parser('prepare')
    for arg in ('image','source','ocr','out-dir'):
        p.add_argument('--'+arg,required=True)
    p = commands.add_parser('compare')
    for arg in ('packet','readings','out'):
        p.add_argument('--'+arg,required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        result = prepare(args.image,args.source,args.ocr,args.out_dir)
        print(f"{len(result['entries'])} cards; {len(result['unmapped'])} targets need visual location")
    else:
        result = compare(args.packet,args.readings,args.out)
        print(result['counts'])


if __name__ == '__main__':
    main()
