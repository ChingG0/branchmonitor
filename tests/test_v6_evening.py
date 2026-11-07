import copy,csv,json,tempfile,unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch
import numpy as np,pandas as pd
from backend import v6_evening_model as M,v6_evening_forecast as E,v6_evening_inputs as I

def frame(n=420):
 rng=np.random.default_rng(912);close=100*np.exp(np.cumsum(rng.normal(.0001,.012,n)))
 return pd.DataFrame({'open':close,'high':close*1.01,'low':close*.99,'close':close},index=pd.bdate_range(end='2026-11-06',periods=n).strftime('%Y-%m-%d'))
def at(t):return E.stamp('2026-11-06T'+t+'+08:00')
class Timing(unittest.TestCase):
 def test_windows_and_DST_are_actual_timestamp_based(self):
  for t,slot in [('17:59:59',None),('18:00:00','18'),('18:30:00','18'),('18:30:01',None),('22:00:00','22'),('22:30:00','22'),('22:30:01',None)]:self.assertEqual(E.slot(at(t)),slot)
  self.assertEqual(at('22:00:00').astimezone(E.NY).tzname(),'EST');self.assertEqual(E.stamp('2026-07-06T22:00:00+08:00').astimezone(E.NY).tzname(),'EDT')
 def test_18_formal_22_revision_or_supplement_not_double_counted(self):
  a=E.classify([],'2026-11-06',at('18:01:00'),at('18:02:00'));self.assertEqual(a['role'],'18_formal')
  previous=[{**a,'anchorDate':'2026-11-06'}]
  b=E.classify(previous,'2026-11-06',at('22:01:00'),at('22:02:00'));self.assertEqual(b['role'],'22_revision');self.assertFalse(b['countsFormal'])
  self.assertEqual(E.classify([],'2026-11-06',at('22:01:00'),at('22:02:00'))['role'],'22_supplement')
  self.assertEqual(E.classify(previous,'2026-11-06',at('18:05:00'),at('18:06:00'))['role'],'same_window_revision')
 def test_outside_or_late_save_does_not_consume_formal_slot(self):
  for cut,saved in [('18:29:00','18:30:01'),('21:59:59','22:01:00'),('23:00:00','23:01:00')]:
   x=E.classify([],'2026-11-06',at(cut),at(saved));self.assertFalse(x['qualified']);self.assertFalse(x['countsFormal'])
  self.assertFalse(E.classify([],'2026-11-05',at('18:00:00'),at('18:01:00'))['qualified'])
class CausalModel(unittest.TestCase):
 def test_prefix_invariance_and_already_matured_training_endpoints(self):
  p=frame();a=M.Baseline(p);i=370;changed=p.copy();changed.iloc[i+1:]*=1000;b=M.Baseline(changed);prefix=M.Baseline(p.iloc[:i+1])
  for h in (1,2,3):
   x=a.predict(i,h);self.assertEqual(x,b.predict(i,h));self.assertEqual(x,prefix.predict(i,h));self.assertLessEqual(x['lastTrainingOutcome'],p.index[i]);self.assertTrue(x['coherent'])
 def test_probability_quantile_coherence_and_no_narrowing_based_on_clock(self):
  a=M.Baseline(frame())
  for i in range(300,420):
   for h in (1,2,3):
    x=a.predict(i,h);self.assertTrue(M.coherence(x['up'],x['anchorClose'],[x['q10Close'],x['medianClose'],x['q90Close']]));self.assertAlmostEqual(x['medianClose'],x['anchorClose']+x['medianChangePoints'])
 def test_65_threshold_and_exact50_abstain(self):
  for p,d in [(.5,'none'),(.649,'none'),(.65,'up'),(.35,'down'),(.351,'none')]:self.assertEqual(M.direction(p),d)
 def test_metrics_report_coverage_and_pinball_not_just_accuracy(self):
  base={'actualClose':110,'anchorClose':100,'medianClose':105,'q10Close':99,'q90Close':115,'actualUp':True,'up':.7}
  low={**base,'up':.55};x=M.metrics([base,low]);self.assertEqual(x['n'],2);self.assertEqual(x['thresholds'][1]['coverage'],.5);self.assertEqual(x['thresholds'][1]['accuracy'],1);self.assertEqual(x['coverage80'],1);self.assertAlmostEqual(x['pinballPoints']['0.5'],2.5)
 def test_nonoverlap_is_fixed_chronological_not_best_phase(self):
  rows=[{'originIndex':i,'targetIndex':i+3} for i in range(10)];x=M.nonoverlapping(rows);self.assertEqual([r['originIndex'] for r in x],[0,3,6,9])
 def test_shadow_model_automatically_falls_back_and_recovers_on_same60(self):
  bad=[{'qualified':True,'actualUp':False,'candidateUp':.9,'M0Up':.5} for _ in range(60)]
  good=[{'qualified':True,'actualUp':False,'candidateUp':.1,'M0Up':.5} for _ in range(60)]
  self.assertEqual(M.select_model(historical_eligible=True,shadow_rows=bad),'M0');self.assertEqual(M.select_model(historical_eligible=True,shadow_rows=bad+good),'candidate');self.assertEqual(M.select_model(historical_eligible=False,shadow_rows=good),'M0')
 def test_seasonality_never_reads_current_month_final_or_future_years(self):
  p=frame(1100);a=I.seasonal(p,'2026-05-15');changed=p.copy();changed.loc[changed.index>='2026-05-15']*=100
  self.assertEqual(a,I.seasonal(changed,'2026-05-15'));self.assertLess(a['dataThrough'],'2026-05-15')
