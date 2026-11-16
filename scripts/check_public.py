#!/usr/bin/env python3
"""Audit the exact staged Git content; never print matched secret or private data."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

FORBIDDEN = {'.local','.models','.venv','.venv-mlx','.cache','.aws','.ssh','.codex','.agents'}
PUBLIC_ROOTS = {'docs','scripts','tests','native'}
ROOT_FILES = {'.gitignore','AGENTS.md','README.md','PROJECT_CONFIG.md','DEMO_RESULTS.md',
 'config.example.json','config.mlx.example.json','opencode.example.json','model-manifest.json','quality-release.json',
 'requirements-common.txt','requirements-macos-arm64.txt','requirements-portable.txt','requirements-mlx.txt','requirements-mlx.lock',
 'media_index.py','media_library.py','media_decode.py','native_frames.py','single_library.py','index_pipeline.py','grounded_index.py','local_ocr.py',
 'speech_quality.py','local_runtime.py','local_workflow.py','quality_suite.py','install_local.py','setup_support.py',
 'setup.sh','setup.command','setup.cmd','setup.ps1','opencode-local','library-local','试跑.command','开始索引.command','项目架构介绍.pdf'}
REVIEWED_ARTIFACTS = {'tests/fixtures/local-test-speech.mp3': 'd7783561d6ce664d217d6952a53782916a39a03c91e92e4a2358f517a173d56f', '项目架构介绍.pdf': 'df3d09620fca151c8736ae5b5ea6b4c651ce66335517e54aa9e0c9cfcfe43a58'}
SECRETS = re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16}|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY)')
EMAILS = re.compile(rb'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
HOMES = re.compile(rb'/' + rb'Users/[^\s\x00`"<>]+')


def audit_name(name):
    path=PurePosixPath(name)
    if any(part in FORBIDDEN or part.startswith('.venv-mlx.') for part in path.parts):
        return 'private_runtime_path'
    if name not in ROOT_FILES and path.parts[0] not in PUBLIC_ROOTS:
        return 'outside_public_allowlist'
    if path.name=='index.json' and path.parts[0]!='demo':
        return 'personal_index'
    if name in REVIEWED_ARTIFACTS:
        return None
    if path.suffix.lower() in {'.png','.svg','.pdf','.mp3','.m4a','.aac','.aif','.aiff','.flac','.ogg','.safetensors','.npz','.wav','.mp4','.mov','.jpg','.jpeg','.heic','.dng','.nef','.gif','.webp'} and path.parts[0]!='demo':
        return 'private_media_or_weights'
    if path.suffix.lower() in {'.key','.pem','.p12','.sqlite','.db'}:
        return 'credential_or_session_file'
    return None


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--private-index',type=Path,help='Optional local index for an additional private-path leak comparison; never uploaded')
    args=parser.parse_args()
    raw=subprocess.check_output(['git','diff','--cached','--name-only','--diff-filter=ACMR','-z'])
    names=[n.decode() for n in raw.split(b'\0') if n]
    private=[]
    if args.private_index:
        data=json.loads(args.private_index.read_text())
        private=[a['relative_path'].encode() for a in data['assets'] if len(a['relative_path'])>=12]
    findings=[]
    size=0
    for name in names:
        reason=audit_name(name)
        content=subprocess.check_output(['git','show',':'+name])
        size+=len(content)
        if name in REVIEWED_ARTIFACTS and hashlib.sha256(content).hexdigest()!=REVIEWED_ARTIFACTS[name]:
            findings.append({'file':name,'reason':'reviewed_artifact_changed'})
        if reason:findings.append({'file':name,'reason':reason})
        if len(content)>10*1024*1024:findings.append({'file':name,'reason':'oversized_public_file'})
        if SECRETS.search(content):findings.append({'file':name,'reason':'possible_secret'})
        if name not in REVIEWED_ARTIFACTS and any(m.group().lower()!=b'git'+b'@github.com' for m in EMAILS.finditer(content)):
            findings.append({'file':name,'reason':'possible_personal_email'})
        if HOMES.search(content):findings.append({'file':name,'reason':'personal_home_path'})
        if any(value in content for value in private):findings.append({'file':name,'reason':'private_media_relative_path'})
    print(json.dumps({'status':'passed' if not findings else 'blocked','staged_files':len(names),'staged_bytes':size,'findings':findings},indent=2))
    return 1 if findings else 0


if __name__=='__main__':raise SystemExit(main())
