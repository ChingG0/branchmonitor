"""V2 frozen three-position strength/intraday-stop research rules, computed only from local closing snapshots.

This is a funded simulation, not an order service. Historical source corrections
invalidate the cache. No other strategy's saved lists are changed.
"""
import math
import hashlib
import json
import sqlite3
import threading
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from . import db, price_levels

VERSION = 'v2-strength-intraday-3atr-hard8-1'
INITIAL = 1_000_000.0
BUY = .002855
SELL = .005855
_lock = threading.Lock()
_cache = {}
_watch = {}


def tick_floor(price):
    """Taiwan common-stock tick, rounded downward for a sell stop."""
    tick = .01 if price < 10 else .05 if price < 50 else .1 if price < 100 else .5 if price < 500 else 1 if price < 1000 else 5
    return round(math.floor((price + 1e-9) / tick) * tick, 5)


def empty(message):
    return dict(key='v2', label='V2 三檔強度重排', available=False,
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


def next_defense(pos, close, atr, profit_protection=False):
    """Tomorrow's stop uses only the completed close, never the next day's high.

    V1 switches permanently to 2ATR after peak CLOSE reaches entry +8%; the
    original V2 defaults to 3ATR. A switch never lowers an existing stop.
    """
    peak = max(pos['peak'], close) if np.isfinite(close) else pos['peak']
    multiple = 2 if profit_protection and peak >= pos['entry_price'] * 1.08 else 3
    return max(pos['stop'], peak - multiple * atr) if np.isfinite(atr) else pos['stop']


def calculate(raw, flows, rs_rows, cutoff=None, *, profit_protection=False,
              initial=INITIAL, max_leverage=1.0, annual_interest=0.0,
              start_date='2026-05-01'):
    if (not math.isfinite(initial) or initial <= 0 or not 1 <= max_leverage <= 1.5
            or not 0 <= annual_interest <= 1):
        raise ValueError('本金、槓桿或借款利率不合理')
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
    base_score = score.copy()
    strength_rank = rank(res20 / np.maximum(noise * np.sqrt(20), 1e-8))
    score = .7 * base_score + .3 * strength_rank
    mask = eligible & (res20 > 0) & (p['close'] > ma20) & (pressure > 0) & (squeeze <= 1) & (distance >= -.02) & (consistency >= .55) & (net.to_numpy() > 0) & (bias <= .20)
    ranks = []
    for t in range(len(days)):
        ids = np.flatnonzero(mask[t] & np.isfinite(score[t]))
        ids = ids[np.argsort(-base_score[t, ids], kind='stable')]
        ranks.append(ids[np.argsort(-score[t, ids], kind='stable')][:3].tolist())
    ta, tp = si['TAIEX'], si['TPEX']
    block = (p['close'][:, ta] < ma20[:, ta]) & (p['close'][:, tp] < ma20[:, tp])
    starts = [i for i, day in enumerate(days) if day >= start_date]
    if not starts:
        return empty(f'尚無 {start_date} 起的行情')
    a = starts[0]
    cash, positions, trades, entries, curve, snapshots = float(initial), [], [], [], [], {}
    financing_cost = 0.0

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
                    holding_sessions=t - pos['entry'], stop=tick_floor(pos['stop']),
                    next_stop=tick_floor(next_defense(pos, pos['last'], atr[t, pos['s']], profit_protection)),
                    protection_active=bool(profit_protection and pos['peak'] >= pos['entry_price'] * 1.08),
                    next_protection_active=bool(profit_protection and max(pos['peak'], pos['last']) >= pos['entry_price'] * 1.08),
                    pending_exit=bool(pos.get('pending_exit')))

    def snapshot(t, buys, sells):
        held = [position_row(pos, t) for pos in positions]
        realized = sum(row['pnl'] for row in trades)
        unrealized = sum(row['pnl'] for row in held)
        equity = cash + sum(row['value'] for row in held)
        assert abs(initial + realized + unrealized - financing_cost - equity) < .001
        gross = sum(row['qty'] * row['mark_price'] for row in held)
        borrowed = max(0., -cash)
        candidates = [dict(symbol=symbols[s], name=names[symbols[s]], close=p['close'][t, s],
                           score=score[t, s] * 100, base_score=base_score[t, s] * 100, strength_rank=strength_rank[t, s] * 100, pressure_pct=pressure[t, s] * 100,
                           consistency_pct=consistency[t, s] * 100, excess20_pct=res20[t, s] * 100,
                           squeeze=squeeze[t, s], atr=atr[t, s],
                           initial_stop_ref=tick_floor(max(p['close'][t, s] - 3 * atr[t, s], p['close'][t, s] * .92)) if np.isfinite(atr[t, s]) else None,
                           resistance=price_levels.first_resistance(p['high'], p['close'][t, s], atr[t, s], t, s),
                           status='已持有' if any(pos['s'] == s for pos in positions) else
                           '大盤暫停買進' if block[t] else '滿倉候選' if len(positions) >= 3 else '隔日開盤候選') for s in ranks[t]]
        snapshots[days[t]] = dict(candidates=candidates, holdings=held, buy=buys, sell=sells,
                                 blocked=bool(block[t]), cash=cash, equity=equity,
                                 realized_pnl=realized, unrealized_pnl=unrealized,
                                 borrowed=borrowed, financing_cost=financing_cost,
                                 gross_exposure=gross, actual_leverage=gross/equity if equity>0 else None)
        curve.append(dict(date=days[t], value=(equity / initial - 1) * 100, equity=equity,
                          cash=cash, borrowed=borrowed, financing_cost=financing_cost,
                          open_positions=len(positions), closed_positions=len(trades),
                          day_return_pct=None, eligible=len(candidates), entries=len(buys)))

    def sell(pos, t, price, reason, sells):
        nonlocal cash
        value = pos['qty'] * price * (1 - SELL)
        row = position_row(pos, t)
        row.update(exit_date=days[t], exit_price=price, value=value,
                   pnl=value - pos['cost'], return_pct=(value / pos['cost'] - 1) * 100,
                   reason=reason)
        trades.append(row)
        cash += value
        positions.remove(pos)
        sells.append(symbols[pos['s']])

    snapshot(a, [], [])
    for t in range(a + 1, len(days)):
        prev = t - 1
        buys, sells, exited = [], [], set()
        if cash < 0 and annual_interest:
            elapsed=(date.fromisoformat(days[t])-date.fromisoformat(days[prev])).days
            charge=-cash*annual_interest*elapsed/365
            cash-=charge
            financing_cost+=charge
        # Fix stops using only data available before this session.
        for pos in list(positions):
            s = pos['s']
            cp = p['close'][prev, s]
            if np.isfinite(cp):
                pos['peak'] = max(pos['peak'], cp)
            if np.isfinite(atr[prev, s]):
                pos['stop'] = next_defense(pos, cp, atr[prev, s], profit_protection)
            stop = tick_floor(pos['stop'])
            if pos.get('pending_exit') or (np.isfinite(p['open'][t, s]) and p['open'][t, s] <= stop):
                if fillable(t, s):
                    sell(pos, t, p['open'][t, s], '開盤跳空／延後停損', sells)
                    exited.add(s)
                else:
                    pos['pending_exit'] = True
        if not block[prev]:
            equity = cash + sum(pos['qty'] * pos['last'] * (1 - SELL) for pos in positions)
            held = {pos['s'] for pos in positions}
            for s in ranks[prev]:
                if len(positions) >= 3:
                    break
                if s in held or s in exited or not fillable(t, s):
                    continue
                op = p['open'][t, s]
                prior_vol = p['volume'][prev, s]
                maxqty = int(prior_vol * .01) if np.isfinite(prior_vol) else 0
                if max_leverage == 1:
                    budget=min(cash,equity/3)
                else:
                    gross=sum(pos['qty']*pos['last'] for pos in positions)
                    budget=max(0.,min(equity*max_leverage/3,
                                      equity*max_leverage-gross,
                                      cash+equity*(max_leverage-1)))
                qty = min(int(budget / (op * (1 + BUY))), maxqty)
                if qty < 1 or not np.isfinite(atr[prev, s]):
                    continue
                cost = qty * op * (1 + BUY)
                cash -= cost
                positions.append(dict(s=s, entry=t, entry_price=op, cost=cost, qty=qty,
                                      last=op, peak=op, stop=max(op - 3 * atr[prev, s], op * .92)))
                held.add(s)
                buys.append(symbols[s])
                entries.append(dict(date=days[t], signal_date=days[prev], symbol=symbols[s], cost=cost))
        # A new position can stop out today; intraday proceeds cannot fund past opening buys.
        for pos in list(positions):
            s = pos['s']
            stop = tick_floor(pos['stop'])
            if np.isfinite(p['low'][t, s]) and p['low'][t, s] <= stop:
                if fillable(t, s):
                    sell(pos, t, min(p['open'][t, s], stop), '盤中預設停損', sells)
                else:
                    pos['pending_exit'] = True
        for pos in positions:
            cp = p['close'][t, pos['s']]
            if np.isfinite(cp) and cp > 0:
                pos['last'] = cp
        assert (max_leverage>1 or cash >= -.001) and len(positions) <= 3
        snapshot(t, buys, sells)
    latest = snapshots[days[-1]]
    values = np.array([row['equity'] for row in curve])
    activity = [dict(date=day, buy=row['buy'], sell=row['sell'], hold=[p['symbol'] for p in row['holdings']]) for day, row in snapshots.items()]
    benchmark = [dict(date=days[t], value=(p['close'][t, ta] / p['close'][a, ta] - 1) * 100) for t in range(a, len(days))]
    result = dict(key='v2', label='V2 三檔強度重排', version=VERSION, available=True,
                  description='原 V8 資格與分數 70%＋20 日市場調整強度／波動百分位 30%；最多三檔，盤中 3ATR／8% 防線。',
                  start=days[a], as_of=days[-1], initial=initial, equity=latest['equity'], cash=cash,
                  portfolio_return_pct=curve[-1]['value'], realized_pnl=latest['realized_pnl'],
                  unrealized_pnl=latest['unrealized_pnl'], financing_cost=financing_cost,
                  borrowed=latest['borrowed'], gross_exposure=latest['gross_exposure'],
                  actual_leverage=latest['actual_leverage'], max_leverage=max_leverage,
                  annual_interest=annual_interest,
                  max_drawdown_pct=float((values / np.maximum.accumulate(values) - 1).min() * 100),
                  curve=curve, benchmark_curve=benchmark, days=snapshots, activity=activity,
                  trades=trades, entries=len(entries), entry_events=entries, open_positions=latest['holdings'],
                  wins=sum(row['pnl'] > 0 for row in trades), hit_rate=sum(row['pnl'] > 0 for row in trades) / len(trades) * 100 if trades else None,
                  top_realized=sorted(trades, key=lambda r: r['return_pct'], reverse=True)[:10],
                  top_unrealized=sorted(latest['holdings'], key=lambda r: r['return_pct'], reverse=True)[:10],
                  source_note='分點僅涵蓋本機已追蹤樣本；歷史名單有事後選擇偏差。未完整還原除權息；缺價沿用最後可用價。零股以開盤價近似，未模擬零股撮合。',
                  research_note='研究版：沿用已觀察過的 2026/5 起樣本，尚未完成 2018 年起或獨立樣本驗證。盤中停損以日 K 高低價近似成交；8% 是初始價格防線，跳空、無法成交及成本仍可能使損失超過 8%。')
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
