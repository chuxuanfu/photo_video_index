"""Local-only index owner. Private contents never enter the supervision channel."""
from pathlib import Path
import ctypes, datetime, errno, fcntl, hashlib, json, logging, os, resource, signal, socket, sys, threading, time

CODE=Path(__file__).resolve().parent
sys.path.insert(0,str(CODE))
CFG=json.loads(Path(sys.argv[1]).read_text())
S=Path(CFG['state']); INDEX=Path(CFG['index_root']); HEALTH=Path(CFG['health']); PORT=CFG['port']
START=time.monotonic(); PHASE='starting'; LOCK=threading.Lock(); STOP=threading.Event()
COUNTS={'image_items':0,'video_windows':0,'model_calls':0,'committed_batches':0,
        'scope_skips':0,'cloud_placeholders_skipped':0,'unsupported_files_skipped':0}
NET={'blocked_probe_count':0,'unexpected_probe_success':0,'nonlocal_connect_attempts':0,'dns_attempts':0}
CORE=None; ROOT=None; LID=None; ERROR='none'; INITIALIZING=True
ALLOWED_PHASES={'starting','network_proof','pilot','full_index','ready','stopping','stopped','blocked'}

def put(path,obj):
    tmp=path.with_name(path.name+'.new')
    with tmp.open('w') as f:json.dump(obj,f);f.flush();os.fsync(f.fileno())
    tmp.replace(path)

def counts():
    out={'total':0,'indexed':0,'failed':0,'pending':0,'photos_indexed':0,'videos_indexed':0}
    if CORE is not None:
        for r in CORE.state.db.query('SELECT media_type,status,COUNT(*) AS n FROM assets GROUP BY media_type,status'):
            if r['media_type'] not in ('image','video'):continue
            n=int(r['n']);out['total']+=n
            if r['status'] in ('indexed','failed','pending'):out[r['status']]+=n
            if r['status']=='indexed':out[('photos_' if r['media_type']=='image' else 'videos_')+'indexed']+=n
    return out

def health():
    assert PHASE in ALLOWED_PHASES
    v={'schema':1,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
       'result_limit_max':50,'pid':os.getpid(),'instance_id':CFG['instance_id'],'phase':PHASE,'scope':'synthetic' if CFG['synthetic'] else 'authorized_memory',
       'elapsed_s':round(time.monotonic()-START,2),'error_code':ERROR,
       'result_limit_max':50,'scan_complete':(INDEX/'READY.json').exists() or (S/'scan-complete').exists(), **counts(), **COUNTS,
       'network_proof_passed':(S/'network-proof.json').exists(),
       'nonlocal_connect_attempts':NET['nonlocal_connect_attempts'],
       'dns_attempts':NET['dns_attempts'], 'unexpected_probe_success':NET['unexpected_probe_success']}
    with LOCK:put(HEALTH,v)
    return v

def blocked(code):
    global PHASE,ERROR
    PHASE='blocked';ERROR=code;health()

def check_network():
    # The OS policy, not a Python convention, must refuse each operation.
    for family,addr in ((socket.AF_INET,('1.1.1.1',443)),(socket.AF_INET,('10.255.255.1',9)),
                        (socket.AF_INET,('127.0.0.1',PORT+7)),(socket.AF_INET6,('::1',PORT+7))):
        with socket.socket(family,socket.SOCK_STREAM) as p:
            p.settimeout(1)
            rc=p.connect_ex(addr)
            if rc not in (errno.EPERM,errno.EACCES):
                NET['unexpected_probe_success']+=1;raise RuntimeError('network_policy_not_enforced')
            NET['blocked_probe_count']+=1
    with socket.socket() as p:
        try:p.bind(('0.0.0.0',PORT+8))
        except OSError as e:
            if e.errno not in (errno.EPERM,errno.EACCES):raise
            NET['blocked_probe_count']+=1
        else:raise RuntimeError('nonloopback_bind_allowed')
    put(S/'network-proof.json',{'passed':True,'blocked':NET['blocked_probe_count']})
    def audit(event,args):
        if event=='socket.getaddrinfo':
            NET['dns_attempts']+=1
            raise PermissionError('offline_dns_disabled')
        if event=='socket.connect':
            NET['nonlocal_connect_attempts']+=1
            raise PermissionError('worker_outbound_disabled')
    sys.addaudithook(audit)

