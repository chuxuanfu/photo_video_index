"""Run only in the deny-network maintenance sandbox. Output fixed aggregate fields."""
from __future__ import annotations
from pathlib import Path
import errno,hashlib,json,os,re,shutil,socket,sqlite3,sys,time,uuid
STAGE='initializing'
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from memorysearch_local.config import load,private_write
from memorysearch_local.discovery import walk_media

def network_proof():
 blocked=0
 for address in [('1.1.1.1',443),('10.255.255.1',9),('127.0.0.1',19139)]:
  with socket.socket() as connection:
   connection.settimeout(1)
   if connection.connect_ex(address) not in (errno.EPERM,errno.EACCES):raise RuntimeError('network_policy')
   blocked+=1
 def audit(event,args):
  if event in ('socket.connect','socket.getaddrinfo'):raise PermissionError('offline')
 sys.addaudithook(audit)
 return blocked

def connect_readonly(path):
 db=sqlite3.connect(Path(path).as_uri()+'?mode=ro',uri=True,timeout=20,isolation_level=None)
 db.execute('PRAGMA query_only=ON');return db

def counts(db):
 if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('index_invalid')
 required={'assets','embeddings','libraries'}
 for name in required:
  if db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone()[0]!=1:raise RuntimeError('index_schema')
 result={f'{media}_{status}':0 for media in ['image','video'] for status in ['indexed','failed','pending','missing']}
 for media,status,n in db.execute('SELECT media_type,status,COUNT(*) FROM assets GROUP BY media_type,status'):
  key=f'{media}_{status}'
  if key in result:result[key]=int(n)
 result['libraries']=int(db.execute('SELECT COUNT(*) FROM libraries').fetchone()[0])
 result['embeddings']=int(db.execute('SELECT COUNT(*) FROM embeddings').fetchone()[0])
 profiles={key:json.loads(value) for key,value in db.execute('SELECT key,json FROM index_profiles')}
 for key in (row[0] for row in db.execute('SELECT DISTINCT profile_key FROM embeddings')):
  profile=profiles.get(key,{})
  if profile.get('model_id')!='google/embeddinggemma-2' or profile.get('revision')!='914f7f89142e33e77833254d9c9b90c3cef7303b' or profile.get('dim')!=768:raise RuntimeError('index_profile')
 if db.execute('SELECT COUNT(*) FROM embeddings WHERE dim!=768 OR length(vector)!=dim*4').fetchone()[0]:raise RuntimeError('index_vectors')
 if db.execute('PRAGMA foreign_key_check').fetchone() is not None:raise RuntimeError('index_references')
 return result

def backup(config):
 source=config.source_index
 if source is None or not source.is_dir() or source.is_symlink():raise RuntimeError('source_invalid')
 if config.index.exists():raise RuntimeError('destination_exists_no_overwrite')
 # No parent or source directory is created or modified.
 config.index.mkdir(mode=0o700)
 pending=config.index/'mediaindex.sqlite3.pending';target=config.index/'mediaindex.sqlite3'
 begin=time.monotonic();copied=0;missing=0
 try:
  original=connect_readonly(source/'mediaindex.sqlite3');original.execute('BEGIN')
  before=counts(original)
  # Match the actual destination encoder space, without importing a model or
  # accepting an unrelated historical profile that would produce empty ranks.
  sys.path.insert(0,str(Path(__file__).resolve().parent/'vendor'))
  from dataclasses import replace
  from mediaindex.config import Settings
  profile=replace(Settings(data_dir=config.index,device=config.device).index_profile(),encoders=('text','image'),video_modalities=('video-visual',))
  if not original.execute('SELECT COUNT(*) FROM index_profiles WHERE key=?',(profile.key,)).fetchone()[0]:raise RuntimeError('index_profile')
  roots=original.execute('SELECT root_path FROM libraries').fetchall()
  if len(roots)!=1 or roots[0][0]!=str(config.media):raise RuntimeError('library_root_mismatch')
  destination=sqlite3.connect(pending)
  def progress(*_):
   if time.monotonic()-begin>900:raise RuntimeError('backup_timeout')
  original.backup(destination,pages=256,progress=progress,sleep=.01)
  destination.execute('PRAGMA journal_mode=DELETE');after=counts(destination)
  if before!=after:raise RuntimeError('snapshot_count_mismatch')
  destination.close();pending.chmod(0o600)
  for suffix in ['-wal','-shm']:
   extra=Path(str(pending)+suffix)
   if extra.exists():extra.unlink()
  for suffix in ('-shm','-wal'):
   artifact=Path(str(pending)+suffix)
   if artifact.exists():artifact.unlink()
  # Only thumbnails referenced by indexed assets are copied. No originals, logs,
  # job markers, caches or arbitrary source-directory members are enumerated.
  hashes=original.execute("SELECT DISTINCT content_hash FROM assets WHERE status='indexed' AND media_type IN ('image','video')")
  for (content_hash,) in hashes:
   if not isinstance(content_hash,str) or not re.fullmatch('[0-9a-f]{64}',content_hash):raise RuntimeError('thumbnail_hash_invalid')
   relative=Path(content_hash[:2])/(content_hash+'.jpg')
   old=source/'thumbs'/relative
   if not old.exists():missing+=1;continue
   if old.is_symlink() or not old.is_file():raise RuntimeError('thumbnail_scope')
   new=config.index/'thumbs'/relative;new.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
   shutil.copyfile(old,new);new.chmod(0o600);copied+=1
  original.execute('ROLLBACK');original.close()
  os.replace(pending,target)
  private_write(config.index/'READY.json',{'schema':1,'snapshot_verified':True,'counts':after,'thumbnail_files':copied,'source_preserved':True})
  return {'schema':1,'passed':True,'counts_before':before,'counts_after':after,'thumbs_copied':copied,'source_missing_thumbs':missing,'sqlite_backup_used':True,'source_preserved':True,'private_paths_exported':False,'elapsed_s':round(time.monotonic()-begin,3)}
 except Exception:
  # Remove only this newly created, incomplete destination. Never touch source.
  shutil.rmtree(config.index)
  raise

