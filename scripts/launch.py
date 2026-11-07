"""Double-click launcher. Uses only this directory, regardless of where it is moved."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

ROOT=Path(__file__).resolve().parents[1]
URL='http://127.0.0.1:8765'
os.chdir(ROOT)


def health():
    try:
        with urllib.request.urlopen(URL+'/api/health',timeout=1) as response:
            return json.load(response)
    except (OSError,ValueError,urllib.error.URLError):
        return None


def main():
    if sys.version_info<(3,10):
        raise SystemExit('需要 Python 3.10 或更新版本。')
    existing=health()
    if existing and existing.get('app')=='branch-monitor':
        webbrowser.open(URL)
        print('分點觀測站已在運行：'+URL)
        return
    if any(importlib.util.find_spec(name) is None for name in ['requests','bs4','numpy','xlrd']):
        python=ROOT/'.venv/bin/python'
        if not python.exists():
            subprocess.run([sys.executable,'-m','venv',str(ROOT/'.venv')],check=True)
        subprocess.run([str(python),'-m','pip','install','-r',str(ROOT/'requirements.txt')],check=True)
        os.execv(str(python),[str(python),str(Path(__file__).resolve())])
    if not (ROOT/'dist/index.html').exists():
        print('正在建立前端，需要已安裝 Node.js。')
        subprocess.run(['npm','ci'],check=True)
        subprocess.run(['npm','run','build'],check=True)
    (ROOT/'data').mkdir(exist_ok=True)
    with (ROOT/'data/server.log').open('a') as log:
        process=subprocess.Popen([sys.executable,'-m','backend.server'],cwd=ROOT,
            stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    for _ in range(50):
        if process.poll() is not None:
            raise SystemExit('啟動失敗，請查看 data/server.log。常見原因是 Python 執行架構或套件不相容；若 8765 已占用，紀錄會明確顯示 Address already in use。')
        state=health()
        if state and state.get('app')=='branch-monitor':
            webbrowser.open(URL)
            print('分點觀測站已啟動：'+URL+'\n關閉瀏覽器不會停止背景更新；雙擊「停止監控.command」可結束服務。')
            return
        time.sleep(.3)
    raise SystemExit('啟動時間較長，請查看 data/server.log。')


if __name__=='__main__':main()
