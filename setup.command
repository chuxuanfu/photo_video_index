#!/bin/zsh
# Finder 可双击的 macOS 安装入口；实际逻辑在 setup.sh。
cd "${0:A:h}" || exit 1
bash ./setup.sh
result=$?
echo
if [[ $result -eq 0 ]]; then
  echo "安装完成。现在可以双击「试跑.command」。"
else
  echo "安装未完成；请查看上方错误并重新双击本文件。"
fi
if [[ -t 0 ]]; then
  read "reply?按 Return 关闭窗口…"
fi
exit $result
