import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import db,sector_flow,forward_pe_sectors as S


def taxonomy():
    return {'groups':[{'name':'我們的A','parent':'自訂大類'},{'name':'我們的B','parent':'自訂大類'}],
            'memberships':{'1000':['我們的A','我們的A','我們的B'],'1001':['我們的A'],'2000':['我們的B']},
            'snapshot':'2026-09-11','source':'prototype/sectors.sqlite3','error':''}


def sources():
    def row(s,p,a):return {'symbol':s,'name':s,'close':p,'turnover':a}
    current={'TWSE':{'status':'ok','rows':[row('1000',110,300),row('1001',90,100),row('9999',100,600),row('0050',100,999999)]},
             'TPEX':{'status':'ok','rows':[row('2000',120,1000)]}}
    prior={'TWSE':{'status':'ok','rows':[row('1000',100,100),row('1001',100,100),row('9999',100,300),row('0050',100,999999)]},
           'TPEX':{'status':'ok','rows':[row('2000',100,500)]}}
    return current,prior


class CalculationTests(unittest.TestCase):
    def test_custom_multilabel_groups_equalweight_and_unique_market_denominator(self):
        current,prior=sources();r=S.calculate(taxonomy(),current,prior)
        groups={x['name']:x for x in r['rows']}
        self.assertEqual(set(groups),{'我們的A','我們的B'})
        self.assertEqual(r['market_turnover_twd'],2000)
        self.assertEqual(r['market_names'],4)  # Includes unclassified 9999, excludes ETF.
        self.assertEqual(groups['我們的A']['amount_twd'],400)  # Duplicate membership not duplicated.
        self.assertAlmostEqual(groups['我們的A']['return_pct'],0)
        self.assertEqual(groups['我們的B']['amount_share_pct'],65)
        self.assertEqual(groups['我們的B']['previous_amount_share_pct'],60)
        self.assertEqual(groups['我們的B']['share_change_pp'],5)
        self.assertAlmostEqual(groups['我們的B']['return_pct'],15)
        self.assertEqual((groups['我們的A']['advancers'],groups['我們的A']['decliners']),(1,1))

    def test_missing_amount_is_partial_not_zero_and_zero_is_valid(self):
        current,prior=sources();current['TWSE']['rows'][1]['turnover']=None
        r=S.calculate(taxonomy(),current,prior);g=next(x for x in r['rows'] if x['name']=='我們的A')
        self.assertEqual(g['amount_twd'],300)
        self.assertEqual(g['amount_names'],1)
        self.assertFalse(g['amount_complete'])
        self.assertIsNone(g['amount_share_pct']);self.assertIsNone(r['market_turnover_twd'])
        current['TWSE']['rows'][1]['turnover']=0
        r=S.calculate(taxonomy(),current,prior);g=next(x for x in r['rows'] if x['name']=='我們的A')
        self.assertTrue(g['amount_complete'])
        self.assertAlmostEqual(g['amount_share_pct'],300/1900*100)

    def test_missing_market_never_gets_twse_only_normalized_share(self):
        current,prior=sources();current['TPEX']={'status':'missing','rows':[]}
        r=S.calculate(taxonomy(),current,prior)
        self.assertIsNone(r['market_turnover_twd'])
        self.assertTrue(all(g['amount_share_pct'] is None for g in r['rows']))
        g=next(x for x in r['rows'] if x['name']=='我們的B')
        self.assertEqual(g['amount_twd'],300);self.assertEqual(g['quoted'],1)

    def test_missing_close_cannot_be_scored_as_flat(self):
        current,prior=sources();current['TWSE']['rows'][1]['close']=None
        g=next(x for x in S.calculate(taxonomy(),current,prior)['rows'] if x['name']=='我們的A')
        self.assertEqual(g['quoted'],1)
        self.assertAlmostEqual(g['return_pct'],10)
        self.assertEqual(g['return_coverage_pct'],50)
        self.assertEqual(g['unchanged'],0)

    def test_same_code_in_two_markets_is_excluded_instead_of_overwritten(self):
        current,prior=sources();current['TPEX']['rows'].append({'symbol':'1000','close':999,'turnover':1000})
        r=S.calculate(taxonomy(),current,prior)
        self.assertEqual(r['duplicate_symbols'],['1000'])
        self.assertIsNone(r['market_turnover_twd'])
        self.assertIsNone(r['rows'][0]['amount_share_pct'])

    def test_transfer_between_markets_is_not_a_same_market_daily_return(self):
        current,prior=sources();moved=prior['TWSE']['rows'].pop(0);prior['TPEX']['rows'].append(moved)
        g=next(x for x in S.calculate(taxonomy(),current,prior)['rows'] if x['name']=='我們的A')
        self.assertEqual(g['quoted'],1)
        self.assertAlmostEqual(g['return_pct'],-10)

    def test_invalid_numbers_and_out_of_scope_symbols_not_used(self):
        current,prior=sources();current['TWSE']['rows'][0]['turnover']=float('nan')
        current['TWSE']['rows'][1]['close']=True
        t=taxonomy();t['memberships']['0050']=['我們的A']
        g=next(x for x in S.calculate(t,current,prior)['rows'] if x['name']=='我們的A')
        self.assertEqual(g['members'],3)
        self.assertEqual(g['amount_names'],1)
        self.assertEqual(g['quoted'],1)
        self.assertAlmostEqual(g['return_pct'],10)
        json.dumps(g,allow_nan=False)

    def test_calendar_confirms_holidays_and_does_not_skip_a_missing_session(self):
        ok={'TWSE':'ok','TPEX':'ok'};closed={'TWSE':'no_data','TPEX':'no_data'}
        self.assertEqual(S.previous_session({'2026-11-06':ok,'2026-11-09':ok},'2026-11-09'),'2026-11-06')
        self.assertIsNone(S.previous_session({'2026-11-04':ok,'2026-11-06':ok},'2026-11-06'))
        self.assertEqual(S.previous_session({'2026-11-04':ok,'2026-11-05':closed,'2026-11-06':ok},'2026-11-06'),'2026-11-04')


class ReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.patch=patch.object(db,'DATA',self.root);self.patch.start()
        self.tax=patch.object(sector_flow,'classification',side_effect=taxonomy);self.tax.start()
        margin=self.root/'margin_statistics';margin.mkdir()
        with sqlite3.connect(margin/'sources.sqlite3') as c:
            c.executescript('CREATE TABLE latest(market TEXT,kind TEXT,day TEXT,snapshot_id TEXT);CREATE TABLE snapshots(id TEXT,market TEXT,kind TEXT,day TEXT,row_count INTEGER,sha256 TEXT,url TEXT,retrieved_at TEXT,quality TEXT);CREATE TABLE quotes(snapshot_id TEXT,symbol TEXT,name TEXT,close REAL,turnover REAL);')
            for day,snap in zip(('2026-11-06','2026-11-05'),sources()):
                for market,info in snap.items():
                    ident=market+day;c.execute('INSERT INTO latest VALUES(?,?,?,?)',(market,'quotes',day,ident))
                    c.execute('INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?,?)',(ident,market,'quotes',day,len(info['rows']),'sha_'+ident,'https://official.example/quotes','2026-11-07T18:00:00+08:00','saved'))
                    c.executemany('INSERT INTO quotes VALUES(?,?,?,?,?)',[(ident,r['symbol'],r['name'],r['close'],r['turnover']) for r in info['rows']])
        with sqlite3.connect(self.root/'monitor.sqlite3') as c:
            c.execute('CREATE TABLE market_days(date TEXT,twse_status TEXT,tpex_status TEXT)')
            c.executemany('INSERT INTO market_days VALUES(?,?,?)',[(d,'ok','ok') for d in ('2026-11-04','2026-11-05','2026-11-06')])

    def tearDown(self):self.tax.stop();self.patch.stop();self.tmp.cleanup()

    def test_read_uses_actual_taxonomy_and_official_amounts_without_mutation_or_refresh(self):
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch.object(db,'connect',side_effect=AssertionError('No writable monitor connection')),patch.object(sector_flow,'refresh',side_effect=AssertionError('No refresh')):
            r=S.report();again=S.report()
        self.assertEqual(r,again)
        self.assertEqual(r['date'],'2026-11-06');self.assertEqual(r['previous_date'],'2026-11-05')
        self.assertEqual(r['status'],'ok');self.assertEqual(r['classification']['groups'],2)
        self.assertEqual(r['sources']['current']['TWSE']['sha256'],'sha_TWSE2026-11-06')
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_missing_exact_previous_snapshot_does_not_use_older_data(self):
        with sqlite3.connect(self.root/'margin_statistics/sources.sqlite3') as c:c.execute("DELETE FROM latest WHERE market='TWSE' AND day='2026-11-05'")
        r=S.report();self.assertEqual(r['previous_date'],'2026-11-05')
        self.assertEqual(r['sources']['previous']['TWSE']['status'],'missing')
        self.assertEqual(r['status'],'partial')
        self.assertTrue(all(g['previous_amount_share_pct'] is None for g in r['rows']))
        self.assertTrue(any('手動' in w for w in r['warnings']))

    def test_missing_stock_amount_shows_manual_guidance_even_when_snapshot_counts_match(self):
        with sqlite3.connect(self.root/'margin_statistics/sources.sqlite3') as c:c.execute("UPDATE quotes SET turnover=NULL WHERE snapshot_id='TWSE2026-11-06' AND symbol='1000'")
        r=S.report();self.assertEqual(r['status'],'partial')
        self.assertIsNone(r['market_turnover_twd'])
        self.assertTrue(any('含缺值' in w and '手動' in w for w in r['warnings']))

    def test_snapshot_pointer_to_another_date_is_rejected_even_if_counts_match(self):
        with sqlite3.connect(self.root/'margin_statistics/sources.sqlite3') as c:c.execute("UPDATE latest SET snapshot_id='TWSE2026-11-05' WHERE market='TWSE' AND day='2026-11-06'")
        r=S.report();self.assertEqual(r['status'],'partial')
        self.assertEqual(r['sources']['current']['TWSE']['status'],'snapshot_identity_mismatch')
        self.assertEqual(r['sources']['current']['TWSE']['snapshot_day'],'2026-11-05')
        self.assertIsNone(r['market_turnover_twd'])

    def test_wrong_source_row_count_is_not_trusted(self):
        with sqlite3.connect(self.root/'margin_statistics/sources.sqlite3') as c:c.execute("UPDATE snapshots SET row_count=99 WHERE id='TWSE2026-11-06'")
        r=S.report();self.assertEqual(r['status'],'partial')
        self.assertIsNone(r['market_turnover_twd'])

    def test_unavailable_dictionary_or_source_does_not_create_or_substitute_data(self):
        with patch.object(sector_flow,'classification',return_value={'groups':[],'memberships':{},'snapshot':None,'source':'custom','error':'我們的分類未齊'}):
            r=S.report();self.assertEqual(r['rows'],[]);self.assertIn('我們的分類未齊',r['warnings'])
        with patch.object(db,'DATA',self.root/'empty'):
            self.assertEqual(S.report()['status'],'missing')
            self.assertFalse((self.root/'empty').exists())
        with self.assertRaises(ValueError):S.report('not-a-date')


if __name__=='__main__':unittest.main()
