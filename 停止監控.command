#!/bin/bash
cd "$(dirname "$0")" || exit 1
if [ -f "cloud-runtime.local.json" ]; then
  python3 scripts/stop.py --port 3001 --browser-host localhost
else
  python3 scripts/stop.py
fi
