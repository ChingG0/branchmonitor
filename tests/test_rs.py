import json
import tempfile
import unittest
from datetime import date,timedelta
from pathlib import Path
from unittest.mock import patch
from backend import db,rs,sources

F=Path(__file__).parent/'fixtures'
def fixture(name):return json.loads((F/(name+'.json')).read_text())
class RsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.p=patch.object(db,'DATA',Path(self.temp.name));self.p.start();db.initialize()
    def tearDown(self):self.p.stop();self.temp.cleanup()
    def test_calendar_uses_official_dates_and_rejects_wrong_month(self):
        data=fixture('rs-index');rows=rs.parse_index(data,'2026-09-01')
        self.assertEqual(rows['2026-09-01'],46948.72)
        with self.assertRaises(sources.SourceError):rs.parse_index(data,'2026-08-01')
        self.assertEqual(rs.roc('1150831'),'2026-08-31')
        self.assertEqual(rs.parse_index({'stat':'很抱歉，沒有符合條件的資料!'},'2026-01-01'),{})
    def test_corporate_events_validate_period_and_cash_dividend_is_not_excluded(self):
        for name,market,kind in [('rs-tpex-split','TPEX','par'),('rs-tpex-red','TPEX','capital'),('rs-split','TWSE','par'),('rs-reduction','TWSE','capital')]:
            rows=rs.parse_events(fixture(name),'2026-01-01','2026-09-10',market,kind)
            self.assertEqual(len(rows),2)
            with self.assertRaises(sources.SourceError):rs.parse_events(fixture(name),'2026-02-01','2026-09-10',market,kind)
        self.assertEqual(rs.parse_events(fixture('rs-tpex-ex'),'2026-01-01','2026-09-10','TPEX','rights'),[])

    def test_tpex_openapi_rights_accepts_only_matching_stock_rights(self):
        data=[
            {'Date':'1151002','SecuritiesCompanyCode':'6171','ExRightsDiviend':'除息'},
            {'Date':'1151002','SecuritiesCompanyCode':'6488','ExRightsDiviend':'除權息'},
            {'Date':'1151005','SecuritiesCompanyCode':'00950B','ExRightsDiviend':'除息'},
        ]
        self.assertEqual(rs.parse_tpex_openapi_rights(data,'2026-10-02'),[
            {'symbol':'6488','date':'2026-10-02','kind':'rights'}])
        with self.assertRaises(sources.SourceError):rs.parse_tpex_openapi_rights(data,'2026-10-01')

    def test_tpex_event_fallback_extends_cached_range_and_excludes_resumptions(self):
        before=[{'symbol':str(code),'close':10} for code in range(1000,1301)]
        after=before+[{'symbol':'6488','close':450}]
        with db.connect() as c:
            c.execute('INSERT INTO rs_quotes VALUES (?,?,?,?)',('2026-10-01','TPEX',json.dumps(before),'test'))
            c.execute('INSERT INTO rs_quotes VALUES (?,?,?,?)',('2026-10-02','TPEX',json.dumps(after),'test'))
        cached={'stat':'ok','date':'20260101~20261001','tables':[{'fields':['代號','恢復買賣日期'],'data':[]}]}
        with patch.object(sources,'get',return_value=json.dumps(cached).encode()):
            rows=rs.tpex_event_fallback('2026-01-01','2026-10-02','capital')
        self.assertEqual(rows,[{'symbol':'6488','date':'2026-10-02','kind':'capital'}])
    def test_percentile_ties_and_single_sample(self):
        rows=[dict(symbol=str(i),return_pct=v,ytd_pct=v) for i,v in enumerate([0,10,10,30])]
        result={r['symbol']:r['rs'] for r in rs.rank(rows)}
        self.assertEqual(result,{'3':100,'1':50,'2':50,'0':0})
        self.assertEqual(rs.rank([dict(symbol='1',return_pct=0,ytd_pct=0)])[0]['rs'],50)
    def test_modes_change_relative_strength_but_ytd_comparison_stays_fixed(self):
        anchors={'20':'2026-08-13','60':'2026-06-16','ytd':'2025-12-31'}
        def snap(prices):return {s:dict(symbol=s,name=s,market='TWSE',close=p) for s,p in prices.items()}
        snapshots={
          '2026-09-10':snap({'1001':150,'1002':120,'1003':100,'0050':200,'1004':100,'1005':100}),
          anchors['20']:snap({'1001':145,'1002':80,'1003':100,'0050':100,'1004':50}),
          anchors['60']:snap({'1001':100,'1002':130,'1003':100,'0050':100,'1004':50}),
          anchors['ytd']:snap({'1001':100,'1002':100,'1003':100,'0050':100,'1004':50}),
        }
        payload=rs.calculate(snapshots,anchors,'2026-09-10',30,[{'symbol':'1004','date':'2026-04-01','kind':'par'}])
        self.assertEqual(payload['groups']['20'][0]['symbol'],'1002')
        self.assertEqual(payload['groups']['60'][0]['symbol'],'1001')
        for rows in payload['groups'].values():
            self.assertEqual(len(rows),3)
            self.assertNotIn('0050',[r['symbol'] for r in rows])
            row=next(r for r in rows if r['symbol']=='1002');self.assertAlmostEqual(row['ytd_pct'],20);self.assertAlmostEqual(row['excess_pct'],-10)
    def test_report_strict_threshold_two_sides_and_equal_preserve_saved_data(self):
        rows=[dict(symbol=str(i),rs=score,excess_pct=excess) for i,(score,excess) in enumerate([(60,10),(60.01,10),(99,-10),(80,0)])]
        payload=dict(groups={m:rows for m in rs.MODES},excluded={m:{} for m in rs.MODES},anchors={m:'2025-12-31' for m in rs.MODES},as_of='2026-09-10',index_ytd=20,index_close=120,index_base_close=100,fetched_at='test')
        with db.connect() as c:c.execute('INSERT INTO rs_days VALUES (?,?,?)',('2026-09-10',json.dumps(payload),'test'))
        with patch.object(sources,'get',side_effect=AssertionError('GET must not fetch')):
            r=rs.report('20')
        self.assertEqual(r['hidden'],1);self.assertEqual(len(r['left']),1);self.assertEqual(len(r['right']),1);self.assertEqual(len(r['equal']),1)
        with self.assertRaises(ValueError):rs.report('invalid')
    def test_failed_refresh_retains_existing_result(self):
        with db.connect() as c:
            c.execute('INSERT INTO rs_days VALUES (?,?,?)',('2026-09-10','{"keep":true}','test'))
            c.execute('INSERT INTO rs_state VALUES (1,?,?,NULL,?)',('running','test','test'))
        rs._lock.acquire()
        with patch.object(rs,'index_month',side_effect=sources.SourceError('calendar unavailable')):rs._worker()
        self.assertEqual(rs.job_status()['status'],'failed')
        with db.connect() as c:self.assertEqual(json.loads(c.execute('SELECT payload FROM rs_days').fetchone()[0]),{'keep':True})

    def test_local_history_backfill_uses_point_in_time_anchors(self):
        days=[];cursor=date(2026,1,2)
        while len(days)<61:
            if cursor.weekday()<5:days.append(cursor.isoformat())
            cursor+=timedelta(days=1)
        base=[dict(symbol='1001',name='甲',market='TWSE',close=100),dict(symbol='1002',name='乙',market='TWSE',close=100),dict(symbol='TAIEX',name='加權',market='TWSE',close=100)]
        with db.connect() as c:
            c.execute('INSERT INTO rs_quotes VALUES (?,?,?,?)',('2025-12-31','TWSE',json.dumps(base),'test'))
            c.execute('INSERT INTO rs_quotes VALUES (?,?,?,?)',('2025-12-31','TPEX','[]','test'))
            for i,day in enumerate(days):
                c.execute('INSERT INTO market_days VALUES (?,?,?,?)',(day,'ok','ok',''))
                for symbol,name,close in [('1001','甲',100+i),('1002','乙',100+i/2),('TAIEX','加權',100+i/4)]:
                    c.execute('INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?)',(day,symbol,name,close,close,close,close,1000,'TWSE'))
        result=rs.backfill_local_days(days[-2],days[-1],corporate_events=[],min_universe=2)
        self.assertEqual(result['created'],[days[-1]])
        with db.connect() as c:payload=json.loads(c.execute('SELECT payload FROM rs_days WHERE date=?',(days[-1],)).fetchone()[0])
        self.assertEqual(payload['anchors']['20'],days[-21])
        self.assertEqual(payload['anchors']['60'],days[0])
        self.assertEqual(payload['source'],'verified-local-history')
        self.assertEqual(len(payload['groups']['20']),2)

if __name__=='__main__':unittest.main()
