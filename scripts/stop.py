"""Stop only this checkout's server at the explicitly selected port."""
import argparse
import json
import os
from pathlib import Path
import signal
import urllib.request

ROOT=Path(__file__).resolve().parents[1]


def main(argv=None):
    parser=argparse.ArgumentParser(description='停止指定資料夾的本機服務')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--browser-host',choices=['localhost','127.0.0.1'],default='127.0.0.1')
    args=parser.parse_args(argv)
    if not 1<=args.port<=65535:parser.error('port必須介於1與65535')
    url=f'http://{args.browser_host}:{args.port}'
    try:
        with urllib.request.urlopen(url+'/api/health',timeout=2) as response:
            info=json.load(response)
    except (OSError,ValueError):
        print(f'{url} 分點觀測站目前沒有啟動。')
        return
    if info.get('app')!='branch-monitor' or info.get('directory')!=str(ROOT) or not isinstance(info.get('pid'),int) or isinstance(info['pid'],bool) or info['pid']<=1:
        raise SystemExit('此連接埠不是這個資料夾的監控程序，未停止任何程序。')
    os.kill(info['pid'],signal.SIGTERM)
    print(f'已停止 {url} 的監控。資料保留。')


if __name__=='__main__':main()
