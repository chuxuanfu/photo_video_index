"""Machine-specific paths live only in ignored, owner-readable local configuration."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json,os,stat

REPO=Path(__file__).resolve().parent.parent
DEFAULT_CONFIG=REPO/'runtime/local-config.json'
MESSAGES={
 'config_missing':'尚未完成本机配置。请先运行安装/检查入口；不会自动扫描图库。',
 'config_invalid':'本机配置字段不完整或无效，请检查 MEDIA_ROOT、INDEX_ROOT 和运行资源。',
 'config_permissions':'本机配置必须只允许当前用户读取（0600）。',
 'path_invalid':'目录必须存在于本机，不能使用符号链接、占位路径或同一个媒体/索引根。',
 'index_not_ready':'现有索引尚未安全迁移并校验；未开始建库或扫描原件。',
 'sync_unconfirmed':'无法排除目标目录的同步。未写入私人索引，也未切换现有服务。',
 'port_busy':'本机端口已被占用；没有停止或覆盖已有服务。',
 'runtime_missing':'运行资源不完整，请运行安装/检查入口。',
 'service_busy':'当前服务仍有工作，未执行切换。',
 'network_policy':'离线边界未通过，已阻止启动。',
 'start_failed':'启动未完成；旧索引与原件未修改。请查看匿名状态。',
}
class LocalError(RuntimeError):
 def __init__(self,code):self.code=code;super().__init__(MESSAGES.get(code,MESSAGES['start_failed']))

def private_write(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
 temporary=path.with_name(path.name+'.new')
 fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
 with os.fdopen(fd,'w') as file:
  json.dump(value,file,ensure_ascii=False,indent=2);file.write('\n');file.flush();os.fsync(file.fileno())
 os.replace(temporary,path);path.chmod(0o600)

def absolute(value):
 if not isinstance(value,str) or not value or '<' in value or '\0' in value:raise LocalError('config_invalid')
 path=Path(value).expanduser()
 if not path.is_absolute():path=REPO/path
 return Path(os.path.abspath(path))

def unlinked(path,*,must_exist=True):
 path=Path(path)
 for parent in [path,*path.parents]:
  try:value=parent.lstat()
  except FileNotFoundError:
   if parent==path and not must_exist:continue
   raise LocalError('path_invalid')
  if stat.S_ISLNK(value.st_mode) or getattr(value,'st_flags',0)&0x40000000:raise LocalError('path_invalid')
 if must_exist and not path.is_dir():raise LocalError('path_invalid')

@dataclass(frozen=True)
class Config:
 file:Path
 media:Path
 index:Path
 runtime:Path
 python:Path
 encoder:Path
 verifier:Path
 native:Path
 port:int=19130
 device:str='mps'
 synthetic:bool=False
 no_sync_confirmed:bool=False
 source_index:Path|None=None
 @property
 def state(self):return self.runtime/'state'
 @property
 def health(self):return self.state/'health.json'
 @property
 def policy(self):return self.runtime/'photo-review-policy.json'

def load(path=DEFAULT_CONFIG):
 path=Path(path).absolute().absolute()
 if not path.is_file() or path.is_symlink():raise LocalError('config_missing')
 s=path.stat()
 if s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)&0o077:raise LocalError('config_permissions')
 try:
  value=json.loads(path.read_text())
  required={'schema','MEDIA_ROOT','INDEX_ROOT','RUNTIME_PYTHON','ENCODER_MODEL_ROOT','VERIFIER_MODEL_ROOT','NATIVE_BIN_ROOT'}
  allowed=required|{'PORT','DEVICE','SCOPE','NO_SYNC_CONFIRMED','SOURCE_INDEX_ROOT','RUNTIME_ROOT'}
  if not required<=set(value) or set(value)-allowed or value['schema']!=1:raise ValueError()
  media=absolute(value['MEDIA_ROOT']);index=absolute(value['INDEX_ROOT'])
  runtime=absolute(value.get('RUNTIME_ROOT','runtime'))
  synthetic=value.get('SCOPE','production')=='synthetic'
  if value.get('SCOPE','production') not in ('production','synthetic'):raise ValueError()
  port=value.get('PORT',19130);device=value.get('DEVICE','mps')
  if not isinstance(port,int) or isinstance(port,bool) or not 1024<=port<=65000 or device not in ('mps','cpu'):raise ValueError()
  if runtime!=REPO/'runtime' and not runtime.is_relative_to(REPO/'runtime/test-runs'):raise ValueError()
  if synthetic:
   if not media.is_relative_to(REPO/'runtime/test-runs') or not (media/'SYNTHETIC-ONLY').is_file():raise ValueError()
  elif index.parent!=media or index.name!='.memorysearch-index':raise ValueError()
  if media==index:raise ValueError()
  unlinked(media);unlinked(index,must_exist=index.exists())
  return Config(path,media,index,runtime,absolute(value['RUNTIME_PYTHON']),absolute(value['ENCODER_MODEL_ROOT']),absolute(value['VERIFIER_MODEL_ROOT']),absolute(value['NATIVE_BIN_ROOT']),port,device,synthetic,value.get('NO_SYNC_CONFIRMED') is True,absolute(value['SOURCE_INDEX_ROOT']) if value.get('SOURCE_INDEX_ROOT') else None)
 except LocalError:raise
 except Exception:raise LocalError('config_invalid') from None

def storage_gate(config,probe):
 if config.synthetic:return
 checks=probe.get('checks',[])
 if not config.no_sync_confirmed or not checks:raise LocalError('sync_unconfirmed')
 for row in checks:
  if row.get('local_internal') is not True or row.get('symlink_or_alias') is not False or row.get('icloud_targeted') is not False or row.get('placeholder') is not False or row.get('sync_marker') is not False:raise LocalError('sync_unconfirmed')
