import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import install_local
import media_library
import single_library


class LocalInstallTests(unittest.TestCase):
    def test_model_checksums_are_content_and_size_sensitive(self):
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/'weight';file.write_bytes(b'abc')
            spec={'size':3,'sha256':hashlib.sha256(b'abc').hexdigest()}
            self.assertTrue(install_local.matches(file,spec))
            file.write_bytes(b'abd');self.assertFalse(install_local.matches(file,spec))
            file.write_bytes(b'abcd');self.assertFalse(install_local.matches(file,spec))
            file.write_bytes(b'abc')
            self.assertTrue(install_local.matches(file,{'size':3,'git_blob_sha1':hashlib.sha1(b'blob 3\0abc').hexdigest()}))

    def test_unsafe_roots_and_foreign_index_preserved(self):
        with self.assertRaises(Exception):media_library.check_root(Path.home())
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);file=root/'index.json';original=b'{"schema_version":1,"kind":"photo","library_id":"x","assets":[]}'
            file.write_bytes(original)
            with self.assertRaises(Exception):media_library.catalog_root(root)
            self.assertEqual(file.read_bytes(),original)

    def test_concurrent_update_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            first=single_library.catalog(root)
            with patch.object(media_library,'STATE',root/'.state'):
                media_library.commit(root,first,None)
                edited=dict(first,generation=9)
                (root/'index.json').write_text(json.dumps(edited))
                with self.assertRaises(Exception):media_library.commit(root,dict(first,generation=2),first)
                self.assertEqual(json.loads((root/'index.json').read_text())['generation'],9)

    def test_review_queue_and_raw_animation_are_distinct(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'raw.dng').write_bytes(b'raw');(root/'motion.gif').write_bytes(b'gif')
            first=single_library.catalog(root)
            self.assertTrue(all(a['analysis_status']=='pending' for a in first['assets']))
            first['assets'][0]['analysis_status']='needs_review'
            first['assets'][0]['analysis_error_type']='VisualGate'
            second=single_library.catalog(root,first)
            self.assertEqual(second['summary']['needs_review'],1)
            self.assertEqual(second['assets'][0]['analysis_error_type'],'VisualGate')
            prior=single_library.catalog(root)
            prior['assets'][0]['analysis_status']='unsupported'
            upgraded=single_library.catalog(root,prior)
            self.assertEqual(upgraded['assets'][0]['analysis_status'],'pending')

    def test_changed_video_never_reuses_old_frames(self):
        import index_pipeline
        from types import SimpleNamespace
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'media';root.mkdir()
            source=root/'clip.mp4';source.write_bytes(b'old')
            asset=single_library.catalog(root)['assets'][0]
            cache=Path(folder)/'cache'
            def extract(binary,path,at_ms,target):
                color='red' if path.read_bytes()==b'old' else 'blue'
                Image.new('RGB',(16,16),color).save(target)
                return {'actual_at_ms':max(0,at_ms-100)}
            def describe(path,args,frame=False):
                pixel=Image.open(path).getpixel((0,0))
                color='red' if pixel[0]>pixel[2] else 'blue'
                return {'summary':color,'description':color,'objects':[color], 'tags':[color],'spatial_relations':[],'ocr_text':'','uncertainty_note':''}
            with patch.object(index_pipeline.app,'video_info',return_value=(1000,False)),patch.object(index_pipeline.app,'ffmpeg_path',return_value='ffmpeg'),patch.object(index_pipeline.app,'extract_frame',side_effect=extract) as extraction,patch.object(index_pipeline.grounded,'describe_grounded',side_effect=describe),patch.object(index_pipeline.grounded,'summarize',side_effect=lambda frames,*args:frames[0]['summary']):
                first=index_pipeline.analyze(root,asset,cache,SimpleNamespace())
                self.assertEqual(first['summary'],'red')
                self.assertEqual(first['frames'][1]['at_ms'],400)
                self.assertEqual(first['frames'][1]['requested_at_ms'],500)
                source.write_bytes(b'new')
                changed=single_library.catalog(root)['assets'][0]
                second=index_pipeline.analyze(root,changed,cache,SimpleNamespace())
                self.assertEqual(second['summary'],'blue')
                third=index_pipeline.analyze(root,changed,cache,SimpleNamespace())
                self.assertEqual(third,second)
                self.assertEqual(extraction.call_count,4)

    def test_known_noise_hallucination_is_rejected(self):
        from speech_quality import reasons
        repeated={'start':0,'end':2,'text':'ව'*100,'no_speech_prob':0.84,'avg_logprob':-0.2,'compression_ratio':20.8}
        found=reasons(repeated,[True]*67)
        self.assertIn('repeated_characters',found)
        self.assertIn('no_speech_probability',found)
        speech={'start':0,'end':2,'text':'A red circle is beside a blue square.','no_speech_prob':0.001,'avg_logprob':-0.3,'compression_ratio':1.1}
        self.assertEqual(reasons(speech,[True]*67),[])
        self.assertIn('insufficient_speech_activity',reasons(speech,[False]*67))

    def test_summary_time_range_comes_from_media_not_generated_prose(self):
        import grounded_index
        frames=[{'at_ms':0,'summary':'水中有人','description':'蓝色水面与水花'}]
        with patch.object(grounded_index,'call',side_effect=AssertionError('Portable evidence summaries must not generate new timestamps or actions')) as model:
            summary=grounded_index.summarize(frames,[],2700,object())
        self.assertTrue(summary.startswith('00:00:00.000–00:00:02.700：'))
        self.assertNotIn('00:03',summary)
        self.assertEqual(model.call_count,0)


if __name__=='__main__':unittest.main()
