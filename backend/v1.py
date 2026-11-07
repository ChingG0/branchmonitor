"""V1: V2 entries with pre-session 2ATR protection after +8% peak close."""
import math
import hashlib
import json
import sqlite3
import threading
from pathlib import Path

import numpy as np
import pandas as pd

from . import db, v2

VERSION = 'v1-strength-profit8-trail2-20261015'
INITIAL = 1_000_000.0
BUY = .002855
SELL = .005855
_lock = threading.Lock()
_cache = {}
_watch = {}


def empty(message):
    return dict(key='v1', label='V1 獲利保護優化', available=False,
                error=message, curve=[], days={}, trades=[], open_positions=[],
                portfolio_return_pct=None, top_realized=[], top_unrealized=[])


def report(path=None, cutoff=None):
    path = Path(path or db.DATA / 'monitor.sqlite3')
    if not path.exists():
        return empty('尚無本機行情')
    with _lock:
        identity = (str(path.resolve()), path.stat().st_ino)
        if identity not in _watch:
            for watcher in _watch.values():
                watcher.close()
            _watch.clear()
            _watch[identity] = sqlite3.connect(f'file:{path}?mode=ro', uri=True, check_same_thread=False)
            _cache.clear()
        revision = _watch[identity].execute('PRAGMA data_version').fetchone()[0]
        revision_key = ('revision', identity, cutoff)
        prior = _cache.get(revision_key)
        if prior and prior[0] == revision:
            return prior[1]
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as conn:
            conn.execute('BEGIN')
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'prices', 'flows', 'rs_days'} <= tables:
                return empty('請先更新行情、分點與個股 RS')
            rs_rows = conn.execute('SELECT date,payload FROM rs_days ORDER BY date').fetchall()
            raw = pd.read_sql_query('SELECT date,symbol,name,open,high,low,close,volume FROM prices ORDER BY date,symbol', conn)
            flows = pd.read_sql_query('SELECT date,symbol,sum(net) net FROM flows GROUP BY date,symbol ORDER BY date,symbol', conn)
        # Unrelated report/cost writes must not rerun the model. Fingerprint
        # relevant rows, including offsetting same-day corrections, after commits.
        digest = hashlib.sha256(repr(rs_rows).encode())
        for frame in (raw, flows):
            digest.update(pd.util.hash_pandas_object(frame, index=False).values.tobytes())
        key = (VERSION, identity, cutoff, digest.hexdigest())
        value = _cache[key] if key in _cache else calculate(raw, flows, rs_rows, cutoff)
        # Bound memory and cache no source writes; selecting dates reuses it.
        _cache.clear()
        _cache[key] = value
        _cache[revision_key] = (revision, value)
        return value


def calculate(raw, flows, rs_rows, cutoff=None):
    result = v2.calculate(raw, flows, rs_rows, cutoff, profit_protection=True)
    result.update(key='v1', label='V1 獲利保護優化', version=VERSION,
                  description='沿用 V2 三檔強度重排與初始 3ATR／8% 防線；最高收盤較買價達 +8% 後，下一交易日起改用 2ATR 追蹤。',
                  research_note='研究版：選定時（行情至 2026/10/14）全期僅 21 筆完成；9/1 重新起跑僅 2 筆完成、未見優於 V2。不可視為已證實勝率穩定超過 50%。未設固定停利，跳空及成本仍可能讓已啟動保護的部位虧損。')
    result['validation'] = json.loads((Path(__file__).with_name('v1_validation.json')).read_text())
    return result
