import copy
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

import patch_image as patch
import workflow as flow


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.save('source.json', {'items': [{'id': 'a', 'text': '증가'}]})
        self.prompt = self.root / 'prompt.txt'
        self.prompt.write_text('정확한 문구: “증가”')
        self.image = self.root / 'image.png'
        Image.new('RGBA', (96, 64), (20, 30, 40, 255)).save(self.image)
        self.request = self.root / 'request.json'
        flow.freeze(self.source, self.prompt, self.request)
        self.candidate = self.root / 'candidate.json'
        flow.register(self.request, self.prompt, self.image, self.candidate)

    def save(self, name, obj):
        path = self.root / name
        path.write_text(json.dumps(obj, ensure_ascii=False))
        return path

    def review(self, image=None):
        return {'image_sha256': patch.file_hash(image or self.image),
                'source_sha256': patch.file_hash(self.source), 'reviewer': 'synthetic test fixture',
                'items': [{'id': 'a', 'status': 'correct', 'observed_text': '증가',
                           'evidence': 'visual+ocr', 'bbox': [5, 5, 35, 25], 'ocr_ids': ['o1']}],
                'inventory': {'status': 'complete', 'unexpected_text': []},
                'design': {'status': 'passed', 'note': 'Synthetic control'}, 'ignored_ocr': []}

    def ocr(self, image=None):
        return {'image_sha256': patch.file_hash(image or self.image), 'language_correction': False,
                'observations': [{'observation_id': 'o1', 'text': '증가'}]}

    def evaluate(self, review=None, ocr=None, name='gate.json', candidate=None, report=None):
        review_path = self.save('review-'+name, review if review is not None else self.review())
        ocr_path = self.save('ocr-'+name, ocr if ocr is not None else self.ocr())
        return flow.gate(candidate or self.candidate, review_path, self.root/name, ocr_path, report)

    def test_complete_review_passes_without_claiming_pixel_preservation(self):
        result = self.evaluate()
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['preservation']['status'], 'not_requested')

    def test_file_exists_is_not_a_text_pass(self):
        result = flow.gate(self.candidate, None, self.root/'gate.json')
        self.assertEqual(result['status'], 'needs_review')

    def test_large_nonimage_file_is_rejected(self):
        invalid = self.root/'invalid.png'
        invalid.write_bytes(b'not a PNG' * 1000)
        with self.assertRaises(OSError):
            flow.register(self.request, self.prompt, invalid, self.root/'bad.json')

    def test_wrong_prompt_or_changed_source_cannot_be_registered(self):
        wrong = self.root/'wrong.txt'
        wrong.write_text('중가')
        with self.assertRaisesRegex(ValueError, 'Actual tool prompt'):
            flow.register(self.request, wrong, self.image, self.root/'wrong.json')
        self.save('source.json', {'items': [{'id': 'a', 'text': '감소'}]})
        with self.assertRaises(ValueError):
            flow.register(self.request, self.prompt, self.image, self.root/'changed.json')

    def test_freeze_rejects_omitted_copy_and_partial_new_generation(self):
        with self.assertRaisesRegex(ValueError, 'Required copy missing'):
            p = self.root/'missing.txt'
            p.write_text('좋은 디자인')
            flow.freeze(self.source, p, self.root/'bad.json')
        self.save('two.json', {'items': [{'id': 'a', 'text': '증가'}, {'id': 'b', 'text': '감소'}]})
        with self.assertRaisesRegex(ValueError, 'complete source'):
            flow.freeze(self.root/'two.json', self.prompt, self.root/'partial.json', target_ids=['a'])

    def test_ocr_false_alarm_requires_explicit_visual_adjudication(self):
        raw = self.ocr()
        raw['observations'][0]['text'] = '중가'
        self.assertEqual(self.evaluate(ocr=raw)['status'], 'needs_review')
        review = self.review()
        review['items'][0]['ocr_note'] = 'Visual glyph has ㅡ; raw OCR misread it as ㅜ.'
        result = self.evaluate(review, raw, 'resolved.json')
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['ocr']['discrepancies'][0]['raw_ocr'], '중가')

    def test_confirmed_error_blocks_even_when_ocr_matches_source(self):
        review = self.review()
        review['items'][0].update(status='incorrect', observed_text='중가')
        self.assertEqual(self.evaluate(review)['status'], 'unresolved')

    def test_ocr_only_or_uncertain_glyphs_cannot_certify(self):
        review = self.review()
        review['items'][0]['evidence'] = 'ocr'
        self.assertEqual(self.evaluate(review)['status'], 'needs_review')
        review['items'][0].update(evidence='visual+ocr', status='uncertain')
        self.assertEqual(self.evaluate(review, name='uncertain.json')['status'], 'needs_review')

    def test_missing_extra_and_unmapped_text_are_not_ignored(self):
        review = self.review()
        review['items'] = []
        self.assertEqual(self.evaluate(review)['status'], 'needs_review')
        review = self.review()
        review['inventory']['unexpected_text'] = [{'text': '추가'}]
        self.assertEqual(self.evaluate(review, name='extra.json')['status'], 'unresolved')
        raw = self.ocr()
        raw['observations'].append({'observation_id': 'o2', 'text': '추가'})
        self.assertEqual(self.evaluate(ocr=raw, name='unmapped.json')['status'], 'needs_review')

    def test_correct_claim_does_not_override_different_transcription(self):
        review = self.review()
        review['items'][0]['observed_text'] = '중가'
        self.assertEqual(self.evaluate(review)['status'], 'needs_review')

    def test_stale_review_and_stale_ocr_are_rejected(self):
        review = self.review()
        review['image_sha256'] = 'old'
        with self.assertRaisesRegex(ValueError, 'different image'):
            self.evaluate(review)
        raw = self.ocr()
        raw['image_sha256'] = 'old'
        with self.assertRaisesRegex(ValueError, 'OCR belongs'):
            self.evaluate(ocr=raw, name='stale-ocr.json')

    def test_corrected_ocr_and_duplicate_mappings_are_rejected(self):
        raw = self.ocr()
        raw['language_correction'] = True
        with self.assertRaisesRegex(ValueError, 'raw OCR'):
            self.evaluate(ocr=raw)
        review = self.review()
        review['items'][0]['ocr_ids'] = ['o1', 'o1']
        with self.assertRaisesRegex(ValueError, 'mapping'):
            self.evaluate(review, name='duplicate.json')

    def test_visual_fallback_records_why_ocr_is_unavailable(self):
        review = self.review()
        review['items'][0]['evidence'] = 'visual'
        rp = self.save('visual.json', review)
        self.assertEqual(flow.gate(self.candidate, rp, self.root/'incomplete.json')['status'], 'needs_review')
        review['ocr_unavailable_reason'] = 'OCR runtime unavailable; directly inspected full image.'
        rp = self.save('visual-reason.json', review)
        self.assertEqual(flow.gate(self.candidate, rp, self.root/'complete.json')['status'], 'verified')

    def repair(self):
        job = self.root/'job.json'
        patch.crop(self.image, [0, 0, 96, 64], [5, 5, 35, 25], 1, self.root/'crop.png', job)
        request = self.root/'edit-request.json'
        run = self.root/'repair-run.json'
        flow.init_run(self.source, self.image, run)
        flow.freeze(self.source, self.prompt, request, kind='edit', target_ids=['a'], preserve_job=job, run_path=run)
        generated = self.root/'generated.png'
        Image.new('RGBA', (96, 64), (50, 60, 70, 255)).save(generated)
        final, report = self.root/'fixed.png', self.root/'patch.json'
        patch.compose(job, generated, final, report)
        candidate = self.root/'fixed-candidate.json'
        flow.register(request, self.prompt, final, candidate)
        return request, final, report, candidate

    def test_preservation_is_recomputed_against_frozen_region(self):
        request, final, report, candidate = self.repair()
        result = self.evaluate(self.review(final), self.ocr(final), candidate=candidate, report=report)
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['preservation']['changed_pixels_outside'], 0)
        damaged = self.root/'damaged.png'
        im = Image.open(final).convert('RGBA')
        im.putpixel((90, 60), (0, 0, 0, 0))
        im.save(damaged)
        dishonest = json.loads(report.read_text())
        dishonest.update(output_file_sha256=patch.file_hash(damaged), outside_pixels_equal=True, changed_pixels_outside=0)
        bad_report = self.save('dishonest-report.json', dishonest)
        bad_candidate = self.root/'damaged-candidate.json'
        flow.register(request, self.prompt, damaged, bad_candidate)
        result = self.evaluate(self.review(damaged), self.ocr(damaged), 'damaged-gate.json', bad_candidate, bad_report)
        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['preservation']['changed_pixels_outside'], 1)

    def test_preservation_cannot_be_claimed_without_report(self):
        _, final, _, candidate = self.repair()
        self.assertEqual(self.evaluate(self.review(final), self.ocr(final), candidate=candidate)['status'], 'needs_review')

    def test_release_rechecks_evidence_and_preserves_exact_image_bytes(self):
        self.evaluate()
        out = self.root/'final.png'
        result = flow.release(self.root/'gate.json', out)
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(out.read_bytes(), self.image.read_bytes())
        with self.assertRaises(ValueError):
            flow.release(self.root/'gate.json', out)

    def test_changing_status_label_cannot_bypass_release(self):
        result = flow.gate(self.candidate, None, self.root/'unchecked.json')
        result['status'] = 'verified'
        self.save('unchecked.json', result)
        with self.assertRaisesRegex(ValueError, 'blocked'):
            flow.release(self.root/'unchecked.json', self.root/'final.png')
        self.assertFalse((self.root/'final.png').exists())

    def test_stale_image_cannot_be_released(self):
        self.evaluate()
        Image.new('RGBA', (96, 64), (255, 0, 0, 255)).save(self.image)
        with self.assertRaisesRegex(ValueError, 'changed'):
            flow.release(self.root/'gate.json', self.root/'final.png')
        self.assertFalse((self.root/'final.png').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
