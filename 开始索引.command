#!/bin/zsh
cd "${0:A:h}" || exit 1
if [[ ! -x .venv-mlx/bin/python ]]; then
  echo '请先双击 setup.command 完成安装。'
  read 'reply?按 Return 关闭窗口…'
  exit 1
fi
root=$(/usr/bin/osascript -e 'POSIX path of (choose folder with prompt "选择获准读取的照片/视频母文件夹；这里只先处理 5 个文件")') || exit 0
./library-local configure --root "$root" && ./library-local catalog && ./library-local analyze --limit 5
result=$?
echo '索引保存在所选母文件夹的 index.json；原件保持只读。未通过本地检查的条目进入待复审状态。'
read 'reply?按 Return 关闭窗口…'
exit $result
