import copy,tempfile,unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import numpy as np,pandas as pd
from backend import v6_observation as O,v6_evening_model as M,v6_evening_forecast as E

def frame(n=2600):
 r=np.random.default_rng(551);close=100*np.exp(np.cumsum(r.normal(.0001,.012,n)))
 return pd.DataFrame({'open':close*np.exp(r.normal(0,.004,n)),'high':close*1.02,'low':close*.98,'close':close},index=pd.bdate_range(end='2026-11-06',periods=n).strftime('%Y-%m-%d'))
def monthly(p):
 out={}
 for d,r in p.iterrows():out[d[:7]]={'date':d,'month':d[:7],'close':float(r.close),'complete':True}
 return list(out.values())
def live(p,move=.015,quote='2026-11-06T22:02:00+08:00'):
 close=float(p.close.iloc[-1]);return {'night':[{'included':True,'state':'ready','contract':'202611','quote_at':quote,'price':close*(1+move),'day_close':close,'day_close_date':'2026-11-06','change_day_pct':move*100,'session_range_pct':3.0}],'overseas':[]}
CUT='2026-11-06T22:03:00+08:00'
class Observation(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.p=frame();cls.months=monthly(cls.p)
 def make(self,p=None,l=None,months=None):return O.Observation(self.p if p is None else p,live(self.p) if l is None else l,CUT,self.months if months is None else months)
 def test_live_night_changes_probability_and_prices_coherently(self):
  bull=self.make(l=live(self.p,.03));bear=self.make(l=live(self.p,-.03))
  for h in (1,2,3):
   a=bull.predict(h);b=bear.predict(h);self.assertGreater(a['up'],b['up']);self.assertGreater(a['medianClose'],b['medianClose'])
   for r in (a,b):
    self.assertEqual(r['model'],'OBS');self.assertFalse(r['trained']);self.assertFalse(r['calibrated']);self.assertEqual(r['up']+r['down'],1)
    self.assertLess(r['q10Close'],r['anchorClose']);self.assertGreater(r['q90Close'],r['anchorClose']);self.assertTrue(M.coherence(r['up'],r['anchorClose'],[r['q10Close'],r['medianClose'],r['q90Close']]))
 def test_stale_or_future_night_is_not_used_as_new_information(self):
  for q in ('2026-11-06T21:30:00+08:00','2026-11-06T22:04:00+08:00'):
   a=self.make(l=live(self.p,quote=q));self.assertIsNone(a.night);self.assertTrue(a.inputs['nightMissing']);self.assertEqual(a.predict(1)['model'],'OBS')
 def test_future_prices_month_final_and_years_cannot_change_forecast_or_analogs(self):
  p=self.p.copy();p.loc['2026-11-09']=p.iloc[-1]*50
  months=copy.deepcopy(self.months);months.extend([{'date':'2026-11-30','month':'2026-11','close':99999,'complete':True},{'date':'2030-11-30','month':'2030-11','close':9999,'complete':True}])
  a=self.make();b=self.make(p=p,months=months)
  self.assertEqual(a.public_context(),b.public_context())
  for h in (1,2,3):self.assertEqual(a.predict(h),b.predict(h));self.assertLessEqual(a.predict(h)['lastTrainingOutcome'],'2026-11-06')
 def test_partial_month_is_same_session_count_and_not_current_year_final(self):
  a=self.make();n=a.context['currentMonthSessionCount'];self.assertEqual(n,5)
  for c in a.analogs['cases']:
   self.assertLess(c['year'],2026);ds=[d for d in self.p.index if d.startswith(str(c['year'])+'-11')];self.assertEqual(c['matchedThrough'],ds[n-1]);self.assertEqual(len(c['path']),61);self.assertLessEqual(c['outcomes'][-1]['date'],'2026-11-06')
  self.assertFalse(a.analogs['matchingUsesFutureOutcomes'])
 def test_year_ranking_does_not_use_years_subsequent_outcomes(self):
  a=self.make();ctx=a.context;before=O.year_analogs(self.p,ctx,'2026-11-06');changed=self.p.copy()
  # Mutate December outcomes while keeping matching prefixes/availability fixed.
  for d in changed.index:
   if '-12-' in d and not d.startswith('2025'):changed.loc[d]*=3
  after=O.year_analogs(changed,ctx,'2026-11-06');self.assertEqual([(c['year'],c['distance']) for c in before['cases']],[(c['year'],c['distance']) for c in after['cases']])
 def test_long_months_mean_affects_forecast_not_only_display(self):
  months=self.months+[{'date':'1967-10-31','month':'1967-10','close':100,'complete':True},{'date':'1967-11-30','month':'1967-11','close':200,'complete':True}]
  a=self.make();b=self.make(months=months)
  self.assertNotEqual(a.context['monthStats']['meanPct'],b.context['monthStats']['meanPct']);self.assertNotEqual(a.predict(1)['seasonalDriftLog'],b.predict(1)['seasonalDriftLog'])
 def test_same_overseas_direction_is_not_counted_as_second_return(self):
  l=live(self.p);l['overseas']=[{'key':'sox','included':True,'quote_at':'2026-11-06T22:02:00+08:00','change_pct':12,'state':'ready'}]
  a=self.make();b=self.make(l=l);self.assertEqual(a.predict(1),b.predict(1))
 def test_manual_publication_preserves_ledger_and_separates_baseline_scores(self):
  p=self.p;study={'pricesSha256':'0'*64,'protocolSha256':'1'*64,'inputFile':'test.csv','summary':M.summary(M.Baseline(p).replay(5))}
  with tempfile.TemporaryDirectory() as tmp,patch.object(E,'backtest',return_value=study),patch.object(O,'load_monthly',return_value=(self.months,{'sha256':'0'*64})):
   root=Path(tmp);sessions=list(p.index)+['2026-11-09','2026-11-10','2026-11-11'];at=E.stamp(CUT)
   x=E.publish(p,sessions,live=live(p),at=at,store=root);raw={f.name:f.read_bytes() for f in root.glob('event-*')}
   self.assertEqual(x['forecastMode'],'observation');self.assertFalse(x['countsFormal']);self.assertTrue(x['countsObservation']);self.assertTrue(all(t['model']=='OBS' and t['M0Up'] is not None for t in x['targets']))
   y=E.report(store=root);self.assertEqual(y['latest']['id'],x['id']);self.assertEqual(raw,{f.name:f.read_bytes() for f in root.glob('event-*')});self.assertTrue(all(h['n']==0 for group in y['progress'] for h in group['horizons']))
 def test_computation_crossing_window_does_not_backdate_save(self):
  p=self.p;study={'pricesSha256':'0'*64,'protocolSha256':'1'*64,'inputFile':'test.csv','summary':M.summary(M.Baseline(p).replay(5))}
  with tempfile.TemporaryDirectory() as tmp,patch.object(E,'backtest',return_value=study),patch.object(O,'load_monthly',return_value=(self.months,{'sha256':'0'*64})),patch.object(E,'clock',side_effect=[E.stamp('2026-11-06T18:29:59+08:00'),E.stamp('2026-11-06T18:29:59.5+08:00'),E.stamp('2026-11-06T18:30:01+08:00')]):
   x=E.publish(p,list(p.index)+['2026-11-09','2026-11-10','2026-11-11'],store=Path(tmp),live=live(p))
   self.assertEqual(x['savedAt'],'2026-11-06T18:30:01+08:00');self.assertEqual(x['role'],'unqualified');self.assertFalse(x['countsFormal']);self.assertFalse(x['countsObservation'])
if __name__=='__main__':unittest.main()