def local_filesystem(path):
    # Darwin statfs64: f_flags at byte 64, MNT_LOCAL=0x1000 (SDK sys/mount.h).
    buf=ctypes.create_string_buffer(4096)
    libc=ctypes.CDLL(None,use_errno=True)
    rc=libc.statfs(os.fsencode(path),ctypes.byref(buf))
    if rc:raise OSError(ctypes.get_errno(),'root_filesystem_unavailable')
    flags=int.from_bytes(buf.raw[64:68],sys.byteorder)
    return bool(flags&0x1000)

def monitor():
    while not STOP.wait(3):
        try:
            health()
            # Unified GPU allocation is separately capped by torch below.
            if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss>26*1024**3:
                blocked('memory_budget');STOP.set()
        except Exception:
            blocked('health_internal');STOP.set()

def index_all():
    global PHASE,ERROR
    try:
        import mediaindex.ingest as ingest
        from mediaindex.jobs import JobContext,Job
        pilot=not (S/'pilot-passed').exists()
        phase_limit=8 if pilot else None
        def walk(root,extensions,skipped=None):
            extensions=extensions & (ingest.IMAGE_EXTENSIONS|ingest.VIDEO_EXTENSIONS)
            n=0;device=os.lstat(root).st_dev
            for key in ('scope_skips','cloud_placeholders_skipped','unsupported_files_skipped'):COUNTS[key]=0
            for directory,dirs,files in os.walk(root,followlinks=False):
                allowed=[]
                for name in sorted(dirs):
                    p=Path(directory)/name
                    if p==INDEX or name.startswith('.'):continue
                    st=os.lstat(p)
                    if p.is_symlink() or st.st_dev!=device:COUNTS['scope_skips']+=1;continue
                    if getattr(st,'st_flags',0)&0x40000000:COUNTS['cloud_placeholders_skipped']+=1;continue
                    allowed.append(name)
                dirs[:]=allowed
                for name in sorted(files):
                    if STOP.is_set():return
                    if name.startswith('.'):continue
                    p=Path(directory)/name
                    st=os.lstat(p)
                    if p.is_symlink() or st.st_dev!=device:COUNTS['scope_skips']+=1;continue
                    if getattr(st,'st_flags',0)&0x40000000:COUNTS['cloud_placeholders_skipped']+=1;continue
                    if p.suffix.lower() not in extensions:COUNTS['unsupported_files_skipped']+=1;continue
                    yield p,p.relative_to(root).as_posix();n+=1
                    if phase_limit is not None and n>=phase_limit:return
        ingest.walk_files=walk
        def run(limited):
            nonlocal phase_limit
            phase_limit=8 if limited else None
            job=Job(id='pilot' if limited else 'full',kind='import',library_id=LID)
            job.status='running';job.created=time.time()
            def progress(j):
                if STOP.is_set():j._cancel.set()
                CORE.state.store.save_job(j.to_dict(),LID)
            result=ingest.run_image_import(JobContext(job,progress),CORE.state.store,
                     CORE.state.settings.thumbs_dir,LID,CORE.state.embed_batch,batch_size=2,
                     embed_audio=None,embed_video=CORE.state.embed_video)
            job.result=result;job.status='cancelled' if result.get('cancelled') else 'done';job.finished=time.time()
            CORE.state.store.save_job(job.to_dict(),LID)
            return result
        if pilot:
            PHASE='pilot';health();run(True)
            c=counts()
            if c['indexed']<1 or c['pending'] or c['failed']:
                blocked('pilot_not_healthy');return
            (S/'pilot-passed').write_text('1')
        if STOP.is_set():return
        PHASE='full_index';health();run(False)
        if not STOP.is_set():
            (S/'scan-complete').write_text('1');PHASE='ready';health()
    except PermissionError:blocked('permission')
    except MemoryError:blocked('memory_budget')
    except Exception:blocked('index_internal')

