"""Chat-operated incremental backup. No app imports, network writes or strategy calls.

The Drive connector runs in the conversation, not in this process. 'prepare'
never means cloud success; 'confirm' requires the connector read-back receipt.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from zoneinfo import ZoneInfo

FORMAT = 'branch-monitor-incremental-v1'
CHUNK = 8 * 1024**2
RESERVE = 8 * 1024**2
DEFAULT_SOURCE = Path(__file__).resolve().parents[1]
DEFAULT_HOME = Path('/Users/ching/Desktop/branch-monitor-google-drive')
BASELINE = '20261107T140242'
BASE_FOLDER = '1pdLe0BKu_eFyV4PY7mm8uD4RxW_eR9tJ'
BASE_RECEIPT_SHA = '2456fcd3c065865ccecb25aa3f44678f6e27c68390dbca75be262476616abc6f'
BASE_FILES_SHA = '0f721acb44e2f1929bad0733ce8936ace43179f53744034060a20731fbd8c2b3'
BASE_DB_SHA = 'f186369f2d54cda5ee26f1ffb2733aa539425c9a7b23288d9aeef14d593edc44'
ROOTS = ('data', 'research', 'prototype', 'config')
EXPORTS = ('evening-forecast.json', 'daily-probability-history.json',
           'forecast-presentation.json', 'prospective-status.json')


def now():
    return datetime.now(ZoneInfo('Asia/Taipei')).isoformat(timespec='microseconds')


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n').encode()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(CHUNK), b''):
            h.update(block)
    return h.hexdigest()


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(encoded(value)); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def safe(relative):
    p = PurePosixPath(relative)
    if not relative or p.is_absolute() or '..' in p.parts or p.as_posix() != relative:
        raise ValueError('Unsafe backup path')
    return relative


def excluded(name):
    n = name.lower()
    return (n in {'.git', '.venv', 'node_modules', '__pycache__', '.pytest_cache',
                  'dist', '.claude', '.ds_store', 'credentials.json', 'token.json'}
            or n == '.env' or n.startswith(('.env.', 'service-account', 'service_account', 'client_secret'))
            or n.endswith(('.key', '.pem', '.p12', '.pyc', '.pyo', '.lock', '.pid', '-wal', '-shm', '-journal')))


def inventory(source):
    result = {}
    def walk(folder):
        if not folder.is_dir():
            raise ValueError('Missing backup root: ' + folder.name)
        for p in sorted(folder.iterdir()):
            if excluded(p.name):
                continue
            if p.is_symlink():
                raise ValueError('Symlink requires explicit review: ' + str(p))
            if p.is_dir():
                walk(p)
            elif p.is_file():
                result[p.relative_to(source).as_posix()] = signature(p)
            else:
                raise ValueError('Unsupported file: ' + str(p))
    for name in ROOTS:
        walk(source / name)
    for name in EXPORTS:
        p = source / 'frontend/public/v6-research' / name
        result[p.relative_to(source).as_posix()] = signature(p)
    return result


def signature(path):
    s = path.stat()
    return (s.st_size, s.st_mtime_ns, s.st_ino, s.st_mode)


def sidecars(source, files):
    result = {}
    for relative in files:
        if Path(relative).suffix.lower() in ('.sqlite3', '.sqlite', '.db'):
            for suffix in ('-wal', '-journal'):
                p = Path(str(source / relative) + suffix)
                result[relative + suffix] = signature(p) if p.exists() else None
    return result


def space(path, required):
    if shutil.disk_usage(path).free < required + RESERVE:
        raise OSError('本機空間不足；保留待備份狀態，不刪原始資料或舊備份')


@contextmanager
def lock(home):
    home.mkdir(parents=True, exist_ok=True)
    with (home / '.backup.lock').open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def queue(model, *, home=DEFAULT_HOME, source=DEFAULT_SOURCE):
    """Only called by successful explicit CLI publications; not by report/GET."""
    if model not in ('v3', 'v6', 'v10', 'v11'):
        raise ValueError('Unknown manual account')
    control = Path(home) / '60-更新後備份'
    event = {'model': model, 'savedAt': now(), 'source': str(Path(source).resolve()),
             'status': 'pending_cloud', 'id': uuid.uuid4().hex}
    atomic(control / 'queue' / (event['id'] + '.json'), event)
    return event


def notify(model):
    """A backup failure must not roll back an already committed publication."""
    try:
        event = queue(model)
        print('雲端備份待處理：' + event['id'], file=sys.stderr)
    except Exception as exc:
        print('策略已保存，但備份排隊失敗：' + str(exc), file=sys.stderr)


class Backup:
    def __init__(self, source=DEFAULT_SOURCE, home=DEFAULT_HOME):
        self.source = Path(source).resolve()
        self.home = Path(home)
        self.control = self.home / '60-更新後備份'
        self.base = self.home / '10-資料庫快照' / BASELINE

    def state(self):
        """Replay only confirmed deltas. Prepared versions never become parents."""
        receipt = self.home / '00-計畫與盤點/包4傳輸/UPLOAD_RECEIPT.json'
        if sha(receipt) != BASE_RECEIPT_SHA:
            raise ValueError('First cloud backup receipt does not match sealed baseline')
        if sha(self.base / 'FILES.jsonl') != BASE_FILES_SHA or sha(self.base / 'DATABASES.json') != BASE_DB_SHA:
            raise ValueError('Baseline metadata differs from sealed cloud backup')
        rows = {}
        with (self.base / 'FILES.jsonl').open() as f:
            for line in f:
                r = json.loads(line); rows[safe(r['path'])] = r
        for r in json.loads((self.base / 'DATABASES.json').read_text()):
            rows[r['path']]['source_sha256'] = r['source_sha256']
        parent = {'kind': 'full', 'id': BASELINE, 'folderId': BASE_FOLDER,
                  'filesSha256': sha(self.base / 'FILES.jsonl'), 'receiptSha256': BASE_RECEIPT_SHA}
        versions = self.control / 'versions'
        for d in sorted(versions.glob('*')) if versions.exists() else []:
            if not (d / 'CONFIRMED.json').exists():
                continue
            m = json.loads((d / 'MANIFEST.json').read_text())
            proof = json.loads((d / 'CONFIRMED.json').read_text())
            if m['parent'] != parent or proof['manifestSha256'] != sha(d / 'MANIFEST.json'):
                raise ValueError('Broken incremental parent chain')
            if proof['receiptSha256'] != sha(d / 'RECEIPT.json'):
                raise ValueError('Changed cloud confirmation receipt')
            for p in m['deleted']:
                rows.pop(safe(p), None)
            for r in m['changed']:
                rows[safe(r['path'])] = r
            parent = {'kind': 'incremental', 'id': m['id'], 'folderId': proof['folderId'],
                      'manifestSha256': proof['manifestSha256']}
        return rows, parent

    def chunk(self, path):
        """Content-addressed objects; only one 8MiB block is materialized at a time."""
        objects = self.control / 'objects'
        objects.mkdir(parents=True, exist_ok=True)
        result = []; total = hashlib.sha256()
        with path.open('rb') as f:
            for data in iter(lambda: f.read(CHUNK), b''):
                total.update(data)
                digest = hashlib.sha256(data).hexdigest()
                target = objects / (digest + '.gz')
                if not target.exists():
                    packed = gzip.compress(data, mtime=0)
                    space(objects, len(packed))
                    fd, tmp = tempfile.mkstemp(prefix='.chunk-', dir=objects)
                    try:
                        with os.fdopen(fd, 'wb') as out:
                            out.write(packed); out.flush(); os.fsync(out.fileno())
                        os.replace(tmp, target)
                    finally:
                        if os.path.exists(tmp): os.unlink(tmp)
                # Revalidate cached objects before reuse; no corrupt resume.
                if hashlib.sha256(gzip.decompress(target.read_bytes())).hexdigest() != digest:
                    raise ValueError('Corrupt cached object')
                result.append({'name': target.name, 'rawBytes': len(data),
                               'bytes': target.stat().st_size, 'sha256': sha(target)})
        return total.hexdigest(), result

    def prepare(self):
        with lock(self.control):
            # Finish an existing prepared upload before starting another parent.
            pending = [d for d in sorted((self.control / 'versions').glob('*'))
                       if (d / 'MANIFEST.json').exists() and (d / 'UPLOAD.json').exists()
                       and not (d / 'CONFIRMED.json').exists()]
            if pending:
                return {'status': 'pending_cloud', 'version': str(pending[0]), 'resumed': True}
            rows, parent = self.state()
            before = inventory(self.source)
            side_start = sidecars(self.source, before)
            queue_ids = sorted(p.stem for p in (self.control / 'queue').glob('*.json'))
            identity = datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y%m%dT%H%M%S%f')
            version = self.control / 'versions' / identity
            version.mkdir(parents=True)
            started = now(); changed = []; total = 0; reused = 0
            try:
                for relative, sig in before.items():
                    path = self.source / relative
                    previous = rows.get(relative)
                    raw_hash = sha(path)
                    if signature(path) != sig:
                        raise ValueError('Source changed while hashing: ' + relative)
                    is_db = path.suffix.lower() in ('.sqlite3', '.sqlite', '.db')
                    side_before = {s: signature(Path(str(path) + s)) for s in ('-wal', '-journal')
                                   if Path(str(path) + s).exists()}
                    active_side = any(s[0] > 0 for s in side_before.values())
                    if (previous and raw_hash == previous.get('source_sha256' if is_db else 'sha256')
                            and not (is_db and active_side)):
                        reused += 1; continue
                    capture = path
                    if is_db:
                        space(version, sig[0])
                        capture = version / 'capture.sqlite3'
                        try:
                            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as src:
                                with sqlite3.connect(capture) as dst:
                                    src.backup(dst)
                                    if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                                        raise ValueError('Database integrity failure: ' + relative)
                            digest, parts = self.chunk(capture)
                        finally:
                            if capture.exists(): capture.unlink()
                    else:
                        digest, parts = self.chunk(capture)
                    if signature(path) != sig or any(signature(Path(str(path) + s)) != v for s, v in side_before.items()):
                        raise ValueError('Source changed during capture: ' + relative)
                    row = {'path': relative, 'bytes': sum(p['rawBytes'] for p in parts),
                           'sha256': digest, 'chunks': parts, 'source_mtime_ns': sig[1],
                           'mode': sig[3] & 0o777, 'kind': 'sqlite_backup' if is_db else 'file'}
                    if is_db: row['source_sha256'] = raw_hash
                    changed.append(row); total += row['bytes']
                # Detect additions, removals and edits during the entire multi-file scan.
                if inventory(self.source) != before or sidecars(self.source, before) != side_start:
                    raise ValueError('Source changed during backup; retry after manual updates finish')
                manifest = {'format': FORMAT, 'id': identity, 'source': str(self.source),
                            'startedAt': started, 'completedCaptureAt': now(), 'parent': parent,
                            'queueIds': queue_ids, 'changed': changed,
                            'deleted': sorted(set(rows) - set(before)), 'sourceFiles': len(before),
                            'unchangedFiles': reused, 'changedRawBytes': total,
                            'consistency': 'per-database SQLite backup; stable source scan, not cross-DB transaction',
                            'codeCommit': self.commit(), 'cloudStatus': 'pending_cloud'}
                atomic(version / 'MANIFEST.json', manifest)
                plan = self.upload_plan(version, manifest)
                # Receipt overhead is reserved; shared Google One free quota is
                # checked by the operator independently of this app budget.
                used = 2_578_983_665 + sum(json.loads(p.read_text())['uploadBytes'] + 100_000
                    for p in (self.control / 'versions').glob('*/CONFIRMED.json'))
                if used + plan['bytes'] + 100_000 > 20_000_000_000:
                    raise OSError('超過20GB備份預算；不刪舊版，先整理保留政策')
                atomic(version / 'UPLOAD.json', plan)
                return {'status': 'pending_cloud', 'version': str(version),
                        'changedFiles': len(changed), 'unchangedFiles': reused,
                        'changedRawBytes': total, 'uploadBytes': json.loads((version / 'UPLOAD.json').read_text())['bytes']}
            except Exception as exc:
                if (version / 'MANIFEST.json').exists() and not (version / 'UPLOAD.json').exists():
                    (version / 'MANIFEST.json').unlink()
                atomic(version / 'FAILED.json', {'failedAt': now(), 'error': str(exc), 'cloudStatus': 'not_uploaded'})
                raise

    def commit(self):
        try:
            return subprocess.check_output(['git', '-C', str(self.source), 'rev-parse', 'HEAD'], text=True,
                                           stderr=subprocess.DEVNULL).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None

    def upload_plan(self, version, manifest):
        known = set()
        for d in (self.control / 'versions').glob('*'):
            if (d / 'CONFIRMED.json').exists():
                receipt = json.loads((d / 'RECEIPT.json').read_text())
                known.update(x['sha256'] for x in receipt['files'])
        items = {}
        for r in manifest['changed']:
            for p in r['chunks']:
                if p['sha256'] not in known:
                    items[p['name']] = {**p, 'path': str(self.control / 'objects' / p['name'])}
        p = version / 'MANIFEST.json'
        items[p.name] = {'name': p.name, 'path': str(p), 'bytes': p.stat().st_size, 'sha256': sha(p)}
        return {'files': list(items.values()), 'bytes': sum(x['bytes'] for x in items.values()),
                'status': 'pending_cloud', 'readBackRequired': True}

    def confirm(self, version, receipt_path):
        """Operator attestation from actual Drive GET/SHA readback, not an uploader."""
        version = Path(version).resolve()
        if version.parent != (self.control / 'versions').resolve():
            raise ValueError('Version outside backup store')
        with lock(self.control):
            if (version / 'CONFIRMED.json').exists():
                return json.loads((version / 'CONFIRMED.json').read_text())
            receipt = json.loads(Path(receipt_path).read_text())
            plan = json.loads((version / 'UPLOAD.json').read_text())
            manifest = json.loads((version / 'MANIFEST.json').read_text())
            _, parent = self.state()
            if parent != manifest['parent']:
                raise ValueError('Parent changed; do not confirm stale branch')
            files = receipt.get('files', [])
            if len(files) != len(plan['files']) or len({x['name'] for x in files}) != len(files):
                raise ValueError('Incomplete or duplicate cloud receipt')
            by = {x['name']: x for x in files}
            if not receipt.get('folderId') or not receipt.get('ownerOnly') or not receipt.get('verifiedAt'):
                raise ValueError('Missing private folder / readback attestation')
            for item in plan['files']:
                r = by.get(item['name'], {})
                if (r.get('sha256') != item['sha256'] or r.get('bytes') != item['bytes']
                        or not r.get('fileId') or r.get('readBackSha256') != item['sha256']
                        or sha(item['path']) != item['sha256']):
                    raise ValueError('Cloud readback mismatch: ' + item['name'])
            atomic(version / 'RECEIPT.json', receipt)
            result = {'status': 'cloud_verified', 'confirmedAt': now(), 'folderId': receipt['folderId'],
                      'manifestSha256': sha(version / 'MANIFEST.json'),
                      'receiptSha256': sha(version / 'RECEIPT.json'), 'uploadBytes': plan['bytes']}
            # This marker is written LAST. Failure before it leaves queue pending.
            atomic(version / 'CONFIRMED.json', result)
            return result

    def status(self):
        versions = []
        acknowledged = set()
        for d in sorted((self.control / 'versions').glob('*')):
            if (d / 'CONFIRMED.json').exists():
                m = json.loads((d / 'MANIFEST.json').read_text())
                acknowledged.update(m['queueIds'])
                versions.append({'id': d.name, **json.loads((d / 'CONFIRMED.json').read_text())})
            elif (d / 'MANIFEST.json').exists() and (d / 'UPLOAD.json').exists(): versions.append({'id': d.name, 'status': 'pending_cloud'})
            else: versions.append({'id': d.name, 'status': 'capture_failed'})
        pending = [p.stem for p in (self.control / 'queue').glob('*.json') if p.stem not in acknowledged]
        return {'versions': versions, 'pendingManualUpdates': len(pending),
                'budgetBytes': 20_000_000_000, 'deletionEnabled': False,
                'retention': '保留完整基底及所有依賴鏈；包6還原通過前不刪任何版本',
                'connectorRequired': True, 'freeLocalBytes': shutil.disk_usage(self.home).free}


def main():
    p = argparse.ArgumentParser(description='手動更新後增量備份；不發布策略、不自動登入Drive')
    p.add_argument('action', choices=['prepare', 'status', 'confirm'])
    p.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    p.add_argument('--home', type=Path, default=DEFAULT_HOME)
    p.add_argument('--version', type=Path); p.add_argument('--receipt', type=Path)
    a = p.parse_args(); b = Backup(a.source, a.home)
    if a.action == 'prepare': result = b.prepare()
    elif a.action == 'status': result = b.status()
    else:
        if not a.version or not a.receipt: p.error('confirm requires --version and --receipt')
        result = b.confirm(a.version, a.receipt)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
