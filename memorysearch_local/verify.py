"""Generated-only integration. Never accepts a user media root or production config."""
from pathlib import Path
import argparse,json,os,socket,subprocess,sys,time,uuid

REPO=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(REPO))
from memorysearch_local.config import private_write

def command(name,*args):
 result=subprocess.run([str(REPO/name),*map(str,args)],cwd=REPO,capture_output=True,text=True,timeout=600)
 try:value=json.loads(result.stdout)
 except Exception:raise RuntimeError('launcher_output') from None
 if result.returncode:raise RuntimeError(value.get('error_code','launcher_failed'))
 return value

def fixture():
 run=REPO/'runtime/test-runs'/('verify-'+uuid.uuid4().hex[:10]);media=run/'media';media.mkdir(parents=True,mode=0o700)
 (media/'SYNTHETIC-ONLY').touch();(media/'readonly-canary').write_text('generated')
 (media/'forbidden-original.txt').write_text('generated only')
 script="from PIL import Image,ImageDraw;from pathlib import Path;import sys;p=Path(sys.argv[1]);im=Image.new('RGB',(512,384),'white');ImageDraw.Draw(im).ellipse((100,50,400,350),fill='red');im.save(p/'red-circle.png');im=Image.new('RGB',(512,384),'white');ImageDraw.Draw(im).rectangle((120,60,390,330),fill='blue');im.save(p/'blue-square.png')"
 subprocess.run([str(REPO/'runtime/python/bin/python3'),'-I','-c',script,str(media)],capture_output=True,check=True)
 subprocess.run([str(REPO/'runtime/native/ffmpeg'),'-nostdin','-hide_banner','-loglevel','error','-f','lavfi','-i','testsrc2=size=320x240:rate=10','-t','4','-c:v','libx264','-pix_fmt','yuv420p',str(media/'test-pattern.mp4')],capture_output=True,check=True)
 with socket.socket() as connection:connection.bind(('127.0.0.1',0));port=connection.getsockname()[1]
 cfg={'schema':1,'SCOPE':'synthetic','MEDIA_ROOT':str(media),'INDEX_ROOT':str(media/'.memorysearch-index'),'RUNTIME_ROOT':str(run/'work'),'RUNTIME_PYTHON':str(REPO/'runtime/python/bin/python3'),'ENCODER_MODEL_ROOT':str(REPO/'runtime/models/embeddinggemma-2'),'VERIFIER_MODEL_ROOT':str(REPO/'runtime/models/verifier-4b'),'NATIVE_BIN_ROOT':str(REPO/'runtime/native'),'PORT':port,'DEVICE':'cpu','NO_SYNC_CONFIRMED':False}
 file=run/'config.json';private_write(file,cfg)
 # Real retrieval runs on CPU so this self-test never competes for the live GPU.
 # Photo decision semantics are covered separately, without a second model.
 private_write(run/'work/photo-review-policy.json',{'schema':1,'enabled':False})
 return run,file,cfg

def live():
 run,file,cfg=fixture();summary={'schema':1,'scope':'generated_only','real_model':True,'device':'cpu','photo_model_executed':False,'quality_benchmark':False}
 begin=time.monotonic()
 try:
  command('MemorySearch-Install.command','--config',file)
  opened=time.monotonic();summary['native_client']=command('MemorySearch-Open.command','--config',file,'--verify-client');summary['cold_start_and_client_s']=round(time.monotonic()-opened,3)
  if not summary['native_client'].get('passed'):raise RuntimeError('native_client')
  summary['status']=command('MemorySearch-Status.command','--config',file)
  pid=summary['status']['pid'];again=command('MemorySearch-Start.command','--config',file)
  summary['duplicate_start_reused_pid']=again.get('pid')==pid
  warm=time.monotonic();summary['warm_native_client']=command('MemorySearch-Open.command','--config',file,'--verify-client');summary['warm_client_s']=round(time.monotonic()-warm,3)
  summary['dry_run']=command('MemorySearch-Incremental.command','--config',file)
  # Use a separate generated destination. Never overwrite any existing index.
  migration={**cfg,'SOURCE_INDEX_ROOT':cfg['INDEX_ROOT'],'INDEX_ROOT':str(Path(cfg['MEDIA_ROOT'])/'.migration-copy-index'),'RUNTIME_ROOT':str(run/'maintenance')}
  migration_file=run/'migration.json';private_write(migration_file,migration)
  summary['snapshot']=command('MemorySearch-Migrate-Index.command','--config',migration_file)
  summary['stopped']=command('MemorySearch-Stop.command','--config',file)
  # No changes: this tests the explicit writer route without any full rebuild.
  summary['incremental']=command('MemorySearch-Incremental.command','--config',migration_file,'--apply')
  summary['after_incremental_dry_run']=command('MemorySearch-Incremental.command','--config',migration_file)
  recovered=command('MemorySearch-Start.command','--config',file)
  summary['restart_ready_without_reindex']=recovered.get('phase')=='ready' and recovered.get('indexed')==3 and recovered.get('model_calls')==0
  summary['passed']=all([summary['duplicate_start_reused_pid'],summary['snapshot'].get('passed'),summary['incremental'].get('passed'),summary['incremental'].get('summary',{}).get('embedded')==0,summary['after_incremental_dry_run'].get('passed'),summary['restart_ready_without_reindex'],summary['warm_native_client'].get('passed')])
 except Exception as error:
  summary['passed']=False;summary['error_code']=str(error) if str(error) in ('launcher_output','launcher_failed','start_failed','runtime_missing','maintenance_failed','native_client') else 'synthetic_verification_failed'
 finally:
  try:command('MemorySearch-Stop.command','--config',file)
  except Exception:pass
  summary['total_s']=round(time.monotonic()-begin,3);private_write(run/'SUMMARY.json',summary)
 print(json.dumps(summary,ensure_ascii=False,sort_keys=True));return 0 if summary.get('passed') else 1

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');args=parser.parse_args()
 if args.live:return live()
 result=subprocess.run(['/usr/bin/python3','-m','unittest','discover','-s',str(REPO/'memorysearch_local/tests'),'-p','test_*.py'],cwd=REPO,capture_output=True,text=True)
 print(json.dumps({'schema':1,'mode':'no_inference_boundary_tests','passed':result.returncode==0,'private_reads':False}));return result.returncode
if __name__=='__main__':raise SystemExit(main())
