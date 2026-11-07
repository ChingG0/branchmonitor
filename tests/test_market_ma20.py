import copy,json,sqlite3,tempfile,unittest
from datetime import date,datetime,timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
from backend import market_ma20 as m,market_monitor,db

class MarketMa20Tests(unittest.TestCase):
    def rows(self,end=date(2026,10,28),n=21):
        dates=[];d=end
        while len(dates)<n:
            if d.weekday()<5:dates.append(d.isoformat())
            d-=timedelta(days=1)
        dates.reverse();closes=[100]+[120]+[100]*18+[90]
        result=[]
        for s,scale in [('TAIEX',1),('TPEX',.1)]:
            for day,cp in zip(dates,closes):result.append(dict(symbol=s,date=day,open=cp*scale,high=(cp+5)*scale,low=(cp-5)*scale,close=cp*scale))
        return result,dates
    def test_distance_next_ma_threshold_and_dynamic_days(self):
        rows,days=self.rows();result=m.build(rows,cutoff=days[-1]);r=result['indices'][0]
        self.assertTrue(result['both_below']);self.assertEqual(r['ma20'],100.5);self.assertEqual(r['remaining_up_points'],10.5)
        self.assertAlmostEqual(r['distance_pct'],(90/100.5-1)*100)
        self.assertAlmostEqual(r['remaining_up_pct'],(100.5/90-1)*100)
        threshold=1890/19;self.assertAlmostEqual(r['next_raw_threshold'],threshold)
        quote=r['next_close_threshold'];self.assertEqual(quote,99.48)
        self.assertGreaterEqual(quote,(1890+quote)/20)
        self.assertLess(quote-.01,(1890+quote-.01)/20)
        w=r['range_windows']['20'];self.assertEqual(w['average_points'],10);self.assertAlmostEqual(w['amplitude_units'],1.05)
        self.assertEqual([s['sessions'] for s in w['scenarios']],[2,1])
        # A static ceil(10.5/5)=3 would be wrong: rolling MA makes it two.
        self.assertEqual(m.touch_days([120]+[100]*18+[90],5),2)
    def test_average_range_percent_uses_each_previous_close(self):
        rows,days=self.rows();r=m.build(rows,cutoff=days[-1])['indices'][0]['range_windows']['20']
        previous=[100,120]+[100]*18
        self.assertAlmostEqual(r['average_pct'],sum(10/x*100 for x in previous)/20)
    def test_missing_high_low_never_becomes_close_change_or_zero(self):
        rows,days=self.rows();target=next(r for r in rows if r['symbol']=='TAIEX' and r['date']==days[-2]);target.update(high=None,low=None)
        r=m.build(rows,cutoff=days[-1])['indices'][0];w=r['range_windows']['20']
        self.assertTrue(r['ma_ready']);self.assertEqual(w['samples'],19);self.assertIsNone(w['average_points'])
        self.assertIsNone(w['scenarios'][0]['sessions']);self.assertIn('不以收盤漲跌',w['reason'])
    def test_official_ohlc_fallback_requires_same_date_close_agreement(self):
        rows,days=self.rows();target=next(r for r in rows if r['symbol']=='TAIEX' and r['date']==days[-2]);backup=dict(target);target.update(high=None,low=None,open=None)
        result=m.build(rows,cutoff=days[-1],fallback=[backup]);self.assertTrue(result['indices'][0]['range_windows']['20']['ready'])
        self.assertEqual(result['indices'][0]['ohlc_sources']['official_taiex_cache'],1)
        bad=dict(backup,close=backup['close']+1);self.assertFalse(m.build(rows,cutoff=days[-1],fallback=[bad])['indices'][0]['range_windows']['20']['ready'])
        self.assertIsNone(target['high']) # build never overwrites its input.
    def test_missing_tpex_calendar_day_does_not_compact_ma20(self):
        rows,days=self.rows();rows=[r for r in rows if not(r['symbol']=='TPEX' and r['date']==days[-3])]
        result=m.build(rows,cutoff=days[-1]);r=result['indices'][1]
        self.assertFalse(r['ma_ready']);self.assertIsNone(r['ma20']);self.assertIsNone(result['both_below'])
    def test_latest_indices_are_aligned_and_warn_on_lag(self):
        rows,days=self.rows();rows=[r for r in rows if not(r['symbol']=='TPEX' and r['date']==days[-1])]
        r=m.build(rows,cutoff=days[-1]);self.assertEqual(r['as_of'],days[-2]);self.assertTrue(r['warnings'])
        self.assertEqual(len(set(x['as_of'] for x in r['indices'])),1)
    def test_above_equal_zero_range_and_insufficient_history(self):
        self.assertEqual(m.touch_days([100]*20,0),0)
        self.assertIsNone(m.touch_days([120]+[100]*18+[90],0))
        self.assertIsNone(m.touch_days([90]*19,5))
        rows,days=self.rows()
        for r in rows:r['high']=r['low']=r['open']=r['close']
        r=m.build(rows,cutoff=days[-1])['indices'][0]['range_windows']['20'];self.assertEqual(r['average_points'],0);self.assertIsNone(r['amplitude_units']);self.assertIsNone(r['scenarios'][0]['sessions'])
    def test_read_is_offline_and_excludes_intraday_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'monitor.sqlite3';research=Path(tmp)/'absent.sqlite3';rows,days=self.rows()
            rows += [dict(symbol=s,date='2026-10-29',open=10000,high=10001,low=9999,close=10000) for s in ['TAIEX','TPEX']]
            with sqlite3.connect(path) as c:
                c.execute('CREATE TABLE prices(symbol TEXT,date TEXT,open REAL,high REAL,low REAL,close REAL)')
                c.executemany('INSERT INTO prices VALUES (:symbol,:date,:open,:high,:low,:close)',rows)
            before=path.read_bytes()
            value=m.report(path,research_path=research,now=datetime(2026,10,29,13,tzinfo=ZoneInfo('Asia/Taipei')))
            self.assertEqual(value['as_of'],'2026-10-28');self.assertEqual(value['indices'][0]['close'],90)
            self.assertEqual(before,path.read_bytes());self.assertFalse(research.exists())
            after=m.report(path,research_path=research,now=datetime(2026,10,29,16,tzinfo=ZoneInfo('Asia/Taipei')))
            self.assertEqual(after['as_of'],'2026-10-29')
    def test_dashboard_read_is_derived_without_fetch_or_publication(self):
        rows,days=self.rows();data=m.build(rows,cutoff=days[-1])
        with tempfile.TemporaryDirectory() as tmp,patch.object(db,'DATA',Path(tmp)):
            db.initialize()
            with patch.object(market_monitor.market_ma20,'report',return_value=data),patch.object(market_monitor,'fetch',side_effect=AssertionError('no fetch')):
                before=[]
                with db.connect() as c:before=list(c.execute('SELECT * FROM market_observations'))
                card=next(c for c in market_monitor.report()['cards'] if c['id']=='ma20');self.assertEqual(card['data'],data)
                with db.connect() as c:self.assertEqual(list(c.execute('SELECT * FROM market_observations')),before)

if __name__=='__main__':unittest.main()
