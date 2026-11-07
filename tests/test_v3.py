import copy
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from backend import v3, v3_market_study

class V3Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'v3-account.json';self.source=Path(self.tmp.name)/'prices.sqlite3'
        with sqlite3.connect(self.source) as c:
            c.execute('CREATE TABLE prices(date TEXT,symbol TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL)')
        self.ctx=self.context('2026-10-13')

    def context(self,day):
        return dict(as_of=day,ready=True,blocked=False,rs_date=day,branch_date=day,breadth_pct=60,
                    indices={'TAIEX':dict(close=100,sma20=95,above=True)},stock_stats={'2330':dict(close=100,atr=2)},
                    candidates=[dict(symbol='2330',name='台積電',close=100,atr=2,score=90,status='待手動研究')])

    def put(self,day,symbol='2330',op=100,hi=102,lo=99,cp=101):
        with sqlite3.connect(self.source) as c:
            c.execute('INSERT INTO prices VALUES (?,?,?,?,?,?,?)',(day,symbol,op,hi,lo,cp,1_000_000))

    def init(self):
        v3.initialize(self.ctx,'根據當時已發布資料保留現金，待下一次手動研究。',self.path)
        self.put('2026-10-13')

    def decision(self,**extra):
        result=dict(expected_revision=0,as_of='2026-10-13',analysis='兩個指數與股價資料均已確認，先研究一檔，設定失效條件。',target_exposure_pct=50,actions=[dict(side='buy',symbol='2330',budget=40_000,reason='吸籌轉強',thesis='已驗證的供需假設',invalidation='需求或價格跌破假設')])
        result.update(extra);return result

    def test_report_never_initializes_or_revalues(self):
        with patch.object(v3.v2,'report',side_effect=AssertionError('不能重算')):
            before=set(Path(self.tmp.name).iterdir());r=v3.report(self.path)
            self.assertEqual(set(Path(self.tmp.name).iterdir()),before)
            self.assertFalse(r['available']);self.assertEqual(r['cash'],100000)
            self.init();raw=self.path.read_bytes()
            for _ in range(3):v3.report(self.path)
            self.assertEqual(self.path.read_bytes(),raw)
            self.assertEqual(v3.report(self.path)['trades'],[])

    def test_init_cannot_reset_account(self):
        self.init();original=self.path.read_bytes()
        with self.assertRaises(ValueError):v3.initialize(self.ctx,'test',self.path)
        self.assertEqual(self.path.read_bytes(),original)

    def test_plan_not_filled_same_day_then_only_manual_reconcile(self):
        self.init();state=v3.apply_review(self.decision(),self.source,self.path,self.ctx)
        self.assertEqual(state['holdings'],[]);self.assertEqual(state['cash'],100000)
        self.assertEqual(len(state['pending_orders']),1)
        # Source advances; GET continues to return the saved state unchanged.
        raw=self.path.read_bytes();self.put('2026-10-14',cp=102)
        self.assertEqual(v3.report(self.path)['as_of'],'2026-10-13');self.assertEqual(raw,self.path.read_bytes())
        # Tests model the committed order as submitted on the signal day.
        state['pending_orders'][0]['not_before_date']='2026-10-13'
        settled=v3.reconcile(state,['2026-10-14'],{'2026-10-14':{'2330':dict(open=100,high=103,low=99,close=102,volume=1e6)}})
        p=settled['holdings'][0];self.assertEqual(p['entry_date'],'2026-10-14')
        self.assertLessEqual(p['cost'],40000);self.assertAlmostEqual(p['cost'],p['qty']*100*(1+v3.v2.BUY))
        self.assertAlmostEqual(settled['equity'],100000+settled['realized_pnl']+settled['unrealized_pnl'])
        self.assertEqual(raw,self.path.read_bytes())

    def funded(self):
        state=v3._empty();state.update(as_of='2026-10-12',start='2026-10-12',order_events=[])
        cost=100*100*(1+v3.v2.BUY);state['cash']-=cost
        state['holdings']=[dict(symbol='2330',name='台積電',qty=100,cost=cost,entry_date='2026-10-12',entry_price=100,signal_date='2026-10-11',mark_price=100,mark_date='2026-10-12',peak=100,stop=94,thesis='研究',invalidation='失效')]
        v3._mark(state,'2026-10-12',{})
        return state

    def test_standing_stop_fixed_between_manual_reviews_and_gap(self):
        state=self.funded()
        bars={'2026-10-13':{'2330':dict(open=110,high=121,low=100,close=120,volume=1e6)},'2026-10-14':{'2330':dict(open=90,high=95,low=85,close=91,volume=1e6)}}
        interim=v3.reconcile(state,['2026-10-13'],bars)
        self.assertEqual(interim['holdings'][0]['stop'],94) # no automatic trailing
        self.assertEqual(interim['holdings'][0]['peak'],120)
        end=v3.reconcile(state,sorted(bars),bars)
        t=end['trades'][0];self.assertEqual(t['exit_price'],90);self.assertLess(t['return_pct'],-8)
        self.assertAlmostEqual(end['equity'],100000+t['pnl'])
        self.assertEqual(state['as_of'],'2026-10-12')

    def test_later_price_does_not_change_earlier_account(self):
        state=self.funded();a={'2026-10-13':{'2330':dict(open=101,high=105,low=99,close=104,volume=1e6)}}
        b=copy.deepcopy(a);b['2026-10-14']={'2330':dict(open=300,high=400,low=200,close=350,volume=1e6)}
        self.assertEqual(v3.reconcile(state,['2026-10-13'],a),v3.reconcile(state,['2026-10-13'],b))

    def test_unknown_or_unfillable_prices_do_not_create_fake_fill(self):
        state=self.funded();missing=v3.reconcile(state,['2026-10-13'],{})
        self.assertEqual(len(missing['holdings']),1);self.assertEqual(missing['holdings'][0]['mark_date'],'2026-10-12')
        locked=v3.reconcile(state,['2026-10-13'],{'2026-10-13':{'2330':dict(open=90,high=90,low=90,close=90,volume=100)}})
        self.assertTrue(locked['holdings'][0]['pending_exit']);self.assertFalse(locked['trades'])

    def test_invalid_duplicate_stale_and_oversized_decisions_atomic(self):
        self.init();raw=self.path.read_bytes()
        bad=[self.decision(expected_revision=3),self.decision(as_of='2026-10-12'),self.decision(target_exposure_pct=float('nan')),
             self.decision(actions=[dict(side='buy',symbol='2330',budget=60000,reason='x',thesis='x',invalidation='x')]),
             self.decision(actions=self.decision()['actions']*2),self.decision(actions=[dict(side='sell',symbol='2330',reason='x')])]
        for d in bad:
            with self.assertRaises(ValueError):v3.apply_review(d,self.source,self.path,self.ctx)
            self.assertEqual(self.path.read_bytes(),raw)
        state=v3.apply_review(self.decision(),self.source,self.path,self.ctx)
        with self.assertRaises(ValueError):v3.apply_review(self.decision(),self.source,self.path,self.ctx)
        self.assertEqual(json.loads(self.path.read_text())['revision'],state['revision'])

    def test_blocked_market_rejects_buy_and_allows_cash_review(self):
        self.init();blocked=dict(self.ctx,blocked=True)
        with self.assertRaises(ValueError):v3.apply_review(self.decision(),self.source,self.path,blocked)
        s=v3.apply_review(self.decision(actions=[],target_exposure_pct=0),self.source,self.path,blocked)
        self.assertEqual(s['cash'],100000);self.assertEqual(len(s['journal']),2)

    def test_only_explicit_review_can_raise_stop_prospectively(self):
        self.init();state=self.funded();state.update(created_at=v3.timestamp(),revision=0,version=v3.VERSION,journal=[])
        v3._atomic(self.path,state)
        self.put('2026-10-13','TAIEX',100,101,99,100)
        with sqlite3.connect(self.source) as c:c.execute("UPDATE prices SET open=108,high=120,low=95,close=110 WHERE symbol='2330'")
        ctx=self.context('2026-10-13');ctx['stock_stats']['2330']=dict(close=110,atr=2)
        result=v3.apply_review(self.decision(actions=[],target_exposure_pct=100),self.source,self.path,ctx)
        self.assertEqual(result['trades'],[]) # cannot retrospectively stop at 106 against today's 95 low
        self.assertEqual(result['holdings'][0]['stop'],106)

    def test_gap_entry_cancel_and_same_day_stop(self):
        self.init();state=v3.apply_review(self.decision(),self.source,self.path,self.ctx)
        state.setdefault('order_events',[]);state['pending_orders'][0]['not_before_date']='2026-10-13'
        x=v3.reconcile(state,['2026-10-14'],{'2026-10-14':{'2330':dict(open=105,high=106,low=100,close=103,volume=1e6)}})
        self.assertEqual(x['holdings'],[]);self.assertEqual(x['order_events'][0]['status'],'cancelled')
        x=v3.reconcile(state,['2026-10-14'],{'2026-10-14':{'2330':dict(open=100,high=102,low=90,close=93,volume=1e6)}})
        self.assertEqual(x['holdings'],[]);self.assertEqual(x['trades'][0]['exit_price'],94)

    def test_market_study_is_separate_frozen_snapshot(self):
        self.init();account_before=self.path.read_bytes()
        for i in range(19):
            day=(date(2026,9,1)+timedelta(days=i)).isoformat()
            self.put(day,'TAIEX',cp=100+i)
            self.put(day,'TPEX',cp=50+i)
        self.put('2026-10-13','TAIEX',cp=105)
        self.put('2026-10-13','TPEX',cp=55)
        ctx=dict(self.ctx,indices={'TAIEX':dict(close=105,sma20=108,above=False),
                                   'TPEX':dict(close=55,sma20=58,above=False)},blocked=True)
        decision=dict(expected_revision=0,as_of='2026-10-13',
                      analysis='兩個市場同時低於20MA，先保留現金並觀察有效收盤回升。',
                      buy_condition='任一市場收盤重新站上當日20MA，仍須同日資料和人工審核。',
                      stock_target_pct=0,bond_target_pct=0,
                      bond_reason='目前無債券價格與利率研究，短期模擬帳戶保留現金。',sources=[])
        path=v3_market_study.study_path(self.path)
        study=v3_market_study.generate(decision,self.source,path,ctx)
        self.assertEqual(study['revision'],1)
        self.assertEqual(study['allocation'],{'stock_pct':0.0,'bond_pct':0.0,'cash_pct':100.0})
        self.assertGreater(study['indices']['TAIEX']['next_close_to_sma20'],0)
        self.assertTrue(study['indices']['TAIEX']['resistance'])
        self.assertEqual(self.path.read_bytes(),account_before)
        with patch.object(v3.v2,'report',side_effect=AssertionError('GET不能重算')):
            self.assertEqual(v3.report(self.path)['market_study'],study)
        with self.assertRaises(ValueError):
            v3_market_study.generate(decision,self.source,path,ctx)
        self.assertEqual(self.path.read_bytes(),account_before)

    def test_market_study_rejects_missing_index_and_invalid_allocation(self):
        ctx=dict(self.ctx,indices={'TAIEX':dict(close=100,sma20=95,above=True),
                                   'TPEX':dict(close=50,sma20=49,above=True)})
        decision=dict(expected_revision=0,as_of='2026-10-13',
                      analysis='兩個市場都在20MA上方，但資料不足先不計算觀察位階。',
                      buy_condition='若資料完整且通過人工審核後才考慮買入。',
                      stock_target_pct=70,bond_target_pct=40,
                      bond_reason='債券商品資料尚未核對，不建議強制配置。')
        path=v3_market_study.study_path(self.path)
        with self.assertRaises(ValueError):
            v3_market_study.generate(decision,self.source,path,ctx)
        self.assertFalse(path.exists())
        decision['bond_target_pct']=0
        with self.assertRaises(ValueError):
            v3_market_study.generate(decision,self.source,path,ctx)
        self.assertFalse(path.exists())

    def test_after_7_manual_review_syncs_dated_study_without_get_recompute(self):
        self.init()
        for i in range(19):
            day=(date(2026,9,1)+timedelta(days=i)).isoformat()
            self.put(day,'TAIEX',cp=100+i)
            self.put(day,'TPEX',cp=50+i)
        self.put('2026-10-13','TAIEX',cp=105)
        self.put('2026-10-13','TPEX',cp=55)
        now=datetime(2026,10,14,7,0,tzinfo=v3.TZ)
        state=v3.apply_review(self.decision(),self.source,self.path,self.ctx,now=now)
        study=state['market_study_sync']['study']
        self.assertEqual(study['as_of'],'2026-10-13')
        self.assertEqual(study['recorded_at'],'2026-10-14T07:00:00+08:00')
        self.assertEqual(study['allocation']['stock_pct'],50)
        self.assertEqual(study['revision'],1)
        raw=self.path.read_bytes()
        with patch.object(v3.v2,'report',side_effect=AssertionError('GET不能重算')):
            self.assertEqual(v3.report(self.path)['market_study'],study)
        self.assertEqual(raw,self.path.read_bytes())
        self.assertFalse(v3_market_study.study_path(self.path).exists())

    def test_before_7_review_does_not_claim_morning_study(self):
        self.init()
        state=v3.apply_review(self.decision(),self.source,self.path,self.ctx,
                              now=datetime(2026,10,14,6,59,tzinfo=v3.TZ))
        self.assertNotIn('market_study_sync',state)
        self.assertIsNone(v3.report(self.path)['market_study'])

    def test_missing_index_does_not_relabel_old_study_as_synced(self):
        self.init()
        old_study={'version':v3_market_study.VERSION,'revision':1,'as_of':'2026-10-12',
                   'recorded_at':'2026-10-13T10:00:00+08:00'}
        v3._atomic(v3_market_study.study_path(self.path),old_study)
        state=v3.apply_review(self.decision(),self.source,self.path,self.ctx,
                              now=datetime(2026,10,14,7,30,tzinfo=v3.TZ))
        self.assertIsNone(state['market_study_sync']['study'])
        self.assertIsNone(v3.report(self.path)['market_study'])
        self.assertIn('缺少可驗證',v3.report(self.path)['market_study_status'])

    def test_no_integration_update_route_or_scheduler(self):
        root=Path(v3.__file__).resolve().parents[1]
        updater=(root/'backend/full_update.py').read_text();server=(root/'backend/server.py').read_text()
        self.assertNotIn('v3',updater.lower())
        self.assertNotIn("'/api/v3'",server.split('def do_POST')[1])
        view=(root/'frontend/src/V3Panel.vue').read_text()
        self.assertNotIn('fetch(',view);self.assertNotIn('@refresh',view)
        self.assertIn('v3_result=v3.report()', (root/'backend/daily_follow.py').read_text())

if __name__=='__main__':unittest.main()
