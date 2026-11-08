import gzip
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend import db, forward_pe_store as S, forward_pe_valuation as V, forward_pe_refresh as F, forward_pe_dashboard as D, forward_pe_river as R
from tests.test_forward_pe_valuation import fixture

NOW='2026-11-08T23:00:00+08:00'

class ManualFreezeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.patches=[patch.object(db,'DATA',self.root),patch.object(S,'now',return_value=NOW),patch.object(F.margin_statistics,'folder',return_value=self.root/'margin')]
        for p in self.patches:p.start()
        S.initialize();m,q,e=fixture();self.quotes=list(q.values());self.estimates=e
        src=S.source('fixture','licensed_export','https://source.example/v1',b'original')
        self.universe=S.universe(src,'2026-10-30',m)
        self.eids=S.estimate_batch(src,e[:1]);self.qids=S.quote_batch(src,self.quotes)
        self.snap=S.snapshot(self.universe,self.qids,self.eids);self.saved=V.save(snapshot_id=self.snap)
        self.original=Path(self.saved['report_path']).read_bytes()
        self.refs={};(self.root/'margin/raw').mkdir(parents=True)
        for kind in ('quotes','shares'):
            raw=kind.encode();sha=hashlib.sha256(raw).hexdigest();rel='raw/'+sha+'.gz'
            (self.root/'margin'/rel).write_bytes(gzip.compress(raw))
            self.refs[kind]={'id':kind,'day':'2026-11-06','market':'TWSE','kind':kind,'sha256':sha,'retrieved_at':NOW,'row_count':50,'raw_path':rel}
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def freeze(self,rows=None,refs=None):
        with patch.object(F,'newest_quote_day',return_value='2026-11-06'),patch.object(F.P,'local_quotes',return_value=('2026-11-06',rows or self.quotes,refs or self.refs)) as reader:
            result=F.freeze();reader.assert_called_once_with(sorted(r['symbol'] for r in self.quotes),day='2026-11-06');return result
    def test_imported_eps_enter_new_snapshot_without_changing_prior_snapshot(self):
        src=S.source('export','licensed_export','https://source.example/v2',b'imported')
        ids=S.estimate_batch(src,self.estimates[1:])
        out=self.freeze();report=V.read(out['id'])
        self.assertEqual(set(report['input_estimate_ids']),set(ids+self.eids))
        self.assertEqual(report['periods'][0]['groups'][0]['qualified']['names'],50)
        self.assertEqual(report['information_cutoff'],NOW)
        self.assertFalse(out['network_downloaded'])
        self.assertEqual(Path(self.saved['report_path']).read_bytes(),self.original)
        self.assertEqual(S.read_snapshot(self.snap)['payload']['estimate_ids'],self.eids)
    def test_repeated_freeze_is_not_another_history_month(self):
        self.freeze();self.freeze()
        r=R.report();self.assertEqual(r['saved_calculations'],3)
        self.assertEqual(len({p['month'] for p in r['series'][0]['windows']['5']['points'] if p.get('report_id')}),1)
    def test_missing_latest_shares_never_falls_back_or_changes_pointer(self):
        pointer=(S.home()/'calculations/latest.json').read_bytes()
        with patch.object(F,'newest_quote_day',return_value='2026-11-09'),patch.object(F.P,'local_quotes',side_effect=ValueError('兩種來源日期未對齊')) as reader:
            with self.assertRaises(ValueError):F.freeze()
            self.assertEqual(reader.call_args.kwargs,{'day':'2026-11-09'})
        self.assertEqual((S.home()/'calculations/latest.json').read_bytes(),pointer)
    def test_incomplete_latest_prices_and_wrong_source_identity_stop_before_write(self):
        before=(S.home()/'observations.sqlite3').read_bytes()
        for rows,refs in [(self.quotes[:-1],self.refs),(self.quotes,{**self.refs,'shares':{**self.refs['shares'],'day':'2026-11-05'}})]:
            with self.assertRaises(ValueError):self.freeze(rows,refs)
        self.assertEqual((S.home()/'observations.sqlite3').read_bytes(),before)
    def test_corrupt_parent_raw_stops_before_pointer_change(self):
        pointer=(S.home()/'calculations/latest.json').read_bytes()
        (self.root/'margin'/self.refs['quotes']['raw_path']).write_bytes(gzip.compress(b'corrupt'))
        with self.assertRaises(ValueError):self.freeze()
        self.assertEqual((S.home()/'calculations/latest.json').read_bytes(),pointer)
    def test_no_forecasts_still_preserves_fifty_stock_rows(self):
        snap=S.snapshot(self.universe,self.qids,[])
        report=V.compute(snapshot_id=snap)
        self.assertEqual(len(report['basket_rows']),50)
        self.assertTrue(all(r['estimate'] is None for r in report['basket_rows']))
        self.assertTrue(all(p['groups']==[] for p in report['periods']))
    def test_newest_quote_does_not_choose_an_older_common_share_day(self):
        with sqlite3.connect(self.root/'margin/sources.sqlite3') as c:
            c.execute('CREATE TABLE latest(market,kind,day)')
            c.executemany('INSERT INTO latest VALUES(?,?,?)',[('TWSE','quotes','2026-11-05'),('TWSE','shares','2026-11-05'),('TWSE','quotes','2026-11-06'),('TPEX','quotes','2026-11-08'),('TWSE','quotes','2026-11-09')])
        self.assertEqual(F.newest_quote_day(),'2026-11-06')
    def test_future_estimate_does_not_enter_snapshot(self):
        src=S.source('export','licensed_export','https://source.example/v2',b'future')
        # Artificial future record verifies a bad legacy row is never selected.
        with S.connect(True) as c:
            c.execute('INSERT INTO estimates VALUES(?,?,?,?,?,?,?,?,?)',('future',src,'1001','calendar_year','2026','median','2026-11-09T18:00:00+08:00','2026-11-09T17:00:00+08:00',S.canonical(self.estimates[1])))
        out=self.freeze();self.assertNotIn('future',V.read(out['id'])['input_estimate_ids'])

