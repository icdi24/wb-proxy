#!/usr/bin/env sh
# wb-proxy 启动入口（Linux / macOS）
set -e
cd "$(dirname "$0")/.." || exit 1
if [ "$#" -eq 0 ]; then
  exec python3 scripts/wbctl.py start
fi
exec python3 scripts/wbctl.py "$@"
