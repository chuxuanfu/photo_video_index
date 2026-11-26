"""Human launchers. Never discover a library or rebuild an index implicitly."""
from __future__ import annotations
from pathlib import Path
import argparse,fcntl,hashlib,json,os,signal,socket,subprocess,sys,time,urllib.request,uuid
REPO=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(REPO))
from memorysearch_local.config import Config,DEFAULT_CONFIG,LocalError,MESSAGES,load,private_write,storage_gate
from memorysearch_local.isolation import client_policy,runtime_environment,worker_policy,maintenance_policy


PUBLIC_FIELDS={'result_limit_max','schema','utc','pid','phase','scope','error_code','scan_complete','total','indexed','failed','pending','photos_indexed','videos_indexed','image_items','video_windows','model_calls','committed_batches','network_proof_passed','nonlocal_connect_attempts','dns_attempts','unexpected_probe_success','scope_skips','cloud_placeholders_skipped','unsupported_files_skipped'}
def emit(value):print(json.dumps(value,ensure_ascii=False,sort_keys=True))
def health(config):
 try:
  value=json.loads(config.health.read_text())
  if value.get('phase') not in ('starting','network_proof','pilot','full_index','ready','stopping','stopped','blocked'):raise ValueError()
  return value
 except Exception:return {'schema':1,'phase':'not_started','pid':0,'error_code':'none'}
def public_status(config):
 value=health(config);out={}
 enums={'phase':{'starting','network_proof','pilot','full_index','ready','stopping','stopped','blocked','not_started'},'scope':{'synthetic','authorized_memory'},'error_code':{'none','memory_budget','health_internal','pilot_not_healthy','permission','index_internal','root_invalid','root_not_local','root_unavailable','original_write_not_denied','library_scope_mismatch','startup_internal'}}
 for key,val in value.items():
  if key not in PUBLIC_FIELDS:continue
  if key in enums:
   if isinstance(val,str) and val in enums[key]:out[key]=val
  elif key=='utc':
   if isinstance(val,str) and len(val)<48 and all(c in '0123456789-:+.TZ' for c in val):out[key]=val
  elif isinstance(val,(int,bool)) and val>=0:out[key]=val
 return out
def alive(pid):
 if not isinstance(pid,int) or pid<=0:return False
 try:os.kill(pid,0);return True
 except ProcessLookupError:return False
 except PermissionError:return True
def request(config,path):
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 with opener.open(f'http://127.0.0.1:{config.port}'+path,timeout=10) as response:return json.load(response)
def owns_service(config):
 try:
  proof=json.loads((config.state/'owner.json').read_text())
  value=request(config,'/local-status')
  return value.get('instance_id')==proof['instance_id'] and value.get('pid')==proof['pid'] and alive(proof['pid'])
 except Exception:return False
def port_free(port):
 with socket.socket() as sock:
  sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
  try:sock.bind(('127.0.0.1',port));return True
  except OSError:return False
def build_native(config):
 target=config.runtime/'bin';target.mkdir(parents=True,exist_ok=True,mode=0o700)
 cache=config.runtime/'build-cache';cache.mkdir(exist_ok=True,mode=0o700)
 for name in ['NativePhotoViewer','StorageProbe']:
  source=REPO/'memorysearch_local/native'/(name+'.swift');binary=target/name
  expected=hashlib.sha256(source.read_bytes()).hexdigest();stamp=target/(name+'.source-sha256')
  if binary.is_file() and stamp.is_file() and stamp.read_text().strip()==expected:continue
  staging=binary.with_name(binary.name+'.building')
  result=subprocess.run(['/usr/bin/swiftc','-O','-module-cache-path',str(cache),str(source),'-o',str(staging)],capture_output=True,text=True,timeout=120)
  if result.returncode:raise LocalError('runtime_missing')
  os.replace(staging,binary);stamp.write_text(expected+'\n');binary.chmod(0o700)
def probe_storage(config):
 build_native(config)
 result=subprocess.run([str(config.runtime/'bin/StorageProbe'),str(config.media.parent),str(config.media)],capture_output=True,text=True,timeout=20)
 if result.returncode:raise LocalError('sync_unconfirmed')
 try:return json.loads(result.stdout)
 except Exception:raise LocalError('sync_unconfirmed') from None
