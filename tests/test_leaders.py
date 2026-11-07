import tempfile
import unittest
from datetime import date,timedelta
from pathlib import Path
from unittest.mock import patch
from backend import db,leaders,sources
from backend.analytics import analyze

DAYS=[(date(2026,1,1)+timedelta(days=i)).isoformat() for i in range(25)]

class LeaderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.p=patch.object(db,'DATA',Path(self.temp.name));self.p.start();db.initialize()
    def tearDown(self):self.p.stop();self.temp.cleanup()
    def test_style_boundaries_do_not_overlap(self):
        self.assertEqual([leaders.style_key(d) for d in [0,1,2,4,5,20,21,None]],['day','overnight','quick','quick','short','short','swing','unknown'])
    def test_forward_returns_use_trading_days_equal_weights_and_positive_net_only(self):
        days=[(date(2026,1,2)+timedelta(days=i)).isoformat() for i in range(20) if (date(2026,1,2)+timedelta(days=i)).weekday()<5]
        flows=[dict(date=days[0],symbol=s,net=n) for s,n in [('A',500),('B',1),('C',20),('SELL',-1),('FLAT',0)]]
        prices={(days[i],s):p for s,values in [('A',[100,110,90]),('B',[100,90,120]),('C',[100,100,100])] for i,p in zip([0,5,10],values)}
        r=leaders.forward_stats(flows,prices,days,days[10])
        self.assertEqual(r['5']['samples'],3);self.assertEqual(r['5']['wins'],1)
        self.assertAlmostEqual(r['5']['win_rate'],100/3);self.assertAlmostEqual(r['5']['avg_return'],0)
        self.assertAlmostEqual(r['10']['avg_return'],10/3)
        self.assertEqual(r['10']['wins'],1);self.assertEqual(r['10']['total'],3)
    def test_forward_missing_prices_are_not_filled_and_future_data_is_excluded(self):
        flows=[dict(date=DAYS[i],symbol=s,net=1) for i,s in [(0,'GOOD'),(0,'MISSING'),(0,'ZERO'),(8,'RECENT'),(15,'FUTURE')]]
        prices={(DAYS[i],s):p for i,s,p in [(0,'GOOD',10),(5,'GOOD',12),(10,'GOOD',20),(0,'MISSING',10),(6,'MISSING',12),(0,'ZERO',0),(5,'ZERO',10),(8,'RECENT',10),(13,'RECENT',12)]}
        r=leaders.forward_stats(flows,prices,DAYS,DAYS[9])
        self.assertEqual((r['5']['samples'],r['5']['missing'],r['5']['pending'],r['5']['total']),(1,2,1,4))
        self.assertEqual(r['10']['pending'],4);self.assertIsNone(r['10']['win_rate']);self.assertIsNone(r['10']['avg_return'])
    def test_forward_calendar_gap_does_not_shift_maturity_or_penalize_later_buys(self):
        flows=[dict(date=DAYS[i],symbol='A',net=1) for i in [0,2,4]]
        prices={(d,'A'):10 for d in DAYS}
        r=leaders.forward_stats(flows,prices,[d for d in DAYS if d!=DAYS[2]],DAYS[-1],[DAYS[2]])
        for horizon in ['5','10']:
            self.assertEqual(r[horizon]['missing'],2);self.assertEqual(r[horizon]['samples'],1)
            self.assertEqual(r[horizon]['win_rate'],0)
        empty=leaders.forward_stats([],{},[],DAYS[-1])
        self.assertIsNone(empty['5']['win_rate']);self.assertEqual(empty['5']['total'],0)
    def test_partial_sales_count_as_events_but_one_clearance_and_turnover_is_gross(self):
        flows=[dict(date=DAYS[i],symbol=s,name=s,buy=b,sell=v,net=b-v) for i,s,b,v in [(0,'1001',110,10),(1,'1001',5,55),(4,'1001',0,50),(0,'1002',100,0),(21,'1002',0,100),(2,'1003',0,10)]]
        quotes=[dict(date=DAYS[i],symbol=s,close=p) for i,s,p in [(0,'1001',10),(1,'1001',12),(4,'1001',8),(0,'1002',10),(21,'1002',15),(2,'1003',10)]]
        result=analyze(flows,quotes,DAYS,DAYS[-1])
        row=leaders.summarize({'name':'A'},result,flows,{(r['date'],r['symbol']):r['close'] for r in quotes},DAYS[0],DAYS[-1],True,3,3)
        self.assertEqual((row['paired_wins'],row['paired_samples']),(2,3))
        self.assertEqual((row['cycle_wins'],row['cycle_samples']),(1,2))
        self.assertEqual(row['cycle_pnl'],500000)
        self.assertEqual(row['turnover'],4920000)
        self.assertEqual(row['profit_style'],'21 日以上波段')
        day=row['styles'][0];self.assertFalse(day['observable']);self.assertIsNone(day['count']);self.assertIsNone(day['pnl'])
        self.assertEqual(row['paired_unscored'],1)
    def test_style_uses_net_profit_not_only_winning_proceeds_or_frequency(self):
        cycles=[dict(complete=True,holding_days=d,pnl=p) for d,p in [(1,200),(1,-250),(5,20),(5,20),(5,20),(21,100)]]
        styles,common,profit=leaders.style_stats(cycles)
        self.assertEqual(common,'5–20 日短線');self.assertEqual(profit,'21 日以上波段')
        overnight=next(r for r in styles if r['key']=='overnight')
        self.assertEqual(overnight['pnl'],-50)
    def test_empty_samples_and_missing_prices_are_not_zero_profits(self):
        result=analyze([],[],[],DAYS[-1])
        row=leaders.summarize({},result,[],{},None,DAYS[-1],False,0,0)
        self.assertIsNone(row['paired_rate']);self.assertIsNone(row['cycle_pnl']);self.assertIsNone(row['turnover']);self.assertIsNone(row['profit_style'])
        row=leaders.summarize({},result,[dict(date=DAYS[0],symbol='A',buy=100,sell=0)],{},DAYS[0],DAYS[-1],True,0,1)
        self.assertIsNone(row['turnover']);self.assertEqual(row['unpriced_flows'],1)
    def test_report_only_active_branches_and_selected_date_and_never_fetches(self):
        with db.connect() as c:
            c.execute('DELETE FROM branches');c.executemany('INSERT INTO branches VALUES (?,?,?,?)',[('A','1','追蹤',1),('B','2','未追蹤',0)])
            for day in DAYS:c.execute('INSERT INTO market_days VALUES (?,?,?,?)',(day,'ok','ok',''))
            for day in [DAYS[1],DAYS[-1]]:c.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)',('A','1',day,'observed',1,'','test'))
            c.execute('INSERT INTO candidates VALUES (?,?,?,?)',('A','1','1001','測試'))
            c.execute('INSERT INTO history_intervals VALUES (?,?,?,?,?,?)',('A','1','1001',DAYS[0],DAYS[-1],'test'))
            for b,branch in [('A','1'),('B','2')]:
                for day,buy,sell in [(DAYS[0],10,0),(DAYS[2],0,10)]:c.execute('INSERT INTO flows VALUES (?,?,?,?,?,?,?,?,?)',(b,branch,day,'1001','測試',buy,sell,buy-sell,'test'))
            for i,day in enumerate(DAYS):c.execute('INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?)',(day,'1001','測試',10+i,None,None,None,None,'TWSE'))
        with patch.object(sources,'get',side_effect=AssertionError('must only read local DB')):
            latest=leaders.report(refresh=True);early=leaders.report(DAYS[1],refresh=True)
        self.assertEqual(len(latest['rows']),1);self.assertEqual(latest['rows'][0]['cycle_samples'],1)
        self.assertEqual(early['rows'][0]['cycle_samples'],0)
        self.assertEqual(latest['rows'][0]['forward_5_samples'],1)
        self.assertAlmostEqual(latest['rows'][0]['forward_5_return'],50)
        self.assertAlmostEqual(latest['rows'][0]['forward_10_return'],100)
        self.assertIsNone(early['rows'][0]['forward_5_rate'])
        self.assertEqual(early['rows'][0]['forward']['5']['pending'],1)
        with self.assertRaises(ValueError):leaders.report('1999-01-01')
        cached=leaders.report(DAYS[1]);cached['rows'].clear()
        self.assertEqual(len(leaders.report(DAYS[1])['rows']),1)

    def test_report_excludes_ignored_symbol_and_cache_signature_changes(self):
        with db.connect() as c:
            c.execute('DELETE FROM branches');c.execute('INSERT INTO branches VALUES (?,?,?,1)',('A','1','追蹤'))
            for day in DAYS:c.execute('INSERT INTO market_days VALUES (?,?,?,?)',(day,'ok','ok',''))
            c.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)',('A','1',DAYS[-1],'observed',1,'','test'))
            c.execute('INSERT INTO candidates VALUES (?,?,?,?)',('A','1','1001','下市股票'))
            c.execute('INSERT INTO flows VALUES (?,?,?,?,?,?,?,?,?)',('A','1',DAYS[0],'1001','下市股票',10,0,10,'test'))
            c.execute('INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?)',(DAYS[0],'1001','下市股票',10,None,None,None,None,'TWSE'))
        before=leaders.report(refresh=True)['rows'][0]
        self.assertEqual(before['candidate_stocks'],1);self.assertIsNotNone(before['turnover'])
        db.update_settings({'ignored_symbols':['1001']})
        after=leaders.report()['rows'][0]
        self.assertEqual(after['candidate_stocks'],0);self.assertIsNone(after['turnover'])

    def test_saved_report_survives_restart_and_new_data_until_manual_refresh(self):
        with db.connect() as c:
            c.execute('DELETE FROM branches');c.execute("INSERT INTO branches VALUES ('A','1','追蹤',1)")
            c.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)',('A','1',DAYS[1],'observed',0,'','test'))
        saved=leaders.report(refresh=True)
        with db.connect() as c:
            c.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)',('A','1',DAYS[2],'observed',0,'','test'))
        import importlib
        importlib.reload(leaders)
        with patch.object(leaders,'analyze',side_effect=AssertionError('saved report must not recalculate')):
            reused=leaders.report()
            self.assertEqual(reused['as_of'],DAYS[1])
            self.assertEqual(reused['computed_at'],saved['computed_at'])
            self.assertEqual(reused['source_latest_date'],DAYS[2])
            reused['rows'].clear()
            self.assertEqual(len(leaders.report()['rows']),1)
        fresh=leaders.report(refresh=True)
        self.assertEqual(fresh['as_of'],DAYS[2])
        with patch.object(leaders,'analyze',side_effect=AssertionError('historical snapshot must persist')):
            self.assertEqual(leaders.report(DAYS[1])['computed_at'],saved['computed_at'])

    def test_failed_refresh_keeps_saved_report_and_watchlist_changes_are_isolated(self):
        with db.connect() as c:
            c.execute('DELETE FROM branches');c.execute("INSERT INTO branches VALUES ('A','1','追蹤',1)")
            c.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)',('A','1',DAYS[1],'observed',0,'','test'))
        saved=leaders.report(refresh=True)
        with patch.object(leaders,'analyze',side_effect=ValueError('failed calculation')):
            with self.assertRaises(ValueError):leaders.report(refresh=True)
            self.assertEqual(leaders.report()['computed_at'],saved['computed_at'])
        with db.connect() as c:c.execute("INSERT INTO branches VALUES ('B','2','新分點',1)")
        self.assertEqual(len(leaders.report()['rows']),2)

if __name__=='__main__':unittest.main()
