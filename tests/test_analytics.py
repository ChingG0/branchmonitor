import unittest
from datetime import date,timedelta
from backend.analytics import analyze


def flow(day,net,symbol='2330'):
    return dict(date=day,symbol=symbol,name='測試股票',net=net,buy=max(0,net),sell=max(0,-net),source='fixture')


def quotes(days,values,symbol='2330'):
    return [dict(date=d,symbol=symbol,close=p,volume=100000,name='測試股票') for d,p in zip(days,values) if p is not None]


class PortfolioTests(unittest.TestCase):
    def test_fifo_multiple_lots_and_return_denominator(self):
        days=['2026-09-01','2026-09-02','2026-09-03','2026-09-04','2026-09-07']
        result=analyze([flow(days[0],1),flow(days[1],.5),flow(days[3],-1.2)],quotes(days,[100,120,125,130,110]),days,days[-1])
        self.assertAlmostEqual(result['positions'][0]['quantity'],.3)
        self.assertAlmostEqual(result['positions'][0]['cost'],36000)
        self.assertAlmostEqual(result['stats']['realized'],32000)
        self.assertAlmostEqual(result['stats']['unrealized'],-3000)
        self.assertAlmostEqual(result['stats']['return_pct'],18.125)
        self.assertEqual(result['positions'][0]['holding_days'],3)
        self.assertEqual(len(result['series']),5)
        self.assertAlmostEqual(result['series'][-1]['pnl'],result['stats']['pnl'])

    def test_unknown_opening_inventory_is_not_a_short_or_a_win(self):
        days=['2026-09-01','2026-09-02','2026-09-03']
        r=analyze([flow(days[0],-.5),flow(days[1],.2),flow(days[2],-.3)],quotes(days,[100,100,110]),days,days[-1])
        self.assertEqual(r['positions'],[])
        self.assertAlmostEqual(r['stats']['unmatched_lots'],.6)
        self.assertEqual(r['stats']['closed_count'],0)
        self.assertEqual(r['stats']['unscored_events'],2)
        self.assertIsNone(r['stats']['win_rate'])

    def test_breakeven_stays_in_win_denominator(self):
        days=['2026-09-01','2026-09-02']
        flows=[];prices=[]
        for symbol,p1 in [('A',110),('B',90),('C',100)]:
            flows.extend([flow(days[0],1,symbol),flow(days[1],-1,symbol)])
            prices.extend(quotes(days,[100,p1],symbol))
        r=analyze(flows,prices,days,days[-1])
        self.assertAlmostEqual(r['stats']['win_rate'],100/3)

    def test_missing_entry_price_keeps_quantity_and_market_value(self):
        days=['2026-09-01','2026-09-02']
        r=analyze([flow(days[0],1)],quotes(days,[None,120]),days,days[-1])
        self.assertEqual(r['positions'][0]['quantity'],1)
        self.assertIsNone(r['positions'][0]['avg_cost'])
        self.assertEqual(r['stats']['market_value'],120000)
        self.assertIsNone(r['stats']['return_pct'])

    def test_no_trade_days_are_still_marked(self):
        days=['2026-09-01','2026-09-02','2026-09-03']
        r=analyze([flow(days[0],1)],quotes(days,[100,110,120]),days,days[-1])
        self.assertEqual(len(r['series']),3)
        self.assertEqual(r['series'][-1]['pnl'],20000)
        self.assertEqual(r['series'][-1]['realized_pnl'],0)
        self.assertEqual(r['series'][-1]['unrealized_pnl'],20000)

    def test_known_realized_curve_survives_unknown_opening_sale(self):
        days=['2026-09-01','2026-09-02','2026-09-03','2026-09-04']
        flows=[flow(days[0],-.5),flow(days[1],1),flow(days[2],-1)]
        r=analyze(flows,quotes(days,[90,100,120,125]),days,days[-1])
        self.assertEqual([p['realized_pnl'] for p in r['series']],[0,0,20000,20000])
        self.assertEqual([p['unrealized_pnl'] for p in r['series']],[0,0,0,0])
        self.assertEqual(r['series'][-1]['pnl'],20000)

    def test_unrealized_curve_breaks_while_open_lot_cost_is_unknown(self):
        days=['2026-09-01','2026-09-02','2026-09-03']
        r=analyze([flow(days[0],1)],quotes(days,[None,110,120]),days,days[-1])
        self.assertEqual([p['realized_pnl'] for p in r['series']],[0,0,0])
        self.assertEqual([p['unrealized_pnl'] for p in r['series']],[None,None,None])
        self.assertEqual([p['unrealized_complete'] for p in r['series']],[False,False,False])

    def test_unrealized_curve_keeps_known_part_of_mixed_positions(self):
        days=['2026-09-01','2026-09-02']
        flows=[flow(days[0],1,'A'),flow(days[0],1,'B')]
        prices=quotes(days,[None,120],'A')+quotes(days,[100,110],'B')
        r=analyze(flows,prices,days,days[-1])
        self.assertEqual(r['series'][-1]['unrealized_pnl'],10000)
        self.assertFalse(r['series'][-1]['unrealized_complete'])

    def test_future_prices_never_mark_a_past_position(self):
        days=['2026-09-01','2026-09-02','2026-09-03']
        r=analyze([flow(days[0],1)],quotes(days,[100,None,300]),days,days[1])
        self.assertIsNone(r['positions'][0]['close'])
        self.assertIsNone(r['stats']['return_pct'])

    def test_no_sales_has_no_win_rate(self):
        r=analyze([],[],[],'2026-09-01')
        self.assertIsNone(r['stats']['win_rate'])
        self.assertIsNone(r['stats']['return_pct'])


class SignalTests(unittest.TestCase):
    def setUp(self):
        self.days=[(date(2026,8,1)+timedelta(days=i)).isoformat() for i in range(11)]
        self.prices=quotes(self.days,[100]*10+[110])+quotes(self.days,[200]*10+[210],'TAIEX')

    def test_ten_market_sessions_not_ten_observed_bars(self):
        r=analyze([flow(self.days[0],1)],self.prices,self.days,self.days[-1])
        self.assertAlmostEqual(r['signals'][0]['excess'],5)
        self.assertTrue(r['signals'][0]['hit'])
        self.assertEqual(r['hit_stats']['rate'],100)

    def test_nine_sessions_is_pending(self):
        r=analyze([flow(self.days[0],1)],self.prices,self.days,self.days[-2])
        self.assertEqual(r['hit_stats']['pending'],1)
        self.assertEqual(r['hit_stats']['scored'],0)

    def test_missing_maturity_price_does_not_extend_horizon(self):
        prices=[p for p in self.prices if not(p['symbol']=='2330' and p['date']==self.days[-1])]
        r=analyze([flow(self.days[0],1)],prices,self.days,self.days[-1])
        self.assertEqual(r['signals'][0]['status'],'missing')
        self.assertEqual(r['signals'][0]['maturity'],self.days[-1])
        self.assertEqual(r['hit_stats']['scored'],0)

    def test_calendar_failure_suppresses_horizons_and_watchlist(self):
        r=analyze([flow(self.days[0],1)],self.prices,self.days,self.days[-1],calendar_complete=False)
        self.assertIsNone(r['positions'][0]['holding_days'])
        self.assertIsNone(r['hit_stats']['rate'])
        self.assertEqual(r['watchlist'],[])

    def test_unverified_stock_does_not_enter_watchlist(self):
        r=analyze([flow(self.days[-1],1)],self.prices,self.days,self.days[-1],verified_symbols=set())
        self.assertEqual(r['watchlist'],[])


if __name__=='__main__':unittest.main()
