#!/bin/zsh
set -euo pipefail
app_root=${0:A:h}
exec /usr/bin/python3 "$app_root/memorysearch_local/verify_limits.py" "$@"
