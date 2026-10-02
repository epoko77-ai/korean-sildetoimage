"""Offline mechanical tests. These fixtures do not establish visual Hangul accuracy."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from PIL import Image, ImageDraw

import patch_image as patch
import stroke_repair as stroke
import workflow as flow


class StrokeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root/'base.png'
        im = Image.new('RGBA',(96,64),'white')
        draw = ImageDraw.Draw(im)
        draw.rectangle((22,29,42,31),fill=(25,35,45,255))
        draw.rectangle((30,32,31,36),fill=(25,35,45,255))
        im.save(self.base)
        self.source = self.save('source.json',{'items':[{'id':'line','text':'근거'}]})
        self.review = {'image_sha256':patch.file_hash(self.base),'source_sha256':patch.file_hash(self.source),
                       'reviewer':'synthetic mechanical fixture','item_id':'line','status':'incorrect',
                       'evidence':'visual','observed_text':'군거','glyph_box':[20,20,45,50],
                       'context_box':[10,10,80,60],'shape_note':'Fixture spur below horizontal bar.',
                       'background_note':'Known white fixture donors.','design_note':'Do not change the bar.',
                       'segments':[{'axis':'horizontal','fixed':y,'start':30,'end':32} for y in range(32,37)]}

    def save(self,name,value):
        p=self.root/name;p.write_text(json.dumps(value,ensure_ascii=False));return p

    def freeze(self):
        review_path=self.save('stroke-review.json',self.review)
        plan=self.root/'plan.json'
        stroke.plan(self.base,self.source,review_path,plan)
        prompt=self.root/'plan-text.txt';prompt.write_text('근거: remove visually reviewed spur only.')
        request=self.root/'request.json'
        flow.freeze(self.source,prompt,request,kind='local-stroke',target_ids=['line'],stroke_plan=plan)
        return plan,request,prompt

    def candidate(self):
        plan,request,prompt=self.freeze()
        output,report=self.root/'candidate.png',self.root/'patch.json'
        stroke.apply(plan,request,output,report)
        record=self.root/'candidate.json'
        flow.register(request,prompt,output,record)
        return plan,request,prompt,output,report,record

    def final_review(self,image):
        return {'image_sha256':patch.file_hash(image),'source_sha256':patch.file_hash(self.source),
                'reviewer':'synthetic mechanical fixture',
                'items':[{'id':'line','status':'correct','observed_text':'근거','evidence':'visual','bbox':[20,20,65,50]}],
                'inventory':{'status':'complete','unexpected_text':[]},
                'design':{'status':'passed','note':'Synthetic control only.'},
                'ocr_unavailable_reason':'Synthetic nontext geometry tests mechanical invariants.'}

    def test_bounded_apply_and_release_recheck_exact_stroke(self):
        plan,request,prompt,out,report,record=self.candidate()
        metrics=stroke.verify_result(plan,out)
        self.assertTrue(metrics['passed']);self.assertEqual(metrics['changed_pixels_total'],10)
        review=self.save('final-review.json',self.final_review(out))
        gate=self.root/'gate.json'
        self.assertEqual(flow.gate(record,review,gate,patch_report=report)['status'],'verified')
        final=self.root/'final.png';flow.release(gate,final)
        self.assertEqual(final.read_bytes(),out.read_bytes())

    def test_unknown_uncertain_and_normal_are_not_edited(self):
        for status,observed in [('uncertain','군거'),('correct','근거'),('incorrect','근거')]:
            review=copy.deepcopy(self.review);review.update(status=status,observed_text=observed)
            path=self.save('review.json',review)
            with self.assertRaises(ValueError):stroke.plan(self.base,self.source,path,self.root/'rejected.json')
            self.assertFalse((self.root/'rejected.json').exists())

    def test_ocr_only_and_unsupported_component_change_are_rejected(self):
        review=copy.deepcopy(self.review);review['evidence']='ocr'
        with self.assertRaises(ValueError):stroke.plan(self.base,self.source,self.save('ocr-only.json',review),self.root/'bad.json')
        self.source=self.save('source.json',{'items':[{'id':'line','text':'짧은'}]})
        review.update(evidence='visual',observed_text='짦은',source_sha256=patch.file_hash(self.source))
        with self.assertRaisesRegex(ValueError,'Unsupported'):stroke.plan(self.base,self.source,self.save('cluster.json',review),self.root/'bad.json')

    def test_mask_cannot_cross_glyph_or_cover_disconnected_parts(self):
        for segment in [{'axis':'horizontal','fixed':36,'start':44,'end':46},
                        {'axis':'horizontal','fixed':45,'start':38,'end':40}]:
            review=copy.deepcopy(self.review);review['segments'].append(segment)
            with self.assertRaises(ValueError):stroke.plan(self.base,self.source,self.save('bad-review.json',review),self.root/'bad.json')

    def test_mismatched_donor_colors_are_not_smoothed(self):
        im=Image.open(self.base);im.putpixel((29,32),(0,0,0,255));im.save(self.base)
        self.review['image_sha256']=patch.file_hash(self.base)
        with self.assertRaisesRegex(ValueError,'Donors differ'):self.freeze()

    def test_changed_review_or_mask_invalidates_frozen_plan(self):
        plan,request,_=self.freeze()
        p=stroke.read(plan);mask=Image.open(p['mask']['path']);mask.putpixel((28,32),255);mask.save(p['mask']['path'])
        with self.assertRaisesRegex(ValueError,'changed'):stroke.apply(plan,request,self.root/'bad.png',self.root/'bad-report.json')
        self.assertFalse((self.root/'bad.png').exists())

    def test_unfrozen_apply_and_source_target_mismatch_are_rejected(self):
        plan,request,prompt=self.freeze()
        other=self.root/'other-request.json';flow.freeze(self.source,prompt,other,kind='edit')
        with self.assertRaises(ValueError):stroke.apply(plan,other,self.root/'bad.png',self.root/'bad-report.json')
        with self.assertRaises(ValueError):flow.freeze(self.source,prompt,self.root/'bad-request.json',kind='local-stroke',target_ids=[],stroke_plan=plan)

    def test_inside_glyph_outside_mask_and_wrong_inside_pixels_both_fail_gate(self):
        plan,request,prompt,out,report,record=self.candidate()
        for tag,xy in [('outside',(23,24)),('inside',(30,34))]:
            im=Image.open(out);im.putpixel(xy,(0,0,0,255));bad=self.root/(tag+'.png');im.save(bad)
            bad_record=self.root/(tag+'-record.json');flow.register(request,prompt,bad,bad_record)
            forged=stroke.read(report);forged['output_file_sha256']=patch.file_hash(bad)
            bad_report=self.save(tag+'-patch.json',forged)
            review=self.save(tag+'-review.json',self.final_review(bad))
            result=flow.gate(bad_record,review,self.root/(tag+'-gate.json'),patch_report=bad_report)
            self.assertEqual(result['status'],'unresolved')
            self.assertFalse(result['preservation']['stroke_mask']['passed'])

    def ocr(self,text_value='군거'):
        return {'image_sha256':patch.file_hash(self.base),'language_correction':False,
                'observations':[{'observation_id':'o1','text':text_value,'bbox':[20,20,65,50],
                    'characters':[{'index':0,'text':text_value[0],'bbox':[20,20,45,50]},
                                  {'index':1,'text':text_value[1],'bbox':[45,20,65,50]}]}]}

    def test_locator_suggests_real_character_geometry_but_never_confirms_error(self):
        result=stroke.locate(self.base,self.source,self.save('ocr.json',self.ocr()),self.root/'locations.json')
        row=result['items'][0]
        # Two syllables, one substitution: below minimum matching similarity; remain unlocated.
        self.assertEqual(row['status'],'needs_visual_mapping')
        self.source=self.save('long-source.json',{'items':[{'id':'line','text':'근거 자료'}]})
        ocr=self.ocr();ocr['observations'][0].update(text='군거 자료')
        ocr['observations'][0]['characters'] += [{'index':i,'text':c} for i,c in enumerate(' 자료',2)]
        result=stroke.locate(self.base,self.source,self.save('long-ocr.json',ocr),self.root/'long-locations.json')
        row=result['items'][0];self.assertEqual(row['status'],'needs_visual_review')
        self.assertEqual(row['differences'][0]['glyph_bbox'],[20,20,45,50])

    def test_locator_preserves_normal_and_ambiguous_occurrences(self):
        normal=self.ocr('근거')
        result=stroke.locate(self.base,self.source,self.save('normal-ocr.json',normal),self.root/'normal.json')
        self.assertEqual(result['items'][0]['status'],'ocr_agrees_visual_review_required')
        self.assertEqual(result['items'][0]['differences'],[])
        normal['observations'].append({**normal['observations'][0],'observation_id':'o2'})
        result=stroke.locate(self.base,self.source,self.save('duplicate-ocr.json',normal),self.root/'duplicate.json')
        self.assertEqual(result['items'][0]['status'],'needs_visual_mapping')

    def test_stale_or_language_corrected_ocr_rejected(self):
        for key,value in [('image_sha256','wrong'),('language_correction',True)]:
            ocr=self.ocr();ocr[key]=value
            with self.assertRaises(ValueError):stroke.locate(self.base,self.source,self.save('bad-ocr.json',ocr),self.root/'bad-locations.json')

    def test_invalid_ocr_geometry_keeps_text_difference_and_visual_fallback(self):
        # Vision can emit a zero-area box for a space. One bad hint must not
        # discard the line's copy difference or stop review of the other items.
        self.source=self.save('source.json',{'items':[{'id':'line','text':'한글A문장'}]})
        for n,bad_box in enumerate(([0,0,0,0],[-1,10,12,30],[20,20,200,40],None)):
            with self.subTest(box=bad_box):
                ocr=self.ocr();obs=ocr['observations'][0]
                obs.update(text='한글 문장',bbox=bad_box,characters=[
                    {'index':i,'text':c,'bbox':bad_box if c==' ' else [10+i*12,10,22+i*12,35]}
                    for i,c in enumerate('한글 문장')])
                result=stroke.locate(self.base,self.source,self.save(f'geometry-{n}.json',ocr),self.root/f'locations-{n}.json')
                row=result['items'][0]
                self.assertEqual(row['status'],'needs_visual_review')
                self.assertIsNone(row['context_bbox'])
                finding=row['differences'][0]
                self.assertEqual((finding['expected'],finding['observed']),('A',' '))
                self.assertIsNone(finding['glyph_bbox'])
                self.assertEqual(finding['localization'],'needs_visual_location')


if __name__=='__main__':unittest.main()
