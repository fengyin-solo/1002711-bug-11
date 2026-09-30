#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# 复用已有虚拟环境；若解释器链接失效（比如仓库在别的机器上建的 .venv）则重建
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c "import sys" >/dev/null 2>&1; then
  rm -rf .venv
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