class Inputs(unittest.TestCase):
 def bars(self):
  out=[]
  for n in range(4):
   start=at('15:00:00')+timedelta(minutes=n);end=start+timedelta(minutes=1);out.append({'contract':'202611','bar_start_at':start.isoformat(),'bar_end_at':end.isoformat(),'available_at':end.isoformat(),'close':100+n,'high':101+n,'low':99,'volume':2})
  return out
 def test_partial_or_future_available_bar_never_contributes_to_range(self):
  rows=self.bars();x=I.at_cutoff(rows,at('15:03:00').isoformat(),'202611');self.assertEqual(x['bars'],3);self.assertEqual(x['high_so_far'],103)
  rows[2]['available_at']=at('15:03:01').isoformat();x=I.at_cutoff(rows,at('15:03:00').isoformat(),'202611');self.assertEqual(x['bars'],2);self.assertEqual(x['high_so_far'],102)
 def test_missing_earlier_bar_cannot_claim_full_night_range(self):
  self.assertIsNone(I.at_cutoff(self.bars()[1:],at('15:03:00').isoformat(),'202611'))
 def test_unverified_or_future_macro_vintage_is_missing_not_zero(self):
  base={'key':'foreign','source_sha256':'0'*64,'publication_verified':True,'event_at':at('17:00:00').isoformat(),'available_at':at('18:20:00').isoformat(),'value':12}
  self.assertEqual(I.feature_asof([base],at('18:00:00').isoformat()),{});self.assertEqual(I.feature_asof([base],at('22:00:00').isoformat())['foreign']['value'],12)
  self.assertEqual(I.feature_asof([{**base,'publication_verified':'false'}],at('22:00:00').isoformat()),{})
