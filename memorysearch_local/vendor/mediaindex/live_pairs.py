# MemorySearch adapted packaging; see ../../THIRD-PARTY.md and vendor notices.
"""A filename proposes a pair; only matching native content identifiers confirm it.

Missing, malformed, ambiguous or unreadable metadata keeps a normal searchable
video. This code never changes original media or enumerates beyond `files`.
"""
from collections import defaultdict
from pathlib import Path
import hashlib,json,os,sqlite3,subprocess

VERSION='native-content-identifier-pair-v1'
STATS={'checks':0,'cache_hits':0,'verified':0,'unknown':0,'ambiguous':0}

def verified_partners(files,root,image_extensions,video_extensions):
    root=Path(root).resolve(strict=True);groups=defaultdict(lambda:{'images':[],'videos':[]})
    for path,rel in files:
        p=Path(rel);kind='images' if p.suffix.lower() in image_extensions else 'videos' if p.suffix.lower() in video_extensions else None
        if kind:groups[(str(p.parent),p.stem.casefold())][kind].append((Path(path),rel))
    binary=os.environ.get('MEMORYSEARCH_LIVE_PAIR_PROBE','');cache=os.environ.get('MEMORYSEARCH_LIVE_PAIR_CACHE','')
    if not binary or not cache or not Path(binary).is_file():return {}
    binary_hash=hashlib.sha256(Path(binary).read_bytes()).hexdigest();out={}
    with sqlite3.connect(cache,timeout=3) as db:
        db.execute('CREATE TABLE IF NOT EXISTS verified_live_pairs(pair_key TEXT PRIMARY KEY, verified INTEGER NOT NULL)')
        for group in groups.values():
            if not group['images'] or not group['videos']:continue
            if len(group['images'])!=1 or len(group['videos'])!=1:
                STATS['ambiguous']+=1;continue
            photo,photo_rel=group['images'][0];movie,movie_rel=group['videos'][0]
            if movie.suffix.lower()!='.mov':continue
            try:
                if any(p.is_symlink() or not p.resolve(strict=True).is_relative_to(root) for p in (photo,movie)):continue
                identities=[]
                for p in (photo,movie):
                    s=p.stat();identities.append((str(p.relative_to(root)),s.st_size,s.st_mtime_ns,s.st_ctime_ns))
                key=hashlib.sha256(json.dumps([VERSION,binary_hash,str(root),identities],separators=(',',':')).encode()).hexdigest()
                row=db.execute('SELECT verified FROM verified_live_pairs WHERE pair_key=?',(key,)).fetchone()
                if row is not None:
                    STATS['cache_hits']+=1;verified=bool(row[0])
                else:
                    STATS['checks']+=1
                    run=subprocess.run([binary],input=json.dumps([str(photo),str(movie)]).encode(),capture_output=True,timeout=4,check=False)
                    if run.returncode or len(run.stdout)>4096:raise ValueError('native pair evidence unavailable')
                    result=json.loads(run.stdout);verified=result.get('verified') is True
                    if result.get('unknown'):raise ValueError('native pair evidence unknown')
                    db.execute('INSERT OR REPLACE INTO verified_live_pairs VALUES(?,?)',(key,int(verified)));db.commit()
                if verified:out[movie_rel]=photo_rel;STATS['verified']+=1
            except (OSError,ValueError,subprocess.TimeoutExpired,json.JSONDecodeError):STATS['unknown']+=1
    return out
