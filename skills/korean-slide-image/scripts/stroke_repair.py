#!/usr/bin/env python3
"""Locate copy differences and execute visually planned, bounded stroke removals.

No model/API/font substitution. OCR suggests locations; an agent/person still identifies
the actual erroneous stroke. A plan and its evidence are frozen before one candidate.
"""
import argparse
import difflib
import json
import sys
import unicodedata
from pathlib import Path
from PIL import Image, ImageDraw

import patch_image as patch
import slide_text as text

CHO = list('ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ')
JUNG = list('ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ')
JONG = ['', *list('ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ')]
REMOVALS = {('ㅗ', 'ㅡ'), ('ㅜ', 'ㅡ'), ('ㅕ', 'ㅓ')}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def bound(path):
    return {'path': str(Path(path).resolve()), 'sha256': patch.file_hash(path)}


def check(ref):
    if patch.file_hash(ref['path']) != ref['sha256']:
        raise ValueError('Bound input changed: ' + ref['path'])
    return Path(ref['path'])


def decompose(char):
    if not isinstance(char, str) or len(char) != 1 or not 0xAC00 <= ord(char) <= 0xD7A3:
        return None
    n = ord(char) - 0xAC00
    return [CHO[n // 588], JUNG[n % 588 // 28], JONG[n % 28]]


def difference(observed, expected):
    before, after = decompose(observed), decompose(expected)
    changed = [i for i in range(3) if before[i] != after[i]] if before and after else []
    supported = (changed == [1] and (before[1], after[1]) in REMOVALS)
    return {'observed': before, 'expected': after, 'changed_components': changed,
            'route': 'review_stroke_removal' if supported else 'other_repair',
            'supported_removal': supported}


def optional_box(value, size):
    """OCR geometry is only a hint; empty/invalid boxes require visual location."""
    try:
        return list(patch.box(value, size))
    except (ValueError, TypeError):
        return None


def locate(image_path, source_path, ocr_path, out):
    """Conservative one-line matching. Ambiguous/multiline mappings stay unlocated."""
    image = patch.load_image(image_path)
    source = text.indexed_items(read(source_path))
    if not source:
        raise ValueError('Source is empty')
    ocr = read(ocr_path)
    text.verify_image_binding(ocr, image_path)
    if ocr.get('language_correction') is not False:
        raise ValueError('Use raw OCR without language correction')
    observations = ocr.get('observations', [])
    ids = [r.get('observation_id') for r in observations]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('OCR observation ids must be unique strings')
    rows = []
    similarities = {}
    for sid, item in source.items():
        expected = text.canonical(item['text'])
        if not expected.strip():
            raise ValueError('Empty source text')
        similarities[sid] = [(difflib.SequenceMatcher(None, expected, text.canonical(o['text']), autojunk=False).ratio(), n)
                             for n, o in enumerate(observations)]
    for sid, item in source.items():
        ranked = sorted(similarities[sid], reverse=True)
        result = {'id': sid, 'expected_text': item['text'], 'status': 'needs_visual_mapping', 'differences': []}
        if (not ranked or ranked[0][0] < .65 or
                (len(ranked) > 1 and ranked[0][0] - ranked[1][0] < .08)):
            result['reason'] = 'No unique single OCR line; visually map the paragraph/occurrence.'
            rows.append(result)
            continue
        score, index = ranked[0]
        competitors = [dict((n, s) for s, n in matches).get(index, 0)
                       for other, matches in similarities.items() if other != sid]
        if competitors and score - max(competitors) < .08:
            result['reason'] = 'Several source items map to this OCR line; do not guess the occurrence.'
            rows.append(result)
            continue
        obs = observations[index]
        expected, observed = text.canonical(item['text']), text.canonical(obs['text'])
        result.update(observation_id=obs['observation_id'], raw_ocr=obs['text'], match_similarity=score,
                      context_bbox=optional_box(obs.get('bbox'), image.size),
                      status='ocr_agrees_visual_review_required' if expected == observed else 'needs_visual_review')
        chars = obs.get('characters', [])
        # Swift indices are grapheme indices. Use them only where normalization and codepoints agree.
        geometry_valid = (observed == obs['text'] and ''.join(c.get('text', '') for c in chars) == observed
                          and all(len(c.get('text', '')) == 1 and c.get('index') == n for n, c in enumerate(chars)))
        matcher = difflib.SequenceMatcher(None, expected, observed, autojunk=False)
        for tag, a, b, c, d in matcher.get_opcodes():
            if tag == 'equal':
                continue
            intervals = [(a + k, a + k + 1, c + k, c + k + 1) for k in range(b-a)] if tag == 'replace' and b-a == d-c else [(a,b,c,d)]
            for i,j,k,l in intervals:
                finding = {'expected_range': [i,j], 'observed_range': [k,l],
                           'expected': expected[i:j], 'observed': observed[k:l], 'glyph_bbox': None,
                           'localization': 'needs_visual_location'}
                if j-i == l-k == 1:
                    finding['jamo'] = difference(observed[k:l], expected[i:j])
                    if geometry_valid:
                        box = optional_box(chars[k].get('bbox'), image.size)
                        # Some OCR engines report the whole word for every character.
                        if box and box[2]-box[0] <= 1.8 * (box[3]-box[1]):
                            finding.update(glyph_bbox=box, localization='ocr_character_hint')
                result['differences'].append(finding)
        rows.append(result)
    result = {'schema_version': 1, 'kind': 'stroke-locations', 'status': 'needs_review',
              'image': bound(image_path), 'source': bound(source_path), 'ocr': bound(ocr_path),
              'visual_review_required': True, 'automatic_mask_detection': False, 'items': rows}
    patch.fresh(out)
    patch.save_json(result, out)
    return result


def review_geometry(base, source_path, review):
    source = text.indexed_items(read(source_path))
    item = source.get(review.get('item_id'))
    if not item or not isinstance(review.get('reviewer'), str) or not review['reviewer'].strip():
        raise ValueError('Name a source item and the actual visual reviewer')
    if review.get('status') != 'incorrect' or review.get('evidence') not in ('visual', 'visual+ocr'):
        raise ValueError('Only a visually confirmed error can create a stroke plan')
    expected = text.canonical(item['text'])
    observed = text.canonical(review.get('observed_text', ''))
    diffs = [i for i, (a,b) in enumerate(zip(observed, expected)) if a != b]
    if len(observed) != len(expected) or len(diffs) != 1:
        raise ValueError('Stroke removal requires exactly one changed syllable in this source item')
    index = diffs[0]
    delta = difference(observed[index], expected[index])
    if not delta['supported_removal']:
        raise ValueError('Unsupported component change; use another repair route, not a guessed stroke')
    glyph = patch.box(review.get('glyph_box'), base.size)
    context = patch.box(review.get('context_box'), base.size)
    if not patch.contains(context, glyph):
        raise ValueError('Glyph must be inside its visual context')
    if glyph[2]-glyph[0] > 1.8 * (glyph[3]-glyph[1]):
        raise ValueError('Use one glyph box, not the entire word or line')
    for key in ['shape_note', 'background_note', 'design_note']:
        if not isinstance(review.get(key), str) or not review[key].strip():
            raise ValueError('Record visual evidence: ' + key)
    return expected, observed, index, delta, glyph, context


def pixel_operations(base, glyph, segments):
    if not isinstance(segments, list) or not segments:
        raise ValueError('A visually selected stroke mask is required')
    pixels = base.load()
    operations, seen = [], set()
    for segment in segments:
        axis, fixed, start, end = (segment.get(k) for k in ['axis', 'fixed', 'start', 'end'])
        if axis not in ('horizontal', 'vertical') or any(type(v) is not int for v in [fixed, start, end]) or start >= end:
            raise ValueError('Segments need axis and integer fixed/start/end (end exclusive)')
        points = [(v, fixed) if axis == 'horizontal' else (fixed, v) for v in range(start,end)]
        donors = [(start-1, fixed), (end, fixed)] if axis == 'horizontal' else [(fixed, start-1), (fixed,end)]
        if any(not(glyph[0] <= x < glyph[2] and glyph[1] <= y < glyph[3]) for x,y in points+donors):
            raise ValueError('Mask and both interpolation donors must be inside the single glyph box')
        a,b = [pixels[xy] for xy in donors]
        if a[3] != 255 or b[3] != 255 or any(pixels[xy][3] != 255 for xy in points):
            raise ValueError('Transparent stroke regions require another repair method')
        if max(abs(a[c]-b[c]) for c in range(3)) > 48:
            raise ValueError('Donors differ too much; inspect texture/neighboring strokes rather than smoothing them')
        for step, (x,y) in enumerate(points, 1):
            if (x,y) in seen:
                raise ValueError('Overlapping stroke segments are ambiguous')
            seen.add((x,y))
            t = step / (len(points)+1)
            after = [round((1-t)*a[c]+t*b[c]) for c in range(3)] + [pixels[x,y][3]]
            operations.append({'xy': [x,y], 'before': list(pixels[x,y]), 'after': after})
    area = (glyph[2]-glyph[0]) * (glyph[3]-glyph[1])
    if len(seen) > max(8, int(area * .18)):
        raise ValueError('Mask is too large for this bounded stroke-removal route')
    pending, connected = [next(iter(seen))], set()
    while pending:
        p = pending.pop()
        if p in connected:
            continue
        connected.add(p)
        pending.extend((p[0]+dx,p[1]+dy) for dx in [-1,0,1] for dy in [-1,0,1]
                       if (p[0]+dx,p[1]+dy) in seen and (p[0]+dx,p[1]+dy) not in connected)
    if connected != seen:
        raise ValueError('Only one connected stroke may be removed per candidate')
    if not any(op['before'] != op['after'] for op in operations):
        raise ValueError('Plan does not change any pixels')
    return operations


def plan(base_path, source_path, review_path, out):
    base = patch.load_image(base_path)
    review = read(review_path)
    if review.get('image_sha256') != patch.file_hash(base_path) or review.get('source_sha256') != patch.file_hash(source_path):
        raise ValueError('Visual stroke review belongs to a different image/source')
    expected, observed, index, delta, glyph, context = review_geometry(base, source_path, review)
    ops = pixel_operations(base, glyph, review.get('segments'))
    out = Path(out)
    mask_path, crop_path, job_path, preview_path = [out.with_name(out.stem+s) for s in ['-mask.png','-context.png','-job.json','-mask-preview.png']]
    patch.fresh(out, mask_path, crop_path, job_path, preview_path)
    mask = Image.new('L', base.size, 0)
    for op in ops:
        mask.putpixel(tuple(op['xy']), 255)
    patch.save_png(mask, mask_path)
    patch.crop(base_path, context, glyph, 1, crop_path, job_path)
    preview = base.crop(glyph).convert('RGB')
    draw = ImageDraw.Draw(preview)
    for op in ops:
        x,y = op['xy'];draw.point((x-glyph[0],y-glyph[1]), fill=(255,0,160))
    patch.save_png(preview.resize((preview.width*8,preview.height*8),Image.Resampling.NEAREST), preview_path)
    result = {'schema_version': 1, 'kind': 'stroke-plan', 'status': 'planned_not_verified',
              'base': bound(base_path), 'source': bound(source_path), 'review': bound(review_path),
              'job': bound(job_path), 'mask': bound(mask_path), 'mask_preview': bound(preview_path),
              'item_id': review['item_id'], 'expected_text': expected, 'observed_text': observed,
              'glyph_index': index, 'jamo': delta, 'glyph_box': list(glyph), 'context_box': list(context),
              'segments': review['segments'], 'pixels': ops, 'mask_pixels': len(ops),
              'automatic_mask_detection': False, 'visual_review_required': True,
              'algorithm': 'linear RGB interpolation between same-row/column neighbors; alpha unchanged'}
    patch.save_json(result, out)
    return result


def validate_plan(path):
    p = read(path)
    if p.get('kind') != 'stroke-plan' or p.get('schema_version') != 1:
        raise ValueError('Expected a frozen stroke-removal plan')
    for key in ['base','source','review','job','mask','mask_preview']:
        check(p[key])
    base = patch.load_image(p['base']['path'])
    review = read(p['review']['path'])
    if review.get('image_sha256') != p['base']['sha256'] or review.get('source_sha256') != p['source']['sha256']:
        raise ValueError('Stroke evidence binding is invalid')
    expected, observed, index, delta, glyph, context = review_geometry(base, p['source']['path'], review)
    ops = pixel_operations(base, glyph, review.get('segments'))
    if (ops != p['pixels'] or p['segments'] != review['segments'] or p['item_id'] != review['item_id']
            or p['expected_text'] != expected or p['observed_text'] != observed or p['glyph_index'] != index
            or p['jamo'] != delta or p['glyph_box'] != list(glyph) or p['context_box'] != list(context)):
        raise ValueError('Plan does not match its source visual review and pixel operations')
    mask = Image.open(p['mask']['path']).convert('L')
    expected_mask = Image.new('L', base.size, 0)
    for op in ops:
        expected_mask.putpixel(tuple(op['xy']),255)
    if mask.size != base.size or mask.tobytes() != expected_mask.tobytes():
        raise ValueError('Stroke mask differs from planned pixels')
    job = read(p['job']['path'])
    if (job['base_file_sha256'] != p['base']['sha256'] or job['edit_box'] != list(glyph)
            or job['context_box'] != list(context) or job['scale'] != 1
            or patch.file_hash(job['crop']) != job['crop_sha256']):
        raise ValueError('Stroke plan preservation job is invalid')
    return p


def verify_result(plan_path, final_path):
    p = validate_plan(plan_path)
    base, final = patch.load_image(p['base']['path']), patch.load_image(final_path)
    if base.size != final.size:
        return {'passed': False, 'reason': 'Dimensions changed'}
    expected = base.copy()
    for op in p['pixels']:
        expected.putpixel(tuple(op['xy']),tuple(op['after']))
    delta = patch.changed_mask(base, final)
    mask = Image.open(p['mask']['path']).convert('L')
    outside = sum(changed != 0 and allowed == 0 for changed,allowed in zip(delta.tobytes(),mask.tobytes()))
    match = expected.tobytes() == final.tobytes()
    return {'passed': match and outside == 0, 'matches_planned_pixels': match,
            'changed_pixels_total': delta.histogram()[255], 'changed_pixels_outside_stroke': outside,
            'visual_review_required': True}


def apply(plan_path, request_path, out, report_path):
    import workflow
    request = workflow.load_request(request_path)
    if request['operation'] != 'local-stroke' or request.get('stroke_plan') != bound(plan_path):
        raise ValueError('Freeze this stroke plan in a local-stroke request before applying it')
    p = validate_plan(plan_path)
    base = patch.load_image(p['base']['path'])
    final = base.copy()
    for op in p['pixels']:
        final.putpixel(tuple(op['xy']),tuple(op['after']))
    patch.fresh(out, report_path)
    patch.save_png(final,out,base.info.get('icc_profile'))
    metrics = verify_result(plan_path,out)
    if not metrics['passed']:
        raise ValueError('Applied pixels differ from frozen plan')
    result = {'schema_version':1,'kind':'patch','method':'local-stroke','status':'needs_review',
              'job':p['job']['path'],'stroke_plan':bound(plan_path),'request':bound(request_path),
              'base_file_sha256':p['base']['sha256'],'base_rgba_sha256':patch.image_hash(base),
              'output_file_sha256':patch.file_hash(out),'output_rgba_sha256':patch.image_hash(final),
              'edit_box':p['glyph_box'],'out':str(Path(out).resolve()),
              'visual_review_required':True,'stroke_preservation':metrics,
              **patch.preservation(base,final,[p['glyph_box']])}
    patch.save_json(result,report_path)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command',required=True)
    locate_parser = sub.add_parser('locate')
    for k in ['image','source','ocr','out']:
        locate_parser.add_argument('--'+k,required=True)
    plan_parser = sub.add_parser('plan')
    for k in ['base','source','review','out']:
        plan_parser.add_argument('--'+k,required=True)
    apply_parser = sub.add_parser('apply')
    for k in ['plan','request','out','report']:
        apply_parser.add_argument('--'+k,required=True)
    args = parser.parse_args()
    try:
        if args.command == 'locate':
            result = locate(args.image,args.source,args.ocr,args.out)
        elif args.command == 'plan':
            result = plan(args.base,args.source,args.review,args.out)
        else:
            result = apply(args.plan,args.request,args.out,args.report)
        print(json.dumps(result,ensure_ascii=False))
        return 0
    except (OSError,ValueError,KeyError,TypeError,IndexError) as error:
        print(f'ERROR: {error}',file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
