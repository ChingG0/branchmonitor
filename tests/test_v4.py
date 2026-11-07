import json
import unittest
import pandas as pd

from backend import v2, v4
import test_v8 as fixtures


class V4Tests(unittest.TestCase):
    @staticmethod
    def fixture():
        raw, flows, rs = fixtures.V8Tests.fixture()
        old = sorted(raw.date.unique())
        new = pd.bdate_range('2026-06-01', periods=len(old)).strftime('%Y-%m-%d').tolist()
        mapping = dict(zip(old, new))
        raw.date = raw.date.map(mapping)
        flows.date = flows.date.map(mapping)
        rs = [(mapping[day], payload) for day, payload in rs]
        return raw, flows, rs

    def test_starts_empty_on_today_and_uses_v1_signals(self):
        raw, flows, rs = self.fixture()
        before = v4.calculate(raw, flows, rs, '2026-10-15')
        self.assertFalse(before['available'])
        self.assertEqual(before['label'], 'V4 百萬挑戰')
        self.assertEqual(before['equity'], 100_000)
        preview = before['prestart_preview']
        self.assertEqual((preview['signal_date'], preview['intended_date']),
                         ('2026-10-15', '2026-10-16'))
        self.assertEqual(before['trades'], [])
        self.assertEqual(before['open_positions'], [])
        self.assertTrue(all(row['status'] == ('大盤暫停買進' if preview['blocked']
                                               else '起跑前觀察')
                            for row in preview['candidates']))
        self.assertTrue(all(isinstance(row['planned_shares'], int)
                            for row in preview['candidates']))
        # Adding later prices must not alter the prior-close preview.
        earlier = v4.calculate(raw[raw.date <= '2026-10-15'],
                               flows[flows.date <= '2026-10-15'],
                               [(day, payload) for day, payload in rs if day <= '2026-10-15'])
        self.assertEqual(preview, earlier['prestart_preview'])
        self.assertIsNone(v4.calculate(raw, flows, rs, '2026-10-14')['prestart_preview'])
        result = v4.calculate(raw, flows, rs)
        self.assertTrue(result['available'])
        self.assertEqual(result['label'], 'V4 百萬挑戰')
        self.assertEqual(result['initial'], 100_000)
        self.assertGreaterEqual(result['start'], v4.START)
        self.assertTrue(all(row['date'] > result['start'] for row in result['entry_events']))
        baseline = v2.calculate(raw, flows, rs, profit_protection=True,
                                initial=100_000, start_date=v4.START)
        self.assertEqual(result['unlevered_curve'], baseline['curve'])
        for day, snap in result['days'].items():
            self.assertAlmostEqual(snap['equity'], 100_000 + snap['realized_pnl'] +
                                   snap['unrealized_pnl'] - snap['financing_cost'])
            self.assertAlmostEqual(snap['borrowed'], max(0, -snap['cash']))
            self.assertTrue(all(isinstance(row['planned_shares'], int)
                                for row in snap['candidates']))
        json.dumps(result, allow_nan=False)

    def test_planned_shares_use_close_fees_liquidity_and_do_not_trade(self):
        day = '2026-10-15'
        raw = pd.DataFrame([(day, 'A', 100_000), (day, 'B', 5_000),
                            (day, 'C', 100_000)],
                           columns=['date', 'symbol', 'volume'])
        snap = {'equity': 100_000., 'cash': 100_000., 'gross_exposure': 0.,
                'holdings': [], 'blocked': True,
                'candidates': [{'symbol': 'A', 'close': 100.},
                               {'symbol': 'B', 'close': 200.},
                               {'symbol': 'C', 'close': 1000.}]}
        v4._attach_planned_shares(raw, {day: snap})
        self.assertEqual([row['planned_shares'] for row in snap['candidates']],
                         [498, 50, 49])
        self.assertEqual((snap['cash'], snap['gross_exposure'], snap['holdings']),
                         (100_000., 0., []))

    def test_loan_interest_and_prefix_are_consistent(self):
        raw, flows, rs = self.fixture()
        result = v4.calculate(raw, flows, rs)
        borrowed = [point for point in result['curve'] if point['borrowed'] > 0]
        self.assertTrue(borrowed, 'fixture should exercise the credit line')
        self.assertGreater(result['financing_cost'], 0)
        self.assertAlmostEqual(result['equity'], result['initial'] + result['realized_pnl'] +
                               result['unrealized_pnl'] - result['financing_cost'])
        cutoff = result['curve'][-5]['date']
        prefix = v4.calculate(raw, flows, rs, cutoff)
        self.assertEqual(prefix['curve'], [x for x in result['curve'] if x['date'] <= cutoff])
        self.assertEqual(prefix['days'], {day:x for day,x in result['days'].items() if day <= cutoff})


if __name__ == '__main__':
    unittest.main()
