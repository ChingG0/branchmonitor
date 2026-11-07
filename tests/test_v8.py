import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from backend import v8


class V8Tests(unittest.TestCase):
    @staticmethod
    def fixture():
        rng = np.random.default_rng(51)
        days = pd.bdate_range('2026-01-01', periods=190).strftime('%Y-%m-%d').tolist()
        rows, flows, rs = [], [], []
        symbols = ['1101', '1102', '1103', '1104', '1105', '1106', '1107', 'TAIEX', 'TPEX']
        for i, symbol in enumerate(symbols):
            prices = 100 * np.cumprod(1 + rng.normal(.004 if i < 7 else .0005, .004, len(days)))
            if i < 7:
                prices[140:145] *= .75
            for t, day in enumerate(days):
                close = prices[t]
                rows.append(dict(date=day, symbol=symbol, name=symbol, open=close * .998,
                                 high=close * 1.005, low=close * .992, close=close, volume=2e6))
                if i < 7:
                    flows.append(dict(date=day, symbol=symbol, net=100 + i * 10 + t))
        for day in days:
            rs.append((day, json.dumps({'groups': {mode: [dict(symbol=s, rs=75) for s in symbols[:7]] for mode in ['20', '60', 'ytd']}})))
        return pd.DataFrame(rows), pd.DataFrame(flows), rs

    def test_account_cashflows_and_no_future_data(self):
        raw, flows, rs = self.fixture()
        result = v8.calculate(raw, flows, rs)
        self.assertTrue(result['available'])
        self.assertGreater(result['entries'], 0)
        self.assertGreater(len(result['trades']), 0)
        for day, snap in result['days'].items():
            self.assertGreaterEqual(snap['cash'], 0)
            self.assertLessEqual(len(snap['holdings']), 5)
            self.assertEqual(len({p['symbol'] for p in snap['holdings']}), len(snap['holdings']))
            self.assertAlmostEqual(snap['equity'], v8.INITIAL + snap['realized_pnl'] + snap['unrealized_pnl'])
            self.assertAlmostEqual(snap['equity'], snap['cash'] + sum(p['value'] for p in snap['holdings']))
            for candidate in snap['candidates']:
                self.assertAlmostEqual(candidate['initial_stop_ref'], round(
                    candidate['close'] - 3 * candidate['atr'], 2))
                if candidate['resistance']:
                    self.assertGreater(candidate['resistance']['low'], candidate['close'])
        for trade in result['trades']:
            self.assertLess(trade['signal_date'], trade['entry_date'])
            self.assertGreater(trade['exit_date'], trade['entry_date'])
            self.assertAlmostEqual(trade['cost'], trade['qty'] * trade['entry_price'] * (1 + v8.BUY))
            self.assertAlmostEqual(trade['pnl'], trade['qty'] * trade['exit_price'] * (1 - v8.SELL) - trade['cost'])
        cutoff = sorted(result['days'])[35]
        prefix = v8.calculate(raw, flows, rs, cutoff)
        self.assertEqual(prefix['curve'], [p for p in result['curve'] if p['date'] <= cutoff])
        self.assertEqual(prefix['days'], {d: p for d, p in result['days'].items() if d <= cutoff})
        json.dumps(result, allow_nan=False)

    def test_missing_rs_never_fills_with_stale_candidates(self):
        raw, flows, rs = self.fixture()
        result = v8.calculate(raw, flows, rs[:-1])
        self.assertEqual(result['days'][result['as_of']]['candidates'], [])

    def test_cache_invalidates_same_day_corrections_even_when_totals_match(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.sqlite3'
            with sqlite3.connect(path) as c:
                c.executescript('CREATE TABLE prices(date,symbol,name,open,high,low,close,volume); CREATE TABLE flows(date,symbol,net); CREATE TABLE rs_days(date,payload);')
                c.executemany('INSERT INTO flows VALUES (?,?,?)', [('2026-05-04', '1101', 1), ('2026-05-04', '1102', 3)])
            with patch.object(v8, 'calculate', return_value={'available': False}) as calculate:
                v8.report(path)
                v8.report(path)
                self.assertEqual(calculate.call_count, 1)
                with sqlite3.connect(path) as c:
                    c.execute("UPDATE flows SET net=4-net")
                v8.report(path)
                self.assertEqual(calculate.call_count, 2)
                with sqlite3.connect(path) as c:
                    c.execute('CREATE TABLE unrelated_report(date,payload)')
                    c.execute("INSERT INTO unrelated_report VALUES ('2026-05-04','cost enrichment')")
                v8.report(path)
                self.assertEqual(calculate.call_count, 2)


if __name__ == '__main__':
    unittest.main()
