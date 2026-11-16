#!/usr/bin/env python3
"""Project-scoped local model and OpenCode launchers. No external networking."""
from __future__ import annotations

import os
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parent
NETWORK_PROFILE = '(version 1) (allow default) (deny network*) (allow network-outbound (remote ip "localhost:*")) (allow network-inbound (local ip "localhost:*"))'


def offline_environment() -> dict[str, str]:
    # Do not inherit cloud credentials, proxy settings or a shared OpenCode server.
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'USER', 'LOGNAME', 'SHELL', 'LANG', 'LC_ALL', 'TERM', 'TMPDIR') if key in os.environ}
    state = PROJECT / '.local/opencode-state'
    for key, folder in (('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'), ('XDG_CACHE_HOME', 'cache'), ('XDG_STATE_HOME', 'state')):
        path = state / folder
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    isolated_home = state / 'home'
    isolated_home.mkdir(exist_ok=True)
    env.update({
        'OPENCODE_TEST_HOME': str(isolated_home),
        'OPENCODE_CONFIG_DIR': str(state / 'config/opencode'),
        'OPENCODE_CONFIG': str(PROJECT / 'opencode.json'),
        'OPENCODE_CONFIG_PROJECT_DISABLE': '1',
        'OPENCODE_DISABLE_PROJECT_CONFIG': '1',
        'OPENCODE_DISABLE_CLAUDE_CODE': '1',
        'OPENCODE_DISABLE_CLAUDE_CODE_SKILLS': '1',
        'OPENCODE_DISABLE_AUTOUPDATE': '1',
        'OPENCODE_DISABLE_MODELS_FETCH': '1',
        'OPENCODE_DISABLE_FREE_MODELS': '1',
        'OPENCODE_DISABLE_FILEWATCHER': '1',
        'OPENCODE_CLIENT': 'cli',
        'HF_HOME': str(PROJECT / '.cache/huggingface'),
        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
        'HF_HUB_DISABLE_TELEMETRY': '1', 'DO_NOT_TRACK': '1',
        'OTEL_SDK_DISABLED': 'true', 'TOKENIZERS_PARALLELISM': 'false',
        'NO_PROXY': '127.0.0.1,localhost,::1',
    })
    return env


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ('model', 'opencode', 'workflow', 'library', 'search', 'quality'):
        raise SystemExit('Usage: local_runtime.py model | opencode [args] | workflow synthetic|trial | library | search')
    mode, args = sys.argv[1], sys.argv[2:]
    if mode == 'model':
        import media_index as app
        import urllib.parse
        config = app.load_config(PROJECT / 'config.mlx.json')
        port = urllib.parse.urlsplit(app.local_model_url(config.get('mlx_vlm_url', 'http://127.0.0.1:8080/v1'))).port
        if args:
            raise SystemExit('Model configuration is fixed to the downloaded local vision checkpoint.')
        command = [str(PROJECT / '.venv-mlx/bin/python'), '-m', 'mlx_vlm.server',
                   '--model', str(PROJECT / '.models/Qwen3.8-27B-4bit'),
                   '--host', '127.0.0.1', '--port', str(port), '--max-num-seqs', '1',
                   '--max-tokens', '2048', '--max-pixels', '1048576',
                   '--vision-cache-size', '2', '--log-level', 'WARNING']
    elif mode == 'workflow':
        if args not in (['synthetic'], ['trial']):
            raise SystemExit('Choose synthetic or trial.')
        command = [str(PROJECT / '.venv-mlx/bin/python'), str(PROJECT / 'local_workflow.py'), *args]
    elif mode == 'quality':
        command = [str(PROJECT / '.venv-mlx/bin/python'), str(PROJECT / 'quality_suite.py'), *args]
    elif mode == 'library':
        command = [str(PROJECT / '.venv-mlx/bin/python'), str(PROJECT / 'media_library.py'), *(args or ['status'])]
    elif mode == 'search':
        if args:
            raise SystemExit('The parent root comes only from the authorized local configuration.')
        request = json.loads((PROJECT / '.local/private-trial.json').read_text())
        if request.get('authorized') is not True:
            raise SystemExit('An authorized parent root is required.')
        index = Path(request['authorized_scope_root']).resolve(strict=True) / 'index.json'
        command = [str(PROJECT / '.venv-mlx/bin/python'), str(PROJECT / 'media_index.py'),
                   '--config', str(PROJECT / 'config.mlx.json'), 'serve', '--index', str(index),
                   '--no-semantic', '--port', '8765']
    else:
        command = [str(PROJECT / '.local/opencode-2.3.0/bin/opencode'), '--standalone', *args]
    os.chdir(PROJECT)
    command = ['/usr/bin/sandbox-exec', '-p', NETWORK_PROFILE, *command]
    os.execve(command[0], command, offline_environment())


if __name__ == '__main__':
    main()