class DashboardTests(unittest.TestCase):
    def test_valuation_failure_does_not_hide_sectors(self):
        with patch.object(D.R,'report',side_effect=ValueError('corrupt SHA')),patch.object(D.C,'report',return_value={'date':'2026-11-06','rows':[1]}),patch.object(D,'latest_market_day',return_value='2026-11-06'):
            r=D.report()
        self.assertEqual(r['sectors']['rows'],[1]);self.assertIsNone(r['river']['valuation']);self.assertTrue(r['errors'])
    def test_sector_failure_does_not_hide_valuation(self):
        v={'valuation':{'price_date':'2026-11-06'}}
        with patch.object(D.R,'report',return_value=v),patch.object(D.C,'report',side_effect=ValueError('bad taxonomy')),patch.object(D,'latest_market_day',return_value='2026-11-06'):
            r=D.report()
        self.assertEqual(r['river'],v);self.assertIsNone(r['sectors']);self.assertEqual(r['freshness']['status'],'current')
    def test_stale_prices_flag_and_calendar_unknown_are_distinct(self):
        with patch.object(D.R,'report',return_value={'valuation':{'price_date':'2026-11-05'}}),patch.object(D.C,'report',return_value={}):
            with patch.object(D,'latest_market_day',return_value='2026-11-06'):
                r=D.report();self.assertEqual(r['freshness']['status'],'stale');self.assertIn('2026-11-05',r['errors'][0])
            with patch.object(D,'latest_market_day',side_effect=sqlite3.OperationalError('missing')):
                self.assertEqual(D.report()['freshness']['status'],'unknown')
    def test_empty_read_creates_nothing_and_does_not_fetch_or_compute(self):
        with tempfile.TemporaryDirectory() as t,patch.object(db,'DATA',Path(t)),patch.object(D.C,'report',return_value=None),patch.object(V,'compute',side_effect=AssertionError('read cannot compute')):
            self.assertEqual(D.report()['river']['status'],'not_saved')
            self.assertEqual(list(Path(t).iterdir()),[])


