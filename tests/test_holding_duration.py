import copy
import unittest
from backend import holding_duration, v9_tx


class HoldingDurationTests(unittest.TestCase):
    def test_completed_costed_trades_zero_duration_and_no_mutation(self):
        rows=[
            dict(entry_date='2026-10-20',exit_date='2026-10-21',holding_sessions=1,pnl=10),
            dict(entry_date='2026-10-20',exit_date='2026-10-29',holding_sessions=5,pnl=90),
            dict(entry_date='2026-10-22',exit_date='2026-10-22',holding_sessions=0,pnl=-1),
            dict(entry_date='2026-10-20',exit_date='2026-10-21',holding_sessions=1,net_return_pct=-.2,hit=True),
            dict(entry_date='2026-10-20',exit_date='2026-10-21',holding_sessions=1,pnl=0),
            dict(entry_date='2026-10-20',holding_sessions=9,pnl=1000),
            dict(entry_date='2026-10-20',exit_date='2026-10-30',holding_sessions=9,pnl=1000),
        ]
        original=copy.deepcopy(rows)
        s=holding_duration.summarize(rows,as_of='2026-10-29')
        self.assertEqual(s['win'],dict(samples=2,avg_days=3))
        self.assertEqual(s['loss'],dict(samples=2,avg_days=.5))
        self.assertEqual(s['flat_count'],1)
        self.assertEqual(s['completed_count'],5)
        self.assertEqual(rows,original)

    def test_saved_v3_calendar_ignores_weekend_and_market_closure(self):
        calendar=['2026-10-22','2026-10-27','2026-10-28','2026-10-29']
        s=holding_duration.summarize([dict(entry_date='2026-10-22',exit_date='2026-10-28',pnl=12)],calendar,as_of='2026-10-29')
        self.assertEqual(s['win']['avg_days'],2)
        self.assertIsNone(s['loss']['avg_days'])

    def test_no_samples_or_invalid_values_do_not_become_zero_days(self):
        self.assertIsNone(holding_duration.summarize([])['win']['avg_days'])
        rows=[dict(entry_date='2026-10-20',exit_date='2026-10-21',pnl=1),
              dict(entry_date='2026-10-20',exit_date='2026-10-21',holding_sessions=2,pnl=float('nan'))]
        s=holding_duration.summarize(rows)
        self.assertEqual(s['unavailable_count'],2)
        self.assertEqual(s['win']['samples'],0)

    def test_v9_direction_round_trip_includes_roll_costs_without_splitting(self):
        h=object.__new__(v9_tx.TXHistory)
        h.days=['2026-01-01','2026-01-02','2026-01-05','2026-01-06','2026-01-07','2026-01-08']
        h.day_index={d:i for i,d in enumerate(h.days)}
        h.index={d:dict(open=100,close=100) for d in h.days}
        h.signals=dict(zip(h.days,[1,1,-1,-1,1,1]))
        h.roll_map=dict(zip(h.days,['A','B','B','B','B','B']))
        h.quotes={('2026-01-02','A'):dict(open=100,close=110),
                  ('2026-01-05','A'):dict(open=112,close=112),
                  ('2026-01-05','B'):dict(open=120,close=125),
                  ('2026-01-06','B'):dict(open=122,close=115),
                  ('2026-01-07','B'):dict(open=115,close=110),
                  ('2026-01-08','B'):dict(open=108,close=109)}
        r=h.simulate('2026-01-02','2026-01-08')
        self.assertEqual(len(r['trades']),2)
        long,short=r['trades'];cost=v9_tx.COST_POINTS_PER_SIDE
        self.assertEqual(long['entry_date'],'2026-01-02')
        self.assertEqual(long['exit_date'],'2026-01-06')
        self.assertEqual(long['holding_sessions'],2)
        self.assertAlmostEqual(long['pnl_points'],14-4*cost)
        self.assertEqual(short['holding_sessions'],2)
        self.assertAlmostEqual(short['pnl_points'],14-2*cost)
        # The open final long leg is not counted as a completed trade.
        self.assertAlmostEqual(r['pnl_points'],(14-4*cost)+(14-2*cost)+(1-2*cost))

if __name__=='__main__':unittest.main()
