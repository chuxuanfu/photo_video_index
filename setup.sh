#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"
export UV_CACHE_DIR="$PROJECT_DIR/.cache/uv"

fail() { printf '安装失败：%s\n' "$*" >&2; exit 1; }
note() { printf '\n==> %s\n' "$*"; }

command -v curl >/dev/null 2>&1 || fail "系统缺少 curl。请先安装 curl。"
command -v uname >/dev/null 2>&1 || fail "无法识别系统。"
OS_NAME="$(uname -s)"
CPU_ARCH="$(uname -m)"
case "$OS_NAME/$CPU_ARCH" in
  Darwin/arm64)
    BACKEND=mlx
    BACKEND_REQUIREMENTS=requirements-macos-arm64.txt
    ;;
  Darwin/x86_64|Linux/x86_64|Linux/aarch64)
    BACKEND=faster
    BACKEND_REQUIREMENTS=requirements-portable.txt
    ;;
  *)
    fail "当前自动安装支持 macOS Apple Silicon/Intel、Linux x86_64/ARM64；检测到 ${OS_NAME}/${CPU_ARCH}。Windows 请双击 setup.cmd。"
    ;;
esac

if [[ "$OS_NAME" == Darwin ]]; then
  MAJOR="$(sw_vers -productVersion | cut -d. -f1)"
  [[ "$MAJOR" =~ ^[0-9]+$ ]] || fail "无法确定 macOS 版本。"
  (( MAJOR >= 14 )) || fail "Ollama 当前要求 macOS 14 或更新版本。"
fi

note "平台：${OS_NAME} ${CPU_ARCH}；语音后端：${BACKEND}"
note "准备 uv 和独立 Python 3.12 环境"
UV_BIN="$(command -v uv 2>/dev/null || true)"
if [[ -z "$UV_BIN" && -x "${HOME:?}/.local/bin/uv" ]]; then UV_BIN="$HOME/.local/bin/uv"; fi
if [[ -z "$UV_BIN" && -x "${HOME:?}/.cargo/bin/uv" ]]; then UV_BIN="$HOME/.cargo/bin/uv"; fi
if [[ -z "$UV_BIN" ]]; then
  tmp_installer="$(mktemp)"
  trap 'rm -f "$tmp_installer"' EXIT
  curl --retry 3 --connect-timeout 15 -fsSL https://astral.sh/uv/install.sh -o "$tmp_installer"
  sh "$tmp_installer"
  rm -f "$tmp_installer"
  trap - EXIT
  UV_BIN="$(command -v uv 2>/dev/null || true)"
  if [[ -z "$UV_BIN" && -x "$HOME/.local/bin/uv" ]]; then UV_BIN="$HOME/.local/bin/uv"; fi
  if [[ -z "$UV_BIN" && -x "$HOME/.cargo/bin/uv" ]]; then UV_BIN="$HOME/.cargo/bin/uv"; fi
  [[ -n "$UV_BIN" ]] || fail "uv 安装后仍未找到；请查看 https://docs.astral.sh/uv/getting-started/installation/"
fi
"$UV_BIN" python install --no-bin 3.12
VENV_MARKER=.venv/.media-index-install-path
if [[ ! -x .venv/bin/python ]] || [[ ! -f "$VENV_MARKER" ]] || [[ "$(cat "$VENV_MARKER")" != "$PROJECT_DIR" ]] || ! .venv/bin/python -c 'import sys; assert sys.version_info[:2] == (3, 12)' >/dev/null 2>&1; then
  "$UV_BIN" venv --clear .venv --python 3.12
  printf '%s\n' "$PROJECT_DIR" > "$VENV_MARKER"
fi
PYTHON="$PROJECT_DIR/.venv/bin/python"
"$UV_BIN" pip install --python "$PYTHON" -r requirements-common.txt -r "$BACKEND_REQUIREMENTS"
DEFAULT_MODEL="$("$PYTHON" setup_support.py suggest-model --backend "$BACKEND")"
"$PYTHON" setup_support.py prepare-config --default-model "${MEDIA_INDEX_MODEL:-$DEFAULT_MODEL}"
"$PYTHON" setup_support.py link-ffmpeg

note "准备 Ollama"
find_ollama() {
  local found
  found="$(command -v ollama 2>/dev/null || true)"
  if [[ -n "$found" ]]; then printf '%s\n' "$found"; return; fi
  if [[ -x /Applications/Ollama.app/Contents/Resources/ollama ]]; then
    printf '%s\n' /Applications/Ollama.app/Contents/Resources/ollama; return
  fi
  if [[ -x "$HOME/Applications/Ollama.app/Contents/Resources/ollama" ]]; then
    printf '%s\n' "$HOME/Applications/Ollama.app/Contents/Resources/ollama"; return
  fi
}
OLLAMA_BIN="$(find_ollama)"
if [[ -z "$OLLAMA_BIN" ]]; then
  tmp_installer="$(mktemp)"
  trap 'rm -f "$tmp_installer"' EXIT
  curl --retry 3 --connect-timeout 15 -fsSL https://ollama.com/install.sh -o "$tmp_installer"
  sh "$tmp_installer"
  rm -f "$tmp_installer"
  trap - EXIT
  OLLAMA_BIN="$(find_ollama)"
  [[ -n "$OLLAMA_BIN" ]] || fail "Ollama 安装后仍未找到；请查看 https://ollama.com/download"
fi

if ! curl --max-time 3 -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  if [[ "$OS_NAME" == Darwin && -d /Applications/Ollama.app ]]; then
    open -a /Applications/Ollama.app || true
  fi
  if ! curl --max-time 3 -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    mkdir -p .cache
    nohup "$OLLAMA_BIN" serve > .cache/ollama-serve.log 2>&1 < /dev/null &
  fi
  for _ in {1..30}; do
    if curl --max-time 3 -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then break; fi
    sleep 1
  done
fi
curl --max-time 3 -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1 || fail "Ollama 没有启动。请打开 Ollama 应用后重跑；后台日志在 .cache/ollama-serve.log。"

MODEL="$("$PYTHON" setup_support.py model-name)"
if [[ "${MEDIA_INDEX_SKIP_MODELS:-0}" != 1 ]]; then
  note "下载或检查视觉模型 ${MODEL}（首次需要联网；请预留模型和运行空间）"
  if ! "$OLLAMA_BIN" list | awk 'NR > 1 {print $1}' | grep -Fxq "$MODEL"; then
    "$OLLAMA_BIN" pull "$MODEL"
  else
    echo "视觉模型已存在，跳过下载。"
  fi
  note "下载或检查语音模型"
  "$PYTHON" setup_support.py download-asr --backend "$BACKEND"
else
  echo "已按 MEDIA_INDEX_SKIP_MODELS=1 跳过模型下载；此时尚不能离线生成新索引。"
fi

note "检查运行环境"
"$PYTHON" setup_support.py verify --backend "$BACKEND"
"$PYTHON" media_index.py doctor --model "$MODEL"
echo
echo "完成：可双击试跑.command，或用 .venv/bin/python media_index.py index --photo-root /你的/Photo --video-root /你的/Video"
