"""Generated-only limit integration through the normal API and native controls."""
from pathlib import Path
import json,os,shutil,subprocess,sys,time,urllib.request,urllib.error,statistics
REPO=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(REPO))
from memorysearch_local.verify import fixture,command
from memorysearch_local.config import load,private_write
from memorysearch_local.isolation import client_policy

def post(port,body):
 request=urllib.request.Request(f'http://127.0.0.1:{port}/search',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 with opener.open(request,timeout=90) as response:return json.load(response)

def main():
 run,file,raw=fixture();media=Path(raw['MEDIA_ROOT'])
 for n in range(28):shutil.copyfile(media/('red-circle.png' if n%2 else 'blue-square.png'),media/f'photo-{n:02}.png')
 for n in range(24):shutil.copyfile(media/'test-pattern.mp4',media/f'video-{n:02}.mp4')
 report={'schema':1,'scope':'generated_only','quality_benchmark':False,'photo_visual_model_executed':False,'photos':30,'videos':25,'api_requests':0}
 def search(body):report['api_requests']+=1;return post(raw['PORT'],body)
 try:
  report['started']=command('MemorySearch-Start.command','--config',file)
  binary=run/'native-bin/NativePhotoViewer';binary.parent.mkdir(mode=0o700)
  built=subprocess.run(['/usr/bin/swiftc','-O','-module-cache-path',str(REPO/'runtime/build-cache'),str(REPO/'memorysearch_local/native/NativePhotoViewer.swift'),'-o',str(binary)],capture_output=True,timeout=120)
  assert built.returncode==0,'native_build'
  state=run/'native-client';state.mkdir(mode=0o700)
  policy=state/'client.sb';policy.write_text(client_policy(binary,state,raw['PORT']))
  env={'PATH':'/usr/bin:/bin','TMPDIR':str(state)+'/','MEMORYSEARCH_PORT':str(raw['PORT']),'MEMORYSEARCH_CLIENT_STATE':str(state),'DO_NOT_TRACK':'1'}
  def native(*args):
   process=subprocess.run(['/usr/bin/sandbox-exec','-f',str(policy),str(binary),*args],cwd=state,env=env,capture_output=True,text=True,timeout=180)
   value=json.loads(process.stdout);assert process.returncode==0 and value.get('passed'),value;return value
  report['settings_write']=native('--verify-limit-write','25')
  report['native']=native('--verify-result-limits')
  report['ui_restart']=native('--verify-limit-ui-read','25')
  report['api_cases']=[]
  for mode,available in [('image',30),('video',25),('mixed',55)]:
   for limit in [1,5,10,20,25,50]:
    body={'text':'红色圆形','mode':mode,'limit':limit};begin=time.monotonic();value=search(body);rows=value['results']
    assert len(rows)==min(limit,available) and len({r['asset']['id'] for r in rows})==len(rows)
    assert value['requested_limit']==limit
    report['api_cases'].append({'mode':mode,'limit':limit,'returned':len(rows),'elapsed_s':round(time.monotonic()-begin,4)})
   implicit=search({'text':'红色圆形','mode':mode});explicit=search({'text':'红色圆形','mode':mode,'limit':10})
   assert [r['asset']['id'] for r in implicit['results']]==[r['asset']['id'] for r in explicit['results']]
  report['default_10_order_parity']=True;report['invalid_rejected']=0
  for limit in [0,51,-1,True,'25',2.5]:
   try:search({'text':'红色圆形','mode':'image','limit':limit})
   except urllib.error.HTTPError as error:assert error.code==422;report['invalid_rejected']+=1
   else:raise AssertionError('limit_validation')
  # Exercise the real review queue/fallback without loading a second GPU model.
  private_write(Path(raw['RUNTIME_ROOT'])/'photo-review-policy.json',{'schema':1,'enabled':True})
  fallback=[]
  for mode in ['image','mixed']:
   value=search({'text':'红色圆形','mode':mode,'limit':25,'_public_fault':'error'});native_ids=[r['asset']['id'] for r in value['results']];jid=value['photo_review_job'];assert jid
   opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
   for _ in range(100):
    with opener.open(f'http://127.0.0.1:{raw["PORT"]}/photo-review/{jid}',timeout=10) as response:result=json.load(response)
    if result['photo_review']['complete']:break
    time.sleep(.03)
   assert result['photo_review']['stage']=='fallback' and [r['asset']['id'] for r in result['results']]==native_ids and len(native_ids)==25
   fallback.append({'mode':mode,'returned':len(native_ids),'order_preserved':True})
  report['review_fallback']=fallback
  report['passed']=True
 except Exception as error:report['passed']=False;report['error_class']=type(error).__name__
 finally:
  try:report['stopped']=command('MemorySearch-Stop.command','--config',file)
  except Exception:report['stop_failed']=True
  private_write(run/'LIMITS-SUMMARY.json',report)
 print(json.dumps(report,sort_keys=True));return 0 if report.get('passed') else 1
if __name__=='__main__':raise SystemExit(main())
