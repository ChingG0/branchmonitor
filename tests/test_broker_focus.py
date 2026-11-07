import json,tempfile,unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from backend import db,broker_focus as f

class ImmediateThread:
    def __init__(self,target,**kwargs):self.target=target
    def start(self):self.target()

class FocusTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.patch=patch.object(db,'DATA',Path(self.temp.name));self.patch.start();db.initialize()
        self.classifier=patch.object(f.sector_flow,'classification',return_value={'memberships':{'2330':['IC製造','晶圓代工']},'groups':[], 'snapshot':'2026-09-11','source':'prototype/sectors.sqlite3','error':''});self.classifier.start()
        with db.connect() as c:
            c.execute('DELETE FROM branches')
            c.executemany('INSERT INTO branches VALUES (?,?,?,?)',[('A','1','甲',1),('A','2','乙',1),('B','3','丙',1),('C','4','丁',0)])
            c.executemany('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)',[(a,b,'2026-09-16','observed',0,'','now') for a,b in [('A','1'),('A','2'),('B','3'),('C','4')]])
    def tearDown(self):self.classifier.stop();self.patch.stop();self.temp.cleanup()
    def add_flow(self,broker,branch,symbol,net,price=100):
        with db.connect() as c:
            c.execute('INSERT INTO flows VALUES (?,?,?,?,?,?,?,?,?)',(broker,branch,'2026-09-16',symbol,symbol,max(net,0),max(-net,0),net,'test'))
            if price is not None:c.execute('INSERT OR IGNORE INTO prices VALUES (?,?,?,?,?,?,?,?,?)',('2026-09-16',symbol,symbol,price,price,price,price,1000,'TWSE'))
    def save_day(self,day,rows):
        with db.connect() as c:c.execute('INSERT INTO focus_turnover_days VALUES (?,?,?)',(day,json.dumps(dict(rows=rows,sources=['a','b'])),'now'))
    def test_consensus_uses_active_observed_branches_with_both_directions(self):
        for broker,branch,net in [('A','1',100),('A','2',20),('B','3',-150),('C','4',999)]:self.add_flow(broker,branch,'2330',net)
        self.add_flow('A','1','0050',500)
        with patch.object(f.sources,'get',side_effect=AssertionError('must not fetch')):r=f.consensus()
        self.assertEqual((r['observed'],r['total']),(3,3));self.assertEqual(len(r['rows']),1)
        row=r['rows'][0];self.assertEqual((row['buy_count'],row['sell_count'],row['buy_lots'],row['sell_lots'],row['net']),(2,1,120,150,-30))
        self.assertEqual((row['buy_value'],row['sell_value'],row['net_value']),(12000000,15000000,-3000000))
        self.assertEqual(row['buyers'][0]['net_value'],10000000)
        with db.connect() as c:c.execute("UPDATE snapshots SET status='error' WHERE branch='2'")
        r=f.consensus();self.assertEqual(r['missing'],['乙']);self.assertEqual(r['rows'][0]['buy_count'],1)
        with self.assertRaises(ValueError):f.consensus('2026-09-15')
    def test_ignored_symbols_do_not_enter_consensus(self):
        self.add_flow('A','1','2330',100)
        with patch.object(db,'ignored_symbols',return_value={'2330'}):self.assertEqual(f.consensus()['rows'],[])
    def fixture(self,market='TWSE',start=2000):
        fields=['證券代號','證券名稱','成交金額','收盤價'] if market=='TWSE' else ['代號','名稱','成交金額(元)','收盤']
        rows=[[str(i),str(i),str((i-start+1)*1000000),'10'] for i in range(start,start+35)]
        rows.append(['0050','ETF','999999999','100'])
        return dict(stat='ok',date='20260901',tables=[dict(fields=fields,data=rows)])
    def test_turnover_uses_actual_amount_validates_date_and_excludes_funds(self):
        for market in ['TWSE','TPEX']:
            d=self.fixture(market);r=f.parse_turnover(json.dumps(d),'2026-09-01',market)
            self.assertEqual(len(r),35);self.assertEqual(r[0]['amount'],1000000)
            d['date']='20260902'
            with self.assertRaises(ValueError):f.parse_turnover(json.dumps(d),'2026-09-01',market)
            d=self.fixture(market);d['tables'][0]['data'][0][2]='--'
            with self.assertRaises(ValueError):f.parse_turnover(json.dumps(d),'2026-09-01',market)

    def test_tpex_openapi_turnover_requires_exact_date_and_actual_amount(self):
        payload=[{'Date':'1151002','SecuritiesCompanyCode':str(code),'CompanyName':'測試',
                  'Close':'10','TransactionAmount':str(code*1000)} for code in range(6000,6035)]
        payload.append({'Date':'1151002','SecuritiesCompanyCode':'00631L','CompanyName':'ETF',
                        'Close':'100','TransactionAmount':'999999999'})
        rows=f.parse_tpex_openapi_turnover(payload,'2026-10-02')
        self.assertEqual(len(rows),35);self.assertEqual(rows[0]['amount'],6000000)
        with self.assertRaises(ValueError):f.parse_tpex_openapi_turnover(payload,'2026-10-01')

    def test_tpex_turnover_uses_official_openapi_after_page_source_failure(self):
        payload=[{'Date':'1151002','SecuritiesCompanyCode':str(code),'CompanyName':'測試',
                  'Close':'10','TransactionAmount':str(code*1000)} for code in range(6000,6035)]
        with patch.object(f.sources,'get',side_effect=[f.sources.SourceError('HTTP 520'),json.dumps(payload).encode()]) as fetch:
            rows=f.market_turnover('2026-10-02','TPEX')
        self.assertEqual(len(rows),35)
        self.assertEqual(fetch.call_args_list[-1].args[0],'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes')
        self.assertTrue(fetch.call_args_list[-1].kwargs['refresh'])
    def test_record_combines_both_markets_and_never_saves_partial_day(self):
        with patch.object(f.sources,'get',side_effect=lambda url,**kwargs:json.dumps(self.fixture('TWSE' if 'twse.com' in url else 'TPEX',2000 if 'twse.com' in url else 6000))):f.record_day('2026-09-01')
        with db.connect() as c:d=json.loads(c.execute('SELECT payload FROM focus_turnover_days').fetchone()[0])
        self.assertEqual(len(d['rows']),30);self.assertEqual(d['rows'][0]['amount'],35000000);self.assertEqual({r['market'] for r in d['rows']},{'TWSE','TPEX'})
        with patch.object(f.sources,'get',side_effect=lambda url,**kwargs:json.dumps(self.fixture()) if 'twse.com' in url else (_ for _ in ()).throw(ValueError('offline'))):
            with self.assertRaises(ValueError):f.record_day('2026-09-02')
        with db.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM focus_turnover_days').fetchone()[0],1)
    def test_refresh_skips_saved_days_and_retries_only_failed_days(self):
        self.save_day('2026-09-01',[])
        def record(day):
            if day=='2026-09-03':raise ValueError('missing market')
            self.save_day(day,[])
        with patch.object(f.rs,'index_month',return_value={d:100 for d in ['2026-09-01','2026-09-02','2026-09-03']}),patch.object(f,'record_day',side_effect=record) as rec,patch.object(f.threading,'Thread',ImmediateThread):
            self.assertEqual(f.refresh()['status'],'partial');self.assertEqual([c.args[0] for c in rec.call_args_list],['2026-09-02','2026-09-03'])
            rec.reset_mock();f.refresh();rec.assert_called_once_with('2026-09-03')
    def test_history_appearance_counts_have_no_lookahead_and_tags_persist(self):
        self.save_day('2026-09-01',[{'symbol':'2330','amount':20}]);self.save_day('2026-09-02',[{'symbol':'2330','amount':30}])
        f.tag('2330',' 半導體 ');db.initialize()
        with patch.object(f.sources,'get',side_effect=AssertionError('GET must not fetch')):r=f.report(turnover_date='2026-09-01')
        self.assertEqual(r['turnover']['rows'][0]['appearances'],1);self.assertEqual(r['tags']['2330'],'IC製造、晶圓代工')
        with db.connect() as c:self.assertEqual(c.execute("SELECT sector FROM focus_tags WHERE symbol='2330'").fetchone()[0],'半導體')
        self.assertEqual(f.report()['turnover']['rows'][0]['appearances'],2)
        with self.assertRaises(ValueError):f.tag('2330','a'*31)
        with self.assertRaises(ValueError):f.tag('9999','fake')
    def test_rs_overlap_strict_threshold_same_snapshot_and_balanced_order(self):
        def row(symbol,rs):return dict(symbol=symbol,name=symbol,rs=rs,return_pct=10,ytd_pct=30,excess_pct=-5)
        def snapshot(day,symbol):
            payload=dict(groups={'20':[row(symbol,95),row('2222',80),row('3333',60),row('4444',85)],'ytd':[row(symbol,61),row('2222',90),row('3333',99)]},anchors={'20':'2026-08-01','ytd':'2025-12-31'},index_ytd=35)
            with db.connect() as c:c.execute('INSERT INTO rs_days VALUES (?,?,?)',(day,json.dumps(payload),'now'))
        snapshot('2026-09-15','1111');snapshot('2026-09-16','5555')
        r=f.rs_overlap();self.assertEqual(r['as_of'],'2026-09-16');self.assertEqual([row['symbol'] for row in r['rows']],['2222','5555'])
        self.assertEqual(r['rows'][0]['excess_pct'],-5)
    def test_schedule_respects_disabled_before_close_and_already_saved_today(self):
        current=datetime(2026,9,16,19,tzinfo=f.TZ)
        with patch.object(f,'_last_slot',None),patch.object(f,'refresh') as refresh:
            f.schedule_tick(current,{'schedule_enabled':False});refresh.assert_not_called()
            f.schedule_tick(current.replace(hour=10),{'schedule_enabled':True});refresh.assert_not_called()
            self.save_day('2026-09-16',[]);f.schedule_tick(current,{'schedule_enabled':True});refresh.assert_not_called()
            with db.connect() as c:c.execute('DELETE FROM focus_turnover_days')
            f.schedule_tick(current,{'schedule_enabled':True});f.schedule_tick(current,{'schedule_enabled':True});refresh.assert_called_once()
