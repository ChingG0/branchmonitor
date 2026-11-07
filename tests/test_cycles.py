import unittest
from backend.analytics import analyze
from test_analytics import flow,quotes

D=['2026-09-01','2026-09-02','2026-09-03','2026-09-04','2026-09-07','2026-09-08','2026-09-09']

def run(nets,values,**kwargs):
    days=D[:len(nets)]
    return analyze([flow(d,n) for d,n in zip(days,nets)],quotes(days,values),days,days[-1],**kwargs)

class CycleTests(unittest.TestCase):
    def test_500_lots_partial_sales_are_one_complete_cycle(self):
        r=run([500,250,-600,-150],[100,120,130,110])
        c=r['cycles'][0]
        self.assertEqual(len(r['cycles']),1)
        self.assertEqual(c['quantity'],0)
        self.assertEqual(c['bought_lots'],750)
        self.assertEqual(c['cost'],80000000)
        self.assertEqual(c['pnl'],14500000)
        self.assertAlmostEqual(c['return_pct'],18.125)
        self.assertEqual(c['holding_days'],3)
        self.assertEqual(r['cycle_stats']['closed_count'],1)
        self.assertEqual(r['stats']['closed_count'],2)

    def test_open_cycle_includes_partial_realized_and_unrealized(self):
        r=run([1,.5,-1.2,0],[100,120,130,110]);c=r['cycles'][0]
        self.assertAlmostEqual(c['realized_pnl'],32000)
        self.assertAlmostEqual(c['unrealized_pnl'],-3000)
        self.assertAlmostEqual(c['pnl'],29000)
        self.assertAlmostEqual(c['return_pct'],18.125)
        self.assertEqual(c['status'],'open')
        self.assertIsNone(r['stock_stats'][0]['win_rate'])

    def test_reentry_is_new_cycle_and_breakeven_in_denominator(self):
        r=run([1,-1,1,-1,1,-1,2],[100,110,100,90,100,100,100])
        self.assertEqual(len(r['cycles']),4)
        s=r['stock_stats'][0]
        self.assertEqual((s['wins'],s['losses'],s['breakeven'],s['closed_count']),(1,1,1,3))
        self.assertAlmostEqual(s['win_rate'],100/3)
        self.assertEqual(s['closed_pnl'],0)
        self.assertAlmostEqual(r['positions'][0]['win_rate'],100/3)

    def test_add_after_partial_sale_does_not_start_new_cycle(self):
        r=run([1,-.5,.5,-1],[100,120,110,130]);c=r['cycles'][0]
        self.assertEqual(len(r['cycles']),1)
        self.assertEqual(c['bought_lots'],1.5)
        self.assertAlmostEqual(c['pnl'],35000)

    def test_unknown_excess_sale_invalidates_only_its_cycle(self):
        r=run([1,-1.2,1,-1],[100,110,100,120]);old=r['cycles'][1]
        self.assertFalse(old['complete']);self.assertIsNone(old['pnl'])
        self.assertAlmostEqual(old['unmatched_lots'],.2)
        self.assertEqual(r['cycle_stats']['closed_count'],1)
        self.assertEqual(r['cycle_stats']['unscored_count'],1)
        self.assertEqual(r['stock_stats'][0]['win_rate'],100)

    def test_missing_entry_does_not_poison_next_cycle(self):
        r=run([1,-1,1,-1],[None,110,100,120])
        self.assertIsNone(r['cycles'][1]['pnl'])
        self.assertEqual(r['cycles'][0]['pnl'],20000)
        self.assertEqual(r['cycle_stats']['win_rate'],100)

    def test_missing_intermediate_exit_invalidates_total_cycle(self):
        r=run([1,-.5,-.5],[100,None,120])
        self.assertIsNone(r['cycles'][0]['pnl'])
        self.assertEqual(r['cycle_stats']['closed_count'],0)

    def test_missing_mark_preserves_realized_but_not_total(self):
        r=run([1,-.5,0],[100,120,None]);c=r['cycles'][0]
        self.assertEqual(c['realized_pnl'],10000)
        self.assertIsNone(c['unrealized_pnl']);self.assertIsNone(c['pnl'])

    def test_unverified_history_cannot_create_scored_clearance(self):
        r=run([1,-1],[100,110],verified_symbols=set())
        self.assertFalse(r['cycles'][0]['complete'])
        self.assertIsNone(r['stock_stats'][0]['win_rate'])

    def test_later_gap_does_not_remove_earlier_verified_cycle(self):
        intervals=[{'symbol':'2330','start':D[0],'end':D[1]}]
        r=run([1,-1,1,0],[100,110,100,120],verified_symbols=set(),history_intervals=intervals)
        self.assertTrue(r['cycles'][1]['complete'])
        self.assertIsNone(r['cycles'][0]['pnl'])
        self.assertEqual(r['stock_stats'][0]['win_rate'],100)

    def test_pre_entry_gap_invalidates_inferred_zero(self):
        intervals=[{'symbol':'2330','start':D[1],'end':D[2]}]
        r=run([0,1,-1],[100,100,110],history_intervals=intervals)
        self.assertFalse(r['cycles'][0]['complete'])

    def test_calendar_failure_only_suppresses_days(self):
        r=run([1,-1],[100,110],calendar_complete=False)
        self.assertIsNone(r['cycles'][0]['holding_days'])
        self.assertEqual(r['cycles'][0]['pnl'],10000)

    def test_floating_lots_close_as_breakeven(self):
        r=run([.1,.2,-.3],[100,100,100])
        self.assertEqual(r['cycles'][0]['quantity'],0)
        self.assertEqual(r['stock_stats'][0]['breakeven'],1)
        self.assertEqual(r['cycle_stats']['win_rate'],0)

    def test_tiny_residual_and_empty_inputs_do_not_crash(self):
        self.assertEqual(run([1e-10],[100])['cycles'],[])
        self.assertEqual(analyze([],[],[],D[0])['cycles'],[])

    def test_asof_never_includes_future_clearance(self):
        r=analyze([flow(D[0],1),flow(D[2],-1)],quotes(D[:3],[100,110,120]),D[:3],D[1])
        self.assertEqual(r['cycles'][0]['status'],'open')
        self.assertEqual(r['cycles'][0]['pnl'],10000)
        self.assertIsNone(r['cycle_stats']['win_rate'])

    def test_two_stocks_keep_separate_wins(self):
        flows=[flow(D[0],1,'A'),flow(D[1],-1,'A'),flow(D[0],1,'B'),flow(D[1],-1,'B')]
        r=analyze(flows,quotes(D[:2],[100,110],'A')+quotes(D[:2],[100,90],'B'),D[:2],D[1])
        self.assertEqual({s['symbol']:s['win_rate'] for s in r['stock_stats']},{'A':100,'B':0})
        self.assertEqual(r['cycle_stats']['win_rate'],50)
