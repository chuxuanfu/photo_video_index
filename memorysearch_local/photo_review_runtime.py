"""Default photo presentation review. Private jobs stay in process RAM and loopback.

Native search and file membership are untouched. Only an existing Top10 photo
may be decoded. Missing/failed evidence never deletes a native candidate.
"""
from pathlib import Path
import copy,json,time,threading,queue,uuid,collections

def aid(row):return row['asset']['id']
def projection(rows,decisions=None,*,pending=False,fallback=None):
    original=copy.deepcopy(rows)
    if len(original)>10 or len({aid(x) for x in original})!=len(original):raise ValueError('native_page_contract')
    if fallback:
        return dict(results=original,original_results=copy.deepcopy(original),confirmed=[],remaining=[],photo_review=dict(stage='fallback',reason=fallback,complete=True))
    yes=[];rest=[];decisions=decisions or {}
    for row in copy.deepcopy(rows):
        d=decisions.get(aid(row),'PENDING');d=d if d in ('PASS','FAIL','UNKNOWN','PENDING') else 'UNKNOWN'
        row['photo_review_decision']=d
        (yes if d=='PASS' else rest).append(row)
    return dict(results=yes+rest,original_results=original,confirmed=yes,remaining=rest,photo_review=dict(stage='verifying' if pending else 'complete',complete=not pending))

class PhotoReview:
    def __init__(self,core,root,library_id,code,*,resources,synthetic=False):
        self.core=core;self.root=Path(root);self.lid=library_id;self.code=Path(code);self.synthetic=synthetic;self.resources=resources
        self.jobs=collections.OrderedDict();self.lock=threading.RLock();self.todo=queue.Queue(8);self.gate=None;self.gate_failed=False
        self.stats=dict(model_loads=0,model_calls=0,completed=0,fallbacks=0,active=0,queue_refusals=0)
        threading.Thread(target=self.loop,daemon=True).start()
    def enabled(self):
        try:
            policy=Path(self.resources['policy'])
            j=json.loads((policy if policy.is_file() else self.code/'photo-review-policy.json').read_text())
            return set(j)=={'schema','enabled'} and j['schema']==1 and j['enabled'] is True
        except Exception:return False
    def status(self):
        with self.lock:return dict(schema=1,enabled=self.enabled(),model_loaded=self.gate is not None,queued=self.todo.qsize(),**self.stats)
    def submit(self,text,rows,*,fault=None,native_only=False):
        if not self.enabled() or self.synthetic and native_only:return None
        jid=uuid.uuid4().hex
        with self.lock:
            while len(self.jobs)>=32:
                old=next((k for k,v in self.jobs.items() if v['out']['photo_review']['complete']),None)
                if old is None:self.stats['queue_refusals']+=1;return None
                del self.jobs[old]
            job=dict(text=text,rows=copy.deepcopy(rows),fault=fault if self.synthetic else None,begin=time.monotonic(),out=projection(rows,pending=True))
            try:self.todo.put_nowait((jid,job))
            except queue.Full:self.stats['queue_refusals']+=1;return None
            self.jobs[jid]=job
        return jid
    def get(self,jid):
        with self.lock:
            job=self.jobs.get(jid)
            if job is None:return None
            if time.monotonic()-job['begin']>40 and not job['out']['photo_review']['complete']:
                job['out']=projection(job['rows'],fallback='timeout');self.stats['fallbacks']+=1
            return copy.deepcopy(job['out'])
    def get_gate(self):
        if self.gate_failed:raise RuntimeError('verifier_unavailable')
        if self.gate is None:
            from baseline_gate import VisualGate
            from runtime_compat import install_counter,contract_test
            if not contract_test()['passed']:raise RuntimeError('verifier_interface')
            try:
                self.gate=VisualGate(Path(self.resources['verifier_model_root']),self.code/'VERIFIER-MODEL-LOCK.json')
                install_counter(self.gate);self.stats['model_loads']+=1
            except Exception:self.gate_failed=True;raise
        return self.gate
    def loop(self):
        while True:
            jid,job=self.todo.get();decisions={};first=None
            with self.lock:self.stats['active']=1
            try:
                for row in job['rows']:
                    with self.lock:
                        if job['out']['photo_review']['complete']:break
                    if job['fault']=='error':raise RuntimeError('injected_boundary_failure')
                    if job['fault']=='timeout' or time.monotonic()-job['begin']>40:raise TimeoutError()
                    # Lock shared with the resident native encoder, including model
                    # load. No concurrent Gemma/4B inference and no second encoder.
                    with self.core.state.host.use(timeout=30):
                        gate=self.get_gate()
                        asset=self.core.state.store.get_asset(aid(row))
                        if not asset or asset['library_id']!=self.lid or asset['media_type']!='image':raise ValueError('photo_scope')
                        from mediaindex.paths import resolve_in_root
                        from PIL import Image,ImageOps
                        path=resolve_in_root(self.root,asset['rel_path'])
                        with Image.open(path) as im:rgb=ImageOps.exif_transpose(im).convert('RGB')
                        try:v=gate.confirm(job['text'],[rgb])
                        finally:rgb.close()
                        self.stats['model_calls']+=1
                    if time.monotonic()-job['begin']>40:raise TimeoutError()
                    d=v.get('verdict');decisions[aid(row)]=d if d in ('PASS','FAIL','UNKNOWN') else 'UNKNOWN'
                    if d=='PASS' and first is None:first=time.monotonic()-job['begin']
                    with self.lock:
                        if not job['out']['photo_review']['complete']:
                            job['out']=projection(job['rows'],decisions,pending=True)
                            job['out']['photo_review'].update(completed=len(decisions),total=len(job['rows']),first_confirmed_s=first)
                with self.lock:
                    if not job['out']['photo_review']['complete']:
                        job['out']=projection(job['rows'],decisions)
                        job['out']['photo_review'].update(completed=len(decisions),total=len(job['rows']),first_confirmed_s=first,wall_s=time.monotonic()-job['begin'])
            except Exception as exc:
                with self.lock:
                    why='timeout' if isinstance(exc,TimeoutError) else 'verifier_unavailable'
                    job['out']=projection(job['rows'],fallback=why);self.stats['fallbacks']+=1
            finally:
                with self.lock:
                    self.stats['completed']+=1;self.stats['active']=0
                    # Query text is not retained once the background job finishes.
                    job.pop('text',None)
                self.todo.task_done()
