"""V9: frozen TX drawdown reversal, using prior closes and next day-session opens.

Historical seed files are immutable, source-hashed TAIFEX/Yahoo downloads.
Only newer official TX bars and the app's saved TAIEX bars extend the curve.
"""
from __future__ import annotations

import csv
import json
import math
import sqlite3
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import backtest, db, research

VERSION = 'v9-tx-drawdown20-cross8-short-one-session-1'
SEED = Path(__file__).with_name('v9_seed')
SEED_END = '2026-10-07'
COST_POINTS_PER_SIDE = 2.0
POINT_VALUE_TWD = 200
YEAR_START = 2018


def _init(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS v9_tx_quotes (
        date TEXT NOT NULL, contract TEXT NOT NULL,
        open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
        source TEXT NOT NULL, fetched_at TEXT NOT NULL,
        PRIMARY KEY(date,contract))''')


@lru_cache(maxsize=1)
def _seed():
    with (SEED / 'manifest.json').open() as handle:
        manifest = json.load(handle)
    for name in ('tx.csv', 'taiex.csv'):
        import hashlib
        if hashlib.sha256((SEED / name).read_bytes()).hexdigest() != manifest['hashes'][name]:
            raise ValueError(f'V9 歷史行情校驗失敗：{name}')
    with (SEED / 'tx.csv').open() as handle:
        futures = {(row['date'], row['contract']): {
            key: float(row[key]) for key in ('open', 'high', 'low', 'close')}
            for row in csv.DictReader(handle)}
    with (SEED / 'taiex.csv').open() as handle:
        index = {row['date']: {key: float(row[key]) for key in ('open', 'high', 'low', 'close')}
                 for row in csv.DictReader(handle)}
    return futures, index


def _loaded():
    seed_futures, seed_index = _seed()
    futures, index = dict(seed_futures), dict(seed_index)
    with db.connect() as conn:
        _init(conn)
        for row in conn.execute('SELECT date,contract,open,high,low,close FROM v9_tx_quotes WHERE date>?', (SEED_END,)):
            futures[(row['date'], row['contract'])] = {key: float(row[key]) for key in ('open', 'high', 'low', 'close')}
        try:
            rows = conn.execute("SELECT date,open,high,low,close FROM prices WHERE symbol='TAIEX' AND date>? ORDER BY date", (SEED_END,)).fetchall()
        except sqlite3.OperationalError:
            rows = []
        for row in rows:
            if all(isinstance(row[key], (int, float)) and math.isfinite(row[key]) and row[key] > 0
                   for key in ('open', 'high', 'low', 'close')):
                index[row['date']] = {key: float(row[key]) for key in ('open', 'high', 'low', 'close')}
    return futures, index


def refresh():
    """Fetch only newer index days with missing contract bars; preserve gaps."""
    with db.connect() as conn:
        _init(conn)
        try:
            dates = [row[0] for row in conn.execute(
                "SELECT date FROM prices WHERE symbol='TAIEX' AND date>? AND open>0 AND high>0 AND low>0 AND close>0 ORDER BY date",
                (SEED_END,))]
        except sqlite3.OperationalError:
            dates = []
        complete = {row[0] for row in conn.execute(
            'SELECT date FROM v9_tx_quotes GROUP BY date HAVING COUNT(*)>=2')}
    created, failures = 0, []
    for day in dates:
        if day in complete:
            continue
        try:
            rows = backtest.fetch_futures(day)
            valid = [row for row in rows if row.get('date') == day and
                     len(str(row.get('contract', ''))) == 6 and str(row['contract']).isdigit() and
                     backtest.valid_bar(row)]
            if len({row['contract'] for row in valid}) < 2:
                raise ValueError('近月／次近月 TX 日盤行情尚未齊全')
            with db.connect() as conn:
                _init(conn)
                conn.executemany('INSERT OR REPLACE INTO v9_tx_quotes VALUES (?,?,?,?,?,?,?,?)', [
                    (day, row['contract'], row['open'], row['high'], row['low'], row['close'],
                     row.get('source', 'TAIFEX'), research.now()) for row in valid])
            created += 1
        except Exception as error:
            failures.append(f'{day}：{str(error)[:100]}')
    latest = latest_date()
    status = 'partial' if failures else 'complete'
    message = (f'V9 台指期更新至 {latest}；新增 {created} 日' +
               (f'；待補 {len(failures)} 日：{failures[0]}' if failures else '') if dates
               else f'V9 研究行情已保存至 {SEED_END}；等待更新後的加權指數日線')
    return {'status': status, 'message': message, 'as_of': latest,
            'added_days': created, 'missing_dates': failures}


def latest_date():
    # The home card must reflect the last *continuous* day, never a later bar
    # saved after an earlier missing day.
    return TXHistory(*_loaded()).days[-1]


class TXHistory:
    def __init__(self, futures, index):
        self.quotes = futures
        self.index = index
        contracts_by_day = {}
        for day, contract in futures:
            if day in index:
                contracts_by_day.setdefault(day, []).append(contract)
        common = sorted(day for day, contracts in contracts_by_day.items() if len(set(contracts)) >= 2)
        # A fresh index day without TX is a missing bar, not permission to skip a day.
        missing = sorted(day for day in index if day > SEED_END and day not in common)
        if missing:
            common = [day for day in common if day < missing[0]]
        self.days = common
        self.missing_dates = missing
        self.day_index = {day: i for i, day in enumerate(common)}
        self.near = {day: sorted(set(contracts_by_day[day]))[0] for day in common}
        self.far = {day: sorted(set(contracts_by_day[day]))[1] for day in common}
        last = common[-1]
        maximum = {}
        for day, contract in futures:
            if day in self.day_index:
                maximum[contract] = max(maximum.get(contract, day), day)
        expiry = {}
        for contract, observed in maximum.items():
            if contract[:6] <= last[:7].replace('-', '') and observed < last:
                expiry[contract] = observed
            else:
                year, month = int(contract[:4]), int(contract[4:6])
                expiry[contract] = next(date(year, month, n).isoformat() for n in range(15, 22)
                                        if date(year, month, n).weekday() == 2)
        self.expiry = expiry
        self.roll_map = {}
        active = None
        for day in common:
            near, far = self.near[day], self.far[day]
            exp = expiry[near]
            remaining = (self.day_index[exp] - self.day_index[day] if exp in self.day_index
                         else int(np.busday_count(day, exp)))
            suggested = far if remaining <= 5 else near
            active = max(active or near, suggested, near)
            if (day, active) not in futures:
                raise ValueError(f'V9 缺少轉倉行情：{day} {active}')
            self.roll_map[day] = active
        self.signals = {}
        prior_drawdown = None
        for i, day in enumerate(common):
            drawdown = (index[day]['close'] / max(index[p]['high'] for p in common[i-20:i]) - 1
                        if i >= 20 else None)
            self.signals[day] = (-1 if drawdown is not None and prior_drawdown is not None and
                                 drawdown <= -.08 and prior_drawdown > -.08 else 1)
            prior_drawdown = drawdown

    def simulate(self, start, end, defensive=True):
        dates = [day for day in self.days if start <= day <= end]
        if not dates:
            raise ValueError('V9 無可用交易日')
        if self.day_index[dates[0]] == 0:
            raise ValueError('V9 缺少前一日訊號')
        denominator = self.index[dates[0]]['open']
        sign, contract, last_close, pnl = 0, None, None, 0.0
        entries = exits = rolls = 0
        curve, activity, trades = [], [], []
        active_trade = None
        prior = self.days[self.day_index[dates[0]] - 1]
        for day in dates:
            desired = self.signals[prior] if defensive else 1
            target = self.roll_map[prior]
            if (day, target) not in self.quotes:
                raise ValueError(f'V9 下一日開盤缺少可成交契約：{day} {target}')
            if sign:
                old = self.quotes[(day, contract)]
                pnl += sign * (old['open'] - last_close)
            changed = sign != desired or sign and contract != target
            action = '持有'
            if changed:
                if sign:
                    pnl -= COST_POINTS_PER_SIDE
                    exits += 1
                    if sign != desired and active_trade is not None:
                        points = pnl - active_trade['pnl_before']
                        trades.append({
                            'entry_date': active_trade['entry_date'], 'exit_date': day,
                            'direction': active_trade['direction'],
                            'holding_sessions': self.day_index[day] - self.day_index[active_trade['entry_date']],
                            'pnl_points': round(points, 8),
                            'pnl': round(points * POINT_VALUE_TWD, 6),
                        })
                        active_trade = None
                if desired:
                    if active_trade is None:
                        active_trade = {'entry_date': day, 'direction': desired, 'pnl_before': pnl}
                    pnl -= COST_POINTS_PER_SIDE
                    entries += 1
                if sign and contract != target:
                    rolls += 1
                if sign == 1 and desired == -1:
                    action = '多單平倉／建立空單'
                elif sign == -1 and desired == 1:
                    action = '空單回補／建立多單'
                elif sign and contract != target:
                    action = '近遠月轉倉'
                elif desired == 1:
                    action = '建立多單'
                if action != '持有':
                    activity.append({'date': day, 'signal_date': prior, 'action': action,
                                     'contract': target, 'open': self.quotes[(day, target)]['open']})
            sign, contract = desired, target
            quote = self.quotes[(day, contract)]
            pnl += sign * (quote['close'] - quote['open'])
            last_close = quote['close']
            # Price as if liquidated at this close; the position itself stays open.
            net_mark = pnl - COST_POINTS_PER_SIDE
            curve.append({'date': day, 'value': round(net_mark / denominator * 100, 4),
                          'pnl_points': round(net_mark, 2), 'position': sign,
                          'contract': contract, 'action': action})
            prior = day
        values = [denominator] + [denominator + row['pnl_points'] for row in curve]
        peak = values[0]
        drawdown = 0.0
        for value in values[1:]:
            peak = max(peak, value)
            drawdown = min(drawdown, (value / peak - 1) * 100)
        return {'from': dates[0], 'to': dates[-1], 'return_pct': curve[-1]['value'],
                'pnl_points': curve[-1]['pnl_points'],
                'pnl_twd_per_contract': curve[-1]['pnl_points'] * POINT_VALUE_TWD,
                'taiex_pct': round((self.index[dates[-1]]['close'] / denominator - 1) * 100, 4),
                'max_drawdown_pct': round(drawdown, 4), 'entries': entries,
                'exits_including_mark': exits + 1, 'rolls': rolls,
                'curve': curve, 'activity': activity, 'trades': trades}


def report():
    futures, index = _loaded()
    study = TXHistory(futures, index)
    last = study.days[-1]
    year = int(last[:4])
    ytd = study.simulate(f'{year}-01-01', last)
    baseline = study.simulate(f'{year}-01-01', last, defensive=False)
    years = []
    for value in range(YEAR_START, year + 1):
        period = study.simulate(f'{value}-01-01', last if value == year else f'{value}-12-31')
        long_tx = study.simulate(f'{value}-01-01', last if value == year else f'{value}-12-31', defensive=False)
        years.append({'year': value, 'from': period['from'], 'to': period['to'],
                      'model_pct': period['return_pct'], 'hold_tx_pct': long_tx['return_pct'],
                      'taiex_pct': period['taiex_pct'],
                      'edge_vs_index_pp': round(period['return_pct'] - period['taiex_pct'], 4),
                      'edge_vs_hold_tx_pp': round(period['return_pct'] - long_tx['return_pct'], 4)})
    denominator = index[ytd['from']]['open']
    index_curve = [{'date': row['date'], 'value': round((index[row['date']]['close'] / denominator - 1) * 100, 4)}
                   for row in ytd['curve']]
    last_signal = study.signals[last]
    position = ytd['curve'][-1]['position']
    next_action = ('下一日盤開盤平多翻空' if position == 1 and last_signal == -1 else
                   '下一日盤開盤回補轉多' if position == -1 and last_signal == 1 else
                   '維持空單' if position == -1 else '維持多單')
    return {'key': 'v9', 'version': VERSION, 'label': 'V9 台指期回檔反手',
            'description': '加權指數首次跌破前20日高點8%時，下一日盤開盤由多翻空；再下一日盤開盤恢復多單。',
            'as_of': last, 'signal_date': last, 'position': position,
            'next_action': next_action, 'target_contract': study.roll_map[last],
            'index_close': index[last]['close'], 'status': 'partial' if study.missing_dates else 'complete',
            'missing_dates': study.missing_dates,
            'portfolio_return_pct': ytd['return_pct'], 'realized_return_pct': None,
            'unrealized_return_pct': None,
            'period': f'{year} 年初至 {last}', 'curve': ytd['curve'],
            'index_curve': index_curve, 'hold_tx_curve': baseline['curve'],
            'taiex_pct': ytd['taiex_pct'], 'hold_tx_pct': baseline['return_pct'],
            'pnl_points': ytd['pnl_points'], 'pnl_twd_per_contract': ytd['pnl_twd_per_contract'],
            'max_drawdown_pct': ytd['max_drawdown_pct'], 'years': years,
            'activity': ytd['activity'][-30:],
            'trades': ytd['trades'],
            'cost_points_per_side': COST_POINTS_PER_SIDE, 'point_value_twd': POINT_VALUE_TWD,
            'basis': '一口累積點數損益÷期初指數開盤點數；每次買賣扣2點；每日曲線假設當日收盤平倉並預扣2點。非保證金報酬，不複利。',
            'validation': '研究規則曾看過2026行情，且少數反手貢獻多數增益；正式顯示不代表未來優勢已獲獨立驗證。',
            'source': {'futures': 'https://www.taifex.com.tw/cht/3/futDailyMarketView',
                       'index_history': 'https://finance.yahoo.com/quote/%5ETWII/history/'}}
