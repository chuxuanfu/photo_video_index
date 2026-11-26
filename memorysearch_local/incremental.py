"""Explicit offline incremental maintenance. Never used during normal startup."""
from pathlib import Path
from dataclasses import replace
import fcntl,os,signal,sqlite3,sys,threading,time
if __package__:
 from .maintenance import counts,connect_readonly
 from .config import private_write
 from .discovery import walk_media
else:
 from maintenance import counts,connect_readonly
 from config import private_write
 from discovery import walk_media

def apply(config):
 os.umask(0o077)
 lock=(config.state/'owner.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 source=connect_readonly(config.index/'mediaindex.sqlite3');before=counts(source)
 if not (config.index/'READY.json').is_file():raise RuntimeError('index_not_ready')
 # SQLite's backup includes committed WAL data. Retain one local previous state.
 rollback=config.index/'.rollback';rollback.mkdir(mode=0o700,exist_ok=True)
 pending=rollback/'before-incremental.sqlite3.pending';target=rollback/'before-incremental.sqlite3'
 destination=sqlite3.connect(pending);source.backup(destination);destination.close();source.close()
 pending.chmod(0o600);os.replace(pending,target)
 sys.path.insert(0,str(Path(__file__).resolve().parent/'vendor'))
 import torch
 torch.set_num_threads(4)
 if config.device=='mps':torch.mps.set_per_process_memory_fraction(.35)
 from mediaindex.db import Database
 from mediaindex.store import Store
 from mediaindex.config import Settings
 from mediaindex.model.host import ModelHost
 from mediaindex.model.backend import GemmaBackend
 from mediaindex.indexer import make_image_embedder,make_video_embedder
 from mediaindex.jobs import Job,JobContext
 import mediaindex.ingest as ingest
 # Existing schema only: no migration/rekey/recovery on a production snapshot.
 db=Database.__new__(Database);db.path=str(config.index/'mediaindex.sqlite3');db._local=threading.local();db._write_lock=threading.RLock()
 store=Store(db);settings=Settings(data_dir=config.index,device=config.device,watch_interval=0,chat_model='')
 profile=replace(settings.index_profile(),encoders=('text','image'),video_modalities=('video-visual',))
 if not db.query('SELECT json FROM index_profiles WHERE key=?',(profile.key,)):raise RuntimeError('index_profile')
 libraries=store.list_libraries()
 if len(libraries)!=1 or libraries[0]['root_path']!=str(config.media):raise RuntimeError('library_root_mismatch')
 host=ModelHost(profile,lambda p:GemmaBackend(p,device=config.device,offline=True))
 image=make_image_embedder(store,host);video=make_video_embedder(store,host,settings.thumbs_dir)
 ingest.walk_files=lambda root,extensions,skipped=None:walk_media(root,config.index,extensions & (ingest.IMAGE_EXTENSIONS|ingest.VIDEO_EXTENSIONS))
 job=Job(id='incremental',kind='import',library_id=libraries[0]['id']);job.status='running'
 signal.signal(signal.SIGTERM,lambda *_:job._cancel.set());signal.signal(signal.SIGINT,lambda *_:job._cancel.set())
 begin=time.monotonic()
 try:
  result=ingest.run_image_import(JobContext(job,None),store,settings.thumbs_dir,libraries[0]['id'],image,batch_size=2,embed_audio=None,embed_video=video)
  after=counts(db.conn())
  private_write(config.index/'READY.json',{'schema':1,'snapshot_verified':True,'counts':after,'incremental_completed':not result.get('cancelled',False)})
  return {'schema':1,'passed':not result.get('cancelled',False),'incremental':True,'full_rebuild':False,'counts_before':before,'counts_after':after,'summary':{key:int(result.get(key,0)) for key in ['new','changed','unchanged','embedded','reused_vectors','failed_count','missing_count','video_windows']},'originals_modified':False,'rollback_snapshot_created':True,'private_paths_exported':False,'elapsed_s':round(time.monotonic()-begin,3)}
 finally:
  host.shutdown()
  connection=db.conn()
  if connection.in_transaction:connection.execute('ROLLBACK')
  connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
  connection.execute('PRAGMA journal_mode=DELETE')
  db.close()
