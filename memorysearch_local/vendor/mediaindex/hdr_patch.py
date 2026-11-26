# MemorySearch adapted packaging; see ../../THIRD-PARTY.md and vendor notices.
"""Scoped, resumable refresh of stale HDR visual evidence; originals stay read-only."""
from pathlib import Path
import hashlib

VERSION='explicit-hlg-pq-srgb-mobius-v1'
def visual_source_key(content_hash):
    return hashlib.sha256((content_hash+'|'+VERSION).encode()).hexdigest()

def refresh_existing_hdr(app,root,thumbs):
    from .media.video import probe_video,VideoRejected
    root=Path(root).resolve(strict=True);db=app.state.db;store=app.state.store
    result={'metadata_probes':0,'metadata_cache_hits':0,'hdr_assets_invalidated':0,'unknown':0}
    with db.tx() as c:c.execute('CREATE TABLE IF NOT EXISTS memorysearch_hdr_inputs(asset_id TEXT PRIMARY KEY,content_hash TEXT NOT NULL,hdr INTEGER NOT NULL)')
    libs=[a for a in store.list_libraries() if a['root_path']==str(root)]
    for lib in libs:
        for asset in store.list_assets(lib['id']):
            if asset['media_type']!='video' or not asset['content_hash']:continue
            p=root/asset['rel_path']
            try:
                if p.is_symlink() or not p.resolve(strict=True).is_relative_to(root):continue
                record=db.one('SELECT * FROM memorysearch_hdr_inputs WHERE asset_id=?',(asset['id'],))
                if record and record['content_hash']==asset['content_hash']:
                    hdr=bool(record['hdr']);result['metadata_cache_hits']+=1
                else:
                    info=probe_video(p);hdr=info.color_transfer == 'arib-std-b67';result['metadata_probes']+=1
                    with db.tx() as c:c.execute('INSERT OR REPLACE INTO memorysearch_hdr_inputs VALUES(?,?,?)',(asset['id'],asset['content_hash'],int(hdr)))
                if not hdr or asset.get('live_photo'):continue
                expected=visual_source_key(asset['content_hash'])
                rows=db.query("SELECT source_hash FROM embeddings WHERE asset_id=? AND profile_key=? AND modality='video-visual'",(asset['id'],app.state.profile.key))
                if rows and all(row['source_hash']==expected for row in rows):continue
                if rows:
                    with db.tx() as c:
                        c.execute("DELETE FROM embeddings WHERE asset_id=? AND profile_key=? AND modality IN ('video-visual','video-joint')",(asset['id'],app.state.profile.key))
                        store.set_asset_status(asset['id'],'pending',None,c=c)
                    result['hdr_assets_invalidated']+=1
                    folder=Path(thumbs)/asset['content_hash'][:2]
                    if folder.exists():
                        for t in folder.glob(asset['content_hash']+'*.jpg'):
                            if not t.is_symlink() and t.parent.resolve()==folder.resolve():t.unlink()
            except (OSError,ValueError,VideoRejected):result['unknown']+=1
    return result
