#!/usr/bin/env python3
"""Lossless local crops, bounded patch composition, and preservation checks. Requires Pillow."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageOps, ImageStat


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def image_hash(image):
    return hashlib.sha256(f'RGBA:{image.width}:{image.height}:'.encode() + image.tobytes()).hexdigest()


def load_image(path):
    with Image.open(path) as image:
        if image.getexif().get(274, 1) != 1:
            raise ValueError('Normalize EXIF orientation with prepare before patching')
        result = image.convert('RGBA')
        result.info['icc_profile'] = image.info.get('icc_profile')
        return result


def fresh(*paths):
    resolved = [Path(path).resolve() for path in paths]
    if len(set(resolved)) != len(resolved):
        raise ValueError('Output paths must be distinct')
    for path in resolved:
        if path.exists():
            raise ValueError(f'Refusing to overwrite: {path}')
        path.parent.mkdir(parents=True, exist_ok=True)


def save_png(image, path, profile=None):
    if Path(path).suffix.lower() != '.png':
        raise ValueError('Lossless output must use a .png filename')
    with Path(path).open('xb') as stream:
        image.save(stream, format='PNG', icc_profile=profile)


def save_json(data, path):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def box(value, size):
    if (not isinstance(value, (tuple, list)) or len(value) != 4
            or any(type(n) is not int for n in value)):
        raise ValueError('Boxes require four integer pixel coordinates')
    left, top, right, bottom = value
    if not (0 <= left < right <= size[0] and 0 <= top < bottom <= size[1]):
        raise ValueError(f'Invalid box {value} for image {size}')
    return tuple(value)


def contains(outer, inner):
    return outer[0] <= inner[0] < inner[2] <= outer[2] and outer[1] <= inner[1] < inner[3] <= outer[3]


def changed_mask(before, after):
    if before.size != after.size:
        raise ValueError('Image sizes differ')
    channels = ImageChops.difference(before, after).split()
    result = channels[0]
    for channel in channels[1:]:
        result = ImageChops.lighter(result, channel)
    return result.point(lambda value: 255 if value else 0)


def fill_rect(mask, bounds, value=255):
    left, top, right, bottom = bounds
    ImageDraw.Draw(mask).rectangle((left, top, right - 1, bottom - 1), fill=value)


def preservation(before, after, regions):
    changed = changed_mask(before, after)
    allowed = Image.new('L', before.size, 0)
    for region in regions:
        fill_rect(allowed, box(region, before.size))
    outside = ImageChops.multiply(changed, ImageOps.invert(allowed))
    return {'changed_pixels_total': changed.histogram()[255],
            'changed_pixels_outside': outside.histogram()[255],
            'outside_change_bbox': outside.getbbox(),
            'outside_pixels_equal': outside.getbbox() is None}


def prepare(image_path, out):
    fresh(out)
    with Image.open(image_path) as original:
        profile = original.info.get('icc_profile')
        image = ImageOps.exif_transpose(original).convert('RGBA')
        save_png(image, out, profile)
    return {'out': str(Path(out).resolve()), 'size': list(image.size),
            'file_sha256': file_hash(out), 'rgba_sha256': image_hash(image)}


def crop(base_path, context, edit, scale, out, job_path):
    base = load_image(base_path)
    context, edit = box(context, base.size), box(edit, base.size)
    if not contains(context, edit):
        raise ValueError('Edit box must be within context box')
    if type(scale) is not int or not 1 <= scale <= 4:
        raise ValueError('Scale must be an integer from 1 through 4')
    fresh(out, job_path)
    sample = base.crop(context)
    if scale != 1:
        sample = sample.resize((sample.width * scale, sample.height * scale), Image.Resampling.LANCZOS)
    save_png(sample, out, base.info.get('icc_profile'))
    job = {'schema_version': 1, 'base': str(Path(base_path).resolve()),
           'base_file_sha256': file_hash(base_path), 'base_rgba_sha256': image_hash(base),
           'base_size': list(base.size), 'context_box': list(context), 'edit_box': list(edit),
           'scale': scale, 'candidate_size': list(sample.size),
           'crop': str(Path(out).resolve()), 'crop_sha256': file_hash(out)}
    save_json(job, job_path)
    return job


def feather_mask(size, amount):
    if type(amount) is not int or not 0 <= amount <= min(size) // 2:
        raise ValueError('Feather must fit inside edit box')
    if amount == 0:
        return Image.new('L', size, 255)
    width, height = size
    values = bytes(round(255 * min(1, min(x, y, width - 1 - x, height - 1 - y) / amount))
                   for y in range(height) for x in range(width))
    return Image.frombytes('L', size, values)


def compose(job_path, candidate_path, out, report_path, feather=0):
    job = json.loads(Path(job_path).read_text(encoding='utf-8'))
    if file_hash(job['base']) != job['base_file_sha256']:
        raise ValueError('Base file changed after crop creation')
    if file_hash(job['crop']) != job['crop_sha256']:
        raise ValueError('Context crop changed after job creation')
    base, candidate = load_image(job['base']), load_image(candidate_path)
    if image_hash(base) != job['base_rgba_sha256'] or list(base.size) != job['base_size']:
        raise ValueError('Base pixels do not match job')
    context, edit = box(job['context_box'], base.size), box(job['edit_box'], base.size)
    if not contains(context, edit):
        raise ValueError('Edit box must be within context')
    scale = job['scale']
    if type(scale) is not int or not 1 <= scale <= 4:
        raise ValueError('Invalid scale in job')
    width, height = context[2] - context[0], context[3] - context[1]
    if candidate.size != (width * scale, height * scale):
        raise ValueError('Candidate dimensions differ from context; do not silently stretch or realign')
    if candidate.info.get('icc_profile') not in (None, base.info.get('icc_profile')):
        raise ValueError('Candidate color profile differs; explicitly inspect/convert a separate copy first')
    if scale != 1:
        candidate = candidate.resize((width, height), Image.Resampling.LANCZOS)
    local = (edit[0] - context[0], edit[1] - context[1], edit[2] - context[0], edit[3] - context[1])
    original_patch, replacement = base.crop(edit), candidate.crop(local)
    if original_patch.getchannel('A').getextrema() == (255, 255) and replacement.getchannel('A').getextrema() != (255, 255):
        raise ValueError('Candidate unexpectedly makes an opaque region transparent')
    mask = feather_mask(replacement.size, feather)
    merged = base.copy()
    merged.paste(Image.composite(replacement, original_patch, mask), (edit[0], edit[1]))
    guard = Image.new('L', candidate.size, 0)
    ring = (max(0, local[0] - 4), max(0, local[1] - 4), min(width, local[2] + 4), min(height, local[3] + 4))
    fill_rect(guard, ring)
    fill_rect(guard, local, 0)
    guard_difference = None
    if guard.getbbox():
        delta = ImageChops.difference(base.crop(context).convert('RGB'), candidate.convert('RGB'))
        guard_difference = sum(ImageStat.Stat(delta, guard).mean) / 3
    metrics = preservation(base, merged, [edit])
    if not metrics['outside_pixels_equal']:
        raise ValueError('Internal error: composition changed pixels outside edit box')
    fresh(out, report_path)
    save_png(merged, out, base.info.get('icc_profile'))
    report = {'schema_version': 1, 'kind': 'patch', 'job': str(Path(job_path).resolve()),
              'base_file_sha256': file_hash(job['base']), 'base_rgba_sha256': image_hash(base),
              'output_file_sha256': file_hash(out), 'output_rgba_sha256': image_hash(merged),
              'candidate_file_sha256': file_hash(candidate_path), 'edit_box': list(edit),
              'out': str(Path(out).resolve()), 'feather': feather,
              'candidate_guard_mean_abs_diff': guard_difference,
              'visual_review_required': True, **metrics}
    save_json(report, report_path)
    return report


def verify(base_path, final_path, report_paths, out):
    base, final = load_image(base_path), load_image(final_path)
    current_hash, regions = file_hash(base_path), []
    for path in report_paths:
        report = json.loads(Path(path).read_text(encoding='utf-8'))
        if report.get('kind') != 'patch' or report['base_file_sha256'] != current_hash:
            raise ValueError('Patch report chain does not start at baseline or is out of order')
        current_hash = report['output_file_sha256']
        regions.append(box(report['edit_box'], base.size))
    result = {'schema_version': 1, 'kind': 'preservation', 'baseline': str(Path(base_path).resolve()),
              'final': str(Path(final_path).resolve()), 'regions': regions,
              'final_matches_report_chain': current_hash == file_hash(final_path),
              'visual_review_required': True, **preservation(base, final, regions)}
    result['passed'] = result['outside_pixels_equal'] and result['final_matches_report_chain']
    fresh(out)
    save_json(result, out)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('--image', required=True)
    prep.add_argument('--out', required=True)
    cr = commands.add_parser('crop')
    for name in ['base', 'out', 'job']:
        cr.add_argument('--' + name, required=True)
    for name in ['context', 'edit']:
        cr.add_argument('--' + name, nargs=4, type=int, required=True)
    cr.add_argument('--scale', type=int, default=1)
    co = commands.add_parser('compose')
    for name in ['job', 'candidate', 'out', 'report']:
        co.add_argument('--' + name, required=True)
    co.add_argument('--feather', type=int, default=0)
    ve = commands.add_parser('verify')
    for name in ['base', 'final', 'out']:
        ve.add_argument('--' + name, required=True)
    ve.add_argument('--reports', nargs='+', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            result = prepare(args.image, args.out)
        elif args.command == 'crop':
            result = crop(args.base, args.context, args.edit, args.scale, args.out, args.job)
        elif args.command == 'compose':
            result = compose(args.job, args.candidate, args.out, args.report, args.feather)
        else:
            result = verify(args.base, args.final, args.reports, args.out)
        print(json.dumps(result, ensure_ascii=False))
        return 2 if args.command == 'verify' and not result['passed'] else 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
