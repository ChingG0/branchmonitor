"""原型看板的本機伺服器：啟動時自動更新，另提供手動更新 API。

只綁 127.0.0.1，與正式服務的 8765 無關，也不會寫入正式資料庫。
"""
from __future__ import annotations

import json
import mimetypes
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import sectors
import update

ROOT = Path(__file__).resolve().parent
PORT = 8787
_lock = threading.Lock()
_job = {'status': 'idle', 'message': '尚未更新', 'started_at': None,
        'finished_at': None, 'steps': [], 'errors': [], 'target': None}


def job_snapshot():
    with sectors.store() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key='last_update'").fetchone()
        dates = [r['date'] for r in conn.execute('SELECT DISTINCT date FROM sector_days ORDER BY date')]
        institutional = [r['date'] for r in conn.execute(
            'SELECT date FROM institutional_days ORDER BY date DESC LIMIT 1')]
    try:
        available = update.latest_local()
    except Exception:
        available = None
    state = dict(_job)
    state.update(last_update=row['value'] if row else None,
                 computed=dates[-1] if dates else None,
                 institutional=institutional[0] if institutional else None,
                 available=available,
                 behind=bool(available and dates and dates[-1] != available))
    return state


def run_update(force=False):
    """同一時間只允許一個更新工作。"""
    if not _lock.acquire(blocking=False):
        return False
    def worker():
        _job.update(status='running', message='更新中…', started_at=datetime.now().isoformat(timespec='seconds'),
                    finished_at=None, steps=[], errors=[])
        try:
            summary = update.run(force=force, log=lambda line: print(line, flush=True))
            _job.update(status='complete' if summary['ok'] else 'partial',
                        message=('已更新到 ' + summary['target']) if summary['ok']
                                else f"更新完成，但有 {len(summary['errors'])} 項缺漏",
                        steps=summary['steps'], errors=summary['errors'],
                        target=summary['target'], finished_at=summary['finished_at'])
        except Exception as error:
            _job.update(status='failed', message=f'更新失敗：{type(error).__name__}',
                        errors=[str(error)[:220]], finished_at=datetime.now().isoformat(timespec='seconds'))
        finally:
            _lock.release()
    threading.Thread(target=worker, daemon=True).start()
    return True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if len(args) < 2 or str(args[1]) not in ('200', '304'):
            super().log_message(fmt, *args)

    def local_request(self):
        return urlparse('//' + self.headers.get('Host', '')).hostname in ('127.0.0.1', 'localhost', '::1')

    def send(self, body, status=200, content_type='application/json; charset=utf-8'):
        raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if not self.local_request():
            return self.send({'error': '僅接受本機請求'}, 403)
        path = urlparse(self.path).path
        if path == '/api/status':
            return self.send(job_snapshot())
        try:
            target = (ROOT / (path.lstrip('/') or 'board.html')).resolve()
            if not target.is_relative_to(ROOT) or not target.is_file():
                return self.send({'error': '找不到檔案'}, 404)
            return self.send(target.read_bytes(),
                             content_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            self.send({'error': f'本機處理失敗：{type(error).__name__}'}, 500)

    def do_POST(self):
        if not self.local_request():
            return self.send({'error': '僅接受本機請求'}, 403)
        origin = self.headers.get('Origin')
        if origin and urlparse(origin).hostname not in ('127.0.0.1', 'localhost'):
            return self.send({'error': '不接受外部網站寫入'}, 403)
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.send({'error': '需要 application/json'}, 415)
        length = int(self.headers.get('Content-Length', 0))
        if not 0 <= length <= 4096:
            return self.send({'error': '請求大小不符'}, 400)
        try:
            values = json.loads(self.rfile.read(length) or b'{}')
        except json.JSONDecodeError:
            return self.send({'error': '請求需為 JSON'}, 400)
        if urlparse(self.path).path != '/api/refresh':
            return self.send({'error': '找不到 API'}, 404)
        if not run_update(bool(values.get('force'))):
            return self.send(dict(job_snapshot(), message='已有更新在進行中'), 409)
        return self.send(job_snapshot(), 202)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='原型看板伺服器')
    parser.add_argument('--port', type=int, default=PORT)
    parser.add_argument('--no-startup-update', action='store_true', help='啟動時不自動更新')
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'族群廣度原型 http://127.0.0.1:{args.port}/board.html  (Ctrl+C 停止)', flush=True)
    if not args.no_startup_update:
        print('啟動更新中…（網頁可先開，完成後會自動重新載入）', flush=True)
        run_update()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
        print('\n已停止')


if __name__ == '__main__':
    main()
