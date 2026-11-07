import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from backend import db,major,sources

DAY='2026-09-10';OLD='2026-09-09'
FIXTURES=Path(__file__).parent/'fixtures'

def fixture(side='F',market='0'):
    return (FIXTURES/f'major_{side}_{market}.html').read_text()

class ParseTests(unittest.TestCase):
    def test_four_actual_rankings_include_flat_price_rows_and_all_fifty(self):
        for side in ['F','FA']:
            for market in ['0','1']:
                rows=major.parse_market(fixture(side,market),DAY,market,side)
                self.assertEqual(len(rows),50)
                self.assertEqual(rows[-1]['rank'],50)
        row=next(r for r in major.parse_market(fixture(),DAY,'0','F') if r['symbol']=='2883')
        self.assertEqual(row['buy']-row['sell'],11236)
        self.assertEqual(row['net'],11235)

    def test_mismatched_day_market_direction_or_horizon_is_rejected(self):
        for raw,day,market,side in [(fixture(),OLD,'0','F'),(fixture(),DAY,'1','F'),(fixture(),DAY,'0','FA'),(fixture().replace("value == '001'","value == '005'"),DAY,'0','F')]:
            with self.assertRaises(sources.SourceError):major.parse_market(raw,day,market,side)

    def test_missing_price_or_truncated_page_never_looks_complete(self):
        with self.assertRaises(sources.SourceError):major.parse_market(fixture().replace('73.50','---',1),DAY,'0','F')
        with self.assertRaises(sources.SourceError):major.parse_market(fixture().split('id="oAddCheckbox">10')[0],DAY,'0','F')

class ImmediateThread:
    def __init__(self,target,daemon):self.target=target
    def start(self):self.target()

