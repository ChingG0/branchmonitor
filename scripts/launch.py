"""Double-click launcher. Uses only this directory, regardless of where it is moved."""
import argparse
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


def main(argv=None):
    global URL
    parser=argparse.ArgumentParser(description='啟動指定資料夾的本機服務')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--browser-host',choices=['localhost','127.0.0.1'],default='127.0.0.1')
    parser.add_argument('--no-scheduler',action='store_true')
    args=parser.parse_args(argv)
    if not 1<=args.port<=65535:parser.error('port必須介於1與65535')
    URL=f'http://{args.browser_host}:{args.port}'
    if (ROOT/'cloud-runtime.local.json').is_file() and os.environ.get('BRANCH_MONITOR_CLOUD')!='1':
        os.execv(sys.executable,[sys.executable,str(ROOT/'scripts/cloud_runtime.py'),'launch'])
    if sys.version_info<(3,10):
        raise SystemExit('需要 Python 3.10 或更新版本。')
    existing=health()
    if existing and existing.get('app')=='branch-monitor':
        if existing.get('directory')!=str(ROOT):
            raise SystemExit(f'{URL} 正由另一份Branch Monitor使用，未開啟其他資料夾。')
        webbrowser.open(URL)
        print('分點觀測站已在運行：'+URL)
        return
    if any(importlib.util.find_spec(name) is None for name in ['requests','bs4','numpy','xlrd']):
        python=ROOT/'.venv/bin/python'
        if not python.exists():
            subprocess.run([sys.executable,'-m','venv',str(ROOT/'.venv')],check=True)
        subprocess.run([str(python),'-m','pip','install','-r',str(ROOT/'requirements.txt')],check=True)
        os.execv(str(python),[str(python),str(Path(__file__).resolve()),'--port',str(args.port),'--browser-host',args.browser_host]+(['--no-scheduler'] if args.no_scheduler else []))
    if not (ROOT/'dist/index.html').exists():
        print('正在建立前端，需要已安裝 Node.js。')
        subprocess.run(['npm','ci'],check=True)
        subprocess.run(['npm','run','build'],check=True)
    (ROOT/'data').mkdir(exist_ok=True)
    with (ROOT/'data/server.log').open('a') as log:
        process=subprocess.Popen([sys.executable,'-m','backend.server','--port',str(args.port)]+(['--no-scheduler'] if args.no_scheduler else []),cwd=ROOT,
            stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    for _ in range(50):
        if process.poll() is not None:
            raise SystemExit(f'啟動失敗，請查看 data/server.log。常見原因是 Python 執行架構或套件不相容；若 {args.port} 已占用，紀錄會明確顯示 Address already in use。')
        state=health()
        if state and state.get('app')=='branch-monitor' and state.get('directory')==str(ROOT):
            webbrowser.open(URL)
            print('分點觀測站已啟動：'+URL+'\n關閉瀏覽器不會停止背景更新；雙擊「停止監控.command」可結束服務。')
            return
        time.sleep(.3)
    raise SystemExit('啟動時間較長，請查看 data/server.log。')


if __name__=='__main__':main()
