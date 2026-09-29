#!/usr/bin/env python3
"""Freeze inputs, register real PNGs, and gate image-bound visual reviews. No API calls."""
import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from PIL import Image

import patch_image as patch
import slide_text as text


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def bound(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': patch.file_hash(path)}


def check(ref):
    if not isinstance(ref, dict) or patch.file_hash(ref['path']) != ref['sha256']:
        raise ValueError('Evidence file is missing or changed; create a new record')
    return Path(ref['path'])


def write(path, value):
    text.write_json(path, value)
    return value


def png(path):
    with Image.open(path) as im:
        if im.format != 'PNG':
            raise ValueError('Register a lossless PNG; normalize other formats first')
        if im.getexif().get(274, 1) != 1:
            raise ValueError('Normalize EXIF orientation before registration')
        im.load()
        return {'width': im.width, 'height': im.height, 'mode': im.mode}


def inventory(path):
    source = read(path)
    items = text.indexed_items(source)
    if not items or any(not r['text'].strip() for r in items.values()):
        raise ValueError('A nonempty, reviewed source inventory is required')
    if any('\ufffd' in r['text'] for r in items.values()):
        raise ValueError('Source contains a Unicode replacement character; inspect the original')
    return items


def freeze(source, prompt, out, kind='generate', target_ids=(), references=(), preserve_job=None):
    items = inventory(source)
    if kind not in ('generate', 'edit', 'local-font'):
        raise ValueError('Unknown operation kind')
    targets = list(target_ids) if target_ids else list(items)
    if len(targets) != len(set(targets)) or any(i not in items for i in targets):
        raise ValueError('Target ids must be unique source ids')
    if kind == 'generate' and set(targets) != set(items):
        raise ValueError('New generation must include the complete source inventory')
    prompt_string = Path(prompt).read_text(encoding='utf-8')
    if not prompt_string.strip():
        raise ValueError('Prompt is empty')
    normalized = text.canonical(prompt_string)
    missing = []
    for item_id in targets:
        value = items[item_id]['text']
        # Accept literal copy or its JSON-escaped representation, without changing either file.
        representations = (text.canonical(value), text.canonical(json.dumps(value, ensure_ascii=False)[1:-1]))
        if not any(v in normalized for v in representations):
            missing.append(item_id)
    if missing:
        raise ValueError('Required copy missing from actual prompt: ' + ', '.join(missing))
    result = {'schema_version': 1, 'kind': 'request', 'operation': kind,
              'source': bound(source), 'prompt': bound(prompt), 'target_ids': targets,
              'references': [bound(p) for p in references],
              'copy_presence_checked': True, 'layout_review_required': True,
              'preservation_job': None}
    if preserve_job:
        if kind == 'generate':
            raise ValueError('A new-generation request cannot claim repair preservation')
        job = read(preserve_job)
        if patch.file_hash(job['base']) != job['base_file_sha256'] or patch.file_hash(job['crop']) != job['crop_sha256']:
            raise ValueError('Repair base/crop changed')
        base = patch.load_image(job['base'])
        edit = patch.box(job['edit_box'], base.size)
        context = patch.box(job['context_box'], base.size)
        if not patch.contains(context, edit):
            raise ValueError('Repair region is outside context')
        result['preservation_job'] = bound(preserve_job)
    return write(out, result)


def load_request(path):
    request = read(path)
    if request.get('kind') != 'request' or request.get('schema_version') != 1:
        raise ValueError('Expected a frozen request')
    check(request['source'])
    check(request['prompt'])
    for ref in request.get('references', []):
        check(ref)
    if request.get('preservation_job'):
        job = read(check(request['preservation_job']))
        if patch.file_hash(job['base']) != job['base_file_sha256'] or patch.file_hash(job['crop']) != job['crop_sha256']:
            raise ValueError('Frozen repair base/crop changed')
    return request


def register(request_path, actual_prompt, image, out):
    request = load_request(request_path)
    if patch.file_hash(actual_prompt) != request['prompt']['sha256']:
        raise ValueError('Actual tool prompt differs from frozen prompt; freeze the actual input first')
    info = png(image)
    result = {'schema_version': 1, 'kind': 'candidate', 'status': 'needs_review',
              'request': bound(request_path), 'source': request['source'],
              'actual_prompt': bound(actual_prompt), 'image': bound(image), **info}
    return write(out, result)


def preserve(request, candidate, report_path):
    ref = request.get('preservation_job')
    if not ref:
        if report_path:
            raise ValueError('A preservation job must be frozen before the repair')
        return {'status': 'not_requested'}
    job_path = check(ref)
    job = read(job_path)
    if not report_path:
        return {'status': 'needs_review', 'reason': 'Patch report not supplied'}
    report = read(report_path)
    expected_base = job['base_file_sha256']
    linkage = (report.get('kind') == 'patch' and report.get('base_file_sha256') == expected_base
               and report.get('output_file_sha256') == candidate['image']['sha256']
               and report.get('edit_box') == job['edit_box']
               and Path(report.get('job', '')).resolve() == job_path.resolve())
    base, final = patch.load_image(job['base']), patch.load_image(candidate['image']['path'])
    if base.size != final.size:
        return {'status': 'failed', 'reason': 'Final dimensions differ from repair baseline'}
    metrics = patch.preservation(base, final, [job['edit_box']])
    return {'status': 'passed' if linkage and metrics['outside_pixels_equal'] else 'failed',
            'report': bound(report_path), 'report_linkage_valid': linkage, **metrics}


def gate(candidate_path, review_path, out, ocr_path=None, patch_report=None):
    candidate = read(candidate_path)
    if candidate.get('kind') != 'candidate':
        raise ValueError('Expected a registered candidate')
    check(candidate['request'])
    request = load_request(candidate['request']['path'])
    if candidate['source'] != request['source']:
        raise ValueError('Candidate source differs from frozen request')
    image_path = check(candidate['image'])
    check(candidate['actual_prompt'])
    if candidate['actual_prompt']['sha256'] != request['prompt']['sha256']:
        raise ValueError('Candidate prompt binding is invalid')
    dimensions = png(image_path)
    items = inventory(check(candidate['source']))
    pending, failures = [], []
    result = {'schema_version': 1, 'kind': 'gate', 'candidate': bound(candidate_path),
              'image': candidate['image'], 'source': candidate['source'],
              'status': 'needs_review', 'visual_review_is_reviewer_attestation': True}
    preserved = preserve(request, candidate, patch_report)
    result['preservation'] = preserved
    if preserved['status'] == 'failed':
        failures.append('Pixel preservation failed')
    elif preserved['status'] == 'needs_review':
        pending.append('Pixel preservation not checked')
    if not review_path:
        pending.append('Visual review missing')
        result.update(pending=pending, failures=failures, status='unresolved' if failures else 'needs_review')
        return write(out, result)
    review = read(review_path)
    if review.get('image_sha256') != candidate['image']['sha256'] or review.get('source_sha256') != candidate['source']['sha256']:
        raise ValueError('Visual review belongs to a different image or source')
    if not isinstance(review.get('reviewer'), str) or not review['reviewer'].strip():
        raise ValueError('Name the actual reviewer/method')
    result['review'] = bound(review_path)
    rows = review.get('items')
    if not isinstance(rows, list):
        raise ValueError('Review needs an items array')
    by_id = {}
    for row in rows:
        item_id = row.get('id')
        if item_id not in items or item_id in by_id:
            raise ValueError('Review ids must be unique source ids')
        if row.get('status') not in ('correct', 'incorrect', 'uncertain'):
            raise ValueError('Review status must be correct, incorrect, or uncertain')
        if row.get('evidence') not in ('visual', 'visual+ocr', 'ocr'):
            raise ValueError('Review must distinguish visual and OCR evidence')
        if not isinstance(row.get('observed_text'), str):
            raise ValueError('Transcribe the actually observed text')
        patch.box(row.get('bbox'), (dimensions['width'], dimensions['height']))
        by_id[item_id] = row
        if row['status'] == 'incorrect':
            failures.append(f'{item_id}: confirmed text/shape error')
        elif row['status'] == 'uncertain' or row['evidence'] == 'ocr':
            pending.append(f'{item_id}: visual review incomplete')
        elif text.canonical(row['observed_text']) != text.canonical(items[item_id]['text']):
            pending.append(f'{item_id}: correct claim conflicts with visual transcription')
    for item_id in items.keys() - by_id.keys():
        pending.append(f'{item_id}: no visual observation')
    coverage = review.get('inventory', {})
    if coverage.get('status') != 'complete':
        pending.append('Full-image inventory for missing/extra text is incomplete')
    extras = coverage.get('unexpected_text')
    if not isinstance(extras, list):
        raise ValueError('inventory.unexpected_text must be an array')
    if extras:
        failures.append('Unexpected text is present')
    design = review.get('design', {})
    if design.get('status') not in ('passed', 'failed', 'not_checked'):
        raise ValueError('Design status must be passed, failed, or not_checked')
    if design.get('status') == 'failed':
        failures.append('Visual design/legibility review failed')
    elif design.get('status') != 'passed' or not str(design.get('note', '')).strip():
        pending.append('Visual design/legibility review incomplete')
    result['ocr'] = {'status': 'not_used'}
    if ocr_path:
        ocr = read(ocr_path)
        if ocr.get('image_sha256') != candidate['image']['sha256']:
            raise ValueError('OCR belongs to a different image')
        if ocr.get('language_correction') is not False:
            raise ValueError('Use raw OCR without language correction or declare OCR unavailable')
        observations = {}
        for row in ocr.get('observations', []):
            oid = row.get('observation_id')
            if not isinstance(oid, str) or not oid or oid in observations:
                raise ValueError('OCR observation ids must be unique nonempty strings')
            if not isinstance(row.get('text'), str):
                raise ValueError('Invalid OCR text')
            observations[oid] = row['text']
        consumed, discrepancies = set(), []
        for item_id, row in by_id.items():
            ids = row.get('ocr_ids', [])
            if not isinstance(ids, list) or any(i not in observations or i in consumed for i in ids) or len(ids) != len(set(ids)):
                raise ValueError('OCR mapping contains unknown/reused observation ids')
            consumed.update(ids)
            raw = '\n'.join(observations[i] for i in ids)
            if not ids or text.canonical(raw) != text.canonical(items[item_id]['text']):
                discrepancies.append({'id': item_id, 'raw_ocr': raw, 'expected': items[item_id]['text'],
                                      'visual_status': row['status'], 'review_note': row.get('ocr_note')})
                if not str(row.get('ocr_note', '')).strip():
                    pending.append(f'{item_id}: OCR disagreement/miss needs explicit visual adjudication')
        for ignored in review.get('ignored_ocr', []):
            oid = ignored.get('observation_id')
            if oid not in observations or oid in consumed or not str(ignored.get('reason', '')).strip():
                raise ValueError('Ignored OCR needs a unique id and a visual reason')
            consumed.add(oid)
        if set(observations) - consumed:
            pending.append('OCR observations remain unmapped; inspect for extra text')
        result['ocr'] = {'status': 'recorded', 'file': bound(ocr_path), 'discrepancies': discrepancies,
                         'unmapped_ids': sorted(set(observations) - consumed)}
    else:
        if not str(review.get('ocr_unavailable_reason', '')).strip():
            pending.append('Record why OCR was unavailable/not used')
        if any(r.get('evidence') == 'visual+ocr' for r in rows):
            raise ValueError('Review claims OCR evidence but no OCR file was supplied')
    result.update(pending=pending, failures=failures,
                  status='unresolved' if failures else 'needs_review' if pending else 'verified',
                  items_reviewed=len(by_id), required_items=len(items), reviewer=review['reviewer'])
    return write(out, result)


def release(gate_path, out):
    """Re-evaluate evidence now, then copy the exact verified bytes to a fresh final PNG."""
    saved = read(gate_path)
    if saved.get('kind') != 'gate':
        raise ValueError('Expected a gate result')
    candidate_path = check(saved['candidate'])
    review_path = check(saved['review']) if saved.get('review') else None
    ocr_path = check(saved['ocr']['file']) if saved.get('ocr', {}).get('file') else None
    report_path = check(saved['preservation']['report']) if saved.get('preservation', {}).get('report') else None
    with tempfile.TemporaryDirectory(prefix='korean-slide-gate-') as temp:
        current = gate(candidate_path, review_path, Path(temp)/'check.json', ocr_path, report_path)
    if current['status'] != 'verified':
        raise ValueError('Final export blocked: ' + current['status'])
    if Path(out).suffix.lower() != '.png':
        raise ValueError('Final output must use a .png filename')
    data = Path(current['image']['path']).read_bytes()
    if hashlib.sha256(data).hexdigest() != current['image']['sha256']:
        raise ValueError('Image changed during final export')
    receipt_path = str(out)+'.receipt.json'
    patch.fresh(out, receipt_path)
    with Path(out).open('xb') as stream:
        stream.write(data)
    receipt = {'schema_version': 1, 'kind': 'release', 'status': 'verified',
               'final': bound(out), 'gate': bound(gate_path), 'reviewer': current['reviewer'],
               'visual_review_is_reviewer_attestation': True,
               'preservation': current['preservation']['status']}
    write(receipt_path, receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    fr = sub.add_parser('freeze')
    for key in ('source', 'prompt', 'out'):
        fr.add_argument('--'+key, required=True)
    fr.add_argument('--kind', choices=['generate', 'edit', 'local-font'], default='generate')
    fr.add_argument('--target-id', action='append', default=[])
    fr.add_argument('--reference', action='append', default=[])
    fr.add_argument('--preserve-job')
    reg = sub.add_parser('register')
    for key in ('request', 'actual-prompt', 'image', 'out'):
        reg.add_argument('--'+key, required=True)
    ga = sub.add_parser('gate')
    ga.add_argument('--candidate', required=True)
    ga.add_argument('--review')
    ga.add_argument('--ocr')
    ga.add_argument('--patch-report')
    ga.add_argument('--out', required=True)
    rel = sub.add_parser('release')
    rel.add_argument('--gate', required=True)
    rel.add_argument('--out', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'freeze':
            value = freeze(args.source, args.prompt, args.out, args.kind, args.target_id, args.reference, args.preserve_job)
        elif args.command == 'register':
            value = register(args.request, args.actual_prompt, args.image, args.out)
        elif args.command == 'gate':
            value = gate(args.candidate, args.review, args.out, args.ocr, args.patch_report)
        else:
            value = release(args.gate, args.out)
        print(json.dumps({'kind': value['kind'], 'status': value.get('status'), 'out': args.out,
                          'pending': value.get('pending', []), 'failures': value.get('failures', [])}, ensure_ascii=False))
        return 2 if args.command == 'gate' and value['status'] != 'verified' else 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
