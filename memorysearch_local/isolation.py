"""macOS process policies: originals read-only, private state local, no worker egress."""
from pathlib import Path
import json,os,socket,subprocess
from .config import REPO,LocalError

def q(path):return json.dumps(str(path))
def worker_policy(config):
 reads=[REPO/'memorysearch_local',config.runtime,config.media,config.index,config.python.parent.parent,config.encoder,config.verifier,config.native]
 sub=' '.join('(subpath '+q(p)+')' for p in reads)
 writable=' '.join('(subpath '+q(p)+')' for p in [config.runtime,config.index])
 return ('(version 1)\n(allow default)\n(deny network-outbound)\n'
  f'(deny network-inbound (require-not (local ip "localhost:{config.port}")))\n'
  f'(deny network-bind (require-not (local ip "localhost:{config.port}")))\n'
  '(deny mach-lookup (global-name "com.apple.nsurlsessiond") (global-name "com.apple.nsurlsessiond.privileged") (global-name "com.apple.mDNSResponder") (global-name "com.apple.mDNSResponderHelper"))\n'
  '(deny file-read-data (require-all (subpath "/Users") (require-not (require-any '+sub+'))))\n'
  '(deny file-read-data (subpath "/Volumes") (subpath "/Network") (subpath "/net"))\n'
  '(deny file-read-data (require-all (subpath "/private/var/folders") (require-not (subpath '+q(config.runtime)+'))))\n'
  '(deny file-read-data (require-all (subpath "/private/tmp") (require-not (subpath '+q(config.runtime)+'))))\n'
  '(deny file-write* (require-all (subpath '+q(config.media)+') (require-not (subpath '+q(config.index)+'))))\n'
  '(deny file-write* (require-not (require-any '+writable+' (literal "/dev/null"))))\n')

def client_policy(binary,state,port):
 return ('(version 1)\n(allow default)\n'
  f'(deny network-outbound (require-not (remote ip "localhost:{port}")))\n'
  '(deny network-inbound)\n(deny network-bind)\n'
  '(deny mach-lookup (global-name "com.apple.nsurlsessiond") (global-name "com.apple.nsurlsessiond.privileged") (global-name "com.apple.mDNSResponder"))\n'
  '(deny file-read-data (require-all (subpath "/Users") (require-not (require-any (literal '+q(binary)+') (subpath '+q(state)+')))))\n'
  '(deny file-read-data (subpath "/Volumes") (subpath "/Network") (subpath "/net"))\n'
  '(deny file-write* (require-not (require-any (subpath '+q(state)+') (literal "/dev/null"))))\n')

def maintenance_policy(config,source,*,discover=False):
 reads=[REPO/'memorysearch_local',config.file,config.runtime,config.index,source,config.python.parent.parent,config.encoder,config.verifier,config.native]+([config.media] if discover else [])
 readable=' '.join('(subpath '+q(p)+')' for p in reads)
 writable=' '.join('(subpath '+q(p)+')' for p in [config.runtime,config.index])
 original_boundary='' if discover else '(deny file-read-data (require-all (subpath '+q(config.media)+') (require-not (require-any (subpath '+q(config.index)+') (subpath '+q(source)+')))))\n'
 return ('(version 1)\n(allow default)\n(deny network*)\n'+original_boundary+
  '(deny mach-lookup (global-name "com.apple.nsurlsessiond") (global-name "com.apple.nsurlsessiond.privileged") (global-name "com.apple.mDNSResponder"))\n'
  '(deny file-read-data (require-all (subpath "/Users") (require-not (require-any '+readable+'))))\n'
  '(deny file-read-data (subpath "/Volumes") (subpath "/Network") (subpath "/net"))\n'
  '(deny file-write* (require-not (require-any '+writable+' (literal "/dev/null"))))\n')

def runtime_environment(config):
 cache=config.runtime/'cache';scratch=config.runtime/'scratch'
 cache.mkdir(parents=True,exist_ok=True,mode=0o700);scratch.mkdir(parents=True,exist_ok=True,mode=0o700)
 return {'PATH':str(config.native)+':/usr/bin:/bin','TMPDIR':str(scratch)+'/',
  'HF_HOME':str(cache),'HF_HUB_CACHE':str(cache/'hub'),'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1',
  'HF_HUB_DISABLE_TELEMETRY':'1','DO_NOT_TRACK':'1','OTEL_SDK_DISABLED':'true','TOKENIZERS_PARALLELISM':'false',
  'XDG_CACHE_HOME':str(cache),'TORCH_HOME':str(cache/'torch'),'MPLCONFIGDIR':str(cache/'matplotlib'),
  'CFFIXED_USER_HOME':str(cache),'MEMORYSEARCH_PORT':str(config.port)}

def port_free(port):
 with socket.socket() as sock:
  sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
  try:sock.bind(('127.0.0.1',port))
  except OSError:return False
  return True
