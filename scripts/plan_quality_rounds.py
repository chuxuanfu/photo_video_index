#!/usr/bin/env python3
"""Extend an explicitly selected local test plan; never uploads or runs inference."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import random
import sys

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
import media_index as app
from media_library import check_root
from local_workflow import local_log


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--groups',type=int,required=True)
    args=parser.parse_args()
    if not 1<=args.groups<=20:raise SystemExit('Choose 1..20 bounded test groups')
    base=PROJECT/'.local/quality-review'
    path=base/'manifest.json'
    manifest=json.loads(path.read_text())
    root=check_root(manifest['authorized_scope_root'])
    catalog=app.read_index(root/'index.json')
    if catalog['library_id']!=manifest['library_id']:raise SystemExit('Selected library identity changed')
    used={s['relative_path'] for group in manifest['rounds'] for kind in ('photos','videos') for s in group[kind]}
    rng=random.Random(20261116)
    photos=[a for a in catalog['assets'] if a['kind']=='photo' and a['relative_path'] not in used and a['analysis_status'] not in {'complete','partial'}]
    rng.shuffle(photos)
    video_pool=[a for a in catalog['assets'] if a['kind']=='video' and a['relative_path'] not in used and Path(a['relative_path']).suffix.casefold() in {'.mov','.mp4'} and a['source_size']<400*1024**2]
    rng.shuffle(video_pool)
    video_metadata={}
    probes=0
    def eligible_videos(extension,folders):
        nonlocal probes
        fallback=[]
        for asset in video_pool:
            relative=asset['relative_path']
            if relative in used or Path(relative).suffix.casefold()!=extension:continue
            if relative not in video_metadata:
                if probes>=240:continue
                source=app.resolve_media(root,relative,'video')
                probes+=1
                try:
                    duration,audio=app.video_info(app.ffmpeg_path(),source)
                    video_metadata[relative]=(duration,audio) if 5000<=duration<=60000 else None
                except Exception:video_metadata[relative]=None
            if video_metadata[relative] is None:continue
            if asset['parent_relative_path'] not in folders:return asset
            fallback.append(asset)
        return fallback[0] if fallback else None
    def eligible_photo(extension,folders):
        fallback=[]
        for asset in photos:
            relative=asset['relative_path']
            if relative in used or Path(relative).suffix.casefold()!=extension:continue
            if extension=='.gif':
                from PIL import Image
                source=app.resolve_media(root,relative,'photo')
                try:
                    with Image.open(source) as image:
                        if getattr(image,'n_frames',1)>300:continue
                        duration=0
                        for i in range(getattr(image,'n_frames',1)):
                            image.seek(i);duration+=max(10,int(image.info.get('duration',100)))
                        if duration>10000:continue
                except Exception:continue
            if asset['parent_relative_path'] not in folders:return asset
            fallback.append(asset)
        return fallback[0] if fallback else None
    def item(asset,identifier):
        return {'sample_id':identifier,'asset_id':asset['id'],'kind':asset['kind'],
                'relative_path':asset['relative_path'],'format':Path(asset['relative_path']).suffix.casefold(),
                'source_size':asset['source_size'],'source_mtime_ns':asset['source_mtime_ns']}
    with local_log(base/'plan-build.log'):
        while len(manifest['rounds'])<args.groups:
            number=len(manifest['rounds'])+1
            group={'round':number,'photos':[],'videos':[]}
            folders=set()
            for extension,count in [('.jpg',4),('.jpeg',3),('.heic',2),('.png',1),('.dng',1),('.gif',1)]:
                for _ in range(count):
                    asset=eligible_photo(extension,folders)
                    if asset is None:raise app.IndexErrorUser('Not enough distinct bounded photo test inputs')
                    group['photos'].append(item(asset,f'R{number}P{len(group["photos"])+1:02d}'))
                    used.add(asset['relative_path']);folders.add(asset['parent_relative_path'])
            folders=set()
            for extension in ('.mov','.mov','.mp4'):
                asset=eligible_videos(extension,folders)
                if asset is None:raise app.IndexErrorUser('Not enough eligible short test videos within the probe cap')
                row=item(asset,f'R{number}V{len(group["videos"])+1:02d}')
                row['duration_ms'],row['has_audio']=video_metadata[asset['relative_path']]
                group['videos'].append(row)
                used.add(asset['relative_path']);folders.add(asset['parent_relative_path'])
            manifest['rounds'].append(group)
    manifest['requested_video_count']=3*args.groups
    manifest['target_passed_groups']=args.groups
    manifest['all_previous_groups_must_pass_after_each_change']=True
    manifest['acceptance']={'target_passed_groups':args.groups,'all_previous_groups_regressed':True,
        'no_serious_unresolved_fact_path_timestamp_or_original_mutation_error':True,
        'uncertain_audio_or_text_must_not_be_published_as_confirmed':True,'bulk_processing_during_testing':False}
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    temporary.replace(path)
    lines=['# PRIVATE — bounded quality test plan','',f'Target: {args.groups} accepted groups; regress every earlier group after changes.','No production bulk processing or backup synchronization.','']
    for group in manifest['rounds']:
        lines.append(f'## Group {group["round"]}')
        lines.extend(f'- {s["sample_id"]}: {s["relative_path"]}' for key in ('photos','videos') for s in group[key])
        lines.append('')
    (base/'TEST_PLAN.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'status':'planned','groups':len(manifest['rounds']),
        'photos':sum(len(g['photos']) for g in manifest['rounds']),'videos':sum(len(g['videos']) for g in manifest['rounds']),
        'short_video_metadata_probes':probes,'media_uploaded':0,'production_started':False}))


if __name__=='__main__':raise SystemExit(main())
