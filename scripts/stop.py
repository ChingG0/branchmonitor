import json
import os
from pathlib import Path
import signal
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
try:
    with urllib.request.urlopen('http://127.0.0.1:8765/api/health',timeout=2) as response:
        info=json.load(response)
except OSError:
    print('分點觀測站目前沒有啟動。')
else:
    if info.get('app')!='branch-monitor' or info.get('directory')!=str(ROOT) or not isinstance(info.get('pid'),int) or info['pid']<=1:
        raise SystemExit('此連接埠不是這個資料夾的監控程序，未停止任何程序。')
    os.kill(info['pid'],signal.SIGTERM)
    print('已停止本機監控。資料保留，下次開啟會回補所選期間。')
