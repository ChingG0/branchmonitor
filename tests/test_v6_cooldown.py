import unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from backend import v6_engine as E,v6_comparison as C,v6_trade_policy as P

class CooldownPolicyTests(unittest.TestCase):
 def fixture(self):
  days=pd.bdate_range('2025-01-01',periods=20).strftime('%Y-%m-%d').tolist()
  f=pd.DataFrame(dict(open=100.,close=100.,atr=10.,prob_signal=True),index=days)
  q={(d,c):dict(open=100.,high=101.,low=99.,close=100.) for d in days for c in ['202503','202504']}
  return f,q,days
 def simulate(self,n=None):
  f,q,ds=self.fixture();cfg=dict(family='probability',level=0,take=1.5,stop=1.5,hold=1)
  if n is not None:cfg['cooldown_days']=n
  E.CONTRACT_CACHE.clear();return E.sim(f,q,cfg,ds[1],ds[-1]),ds
 def test_full_cooldown_sessions_and_no_same_day_reentry(self):
  for n in [0,2,5]:
   with self.subTest(n=n):
    r,ds=self.simulate(n);self.assertEqual([t['entry_date'] for t in r['trades']],[ds[i] for i in range(1,len(ds),n+1)])
    self.assertTrue(all(t['entry_date']==t['exit_date'] for t in r['trades']))
    self.assertEqual(len({t['entry_date'] for t in r['trades']}),r['completed'])
 def test_original_default_still_five_for_old_research_calls(self):
  self.assertEqual(self.simulate()[0],self.simulate(5)[0])
 def test_reject_invalid_cooldown(self):
  for x in [True,-1,2.5,'2']:
   with self.subTest(x=x),self.assertRaises(ValueError):self.simulate(x)
 def test_calendar_notes_match_zero_and_two_not_five(self):
  with patch.object(C.v6_forecast,'session_dates',return_value=['2026-11-02','2026-11-03','2026-11-04']):
   r=C.cooldown_details('2026-10-30','2026-10-30',2,total_sessions=2)
  self.assertEqual(r['total_sessions'],2);self.assertEqual(r['blocked_dates'],['2026-11-02','2026-11-03']);self.assertEqual(r['earliest_review'],'2026-11-04');self.assertIn('2個',r['note'])
  r=C.cooldown_details('2026-10-30','2026-10-30',0,total_sessions=0);self.assertEqual(r['blocked_dates'],[]);self.assertIn('最快下一交易日',r['note'])
 def test_comparison_routes_each_strategy_and_saves_version(self):
  f=pd.DataFrame({'close':[100.,100.],'high':[103.,103.],'low':[97.,97.],'ma5':[102.,102.],'dd':[.01,.01],
    'legacy_raw':[.7,.7],'legacy_valid':[True,True],'legacy_score':[70.,70.],
    'cross_raw':[.4,.4],'cross_valid':[True,True],'cross_score':[80.,80.],
    'triple_weak':[0.,0.],'dollar_r5':[.01,.01],'foreign_value':[.6,.6],'tsmc_gap5':[-.01,-.01]},index=['2026-11-03','2026-11-04'])
  captured=[]
  def run(frame,quotes,cfg,*args):
   captured.append(cfg['cooldown_days']);return dict(trades=[dict(exit_date='2026-11-04')],open_position=None,completed=1,wins=1,win_rate=100.,points=1.+cfg['cooldown_days'],max_drawdown_pct=0.,curve=[])
  dates=['2026-11-05','2026-11-06','2026-11-09']
  with patch.object(C.v6_forecast,'session_dates',return_value=dates),patch.object(C,'latest_sources',return_value=([],[])),patch.object(C.E,'sim',side_effect=run),patch.object(C.shared_distribution,'freeze',return_value={'status':'reference','classes':[]}),patch.object(C.v6_short_simulation,'freeze',return_value={'strategy_key':'v6-2'}) as primary:
   r=C.freeze(f,{},now='2026-11-04T21:00:00+08:00',validation={})
  self.assertEqual(r['short_simulation'],{'strategy_key':'v6-2'})
  self.assertEqual(primary.call_args.kwargs['simulation']['points'],3.)
  self.assertEqual(captured,[0,2]);self.assertEqual(r['trade_policy']['version'],P.VERSION)
  self.assertEqual([s['cooldown']['total_sessions'] for s in r['strategies']],[0,2]);self.assertEqual([s['cooldown_sessions'] for s in r['strategies']],[0,2])
  self.assertEqual(r['strategies'][0]['action'],'達觀察線，下一開盤檢查跳空條件');self.assertIn('冷卻中',r['strategies'][1]['action'])

if __name__=='__main__':unittest.main(verbosity=2)
