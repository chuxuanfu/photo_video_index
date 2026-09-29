#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -x .venv/bin/python ]]; then
  echo "尚未安装本项目。请先双击 setup.command，或在终端运行 zsh setup.command。"
  exit 1
fi
PYTHON=.venv/bin/python

echo "请选择试跑内容：1 照片；2 视频；3 照片和视频"
read "choice?输入 1、2 或 3："
photo=""
video=""
if [[ "$choice" == "1" || "$choice" == "3" ]]; then
  photo=$(osascript -e 'POSIX path of (choose folder with prompt "请选择 Photo 文件夹")')
fi
if [[ "$choice" == "2" || "$choice" == "3" ]]; then
  video=$(osascript -e 'POSIX path of (choose folder with prompt "请选择 Video 文件夹")')
fi
if [[ -z "$photo" && -z "$video" ]]; then
  echo "没有选择文件夹。"
  exit 1
fi

"$PYTHON" media_index.py doctor
command=("$PYTHON" media_index.py index --limit 1 --max-frames 3)
indexes=()
if [[ -n "$photo" ]]; then
  command+=(--photo-root "$photo")
  indexes+=(--index "${photo%/}/index.json")
fi
if [[ -n "$video" ]]; then
  command+=(--video-root "$video")
  indexes+=(--index "${video%/}/index.json")
fi

"${command[@]}"
echo "索引生成完毕；现在打开本机搜索页面。关闭此终端窗口或按 Control-C 可结束搜索。"
"$PYTHON" media_index.py serve "${indexes[@]}" --open-browser
