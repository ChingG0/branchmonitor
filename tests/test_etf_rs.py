import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend import db,rs,etf_rs,sources


class EtfRsTests(unittest.TestCase):
    def test_official_empty_event_period_is_distinct_from_source_error(self):
        self.assertEqual(rs.parse_events({'stat':'很抱歉，沒有符合條件的資料!'},'2026-03-31','2026-06-28','TWSE','capital'),[])
        with self.assertRaises(sources.SourceError):rs.parse_events({'stat':'系統維護'},'2026-03-31','2026-06-28','TWSE','capital')

    def test_corporate_events_are_batched_without_gaps(self):
        with patch.object(rs,'events',return_value=[]) as fetch:
            etf_rs.events('2025-12-31','2026-09-11')
        calls=[c.args for c in fetch.call_args_list]
        self.assertEqual(calls,[('2025-12-31','2026-03-30'),('2026-03-31','2026-06-28'),('2026-06-29','2026-09-11')])

    def test_etf_etn_leveraged_inverse_separate_from_stocks(self):
        anchors=dict(ytd='2025-12-31',**{'20':'2026-08-01','60':'2026-06-01'})
        prices={'0050':100,'00631L':100,'00632R':100,'020000':100,'02001L':100,'2330':100,'030000':100,'01001T':100}
        def snap(values):return {s:dict(symbol=s,name=s,market='TWSE',close=p) for s,p in values.items()}
        snapshots={d:snap(prices) for d in anchors.values()}
        snapshots['2026-09-11']=snap({**prices,'0050':140,'00631L':180,'00632R':70,'020000':120,'02001L':160,'00400A':110})
        result=rs.calculate(snapshots,anchors,'2026-09-11',30,[],universe='etf')
        for group in result['groups'].values():
            self.assertEqual({r['symbol'] for r in group}, {'0050','00631L','00632R','020000','02001L'})
            self.assertEqual(group[0]['symbol'],'00631L')
            self.assertEqual(group[0]['rs'],100)
            self.assertAlmostEqual(group[0]['return_pct'],80)
            self.assertEqual(group[-1]['product_type'],'ETF · 反向')
            self.assertAlmostEqual(next(r for r in group if r['symbol']=='020000')['excess_pct'],-10)
        self.assertEqual(result['excluded']['20']['缺少期初價格或新上市'],1)
        split=rs.calculate(snapshots,anchors,'2026-09-11',30,[dict(symbol='00631L',date='2026-03-31')],universe='etf')
        self.assertNotIn('00631L',[r['symbol'] for r in split['groups']['20']])
        self.assertEqual(split['excluded']['20']['公司事件待還原'],1)

    def test_market_parser_includes_etns_and_excludes_warrants(self):
        fields=['證券代號','證券名稱','收盤價']
        symbols=['0050','00631L','00632R','020000','02001L','02001R','02001B','030000','01001T']
        data=dict(stat='OK',date='20260911',tables=[dict(fields=fields,data=[[s,s,'10'] for s in symbols])])
        rows=sources.parse_market(data,'2026-09-11','TWSE')
        self.assertEqual({r['symbol'] for r in rows},set(symbols[:7]))

    def test_split_dates_and_empty_verified_tpex_response(self):
        d=dict(stat='OK',startDate='20260101',endDate='20260911',fields=['恢復買賣日期','ETF代號'],data=[['115/03/31','00631L']])
        self.assertEqual(etf_rs.parse_splits(d,'2026-01-01','2026-09-11','TWSE')[0]['date'],'2026-03-31')
        with self.assertRaises(sources.SourceError):etf_rs.parse_splits(d,'2026-02-01','2026-09-11','TWSE')
        d=dict(stat='ok',date='20260101~20260911',tables=[dict(fields=['恢復買賣日期','證券代號'],data=[])])
        self.assertEqual(etf_rs.parse_splits(d,'2026-01-01','2026-09-11','TPEX'),[])
        d['tables'][0]['fields']=[]
        with self.assertRaises(sources.SourceError):etf_rs.parse_splits(d,'2026-01-01','2026-09-11','TPEX')

    def test_tpex_split_fallback_extends_cached_range_and_excludes_resumptions(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(db,'DATA',Path(folder)):
            db.initialize()
            before=[{'symbol':str(code),'close':10} for code in range(1000,1301)]
            after=before+[{'symbol':'00631L','close':80}]
            with db.connect() as c:
                c.execute('INSERT INTO rs_quotes VALUES (?,?,?,?)',('2026-10-01','TPEX',json.dumps(before),'test'))
                c.execute('INSERT INTO rs_quotes VALUES (?,?,?,?)',('2026-10-02','TPEX',json.dumps(after),'test'))
            cached={'stat':'ok','date':'20260101~20261001','tables':[
                {'fields':['恢復買賣日期','證券代號'],'data':[]}]}
            with patch.object(sources,'get',return_value=json.dumps(cached).encode()):
                rows=etf_rs.tpex_split_fallback('2026-01-01','2026-10-02','bulletin/etfSplitRslt')
            self.assertEqual(rows,[{'symbol':'00631L','date':'2026-10-02','kind':'split'}])

    def test_separate_cache_threshold_and_failed_refresh_retains_both(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(db,'DATA',Path(folder)):
            db.initialize()
            rows=[dict(symbol=s,rs=r,excess_pct=e) for s,r,e in [('0050',60,10),('00631L',100,20),('020000',80,-5)]]
            payload=dict(groups={m:rows for m in rs.MODES},excluded={m:{} for m in rs.MODES},anchors={m:'2025-12-31' for m in rs.MODES},as_of='2026-09-11',index_ytd=20,index_close=120,index_base_close=100,fetched_at='test')
            with db.connect() as c:
                c.execute('INSERT INTO etf_rs_days VALUES (?,?,?)',('2026-09-11',json.dumps(payload),'test'))
                c.execute('INSERT INTO rs_days VALUES (?,?,?)',('2026-09-11','{"keep":true}','test'))
                c.execute('INSERT INTO etf_rs_state VALUES (1,?,?,NULL,?)',('running','test','test'))
            with patch.object(sources,'get',side_effect=AssertionError('Read must not fetch')):
                report=etf_rs.report('20')
                self.assertEqual(report['hidden'],1)
                self.assertEqual(report['left'][0]['symbol'],'00631L')
                self.assertEqual(report['right'][0]['symbol'],'020000')
            etf_rs._lock.acquire()
            with patch.object(etf_rs,'index_month',side_effect=sources.SourceError('calendar unavailable')):etf_rs._worker()
            self.assertEqual(etf_rs.job_status()['status'],'failed')
            self.assertTrue(etf_rs.report()['available'])
            with db.connect() as c:self.assertEqual(json.loads(c.execute('SELECT payload FROM rs_days').fetchone()[0]),{'keep':True})
