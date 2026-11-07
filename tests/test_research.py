import json,os,tempfile,unittest,stat
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from backend import db,research as r,backtest
class Immediate:
    def __init__(self,target,**kw):self.target=target
    def start(self):self.target()
class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.patch=patch.object(db,'DATA',Path(self.tmp.name));self.patch.start();db.initialize();r.initialize()
        self.real_latest_close=r.ensure_latest_close;self.latest_close=patch.object(r,'ensure_latest_close',return_value=[]);self.latest_close.start()
        self.preparation=patch.object(backtest,'prepare',return_value={'target_date':'2026-09-21','warnings':[]});self.preparation.start()
        self.env=patch.dict(os.environ,{'OPENAI_API_KEY':''});self.env.start()
    def tearDown(self):self.latest_close.stop();self.preparation.stop();self.env.stop();self.patch.stop();self.tmp.cleanup()
    def evidence(self):return {'date':'2026-09-18','candidates':[{'symbol':'2330'}]}
    def response(self):
        return {'status':'completed','id':'resp_test','model':'chat-latest','output':[{'type':'web_search_call','status':'completed'},{'type':'message','content':[{'type':'output_text','text':'新聞甲來源','annotations':[{'type':'url_citation','start_index':3,'end_index':5,'url':'https://example.org/news','title':'公告'}]}]}],'usage':{'input_tokens':30,'output_tokens':10}}
    def test_key_permissions_and_no_disclosure(self):
        key='sk-testabcdefghijklmnop'
        result=r.configure({'api_key':key,'model':'chat-latest'})
        self.assertTrue(result['configured']);self.assertNotIn(key,json.dumps(result));self.assertEqual(stat.S_IMODE((db.DATA/'research-config.json').stat().st_mode),0o600)
        r.configure({'model':'another-model','api_key':''});self.assertEqual(r.credentials(),(key,'another-model'))
        r.configure({'model':'chat-latest','remove_key':True});self.assertFalse(r.public_config()['configured'])
        with self.assertRaises(ValueError):r.configure({'api_key':'sk-bad\nsecret'})
    def test_citations_required_and_safe_segments(self):
        result=r.parse_result(self.response());self.assertEqual(result['parts'][1],{'text':'公告','url':'https://example.org/news'})
        bad=self.response();bad['output'][1]['content'][0]['annotations'][0]['url']='javascript:alert(1)'
        with self.assertRaises(ValueError):r.parse_result(bad)
        bad=self.response();bad['status']='incomplete'
        with self.assertRaises(ValueError):r.parse_result(bad)
        bad=self.response();bad['output']=bad['output'][1:]
        with self.assertRaises(ValueError):r.parse_result(bad)
    def test_cached_read_does_not_call_api_force_creates_new_version_failure_preserves_old(self):
        r.configure({'api_key':'sk-testabcdefghijklmnop'})
        with patch.object(r,'available_dates',return_value=['2026-09-18']),patch.object(r,'context',return_value=self.evidence()),patch.object(r.threading,'Thread',Immediate),patch.object(r,'request_report',return_value=r.parse_result(self.response())) as api:
            first=r.generate();self.assertFalse(first['cached']);self.assertFalse(r._lock.locked())
            cached=r.generate();self.assertEqual(cached,{'cached':True,'id':first['id'],'date':'2026-09-18'});self.assertEqual(api.call_count,1);self.assertFalse(r._lock.locked())
            r.report();self.assertEqual(api.call_count,1)
            api.side_effect=ValueError('額度不足');failed=r.generate(force=True)
            state=r.report();self.assertEqual(state['selected']['id'],first['id']);self.assertEqual(r.report(failed['id'])['selected']['status'],'failed');self.assertEqual(len(state['history']),2)
    def test_missing_key_duplicate_and_restart_never_resend(self):
        with patch.object(r,'available_dates',return_value=['2026-09-18']),patch.object(r,'request_report') as api:
            with self.assertRaises(ValueError):r.generate()
            self.assertFalse(r._lock.locked())
            with r._lock:self.assertTrue(r.generate()['busy'])
            with r.store() as c:c.execute('INSERT INTO reports VALUES (?,?,?,?,?,?,?,?,?)',('old','2026-09-18','running',r.now(),None,'chat-latest','{}',None,''))
            r.initialize();self.assertEqual(r.report('old')['selected']['status'],'interrupted');api.assert_not_called()
    def test_transport_is_fixed_tls_no_retry_no_store_and_sanitized_errors(self):
        result=type('Response',(),{'status_code':401})()
        with patch.object(r.requests,'post',return_value=result) as post:
            with self.assertRaisesRegex(ValueError,'API Key'):r.request_report(self.evidence(),'sk-secret','chat-latest')
            self.assertEqual(post.call_count,1);self.assertEqual(post.call_args.args[0],'https://api.openai.com/v1/responses');self.assertFalse(post.call_args.kwargs['json']['store']);self.assertFalse(post.call_args.kwargs['allow_redirects']);self.assertIn('web_search',json.dumps(post.call_args.kwargs['json']))
    def test_date_cutoff_stale_rs_and_no_future_sector_or_prices(self):
        with db.connect() as c:
            c.execute("INSERT INTO prices(date,symbol,name,close,market) VALUES ('2026-09-10','2330','台積電',100,'TWSE')")
            c.execute("INSERT INTO prices(date,symbol,name,close,market) VALUES ('2026-09-11','2330','台積電',110,'TWSE')")
            row={'symbol':'2330','name':'台積電','rs':90,'ytd_pct':20}
            c.execute('INSERT INTO rs_days VALUES (?,?,?)',('2026-09-10',json.dumps({'groups':{'20':[row],'ytd':[row]},'index_ytd':10}),'2026-09-10'))
        dictionary={'memberships':{'2330':['晶圓代工']},'snapshot':'2026-09-11','error':''}
        with patch.object(r,'board_snapshot',return_value={'date':'2026-09-18','leaders':[{'name':'未來族群','inst_value':100}]}),patch.object(r.sector_flow,'classification',return_value=dictionary):
            data=r.context('2026-09-11');self.assertEqual(data['sector_inflow'],[]);self.assertIsNone(data['dates']['sector']);self.assertEqual(data['dates']['rs'],'2026-09-10');self.assertEqual(data['candidates'][0]['close'],110);self.assertEqual(data['news_cutoff'],'2026-09-12T08:30:00+08:00');self.assertTrue(data['warnings'])
            with self.assertRaises(ValueError):r.context('2099-01-01')
    def test_news_deadline_uses_generation_time_for_same_day_post_close(self):
        instant=datetime(2026,9,23,23,35,tzinfo=r.TZ)
        self.assertEqual(r.news_deadline('2026-09-23',instant).isoformat(),'2026-09-23T23:35:00+08:00')
        self.assertEqual(r.news_deadline('2026-09-22',instant).isoformat(),'2026-09-23T08:30:00+08:00')
    def test_latest_report_fetches_and_saves_current_official_close(self):
        instant=datetime(2026,9,23,18,30,tzinfo=r.TZ)
        def market(day,market,refresh=False):
            symbol='2330' if market=='TWSE' else '6488'
            return [{'date':day,'symbol':symbol,'name':'測試','close':100,'open':99,'high':101,'low':98,'volume':1000,'market':market}]
        with patch.object(r.sources,'market_prices',side_effect=market) as source:
            self.assertEqual(self.real_latest_close(instant),[])
            self.assertEqual(source.call_count,2)
            self.assertEqual(self.real_latest_close(instant),[])
            self.assertEqual(source.call_count,2)
        with db.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM prices WHERE date='2026-09-23'").fetchone()[0],2)
            state=c.execute("SELECT twse_status,tpex_status FROM market_days WHERE date='2026-09-23'").fetchone()
            self.assertEqual(tuple(state),('ok','ok'))
    def test_empty_stock_candidates_still_allow_futures_only_report(self):
        class Immediate:
            def __init__(self,target,**kwargs):self.target=target
            def start(self):self.target()
        evidence={'date':'2026-09-18','candidates':[],'warnings':[]}
        result={'parts':[{'text':'台指研究','url':'https://www.twse.com.tw/'}],
                'tracking':{'observations':[],'futures':{'reason':'test'}}}
        with patch.object(r,'available_dates',return_value=['2026-09-18']),patch.object(r,'credentials',return_value=('sk-example','chat-latest')),patch.object(r,'context',return_value=evidence) as context,patch.object(r,'request_report',return_value=result) as api,patch.object(r.threading,'Thread',Immediate),patch('backend.backtest.prepare',return_value={'warnings':[]}),patch('backend.backtest.saved_forecasts',return_value=[]),patch('backend.backtest.ingest_futures'),patch('backend.backtest.recalculate_futures'),patch('backend.strategies.allocation_evidence',return_value={}),patch.object(r.alpha_model,'prepare') as stock_model,patch.object(r.alpha_model,'prepare_futures',return_value={}) as futures_model:
            generated=r.generate()
            self.assertFalse(generated['cached']);api.assert_called_once();context.assert_called_once_with('2026-09-18',include_stocks=False)
            futures_model.assert_called_once();stock_model.assert_not_called();self.assertFalse(r._lock.locked())

    def test_negative_broker_signals_are_kept_as_counter_evidence(self):
        with db.connect() as c:
            c.execute("INSERT INTO prices(date,symbol,name,close,market) VALUES ('2026-09-11','2330','台積電',100,'TWSE')")
            c.execute("INSERT OR REPLACE INTO branches VALUES ('9300','9339','测试分點',1)")
            c.execute("INSERT INTO snapshots VALUES ('9300','9339','2026-09-11','observed',1,'','2026-09-11')")
            c.execute("INSERT INTO flows VALUES ('9300','9339','2026-09-11','2330','台積電',0,100,-100,'test')")
        with patch.object(r,'board_snapshot',return_value={}),patch.object(r.sector_flow,'classification',return_value={'memberships':{},'error':''}):
            result=r.context('2026-09-11');self.assertEqual(result['broker_sells'][0]['net_lots'],-100)

    def test_uses_exact_prototype_close_when_main_date_is_not_available(self):
        import sqlite3
        sector_dir=Path(self.tmp.name)/'proto';sector_dir.mkdir()
        with sqlite3.connect(sector_dir/'sectors.sqlite3') as c:
            c.execute('CREATE TABLE own_prices(date TEXT,symbol TEXT,name TEXT,close REAL)')
            c.executemany('INSERT INTO own_prices VALUES (?,?,?,?)',[('2026-09-18','2330','台積電',2460),('2026-09-17','2330','台積電',2425)])
        board={'date':'2026-09-18','stocks':{'2330':{'n':'台積電','r1':1.44}},'leaders':[{'name':'晶圓代工','inst_value':100,'contributors':[{'symbol':'2330','inst_value':100}]}]}
        with patch.object(r,'board_snapshot',return_value=board),patch.object(r.sector_flow,'PROTOTYPE',sector_dir),patch.object(r.sector_flow,'classification',return_value={'memberships':{'2330':['晶圓代工']},'error':''}):
            row=r.context()['candidates'][0];self.assertEqual(row['close'],2460);self.assertEqual(row['day_pct'],1.44)
