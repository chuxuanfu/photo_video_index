"""Read-only, resumable local visual/speech analysis of one catalog asset."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import media_index as app
import grounded_index as grounded
from media_decode import render_photo


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def processing_signature():
    hasher=hashlib.sha256()
    project=Path(__file__).resolve().parent
    for name in ('index_pipeline.py','grounded_index.py','speech_quality.py','media_decode.py','local_ocr.py','media_index.py','native_frames.py','single_library.py','media_library.py','local_runtime.py','requirements-mlx.lock','model-manifest.json'):
        hasher.update(name.encode()+b'\0'+(project/name).read_bytes())
    return hasher.hexdigest()


def clamp_transcript(rows,duration):
    accepted=[];corrections=0
    for row in rows:
        start=max(0,min(duration,int(row['start_ms'])));end=max(0,min(duration,int(row['end_ms'])))
        if (start,end)!=(row['start_ms'],row['end_ms']):corrections+=1
        if end>start:accepted.append(dict(row,start_ms=start,end_ms=end))
        elif (start,end)==(row['start_ms'],row['end_ms']):corrections+=1
    return accepted,corrections


def analyze(root, asset, destination, args, *, interval_ms=5000, progress=lambda phase: None):
    source = app.resolve_media(root, asset['relative_path'], asset['kind'])
    if source is None:
        raise app.IndexErrorUser('Original is missing or outside the selected root')
    before = sha256(source)
    stat = source.stat()
    if (stat.st_size, stat.st_mtime_ns) != (asset['source_size'], asset['source_mtime_ns']):
        raise app.IndexErrorUser('Original changed since cataloging')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    signature=processing_signature()
    # Every derived frame and caption belongs to this exact source and code.
    # A changed original must never reuse a frame from the previous version.
    destination=destination/(before[:20]+'-'+signature[:20])
    destination.mkdir(exist_ok=True)
    cached = destination / 'result.json'
    if cached.exists():
        record = json.loads(cached.read_text())
        if record.get('source_sha256') == before and record.get('verification_profile') == grounded.PROFILE and record.get('processing_signature') == signature and record.get('interval_ms') == interval_ms:
            return record
    record = {k: asset[k] for k in ('relative_path', 'kind', 'source_size', 'source_mtime_ns')}
    record.update(source_sha256=before, verification_profile=grounded.PROFILE, processing_signature=signature, interval_ms=interval_ms)
    if asset['kind'] == 'photo':
        progress('decoding')
        preview = destination / 'input.jpg'
        record['media_metadata'] = render_photo(source, preview)
        progress('describing')
        if record['media_metadata'].get('animated'):
            animation = grounded.describe_animation(source, destination, args, progress=lambda phase: None)
            record.update(animation.pop('details'))
            record.update(animation)
        else:
            record.update(grounded.describe_grounded(preview, args))
    else:
        ffmpeg = app.ffmpeg_path()
        duration, audio = app.video_info(ffmpeg, source)
        record.update(duration_ms=duration, has_audio=audio, frames=[], transcript=[])
        if duration <= 0:
            raise app.IndexErrorUser('Video has no verified positive duration')
        times = sorted(set(range(0, duration, interval_ms)) | {max(0, duration-500)})
        for at_ms in times:
            progress('video-frame')
            path = destination / f'frame-{at_ms:010d}.jpg'
            cache = path.with_suffix('.json')
            decoded_path=path.with_suffix('.decode.json')
            if not path.exists():
                decoded=app.extract_frame(ffmpeg, source, at_ms, path) or {}
            else:
                decoded=json.loads(decoded_path.read_text()) if decoded_path.exists() else {}
            actual_ms=decoded.get('actual_at_ms',at_ms)
            if not 0 <= actual_ms < duration:
                raise app.IndexErrorUser('Decoded frame time is outside the verified video duration')
            details = json.loads(cache.read_text()) if cache.exists() else grounded.describe_grounded(path, args, frame=True)
            cache.write_text(json.dumps(details, ensure_ascii=False, indent=2)+'\n')
            record['frames'].append({'at_ms':actual_ms, 'requested_at_ms':at_ms, 'decode':decoded, **details})
        record['frames'].sort(key=lambda row:row['at_ms'])
        if audio:
            progress('transcribing')
            wav = destination / 'audio.wav'
            if not wav.exists():
                app.extract_audio(ffmpeg, source, wav)
            from speech_quality import transcribe
            record.update(transcribe(wav, destination, args))
            record['transcript'],corrections=clamp_transcript(record['transcript'],duration)
            record['audio_analysis']['timestamp_corrections']=corrections
            if corrections:record['audio_analysis']['status']='needs_review'
        if any(not 0 <= row['start_ms'] < row['end_ms'] <= duration for row in record['transcript']):
            raise app.IndexErrorUser('Audio timestamp verification failed')
        progress('summarizing')
        record['summary'] = grounded.summarize(record['frames'], record['transcript'], duration, args)
        record['moments'] = app.make_moments(record['frames'], record['transcript'], duration, frame_interval_ms=interval_ms)
        record['sampling'] = {'interval_ms': interval_ms, 'frames': len(times), 'complete_sample_schedule': True,
                              'limitation': 'Events between sampled frames may be missed.'}
    after = source.stat()
    if sha256(source) != before or after.st_size != stat.st_size or after.st_mtime_ns != stat.st_mtime_ns:
        raise app.IndexErrorUser('Original changed during analysis; result rejected')
    record.update(index_status='partial' if record.get('audio_analysis',{}).get('status')=='needs_review' else 'complete', original_unchanged=True)
    temporary = cached.with_suffix('.tmp')
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2)+'\n')
    temporary.replace(cached)
    return record
