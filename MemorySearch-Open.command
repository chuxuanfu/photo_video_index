#!/bin/zsh
set -eu
readonly app_root="${0:A:h}"
exec /usr/bin/python3 "$app_root/memorysearch_local/control.py" open "$@"
