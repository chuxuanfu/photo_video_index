#!/usr/bin/env python3
"""Explicit-root portable index CLI. Runtime network is confined to localhost."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import fcntl

import media_index as app
import single_library as library
from local_workflow import local_log

PROJECT = Path(__file__).resolve().parent
STATE = PROJECT / '.local'


def selected_root():
    file = STATE / 'library.json'
    if not file.exists():
        file = STATE / 'private-trial.json'  # Preserve an earlier explicitly authorized setup.
    if not file.exists():
        raise app.IndexErrorUser('Select a specific root first: ./library-local configure --root PATH')
    config = json.loads(file.read_text())
    if config.get('authorized') is not True:
        raise app.IndexErrorUser('The media root must be explicitly selected')
    return check_root(config['authorized_scope_root'])


def check_root(value):
    root = Path(value).expanduser().resolve(strict=True)
    if not root.is_dir() or root in {Path('/'),Path('/Users'),Path.home(),Path.home()/'Desktop',Path.home()/'Pictures',Path('/Volumes')}:
        raise app.IndexErrorUser('Select a specific media folder, not a home, disk or general personal directory')
    if root.name.casefold().endswith('.photoslibrary'):
        raise app.IndexErrorUser('Select exported media, not the Photos database')
    return root


def catalog_root(root):
    destination = root / 'index.json'
    if destination.is_symlink():
        raise app.IndexErrorUser('The index must not be a symlink')
    prior = app.read_index(destination) if destination.exists() else None
    if prior and (prior.get('schema_version'),prior.get('kind')) != (2,'library'):
        raise app.IndexErrorUser('Existing index uses a different format; it was preserved')
    data = library.catalog(root, prior)
    commit(root, data, prior)
    return data


def commit(root, data, prior):
    # A directory lock creates no extra file in the media root and survives
    # atomic replacement of index.json. Other checkouts use the same lock.
    descriptor=os.open(root,os.O_RDONLY)
    try:
        fcntl.flock(descriptor,fcntl.LOCK_EX)
        _commit_locked(root,data,prior)
    finally:
        os.close(descriptor)


def _commit_locked(root, data, prior):
    destination = root / 'index.json'
    current = app.read_index(destination) if destination.exists() else None
    if current != prior:
        raise app.IndexErrorUser('Concurrent index update detected; completed local caches were preserved')
    if data == prior:
        return
    if prior:
        previous = STATE / 'backups' / data['library_id']
        previous.mkdir(parents=True, exist_ok=True)
        app.write_index(previous / 'previous-index.json', prior)
    app.write_index(destination, data)
    destination.chmod(0o600)


def model_status():
    model = str(PROJECT / '.models/Qwen3.8-27B-4bit')
    config = app.load_config(PROJECT/'config.mlx.json')
    url = app.local_model_url(config.get('mlx_vlm_url','http://127.0.0.1:8080/v1'))
    try:
        request = urllib.request.Request(url+'/models')
        with app.local_model_open(request, 3) as response:
            data = json.load(response)
        rows = data['data']
        return any(row['id'] == model and row.get('loaded') is True for row in rows)
    except Exception:
        return False


def start_model():
    if model_status():
        return
    # Never attach to an unrelated listener or replace another process.
    import socket, urllib.parse
    config = app.load_config(PROJECT/'config.mlx.json')
    port = urllib.parse.urlsplit(app.local_model_url(config.get('mlx_vlm_url','http://127.0.0.1:8080/v1'))).port
    with socket.socket() as sock:
        if sock.connect_ex(('127.0.0.1',port)) == 0:
            raise app.IndexErrorUser('The configured local port is occupied by a different or not-ready service; nothing was stopped')
    with (STATE / 'model-service.log').open('ab') as log:
        process = subprocess.Popen([sys.executable,str(PROJECT/'local_runtime.py'),'model'],stdout=log,stderr=log,stdin=subprocess.DEVNULL,start_new_session=True)
    (STATE/'model-service.pid').write_text(str(process.pid)+'\n')
    started=time.monotonic()
    while time.monotonic()-started < 300:
        if model_status():
            return
        if process.poll() is not None:
            raise app.IndexErrorUser('Local model failed to start; see the local model log')
        time.sleep(1)
    raise app.IndexErrorUser('Model loading timed out; no cloud fallback was attempted')


def options():
    config=app.load_config(PROJECT/'config.mlx.json')
    if config.get('model_backend')!='mlx-vlm' or Path(config.get('vision_model','')).resolve()!=(PROJECT/'.models/Qwen3.8-27B-4bit').resolve():
        raise app.IndexErrorUser('This workflow requires the pinned local MLX vision checkpoint; no alternate backend is selected')
    app.local_model_url(config['mlx_vlm_url'])
    return app.parser(config).parse_args(['index'])


def analyze(limit, all_files, retry_review=False):
    root=selected_root()
    data=catalog_root(root)
    candidates=([a for a in data['assets'] if a['analysis_status'] in {'needs_review','partial'}] if retry_review else [])+[a for a in data['assets'] if a['analysis_status']=='pending']
    if not all_files:
        candidates=candidates[:limit]
    if all_files:
        gate=json.loads((PROJECT/'quality-release.json').read_text())
        from grounded_index import PROFILE
        from index_pipeline import processing_signature
        if gate.get('profile')!=PROFILE or gate.get('bulk_processing_ready') is not True or gate.get('processing_signature')!=processing_signature():
            raise app.IndexErrorUser('The supervised quality gate is still closed. Bounded analysis remains available.')
    if not candidates:
        return {'status':'passed','processed':0,'failed':0,**data['summary']}
    start_model()
    from index_pipeline import analyze as analyze_asset
    prior=copy.deepcopy(data)
    completed=failures=0
    args=options()
    for asset in candidates:
        folder=STATE/'library-cache'/data['library_id']/asset['id']
        folder.mkdir(parents=True,exist_ok=True)
        try:
            with local_log(folder/'run.log'):
                details=analyze_asset(root,asset,folder,args)
            asset.update({key:value for key,value in details.items() if key in library.DETAIL_KEYS})
            asset['analysis_status']='partial' if details.get('index_status')=='partial' else 'complete'
            library.add_time_links(asset)
            completed+=1
        except Exception as exc:
            import traceback
            with (folder/'run.log').open('a') as log:
                traceback.print_exc(file=log)
            asset['analysis_status']='needs_review'
            asset['analysis_error_type']=type(exc).__name__
            failures+=1
        if (completed+failures)%10==0:
            data=library.finalize(data,prior)
            commit(root,data,prior)
            prior=copy.deepcopy(data)
    data=library.finalize(data,prior)
    commit(root,data,prior)
    return {'status':'passed' if not failures else 'needs_review','processed':completed,'failed':failures,**data['summary']}


def self_test():
    from local_workflow import create_synthetic
    from index_pipeline import analyze as analyze_asset
    photos,videos=create_synthetic()
    with (STATE/'ocr-warmup.log').open('ab') as log:
        subprocess.run([sys.executable,str(PROJECT/'local_ocr.py'),str(photos/'shapes-a.png')],stdout=log,stderr=log,timeout=120,check=True)
    start_model()
    root=photos.parent
    checkpoint=STATE/'install-self-test/catalog.json'
    prior=app.read_index(checkpoint) if checkpoint.exists() else None
    data=library.catalog(root,prior)
    checkpoint.parent.mkdir(parents=True,exist_ok=True)
    app.write_index(checkpoint,data)
    results=[]
    for asset in data['assets']:
        with local_log(STATE/'install-self-test.log'):
            result=analyze_asset(root,asset,STATE/'install-self-test'/asset['id'],options(),interval_ms=3000)
        results.append(result)
    if len(results)!=3 or any(not r.get('original_unchanged') or not r.get('summary') for r in results):
        raise app.IndexErrorUser('Synthetic vision/video self-test failed')
    by_name={Path(r['relative_path']).name:r for r in results}
    first=json.dumps(by_name['shapes-a.png'],ensure_ascii=False)
    second=json.dumps(by_name['shapes-b.png'],ensure_ascii=False)
    if not all(word in first for word in ('红','圆','蓝','742')) or not any(word in first for word in ('方块','正方','方形')):
        raise app.IndexErrorUser('Synthetic circle/square/color/OCR facts failed')
    if not all(word in second for word in ('绿','三角','742')):
        raise app.IndexErrorUser('Synthetic triangle/color/OCR facts failed')
    clip=by_name['synthetic.mp4']
    if not any('red circle' in row['text'].casefold() for row in clip['transcript']):
        raise app.IndexErrorUser('Synthetic local speech verification failed')
    if not all(0<=f['at_ms']<clip['duration_ms'] for f in clip['frames']):
        raise app.IndexErrorUser('Synthetic video timestamp verification failed')
    note=STATE/'smoke/agent-note.txt'
    note.write_text('Synthetic data only. Run .venv-mlx/bin/python local_workflow.py synthetic exactly once and report its aggregate result.\n')
    agent=subprocess.run([sys.executable,str(PROJECT/'local_runtime.py'),'opencode','run','--agent','media-index','--format','json','Read .local/smoke/agent-note.txt and execute the allowed synthetic workflow once. Return its aggregate JSON.'],capture_output=True,text=True,timeout=600)
    (STATE/'install-agent-test.jsonl').write_text(agent.stdout)
    (STATE/'install-agent-test.stderr').write_text(agent.stderr)
    events=[]
    for line in agent.stdout.splitlines():
        try:events.append(json.loads(line))
        except ValueError:continue
    tools=[event['part'] for event in events if event.get('type')=='tool_use']
    shells=[part for part in tools if part.get('tool')=='shell']
    successful=False
    if len(shells)==1:
        state=shells[0].get('state',{})
        try:
            outcome=json.loads(state.get('output',''))
            successful=(state.get('status')=='completed' and state.get('input',{}).get('command')=='.venv-mlx/bin/python local_workflow.py synthetic' and outcome.get('status')=='passed' and outcome.get('synthetic') is True)
        except ValueError:pass
    if agent.returncode or not successful or any(event.get('type')=='error' for event in events):
        raise app.IndexErrorUser('The local agent tool-call test did not pass')
    result={'status':'passed','synthetic_photos':2,'synthetic_videos':1,'local_agent_test':True,'private_media_scanned':False}
    (STATE/'install-validation.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def main():
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='command',required=True)
    configure=sub.add_parser('configure');configure.add_argument('--root',required=True)
    for command in ('catalog','status','start-model','self-test'):
        sub.add_parser(command)
    analysis=sub.add_parser('analyze')
    group=analysis.add_mutually_exclusive_group(required=True)
    group.add_argument('--limit',type=int);group.add_argument('--all',action='store_true')
    analysis.add_argument('--retry-review',action='store_true',help='Include explicitly flagged partial/review items in the bounded batch')
    args=parser.parse_args()
    STATE.mkdir(exist_ok=True)
    os.umask(0o077)
    if args.command=='configure':
        root=check_root(args.root)
        (STATE/'library.json').write_text(json.dumps({'authorized':True,'authorized_scope_root':str(root)},indent=2)+'\n')
        result={'status':'passed','root_selected':True,'private_media_scanned':False}
    elif args.command=='catalog':result={'status':'passed',**catalog_root(selected_root())['summary']}
    elif args.command=='status':
        file=selected_root()/'index.json'
        data=app.read_index(file) if file.exists() else None
        result={'status':'passed','model_loaded':model_status(),'catalog_present':bool(data),**(data['summary'] if data else {})}
    elif args.command=='analyze':
        if args.limit is not None and not 1<=args.limit<=100:
            raise app.IndexErrorUser('A bounded batch limit must be 1..100; use explicit --all only after quality validation')
        result=analyze(args.limit,args.all,args.retry_review)
    elif args.command=='start-model':start_model();result={'status':'passed','model_loaded':True}
    else:result=self_test()
    print(json.dumps(result))
    return 0 if result['status']=='passed' else 1


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except app.IndexErrorUser as exc:
        print(json.dumps({'status':'blocked','reason':str(exc)}))
        raise SystemExit(1)
    except Exception as exc:
        import traceback
        STATE.mkdir(exist_ok=True)
        with (STATE/'library-error.log').open('a') as log:
            traceback.print_exc(file=log)
        print(json.dumps({'status':'failed','error_type':type(exc).__name__,'details':'retained in local log'}))
        raise SystemExit(1)
