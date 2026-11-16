#!/usr/bin/env bash
# Apple Silicon only. Everything is installed inside this checkout.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"
unset PYTHONPATH PYTHONHOME UV_INDEX_URL UV_EXTRA_INDEX_URL UV_DEFAULT_INDEX UV_INDEX UV_FIND_LINKS UV_NO_INDEX UV_INSECURE_HOST UV_CONFIG_FILE UV_PYTHON_INSTALL_MIRROR UV_PYTHON_DOWNLOADS_JSON_URL
export PYTHONNOUSERSITE=1
fail() { printf '安装未完成：%s\n' "$*" >&2; exit 1; }
[[ "$(uname -s)/$(uname -m)" == Darwin/arm64 ]] || fail '首发仅支持 Apple Silicon Mac；不提供云端或文字模型替代。'
MAC_MAJOR="$(sw_vers -productVersion | cut -d. -f1)"
(( MAC_MAJOR >= 15 )) || fail '需要 macOS 15 或更新版本。'
MEMORY_BYTES="$(sysctl -n hw.memsize)"
(( MEMORY_BYTES >= 51539607552 )) || fail 'Qwen3.8 27B 视觉配置至少需要 48 GiB 统一内存，推荐 64 GiB；不会自动降级模型。'
FREE_KIB="$(df -Pk "$PROJECT_DIR" | awk 'NR == 2 {print $4}')"
if [[ -x .venv-mlx/bin/python && -x .local/tools/uv ]]; then
  (( FREE_KIB >= 8388608 )) || fail '继续安装请保留至少 8 GiB 可用空间；模型的剩余下载空间会逐项检查。'
else
  (( FREE_KIB >= 47185920 )) || fail '首次安装请预留至少 45 GiB 磁盘空间。'
fi
printf 'Apple Silicon · macOS %s · %.0f GiB 内存\n' "$MAC_MAJOR" "$(awk "BEGIN {print $MEMORY_BYTES/1073741824}")"
umask 077
mkdir -p .local/tools .local/downloads .cache/uv
export UV_CACHE_DIR="$PROJECT_DIR/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$PROJECT_DIR/.local/python"
export UV_PYTHON_BIN_DIR="$PROJECT_DIR/.local/bin"
export UV_NO_CONFIG=1 UV_NO_PROGRESS=1 DO_NOT_TRACK=1 HF_HUB_DISABLE_TELEMETRY=1
UV_BIN="$PROJECT_DIR/.local/tools/uv"
UV_SHA=33540eb7c883ab857eff79bd5ac2aa31fe27b595abecb4a9c003a2c998447232
if [[ ! -x "$UV_BIN" ]] || [[ "$("$UV_BIN" --version)" != 'uv 0.11.28'* ]]; then
  ARCHIVE="$PROJECT_DIR/.local/downloads/uv-0.11.28.tar.gz"
  curl -q --proto '=https' --proto-redir '=https' --tlsv1.2 --retry 3 --connect-timeout 20 -fL \
    https://github.com/astral-sh/uv/releases/download/0.11.28/uv-aarch64-apple-darwin.tar.gz -o "$ARCHIVE"
  [[ "$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')" == "$UV_SHA" ]] || fail 'uv 校验失败，未执行下载内容。'
  tar -xzf "$ARCHIVE" -C .local/tools --strip-components=1 uv-aarch64-apple-darwin/uv
fi
"$UV_BIN" python install --no-bin 3.12.13
if [[ -e .venv-mlx ]] && ! .venv-mlx/bin/python -I -c 'import sys; assert sys.version_info[:3] == (3,12,13)' >/dev/null 2>&1; then
  mv .venv-mlx ".venv-mlx.incompatible-$(date -u +%Y%m%dT%H%M%SZ)"
fi
if [[ ! -x .venv-mlx/bin/python ]]; then
  "$UV_BIN" venv .venv-mlx --python 3.12.13 --managed-python
fi
"$UV_BIN" pip install --python .venv-mlx/bin/python --require-hashes --only-binary :all: \
  --index-url https://pypi.org/simple -r requirements-mlx.lock
.venv-mlx/bin/python install_local.py "$@"
