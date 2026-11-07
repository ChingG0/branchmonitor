#!/bin/bash
PID=$(lsof -t -iTCP:8787 -sTCP:LISTEN 2>/dev/null)
if [ -n "$PID" ]; then kill $PID && echo "族群看板已停止"; else echo "族群看板沒有在執行"; fi
