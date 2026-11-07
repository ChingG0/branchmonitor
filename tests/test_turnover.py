import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend import db,sources,turnover

DAYS=['2026-09-04','2026-09-07','2026-09-08','2026-09-09','2026-09-10']
RAW=(Path(__file__).parent/'fixtures/zco-range.html').read_text()

class TurnoverTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.p=patch.object(db,'DATA',Path(self.temp.name));self.p.start();db.initialize()
        with db.connect() as c:
            for day in DAYS:c.execute('INSERT INTO market_days VALUES (?,?,?,?)',(day,'ok','ok',''))
    def tearDown(self):
        self.p.stop();self.temp.cleanup()

    def test_real_pool_dates_identity_and_rounded_net(self):
        rows=turnover.parse_pool(RAW,'6770',DAYS[0],DAYS[-1])
        self.assertEqual(len(rows),30)
        self.assertEqual(rows[0]['name'],'美商高盛')
        self.assertEqual(rows[0]['buy'],65576)
        self.assertEqual(rows[1]['net'],-5987)
        for symbol,start,end in [('2330',DAYS[0],DAYS[-1]),('6770',DAYS[1],DAYS[-1]),('6770',DAYS[0],DAYS[-2])]:
            with self.assertRaises(sources.SourceError):turnover.parse_pool(RAW,symbol,start,end)
        import re
        truncated=re.sub(r'<tr>\s*<td class="t4t1" nowrap><a.*?</tr>','',RAW,count=1,flags=re.S|re.I)
        self.assertTrue(len(truncated)<len(RAW))
        with self.assertRaises(sources.SourceError):turnover.parse_pool(truncated,'6770',DAYS[0],DAYS[-1])

    def test_turnover_uses_daily_prices_and_both_sides_not_net_or_final_close(self):
        b=dict(broker='A',branch='1',name='甲',buy=500,sell=500,net=0)
        h=[dict(date=DAYS[0],buy=500,sell=0),dict(date=DAYS[-1],buy=0,sell=500)]
        r=turnover.estimate(b,h,{DAYS[0]:100,DAYS[-1]:110},DAYS)
        self.assertEqual(r['turnover'],105000000)
        self.assertEqual(r['sell_amount'],55000000)
        for prices,history in [({DAYS[0]:100},h),({DAYS[0]:100},h[:1])]:
            with self.assertRaises(sources.SourceError):turnover.estimate(b,history,prices,DAYS)

    def test_five_trading_sessions_weekend_and_missing_calendar(self):
        self.assertEqual(turnover.trading_window(DAYS[-1]),DAYS)
        with db.connect() as c:
            c.execute("UPDATE market_days SET twse_status='error' WHERE date=?",(DAYS[2],))
        with self.assertRaises(sources.SourceError):turnover.trading_window(DAYS[-1])

    def seed_job(self,status='queued',payload=None):
        with db.connect() as c:c.execute('INSERT INTO major_turnover VALUES (?,?,?,?,?,?,?)',('6770',DAYS[-1],status,'test',json.dumps(payload) if payload else None,'test',None))

    def test_complete_cache_never_fetches_or_resyncs(self):
        self.seed_job('complete',{'top':[]})
        with patch.object(turnover.major,'report',side_effect=AssertionError('no reload')),patch.object(sources,'get',side_effect=AssertionError('no network')):
            self.assertEqual(turnover.start('6770',DAYS[-1])['status'],'complete')

    def test_failure_publishes_no_partial_leaderboard(self):
        self.seed_job()
        with patch.object(sources,'get',return_value=RAW),patch.object(sources,'history',side_effect=sources.SourceError('broken history')):
            turnover._fetch('6770',DAYS[-1])
        r=turnover.status('6770',DAYS[-1]);self.assertEqual(r['status'],'failed');self.assertIsNone(r['payload'])

    def test_full_worker_gross_sort_and_cache_is_separate_from_watched_flows(self):
        self.seed_job()
        pool=[dict(broker='1000',branch=str(1000+i),name=str(i),buy=i*10,sell=i*8,net=i*2) for i in range(1,5)]
        with db.connect() as c:c.execute('INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?)',(DAYS[-1],'6770','力積電',50,50,50,50,1000,'TWSE'))
        def history(broker,branch,*args,**kwargs):
            i=int(branch)-1000
            return [dict(date=DAYS[-1],buy=i*10,sell=i*8)]
        with patch.object(sources,'get',return_value=RAW),patch.object(turnover,'parse_pool',return_value=pool),patch.object(sources,'history',side_effect=history):
            turnover._fetch('6770',DAYS[-1])
        r=turnover.status('6770',DAYS[-1]);self.assertEqual(r['status'],'complete')
        self.assertEqual([v['name'] for v in r['payload']['top']],['4','3','2'])
        self.assertEqual(r['payload']['top'][0]['turnover'],3600000)
        self.assertEqual(r['payload']['pool_balance'],{'buy':100,'sell':80,'net':20,'branches':4})
        with db.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM flows').fetchone()[0],0)

    def test_balance_offsets_all_branches_not_only_buyers_or_top_three(self):
        self.assertEqual(turnover.pool_balance([{'buy':500,'sell':0},{'buy':0,'sell':700}]),{'buy':500,'sell':700,'net':-200,'branches':2})
        self.assertEqual(turnover.pool_balance([{'buy':500,'sell':500}])['net'],0)

    def test_invalid_requests_and_outside_ranking_are_rejected(self):
        for symbol,end in [('..','2026-09-10'),('6770','2026-02-30'),(6770,DAYS[-1]),('6770',None)]:
            with self.assertRaises(ValueError):turnover.status(symbol,end)
        empty={s:{'buy_top':[],'sell_top':[]} for s in ['market','watched']}
        with patch.object(turnover.major,'report',return_value=empty):
            with self.assertRaises(ValueError):turnover.start('6770',DAYS[-1])

    def test_restart_preserves_complete_and_marks_pending_interrupted(self):
        self.seed_job();db.initialize()
        self.assertEqual(turnover.status('6770',DAYS[-1])['status'],'interrupted')

    def test_rs_only_date_can_request_clicked_stock_without_branch_sync(self):
        payload={'groups':{'20':[{'symbol':'6770','rs':90}]},'trading_days':DAYS}
        with db.connect() as c:c.execute('INSERT INTO rs_days VALUES (?,?,?)',(DAYS[-1],json.dumps(payload),'test'))
        with patch.object(turnover.major,'report',side_effect=ValueError('not a branch date')),patch.object(turnover.threading,'Thread') as thread:
            self.assertEqual(turnover.start('6770',DAYS[-1])['status'],'queued')
            self.assertEqual(thread.return_value.start.call_count,1)
        self.assertEqual(turnover.trading_window(DAYS[-1]),DAYS)

    def test_watched_stock_outside_top_thirty_can_be_opened_on_demand(self):
        with db.connect() as c:
            b=c.execute('SELECT broker,branch FROM branches LIMIT 1').fetchone()
            c.execute('INSERT INTO flows VALUES (?,?,?,?,?,?,?,?,?)',(b['broker'],b['branch'],DAYS[-1],'6770','力積電',10,0,10,'test'))
        empty={s:{'buy_top':[],'sell_top':[]} for s in ['market','watched']}
        with patch.object(turnover.major,'report',return_value=empty),patch.object(turnover.threading,'Thread'):
            self.assertEqual(turnover.start('6770',DAYS[-1])['status'],'queued')

if __name__=='__main__':unittest.main()
