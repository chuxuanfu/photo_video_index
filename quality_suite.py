#!/usr/bin/env python3
"""Bounded supervision of the actual production pipeline; no library writes."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback
import media_index as app
import index_pipeline
from local_workflow import local_log

PROJECT=Path(__file__).resolve().parent
BASE=PROJECT/'.local/quality-review'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def code_fingerprint():
    return hashlib.sha256((index_pipeline.processing_signature()+digest(__file__)).encode()).hexdigest()


def run_round(number,profile,only=None):
    from grounded_index import PROFILE
    from media_library import options
    if profile!=PROFILE:
        raise ValueError('Only the current production vision profile may be certified')
    manifest=json.loads((BASE/'manifest.json').read_text())
    root=Path(manifest['authorized_scope_root']).resolve(strict=True)
    group=next(g for g in manifest['rounds'] if g['round']==number)
    tested=code_fingerprint()
    destination=BASE/f'round-{number}'/(profile+'-'+tested[:12])
    destination.mkdir(parents=True,exist_ok=True)
    started=time.monotonic()
    records,failures=[],[]
    args=options()
    def progress(identifier,phase):
        text=json.dumps({'round':number,'profile':profile,'sample_id':identifier,'phase':phase,
            'completed':len(records),'failures':len(failures),'elapsed_seconds':round(time.monotonic()-started,1)},indent=2)+'\n'
        (BASE/'progress.json').write_text(text)
        (destination/'progress.json').write_text(text)
    samples=[*group['photos'],*group['videos']]
    if only:
        samples=[s for s in samples if s['sample_id'] in only]
        if len(samples)!=len(only):raise ValueError('Subset IDs must belong to this group')
    with local_log(destination/'run.log'):
        for sample in samples:
            identifier=sample['sample_id']
            progress(identifier,'source-verification')
            try:
                # Asset metadata comes from the selected, bounded plan; the same
                # production function validates size/mtime/SHA and root containment.
                result=index_pipeline.analyze(root,sample,destination/identifier,args,
                    progress=lambda phase:progress(identifier,phase))
                derived=destination/identifier/(result['source_sha256'][:20]+'-'+result['processing_signature'][:20])
                record=dict(result,sample_id=identifier,asset_id=sample['asset_id'],profile=profile,
                            status='generated',supervisor_visual_review_complete=False)
                if sample['kind']=='photo':
                    record['details']={k:v for k,v in result.items() if k in {
                        'summary','description','objects','tags','spatial_relations','ocr_text',
                        'uncertainty_note','verification','ocr_verification'}}
                    record['review_images']=[{'at_ms':None,'path':str((derived/'input.jpg').relative_to(PROJECT))}]
                    animation=result.get('animation_frames',[])
                    if animation:
                        chosen=[animation[0],animation[len(animation)//2],animation[-1]]
                        record['review_images']=[{'at_ms':f['at_ms'],'path':str((derived/f'animation-{f["at_ms"]:07d}.jpg').relative_to(PROJECT))} for f in chosen]
                else:
                    frames=result['frames']
                    chosen=[frames[0],frames[len(frames)//2],frames[-1]]
                    record['review_images']=[{'at_ms':f['at_ms'],'path':str((derived/f'frame-{f["requested_at_ms"]:010d}.jpg').relative_to(PROJECT))} for f in chosen]
                source=app.resolve_media(root,sample['relative_path'],sample['kind'])
                assert source and digest(source)==result['source_sha256']
                assert result['original_unchanged'] is True
                (destination/(identifier+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
                records.append(record)
            except Exception as exc:
                traceback.print_exc();sys.stderr.flush()
                failure={'sample_id':identifier,'error_type':type(exc).__name__,'message':str(exc)}
                (destination/(identifier+'.error.json')).write_text(json.dumps(failure,ensure_ascii=False,indent=2)+'\n')
                failures.append({'sample_id':identifier,'error_type':type(exc).__name__})
        report={'round':number,'profile':profile,'status':'technical-failures' if failures else 'awaiting-supervisor',
            'generated_records':len(records),'photos':sum(r['kind']=='photo' for r in records),
            'videos':sum(r['kind']=='video' for r in records),'failures':failures,
            'elapsed_seconds':round(time.monotonic()-started,1),'supervisor_visual_review_complete':False,
            'production_started':False,'code_fingerprint':tested,
            'processing_signature':index_pipeline.processing_signature(),'code_unchanged':tested==code_fingerprint(),
            'complete_group':len(samples)==len(group['photos'])+len(group['videos'])}
        if not report['code_unchanged']:report['status']='code-changed-during-test'
        (destination/'round-report.json').write_text(json.dumps(report,indent=2)+'\n')
    progress('round','awaiting-supervisor')
    print(json.dumps(report))
    return 1 if failures or not report['code_unchanged'] else 0


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['run'])
    parser.add_argument('round',type=int,choices=range(1,11))
    parser.add_argument('--profile',default=__import__('grounded_index').PROFILE)
    parser.add_argument('--only',nargs='+')
    cli=parser.parse_args()
    raise SystemExit(run_round(cli.round,cli.profile,set(cli.only) if cli.only else None))