def incremental_dry_run(config):
 global STAGE
 STAGE='metadata_index_open'
 def no_original_bytes(event,args):
  if event=='open' and isinstance(args[0],(str,bytes)):
   path=Path(os.fsdecode(args[0])).absolute()
   if path.is_relative_to(config.media) and not path.is_relative_to(config.index):raise PermissionError('metadata_only_dry_run')
 sys.addaudithook(no_original_bytes)
 db=connect_readonly(config.index/'mediaindex.sqlite3')
 STAGE='metadata_index_read'
 known={row[0]:(row[1],row[2]) for row in db.execute('SELECT rel_path,size,mtime_ns FROM assets')}
 seen=set();out={'new':0,'changed':0,'unchanged':0,'missing':0,'originals_decoded':0,'index_written':False}
 STAGE='metadata_discovery'
 for path,relative in walk_media(config.media,config.index):
  value=path.stat();seen.add(relative)
  if relative not in known:out['new']+=1
  elif known[relative]!=(value.st_size,value.st_mtime_ns):out['changed']+=1
  else:out['unchanged']+=1
 out['missing']=len(set(known)-seen);db.close()
 return {'schema':1,'passed':True,'dry_run':True,'metadata_only':True,'summary':out,'private_paths_exported':False}

def main():
 global STAGE
 if len(sys.argv)!=3 or sys.argv[1] not in ('migrate','incremental-dry-run','incremental-apply'):return 2
 STAGE='config';config=load(Path(sys.argv[2]));STAGE='network';blocked=network_proof();STAGE='dispatch'
 original_read_blocked=None
 if config.synthetic and sys.argv[1]=='migrate':
  try:
   with (config.media/'forbidden-original.txt').open('rb') as file:file.read(1)
  except PermissionError:original_read_blocked=True
  else:raise RuntimeError('original_read_boundary_failed')
 if sys.argv[1]=='migrate':result=backup(config)
 elif sys.argv[1]=='incremental-dry-run':result=incremental_dry_run(config)
 else:
  from memorysearch_local.incremental import apply
  result=apply(config)
 if original_read_blocked is not None:result['original_read_blocked']=original_read_blocked
 result['network_proof_passed']=True;result['blocked_network_probes']=blocked
 print(json.dumps(result,sort_keys=True));return 0

if __name__=='__main__':
 try:raise SystemExit(main())
 except Exception as error:
  print(json.dumps({'schema':1,'passed':False,'error_code':'maintenance_failed','stage':STAGE,'exception_class':type(error).__name__ if type(error).__name__ in ('OperationalError','IntegrityError','PermissionError','RuntimeError','FileNotFoundError','BlockingIOError') else 'internal','private_paths_exported':False}))
  raise SystemExit(3)