class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.patches=[patch.object(db,'DATA',self.root/'data'),patch.object(db,'ROOT',self.root)]
        for p in self.patches:p.start()
        db.initialize()
        with db.connect() as c:
            c.execute('DELETE FROM branches')
            c.executemany('INSERT INTO branches VALUES (?,?,?,?)',[('A','1','追蹤甲',1),('B','2','追蹤乙',1),('C','3','未追蹤',0)])
            for b,branch in [('A','1'),('B','2'),('C','3')]:
                c.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)',(b,branch,DAY,'observed',0,'','test'))

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def test_official_market_day_can_be_newer_than_delayed_branch_probe(self):
        with db.connect() as c:
            c.execute('INSERT INTO market_days VALUES (?,?,?,?)',(DAY,'ok','ok','official'))
            c.execute('INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?)',(DAY,'TAIEX','加權',100,99,101,98,None,'TWSE'))
        self.assertEqual(major.ranking_date(OLD),(DAY,DAY))
        # MM/DD must still match that independently verified full date.
        self.assertEqual(len(major.parse_market(fixture(),major.ranking_date(OLD)[0],'0','F')),50)
        with self.assertRaises(sources.SourceError):major.parse_market(fixture().replace('09/10','09/09'),major.ranking_date(OLD)[0],'0','F')

    def test_incomplete_or_unpriced_market_day_cannot_advance_probe(self):
        with db.connect() as c:c.execute('INSERT INTO market_days VALUES (?,?,?,?)',(DAY,'ok','no_data',''))
        self.assertEqual(major.ranking_date(OLD),(OLD,None))
        with db.connect() as c:c.execute("UPDATE market_days SET tpex_status='ok'")
        self.assertEqual(major.ranking_date(OLD),(OLD,None))

    def add(self,b,branch,symbol,buy,sell,net,day=DAY,price=100,verified=True):
        with db.connect() as c:
            c.execute('INSERT INTO flows VALUES (?,?,?,?,?,?,?,?,?)',(b,branch,day,symbol,'股票'+symbol,buy,sell,net,'test'))
            c.execute('INSERT OR IGNORE INTO candidates VALUES (?,?,?,?)',(b,branch,symbol,'股票'+symbol))
            if verified:c.execute('INSERT OR IGNORE INTO history_intervals VALUES (?,?,?,?,?,?)',(b,branch,symbol,day,day,'test'))
            if price is not None:c.execute('INSERT OR IGNORE INTO prices VALUES (?,?,?,?,?,?,?,?,?)',(day,symbol,'股票'+symbol,price,price,price,price,100000,'TWSE'))

    def test_circle_uses_cached_five_day_pool_and_only_the_selected_date(self):
        self.add('A','1','2330',500,0,500)
        payload={'top':[{'name':'前三名之一'}],'start':'2026-09-04','end':DAY,'pool_balance':{'buy':1000,'sell':1500,'net':-500,'branches':30}}
        with db.connect() as c:
            c.execute('INSERT INTO major_turnover VALUES (?,?,?,?,?,?,?)',('2330',DAY,'complete','test',json.dumps(payload),'test','test'))
            c.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)',('A','1',OLD,'observed',0,'','test'))
        self.add('A','1','2330',500,0,500,day=OLD)
        row=major.report(DAY)['watched']['buy_top'][0]
        self.assertEqual(row['net'],500)
        self.assertEqual(row['major_balance']['net'],-500)
        self.assertEqual(row['major_balance']['branches'],30)
        self.assertIsNone(major.report(OLD)['watched']['buy_top'][0]['major_balance'])

    def test_aggregate_offsets_branch_sides_and_uses_published_net(self):
        self.add('A','1','2330',500,0,500)
        self.add('B','2','2330',0,200,-200)
        self.add('C','3','2330',9999,0,9999)
        self.add('A','1','2883',5,1,3)
        d=major.report(DAY)
        row=d['watched']['buy_top'][0]
        self.assertEqual((row['symbol'],row['buy'],row['sell'],row['net']),('2330',500,200,300))
        self.assertEqual(row['net_value'],30000000)
        self.assertEqual((row['buy_branches'],row['sell_branches']),(1,1))
        self.assertEqual(len(row['contributors']),2)
        self.assertEqual(d['watched']['totals']['net'],303)
        self.assertEqual(d['watched']['totals']['total_branches'],2)
        self.assertEqual(d['watched']['totals']['ready_branches'],2)

    def test_amount_ranking_uses_net_lots_times_same_day_close(self):
        self.add('A','1','1111',100,0,100,price=10)
        self.add('A','1','2222',50,0,50,price=100)
        d=major.report(DAY)
        self.assertEqual([r['symbol'] for r in d['watched']['buy_top'][:2]],['1111','2222'])
        self.assertEqual([r['symbol'] for r in d['watched']['buy_value_top'][:2]],['2222','1111'])
        self.assertEqual(d['watched']['buy_value_top'][0]['net_value'],5000000)

    def test_ignored_symbol_is_absent_from_watched_totals_and_completeness(self):
        self.add('A','1','2330',500,0,500)
        self.add('A','1','6806',900,0,900,verified=False)
        db.update_settings({'ignored_symbols':['6806']})
        d=major.report(DAY)
        self.assertEqual([r['symbol'] for r in d['watched']['buy_top']],['2330'])
        self.assertEqual(d['watched']['totals']['net'],500)
        branch=next(r for r in d['watched']['branches'] if r['broker']=='A')
        self.assertEqual((branch['verified'],branch['candidates'],branch['status']),(1,1,'ready'))

    def test_missing_branch_is_not_zero_and_stale_day_is_not_carried(self):
        self.add('A','1','2330',500,0,500)
        self.add('B','2','2330',0,200,-200,day=OLD)
        with db.connect() as c:c.execute("DELETE FROM snapshots WHERE broker='B'")
        d=major.report(DAY)
        b=next(r for r in d['watched']['branches'] if r['broker']=='B')
        self.assertIsNone(b['net']);self.assertEqual(b['status'],'missing')
        self.assertEqual(d['watched']['buy_top'][0]['net'],500)
        self.assertEqual(d['watched']['totals']['observed_branches'],1)

    def test_missing_current_price_is_not_replaced_with_future_quote(self):
        self.add('A','1','2330',5,0,5,price=None,verified=False)
        with db.connect() as c:c.execute('INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?)',('2026-09-11','2330','股票',999,999,999,999,10,'TWSE'))
        d=major.report(DAY)
        self.assertIsNone(d['watched']['buy_top'][0]['close'])
        self.assertIsNone(d['watched']['totals']['net_value'])
        self.assertEqual(next(b for b in d['watched']['branches'] if b['broker']=='A')['status'],'partial')

    def test_top_30_applies_after_netting_and_no_zero_rows_in_either_side(self):
        for i in range(40):
            self.add('A','1',str(2000+i),i+1,0,i+1)
            self.add('B','2',str(3000+i),0,i+1,-i-1)
        self.add('A','1','4444',1000,0,1000);self.add('B','2','4444',0,1000,-1000)
        d=major.report(DAY)
        self.assertEqual(len(d['watched']['buy_top']),30);self.assertEqual(len(d['watched']['sell_top']),30)
        self.assertEqual(d['watched']['buy_top'][0]['symbol'],'2039')
        self.assertEqual(d['watched']['sell_top'][0]['symbol'],'3039')
        self.assertNotIn('4444',[r['symbol'] for r in d['watched']['buy_top']+d['watched']['sell_top']])

    def test_untracked_dates_and_invalid_dates_are_excluded(self):
        with db.connect() as c:c.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)',('C','3','2026-09-11','observed',0,'','test'))
        self.assertNotIn('2026-09-11',major.report()['available_dates'])
        with self.assertRaises(ValueError):major.report('2026-09-11')

    def refresh(self,fail=False):
        def fetch(url,refresh=False):
            self.assertTrue(refresh)
            parts=url.split('/')[-1].split('_');side,market=parts[1:3]
            if fail and side=='FA' and market=='1':raise sources.SourceError('failed fourth page')
            return fixture(side,market)
        with patch.object(major.sources,'ranking',return_value=(DAY,[])),patch.object(major.sources,'get',side_effect=fetch),patch.object(major.threading,'Thread',ImmediateThread):
            return major.start_refresh()

    def test_global_merges_markets_and_does_not_change_watchlist_or_flows(self):
        self.add('A','1','2330',500,0,500)
        with db.connect() as c:before=[tuple(r) for r in c.execute('SELECT * FROM flows')]
        self.assertEqual(self.refresh()['status'],'complete')
        d=major.report(DAY)
        self.assertTrue(d['market']['available']);self.assertEqual(len(d['market']['buy_top']),30);self.assertEqual(len(d['market']['sell_top']),30)
        self.assertEqual(len(d['market']['buy_value_top']),30);self.assertEqual(len(d['market']['sell_value_top']),30)
        self.assertIsNotNone(d['market']['buy_value_top'][0]['net_value'])
        self.assertEqual(d['market']['buy_top'][0]['symbol'],'6770');self.assertEqual(d['market']['sell_top'][0]['symbol'],'00406A')
        self.assertEqual(len(d['market']['sources']),4)
        self.assertIn('上櫃',{r['market'] for r in d['market']['buy_top']})
        with db.connect() as c:
            self.assertEqual(before,[tuple(r) for r in c.execute('SELECT * FROM flows')])
            self.assertEqual(c.execute('SELECT COUNT(*) FROM branches WHERE active=1').fetchone()[0],2)

    def test_failed_one_market_preserves_previous_complete_snapshot(self):
        self.refresh()
        with db.connect() as c:before=tuple(c.execute('SELECT * FROM major_days').fetchone())
        self.assertEqual(self.refresh(fail=True)['status'],'failed')
        with db.connect() as c:self.assertEqual(before,tuple(c.execute('SELECT * FROM major_days').fetchone()))
        self.assertTrue(major.report(DAY)['market']['available'])

    def test_past_date_without_saved_global_data_is_explicitly_missing(self):
        self.refresh()
        with db.connect() as c:c.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)',('A','1',OLD,'observed',0,'','test'))
        d=major.report(OLD)
        self.assertFalse(d['market']['available']);self.assertEqual(d['market']['buy_top'],[])
        self.assertEqual(d['market']['latest_date'],DAY)

    def test_scheduler_skips_disabled_completed_and_already_attempted_hour(self):
        current=datetime(2026,9,10,19,0,tzinfo=major.TZ)
        with patch.object(major,'start_refresh') as refresh:
            major.schedule_tick(current,{'schedule_enabled':False});refresh.assert_not_called()
            major.schedule_tick(current,{'schedule_enabled':True,'schedule_time':'20:00'});refresh.assert_not_called()
        self.refresh()
        with patch.object(major,'start_refresh') as refresh:
            major.schedule_tick(current,{'schedule_enabled':True,'schedule_time':'18:30'});refresh.assert_not_called()
        with db.connect() as c:
            c.execute('DELETE FROM major_days')
            c.execute("UPDATE major_state SET status='failed',started_at=?",(current.isoformat(),))
        with patch.object(major,'start_refresh') as refresh:
            major.schedule_tick(current,{'schedule_enabled':True,'schedule_time':'18:30'});refresh.assert_not_called()

    def test_empty_store_has_no_invented_trades(self):
        with db.connect() as c:c.execute('DELETE FROM snapshots')
        d=major.report();self.assertIsNone(d['as_of'])
        self.assertIsNone(d['watched']['totals']['net']);self.assertFalse(d['market']['available'])
