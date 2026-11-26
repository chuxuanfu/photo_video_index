"""Existing completed index: SQLite read-only, no migrations/import/job recovery."""
from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager
import json,sqlite3,threading

def open_core(settings,factory):
    from mediaindex.db import Database
    from mediaindex.store import Store
    from mediaindex.search import MatrixCache
    from mediaindex.model.host import ModelHost
    class ReadOnlyDatabase(Database):
        def __init__(self,path):
            self.path=str(path);self._local=threading.local()
        def conn(self):
            c=getattr(self._local,'conn',None)
            if c is None:
                c=sqlite3.connect(Path(self.path).as_uri()+'?mode=ro',uri=True,timeout=30,isolation_level=None,check_same_thread=False)
                c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');self._local.conn=c
            return c
        def migrate(self):raise PermissionError('completed_index_read_only')
        @contextmanager
        def tx(self):
            # Native matrix loading uses tx() for a consistent READ snapshot.
            # mode=ro and query_only still reject every mutation in that scope.
            c=self.conn();nested=c.in_transaction
            if not nested:c.execute('BEGIN')
            try:yield c
            finally:
                if not nested and c.in_transaction:c.execute('ROLLBACK')
    db=ReadOnlyDatabase(settings.db_path);store=Store(db);profile=settings.index_profile()
    host=ModelHost(profile,factory)
    def public_asset(a):
        out={k:a[k] for k in ('id','library_id','rel_path','media_type','size','content_hash','width','height','duration','status','error','taken_at','place')}
        meta=json.loads(a['meta_json']) if a.get('meta_json') else {}
        out['source']=meta.get('source');out['thumbnail_url']=f"/api/assets/{a['id']}/thumbnail";out['file_url']=f"/api/assets/{a['id']}/file"
        if a['media_type']=='image' and (live:=store.live_video_for(a['library_id'],a['rel_path'])):out['live_video_url']=f'/api/assets/{live}/file'
        out['live_photo']=a.get('live_photo');return out
    return SimpleNamespace(state=SimpleNamespace(settings=settings,profile=profile,db=db,store=store,host=host,matrix_cache=MatrixCache(store),public_asset=public_asset))
