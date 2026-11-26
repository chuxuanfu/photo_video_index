"""Verify local public runtime files against committed manifests, without downloads."""
from pathlib import Path
import hashlib,json,subprocess
from .config import REPO,LocalError

def verify(config):
 model_files=0
 encoder=json.loads((REPO/'memorysearch_local/EMBEDDING-MODEL-LOCK.json').read_text())
 verifier=json.loads((REPO/'memorysearch_local/VERIFIER-MODEL-LOCK.json').read_text())['verifier-4b']
 for root,manifest in [(config.encoder,encoder),(config.verifier,verifier)]:
  for name,record in manifest['files'].items():
   relative=Path(name)
   if relative.is_absolute() or '..' in relative.parts:raise LocalError('runtime_missing')
   file=root/relative
   if file.is_symlink() or not file.is_file():raise LocalError('runtime_missing')
   digest=hashlib.sha256()
   with file.open('rb') as stream:
    for block in iter(lambda:stream.read(4*1024**2),b''):digest.update(block)
   expected=record['sha256'] if isinstance(record,dict) else record
   if digest.hexdigest()!=expected:raise LocalError('runtime_missing')
   model_files+=1
 result=subprocess.run([str(config.python),'-I','-c','import torch,transformers,sentence_transformers,PIL,fastapi,uvicorn,numpy;print("ok")'],capture_output=True,text=True,timeout=60)
 if result.returncode or result.stdout.strip()!='ok':raise LocalError('runtime_missing')
 native=json.loads((REPO/'memorysearch_local/native-tools.lock.json').read_text())['tools']
 for name in ['ffmpeg','ffprobe']:
  digest=hashlib.sha256((config.native/name).read_bytes()).hexdigest()
  if digest!=native[name]['sha256']:raise LocalError('runtime_missing')
  result=subprocess.run([str(config.native/name),'-version'],capture_output=True,timeout=10)
  if result.returncode:raise LocalError('runtime_missing')
 return {'model_files_sha256_verified':model_files,'runtime_imports_passed':True,'native_tools_passed':True}
