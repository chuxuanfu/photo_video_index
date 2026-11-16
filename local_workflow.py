#!/usr/bin/env python3
"""Bounded local indexing tools; stdout contains aggregate results only."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import traceback

import media_index as app

PROJECT = Path(__file__).resolve().parent
SMOKE = PROJECT / '.local/smoke'


@contextlib.contextmanager
def local_log(path: Path):
    # Capture subprocess and native-library output too, not just Python print().
    sys.stdout.flush()
    sys.stderr.flush()
    saved = (os.dup(1), os.dup(2))
    try:
        with path.open('a') as stream, contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            os.dup2(stream.fileno(), 1)
            os.dup2(stream.fileno(), 2)
            try:
                yield
            finally:
                stream.flush()
                os.dup2(saved[0], 1)
                os.dup2(saved[1], 2)
    finally:
        os.close(saved[0])
        os.close(saved[1])


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def options():
    config = app.load_config(PROJECT / 'config.mlx.json')
    return app.parser(config).parse_args(['index', '--limit', '2', '--max-frames', '3'])


def create_synthetic() -> tuple[Path, Path]:
    from PIL import Image, ImageDraw, ImageFont
    photos, videos = SMOKE / 'input/Photo', SMOKE / 'input/Video'
    photos.mkdir(parents=True, exist_ok=True)
    videos.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', 36)
    for name, second in [('shapes-a.png', False), ('shapes-b.png', True)]:
        path = photos / name
        if path.exists():
            continue
        im = Image.new('RGB', (640, 480), 'white')
        draw = ImageDraw.Draw(im)
        if second:
            draw.polygon([(320, 110), (150, 350), (490, 350)], fill=(0, 160, 50))
            draw.text((50, 30), 'LOCAL TEST 742', fill='black', font=font)
        else:
            draw.ellipse((65, 140, 265, 340), fill=(230, 20, 20))
            draw.rectangle((360, 140, 560, 340), fill=(20, 70, 230))
            draw.text((50, 30), 'LOCAL TEST 742', fill='black', font=font)
        im.save(path)
    video = videos / 'synthetic.mp4'
    if not video.exists():
        speech = PROJECT / 'tests/fixtures/local-test-speech.mp3'
        if not speech.is_file():
            raise app.IndexErrorUser('The public synthetic speech fixture is missing.')
        subprocess.run([app.ffmpeg_path(), '-y', '-v', 'error',
                        '-loop', '1', '-t', '4', '-i', str(photos / 'shapes-a.png'),
                        '-loop', '1', '-t', '4', '-i', str(photos / 'shapes-b.png'), '-i', str(speech),
                        '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]',
                        '-map', '[v]', '-map', '2:a', '-af', 'apad', '-t', '8',
                        '-c:v', 'libx264', '-r', '4', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(video)], check=True, timeout=60)
    return photos, videos


def run_index(photos: Path | None, videos: Path | None, output: Path, *, synthetic: bool) -> dict:
    args = options()
    args.output_dir = str(output)
    args.frame_interval = 3 if synthetic else 30
    selected = {}
    source_counts = {}
    before = {}
    for kind, root in [('photo', photos), ('video', videos)]:
        if root is None:
            continue
        root = root.expanduser().resolve(strict=True)
        rows = app.source_files(root, kind)
        source_counts[kind] = len(rows)
        if kind == 'video':
            # A private trial accepts one genuinely short clip; never process a long file by accident.
            rows = [row for row in rows[:100] if app.video_info(app.ffmpeg_path(), row[1])[0] <= 60000][:1]
        else:
            rows = rows[:2]
        if not rows:
            raise app.IndexErrorUser('No eligible bounded inputs in the authorized root.')
        selected[kind] = rows
        before.update({path: digest(path) for _, path in rows})
        if (root / 'index.json').exists():
            before[root / 'index.json'] = digest(root / 'index.json')
    original_source_files = app.source_files
    app.source_files = lambda root, kind: selected[kind]
    indices = []
    roots = {}
    first = {}
    try:
        for kind, root in [('photo', photos), ('video', videos)]:
            if root is None:
                continue
            args.limit = 2 if kind == 'photo' else 1
            path = app.index_root(str(root), kind, args)
            indices.append(str(path))
            data = app.read_index(path)
            roots[data['library_id']] = root.resolve()
            first[kind] = (data['library_id'], data['generation'])
        # Exercise incremental resumption against the same trial outputs.
        for kind, root in [('photo', photos), ('video', videos)]:
            if root is None:
                continue
            args.limit = 2 if kind == 'photo' else 1
            path = app.index_root(str(root), kind, args)
            data = app.read_index(path)
            assert first[kind] == (data['library_id'], data['generation'])
    finally:
        app.source_files = original_source_files
    hits, _ = app.load_hits(indices)
    assert all(app.locate(hit, roots) is not None for hit in hits)
    assert all(digest(path) == value for path, value in before.items())
    data = [app.read_index(Path(path)) for path in indices]
    if not synthetic:
        for library, path in zip(data, indices):
            library['scan_limited'] = source_counts[library['kind']] > len(library['assets'])
            library['index_scope'] = 'bounded-trial'
            app.write_index(Path(path), library)
    video_assets = [a for d in data if d['kind'] == 'video' for a in d['assets']]
    query_hits = {}
    keyword_checks = []
    for library in data:
        for asset in library['assets']:
            visual = asset if library['kind'] == 'photo' else asset['frames'][0]
            candidates = [*visual.get('tags', []), *visual.get('objects', [])]
            query = next((word.strip() for word in candidates if isinstance(word, str) and len(word.strip()) >= 2), asset['summary'][:16])
            matches = app.search_hits(hits, query, max(1, len(hits)))
            keyword_checks.append(any(hit.library_id == library['library_id'] and hit.relative_path == asset['relative_path'] for hit in matches))
    assert keyword_checks and all(keyword_checks)
    if synthetic:
        photo_assets = [a for d in data if d['kind'] == 'photo' for a in d['assets']]
        for a in photo_assets:
            visible = app.searchable_text(a)
            if a['relative_path'] == 'shapes-a.png':
                assert all(word in visible for word in ['红', '圆', '蓝'])
                assert any(word in visible for word in ['正方', '方块', '方形'])
            elif a['relative_path'] == 'shapes-b.png':
                assert '绿' in visible and '三角' in visible
        for word in ('红色', '蓝色', '绿色'):
            query_hits[word] = len(app.search_hits(hits, word, 20))
        assert all(query_hits.values())
        transcripts = [row for a in video_assets for row in a['transcript']]
        assert any('red circle' in row['text'].lower() for row in transcripts)
        assert any('742' in a.get('ocr_text', '') for d in data for a in d['assets'] if d['kind'] == 'photo')
        assert any(hit.source == 'speech' and hit.at_ms is not None for hit in app.search_hits(hits, 'red circle', 20))
    for a in video_assets:
        assert a['index_status'] == 'complete' or not synthetic
        assert all(0 <= row['at_ms'] < a['duration_ms'] for row in a['frames'])
        assert all(0 <= row['start_ms'] < row['end_ms'] <= a['duration_ms'] + 1000 for row in a['transcript'])
        timed = app.search_hits(hits, '00:00:06.000' if synthetic else app.time_label(a['frames'][0]['at_ms']), 20)
        assert any(hit.kind == 'video' and hit.at_ms is not None for hit in timed)
    (output / 'media-roots.json').write_text(json.dumps({k: str(v) for k, v in roots.items()}, indent=2) + '\n')
    report = {
        'status': 'passed', 'synthetic': synthetic,
        'photos': sum(len(d['assets']) for d in data if d['kind'] == 'photo'),
        'videos': len(video_assets), 'frames': sum(len(a['frames']) for a in video_assets),
        'transcript_segments': sum(len(a['transcript']) for a in video_assets),
        'originals_and_existing_indices_unchanged': True,
        'incremental_skip_and_library_id_verified': True,
        'relative_paths_resolve': True, 'video_timecodes_verified': bool(video_assets),
        'keyword_queries_verified': all(keyword_checks) and (all(query_hits.values()) if synthetic else True),
        'synthetic_visual_facts_and_ocr_verified': True if synthetic else None,
        'cloud_fallback': False,
    }
    (output / 'validation.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main() -> int:
    if sys.argv[1:] not in (['synthetic'], ['trial']):
        raise SystemExit('Usage: local_workflow.py synthetic|trial')
    synthetic = sys.argv[1] == 'synthetic'
    if synthetic:
        output = SMOKE / 'output'
    else:
        config_path = PROJECT / '.local/private-trial.json'
        if not config_path.is_file():
            print(json.dumps({'status': 'blocked', 'reason': 'authorized_media_roots_required'}))
            return 2
        request = json.loads(config_path.read_text())
        if request.get('authorized') is not True:
            raise SystemExit('Explicit local media-root authorization is required.')
        run_id = request.get('run_id', '')
        if not isinstance(run_id, str) or not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}', run_id):
            raise SystemExit('An explicit bounded trial run_id is required.')
        output = PROJECT / '.local/trials' / run_id
        request_fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        prior = output / 'request-fingerprint.txt'
        if prior.exists() and prior.read_text().strip() != request_fingerprint:
            raise SystemExit('This run_id already belongs to a different request.')
        completed = output / 'validation.json'
        if completed.exists():
            result = json.loads(completed.read_text())
            if result.get('status') == 'passed':
                result['cached_result'] = True
                print(json.dumps(result))
                return 0
    output.mkdir(parents=True, exist_ok=True)
    if not synthetic:
        (output / 'request-fingerprint.txt').write_text(request_fingerprint + '\n')
    log = output / 'workflow.log'
    try:
        with local_log(log):
            if synthetic:
                photos, videos = create_synthetic()
            else:
                photos = Path(request['photo_root']) if request.get('photo_root') else None
                videos = Path(request['video_root']) if request.get('video_root') else None
                if photos is None and videos is None:
                    raise app.IndexErrorUser('authorized_media_roots_required')
                scope = Path(request['authorized_scope_root']).expanduser().resolve(strict=True)
                if scope in {Path('/'), Path.home(), Path('/Users')}:
                    raise app.IndexErrorUser('A specific media scope is required.')
                for root in (photos, videos):
                    if root is not None:
                        root.expanduser().resolve(strict=True).relative_to(scope)
            result = run_index(photos, videos, output, synthetic=synthetic)
        print(json.dumps(result))
        return 0
    except Exception as exc:
        with log.open('a') as stream:
            traceback.print_exc(file=stream)
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__, 'details': 'retained only in the local workflow log'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