def prepare_environment(config):
 config.runtime.mkdir(parents=True,exist_ok=True,mode=0o700);config.runtime.chmod(0o700)
 config.state.mkdir(parents=True,exist_ok=True,mode=0o700)
 for path in [config.python,config.encoder,config.verifier,config.native]:
  if not path.exists():raise LocalError('runtime_missing')
 cache=config.runtime/'cache/hub/models--google--embeddinggemma-2/snapshots'
 cache.mkdir(parents=True,exist_ok=True,mode=0o700)
 revision='914f7f89142e33e77833254d9c9b90c3cef7303b';link=cache/revision
 if not link.exists():link.symlink_to(config.encoder,target_is_directory=True)
 elif link.resolve()!=config.encoder.resolve():raise LocalError('runtime_missing')
 build_native(config)
def start(config):
 config.state.mkdir(parents=True,exist_ok=True,mode=0o700)
 with (config.state/'controller.lock').open('a') as lock:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:return {'phase':'starting','message':'已有启动操作正在执行；未另起实例。'}
  # HTTP is not listening while the resident model is loading. Match the
  # owner nonce against health before checking the port to avoid a duplicate.
  try:
   owner=json.loads((config.state/'owner.json').read_text());value=health(config)
   if alive(owner['pid']) and value.get('instance_id')==owner['instance_id'] and value.get('pid')==owner['pid']:
    if value.get('phase') in ('starting','network_proof','pilot','full_index','stopping','blocked'):return public_status(config)
  except (OSError,ValueError,KeyError,TypeError):pass
  return _start_locked(config)
