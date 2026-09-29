#!/usr/bin/env python3
"""Extract PPTX text and compare uncorrected observations. No API calls."""
import argparse
import difflib
import hashlib
import json
import posixpath
import re
import sys
import unicodedata
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
}


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def paragraph_text(node):
    parts = []
    for child in node.iter():
        if child.tag == '{%s}t' % NS['a']:
            parts.append(child.text or '')
        elif child.tag == '{%s}br' % NS['a']:
            parts.append('\n')
        elif child.tag == '{%s}tab' % NS['a']:
            parts.append('\t')
    return ''.join(parts)


def extract_pptx(path):
    path = Path(path)
    items, slides = [], []
    with zipfile.ZipFile(path) as archive:
        presentation = ET.fromstring(archive.read('ppt/presentation.xml'))
        rels = ET.fromstring(archive.read('ppt/_rels/presentation.xml.rels'))
        targets = {
            rel.attrib['Id']: rel.attrib['Target'] for rel in rels
            if rel.attrib.get('Type', '').endswith('/slide')
            and rel.attrib.get('TargetMode') != 'External'
        }
        for order, slide_id in enumerate(presentation.findall('p:sldIdLst/p:sldId', NS), 1):
            target = targets[slide_id.attrib['{%s}id' % NS['r']]]
            member = (target.lstrip('/') if target.startswith('/') else
                      posixpath.normpath(posixpath.join('ppt', target)))
            if not member.startswith('ppt/') or '..' in member.split('/'):
                raise ValueError('Invalid slide relationship target')
            root = ET.fromstring(archive.read(member))
            warnings = ['Review master/layout text, automatic bullets/numbering, and visual reading order.']
            if root.findall('.//p:pic', NS) or root.findall('.//p:graphicFrame', NS):
                warnings.append('Graphics present: visually inventory image/chart/SmartArt text and table layout.')
            if root.findall('.//a:fld', NS):
                warnings.append('Dynamic fields present: verify displayed values.')
            slides.append({'slide': order, 'part': member,
                           'hidden': root.attrib.get('show') in ('0', 'false'),
                           'warnings': warnings})
            for index, paragraph in enumerate(root.findall('.//a:p', NS), 1):
                value = paragraph_text(paragraph)
                if value.strip():
                    items.append({'id': f's{order:02d}-p{index:03d}', 'slide': order,
                                  'text': value, 'source_part': member})
    return {'schema_version': 1, 'source': str(path.resolve()),
            'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'coverage': 'requires_visual_inventory', 'slides': slides, 'items': items}


def indexed_items(document):
    if not isinstance(document, dict) or not isinstance(document.get('items'), list):
        raise ValueError('Expected an object with an items array')
    result = {}
    for item in document['items']:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id']:
            raise ValueError('Each item needs a nonempty string id')
        if not isinstance(item.get('text'), str):
            raise ValueError(f"Item {item['id']} needs a string text")
        if item['id'] in result:
            raise ValueError(f"Duplicate id: {item['id']}")
        result[item['id']] = item
    return result


def canonical(text):
    return unicodedata.normalize('NFC', text.replace('\r\n', '\n').replace('\r', '\n'))


def verify_image_binding(observed, image_path):
    actual_hash = hashlib.sha256(Path(image_path).read_bytes()).hexdigest()
    if observed.get('image_sha256') != actual_hash:
        raise ValueError('Observed image hash is missing or stale; inspect/transcribe the current final image')
    return actual_hash


def compare(source, observed):
    expected, actual = indexed_items(source), indexed_items(observed)
    if not expected:
        raise ValueError('Source inventory is empty; cannot certify an empty comparison')
    findings = []
    for item_id in list(expected) + [key for key in actual if key not in expected]:
        raw_a = expected.get(item_id, {}).get('text')
        raw_b = actual.get(item_id, {}).get('text')
        a, b = canonical(raw_a or ''), canonical(raw_b or '')
        if item_id not in actual:
            status = 'missing'
        elif item_id not in expected:
            status = 'unexpected'
        elif a == b:
            status = 'exact'
        elif re.sub(r'\s', '', a) == re.sub(r'\s', '', b):
            status = 'whitespace_difference'
        else:
            status = 'mismatch'
        changes = [{'operation': op, 'expected': a[i:j], 'observed': b[k:l],
                    'expected_span': [i, j], 'observed_span': [k, l]}
                   for op, i, j, k, l in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
                   if op != 'equal']
        findings.append({'id': item_id, 'status': status, 'expected': raw_a,
                         'observed': raw_b, 'changes': changes,
                         'evidence': actual.get(item_id, {}).get('evidence', 'unspecified')})
    counts = {name: sum(row['status'] == name for row in findings)
              for name in ['exact', 'whitespace_difference', 'mismatch', 'missing', 'unexpected']}
    return {'schema_version': 1, 'normalization': 'NFC and CRLF/CR to LF only',
            'all_text_equal': all(row['status'] == 'exact' for row in findings),
            'visual_review_required': True, 'image_binding_checked': False,
            'counts': counts, 'findings': findings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    extract = commands.add_parser('extract')
    extract.add_argument('pptx')
    extract.add_argument('--out', required=True)
    diff = commands.add_parser('compare')
    for name in ('source', 'observed', 'out'):
        diff.add_argument('--' + name, required=True)
    diff.add_argument('--image', help='Bind observations to this actual image using recorded image_sha256')
    args = parser.parse_args()
    try:
        if args.command == 'extract':
            result = extract_pptx(args.pptx)
            write_json(args.out, result)
            print(json.dumps({'slides': len(result['slides']), 'items': len(result['items']),
                              'out': args.out, 'coverage': result['coverage']}))
            return 0
        observed = json.loads(Path(args.observed).read_text(encoding='utf-8'))
        result = compare(json.loads(Path(args.source).read_text(encoding='utf-8')), observed)
        if args.image:
            result['observed_image_sha256'] = verify_image_binding(observed, args.image)
            result['observed_image'] = str(Path(args.image).resolve())
            result['image_binding_checked'] = True
        write_json(args.out, result)
        print(json.dumps({'all_text_equal': result['all_text_equal'], 'counts': result['counts'],
                          'visual_review_required': True, 'out': args.out}))
        return 0 if result['all_text_equal'] else 2
    except (OSError, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
