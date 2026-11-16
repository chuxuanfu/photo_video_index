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
        good={'summary':'画面可见人物与红色圆形。','description':'前景可见人物，背景有红色圆形。',
              'objects':['红色圆形'],'spatial_relations':[],'tags':['图形'],'uncertainty_note':'',
              'ocr_text':'','has_readable_text':False,'accessories':['眼镜']}
        bad=dict(good,description='人物穿红色上衣。')
        with tempfile.TemporaryDirectory() as folder,patch.object(grounded_index,'call',side_effect=[bad,good,{'approved':True,'issues':[],'revised':None}]) as model:
            result=grounded_index.describe_crowded_scene(Path(folder)/'synthetic.jpg',SimpleNamespace())
        self.assertNotIn('上衣',result['description'])
        self.assertEqual(model.call_count,3)
        self.assertIn('眼镜',result['objects'])
        self.assertTrue(result['verification']['close_group_policy'])

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
