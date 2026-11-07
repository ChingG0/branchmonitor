#!/bin/bash
cd "$(dirname "$0")" || exit 1
python3 -B scripts/stop.py --port 3001 --browser-host localhost
