import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import threading
import urllib.parse
import urllib.request
from unittest import mock
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import media_index as app
import single_library as library


class SingleLibraryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'Memory'
        self.photo = self.root / 'Event' / 'Photo' / 'red #1.jpg'
        self.video = self.root / 'Event' / 'Video' / 'clip.mp4'
        for path in [self.photo, self.video]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'synthetic original')

    def annotations(self):
        result = {}
        for path in [self.photo, self.video]:
            stat = path.stat()
            result[path.relative_to(self.root).as_posix()] = {
                'source_size': stat.st_size, 'source_mtime_ns': stat.st_mtime_ns,
                'summary': 'synthetic red circle', 'description': 'synthetic red circle next to a blue square',
                'tags': ['red circle'],
            }
        result[self.video.relative_to(self.root).as_posix()].update({
            'duration_ms': 19542, 'index_status': 'complete',
            'frames': [{'at_ms': 0, 'summary': 'red circle', 'description': 'red circle next to blue square'}],
            'transcript': [{'start_ms': 0, 'end_ms': 19540, 'text': 'synthetic speech'}],
        })
        return result

    def test_one_mixed_index_relocates_with_encoded_relative_links(self):
        data = library.catalog(self.root, annotations=self.annotations())
        index = self.root / 'index.json'
        app.write_index(index, data)
        hits, libraries = app.load_hits([str(self.root)])
        self.assertEqual(len(libraries), 1)
        self.assertEqual({h.kind for h in hits}, {'photo', 'video'})
        self.assertTrue(all(app.locate(h, {}) is not None for h in hits))
        photo = next(a for a in data['assets'] if a['kind'] == 'photo')
        video = next(a for a in data['assets'] if a['kind'] == 'video')
        self.assertEqual(photo['relative_link'], './Event/Photo/red%20%231.jpg')
        self.assertTrue(video['frames'][0]['relative_link'].endswith('#t=0'))
        self.assertEqual(data['source_root_hint'], '')
        copied = Path(self.temp.name) / 'OnlyIndex'
        copied.mkdir()
        shutil.copy2(index, copied / 'index.json')
        copied_hits, _ = app.load_hits([str(copied)])
        self.assertIsNone(app.locate(copied_hits[0], {}))
        self.assertTrue(all(app.locate(h, {data['library_id']: self.root}) is not None for h in copied_hits))

    def test_visual_moment_opens_its_actual_evidence_frame(self):
        asset={'kind':'video','relative_link':'./Video/example.mp4',
               'frames':[{'at_ms':5000}],
               'transcript':[{'start_ms':250,'end_ms':900,'text':'synthetic speech'}],
               'moments':[{'start_ms':2500,'end_ms':7500,'frame_at_ms':5000},
                          {'start_ms':700,'end_ms':900,'frame_at_ms':None}]}
        library.add_time_links(asset)
        self.assertEqual(asset['frames'][0]['relative_link'],'./Video/example.mp4#t=5')
        self.assertEqual(asset['moments'][0]['relative_link'],'./Video/example.mp4#t=5')
        self.assertEqual(asset['moments'][1]['relative_link'],'./Video/example.mp4#t=0.7')
        self.assertEqual(asset['transcript'][0]['relative_link'],'./Video/example.mp4#t=0.25')

    def test_zero_and_exact_time_queries_use_video_duration(self):
        data = library.catalog(self.root, annotations=self.annotations())
        app.write_index(self.root / 'index.json', data)
        hits, _ = app.load_hits([str(self.root)])
        for query, expected in [('00:00:00.000', 0), ('00:00:06.250', 6250), ('00:19.500', 19500)]:
            matches = app.search_hits(hits, query, 10)
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].at_ms, expected)
            self.assertEqual(matches[0].source, 'timecode')
        self.assertEqual(app.search_hits(hits, '00:00:20.000', 10), [])

    def test_incremental_catalog_preserves_ids_and_marks_new_media_pending(self):
        first = library.catalog(self.root, annotations=self.annotations())
        app.write_index(self.root / 'index.json', first)
        second = library.catalog(self.root, first)
        self.assertEqual(second, first)
        (self.photo.parent / 'new.png').write_bytes(b'new synthetic')
        third = library.catalog(self.root, second)
        self.assertEqual(third['library_id'], first['library_id'])
        self.assertEqual(third['generation'], first['generation'] + 1)
        old_ids = {a['relative_path']: a['id'] for a in first['assets']}
        self.assertTrue(all(a['id'] == old_ids[a['relative_path']] for a in third['assets'] if a['relative_path'] in old_ids))
        self.assertEqual(third['summary']['pending_analysis'], 1)
        self.assertFalse(third['analysis_complete'])

    def test_catalog_does_not_follow_media_symlinks_outside_root(self):
        outside = Path(self.temp.name) / 'outside.jpg'
        outside.write_bytes(b'not authorized')
        (self.photo.parent / 'escape.jpg').symlink_to(outside)
        data = library.catalog(self.root)
        self.assertEqual(len(data['assets']), 2)
        self.assertFalse(data['catalog_complete'])
        self.assertEqual(data['skipped_entries'][0]['reason'], 'symlink_not_followed')

    def test_http_viewer_uses_each_assets_kind_in_one_mixed_index(self):
        data = library.catalog(self.root, annotations=self.annotations())
        app.write_index(self.root / 'index.json', data)
        args = app.parser().parse_args(['serve', '--index', str(self.root / 'index.json'), '--port', '0', '--no-semantic'])
        ready = threading.Event()
        servers = []

        def server_factory(address, handler):
            server = ThreadingHTTPServer(address, handler)
            servers.append(server)
            ready.set()
            return server

        with mock.patch.object(app, 'ThreadingHTTPServer', side_effect=server_factory):
            worker = threading.Thread(target=app.serve, args=(args,), daemon=True)
            worker.start()
            self.assertTrue(ready.wait(3))
            try:
                base = 'http://127.0.0.1:' + str(servers[0].server_port)
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                for kind, relative in [('photo', self.photo.relative_to(self.root).as_posix()), ('video', self.video.relative_to(self.root).as_posix())]:
                    query = urllib.parse.urlencode({'lib': data['library_id'], 'path': relative, 't': 6250})
                    with opener.open(base + '/view?' + query, timeout=3) as response:
                        body = response.read().decode()
                    if kind == 'photo':
                        self.assertIn('<img ', body)
                        self.assertNotIn('<video ', body)
                    else:
                        self.assertIn('<video ', body)
                        self.assertIn('this.currentTime=6.25', body)
            finally:
                servers[0].shutdown()
                servers[0].server_close()
                worker.join(3)


if __name__ == '__main__':
    unittest.main()
