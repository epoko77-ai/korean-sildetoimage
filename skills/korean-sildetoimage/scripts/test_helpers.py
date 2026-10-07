#!/usr/bin/env python3
"""Offline regression tests for text truth and pixel preservation. Does not test image generation quality."""
import json
import hashlib
import tempfile
import unicodedata
import unittest
import zipfile
from pathlib import Path
from PIL import Image
import patch_image as patch
import slide_text as text


def inventory(*pairs):
    return {'items': [{'id': key, 'text': value} for key, value in pairs]}


class TextTests(unittest.TestCase):
    def test_nfc_hangul_is_equal(self):
        result = text.compare(inventory(('title', '한글 추진')), inventory(('title', unicodedata.normalize('NFD', '한글 추진'))))
        self.assertTrue(result['all_text_equal'])
        self.assertTrue(result['visual_review_required'])

    def test_numbers_punctuation_and_fullwidth_are_not_hidden(self):
        for observed in ['2026년 3.6%', '2026년 35%', '２０２６년 3.5%', '2026년 3.5']:
            with self.subTest(observed=observed):
                result = text.compare(inventory(('x', '2026년 3.5%')), inventory(('x', observed)))
                self.assertEqual(result['findings'][0]['status'], 'mismatch')

    def test_whitespace_is_review_not_pass(self):
        result = text.compare(inventory(('x', '추진 계획')), inventory(('x', '추진\n계획')))
        self.assertEqual(result['findings'][0]['status'], 'whitespace_difference')
        self.assertFalse(result['all_text_equal'])

    def test_missing_extra_and_repeated_text_by_id(self):
        result = text.compare(inventory(('a', '확인'), ('b', '확인')), inventory(('a', '확인'), ('c', '새 문구')))
        self.assertEqual(result['counts']['exact'], 1)
        self.assertEqual(result['counts']['missing'], 1)
        self.assertEqual(result['counts']['unexpected'], 1)

    def test_duplicate_ids_and_empty_inventory_fail(self):
        with self.assertRaises(ValueError):
            text.compare(inventory(('a', '가')), inventory(('a', '가'), ('a', '나')))
        with self.assertRaises(ValueError):
            text.compare(inventory(), inventory())

    def test_stale_image_observations_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'image.png'
            path.write_bytes(b'original fixture bytes')
            observed = {'image_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            text.verify_image_binding(observed, path)
            path.write_bytes(b'changed image bytes')
            with self.assertRaisesRegex(ValueError, 'stale'):
                text.verify_image_binding(observed, path)

    def test_pptx_relationship_order_runs_breaks_tables_and_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sample.pptx'
            a, p, r = text.NS['a'], text.NS['p'], text.NS['r']
            pres = f'<p:presentation xmlns:p="{p}" xmlns:r="{r}"><p:sldIdLst><p:sldId id="1" r:id="r2"/><p:sldId id="2" r:id="r1"/></p:sldIdLst></p:presentation>'
            rels = '<Relationships><Relationship Id="r1" Type="x/slide" Target="slides/slide1.xml"/><Relationship Id="r2" Type="x/slide" Target="slides/slide2.xml"/></Relationships>'
            slide = f'<p:sld xmlns:p="{p}" xmlns:a="{a}" show="0"><p:cSld><p:spTree><p:grpSp><p:sp><p:txBody><a:p><a:r><a:t>지역 </a:t></a:r><a:r><a:t>경제</a:t></a:r><a:br/><a:r><a:t>활성화</a:t></a:r></a:p></p:txBody></p:sp></p:grpSp><p:graphicFrame><a:tbl><a:tr><a:tc><a:txBody><a:p><a:r><a:t>3.5%</a:t></a:r></a:p></a:txBody></a:tc></a:tr></a:tbl></p:graphicFrame></p:spTree></p:cSld></p:sld>'
            other = f'<p:sld xmlns:p="{p}" xmlns:a="{a}"><a:p><a:r><a:t>다음 장</a:t></a:r></a:p></p:sld>'
            with zipfile.ZipFile(path, 'w') as archive:
                for name, data in [('ppt/presentation.xml', pres), ('ppt/_rels/presentation.xml.rels', rels), ('ppt/slides/slide1.xml', other), ('ppt/slides/slide2.xml', slide)]:
                    archive.writestr(name, data)
            result = text.extract_pptx(path)
            self.assertEqual([item['text'] for item in result['items']], ['지역 경제\n활성화', '3.5%', '다음 장'])
            self.assertEqual(result['items'][2]['slide'], 2)
            self.assertTrue(result['slides'][0]['hidden'])
            self.assertEqual(result['coverage'], 'requires_visual_inventory')


class PixelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base_path = self.root / 'base.png'
        self.base = Image.new('RGBA', (80, 60))
        self.base.putdata([(x * 3, y * 4, (x + y) % 256, 255) for y in range(60) for x in range(80)])
        self.base.save(self.base_path)

    def job(self, name='one', base=None, scale=1, edit=(20, 18, 55, 38)):
        crop_path, job_path = self.root / f'{name}-crop.png', self.root / f'{name}-job.json'
        job = patch.crop(base or self.base_path, [10, 8, 70, 48], list(edit), scale, crop_path, job_path)
        candidate_path = self.root / f'{name}-candidate.png'
        Image.new('RGBA', job['candidate_size'], (255, 20, 80, 255)).save(candidate_path)
        return job_path, candidate_path

    def test_whole_candidate_changes_only_approved_rectangle(self):
        original_bytes = self.base_path.read_bytes()
        job, candidate = self.job()
        out, report = self.root / 'out.png', self.root / 'report.json'
        result = patch.compose(job, candidate, out, report)
        final = patch.load_image(out)
        for y in range(60):
            for x in range(80):
                expected = (255, 20, 80, 255) if 20 <= x < 55 and 18 <= y < 38 else self.base.getpixel((x, y))
                self.assertEqual(final.getpixel((x, y)), expected)
        self.assertEqual(result['changed_pixels_outside'], 0)
        self.assertGreater(result['candidate_guard_mean_abs_diff'], 0)
        self.assertEqual(self.base_path.read_bytes(), original_bytes)

    def test_scaled_candidate_and_inward_feather(self):
        job, candidate = self.job(scale=2)
        out = self.root / 'scaled.png'
        patch.compose(job, candidate, out, self.root / 'report.json', feather=2)
        final = patch.load_image(out)
        self.assertEqual(final.getpixel((20, 18)), self.base.getpixel((20, 18)))
        self.assertEqual(final.getpixel((30, 25)), (255, 20, 80, 255))
        self.assertEqual(patch.preservation(self.base, final, [[20, 18, 55, 38]])['changed_pixels_outside'], 0)

    def test_wrong_size_fails_without_output(self):
        job, candidate = self.job()
        Image.new('RGB', (61, 40)).save(candidate)
        out = self.root / 'bad.png'
        with self.assertRaisesRegex(ValueError, 'dimensions'):
            patch.compose(job, candidate, out, self.root / 'report.json')
        self.assertFalse(out.exists())

    def test_changed_base_or_crop_fails(self):
        job, candidate = self.job()
        self.base.putpixel((0, 0), (9, 9, 9, 255))
        self.base.save(self.base_path)
        with self.assertRaisesRegex(ValueError, 'Base file changed'):
            patch.compose(job, candidate, self.root / 'out.png', self.root / 'report.json')

    def test_exif_normalization_and_no_overwrite(self):
        raw = self.root / 'oriented.png'
        image = Image.new('RGB', (3, 2), 'red')
        exif = image.getexif()
        exif[274] = 6
        image.save(raw, exif=exif)
        out = self.root / 'normalized.png'
        result = patch.prepare(raw, out)
        self.assertEqual(result['size'], [2, 3])
        with self.assertRaises(ValueError):
            patch.prepare(raw, out)

    def test_sequential_overlap_hash_chain_and_union(self):
        job1, candidate1 = self.job()
        out1, report1 = self.root / 'out1.png', self.root / 'report1.json'
        patch.compose(job1, candidate1, out1, report1)
        job2, candidate2 = self.job(name='two', base=out1, edit=(40, 20, 65, 45))
        Image.new('RGBA', (60, 40), 'blue').save(candidate2)
        out2, report2 = self.root / 'out2.png', self.root / 'report2.json'
        patch.compose(job2, candidate2, out2, report2)
        result = patch.verify(self.base_path, out2, [report1, report2], self.root / 'verified.json')
        self.assertTrue(result['passed'])
        with self.assertRaisesRegex(ValueError, 'chain'):
            patch.verify(self.base_path, out2, [report2, report1], self.root / 'bad-check.json')

    def test_outside_alpha_change_is_detected(self):
        job, candidate = self.job()
        out, report = self.root / 'out.png', self.root / 'report.json'
        patch.compose(job, candidate, out, report)
        tampered = patch.load_image(out)
        rgb = tampered.getpixel((1, 1))[:3]
        tampered.putpixel((1, 1), (*rgb, 254))
        changed = self.root / 'tampered.png'
        tampered.save(changed)
        result = patch.verify(self.base_path, changed, [report], self.root / 'failed-check.json')
        self.assertFalse(result['passed'])
        self.assertEqual(result['changed_pixels_outside'], 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
