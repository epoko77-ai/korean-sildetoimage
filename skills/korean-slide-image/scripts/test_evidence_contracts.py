"""Control-flow and pixel contracts, not tests of visual Korean recognition."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from PIL import Image

import glyph_inspect as glyph
import patch_image as patch
import workflow as flow


class EvidenceContracts(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.base = self.root/'base.png'
        Image.new('RGBA', (400, 100), 'white').save(self.base)
        self.source = self.save('source.json', {'items': [{'id': 'line', 'text': '근거 자료'}]})
        self.prompt = self.root/'prompt.txt'
        self.prompt.write_text('근거 자료')
        self.run = self.root/'run.json'
        flow.init_run(self.source, self.base, self.run)
        self.request = self.root/'request.json'
        flow.freeze(self.source, self.prompt, self.request)

    def save(self, name, value):
        path = self.root/name
        path.write_text(json.dumps(value, ensure_ascii=False))
        return path

    def review(self, image):
        return {'image_sha256': patch.file_hash(image), 'source_sha256': patch.file_hash(self.source),
                'reviewer': 'synthetic control-flow fixture; not visual evidence',
                'items': [{'id': 'line', 'observed_text': '근거 자료', 'status': 'correct',
                           'evidence': 'visual', 'bbox': [10, 10, 250, 60]}],
                'inventory': {'status': 'complete', 'unexpected_text': []},
                'design': {'status': 'passed', 'note': 'Synthetic mechanics only'},
                'ocr_unavailable_reason': 'Synthetic fixture, no recognition claim'}

    def manual(self):
        return {'kind': 'glyph-manual-locations', 'image_sha256': patch.file_hash(self.base),
                'source_sha256': patch.file_hash(self.source), 'reviewer': 'fixture locator',
                'entries': [{'item_id': 'line', 'expected_index': 0, 'glyph_bbox': [50, 20, 85, 65],
                             'note': 'Known fixture coordinates, not a visual claim'}]}

    def ocr(self):
        return {'image_sha256': patch.file_hash(self.base), 'language_correction': False,
                'observations': [{'observation_id': 'o1', 'text': '근거 자료', 'bbox': [40, 20, 300, 65],
                                  'characters': [{'index': i, 'text': c, 'bbox': [50+i*40, 20, 85+i*40, 65]}
                                                 for i, c in enumerate('근거 자료')]}]}

    def inspect(self, invalid_geometry=False):
        raw = self.ocr()
        if invalid_geometry:
            raw['observations'][0]['characters'][0]['bbox'] = [0, 0, 0, 0]
        packet = glyph.prepare(self.base, self.source, self.save('ocr.json', raw), self.root/'inspection')
        packet_path = self.root/'inspection/packet.json'
        candidate = self.root/'inspected.json'
        flow.register(self.request, self.prompt, self.base, candidate, [packet_path])
        readings = {'blind_sha256': packet['blind']['sha256'], 'reviewer': 'fixture reader',
                    'entries': [{'id': r['id'], 'status': 'readable', 'observed_text': r['expected']}
                                for r in packet['entries']]}
        return packet, packet_path, candidate, readings

    def resolution(self, row, status='correct'):
        return {'item_id': row['item_id'], 'expected_index': row['expected_index'],
                'status': status, 'observed_text': row['expected'], 'bbox': [50, 20, 85, 65],
                'note': 'Synthetic explicit context adjudication'}

    def patch(self, name, base, edit, previous=()):
        job, crop = self.root/(name+'-job.json'), self.root/(name+'-crop.png')
        patch.crop(base, [0, 0, 400, 100], edit, 1, crop, job)
        request = self.root/(name+'-request.json')
        flow.freeze(self.source, self.prompt, request, kind='edit', preserve_job=job,
                    run_path=self.run, prior_patches=previous)
        paint = self.root/(name+'-paint.png')
        Image.new('RGBA', (400, 100), (20, 30, 40, 255)).save(paint)
        out, report = self.root/(name+'.png'), self.root/(name+'-patch.json')
        patch.compose(job, paint, out, report)
        candidate = self.root/(name+'-candidate.json')
        flow.register(request, self.prompt, out, candidate)
        return out, report, candidate, request

    def test_manual_only_cards_bind_source_without_exposing_answer(self):
        manual = self.save('manual.json', self.manual())
        packet = glyph.prepare(self.base, self.source, None, self.root/'cards', manual)
        glyph.validate_packet(self.root/'cards/packet.json')
        self.assertEqual(len(packet['entries']), 1)
        self.assertEqual(packet['unmapped'], [])
        self.assertIsNone(packet['ocr'])
        self.assertEqual(packet['entries'][0]['localization'], 'manual_visual_hint')
        blind = json.loads(Path(packet['blind']['path']).read_text())
        self.assertNotIn('expected', blind['entries'][0])
        self.assertNotIn('근', json.dumps(blind, ensure_ascii=False))
        self.assertEqual(Image.open(self.base).getpixel((50, 20)), (255, 255, 255, 255))

    def test_manual_override_retains_ocr_hint(self):
        manual = self.manual()
        manual['entries'][0]['glyph_bbox'] = [70, 20, 105, 65]
        packet = glyph.prepare(self.base, self.source, self.save('ocr.json', self.ocr()),
                               self.root/'cards', self.save('manual.json', manual))
        row = next(r for r in packet['entries'] if r['expected_index'] == 0)
        self.assertEqual(row['glyph_bbox'], [70, 20, 105, 65])
        self.assertEqual(row['ocr_glyph_bbox'], [50, 20, 85, 65])

    def test_invalid_manual_bindings_indices_and_geometry_are_rejected(self):
        variants = [copy.deepcopy(self.manual()) for _ in range(6)]
        variants[0]['image_sha256'] = 'stale'
        variants[1]['source_sha256'] = 'stale'
        variants[2]['entries'].append(copy.deepcopy(variants[2]['entries'][0]))
        variants[3]['entries'][0]['expected_index'] = 2  # space
        variants[4]['entries'][0]['glyph_bbox'] = [0, 0, 300, 30]
        variants[5]['entries'][0]['glyph_bbox'] = [0, 0, 401, 100]
        for i, variant in enumerate(variants):
            with self.subTest(i=i), self.assertRaises(ValueError):
                glyph.prepare(self.base, self.source, None, self.root/f'bad-{i}',
                              self.save(f'manual-{i}.json', variant))

    def test_packet_cannot_drop_unmapped_target_or_change_expected_text(self):
        packet, path, _, _ = self.inspect(invalid_geometry=True)
        variants = [copy.deepcopy(packet), copy.deepcopy(packet)]
        variants[0]['unmapped'] = []
        variants[1]['entries'][0]['expected'] = '다'
        for i, variant in enumerate(variants):
            with self.subTest(i=i), self.assertRaises(ValueError):
                glyph.validate_packet(self.save(f'tampered-{i}.json', variant))

    def test_selected_inspection_cannot_be_omitted_by_positive_review(self):
        _, _, candidate, _ = self.inspect()
        gate = self.root/'gate.json'
        result = flow.gate(candidate, self.save('review.json', self.review(self.base)), gate)
        self.assertEqual(result['status'], 'needs_review')
        with self.assertRaisesRegex(ValueError, 'blocked'):
            flow.release(gate, self.root/'final.png')

    def test_uncertain_and_missing_readings_require_individual_adjudication(self):
        packet, path, candidate, readings = self.inspect()
        readings['entries'] = [{'id': packet['entries'][0]['id'], 'status': 'uncertain', 'observed_text': None}]
        rp = self.save('readings.json', readings)
        review = self.review(self.base)
        row = {'packet_sha256': patch.file_hash(path), 'readings': flow.bound(rp), 'resolutions': []}
        review['glyph_inspections'] = [row]
        for count, status in [(0, 'needs_review'), (1, 'needs_review'), (2, 'verified')]:
            row['resolutions'] = [self.resolution(r) for r in packet['entries'][:count]]
            result = flow.gate(candidate, self.save(f'review-{count}.json', review), self.root/f'gate-{count}.json')
            self.assertEqual(result['status'], status)
        receipt = flow.release(self.root/'gate-2.json', self.root/'final.png')
        self.assertEqual(receipt['glyph_inspections']['selected'], 1)

    def test_unmapped_target_cannot_disappear_after_other_cards_agree(self):
        packet, path, candidate, readings = self.inspect(invalid_geometry=True)
        review = self.review(self.base)
        review['glyph_inspections'] = [{'packet_sha256': patch.file_hash(path),
                                       'readings': flow.bound(self.save('readings.json', readings))}]
        result = flow.gate(candidate, self.save('review.json', review), self.root/'gate.json')
        self.assertEqual(result['status'], 'needs_review')
        review['glyph_inspections'][0]['resolutions'] = [self.resolution(packet['unmapped'][0], 'incorrect')]
        result = flow.gate(candidate, self.save('review2.json', review), self.root/'gate2.json')
        self.assertEqual(result['status'], 'unresolved')

    def test_reading_mutation_after_gate_blocks_release(self):
        _, path, candidate, readings = self.inspect()
        rp = self.save('readings.json', readings)
        review = self.review(self.base)
        review['glyph_inspections'] = [{'packet_sha256': patch.file_hash(path), 'readings': flow.bound(rp)}]
        flow.gate(candidate, self.save('review.json', review), self.root/'gate.json')
        rp.write_text(rp.read_text()+' ')
        with self.assertRaisesRegex(ValueError, 'changed'):
            flow.release(self.root/'gate.json', self.root/'final.png')

    def test_deleted_readings_after_gate_block_release_with_evidence_error(self):
        _, path, candidate, readings = self.inspect()
        rp = self.save('readings.json', readings)
        review = self.review(self.base)
        review['glyph_inspections'] = [{'packet_sha256': patch.file_hash(path), 'readings': flow.bound(rp)}]
        flow.gate(candidate, self.save('review.json', review), self.root/'gate.json')
        rp.unlink()
        with self.assertRaisesRegex(ValueError, 'missing or changed'):
            flow.release(self.root/'gate.json', self.root/'final.png')

    def test_different_glyph_reading_requires_consistent_context_resolution(self):
        packet, path, candidate, readings = self.inspect()
        target = packet['entries'][0]
        readings['entries'][0]['observed_text'] = '곤'
        review = self.review(self.base)
        record = {'packet_sha256': patch.file_hash(path),
                  'readings': flow.bound(self.save('readings.json', readings))}
        review['glyph_inspections'] = [record]
        result = flow.gate(candidate, self.save('missing.json', review), self.root/'missing-gate.json')
        self.assertEqual(result['status'], 'needs_review')
        decision = self.resolution(target)
        decision['observed_text'] = '곤'
        record['resolutions'] = [decision]
        result = flow.gate(candidate, self.save('conflict.json', review), self.root/'conflict-gate.json')
        self.assertEqual(result['status'], 'needs_review')
        decision['observed_text'] = target['expected']
        result = flow.gate(candidate, self.save('resolved.json', review), self.root/'resolved-gate.json')
        self.assertEqual(result['status'], 'verified')

    def test_unrequested_resolution_is_rejected(self):
        packet, path, candidate, readings = self.inspect()
        review = self.review(self.base)
        review['glyph_inspections'] = [{'packet_sha256': patch.file_hash(path),
            'readings': flow.bound(self.save('readings.json', readings)),
            'resolutions': [self.resolution(packet['entries'][0])]}]
        with self.assertRaisesRegex(ValueError, 'Unknown'):
            flow.gate(candidate, self.save('review.json', review), self.root/'gate.json')

    def test_inspection_from_another_image_is_rejected_at_registration(self):
        _, path, _, _ = self.inspect()
        other = self.root/'other.png'
        Image.new('RGBA', (400, 100), 'black').save(other)
        with self.assertRaisesRegex(ValueError, 'another image'):
            flow.register(self.request, self.prompt, other, self.root/'other.json', [path])

    def test_new_composited_request_requires_original_contract(self):
        job = self.root/'job.json'
        patch.crop(self.base, [0, 0, 400, 100], [10, 10, 20, 20], 1, self.root/'crop.png', job)
        with self.assertRaisesRegex(ValueError, 'frozen original'):
            flow.freeze(self.source, self.prompt, self.root/'bad.json', kind='edit', preserve_job=job)

    def test_version_two_request_cannot_drop_its_original_at_registration(self):
        final, _, _, request = self.patch('first', self.base, [10, 10, 20, 20])
        data = json.loads(request.read_text())
        data.pop('repair_run')
        with self.assertRaisesRegex(ValueError, 'frozen original'):
            flow.register(self.save('missing-origin.json', data), self.prompt, final, self.root/'bad.json')

    def test_correct_two_step_chain_passes_and_receipt_names_scope(self):
        first, report1, _, _ = self.patch('first', self.base, [10, 10, 20, 20])
        final, report2, candidate, _ = self.patch('second', first, [40, 10, 50, 20], [report1])
        result = flow.gate(candidate, self.save('review.json', self.review(final)),
                           self.root/'gate.json', patch_report=report2)
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['preservation']['cumulative']['steps'], 2)
        self.assertEqual(result['preservation']['cumulative']['changed_pixels_outside'], 0)
        receipt = flow.release(self.root/'gate.json', self.root/'final.png')
        self.assertEqual(receipt['preservation_scope'], 'cumulative')

    def test_missing_prior_report_cannot_silently_start_a_new_origin(self):
        first, _, _, _ = self.patch('first', self.base, [10, 10, 20, 20])
        with self.assertRaisesRegex(ValueError, 'does not reach'):
            self.patch('second', first, [40, 10, 50, 20])

    def test_intermediate_extra_pixel_is_rejected_before_next_patch(self):
        first, report, _, _ = self.patch('first', self.base, [10, 10, 20, 20])
        im = Image.open(first).convert('RGBA')
        im.putpixel((390, 90), (1, 2, 3, 255))
        damaged = self.root/'damaged.png'
        im.save(damaged)
        with self.assertRaisesRegex(ValueError, 'does not reach'):
            self.patch('second', damaged, [40, 10, 50, 20], [report])

    def test_prior_report_mutation_after_gate_blocks_release(self):
        first, report1, _, _ = self.patch('first', self.base, [10, 10, 20, 20])
        final, report2, candidate, _ = self.patch('second', first, [40, 10, 50, 20], [report1])
        flow.gate(candidate, self.save('review.json', self.review(final)), self.root/'gate.json', patch_report=report2)
        report1.write_text(report1.read_text()+' ')
        with self.assertRaisesRegex(ValueError, 'changed'):
            flow.release(self.root/'gate.json', self.root/'final.png')

    def test_reordered_reports_are_rejected(self):
        first, report1, _, _ = self.patch('first', self.base, [10, 10, 20, 20])
        final, report2, _, _ = self.patch('second', first, [40, 10, 50, 20], [report1])
        with self.assertRaisesRegex(ValueError, 'out of order'):
            patch.audit_chain(self.base, final, [report2, report1])

    def test_legacy_single_step_receipt_does_not_claim_cumulative_preservation(self):
        first, report1, _, _ = self.patch('first', self.base, [10, 10, 20, 20])
        final, report, _, request = self.patch('second', first, [40, 10, 50, 20], [report1])
        legacy = json.loads(request.read_text())
        legacy['schema_version'] = 1
        legacy.pop('repair_run')
        legacy.pop('prior_patches')
        request = self.save('legacy-request.json', legacy)
        candidate = self.root/'legacy-candidate.json'
        flow.register(request, self.prompt, final, candidate)
        flow.gate(candidate, self.save('review.json', self.review(final)), self.root/'gate.json', patch_report=report)
        receipt = flow.release(self.root/'gate.json', self.root/'final.png')
        self.assertEqual(receipt['preservation_scope'], 'single_step_legacy')

    def test_stroke_then_patch_chain_validates_exact_binary_stroke_mask(self):
        from test_stroke_repair import StrokeTests
        fixture = StrokeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        plan, _, _, first, report1, _ = fixture.candidate()
        job = fixture.root/'second-job.json'
        patch.crop(first, [0, 0, 96, 64], [70, 40, 75, 45], 1, fixture.root/'second-crop.png', job)
        paint = fixture.root/'paint.png'
        Image.new('RGBA', (96, 64), 'black').save(paint)
        final, report2 = fixture.root/'second.png', fixture.root/'second-report.json'
        patch.compose(job, paint, final, report2)
        result = patch.audit_chain(fixture.base, final, [report1, report2])
        self.assertTrue(result['passed'])
        self.assertEqual(result['steps'], 2)
        self.assertEqual(result['changed_pixels_total'], 35)
        frozen = json.loads(plan.read_text())
        mask_path = Path(frozen['mask']['path'])
        mask = Image.open(mask_path).convert('L')
        mask.putpixel((30, 32), 128)
        mask.save(mask_path)
        frozen['mask']['sha256'] = patch.file_hash(mask_path)
        plan.write_text(json.dumps(frozen))
        report = json.loads(report1.read_text())
        report['stroke_plan'] = flow.bound(plan)
        report1.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, 'Stroke mask differs'):
            patch.audit_chain(fixture.base, final, [report1, report2])


if __name__ == '__main__':
    unittest.main()