def _start_locked(config):
 if owns_service(config):return public_status(config)
 probe=probe_storage(config);storage_gate(config,probe)
 if not config.synthetic:
  if not (config.index/'READY.json').is_file() or not (config.index/'mediaindex.sqlite3').is_file():raise LocalError('index_not_ready')
 if not port_free(config.port):raise LocalError('port_busy')
 prepare_environment(config)
 instance=uuid.uuid4().hex
 worker_config={'state':str(config.state),'health':str(config.health),'port':config.port,'synthetic':config.synthetic,'root':str(config.media),'index_root':str(config.index),'device':config.device,'instance_id':instance,'backend_code':str(REPO/'memorysearch_local/vendor'),'resources':{'verifier_model_root':str(config.verifier),'policy':str(config.policy)}}
 private_write(config.state/'worker-config.json',worker_config)
 (config.state/'worker.sb').write_text(worker_policy(config))
 child=subprocess.Popen(['/usr/bin/sandbox-exec','-f',str(config.state/'worker.sb'),str(config.python),'-I',str(REPO/'memorysearch_local/worker.py'),str(config.state/'worker-config.json')],cwd=REPO/'memorysearch_local',env=runtime_environment(config),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
 private_write(config.state/'owner.json',{'pid':child.pid,'instance_id':instance})
 deadline=time.monotonic()+120
 while time.monotonic()<deadline:
  value=health(config)
  if value.get('pid')==child.pid and value.get('phase') in ('ready','blocked'):return public_status(config)
  if child.poll() is not None:raise LocalError('start_failed')
  time.sleep(.2)
 return {'phase':'starting','pid':child.pid,'message':'模型仍在准备，请查看状态；未启动第二个实例。'}
def stop(config):
 value=health(config)
 if not alive(value.get('pid',0)):return {'phase':'stopped','changes_made':False}
 if not owns_service(config):raise LocalError('service_busy')
 pid=value['pid'];os.kill(pid,signal.SIGTERM)
 deadline=time.monotonic()+45
 while time.monotonic()<deadline:
  value=health(config)
  if value.get('phase')=='stopped' or not alive(pid):return {'phase':'stopped','changes_made':True}
  time.sleep(.2)
 return {'phase':'stopping','message':'正在等待已有请求结束；未强制终止。'}
def open_viewer(config,verify=False):
 status=start(config)
 if status.get('phase')!='ready':return status
 state=config.runtime/'client';state.mkdir(parents=True,exist_ok=True,mode=0o700)
 binary=config.runtime/'bin/NativePhotoViewer';box=state/'client.sb';box.write_text(client_policy(binary,state,config.port))
 env={'PATH':'/usr/bin:/bin','TMPDIR':str(state)+'/','MEMORYSEARCH_PORT':str(config.port),'MEMORYSEARCH_CLIENT_STATE':str(state),'DO_NOT_TRACK':'1','OTEL_SDK_DISABLED':'true'}
 if verify:
  if not config.synthetic:raise LocalError('config_invalid')
  result=subprocess.run(['/usr/bin/sandbox-exec','-f',str(box),str(binary),'--verify-synthetic'],cwd=state,env=env,capture_output=True,text=True,timeout=90)
  try:value=json.loads(result.stdout)
  except Exception:raise LocalError('start_failed') from None
  if result.returncode or value.get('passed') is not True:raise LocalError('start_failed')
  return value
 subprocess.Popen(['/usr/bin/sandbox-exec','-f',str(box),str(binary)],cwd=state,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
 return {'opened':True,'phase':'ready','loopback_only':True}
def run_maintenance(config,action):
 probe=probe_storage(config);storage_gate(config,probe)
 config.state.mkdir(parents=True,exist_ok=True,mode=0o700)
 source=config.source_index if action=='migrate' else config.index
 if source is None:raise LocalError('index_not_ready')
 lock=(config.state/'controller.lock').open('a')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:raise LocalError('service_busy')
 if action=='incremental-apply':
  if alive(health(config).get('pid',0)) or not port_free(config.port):raise LocalError('service_busy')
  prepare_environment(config)
 policy=config.state/'maintenance.sb';policy.write_text(maintenance_policy(config,source,discover=action.startswith('incremental')))
 interpreter=config.python if action=='incremental-apply' else Path('/usr/bin/python3')
 result=subprocess.run(['/usr/bin/sandbox-exec','-f',str(policy),str(interpreter),'-I',str(REPO/'memorysearch_local/maintenance.py'),action,str(config.file)],cwd=REPO/'memorysearch_local',env=runtime_environment(config),capture_output=True,text=True,timeout=3600 if action=='incremental-apply' else 600)
 try:value=json.loads(result.stdout)
 except Exception:raise LocalError('start_failed') from None
 if result.returncode:
  error=LocalError(value.get('error_code','start_failed'))
  error.details={key:value[key] for key in ('stage','exception_class') if isinstance(value.get(key),str) and value[key].replace('_','').isalnum() and len(value[key])<64}
  raise error
 return value
def main():
 parser=argparse.ArgumentParser();parser.add_argument('action',choices=['install','open','start','stop','status','storage','rollback-photo','migrate','incremental-dry-run'])
 parser.add_argument('--config',type=Path,default=DEFAULT_CONFIG)
 parser.add_argument('--verify-client',action='store_true',help=argparse.SUPPRESS)
 parser.add_argument('--apply',action='store_true',help='仅用于增量入口：服务停止后，显式更新新增/改动文件。')
 args=parser.parse_args();config=load(args.config)
 if args.apply:
  if args.action!='incremental-dry-run':raise LocalError('config_invalid')
  args.action='incremental-apply'
 if args.action=='install':
  prepare_environment(config)
  from memorysearch_local.verify_runtime import verify
  emit({'installed':True,'service_started':False,**verify(config)});return
 if args.action=='storage':
  probe=probe_storage(config)
  try:storage_gate(config,probe);probe['private_index_gate']='passed'
  except LocalError:probe['private_index_gate']='blocked'
  emit(probe);return
 if args.action=='status':emit(public_status(config));return
 if args.action=='rollback-photo':private_write(config.policy,{'schema':1,'enabled':False});emit({'photo_review_enabled_for_next_requests':False,'index_changed':False});return
 if args.action in ('migrate','incremental-dry-run','incremental-apply'):emit(run_maintenance(config,args.action));return
 if args.action=='open':emit(open_viewer(config,args.verify_client));return
 emit({'start':start,'stop':stop}[args.action](config))
if __name__=='__main__':
 try:main()
 except LocalError as error:emit({'ok':False,'error_code':error.code,'message':str(error),**getattr(error,'details',{})});raise SystemExit(2)
 except Exception:emit({'ok':False,'error_code':'start_failed','message':MESSAGES['start_failed']});raise SystemExit(3)
