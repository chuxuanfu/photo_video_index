"""Explicit-root discovery. Hidden index/state files are never media inputs."""
from pathlib import Path
import os,stat

IMAGE_EXTENSIONS={'.jpg','.jpeg','.png','.heic','.heif','.webp','.gif','.bmp','.tif','.tiff','.dng','.arw','.cr2','.cr3','.nef','.orf','.rw2','.raf'}
VIDEO_EXTENSIONS={'.mp4','.mov','.m4v','.avi','.mkv','.webm','.mts','.m2ts','.3gp'}

def walk_media(root,index_root,extensions=None,*,stop=None):
 root=Path(root);index_root=Path(index_root)
 allowed=extensions if extensions is not None else IMAGE_EXTENSIONS|VIDEO_EXTENSIONS
 device=root.lstat().st_dev
 for directory,dirs,files in os.walk(root,followlinks=False):
  accepted=[]
  for name in sorted(dirs):
   path=Path(directory)/name
   if name.startswith('.') or path==index_root:continue
   value=path.lstat()
   if stat.S_ISLNK(value.st_mode) or value.st_dev!=device or getattr(value,'st_flags',0)&0x40000000:continue
   accepted.append(name)
  dirs[:]=accepted
  for name in sorted(files):
   if stop is not None and stop.is_set():return
   if name.startswith('.') or name=='index.json':continue
   path=Path(directory)/name
   if path==index_root or index_root in path.parents:continue
   value=path.lstat()
   if not stat.S_ISREG(value.st_mode) or value.st_dev!=device or getattr(value,'st_flags',0)&0x40000000:continue
   if path.suffix.lower() not in allowed:continue
   yield path,path.relative_to(root).as_posix()