def main():
    global PHASE,ROOT,CORE,LID,INITIALIZING
    os.umask(0o077);resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    lock=(S/'owner.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    logging.disable(logging.CRITICAL)
    PHASE='network_proof';health();check_network()
    ROOT=Path(CFG['root'])
    if ROOT.is_symlink() or not ROOT.is_absolute():blocked('root_invalid');return 43
    if not local_filesystem(ROOT):blocked('root_not_local');return 43
    if not ROOT.is_dir():blocked('root_unavailable');return 43
    if CFG['synthetic']:
        canary=ROOT/'readonly-canary'
        try:
            fd=os.open(canary,os.O_WRONLY|os.O_APPEND);os.close(fd)
        except OSError as e:
            if e.errno not in (errno.EPERM,errno.EACCES):raise
        else:blocked('original_write_not_denied');return 43
    sys.path.insert(0,str(Path(CFG.get('backend_code',CODE/'backend'))))
    import torch
    torch.set_num_threads(4)
    if CFG['device']=='mps' and torch.backends.mps.is_available():torch.mps.set_per_process_memory_fraction(.35)
    from dataclasses import replace
    from mediaindex.app import create_app
    from mediaindex.config import Settings
    from mediaindex.model.backend import GemmaBackend
    class FixedSettings(Settings):
        def index_profile(self):
            return replace(super().index_profile(),encoders=('text','image'),video_modalities=('video-visual',))
    settings=FixedSettings(data_dir=INDEX,port=PORT,watch_interval=0,chat_model='',device=CFG['device'],query_wait_seconds=30)
    class Counted:
        def __init__(self,p):self.b=GemmaBackend(p,device=CFG['device'],offline=True)
        def __getattr__(self,n):return getattr(self.b,n)
        def embed_images(self,images):
            v=self.b.embed_images(images);COUNTS['image_items']+=len(images);COUNTS['model_calls']+=1;return v
        def embed_video(self,videos,fps):
            v=self.b.embed_video(videos,fps);COUNTS['video_windows']+=len(videos);COUNTS['model_calls']+=1;return v
    completed=(INDEX/'READY.json').exists() or (CFG['synthetic'] and (S/'scan-complete').exists())
    if not CFG['synthetic'] and not completed:
        blocked('index_internal');return 43
    if completed:
        from readonly_search_core import open_core
        CORE=open_core(settings,Counted)
    else:
        CORE=create_app(settings,backend_factory=Counted,chat=None,editor=None,painter=None)
    assert CORE.state.profile.model_id=='google/embeddinggemma-2'
    assert CORE.state.profile.revision=='914f7f89142e33e77833254d9c9b90c3cef7303b'
    if not completed:CORE.state.store.recover_interrupted_jobs()
    libs=CORE.state.store.list_libraries()
    if libs:
        if len(libs)!=1 or libs[0]['root_path']!=str(ROOT):blocked('library_scope_mismatch');return 43
        lib=libs[0]
    else:lib=CORE.state.store.create_library('Local Memory',ROOT)
    LID=lib['id']
    if not completed:
        native=CORE.state.embed_batch
        def batch(items):
            v=native(items);COUNTS['committed_batches']+=1;health();return v
        batch.profile_key=native.profile_key;batch.modality=native.modality;CORE.state.embed_batch=batch
    import mediaindex.api_search as api_search
    from mediaindex.search import rank
    from search_limits import FILE_LIMIT,validate_limit,rank_files
    # Expand window candidates until the requested number of unique files is
    # available. Default 10 retains the existing order and numerical scores.
    def rank_complete(q,vm,limit,exclude_assets=None,only_assets=None):
        return rank_files(rank,q,vm,limit,exclude_assets,only_assets)
    api_search.rank=rank_complete
    import uvicorn
    from fastapi import FastAPI,HTTPException,Request
    from fastapi.responses import FileResponse,HTMLResponse,JSONResponse
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    from photo_review_runtime import PhotoReview
    photo=PhotoReview(CORE,ROOT,LID,CODE,resources=CFG['resources'],synthetic=CFG['synthetic'])
    ui_samples=[]
    @app.middleware('http')
    async def safe(request,call_next):
        if request.headers.get('host')!=f'127.0.0.1:{PORT}':return JSONResponse({'error':'local_only'},status_code=403)
        origin=request.headers.get('origin')
        if origin and origin!=f'http://127.0.0.1:{PORT}':return JSONResponse({'error':'local_only'},status_code=403)
        response=await call_next(request);response.headers['Cache-Control']='no-store'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Content-Security-Policy']="default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self'; media-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        return response
    @app.get('/')
    def home():return HTMLResponse((CODE/'ui.html').read_text())
    @app.get('/local-status')
    def status():return health()
    @app.post('/search')
    async def search(request:Request):
        body=await request.json();text=body.get('text','');mode=body.get('mode','image')
        if not isinstance(text,str) or not text.strip() or len(text)>500 or mode not in ('image','video','mixed'):raise HTTPException(422,'invalid_query')
        try:limit=validate_limit(body.get('limit',10))
        except ValueError:raise HTTPException(422,'result_limit_1_to_50')
        vec,ms=api_search.embed_query(CORE,lambda b:b.embed_query_texts([text.strip()])[0])
        token=FILE_LIMIT.set(limit)
        try:
            out=api_search.run_search(CORE,mode='text',query_info={},vec=vec,embed_ms=ms,library_ids=[LID],media_types=['image','video'] if mode=='mixed' else [mode],limit=limit,global_rank=mode=='mixed',video_modality='video-visual')
        finally:FILE_LIMIT.reset(token)
        out['requested_limit']=limit
        assert len(out['results'])<=limit and len({r['asset']['id'] for r in out['results']})==len(out['results'])
        if mode in ('image','mixed') and any(row['asset']['media_type']=='image' for row in out['results']):
            jid=photo.submit(text.strip(),out['results'],limit=limit,fault=body.get('_public_fault') if CFG['synthetic'] else None,native_only=bool(body.get('_public_native_only')) if CFG['synthetic'] else False)
            out['photo_review_job']=jid
            out['photo_review_default']=photo.enabled()
        return out
    @app.get('/photo-review/{jid}')
    def photo_job(jid:str):
        if len(jid)!=32 or any(x not in '0123456789abcdef' for x in jid):raise HTTPException(404)
        result=photo.get(jid)
        if result is None:raise HTTPException(404)
        return result
    @app.get('/photo-status')
    def photo_status():return photo.status()
    @app.post('/photo-ui-receipt')
    async def photo_ui_receipt(request:Request):
        body=await request.json();allowed={'first_cards_ms','first_confirmed_ms','terminal_ms','returned','confirmed','remaining','fallback','mode'}
        if not isinstance(body,dict) or set(body)-allowed:raise HTTPException(422)
        for key,value in body.items():
            if key=='mode':
                if value not in ('image','video','mixed'):raise HTTPException(422)
            elif key=='fallback':
                if not isinstance(value,bool):raise HTTPException(422)
            elif value is not None and (not isinstance(value,(float,int)) or isinstance(value,bool) or value<0 or value>3600000):raise HTTPException(422)
        ui_samples.append(dict(body));del ui_samples[:-64]
        return {'saved':True}
    @app.get('/public-ui-receipts')
    def public_ui_receipts():
        if not CFG['synthetic']:raise HTTPException(404)
        return {'samples':ui_samples}
    @app.get('/api/assets/{aid}/{kind}')
    def original(aid:str,kind:str):
        if kind not in ('file','thumbnail','window-thumbnail'):raise HTTPException(404)
        asset=CORE.state.store.get_asset(aid)
        if not asset or asset['library_id']!=LID:raise HTTPException(404)
        if kind!='file':
            from mediaindex.ingest import thumb_path
            p=thumb_path(settings.thumbs_dir,asset['content_hash'])
        else:
            from mediaindex.paths import resolve_in_root
            p=resolve_in_root(ROOT,asset['rel_path'])
        if not p.is_file():raise HTTPException(404)
        return FileResponse(p)
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=PORT,access_log=False,log_level='critical'))
    def stop(*_):
        global PHASE
        PHASE='stopping';STOP.set();server.should_exit=True;health()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    INITIALIZING=False
    threading.Thread(target=monitor,daemon=True).start()
    if completed:
        PHASE='ready';health()
    else:threading.Thread(target=index_all,daemon=True).start()
    try:server.run()
    finally:
        STOP.set()
        PHASE='stopped';health()
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception:
        try:blocked('startup_internal')
        except Exception:pass
        raise SystemExit(44)