class HomeUpdateTests(unittest.TestCase):
    setUp=ManualFreezeTests.setUp
    tearDown=ManualFreezeTests.tearDown
    def run_home(self):
        with patch.object(F,'newest_quote_day',return_value='2026-11-06'),patch.object(F.P,'local_quotes',return_value=('2026-11-06',self.quotes,self.refs)),patch.object(D,'latest_market_day',return_value='2026-11-06'):
            return F.update_home()
    def test_same_day_same_inputs_do_not_duplicate_reports_or_observations(self):
        first=self.run_home();self.assertEqual(first['detail']['action'],'saved')
        path=S.home()/'observations.sqlite3';old=path.read_bytes();report=V.read()
        with patch.object(V,'compute',side_effect=AssertionError('unchanged must not recalculate')):
            second=self.run_home()
        self.assertEqual(second['detail']['action'],'unchanged');self.assertEqual(path.read_bytes(),old)
        self.assertEqual(V.read()['id'],report['id']);self.assertEqual(report['update_context']['trigger'],'home')
        self.assertEqual(Path(self.saved['report_path']).read_bytes(),self.original)
    def test_new_eps_import_creates_new_version_without_overwriting_old(self):
        self.run_home();old=V.read()
        src=S.source('import','licensed_export','https://source.example/new-import',b'new-import')
        newids=S.estimate_batch(src,self.estimates[1:])
        result=self.run_home();new=V.read()
        self.assertEqual(result['detail']['action'],'saved');self.assertNotEqual(old['id'],new['id'])
        self.assertTrue(set(newids).issubset(new['input_estimate_ids']));self.assertEqual(V.read(old['id']),old)
    def test_source_failure_is_one_attempt_and_keeps_latest_pointer(self):
        self.run_home();pointer=(S.home()/'calculations/latest.json').read_bytes()
        with patch.object(F,'prepare',side_effect=ValueError('shares missing')) as prepare,patch.object(D,'latest_market_day',return_value='2026-11-06'):
            result=F.update_home()
        prepare.assert_called_once();self.assertEqual(result['status'],'partial');self.assertEqual(result['detail']['action'],'retained')
        self.assertIn('shares missing',result['message']);self.assertEqual((S.home()/'calculations/latest.json').read_bytes(),pointer)
    def test_market_calendar_mismatch_does_not_replace_old_data(self):
        with patch.object(F,'newest_quote_day',return_value='2026-11-06'),patch.object(F.P,'local_quotes',return_value=('2026-11-06',self.quotes,self.refs)),patch.object(D,'latest_market_day',return_value='2026-11-09'):
            result=F.update_home()
        self.assertEqual(result['detail']['action'],'retained');self.assertEqual(V.read()['id'],self.saved['id'])
    def test_changed_day_rechecks_eps_expiry_even_when_inputs_unchanged(self):
        self.run_home();old=V.read()['id']
        with patch.object(S,'now',return_value='2026-12-22T23:00:00+08:00'):
            result=self.run_home()
        self.assertEqual(result['detail']['action'],'saved');self.assertNotEqual(V.read()['id'],old)
        self.assertEqual(V.read()['periods'][0]['groups'][0]['qualified']['names'],0)
    def test_stale_month_end_list_does_not_generate_current_top_fifty(self):
        p=F.prepare
        with patch.object(F,'newest_quote_day',return_value='2026-11-06'),patch.object(F.P,'local_quotes',return_value=('2026-11-06',self.quotes,self.refs)):
            data=p()
        data['weight_date']='2026-09-30'
        with patch.object(F,'prepare',return_value=data),patch.object(D,'latest_market_day',return_value='2026-11-06'):
            result=F.update_home()
        self.assertEqual(result['detail']['action'],'retained');self.assertIn('月底權重',result['message'])
    def test_home_get_does_not_prepare_calculate_or_publish(self):
        old=(S.home()/'observations.sqlite3').read_bytes()
        with patch.object(F,'prepare',side_effect=AssertionError('GET cannot prepare')),patch.object(V,'compute',side_effect=AssertionError('GET cannot compute')):
            result=F.home_status(target='2026-11-06')
        self.assertEqual(result['status'],'partial');self.assertIn('EPS文件 1/50',result['message'])
        self.assertIn('不自動重試',result['message']);self.assertEqual((S.home()/'observations.sqlite3').read_bytes(),old)
    def test_dashboard_refuses_current_gauge_with_stale_weights(self):
        with patch.object(D.R,'report',return_value={'valuation':{'price_date':'2026-11-06','weight_date':'2026-09-30'}}),patch.object(D.C,'report',return_value={}),patch.object(D,'latest_market_day',return_value='2026-11-06'):
            result=D.report()
        self.assertEqual(result['freshness']['weight_status'],'stale');self.assertIn('月底權重',result['errors'][0])

if __name__=='__main__':unittest.main()
