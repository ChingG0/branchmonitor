import copy,gzip,json,tempfile,unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from backend import v11,v2,alpha_beta

class V11Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=Path(self.tmp.name)/'v11'
        self.now=datetime(2026,10,22,13,tzinfo=ZoneInfo('Asia/Taipei'))
        self.days=['2026-10-20','2026-10-21','2026-10-22','2026-10-23','2026-10-26']
        self.inputs=dict(as_of='2026-10-21',dates=self.days[:2],prices=[dict(symbol='2330',name='測試股',market='TWSE',date=d,open=100,high=102,low=99,close=100,volume=1_000_000) for d in self.days[:2]],events=[],unsafe=[])
        self.candidate=dict(symbol='2330',name='測試股',close=100,alpha_pct=.1,alpha_t=2,beta=1,alpha_ci_pct=[0,.2],atr=2,prior_volume=1_000_000,flags=[])
        self.decision=dict(as_of='2026-10-21',expected_revision=0,analysis='依已取得完整收盤資料建立V11手動帳戶，先保存開盤計畫。',sources=[])
    def publish(self,decision=None,inputs=None,now=None,blocked=False,candidates=None):
        data=inputs or self.inputs;ctx=dict(as_of=data['as_of'],blocked=blocked,candidates=copy.deepcopy([self.candidate] if candidates is None else candidates),indices={},coverage={})
        with patch.object(v11,'load_inputs',return_value=(data,'a'*64,json.dumps(data).encode())),patch.object(v11,'context',return_value=ctx):
            return v11.publish(decision or self.decision,store=self.store,now=now or self.now)
    def later(self):
        result=copy.deepcopy(self.inputs);result['as_of']=self.days[-1];result['dates']=self.days
        for d in self.days[2:]:result['prices'].append(dict(self.inputs['prices'][0],date=d))
        return result
    def test_get_is_read_only_even_after_market_changes(self):
        with patch.object(v11,'load_inputs',side_effect=AssertionError('no source reads')),patch.object(v11,'context',side_effect=AssertionError('no compute')):
            self.assertFalse(v11.report(self.store)['available']);self.assertFalse(self.store.exists())
        self.publish();before={str(p):p.read_bytes() for p in self.store.rglob('*') if p.is_file()}
        with patch.object(v11,'load_inputs',side_effect=AssertionError('no source reads')),patch.object(v11,'reconcile',side_effect=AssertionError('no trade')):
            self.assertEqual(v11.report(self.store)['equity'],1_000_000.)
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.store.rglob('*') if p.is_file()})
    def test_initial_cash_only_and_plan_never_backfills_past_open(self):
        first=self.publish();self.assertEqual(first['holdings'],[]);self.assertEqual(first['portfolio_return_pct'],0)
        self.assertEqual(first['pending_orders'][0]['not_before_date'],'2026-10-22')
        old=next(self.store.glob('*.json'));frozen=old.read_bytes()
        next_=self.publish(dict(self.decision,expected_revision=1,as_of='2026-10-26'),self.later(),self.now.replace(day=26,hour=18),candidates=[])
        self.assertEqual(next_['holdings'][0]['entry_date'],'2026-10-23')
        self.assertEqual(next_['days']['2026-10-22']['buy'],[])
        self.assertEqual(next_['days']['2026-10-23']['buy'],['2330'])
        self.assertEqual(old.read_bytes(),frozen);self.assertEqual(len(next_['snapshot_history']),2)
        self.assertAlmostEqual(next_['initial']+next_['realized_pnl']+next_['unrealized_pnl'],next_['equity'])
    def test_preopen_plan_can_use_upcoming_same_day_open(self):
        first=self.publish(now=self.now.replace(hour=8));self.assertEqual(first['pending_orders'][0]['not_before_date'],'2026-10-21')
        data=self.later();data['dates']=data['dates'][:3];data['as_of']='2026-10-22';data['prices']=data['prices'][:3]
        result=self.publish(dict(self.decision,expected_revision=1,as_of='2026-10-22'),data,self.now.replace(hour=18),candidates=[])
        self.assertEqual(result['holdings'][0]['entry_date'],'2026-10-22')
    def test_no_new_rank_trades_between_reviews_and_market_block(self):
        first=self.publish(blocked=True);self.assertFalse(first['pending_orders'])
        state=v11.reconcile(first,self.later());self.assertFalse(state['holdings']);self.assertFalse(state['trades'])
        self.assertEqual(state['cash'],1_000_000)
    def test_gap_stop_uses_gap_price_and_does_not_rebuy_without_saved_plan(self):
        self.publish(now=self.now.replace(hour=8));data=self.later()
        for b in data['prices']:
            if b['date']=='2026-10-23':b.update(open=80,high=82,low=79,close=81)
        result=self.publish(dict(self.decision,expected_revision=1,as_of='2026-10-26'),data,self.now.replace(day=26,hour=18),candidates=[])
        self.assertEqual(len(result['trades']),1);self.assertEqual(result['trades'][0]['exit_price'],80)
        self.assertEqual(result['trades'][0]['holding_sessions'],1)
        self.assertFalse(result['holdings']);self.assertLess(result['trades'][0]['return_pct'],-8)
    def test_dividend_receivable_conserves_account_and_is_not_cash(self):
        first=self.publish(now=self.now.replace(hour=8));data=self.later()
        data['events']=[dict(symbol='2330',date='2026-10-23',cash=5,unsafe=False)]
        for b in data['prices']:
            if b['date']>='2026-10-23':b.update(open=95,high=97,low=94,close=95)
        result=self.publish(dict(self.decision,expected_revision=1,as_of='2026-10-26'),data,self.now.replace(day=26,hour=18),candidates=[])
        pos=result['holdings'][0];self.assertEqual(result['dividend_receivable'],pos['qty']*5)
        self.assertAlmostEqual(result['cash'],1_000_000-pos['cost'])
        self.assertAlmostEqual(result['equity'],result['cash']+result['dividend_receivable']+pos['value'])
        self.assertAlmostEqual(result['equity'],1_000_000+result['unrealized_pnl'])
    def test_unresolved_corporate_action_aborts_entire_publication(self):
        self.publish(now=self.now.replace(hour=8));before={str(p):p.read_bytes() for p in self.store.rglob('*') if p.is_file()};data=self.later()
        data['events']=[dict(symbol='2330',date='2026-10-23',cash=None,unsafe=True)]
        with self.assertRaisesRegex(ValueError,'未還原公司事件'):
            self.publish(dict(self.decision,expected_revision=1,as_of='2026-10-26'),data,self.now.replace(day=26,hour=18))
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.store.rglob('*') if p.is_file()})
    def test_stale_revision_intraday_future_and_backward_rejected(self):
        self.publish();before=next(self.store.glob('*.json')).read_bytes()
        for d,t in [(self.decision,self.now),(dict(self.decision,as_of='2026-10-22',expected_revision=1),self.now),(dict(self.decision,as_of='2026-10-23',expected_revision=1),self.now),(dict(self.decision,expected_revision=1),self.now.replace(hour=12))]:
            with self.assertRaises(ValueError):self.publish(d,now=t)
        self.assertEqual(len(list(self.store.glob('*.json'))),1);self.assertEqual(before,next(self.store.glob('*.json')).read_bytes())
    def test_unfillable_one_shot_order_is_cancelled(self):
        self.publish(now=self.now.replace(hour=8));data=self.later();data['prices'][2].update(high=100,low=100)
        result=self.publish(dict(self.decision,expected_revision=1,as_of='2026-10-26'),data,self.now.replace(day=26,hour=18),candidates=[])
        self.assertFalse(result['holdings']);self.assertTrue(any(r['status']=='cancelled' for r in result['order_events']))

