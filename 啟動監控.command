#!/bin/bash
cd "$(dirname "$0")" || exit 1
if [ -x ".venv/bin/python" ]; then
  /usr/bin/arch -arm64 .venv/bin/python scripts/launch.py
else
  /usr/bin/arch -arm64 /usr/bin/python3 scripts/launch.py
fi
if [ "$?" -ne 0 ]; then
  read -r -p "按 Enter 關閉視窗…"
fi
