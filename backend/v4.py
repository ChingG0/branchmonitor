"""V4 paper challenge: V1 signals from 2026-10-16 with NT$100k and 1.5x entry cap."""
import hashlib
import sqlite3
import threading
from pathlib import Path

import numpy as np
import pandas as pd

from . import db, v2

VERSION = 'v4-v1-100k-leverage15-20261016'
START = '2026-10-16'
PREVIEW_DATE = '2026-10-15'
INITIAL = 100_000.0
MAX_LEVERAGE = 1.5
# Explicit scenario assumption, not a quotation of a brokerage credit rate.
ANNUAL_INTEREST = .06
BUY = .002855
SELL = .005855
_lock = threading.Lock()
_cache = {}
_watch = {}


def empty(message):
    return dict(key='v4', label='V4 百萬挑戰', available=False,
                error=message, curve=[], days={}, trades=[], open_positions=[],
                start=START, as_of=None, initial=INITIAL, cash=INITIAL, equity=INITIAL,
                borrowed=0., financing_cost=0., gross_exposure=0., actual_leverage=0.,
                max_leverage=MAX_LEVERAGE, annual_interest=ANNUAL_INTEREST,
                portfolio_return_pct=None, unlevered_return_pct=None,
                top_realized=[], top_unrealized=[], prestart_preview=None)


def _attach_planned_shares(raw, snapshots):
    """Preview next-open capacity at the known close; never place a trade."""
    symbols = {row['symbol'] for snap in snapshots.values()
               for row in snap['candidates']}
    if not symbols:
        return
    rows = raw.loc[raw.date.isin(snapshots) & raw.symbol.isin(symbols),
                   ['date', 'symbol', 'volume']]
    volumes = {(day, symbol): volume for day, symbol, volume
               in rows.itertuples(index=False, name=None)}
    for day, snap in snapshots.items():
        held = {row['symbol'] for row in snap['holdings']}
        slots = max(0, 3 - len(held))
        equity, cash = snap['equity'], snap['cash']
        gross = snap['gross_exposure']
        for row in snap['candidates']:
            row['planned_shares'] = 0
            if row['symbol'] in held or slots == 0:
                continue
            price = row['close']
            volume = volumes.get((day, row['symbol']))
            if pd.isna(price) or price <= 0 or pd.isna(volume) or volume <= 0:
                continue
            budget = max(0, min(equity * MAX_LEVERAGE / 3,
                                equity * MAX_LEVERAGE - gross,
                                cash + equity * (MAX_LEVERAGE - 1)))
            shares = min(int(budget / (price * (1 + BUY))), int(volume * .01))
            if shares <= 0:
                continue
            row['planned_shares'] = shares
            cash -= shares * price * (1 + BUY)
            gross += shares * price
            slots -= 1


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
    result = v2.calculate(raw, flows, rs_rows, cutoff, profit_protection=True,
                          initial=INITIAL, max_leverage=MAX_LEVERAGE,
                          annual_interest=ANNUAL_INTEREST, start_date=START)
    if not result['available']:
        pending = empty('2026-10-16 起跑日收盤行情尚未完成；目前空手持有十萬元。')
        # Preview exactly the prior close. It never becomes a trade or changes
        # the V4 start date, even when the prior market filter allows entries.
        if (cutoff is None or cutoff >= PREVIEW_DATE) and not raw.loc[
                (raw.date == PREVIEW_DATE) & (raw.symbol == 'TAIEX') & raw.close.notna()].empty:
            preview = v2.calculate(raw, flows, rs_rows, PREVIEW_DATE,
                                   profit_protection=True, initial=INITIAL,
                                   start_date=PREVIEW_DATE)
            if preview['available']:
                snapshot = preview['days'][PREVIEW_DATE]
                _attach_planned_shares(raw, {PREVIEW_DATE: snapshot})
                pending['prestart_preview'] = dict(
                    signal_date=PREVIEW_DATE, intended_date=START,
                    blocked=snapshot['blocked'],
                    candidates=[dict(row, status='大盤暫停買進' if snapshot['blocked']
                                     else '起跑前觀察') for row in snapshot['candidates']])
        return pending
    baseline = v2.calculate(raw, flows, rs_rows, cutoff, profit_protection=True,
                            initial=INITIAL, start_date=START)
    _attach_planned_shares(raw, result['days'])
    result.update(key='v4', label='V4 百萬挑戰', version=VERSION,
                  unlevered_curve=baseline['curve'],
                  unlevered_return_pct=baseline['portfolio_return_pct'],
                  description='從 2026-10-16 空手起跑，複製 V1 的選股與 3ATR／8% 初始防線、+8% 後 2ATR 保護；最多三檔，買進時總曝險與借款分別不超過淨值的 1.5 倍與 0.5 倍。',
                  research_note='這是十萬元模擬挑戰；借款年息 6% 是試算假設，不代表券商報價。借款日按日曆天數計息，總報酬已扣利息及買賣成本。槓桿上限控制新增買入時的曝險，持有後股價波動可能使即時比率高於 1.5 倍；未模擬券商個股融資資格、追繳及強制處分。歷史資料修復仍可能改變模擬結果。')
    return result
