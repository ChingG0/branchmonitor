import json
import unittest
from backend import v2
import test_v8 as fixtures

class V2Tests(unittest.TestCase):
    def test_cash_identity_prefix_and_capacity(self):
        raw, flows, rs = fixtures.V8Tests.fixture()
        full = v2.calculate(raw, flows, rs)
        self.assertTrue(full['trades'])
        for snap in full['days'].values():
            self.assertGreaterEqual(snap['cash'], 0)
            self.assertLessEqual(len(snap['holdings']), 3)
            self.assertLessEqual(len(snap['candidates']), 3)
            self.assertEqual(len({p['symbol'] for p in snap['holdings']}), len(snap['holdings']))
            self.assertAlmostEqual(snap['equity'], v2.INITIAL + snap['realized_pnl'] + snap['unrealized_pnl'])
            self.assertAlmostEqual(snap['equity'], snap['cash'] + sum(p['value'] for p in snap['holdings']))
            for candidate in snap['candidates']:
                self.assertAlmostEqual(candidate['initial_stop_ref'], v2.tick_floor(max(
                    candidate['close'] - 3 * candidate['atr'], candidate['close'] * .92)))
                if candidate['resistance']:
                    self.assertGreater(candidate['resistance']['low'], candidate['close'])
                    self.assertGreaterEqual(candidate['resistance']['high'], candidate['resistance']['low'])
            for p in snap['holdings']:
                self.assertGreaterEqual(p['next_stop'], p['stop'])
        cut = list(full['days'])[35]
        prefix = v2.calculate(raw, flows, rs, cut)
        self.assertEqual(prefix['days'], {d:s for d,s in full['days'].items() if d <= cut})
        self.assertEqual(prefix['curve'], [p for p in full['curve'] if p['date'] <= cut])
        json.dumps(full, allow_nan=False)

    def test_new_position_can_stop_same_day(self):
        raw, flows, rs = fixtures.V8Tests.fixture()
        baseline = v2.calculate(raw, flows, rs)
        entry = baseline['entry_events'][0]
        selected = (raw.date == entry['date']) & (raw.symbol == entry['symbol'])
        raw.loc[selected, 'low'] = raw.loc[selected, 'open'] * .5
        result = v2.calculate(raw, flows, rs)
        trade = next(t for t in result['trades'] if t['symbol']==entry['symbol'] and t['entry_date']==entry['date'])
        self.assertEqual(trade['exit_date'], entry['date'])
        self.assertEqual(trade['reason'], '盤中預設停損')
        self.assertEqual(trade['exit_price'], trade['stop'])
        self.assertAlmostEqual(trade['pnl'], trade['qty']*trade['exit_price']*(1-v2.SELL)-trade['cost'])
        self.assertIn(trade['symbol'], result['days'][entry['date']]['buy'])
        self.assertIn(trade['symbol'], result['days'][entry['date']]['sell'])

    def test_gap_sells_at_open_and_does_not_rebuy(self):
        raw, flows, rs = fixtures.V8Tests.fixture()
        base = v2.calculate(raw, flows, rs)
        day, snap = next((d,s) for d,s in base['days'].items() if s['holdings'])
        position = snap['holdings'][0]
        days = list(base['days']); tomorrow = days[days.index(day)+1]
        selected = (raw.date==tomorrow)&(raw.symbol==position['symbol'])
        price = position['next_stop']*.75
        for field, value in [('open',price),('low',price*.99),('high',price*1.01),('close',price)]:
            raw.loc[selected,field]=value
        result = v2.calculate(raw, flows, rs)
        trade = next(t for t in result['trades'] if t['symbol']==position['symbol'] and t['entry_date']==position['entry_date'])
        self.assertEqual(trade['exit_date'],tomorrow)
        self.assertEqual(trade['exit_price'],price)
        self.assertNotIn(trade['symbol'],result['days'][tomorrow]['buy'])

    def test_missing_rs_and_tick_rounding(self):
        raw,flows,rs = fixtures.V8Tests.fixture()
        result=v2.calculate(raw,flows,rs[:-1])
        self.assertEqual(result['days'][result['as_of']]['candidates'],[])
        for price,expected in [(9.999,9.99),(49.99,49.95),(99.99,99.9),(499.99,499.5),(999.9,999),(1019,1015)]:
            self.assertEqual(v2.tick_floor(price),expected)
