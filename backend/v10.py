"""V10: chat-published, immutable V1 snapshots. Reads never run the strategy.

This copies V1's historical simulation, including its current positions, rather
than creating retrospective manual trades. Only the explicit CLI saves a new
snapshot. Frontend refresh and scheduled jobs have no publication route.
"""
from __future__ import annotations
import argparse
import copy
from datetime import date, datetime, time
import fcntl
import json
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo
from . import db, v1

VERSION = 'v10-manual-v1-20261022'
LABEL = 'V10 手動 V1'
TZ = ZoneInfo('Asia/Taipei')
POLICY = '僅在對話要求更新時保存；與 V3、V6 一起手動更新。前台更新、切換日期及重新整理只讀取已保存紀錄。'
METHOD = '沿用 V1 的選股、100 萬本金、三檔配置及停損規則。這是按需保存的 V1 策略複本，歷史績效屬規則重播；人工研究附在快照，不改變策略分數或交易規則。'


def _store(store=None):
    return Path(store) if store is not None else db.DATA / 'v10_manual'


def _snapshots(store):
    if not store.exists():
        return []
    rows = []
    for path in sorted(store.glob('[0-9]*.json'), reverse=True):
        row = json.loads(path.read_text())
        if row.get('version') != VERSION or not isinstance(row.get('model'), dict):
            raise ValueError('V10 快照版本不符，保留原紀錄')
        rows.append(row)
    return rows


def report(store=None):
    rows = _snapshots(_store(store))
    if not rows:
        result = v1.empty('尚未建立 V10 手動快照')
        result.update(key='v10', label=LABEL, version=VERSION, mode='manual',
                      revision=0, as_of=None, update_policy=POLICY, manual_method=METHOD)
        return result
    latest = rows[0]
    result = copy.deepcopy(latest['model'])
    result.update(key='v10', label=LABEL, version=VERSION, mode='manual',
                  revision=latest['revision'], recorded_at=latest['recorded_at'],
                  source_version=latest['source_version'], update_policy=POLICY,
                  manual_method=METHOD, review=latest['review'],
                  snapshot_history=[{k:r[k] for k in ('revision','recorded_at','as_of')} for r in rows])
    return result


def publish(decision, *, source=None, store=None, now=None):
    """Explicitly freeze V1 through the requested close; never backdate publication."""
    if not isinstance(decision, dict):
        raise ValueError('V10 更新需提供研究紀錄')
    day = decision.get('as_of')
    if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
        raise ValueError('V10 日期格式必須為 YYYY-MM-DD')
    stamp = now or datetime.now(TZ)
    if stamp.tzinfo is None:
        raise ValueError('V10 保存時間需含時區')
    stamp = stamp.astimezone(TZ)
    if day > stamp.date().isoformat() or (day == stamp.date().isoformat() and stamp.time() < time(16)):
        raise ValueError('V10 只能保存已完成且可取得的收盤資料')
    revision = decision.get('expected_revision')
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError('V10 必須提供有效版號')
    analysis = decision.get('analysis')
    if not isinstance(analysis, str) or len(analysis.strip()) < 10:
        raise ValueError('V10 更新請附上研究與資料說明')
    sources = decision.get('sources', [])
    if not isinstance(sources, list) or any(not isinstance(x, dict) or not isinstance(x.get('title'), str) or not isinstance(x.get('url'), str) or not x['url'].startswith('https://') for x in sources):
        raise ValueError('V10 來源需附標題與 HTTPS 網址')
    model = v1.report(source, cutoff=day)
    if not model.get('available') or model.get('as_of') != day:
        raise ValueError('V10 所選日的 V1 收盤資料尚未完成')
    if model.get('version') != v1.VERSION:
        raise ValueError('V1 策略版本不符')
    if any(d > day for d in model.get('days', {})) or any(p['date'] > day for p in model.get('curve', [])):
        raise ValueError('V10 快照含所選日以後資料')
    # Copy before serialization so the V1 cached report remains unchanged.
    frozen = copy.deepcopy(model)
    target = _store(store);target.mkdir(parents=True, exist_ok=True)
    with (target / '.publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        rows = _snapshots(target)
        current = rows[0] if rows else None
        if revision != (current['revision'] if current else 0):
            raise ValueError('V10 版號已變更，請重新讀取後更新')
        if current and (day < current['as_of'] or stamp.isoformat() < current['recorded_at']):
            raise ValueError('V10 不可倒退資料日期或保存時間')
        row = dict(version=VERSION, revision=revision + 1, as_of=day,
                   recorded_at=stamp.isoformat(timespec='seconds'), source_version=v1.VERSION,
                   review=dict(analysis=analysis.strip(), sources=copy.deepcopy(sources)), model=frozen)
        # Validate JSON fully before opening the output; old snapshots stay intact.
        encoded = json.dumps(row, ensure_ascii=False, allow_nan=False)
        path = target / f"{revision + 1:06d}-{stamp.strftime('%Y%m%dT%H%M%S')}.json"
        fd, temp = tempfile.mkstemp(prefix='.snapshot-', suffix='.tmp', dir=target)
        try:
            with os.fdopen(fd, 'w') as handle:
                handle.write(encoded);handle.flush();os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):os.unlink(temp)
    return report(target)


def main():
    parser = argparse.ArgumentParser(description='V10 與 V3、V6 依對話要求一同手動更新')
    parser.add_argument('command', choices=['report','publish'])
    parser.add_argument('--decision', type=Path)
    parser.add_argument('--source');parser.add_argument('--store', type=Path)
    args = parser.parse_args()
    if args.command == 'publish':
        if not args.decision:parser.error('publish 必須提供 --decision')
        result = publish(json.loads(args.decision.read_text()), source=args.source, store=args.store)
        if not args.store and not args.source:
            from .manual_backup import notify
            notify('v10')
    else:result = report(args.store)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
