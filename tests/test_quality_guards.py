import array
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave
import grounded_index
import index_pipeline
import media_index
import speech_quality


class QualityGuardTests(unittest.TestCase):

    def test_numeric_ocr_accepts_literal_numbers_next_to_chinese_labels(self):
        box=[.1,.2,.3,.1]
        for text,line in [('08:45','样例场次08:45英语3D'),('18:10散场','18:10散场6号厅'),
                          ('报告08:45','5G报告08:45'),('英语3D','08:45英语3D票价'),
                          ('27.75','样例价格27.75元含税')]:
            result={'text':text,'regions':[{'text':text,'bbox':box}],
                    'native_candidates':[{'text':text,'bbox':box}],'uncertainty':''}
            with self.subTest(text=text,line=line):
                out=grounded_index.enforce_numeric_ocr_consensus(result,[line])
                self.assertEqual(out['text'],text)
                self.assertEqual(out['numeric_consensus']['omitted_regions'],0)

    def test_numeric_ocr_allows_yen_width_equivalence_without_rewriting_literal(self):
        box=[.1,.2,.3,.1]
        result={'text':'￥27.75','regions':[{'text':'￥27.75','bbox':box}],
                'native_candidates':[{'text':'¥27.75','bbox':box}],'uncertainty':''}
        out=grounded_index.enforce_numeric_ocr_consensus(result,['样例价¥27.75元'])
        self.assertEqual(out['text'],'￥27.75')
        self.assertEqual(grounded_index.numeric_ocr_key('￥27.75'),grounded_index.numeric_ocr_key('¥27.75'))
        self.assertNotEqual(grounded_index.numeric_ocr_key('￥27.75'),grounded_index.numeric_ocr_key('$27.75'))

    def test_chinese_numeric_boundary_fix_still_rejects_number_fragments(self):
        box=[.1,.2,.3,.1]
        pairs=[('8','18元'),('8','8.75元'),('8','−8元'),('8','8²元'),('8','8⁻元'),
               ('8','8₂元'),('8','AB8C'),('8','α8元'),('8','08:45'),
               ('$8','样例$8.75元'),('27.75','27.750元')]
        for text,line in pairs:
            result={'text':text,'regions':[{'text':text,'bbox':box}],
                    'native_candidates':[{'text':text,'bbox':box}],'uncertainty':''}
            with self.subTest(text=text,line=line):
                self.assertEqual(grounded_index.enforce_numeric_ocr_consensus(result,[line])['text'],'')


    def test_exhausted_visual_review_uses_fresh_partial_class_evidence(self):
        import copy,json
        from PIL import Image
        bad={'summary':'合成图里有未经支持的细节。','description':'合成图里有未经支持的细节。',
             'objects':['未经支持的细节'],'spatial_relations':[],'tags':['细节'],'ocr_text':'',
             'uncertainty_note':'','has_readable_text':False}
        rejected={'approved':False,'issues':['细节缺少证据'],'revised':bad}
        first={'objects':['器物','人物'],'has_readable_text':False}
        second={'objects':['器物','动物'],'has_readable_text':False}
        responses=[bad,rejected,rejected,rejected,bad,rejected,first,second]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic.jpg';Image.new('RGB',(800,600),'white').save(path)
            with patch.object(grounded_index,'native_face_regions',return_value={'detected_regions':0,'largest_area_fraction':0}),patch.object(grounded_index,'call',side_effect=copy.deepcopy(responses)) as model:
                out=grounded_index._describe_grounded(path,object())
            audit=json.loads(path.with_suffix('.visual-audit.json').read_text())
        self.assertEqual(model.call_count,8)
        self.assertEqual(out['objects'],['器物'])
        self.assertNotIn('未经支持的细节',str(out))
        self.assertNotIn('人物',out['objects'])
        self.assertEqual(len(audit['reviews']),4)
        self.assertTrue(out['verification']['detail_review_exhausted'])
        self.assertEqual(index_pipeline.apply_analysis_status(out)['index_status'],'partial')
        self.assertEqual(out['verification']['visual_coverage'],'limited')

    def test_disputed_scene_without_blind_agreement_stays_failed(self):
        first={'objects':['器物'],'has_readable_text':False}
        second={'objects':['动物'],'has_readable_text':False}
        with patch.object(grounded_index,'call',side_effect=[first,second]),self.assertRaises(media_index.IndexErrorUser):
            grounded_index.describe_disputed_scene(Path('synthetic.jpg'),object())


    def test_portable_summary_uses_every_sample_without_generative_inference(self):
        frames=[{'at_ms':0,'summary':'合成画面可见红色圆形。'},
                {'at_ms':5000,'summary':'合成画面可见蓝色方块。'},
                {'at_ms':8500,'summary':'合成画面可见绿色三角形。'}]
        with patch.object(grounded_index,'call',side_effect=AssertionError('No generative cross-frame inference is allowed')):
            text=grounded_index.summarize(frames,[{'text':'synthetic spoken words'}],9000,object())
        for f in frames:
            self.assertIn(media_index.time_label(f['at_ms'])+' 采样：'+f['summary'],text)
        self.assertLess(text.index('红色圆形'),text.index('蓝色方块'))
        self.assertLess(text.index('蓝色方块'),text.index('绿色三角形'))
        self.assertNotIn('synthetic spoken words',text)
        self.assertIn('语音见 transcript',text)


    def test_numeric_ocr_preserves_decimal_and_script_distinctions(self):
        key=grounded_index.numeric_ocr_key
        self.assertNotEqual(key('$8¹²'),key('$812'))
        self.assertNotEqual(key('x₂'),key('x2'))
        self.assertNotEqual(key('8.12'),key('812'))
        self.assertEqual(key('ＡＢＣ １２'),key('ABC12'))

    def test_numeric_ocr_rejects_two_reader_numeric_format_error(self):
        box=[.1,.2,.3,.1]
        result={'text':'SYNTHETIC\n$812','regions':[{'text':'SYNTHETIC','bbox':[0,0,1,.1]},{'text':'$812','bbox':box}],
                'native_candidates':[{'text':'$812','bbox':box}],'uncertainty':'synthetic coverage'}
        out=grounded_index.enforce_numeric_ocr_consensus(result,['SYNTHETIC','STARTING AT $8.12'])
        self.assertEqual(out['text'],'SYNTHETIC')
        self.assertEqual(out['numeric_consensus']['omitted_regions'],1)
        self.assertIn('$812',result['text'])
        self.assertNotIn('$8.12',out['text'])

    def test_numeric_ocr_keeps_literal_agreement_only_from_same_region(self):
        box=[.1,.2,.3,.1]
        result={'text':'$32.60','regions':[{'text':'$32.60','bbox':box}],
                'native_candidates':[{'text':'$32.60','bbox':box}],'uncertainty':''}
        out=grounded_index.enforce_numeric_ocr_consensus(result,['TOTAL $32.60'])
        self.assertEqual(out['text'],'$32.60')
        self.assertEqual(out['numeric_consensus']['omitted_regions'],0)
        result['native_candidates'][0]['bbox']=[.5,.5,.2,.1]
        self.assertEqual(grounded_index.enforce_numeric_ocr_consensus(result,['TOTAL $32.60'])['text'],'')
        result['native_candidates'][0]['bbox']=box
        self.assertEqual(grounded_index.enforce_numeric_ocr_consensus(result,['TOTAL $32.600'])['text'],'')

    def test_ocr_entrypoint_checks_postcondition_after_independent_reading(self):
        import json
        box=[.1,.2,.3,.1]
        result={'text':'$812','regions':[{'text':'$812','bbox':box}],
                'native_candidates':[{'text':'$812','bbox':box}],'uncertainty':''}
        with tempfile.TemporaryDirectory() as folder:
            image=Path(folder)/'synthetic.png'
            image.with_suffix('.ocr-candidates.json').write_text(json.dumps({'lines':['SYNTHETIC $8.12']}))
            with patch.object(grounded_index,'_transcribe_visible_text',return_value=result,create=True) as previous:
                out=grounded_index.transcribe_visible_text(image,object())
            self.assertEqual(previous.call_count,1)
            self.assertEqual(out['text'],'')
            self.assertEqual(out['numeric_consensus']['omitted_regions'],1)

    def test_visual_entrypoint_omits_inferred_device_audience_but_keeps_literal_ocr(self):
        text='老年人专用电动轮椅'
        record={'summary':'合成画面可见'+text+'。','description':'合成画面中央有一把'+text+'。',
                'objects':[text],'spatial_relations':[text+'位于中央'],'tags':[text],
                'ocr_text':text,'uncertainty_note':'','verification':{}}
        with patch.object(grounded_index,'_describe_grounded',return_value=record),patch.object(grounded_index,'enforce_dim_detail_limits',side_effect=lambda p,r,a:r):
            out=grounded_index.describe_grounded(Path('synthetic.png'),object())
        for field in ('summary','description','objects','spatial_relations','tags'):
            self.assertNotIn('老年人',str(out[field]))
        self.assertIn('电动轮椅',out['summary'])
        self.assertEqual(out['ocr_text'],text)
        self.assertIn('老年人',record['summary'])
        self.assertTrue(out['verification']['equipment_audience_omitted'])

    def test_video_summary_cannot_restore_unverified_device_audience(self):
        frames=[{'at_ms':0,'summary':'合成画面可见电动轮椅。','description':'电动轮椅位于中央。'}]
        with patch.object(grounded_index,'call',side_effect=[{'summary':'合成画面可见老年人专用电动轮椅。'},{'approved':True,'issues':[]}]):
            result=grounded_index.summarize(frames,[],3000,object())
        self.assertIn('证据摘要摘录',result)
        self.assertIn('电动轮椅',result)
        self.assertNotIn('老年人',result)

    def test_image_plane_policy_covers_prose_without_losing_confirmed_subjects(self):
        record={'summary':'红色圆形位于蓝色方块后方。','description':'画面可见红色圆形。蓝色方块前方有绿色三角形。',
                'objects':['红色圆形','蓝色方块','绿色三角形'],'spatial_relations':['圆形位于方块后方'],'tags':['合成图形'],
                'ocr_text':'前方','uncertainty_note':'','verification':{}}
        out=grounded_index.omit_unverified_depth_relations(record)
        self.assertTrue(grounded_index.summary_has_unverified_scope('合成圆形位于方块前方。',[]))
        self.assertNotIn('后方',out['summary'])
        self.assertNotIn('前方',out['description'])
        self.assertEqual(out['objects'],record['objects'])
        self.assertEqual(out['spatial_relations'],[])
        self.assertEqual(out['ocr_text'],'前方')
        self.assertIn('红色圆形',out['summary'])
        self.assertIn('蓝色方块',out['summary'])
        self.assertIn('绿色三角形',out['summary'])
        self.assertTrue(out['verification']['unverified_depth_clauses_omitted'])



    def test_summary_cannot_turn_missing_labels_into_disappearance_or_continuity(self):
        frames=[{'at_ms':0,'summary':'采样画面可见红色圆形。','description':'画面可见红色圆形。'},
                {'at_ms':5000,'summary':'采样画面可见蓝色方块。','description':'画面可见蓝色方块。'},
                {'at_ms':10000,'summary':'采样画面可见红色圆形。','description':'画面可见红色圆形。'}]
        for text in ['圆形随后消失。','方块始终位于右侧。','后段没有圆形。','圆形不再出现。','全程没有声音。','合成视频由四个不同的镜头构成。']:
            with self.subTest(text=text),patch.object(grounded_index,'call',side_effect=[{'summary':text},{'approved':True,'issues':[]}]):
                result=grounded_index.summarize(frames,[],11000,object())
            self.assertIn('证据摘要摘录',result);self.assertNotIn(text,result)
            for frame in frames:self.assertIn(frame['summary'],result)
        self.assertFalse(grounded_index.summary_has_unverified_scope('采样画面可见图形；全程无可靠转录文本。',[]))
        self.assertTrue(grounded_index.summary_has_unverified_scope('全程无可靠转录文本。',[{'text':'synthetic speech'}]))


    def test_dim_photometric_view_preserves_geometry_and_original(self):
        import hashlib
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic.jpg';Image.new('RGB',(71,53),(32,32,32)).save(path)
            original=hashlib.sha256(path.read_bytes()).digest()
            raw,view=grounded_index.dim_detail_views(path)
            self.assertEqual(raw,path);self.assertEqual(hashlib.sha256(path.read_bytes()).digest(),original)
            with Image.open(raw) as a,Image.open(view) as b:
                self.assertEqual(a.size,b.size);self.assertGreater(sum(b.getpixel((35,26))),sum(a.getpixel((35,26))))
                self.assertLess(max(b.getpixel((35,26)))-min(b.getpixel((35,26))),3)


    def test_severely_dark_policy_covers_small_exposure_variations(self):
        from PIL import Image
        import numpy as np
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic.png'
            pixels=np.full((100,100,3),30,dtype=np.uint8);pixels[85:]=75
            Image.fromarray(pixels).save(path)
            self.assertTrue(grounded_index.severely_dark(path))
            pixels[80:]=160;Image.fromarray(pixels).save(path)
            self.assertFalse(grounded_index.severely_dark(path))


    def test_severely_dark_inventory_withholds_anatomy_textile_use_and_disputed_classes(self):
        first={'objects':[{'category':'人体局部','position':'右侧'},{'category':'猫','position':'左侧'},{'category':'织物','position':'下方'}]}
        second={'objects':[{'category':'人体局部','position':'右侧'},{'category':'动物','position':'左侧'},{'category':'织物','position':'未知'}]}
        with patch.object(grounded_index,'call',side_effect=[first,second]) as model,patch.object(grounded_index,'dim_detail_views',return_value=[Path('synthetic.jpg'),Path('synthetic-view.jpg')]):
            result=grounded_index.describe_dim_inventory(Path('synthetic.jpg'),object())['record']
        self.assertEqual(result['objects'],['人体局部','织物','动物'])
        self.assertEqual(result['spatial_relations'],['人体局部位于画面右侧'])
        facts=str({k:result[k] for k in ('summary','description','objects','tags','spatial_relations')})
        self.assertNotRegex(facts,r'人手|手指|脚趾|地毯|沙发|猫')
        self.assertNotIn('其他模型的答案',model.call_args_list[0].args[0])
        self.assertNotIn('人体局部位于画面右侧',model.call_args_list[1].args[0])

    def test_dim_guard_replaces_detailed_candidate_and_keeps_literal_ocr(self):
        import copy
        from PIL import Image
        bad={'summary':'一只脚位于地毯上。','description':'画面中脚趾弯曲，旁边是地毯。','objects':['脚','地毯'],'tags':['脚'],'spatial_relations':[],
             'ocr_text':'LEFT FOOT 742','ocr_verification':{'uncertainty':'synthetic partial OCR'},'uncertainty_note':''}
        before=copy.deepcopy(bad);inventory={'objects':[{'category':'人体局部','position':'右侧'},{'category':'织物','position':'下方'}]}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic.png';Image.new('RGB',(800,600),(12,12,12)).save(path)
            with patch.object(grounded_index,'_describe_grounded',return_value=bad),patch.object(grounded_index,'call',side_effect=[inventory,inventory]):
                result=grounded_index.describe_grounded(path,object())
        self.assertEqual(bad,before)
        self.assertEqual(result['objects'],['人体局部','织物'])
        self.assertEqual(result['ocr_text'],'LEFT FOOT 742')
        self.assertIn('synthetic partial OCR',result['uncertainty_note'])
        self.assertTrue(result['verification']['severely_dark_policy'])
        self.assertNotIn('地毯',result['summary']+result['description'])

    def test_dim_guard_does_not_change_bright_evidence_night_scene_or_ocr_only_terms(self):
        from PIL import Image
        cases=[('bright',(230,230,230),{'summary':'人手与桌面。','description':'人手位于桌面右侧。','objects':['人手'],'tags':[],'spatial_relations':[]}),
               ('night',(12,12,12),{'summary':'夜空与建筑。','description':'画面可见建筑与灯光。','objects':['建筑'],'tags':[],'spatial_relations':[]}),
               ('ocr',(12,12,12),{'summary':'文字。','description':'画面可见文字。','objects':['文字'],'tags':[],'spatial_relations':[],'ocr_text':'手指 脚趾 地毯'})]
        with tempfile.TemporaryDirectory() as directory:
            for name,color,record in cases:
                path=Path(directory)/(name+'.png');Image.new('RGB',(800,600),color).save(path)
                with self.subTest(case=name),patch.object(grounded_index,'describe_dim_inventory',side_effect=AssertionError('Unrelated evidence must not be regenerated')):
                    self.assertIs(grounded_index.enforce_dim_detail_limits(path,record,object()),record)

    def test_dim_guard_empty_inventory_or_service_failure_cannot_publish_old_candidate(self):
        from PIL import Image
        empty={'objects':[]}
        with patch.object(grounded_index,'call',side_effect=[empty,empty]),patch.object(grounded_index,'dim_detail_views',return_value=[Path('synthetic.jpg'),Path('synthetic-view.jpg')]):
            with self.assertRaises(media_index.IndexErrorUser):grounded_index.describe_dim_inventory(Path('synthetic.jpg'),object())
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic.png';Image.new('RGB',(800,600),(12,12,12)).save(path)
            record={'summary':'人手。','description':'一只人手。','objects':['人手'],'tags':[],'spatial_relations':[]}
            with patch.object(grounded_index,'call',side_effect=RuntimeError('synthetic local service unavailable')):
                with self.assertRaisesRegex(RuntimeError,'service unavailable'):
                    grounded_index.enforce_dim_detail_limits(path,record,object())

    def test_withheld_visual_classes_or_details_are_explicitly_partial(self):
        cases=[({},'complete'),({'verification':{'main_class_blind_agreement':False}},'partial'),
               ({'animation_frames':[{'verification':{'main_class_blind_agreement':False}}]},'partial'),
               ({'frames':[{'verification':{'severely_dark_policy':True}}]},'partial'),
               ({'audio_analysis':{'status':'needs_review'}},'partial')]
        for record,status in cases:
            with self.subTest(record=record):
                result=index_pipeline.apply_analysis_status(record)
                self.assertEqual(result['index_status'],status)
                self.assertNotIn('original_unchanged',result)
        self.assertEqual(index_pipeline.apply_analysis_status({'frames':[{'verification':{'severely_dark_policy':True}}]})['verification']['visual_coverage'],'limited')

    def test_low_detail_class_needs_blind_agreement_and_retains_verified_text(self):
        from PIL import Image
        primary={'visual_form':'其他/无法确认','main_subject':'人物','scene':'室内','has_readable_text':True}
        reviewed={'main_supported':True,'scene_supported':True,'form_supported':False}
        cases=[('其他/无法确认',True,False),('动物',False,False),('人物',True,False),('人物',False,True)]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic.png';Image.new('RGB',(160,160),'gray').save(path)
            for subject,ambiguous,expected in cases:
                blind={'subject':subject,'direct_pixel_evidence':'synthetic visible outline','ambiguous_between_classes':ambiguous}
                with self.subTest(subject=subject,ambiguous=ambiguous),patch.object(grounded_index,'call',side_effect=[primary,dict(reviewed),blind]) as model,patch.object(grounded_index,'transcribe_visible_text',return_value={'text':'TEST 742','uncertainty':'','regions':[]}):
                    result=grounded_index.describe_coarse(path,object())
                self.assertEqual('人物' in result['objects'],expected)
                self.assertEqual(result['ocr_text'],'TEST 742')
                self.assertIn('室内',result['objects'])
                self.assertEqual(model.call_count,3)
                self.assertNotIn('候选：',model.call_args_list[-1].args[0])
                self.assertEqual(result['verification']['main_class_blind_agreement'],expected)

    def test_workbench_component_types_are_coarsened_without_altering_ocr(self):
        record={'summary':'面包板连接排针与电阻。','description':'银灰色长方体物体位于面包板左侧，袋中有电容和二极管。',
                'objects':['面包板','蓝色排针','蓝色排线','银灰色长方体'],'spatial_relations':['黑色线缆从左上延伸至右侧','面包板位于键盘上方'],
                'tags':['电阻','电容','二极管'],'uncertainty_note':'','ocr_text':'R1 10k'}
        result=grounded_index.normalize_record(record)
        self.assertEqual(result['objects'],['面包板','蓝色电子元件','银灰色长方形物体'])
        self.assertEqual(result['tags'],['电子元件'])
        self.assertEqual(result['spatial_relations'],['面包板位于键盘上方'])
        self.assertNotRegex(result['summary']+result['description'],r'排针|排线|电阻|电容|二极管|长方体')
        self.assertEqual(result['ocr_text'],'R1 10k')
        self.assertIn('细小电子元件只标大类',result['uncertainty_note'])
        self.assertEqual(grounded_index.normalize_record(result),result)

    def test_workbench_inventory_drops_disputed_attributes_and_wire_routes(self):
        first={'objects':[{'category':'面包板','color':'白色','position':'中央'},
                         {'category':'电子元件','color':'蓝色','position':'左侧'},
                         {'category':'连接线','color':'黑色','position':'右侧'},
                         {'category':'工具','color':'灰色','position':'上方'}],'has_readable_text':False}
        second={'objects':[{'category':'面包板','color':'白色','position':'中央'},
                          {'category':'电子元件','color':'未知','position':'右侧'},
                          {'category':'连接线','color':'黑色','position':'右侧'}],'has_readable_text':False}
        with tempfile.TemporaryDirectory() as directory,patch.object(grounded_index,'call',side_effect=[first,second]) as model:
            result=grounded_index.describe_workbench(Path(directory)/'synthetic.jpg',object())
        self.assertEqual(result['objects'],['白色面包板','电子元件','黑色连接线'])
        self.assertEqual(result['spatial_relations'],['白色面包板位于画面中央'])
        self.assertNotIn('工具',str(result))
        self.assertNotIn('蓝色电子元件',str(result))
        self.assertNotIn('连接线位于',str(result))
        self.assertTrue(result['verification']['wire_topology_omitted'])
        self.assertNotIn('白色面包板',model.call_args_list[1].args[0])
        empty={'objects':[],'has_readable_text':False}
        with tempfile.TemporaryDirectory() as directory,patch.object(grounded_index,'call',side_effect=[empty,second]):
            with self.assertRaises(media_index.IndexErrorUser):
                grounded_index.describe_workbench(Path(directory)/'synthetic.jpg',object())

    def test_calendar_inference_and_mask_color_do_not_become_visual_facts(self):
        record={'summary':'秋季树林中有人佩戴白色口罩。','description':'两人站在黄叶树下，旁边可见秋日落叶和浅蓝色口罩。',
                'objects':['黄叶树','白色口罩'],'spatial_relations':['秋季树林位于背景'],
                'tags':['秋季','浅蓝色口罩','黄叶'],'uncertainty_note':'','ocr_text':'秋季展览：白色口罩'}
        result=grounded_index.normalize_record(record)
        facts=str({k:result[k] for k in ('summary','description','objects','spatial_relations','tags')})
        self.assertNotRegex(facts,r'秋季|秋日|白色口罩|浅蓝色口罩')
        self.assertIn('黄叶树',result['objects'])
        self.assertIn('口罩',result['objects'])
        self.assertEqual(result['ocr_text'],'秋季展览：白色口罩')
        self.assertIn('季节未作可靠判断',result['uncertainty_note'])
        self.assertIn('口罩颜色未作可靠判断',result['uncertainty_note'])

    def test_failed_group_details_are_replaced_by_fresh_verified_environment_only(self):
        import copy
        from PIL import Image
        bad={'summary':'人物位于树木旁。','description':'人物与红色行李箱位于树木旁。',
             'objects':['人物','树木','红色行李箱'],'spatial_relations':[],
             'tags':['人物','行李箱'],'uncertainty_note':'','ocr_text':'','has_readable_text':False}
        safe={'summary':'人物与树木。','description':'画面有人物和树木。','objects':['人物','树木'],
              'spatial_relations':[],'tags':['人物','树木'],'uncertainty_note':'个人属性省略。','ocr_text':'','verification':{}}
        def respond(prompt,images,schema,args,**kwargs):
            if 'approved' in schema['properties']:return {'approved':False,'issues':['Unsupported object binding'],'revised':copy.deepcopy(bad)}
            return copy.deepcopy(bad)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic.jpg';Image.new('RGB',(800,600),'white').save(path)
            for count in (2,0):
                with self.subTest(face_regions=count),patch.object(grounded_index,'native_face_regions',return_value={'detected_regions':count,'largest_area_fraction':0.001}),patch.object(grounded_index,'call',side_effect=respond) as model,patch.object(grounded_index,'describe_group_inventory',return_value=copy.deepcopy(safe)) as coarse,patch.object(grounded_index,'describe_disputed_scene',return_value=copy.deepcopy(safe)) as disputed:
                    if count:
                        result=grounded_index.describe_grounded(path,object())
                        self.assertNotIn('行李箱',str(result))
                        self.assertEqual(result['objects'],['人物','树木'])
                        self.assertIn('Individual attributes omitted',result['verification']['detail_policy'])
                        coarse.assert_called_once()
                        disputed.assert_not_called()
                        self.assertEqual(model.call_count,3)
                    else:
                        result=grounded_index.describe_grounded(path,object())
                        self.assertEqual(result['objects'],['人物','树木'])
                        self.assertNotIn('行李箱',str(result))
                        coarse.assert_not_called()
                        disputed.assert_called_once_with(path,unittest.mock.ANY,readable_hint=False)
                        self.assertEqual(model.call_count,6)

    def test_group_inventory_keeps_environment_and_omits_person_attributes_by_construction(self):
        first={'objects':[{'category':'人物','color':'白色','position':'中央'},
                         {'category':'树木','color':'绿色','position':'左侧'},
                         {'category':'树叶','color':'黄色','position':'上方'},
                         {'category':'行李箱','color':'红色','position':'右侧'},
                         {'category':'帽子','color':'红色','position':'中央'}],'has_readable_text':False}
        second={'objects':[{'category':'人物','color':'黑色','position':'右侧'},
                          {'category':'植物','color':'绿色','position':'左侧'},
                          {'category':'树叶','color':'黄色','position':'上方'},
                          {'category':'行李箱','color':'红色','position':'右侧'},
                          {'category':'帽子','color':'红色','position':'中央'}],'has_readable_text':False}
        with tempfile.TemporaryDirectory() as directory,patch.object(grounded_index,'call',side_effect=[first,second]):
            result=grounded_index.describe_group_inventory(Path(directory)/'synthetic.jpg',object())
        self.assertIn('人物',result['objects']);self.assertIn('植物',result['objects'])
        self.assertIn('红色行李箱',result['objects']);self.assertIn('黄色树叶',result['objects'])
        self.assertIn('帽子',result['objects'])
        self.assertNotIn('白色人物',str(result));self.assertNotIn('黑色人物',str(result));self.assertNotIn('红色帽子',str(result))
        self.assertNotIn('人物位于',str(result));self.assertNotIn('帽子位于',str(result))
        self.assertNotRegex(result['summary']+result['description'],r'两人|三人|站立|行走|穿着')
        empty={'objects':[],'has_readable_text':False}
        with tempfile.TemporaryDirectory() as directory,patch.object(grounded_index,'call',side_effect=[empty,empty]):
            with self.assertRaises(media_index.IndexErrorUser):grounded_index.describe_group_inventory(Path(directory)/'synthetic.jpg',object())

    def test_ocr_keeps_meaningful_characters(self):
        key=grounded_index.canonical_ocr
        self.assertNotEqual(key('1.50'),key('150'))
        self.assertNotEqual(key('A.B'),key('AB'))
        self.assertNotEqual(key('AbC'),key('ABC'))
        self.assertEqual(key('ABC ( test )'),key('ABC(test)'))

    def test_speech_similarity_cannot_override_changed_meaning(self):
        agree=speech_quality.texts_agree
        self.assertFalse(agree('We have a red suitcase.','We have a blue suitcase.'))
        self.assertFalse(agree('I do not go.','I do go.'))
        self.assertFalse(agree('The value is 1.50.','The value is 150.'))
        self.assertFalse(agree('a therapist','the rapist'))
        self.assertTrue(agree('Local test 742. A red circle.','local test 742, a red circle.'))

    def test_summary_with_unresolved_issue_is_regenerated(self):
        frames=[{'at_ms':0,'summary':'蓝色海面','description':'开阔蓝色海面与白色浪花'}]
        outputs=[{'summary':'海面上出现红色行李箱。'},
                 {'approved':True,'issues':['证据没有行李箱']},
                 {'summary':'采样画面显示蓝色海面和白色浪花。'},
                 {'approved':True,'issues':[]}]
        with patch.object(grounded_index,'call',side_effect=outputs):
            text=grounded_index.summarize(frames,[],5000,object())
        self.assertNotIn('行李箱',text)
        self.assertIn('蓝色海面',text)

    def test_rejected_video_summary_preserves_all_sample_captions_including_tail(self):
        captions=[f'合成场景{i}有红色圆形。' for i in range(13)]
        frames=[{'at_ms':i*5000,'summary':text,'description':text} for i,text in enumerate(captions)]
        def respond(prompt,images,schema,args,**kwargs):
            if 'summary' in schema['properties']:
                return {'summary':'紫色大象位于蓝色方块后方。'}
            return {'approved':False,'issues':['资料没有这些物体和关系']}
        with patch.object(grounded_index,'call',side_effect=respond):
            text=grounded_index.summarize(frames,[],65000,object())
        self.assertIn('证据摘要摘录',text)
        self.assertNotIn('紫色大象',text)
        self.assertNotIn('蓝色方块',text)
        for caption in captions:self.assertIn(caption,text)
        self.assertIn('00:01:00.000 采样：'+captions[-1],text)
        self.assertTrue(text.startswith('00:00:00.000–00:01:05.000：'))

    def test_uncertain_sleeve_length_is_not_a_search_fact(self):
        record={'summary':'红色短袖上衣','description':'一名穿红色短袖上衣的人站立',
                'objects':['红色短袖上衣'],'spatial_relations':[],'tags':['短袖','红色上衣'],
                'uncertainty_note':''}
        result=grounded_index.normalize_record(record)
        self.assertNotIn('短袖',str(result))
        self.assertIn('红色上衣',result['objects'])

    def test_reference_artifacts_are_regenerated_before_review(self):
        from types import SimpleNamespace
        bad={'summary':'圆形与方形。','description':'红色圆形位于蓝色正方形左侧',
             'objects':['image_1.jpg 1 1 1 圆形'],'spatial_relations':['image_1.jpg 位于 /objects/image_1.jpg 左侧'],
             'tags':['图形'],'uncertainty_note':''}
        good=dict(bad,objects=['红色圆形','蓝色正方形'],spatial_relations=['圆形位于方形左侧'])
        with patch.object(grounded_index.app,'ollama_request',side_effect=[bad,good]) as model:
            result=grounded_index.call('Describe the image',[],grounded_index.SCHEMA,SimpleNamespace())
        self.assertEqual(result['objects'],['红色圆形','蓝色正方形'])
        self.assertEqual(model.call_count,2)
        self.assertFalse(grounded_index.record_has_artifacts(result))

    def test_serialized_fields_cannot_masquerade_as_object_names(self):
        from types import SimpleNamespace
        base={'summary':'圆形与方形。','description':'红色圆形位于蓝色正方形左侧',
              'objects':['红色圆形'],'spatial_relations':[],'tags':['图形'],'uncertainty_note':''}
        for leaked in ("{id: '0', name: '圆形'}", "color: '红色', position: '左侧'", 'spatial_relations', 'false'):
            bad=dict(base,objects=[leaked])
            with self.subTest(leaked=leaked),patch.object(grounded_index.app,'ollama_request',side_effect=[bad,base]) as model:
                self.assertEqual(grounded_index.call('Describe',[],grounded_index.SCHEMA,SimpleNamespace())['objects'],['红色圆形'])
                self.assertEqual(model.call_count,2)

    def test_ambiguous_classes_and_cut_summaries_are_regenerated(self):
        from types import SimpleNamespace
        good={'summary':'红色圆形位于蓝色方形左侧。','description':'画面有红色圆形与蓝色方形。',
              'objects':['红色圆形','蓝色方形'],'spatial_relations':[],'tags':['图形'],'uncertainty_note':''}
        for bad in (dict(good,objects=['圆形/椭圆形']),dict(good,objects=['可能是方形']),dict(good,summary='圆形位于方形左侧与')):
            with self.subTest(bad=bad),patch.object(grounded_index.app,'ollama_request',side_effect=[bad,good]) as model:
                self.assertEqual(grounded_index.call('Describe',[],grounded_index.SCHEMA,SimpleNamespace()),good)
                self.assertEqual(model.call_count,2)

    def test_close_group_cannot_publish_person_clothing_binding(self):
        from types import SimpleNamespace
        good={'summary':'画面可见人物与红色圆形。','description':'画面中有人物，其中一人位于左侧近景，背景有红色圆形。',
              'objects':['红色圆形'],'spatial_relations':[],'tags':['图形'],'uncertainty_note':'',
              'ocr_text':'','has_readable_text':False,'accessories':['眼镜']}
        bad=dict(good,description='人物穿红色上衣。')
        with tempfile.TemporaryDirectory() as folder,patch.object(grounded_index,'call',side_effect=[bad,good,{'approved':True,'issues':[],'revised':None}]) as model:
            result=grounded_index.describe_crowded_scene(Path(folder)/'synthetic.jpg',SimpleNamespace())
        self.assertNotIn('上衣',result['description'])
        self.assertIn('其中一人',result['description'])
        self.assertEqual(model.call_count,3)
        self.assertNotIn('上衣',model.call_args_list[-1].args[0])
        self.assertIn('眼镜',result['objects'])
        self.assertTrue(result['verification']['close_group_policy'])

    def test_close_group_existential_is_not_a_total_person_count(self):
        def record(text):
            return {'summary':'人物与红色行李箱。','description':text,
                    'objects':['红色行李箱'],'spatial_relations':[],'tags':['人物']}
        for text in ['其中一人位于左侧近景。','其中一名人物位于左侧。','其中一位人物位于左侧。']:
            with self.subTest(text=text):
                self.assertFalse(grounded_index.crowded_details_present(record(text)))
        for text in ['画面中有三人。','其中一人穿绿色外套。','其中一人弯腰。',
                     '其中一人位于左侧，另一人穿绿色外套。']:
            with self.subTest(text=text):
                self.assertTrue(grounded_index.crowded_details_present(record(text)))

    def test_close_group_environment_still_requires_independent_review(self):
        from types import SimpleNamespace
        first={'summary':'人物与蓝色方块。','description':'人物在近景，后方有蓝色方块。',
               'objects':['蓝色方块'],'spatial_relations':[],'tags':['方块'],'uncertainty_note':'',
               'ocr_text':'','has_readable_text':False,'accessories':[]}
        corrected=dict(first,summary='人物与绿色球体。',description='背景有绿色球体。',
                       objects=['绿色球体'],tags=['球体'])
        responses=[first,{'approved':False,'issues':['方块没有图像证据'],'revised':corrected},
                   {'approved':True,'issues':[],'revised':None}]
        with tempfile.TemporaryDirectory() as folder,patch.object(grounded_index,'call',side_effect=responses) as model:
            result=grounded_index.describe_crowded_scene(Path(folder)/'synthetic.jpg',SimpleNamespace())
        self.assertNotIn('蓝色方块',str(result))
        self.assertIn('绿色球体',result['objects'])
        self.assertEqual(model.call_count,3)

    def test_distant_people_do_not_inherit_vehicle_relations(self):
        record={'summary':'近处有圆形，远处有人位于绿色小车上。',
                'description':'远处有两人站在绿色小车旁。远处有红色房屋。',
                'objects':['红色圆形','远处站在小车上的人','红色房屋'],
                'spatial_relations':['远处的人位于小车旁'],'tags':['自然场景'],'uncertainty_note':''}
        result=grounded_index.normalize_record(record)
        self.assertEqual(result['description'],'远处有人。远处有红色房屋。')
        self.assertNotIn('小车',result['summary'])
        self.assertIn('远处的人',result['objects'])
        self.assertEqual(result['spatial_relations'],[])
        foreground=dict(record,description='远处有高楼，前景有一人站在红色圆形旁。',objects=['红色圆形'],spatial_relations=[])
        self.assertEqual(grounded_index.normalize_record(foreground)['description'],'远处有高楼，前景有一人站在红色圆形旁。')

    def test_spatial_relations_do_not_publish_ambiguous_depth_or_residue(self):
        record={'summary':'画面中有人和几何体','description':'画面中有穿红色上衣的人、条纹球与圆柱',
                'objects':['人','条纹球','圆柱'],'tags':['几何体'],
                'spatial_relations':['穿红色上衣的人位于画面左上部，方向背对镜头',
                                     '条纹球位于圆柱前方','圆柱位于画面右下部，'],
                'uncertainty_note':''}
        result=grounded_index.normalize_record(record)
        self.assertEqual(result['spatial_relations'],['穿红色上衣的人位于画面左上部','圆柱位于画面右下部'])

    def test_transcript_never_links_past_media_end(self):
        rows=[{'start_ms':-20,'end_ms':500,'text':'first'},
              {'start_ms':900,'end_ms':1200,'text':'tail'},
              {'start_ms':1100,'end_ms':1500,'text':'outside'}]
        result,corrections=index_pipeline.clamp_transcript(rows,1000)
        self.assertEqual([(r['start_ms'],r['end_ms']) for r in result],[(0,500),(900,1000)])
        self.assertEqual(corrections,3)

    def test_delayed_audio_retains_video_timebase(self):
        binary=media_index.ffmpeg_path()
        if not binary:self.skipTest('Bundled ffmpeg not installed')
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'delayed.mp4';output=Path(folder)/'audio.wav'
            media_index.run_checked([binary,'-hide_banner','-loglevel','error','-y','-f','lavfi','-i',
                'color=c=blue:s=320x240:r=1:d=5','-itsoffset','2','-f','lavfi','-i',
                'sine=frequency=440:sample_rate=16000:duration=1','-map','0:v','-map','1:a',
                '-c:v','libx264','-c:a','aac','-t','5',str(source)],'Synthetic audio-offset fixture')
            media_index.extract_audio(binary,source,output)
            with wave.open(str(output),'rb') as stream:
                rate=stream.getframerate()
                samples=array.array('h',stream.readframes(stream.getnframes()))
            first=next(i for i,value in enumerate(samples) if abs(value)>500)/rate
            self.assertGreater(first,1.9);self.assertLess(first,2.1)


if __name__=='__main__':unittest.main()
