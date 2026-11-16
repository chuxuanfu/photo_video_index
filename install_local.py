#!/usr/bin/env python3
"""Download verified runtime assets, configure the local-only application, self-test."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import time
import urllib.request
import urllib.error

PROJECT = Path(__file__).resolve().parent
OPENCODE_VERSION = '2.3.0'
OPENCODE_URL = 'https://registry.npmjs.org/@opencode/cli-darwin-arm64/-/cli-darwin-arm64-2.3.0.tgz'
OPENCODE_INTEGRITY = 'ey2HOWX2DK2YJrKta5a+pcvIlkiCyJJsN7NjkJZPsVg9TDXpQWE1OJLWVFqjxZcWNtaMjvKYlrlCY6g8Z2Lb+A=='
OPENCODE_BINARY_SHA256 = '9f3147cae4ca323827d3ca39451b0d3431b2906e79a08e9b09d1eb34bb703f52'


def matches(path, spec):
    if path.is_symlink() or not path.is_file() or path.stat().st_size != spec['size']:
        return False
    with path.open('rb') as stream:
        if 'git_blob_sha1' in spec:
            hasher = hashlib.sha1(f"blob {spec['size']}\0".encode())
        else:
            hasher = hashlib.sha256()
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            hasher.update(block)
    return hasher.hexdigest() == spec.get('sha256', spec.get('git_blob_sha1'))


def download(url, target):
    """HTTPS, resumable partial files; callers must verify before use."""
    if not url.startswith('https://'):
        raise RuntimeError('Only HTTPS downloads are allowed')
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + '.part')
    if partial.is_symlink() or target.is_symlink():
        raise RuntimeError('Download targets may not be symlinks')
    for attempt in range(4):
        offset = partial.stat().st_size if partial.exists() else 0
        request = urllib.request.Request(url, headers={'Range': f'bytes={offset}-'} if offset else {})
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                if not response.geturl().startswith('https://'):
                    raise RuntimeError('Insecure redirect rejected')
                append = offset and response.status == 206
                if append and not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-'):
                    raise RuntimeError('Invalid resumed download')
                with partial.open('ab' if append else 'wb') as output:
                    shutil.copyfileobj(response, output, 8 * 1024 * 1024)
            partial.replace(target)
            return
        except urllib.error.HTTPError as exc:
            if exc.code==416 and offset and exc.headers.get('Content-Range','')==f'bytes */{offset}':
                partial.replace(target)
                return
            if attempt==3:raise
            time.sleep(min(2 ** attempt,4))
        except Exception:
            if attempt == 3:
                raise
            time.sleep(min(2 ** attempt, 4))


def install_models():
    manifest = json.loads((PROJECT / 'model-manifest.json').read_text())
    verified={}
    remaining=0
    for model in manifest['models']:
        for spec in model['files']:
            key=(model['directory'],spec['path'])
            verified[key]=matches(PROJECT/'.models'/model['directory']/spec['path'],spec)
            if not verified[key]:remaining+=spec['size']
    if shutil.disk_usage(PROJECT).free < remaining+5*1024**3:
        raise RuntimeError(f'Insufficient disk space: remaining model downloads plus reserve need {(remaining+5*1024**3)/1024**3:.1f} GiB')
    total = sum(f['size'] for model in manifest['models'] for f in model['files'])
    print(f'Model download/verification: {total / 1e9:.2f} GB, pinned revisions; existing verified files are reused.', flush=True)
    for model in manifest['models']:
        directory = PROJECT / '.models' / model['directory']
        directory.mkdir(parents=True, exist_ok=True)
        for spec in model['files']:
            relative = PurePosixPath(spec['path'])
            if relative.is_absolute() or '..' in relative.parts:
                raise RuntimeError('Invalid model manifest path')
            target = directory.joinpath(*relative.parts)
            if verified[(model['directory'],spec['path'])]:
                continue
            if target.exists():
                target.rename(target.with_name(target.name + f'.unverified-{time.time_ns()}'))
            print(f"Downloading {model['repo']}: {spec['path']}", flush=True)
            download(f"https://huggingface.co/{model['repo']}/resolve/{model['revision']}/{spec['path']}", target)
            if not matches(target, spec):
                raise RuntimeError('Model checksum failed; installation stopped')
        receipt = {'repo_id': model['repo'], 'revision': model['revision'], 'verified_files': len(model['files']), 'bytes': sum(f['size'] for f in model['files'])}
        (directory / 'download-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    config = json.loads((PROJECT / '.models/Qwen3.8-27B-4bit/config.json').read_text())
    if not config.get('vision_config') or config.get('quantization', {}).get('bits') != 4:
        raise RuntimeError('The pinned checkpoint must contain the expected vision configuration')


def install_opencode():
    directory = PROJECT / f'.local/opencode-{OPENCODE_VERSION}'
    binary = directory / 'bin/opencode'
    if binary.is_file() and hashlib.file_digest(binary.open('rb'), 'sha256').hexdigest() == OPENCODE_BINARY_SHA256:
        return
    archive = PROJECT / '.local/downloads/opencode-2.3.0.tgz'
    if not archive.exists():
        download(OPENCODE_URL, archive)
    actual = base64.b64encode(hashlib.file_digest(archive.open('rb'), 'sha512').digest()).decode()
    if actual != OPENCODE_INTEGRITY:
        archive.rename(archive.with_name(archive.name+f'.rejected-{time.time_ns()}'))
        raise RuntimeError('OpenCode package checksum failed; rerun to download a clean copy')
    binary.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as package:
        member = package.getmember('package/bin/opencode')
        if not member.isfile():
            raise RuntimeError('Unexpected OpenCode archive entry')
        with package.extractfile(member) as source, binary.open('wb') as output:
            shutil.copyfileobj(source, output)
    binary.chmod(0o700)
    if hashlib.file_digest(binary.open('rb'), 'sha256').hexdigest() != OPENCODE_BINARY_SHA256:
        raise RuntimeError('OpenCode executable checksum failed')


def configure():
    from local_runtime import NETWORK_PROFILE, offline_environment
    config_path = PROJECT / 'config.mlx.json'
    if not config_path.exists():
        config = json.loads((PROJECT / 'config.mlx.example.json').read_text())
        config['vision_model'] = str(PROJECT / '.models/Qwen3.8-27B-4bit')
        config['mlx_asr_model'] = str(PROJECT / '.models/whisper-large-v3-turbo')
        config_path.write_text(json.dumps(config, indent=2) + '\n')
    agent_path = PROJECT / 'opencode.json'
    agent = json.loads((PROJECT / 'opencode.example.json').read_text())
    local_config = json.loads(config_path.read_text())
    import media_index as app
    app.local_model_url(local_config['mlx_vlm_url'])
    agent['providers']['local-mlx']['settings']['baseURL'] = local_config['mlx_vlm_url']
    agent['providers']['local-mlx']['models']['qwen3.8-27b']['modelID'] = str(PROJECT / '.models/Qwen3.8-27B-4bit')
    payload = json.dumps(agent, indent=2) + '\n'
    if agent_path.exists() and agent_path.read_text() != payload:
        backup = PROJECT / '.local/config-backups'
        backup.mkdir(parents=True, exist_ok=True)
        shutil.copy2(agent_path, backup / f'opencode-{time.time_ns()}.json')
    agent_path.write_text(payload)
    # mlx-whisper expects ffmpeg on PATH. Keep it inside the environment.
    import imageio_ffmpeg
    ffmpeg = PROJECT / '.venv-mlx/bin/ffmpeg'
    if not ffmpeg.exists():
        ffmpeg.symlink_to(imageio_ffmpeg.get_ffmpeg_exe())
    for path in (config_path, agent_path):
        path.chmod(0o600)
    test = subprocess.run(['/usr/bin/sandbox-exec', '-p', NETWORK_PROFILE, '/usr/bin/true'], env=offline_environment())
    if test.returncode:
        raise RuntimeError('The local-only runtime sandbox is unavailable; no insecure fallback')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-self-test', action='store_true', help='Install only; does not claim validation has passed')
    args = parser.parse_args()
    os.umask(0o077)
    install_models()
    install_opencode()
    configure()
    if not args.skip_self_test:
        subprocess.run([str(PROJECT / '.venv-mlx/bin/python'), str(PROJECT / 'local_runtime.py'), 'library', 'self-test'], check=True)
    print('Installation complete. No private media was scanned. Select a folder with 开始索引.command or ./library-local configure --root PATH.', flush=True)


if __name__ == '__main__':
    main()
