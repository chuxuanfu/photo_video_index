#!/bin/zsh
set -euo pipefail
app_root=${0:A:h}
previous="$app_root/runtime/Return-to-Previous.command"
if [[ ! -x "$previous" ]]; then
  print '尚无已核实的本机回退记录；没有停止服务或改动索引。'
  exit 2
fi
exec "$previous" "$@"
