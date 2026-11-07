#!/bin/bash
cd "$(dirname "$0")" || exit 1
if [ -x ".venv/bin/python" ]; then
  .venv/bin/python -B scripts/cloud_runtime.py launch
else
  python3 -B scripts/cloud_runtime.py launch
fi
if [ "$?" -ne 0 ]; then read -r -p "啟動未完成，按Enter關閉…"; fi
