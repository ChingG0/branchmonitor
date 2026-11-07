"""V8 frozen research rules, computed only from local closing snapshots.

This is a funded simulation, not an order service. Historical source corrections
invalidate the cache. No other strategy's saved lists are changed.
"""
import hashlib
import json
import sqlite3
import threading
from pathlib import Path

import numpy as np
import pandas as pd

from . import db, price_levels

VERSION = 'v8-quiet-atr-5-1'
INITIAL = 1_000_000.0
BUY = .002855
SELL = .005855
_lock = threading.Lock()
_cache = {}
_watch = {}


def empty(message):
    return dict(key='v8', label='V8 吸籌壓縮突破', available=False,
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
    if cutoff:
        raw = raw[raw.date <= cutoff]
        flows = flows[flows.date <= cutoff]
    days = sorted(raw.loc[(raw.symbol == 'TAIEX') & raw.close.notna(), 'date'].unique())
    symbols = sorted(raw.symbol.unique())
    if not days or 'TPEX' not in symbols:
        return empty('加權或櫃買行情不足')
    names = dict(zip(raw.symbol, raw.name))
    si = {s: i for i, s in enumerate(symbols)}

    def panel(frame, field):
        return frame.pivot(index='date', columns='symbol', values=field).reindex(index=days, columns=symbols).astype(float)

    frames = {f: panel(raw, f) for f in ['open', 'high', 'low', 'close', 'volume']}
    p = {f: frame.to_numpy() for f, frame in frames.items()}
    c, vol = frames['close'], frames['volume']
    ma20 = c.rolling(20, min_periods=20).mean().to_numpy()
    turnover = (c * vol).rolling(20, min_periods=18).mean().to_numpy()
    tr = np.maximum(p['high'] - p['low'], np.maximum(abs(p['high'] - c.shift().to_numpy()), abs(p['low'] - c.shift().to_numpy())))
    atr = pd.DataFrame(tr).rolling(14, min_periods=14).mean().to_numpy()
    rs = {mode: np.zeros(c.shape, dtype=bool) for mode in ['20', '60', 'ytd']}
    di = {d: i for i, d in enumerate(days)}
    for day, payload in rs_rows:
        if day not in di:
            continue
        for mode, rows in json.loads(payload).get('groups', {}).items():
            if mode not in rs:
                continue
            for row in rows:
                val = row.get('rs')
                if row.get('symbol') in si and isinstance(val, (int, float)) and np.isfinite(val):
                    rs[mode][di[day], si[row['symbol']]] = True
    plain = np.array([len(s) == 4 and s.isdigit() and not s.startswith('0') for s in symbols])
    eligible = plain[None, :] & (turnover >= 20e6) & (p['close'] >= 10)
    for mask in rs.values():
        eligible &= mask
    ret = c.pct_change(fill_method=None)
    market = ret['TAIEX']
    beta = ret.rolling(60, min_periods=40).cov(market).div(market.rolling(60, min_periods=40).var(), axis=0).clip(-1, 3).shift(1)
    excess = ret - beta.mul(market, axis=0)
    res20 = excess.rolling(20, min_periods=18).sum().to_numpy()
    noise = excess.rolling(20, min_periods=18).std().to_numpy()
    z5 = excess.rolling(5, min_periods=5).sum().to_numpy() / np.maximum(noise * np.sqrt(5), 1e-8)
    net = panel(flows, 'net')
    pressure = (net.rolling(20, min_periods=15).sum() * 1000 / vol.rolling(20, min_periods=18).sum()).to_numpy()
    consistency = net.gt(0).where(net.notna()).rolling(20, min_periods=15).mean().to_numpy()
    compression = (frames['high'].rolling(10).max() - frames['low'].rolling(10).min()) / c
    squeeze = (compression / compression.rolling(40, min_periods=25).median()).shift(1).to_numpy()
    distance = p['close'] / c.rolling(20, min_periods=20).max().shift(1).to_numpy() - 1
    bias = p['close'] / ma20 - 1

    def rank(x):
        return pd.DataFrame(x).where(eligible).rank(axis=1, pct=True).to_numpy()

    score = .4 * rank(pressure) + .3 * rank(consistency) + .3 * rank(z5)
    mask = eligible & (res20 > 0) & (p['close'] > ma20) & (pressure > 0) & (squeeze <= 1) & (distance >= -.02) & (consistency >= .55) & (net.to_numpy() > 0) & (bias <= .20)
    ranks = []
    for t in range(len(days)):
        ids = np.flatnonzero(mask[t] & np.isfinite(score[t]))
        ranks.append(ids[np.argsort(-score[t, ids], kind='stable')][:5].tolist())
    ta, tp = si['TAIEX'], si['TPEX']
    block = (p['close'][:, ta] < ma20[:, ta]) & (p['close'][:, tp] < ma20[:, tp])
    starts = [i for i, day in enumerate(days) if day >= '2026-05-01']
    if not starts:
        return empty('尚無 2026/5 起的行情')
    a = starts[0]
    cash, positions, trades, entries, curve, snapshots = INITIAL, [], [], [], [], {}

    def fillable(t, s):
        o, h, l, v = (p[f][t, s] for f in ['open', 'high', 'low', 'volume'])
        return np.isfinite(o) and o > 0 and np.isfinite(h) and h > l and np.isfinite(v) and v > 0

    def position_row(pos, t):
        value = pos['qty'] * pos['last'] * (1 - SELL)
        return dict(symbol=symbols[pos['s']], name=names[symbols[pos['s']]],
                    signal_date=days[pos['entry'] - 1], entry_date=days[pos['entry']],
                    entry_price=pos['entry_price'], qty=pos['qty'], cost=pos['cost'],
                    mark_date=days[t], mark_price=pos['last'], value=value,
                    pnl=value - pos['cost'], return_pct=(value / pos['cost'] - 1) * 100,
                    holding_sessions=t - pos['entry'], stop=pos['stop'],
                    pending_exit=bool(pos.get('pending_exit')))

    def snapshot(t, buys, sells):
        held = [position_row(pos, t) for pos in positions]
        realized = sum(row['pnl'] for row in trades)
        unrealized = sum(row['pnl'] for row in held)
        equity = cash + sum(row['value'] for row in held)
        assert abs(INITIAL + realized + unrealized - equity) < .001
        candidates = [dict(symbol=symbols[s], name=names[symbols[s]], close=p['close'][t, s],
                           score=score[t, s] * 100, pressure_pct=pressure[t, s] * 100,
                           consistency_pct=consistency[t, s] * 100, excess20_pct=res20[t, s] * 100,
                           squeeze=squeeze[t, s], atr=atr[t, s],
                           initial_stop_ref=round(p['close'][t, s] - 3 * atr[t, s], 2) if np.isfinite(atr[t, s]) and p['close'][t, s] > 3 * atr[t, s] else None,
                           resistance=price_levels.first_resistance(p['high'], p['close'][t, s], atr[t, s], t, s),
                           status='已持有' if any(pos['s'] == s for pos in positions) else
                           '大盤暫停買進' if block[t] else '滿倉候選' if len(positions) >= 5 else '隔日開盤候選') for s in ranks[t]]
        snapshots[days[t]] = dict(candidates=candidates, holdings=held, buy=buys, sell=sells,
                                 blocked=bool(block[t]), cash=cash, equity=equity,
                                 realized_pnl=realized, unrealized_pnl=unrealized)
        curve.append(dict(date=days[t], value=(equity / INITIAL - 1) * 100, equity=equity,
                          cash=cash, open_positions=len(positions), closed_positions=len(trades),
                          day_return_pct=None, eligible=len(candidates), entries=len(buys)))

    snapshot(a, [], [])
    for t in range(a + 1, len(days)):
        prev = t - 1
        buys, sells = [], []
        for pos in list(positions):
            s = pos['s']
            cp = p['close'][prev, s]
            if np.isfinite(cp):
                pos['peak'] = max(pos['peak'], cp)
            if np.isfinite(atr[prev, s]):
                pos['stop'] = max(pos['stop'], pos['peak'] - 3 * atr[prev, s])
            if cp < pos['stop']:
                pos['pending_exit'] = True
            if pos.get('pending_exit') and fillable(t, s):
                price = p['open'][t, s]
                value = pos['qty'] * price * (1 - SELL)
                row = position_row(pos, t)
                row.update(exit_date=days[t], exit_price=price, value=value, pnl=value - pos['cost'],
                           return_pct=(value / pos['cost'] - 1) * 100, reason='收盤跌破 3ATR，次日開盤退出')
                trades.append(row)
                cash += value
                positions.remove(pos)
                sells.append(symbols[s])
        if not block[prev]:
            equity = cash + sum(pos['qty'] * pos['last'] * (1 - SELL) for pos in positions)
            held = {pos['s'] for pos in positions}
            for s in ranks[prev]:
                if len(positions) >= 5:
                    break
                if s in held or not fillable(t, s):
                    continue
                op = p['open'][t, s]
                prior_vol = p['volume'][prev, s]
                maxqty = int(prior_vol * .01) if np.isfinite(prior_vol) else 0
                qty = min(int(min(cash, equity / 5) / (op * (1 + BUY))), maxqty)
                if qty < 1 or not np.isfinite(atr[prev, s]):
                    continue
                cost = qty * op * (1 + BUY)
                cash -= cost
                positions.append(dict(s=s, entry=t, entry_price=op, cost=cost, qty=qty,
                                      last=op, peak=op, stop=op - 3 * atr[prev, s]))
                held.add(s)
                buys.append(symbols[s])
                entries.append(dict(date=days[t], signal_date=days[prev], symbol=symbols[s], cost=cost))
        for pos in positions:
            cp = p['close'][t, pos['s']]
            if np.isfinite(cp) and cp > 0:
                pos['last'] = cp
        assert cash >= -.001
        snapshot(t, buys, sells)
    latest = snapshots[days[-1]]
    values = np.array([row['equity'] for row in curve])
    activity = [dict(date=day, buy=row['buy'], sell=row['sell'], hold=[p['symbol'] for p in row['holdings']]) for day, row in snapshots.items()]
    benchmark = [dict(date=days[t], value=(p['close'][t, ta] / p['close'][a, ta] - 1) * 100) for t in range(a, len(days))]
    result = dict(key='v8', label='V8 吸籌壓縮突破', version=VERSION, available=True,
                  description='吸籌持續性、波動壓縮與扣除市場影響後的強度；固定本金最多持有五檔。',
                  start=days[a], as_of=days[-1], initial=INITIAL, equity=latest['equity'], cash=cash,
                  portfolio_return_pct=curve[-1]['value'], realized_pnl=latest['realized_pnl'],
                  unrealized_pnl=latest['unrealized_pnl'], max_drawdown_pct=float((values / np.maximum.accumulate(values) - 1).min() * 100),
                  curve=curve, benchmark_curve=benchmark, days=snapshots, activity=activity,
                  trades=trades, entries=len(entries), entry_events=entries, open_positions=latest['holdings'],
                  wins=sum(row['pnl'] > 0 for row in trades), hit_rate=sum(row['pnl'] > 0 for row in trades) / len(trades) * 100 if trades else None,
                  top_realized=sorted(trades, key=lambda r: r['return_pct'], reverse=True)[:10],
                  top_unrealized=sorted(latest['holdings'], key=lambda r: r['return_pct'], reverse=True)[:10],
                  source_note='分點僅涵蓋本機已追蹤樣本；歷史名單有事後選擇偏差。未完整還原除權息；缺價沿用最後可用價。零股以開盤價近似，未模擬零股撮合。',
                  research_note='研究中：初始研究全期優於加權與 V5 分批版，但未勝過 V5 整筆投入版；9 月重新啟動曾落後。不得將回測視為穩定勝出。')
    # Standard JSON only; no NaN/Infinity in API responses.
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(v) for v in value]
        if isinstance(value, (np.integer, np.floating, np.bool_)):
            value = value.item()
        if isinstance(value, float) and not np.isfinite(value):
            return None
        return value
    return clean(result)