class V11ContextTests(unittest.TestCase):
    def fixture(self):
        dates=[d.strftime('%Y-%m-%d') for d in pd.bdate_range(end='2026-10-28',periods=126)]
        j=np.arange(125);x=.001+.004*np.sin(j);y=.004+1.1*x+.0015*np.cos(j*1.7)
        market=np.r_[100,np.cumprod(1+x)*100];stock=np.r_[100,np.cumprod(1+y)*100];prices=[]
        for i,d in enumerate(dates):
            for s,values in [('2330',stock),('TAIEX',market),('TPEX',market)]:prices.append(dict(symbol=s,name=s,market='TWSE',date=d,close=float(values[i]),open=float(values[i])*.995,high=float(values[i])*1.02,low=float(values[i])*.98,volume=1_000_000))
        data=dict(as_of=dates[-1],dates=dates,prices=prices,benchmarks={'TAIEX_TR':dict(zip(dates,market))},events=[],unsafe=[],dispositions=[],disposition_coverage={'TWSE':True,'TPEX':True},disposition_observed_at='2026-10-29T08:00:00+08:00',amounts=[dict(date=d,market='TWSE',values={'2330':1e9}) for d in dates[-20:]])
        return data,x,y
    def test_common_benchmark_hac_and_official_amounts(self):
        inputs,x,y=self.fixture();now=datetime(2026,10,29,10,tzinfo=ZoneInfo('Asia/Taipei'));ctx=v11.context(inputs,now)
        row=ctx['candidates'][0];fit=alpha_beta.fit_market(y[-120:],x[-120:])
        self.assertEqual(row['symbol'],'2330');self.assertAlmostEqual(row['alpha_t'],fit['alpha_t'],places=10)
        self.assertAlmostEqual(row['beta'],fit['beta'],places=10);self.assertEqual(row['avg_amount'],1e9)
        inputs['amounts'][0]['values']={};self.assertFalse(v11.context(inputs,now)['candidates'])
    def test_disposition_and_unverified_event_exclude_at_publication(self):
        inputs,_,_=self.fixture();now=datetime(2026,10,29,10,tzinfo=ZoneInfo('Asia/Taipei'))
        inputs['dispositions']=[dict(symbol='2330',published='2026-10-28',start='2026-10-29',end='2026-11-03')]
        self.assertFalse(v11.context(inputs,now)['candidates'])
        inputs['dispositions']=[];inputs['events']=[dict(symbol='2330',date=inputs['dates'][-10],unsafe=True,cash=None)]
        self.assertFalse(v11.context(inputs,now)['candidates'])
    def test_stale_disposition_observation_requires_explicit_data_refresh(self):
        inputs,_,_=self.fixture();now=datetime(2026,10,30,10,tzinfo=ZoneInfo('Asia/Taipei'))
        with self.assertRaisesRegex(ValueError,'不是本次發布日'):v11.context(inputs,now)

if __name__=='__main__':unittest.main()
