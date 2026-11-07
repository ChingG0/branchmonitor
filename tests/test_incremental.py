import json
import tempfile
import unittest
from datetime import date,timedelta
from pathlib import Path
from unittest.mock import patch

from backend import db,service
from backend.coverage import merge_ranges,missing_ranges

A={'broker':'9300','branch':'9339','name':'原分點'}
B={'broker':'1470','branch':'1470','name':'台灣摩根士丹利'}
START='2026-09-03'
LATEST='2026-09-09'


def trading_days(start,end):
    current=date.fromisoformat(start)
    result=[]
    while current<=date.fromisoformat(end):
        if current.weekday()<5:result.append(current.isoformat())
        current+=timedelta(days=1)
    return result


def row(day,branch):
    return dict(date=day,symbol='2330',name='台積電',buy=1,sell=0,net=1,
                source=f'https://test/zgb0.djhtm?branch={branch}&date={day}')


class ImmediateThread:
    def __init__(self,target,args,daemon):self.target,self.args=target,args
    def start(self):self.target(*self.args)


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        (self.root/'config').mkdir()
        (self.root/'config/watchlist.json').write_text(json.dumps({'branches':[A]}))
        self.latest=LATEST
        self.patches=[patch.object(db,'DATA',self.root/'data'),patch.object(db,'ROOT',self.root),
            patch.object(service.sources,'branch_catalog',return_value=[A,B]),
            patch.object(service,'today',return_value=date(2026,9,30)),
            patch.object(service.threading,'Thread',ImmediateThread),
            patch.object(service.sources,'ranking',side_effect=self.ranking),
            patch.object(service.sources,'history',side_effect=self.history),
            patch.object(service.sources,'market_prices',side_effect=self.market)]
        for p in self.patches:p.start()
        db.initialize()
        db.update_settings({'lookback_days':7,'last_sync_date':LATEST})
        with db.connect() as c:
            for day in trading_days(START,LATEST):
                c.execute("INSERT INTO market_days VALUES (?,'ok','ok','')",(day,))
                for symbol in ['2330','TAIEX']:
                    c.execute('INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?)',(day,symbol,'測試',100,100,100,100,100000,'TWSE'))
                c.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)',(A['broker'],A['branch'],day,'observed',1,'','before'))
            c.execute('INSERT INTO candidates VALUES (?,?,?,?)',(A['broker'],A['branch'],'2330','台積電'))
            service.save_flows(c,A['broker'],A['branch'],[row(START,A['branch'])])
            service.save_ranking_entries(c,A['broker'],A['branch'],LATEST,'E',[row(LATEST,A['branch'])])
            service.save_ranking_entries(c,A['broker'],A['branch'],LATEST,'B',[row(LATEST,A['branch'])])
            c.execute('INSERT INTO coverage VALUES (?,?,?,?,?,?)',(A['broker'],A['branch'],'2330',START,LATEST,'before'))
            c.execute('INSERT INTO history_intervals VALUES (?,?,?,?,?,?)',(A['broker'],A['branch'],'2330',START,LATEST,'before'))

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def ranking(self,broker,branch,day=None,refresh=False,unit='E'):
        return day or self.latest,[row(day or self.latest,branch)]

    def history(self,broker,branch,symbol,name,start,end,refresh=False):
        return [dict(row(d,branch),source='verified-history') for d in trading_days(start,end)]

    def market(self,day,market,refresh=False):
        symbols=['2330','TAIEX'] if market=='TWSE' else ['6488']
        return [dict(date=day,symbol=s,name='測試',close=100,open=100,high=100,low=100,volume=100000,market=market) for s in symbols]

    def old_rows(self):
        with db.connect() as c:
            return {table:[tuple(r) for r in c.execute(f'SELECT * FROM {table} WHERE broker=? AND branch=? ORDER BY 1,2,3',(A['broker'],A['branch']))]
                    for table in ['branches','flows','snapshots','coverage','history_intervals','candidates']}

    def test_new_morgan_sync_never_requests_or_changes_old_branch(self):
        before=self.old_rows()
        service.add_branch(B['broker'],B['branch'])
        job=service.sync(7,targets=[B])
        self.assertEqual(job['status'],'complete',job['errors'])
        self.assertEqual(len(job['scope']),1)
        self.assertEqual(job['scope'][0]['branch'],'1470')
        self.assertTrue(all(c.args[:2]==('1470','1470') for c in service.sources.ranking.call_args_list))
        service.sources.history.assert_not_called()
        service.sources.market_prices.assert_not_called()
        self.assertEqual(self.old_rows(),before)
        self.assertNotIn('last_sync_date',db.settings())

    def test_same_day_again_only_checks_latest_date_once(self):
        before=self.old_rows()
        job=service.sync(7)
        self.assertEqual(job['status'],'complete',job['errors'])
        service.sources.ranking.assert_called_once_with(A['broker'],A['branch'],refresh=True)
        service.sources.history.assert_not_called()
        service.sources.market_prices.assert_not_called()
        self.assertEqual(self.old_rows(),before)

    def test_same_day_repairs_only_missing_current_ranking_units_once(self):
        with db.connect() as c:c.execute('DELETE FROM ranking_entries WHERE date=?',(LATEST,))
        job=service.sync(7)
        self.assertEqual(job['status'],'complete',job['errors'])
        self.assertEqual(len(service.sources.ranking.call_args_list),2)
        self.assertEqual(service.sources.ranking.call_args_list[1].args[2],LATEST)
        self.assertEqual(service.sources.ranking.call_args_list[1].kwargs['unit'],'B')
        service.sources.ranking.reset_mock()
        again=service.sync(7)
        self.assertEqual(again['status'],'complete',again['errors'])
        service.sources.ranking.assert_called_once_with(A['broker'],A['branch'],refresh=True)

    def test_amount_only_ranking_is_discovered_without_becoming_fake_lot_flow(self):
        self.latest='2026-09-10'
        def rankings(broker,branch,day=None,refresh=False,unit='E'):
            actual=day or self.latest
            if unit=='B':
                return actual,[dict(row(actual,branch),symbol='9999',name='金額榜股票',buy=500000,sell=0,net=500000)]
            return actual,[row(actual,branch)]
        service.sources.ranking.side_effect=rankings
        job=service.sync(7)
        self.assertEqual(job['status'],'complete',job['errors'])
        service.sources.history.assert_not_called()
        with db.connect() as c:
            flows=[r['symbol'] for r in c.execute('SELECT symbol FROM flows WHERE date=? ORDER BY symbol',(self.latest,))]
            ranks=[tuple(r) for r in c.execute('SELECT unit,symbol FROM ranking_entries WHERE date=? ORDER BY unit,symbol',(self.latest,))]
            candidates={r['symbol'] for r in c.execute('SELECT symbol FROM candidates')}
        self.assertEqual(flows,['2330'])
        self.assertEqual(ranks,[('B','9999'),('E','2330')])
        self.assertIn('9999',candidates)

    def test_next_day_only_downloads_suffix(self):
        self.latest='2026-09-10'
        job=service.sync(7)
        self.assertEqual(job['status'],'complete',job['errors'])
        self.assertTrue(job['progress']['planned'])
        self.assertEqual(job['progress']['phase'],'complete')
        self.assertEqual({key:(value['done'],value['total']) for key,value in job['progress']['stages'].items()},
                         {'source':(1,1),'market':(2,2),'ranking':(2,2),'history':(0,0),'audit':(1,1)})
        self.assertEqual((job['done'],job['total']),(6,6))
        self.assertIsNone(job['progress']['current'])
        self.assertEqual({c.args[0] for c in service.sources.market_prices.call_args_list},{self.latest})
        service.sources.history.assert_not_called()
        with db.connect() as c:
            span=c.execute('SELECT start,end FROM coverage').fetchone()
            self.assertEqual(tuple(span),(START,LATEST))
            daily=c.execute('SELECT buy,sell,net,source FROM flows WHERE broker=? AND branch=? AND date=? AND symbol=?',
                            (A['broker'],A['branch'],self.latest,'2330')).fetchone()
            self.assertEqual(tuple(daily)[:3],(1.0,0.0,1.0))
            self.assertIn('zgb0.djhtm',daily['source'])
            saved=c.execute("SELECT unit,source FROM ranking_entries WHERE broker=? AND branch=? AND date=? ORDER BY unit",(A['broker'],A['branch'],self.latest)).fetchall()
            self.assertEqual([r['unit'] for r in saved],['B','E'])
            self.assertTrue(all('zgb0.djhtm' in r['source'] for r in saved))

    def test_longer_window_does_not_pull_dormant_history_prefix(self):
        job=service.sync(14)
        self.assertEqual(job['status'],'complete',job['errors'])
        service.sources.history.assert_not_called()
        with db.connect() as c:self.assertEqual(tuple(c.execute('SELECT start,end FROM coverage').fetchone()),(START,LATEST))

    def test_ignored_delisted_symbol_is_kept_raw_but_not_retried_or_analyzed(self):
        with db.connect() as c:before=c.execute("SELECT COUNT(*) FROM flows WHERE symbol='2330'").fetchone()[0]
        db.update_settings({'ignored_symbols':['2330']})
        job=service.sync(14)
        self.assertEqual(job['status'],'complete',job['errors'])
        service.sources.history.assert_not_called()
        dashboard=service.dashboard(A['broker'],A['branch'])
        self.assertEqual(dashboard['quality']['candidate_count'],0)
        self.assertEqual(dashboard['quality']['flow_count'],0)
        self.assertFalse(dashboard['positions']);self.assertFalse(dashboard['closed'])
        with db.connect() as c:self.assertGreaterEqual(c.execute("SELECT COUNT(*) FROM flows WHERE symbol='2330'").fetchone()[0],before)
        with self.assertRaises(ValueError):service.stock_detail(A['broker'],A['branch'],'2330')

    def test_historical_settings_do_not_enter_daily_sync(self):
        db.update_settings({'history_start_date':'2026-01-01'})
        before=self.old_rows()
        job=service.sync(7)
        self.assertEqual(job['status'],'complete',job['errors'])
        service.sources.history.assert_not_called()
        service.sources.market_prices.assert_not_called()
        self.assertFalse([c for c in service.sources.ranking.call_args_list if len(c.args)>2])
        with db.connect() as c:
            self.assertEqual([tuple(r) for r in c.execute('SELECT * FROM snapshots WHERE date>=? ORDER BY 1,2,3',(START,))],before['snapshots'])
        service.sources.history.reset_mock();service.sources.ranking.reset_mock();service.sources.market_prices.reset_mock()
        again=service.sync(7)
        self.assertEqual(again['status'],'complete',again['errors'])
        service.sources.history.assert_not_called();service.sources.market_prices.assert_not_called()
        service.sources.ranking.assert_called_once_with(A['broker'],A['branch'],refresh=True)

    def test_fixed_start_rejects_bad_dates_and_accepts_empty(self):
        for value in [True,20260101,'20260101','2026-02-30','2027-01-01','1999-12-31']:
            with self.assertRaises(ValueError):service.sync(7,start_date=value)
        self.assertIsNone(service.validate_history_start(''))
        self.assertIsNone(service.validate_history_start(None))
        self.assertEqual(service.validate_history_start('2026-01-01'),'2026-01-01')

    def test_legacy_start_date_does_not_expand_daily_new_branch_scope(self):
        db.update_settings({'history_start_date':'2026-01-01'})
        before=self.old_rows();service.add_branch(B['broker'],B['branch'])
        job=service.sync(7,targets=[B],start_date='2026-08-31')
        self.assertEqual(job['status'],'complete',job['errors'])
        self.assertEqual(self.old_rows(),before)
        service.sources.history.assert_not_called()

    def test_internal_archive_gap_is_left_for_repair_queue_not_daily_sync(self):
        with db.connect() as c:
            c.execute('DELETE FROM history_intervals')
            c.execute('DELETE FROM coverage')
            c.execute('DELETE FROM flows')
            for begin,end in [('2026-09-04','2026-09-04'),('2026-09-08','2026-09-08')]:
                c.execute('INSERT INTO history_intervals VALUES (?,?,?,?,?,?)',(A['broker'],A['branch'],'2330',begin,end,'before'))
        job=service.sync(7)
        self.assertEqual(job['status'],'complete',job['errors'])
        service.sources.history.assert_not_called()
        with db.connect() as c:verified=[dict(r) for r in c.execute('SELECT start,end FROM history_intervals')]
        self.assertEqual(missing_ranges(START,LATEST,verified),[
            ('2026-09-03','2026-09-03'),('2026-09-05','2026-09-07'),('2026-09-09','2026-09-09')])

    def test_daily_sync_never_opens_per_stock_history(self):
        with db.connect() as c:
            c.execute('DELETE FROM history_intervals')
            c.execute('DELETE FROM coverage')
            c.execute('DELETE FROM flows')
        service.sources.history.return_value=[]
        service.sources.history.side_effect=None
        job=service.sync(7)
        self.assertEqual(job['status'],'complete',job['errors'])
        service.sources.history.assert_not_called()
        job=service.sync(7)
        service.sources.history.assert_not_called()

    def test_targets_empty_inactive_duplicate_or_bad_type_rejected(self):
        for targets in [[],[B],[A,A],'all',[{'broker':1,'branch':2}]]:
            with self.assertRaises(ValueError):service.sync(7,targets=targets)
        service.sources.ranking.assert_not_called()

    def test_forced_empty_market_preserves_verified_calendar_and_reports_error(self):
        service.sources.market_prices.return_value=[]
        service.sources.market_prices.side_effect=None
        job=service.sync(7,force=True)
        self.assertEqual(job['status'],'partial')
        with db.connect() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM market_days WHERE twse_status='ok'").fetchone()[0],5)

    def test_daily_job_marks_latest_complete_independent_of_repair_window(self):
        db.update_settings({'lookback_days':45})
        with db.connect() as c:c.execute("DELETE FROM settings WHERE key='last_sync_date'")
        self.assertEqual(service.sync(7)['status'],'complete')
        self.assertEqual(db.settings()['last_sync_date'],LATEST)

    def test_old_market_and_snapshot_gaps_are_left_for_repair(self):
        with db.connect() as c:
            c.execute("INSERT OR REPLACE INTO market_days VALUES ('2026-09-01','error','error','old')")
            c.execute('INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?,?,?)',
                      (A['broker'],A['branch'],'2026-09-01','error',0,'old','before'))
        job=service.sync(7)
        self.assertEqual(job['status'],'complete',job['errors'])
        service.sources.market_prices.assert_not_called()
        self.assertFalse([call for call in service.sources.ranking.call_args_list if len(call.args)>2])
        with db.connect() as c:
            self.assertEqual(c.execute("SELECT twse_status FROM market_days WHERE date='2026-09-01'").fetchone()[0],'error')
            self.assertEqual(c.execute("SELECT status FROM snapshots WHERE date='2026-09-01'").fetchone()[0],'error')

    def test_snapshot_sync_extends_only_after_last_observed_day(self):
        with db.connect() as c:
            c.execute("UPDATE snapshots SET status='error' WHERE date='2026-09-03'")
            c.execute("DELETE FROM snapshots WHERE date>='2026-09-08'")
        job=service.sync(7)
        self.assertEqual(job['status'],'complete',job['errors'])
        explicit=[call.args[2] for call in service.sources.ranking.call_args_list if len(call.args)>2]
        self.assertEqual(explicit,['2026-09-09','2026-09-08','2026-09-08'])
        with db.connect() as c:
            self.assertEqual(c.execute("SELECT status FROM snapshots WHERE date='2026-09-03'").fetchone()[0],'error')

    def test_force_rechecks_latest_only(self):
        job=service.sync(7,force=True)
        self.assertEqual(job['status'],'complete',job['errors'])
        self.assertEqual({call.args[0] for call in service.sources.market_prices.call_args_list},{LATEST})
        self.assertTrue(all(len(call.args)<3 or call.args[2]==LATEST for call in service.sources.ranking.call_args_list))
        service.sources.history.assert_not_called()

    def test_latest_quotes_not_published_cannot_mark_latest_complete(self):
        self.latest='2026-09-10'
        service.sources.market_prices.side_effect=None
        service.sources.market_prices.return_value=[]
        job=service.sync(7)
        self.assertEqual(job['status'],'partial')
        self.assertEqual(db.settings()['last_sync_date'],LATEST)
        self.assertIn('尚有缺項',job['message'])
        self.assertIn('回補已結束',job['message'])
        self.assertNotIn('待重試',job['message'])

    def test_past_view_uses_earlier_interval_when_latest_interval_has_gap(self):
        with db.connect() as c:
            c.execute('DELETE FROM history_intervals')
            for begin,end in [(START,'2026-09-04'),('2026-09-08',LATEST)]:
                c.execute('INSERT INTO history_intervals VALUES (?,?,?,?,?,?)',(A['broker'],A['branch'],'2330',begin,end,'before'))
            c.execute("UPDATE coverage SET start='2026-09-08'")
        past=service.dashboard(A['broker'],A['branch'],as_of=START)
        self.assertEqual(past['quality']['history_count'],1)
        current=service.dashboard(A['broker'],A['branch'],as_of=LATEST)
        self.assertEqual(current['quality']['history_count'],0)


class RangeTests(unittest.TestCase):
    def test_prefix_internal_suffix(self):
        self.assertEqual(missing_ranges('2026-07-25','2026-08-25',[
            {'start':'2026-08-01','end':'2026-08-10'},
            {'start':'2026-08-15','end':'2026-08-20'}]),[
                ('2026-07-25','2026-07-31'),('2026-08-11','2026-08-14'),('2026-08-21','2026-08-25')])

    def test_adjacent_overlap_and_force(self):
        ranges=[{'start':'2026-08-01','end':'2026-08-10'},{'start':'2026-08-08','end':'2026-08-15'},{'start':'2026-08-16','end':'2026-08-20'}]
        self.assertEqual(merge_ranges(ranges),[{'start':'2026-08-01','end':'2026-08-20'}])
        self.assertEqual(missing_ranges('2026-08-03','2026-08-10',ranges),[])
        self.assertEqual(missing_ranges('2026-08-03','2026-08-10',ranges,True),[('2026-08-03','2026-08-10')])

if __name__=='__main__':unittest.main()
