"""Mechanical checks, not a claim of visual recognition accuracy."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from PIL import Image, ImageDraw

import glyph_inspect as inspect
import patch_image as patch
import stroke_repair as stroke


class InspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root/'base.png'
        im = Image.new('RGB',(400,100),'white')
        ImageDraw.Draw(im).rectangle((96,38,105,42),fill='black')
        im.save(self.base)
        self.source = self.save('source.json',{'items':[{'id':'line','text':'근거 자료'}]})
        self.ocr_data = {'image_sha256':patch.file_hash(self.base),'language_correction':False,
                        'observations':[{'observation_id':'o1','text':'근거 자료','bbox':[40,20,300,65],
                            'characters':[{'index':i,'text':c,'bbox':[50+i*40,20,85+i*40,65]}
                                          for i,c in enumerate('근거 자료')]}]}
        self.ocr = self.save('ocr.json',self.ocr_data)

    def save(self,name,value):
        path = self.root/name
        path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
        return path

    def prepare(self):
        packet = inspect.prepare(self.base,self.source,self.ocr,self.root/'inspection')
        return packet,self.root/'inspection/packet.json'

    def readings(self,packet):
        return {'blind_sha256':packet['blind']['sha256'],'reviewer':'mechanical fixture',
                'entries':[{'id':row['id'],'status':'readable','observed_text':row['expected']}
                           for row in packet['entries']]}

    def test_ocr_agreement_still_gets_risk_glyph_cards_and_original_is_unchanged(self):
        before = self.base.read_bytes()
        packet,_ = self.prepare()
        self.assertEqual({e['expected'] for e in packet['entries']},{'근','거'})
        self.assertEqual(packet['unmapped'],[])
        blind = stroke.read(packet['blind']['path'])
        for row in blind['entries']:
            self.assertEqual(set(row),{'id','card','sheet','slot'})
        self.assertEqual(before,self.base.read_bytes())
        self.assertEqual(packet['status'],'needs_review')

    def test_halo_preserves_pixels_outside_inaccurate_hint(self):
        packet,_ = self.prepare()
        entry = next(e for e in packet['entries'] if e['expected']=='근')
        self.assertLess(entry['glyph_bbox'][2],106)
        self.assertGreaterEqual(entry['crop_box'][2],106)
        expected,_ = inspect.card(patch.load_image(self.base),entry['glyph_bbox'],entry['crop_box'],entry['id'])
        self.assertEqual(expected.tobytes(),Image.open(entry['card']['path']).tobytes())

    def test_invalid_geometry_and_ambiguous_line_stay_unmapped(self):
        self.ocr_data['observations'][0]['characters'][0]['bbox'] = [0,0,0,0]
        self.save('ocr.json',self.ocr_data)
        packet,_ = self.prepare()
        self.assertEqual(len(packet['unmapped']),1)
        self.assertEqual(packet['unmapped'][0]['expected'],'근')
        self.ocr_data['observations'].append({**self.ocr_data['observations'][0],'observation_id':'o2'})
        self.save('ocr.json',self.ocr_data)
        packet = inspect.prepare(self.base,self.source,self.ocr,self.root/'ambiguous')
        self.assertEqual(packet['entries'],[])
        self.assertEqual(len(packet['unmapped']),2)

    def test_nonrisk_ocr_difference_is_included_when_aligned(self):
        self.ocr_data['observations'][0]['text'] = '근거 자로'
        self.ocr_data['observations'][0]['characters'][-1]['text'] = '로'
        self.save('ocr.json',self.ocr_data)
        packet,_ = self.prepare()
        entry = next(e for e in packet['entries'] if e['expected']=='료')
        self.assertEqual(entry['reason'],'ocr_difference')
        self.assertEqual(entry['raw_ocr'],'로')

    def test_isolated_reading_can_disagree_with_matching_ocr_without_approving_repair(self):
        packet,path = self.prepare()
        readings = self.readings(packet)
        row = next(r for r in readings['entries'] if r['observed_text']=='근')
        row['observed_text'] = '군'
        result = inspect.compare(path,self.save('readings.json',readings),self.root/'findings.json')
        self.assertEqual(result['counts']['possible_difference'],1)
        self.assertEqual(result['status'],'needs_review')
        self.assertFalse(result['automatic_repair'])
        self.assertTrue(result['context_confirmation_required'])

    def test_missing_or_uncertain_readings_do_not_pass(self):
        packet,path = self.prepare()
        readings = self.readings(packet)
        readings['entries'].pop()
        readings['entries'][0].update(status='uncertain',observed_text=None)
        result = inspect.compare(path,self.save('readings.json',readings),self.root/'findings.json')
        self.assertEqual(result['counts']['needs_visual_review'],2)
        self.assertEqual(result['counts']['agrees'],0)

    def test_unknown_duplicate_or_other_cards_readings_are_rejected(self):
        packet,path = self.prepare()
        original = self.readings(packet)
        variants = [copy.deepcopy(original) for _ in range(3)]
        variants[0]['entries'][0]['id'] = 'unknown'
        variants[1]['entries'].append(copy.deepcopy(variants[1]['entries'][0]))
        variants[2]['blind_sha256'] = 'other'
        for i,reading in enumerate(variants):
            with self.assertRaises(ValueError):
                inspect.compare(path,self.save(f'readings-{i}.json',reading),self.root/f'rejected-{i}.json')

    def test_changed_source_image_card_or_sheet_invalidates_readings(self):
        packet,path = self.prepare()
        readings = self.save('readings.json',self.readings(packet))
        blind = stroke.read(packet['blind']['path'])
        for ref in (packet['source'],packet['image'],packet['entries'][0]['card'],blind['sheets'][0]):
            file = Path(ref['path']);before = file.read_bytes()
            file.write_bytes(before+b'changed')
            with self.assertRaisesRegex(ValueError,'changed'):
                inspect.compare(path,readings,self.root/'rejected.json')
            file.write_bytes(before)


if __name__ == '__main__':
    unittest.main()