class Ledger(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.p=frame();cls.study={'pricesSha256':'0'*64,'protocolSha256':'1'*64,'inputFile':'isolated.csv','summary':M.summary(M.Baseline(cls.p).replay(20))}
 def test_actual_saved_roles_history_immutable_and_report_read_only(self):
  sessions=list(self.p.index)+['2026-11-09','2026-11-10','2026-11-11']
  with tempfile.TemporaryDirectory() as tmp,patch.object(E,'backtest',return_value=self.study):
   store=Path(tmp);a=E.publish(self.p,sessions,store=store,at=at('18:01:00'));first={p.name:p.read_bytes() for p in store.glob('event-*')}
   b=E.publish(self.p,sessions,store=store,at=at('22:01:00'));c=E.publish(self.p,sessions,store=store,at=at('23:01:00'))
   self.assertEqual([a['role'],b['role'],c['role']],['18_formal','22_revision','unqualified']);self.assertEqual([t['up'] for t in a['targets']],[t['up'] for t in c['targets']]);self.assertEqual(first,{p.name:p.read_bytes() for p in store.glob('event-*') if p.name in first})
   before={p.name:p.read_bytes() for p in store.glob('*')};x=E.report(store=store);self.assertEqual(x['progress'][0]['saved'],1);self.assertTrue(all(h['n']==0 for h in x['progress'][0]['horizons']));self.assertEqual(before,{p.name:p.read_bytes() for p in store.glob('*')})
 def test_tampered_chain_is_rejected_and_does_not_reset(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(E,'backtest',return_value=self.study):
   store=Path(tmp);E.publish(self.p,list(self.p.index)+['2026-11-09','2026-11-10','2026-11-11'],store=store,at=at('18:01:00'));p=next(store.glob('event-*'));p.write_bytes(p.read_bytes()+b' ')
   with self.assertRaises(ValueError):E.read(store)
class StaleCash(unittest.TestCase):
 def test_already_matured_first_target_is_not_reissued_as_future(self):
  p=frame();sessions=list(p.index)+['2026-11-09','2026-11-10','2026-11-11']
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)
   with self.assertRaisesRegex(ValueError,'現貨來源過期'):E.publish(p,sessions,store=root,at=E.stamp('2026-11-09T18:01:00+08:00'))
   self.assertFalse(list(root.glob('event-*')))
class CalendarChanges(unittest.TestCase):
 def test_special_closure_changes_effective_dates_but_never_original_record(self):
  p=frame();study={'pricesSha256':'0'*64,'protocolSha256':'1'*64,'inputFile':'synthetic','summary':M.summary(M.Baseline(p).replay(20))}
  with tempfile.TemporaryDirectory() as tmp,patch.object(E,'backtest',return_value=study):
   root=Path(tmp);E.publish(p,list(p.index)+['2026-11-09','2026-11-10','2026-11-11'],store=root,at=at('18:01:00'));before=next(root.glob('event-*')).read_bytes();events=E.read(root)
   E.settle(events,root,p,at('22:01:00'),sessions=list(p.index)+['2026-11-10','2026-11-11','2026-11-12'],calendar_proof={'source':'synthetic_notice'})
   report=E.report(store=root);self.assertEqual(report['latest']['targets'][0]['targetDate'],'2026-11-10');self.assertEqual(report['latest']['targets'][0]['originalTargetDate'],'2026-11-09');self.assertEqual(next(root.glob('event-0000000*')).read_bytes(),before)
class CandidateResearch(unittest.TestCase):
 def test_no_data_never_reuses_full_night_model(self):
  from backend import v6_evening_candidates as C
  with tempfile.TemporaryDirectory() as tmp,patch.object(C,'ROOT',Path(tmp)):
   self.assertIsNone(C.load('18:00',1,at('18:01:00').isoformat()))
 def test_regular_reference_cannot_be_replaced_with_05am_night_close(self):
  from backend import v6_evening_candidates as C
  p=frame();bars=Inputs().bars();morning={**bars[0],'bar_start_at':at('04:59:00').isoformat(),'bar_end_at':at('05:00:00').isoformat(),'available_at':at('05:00:00').isoformat(),'close':100}
  self.assertIsNone(C.features_at(p,[morning,*bars],at('15:03:00').isoformat(),['night_return_scaled']))
  regular={**morning,'bar_start_at':at('13:44:00').isoformat(),'bar_end_at':at('13:45:00').isoformat(),'available_at':at('13:45:00').isoformat()}
  self.assertIsNotNone(C.features_at(p,[regular,*bars],at('15:03:00').isoformat(),['night_return_scaled']))
 def test_walk_forward_calibration_uses_only_matured_past(self):
  from backend import v6_evening_candidates as C
  p=frame(420);dates=list(p.index)
  def features(prices,bars,when,keys,**kwargs):
   day=when[:10];i=dates.index(day);f=prices.loc[:day];value=float(f.close.iloc[-1]/f.close.iloc[-2]-1)
   return {'date':day,'clock':'18:00','features':{'night_return_scaled':value},'sigma':float(kwargs['sigma_override']),'anchor':float(f.close.iloc[-1]),'contract':'202611'}
  settings={'minTrain':40,'calibrationN':60,'testTail':15,'refitEvery':3}
  with patch.object(C,'features_at',side_effect=features):
   rows,_=C.replay(p,[],'18:00',['night_return_scaled'],1,settings=settings)
   self.assertGreater(len(rows),0);self.assertTrue(all(r['trainedThrough']<r['date'] and r['calibratedThrough']<=r['date'] for r in rows))
   changed=p.copy();cut=rows[5]['date'];changed.loc[changed.index>cut]*=5
   other,_=C.replay(changed,[],'18:00',['night_return_scaled'],1,settings=settings)
   original={r['date']:r for r in rows};later={r['date']:r for r in other}
   for day in original:
    if day<=cut:self.assertAlmostEqual(original[day]['up'],later[day]['up']);self.assertAlmostEqual(original[day]['medianClose'],later[day]['medianClose'])
 def test_report_never_retrains_or_grades(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(E,'backtest',side_effect=AssertionError('report trained')),patch.object(E,'settle',side_effect=AssertionError('report graded')):
   self.assertEqual(E.report(store=Path(tmp))['status'],'not_started')
if __name__=='__main__':unittest.main()
