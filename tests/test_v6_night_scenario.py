import copy,json,tempfile,unittest,math
from pathlib import Path
import pandas as pd
from backend import v6_night_scenario as S,v6_conditional as C

class NightScenario(unittest.TestCase):
 def setUp(self):
  self.e=C.Engine();self.prices=pd.DataFrame({'close':[100*math.exp(.0005*i+.01*math.sin(i)) for i in range(61)]},index=[d.strftime('%Y-%m-%d') for d in pd.bdate_range(end='2026-11-06',periods=61)])
  self.calendar=['2026-11-06','2026-11-09','2026-11-10','2026-11-11']
  self.context={'captured_at':'2026-11-06T20:20:00+08:00','market_as_of':'2026-11-06','volatility':{'as_of':'2026-11-06','close':self.prices.close.iloc[-1]},'night':[{'contract':'202611','included':True,'state':'ready','day_close_date':'2026-11-06','quote_at':'2026-11-06T20:19:59+08:00','price':100.5,'high':101,'low':99.5,'day_close':100}]}
 def calc(self):return S.calculate(self.context,self.prices,self.calendar,engine=self.e)
 def test_fixed_model_only_and_targets_and_explicit_nonforecast(self):
  x=self.calc();self.assertEqual([t['targetDate'] for t in x['targets']],self.calendar[1:]);self.assertFalse(x['isForecast']);self.assertFalse(x['addsProspectiveSamples']);self.assertFalse(x['validatedForThisTime']);self.assertFalse(x['foreignInputsUsed']);self.assertIn('假設夜盤',x['assumption']);self.assertEqual(x['forecastInformationCutoffAssumed'],'2026-11-09T08:30:00+08:00');self.assertEqual(set(x['features']),set(self.e.models.FEATURES[:4]));self.assertEqual(x['nightEndAssumed'],'2026-11-07T05:00:00+08:00')
  for t in x['targets']:
   h=t['horizon'];model=self.e.params[f'H{h}:M1'];f=x['features'];keys=self.e.models.FEATURES[:4]
   z=self.e.models.np.array([[(f[k]-model['scaler']['mean'][i])/model['scaler']['std_ddof0'][i] for i,k in enumerate(keys)]])
   expected=float(self.e.models.initial_probability(model,z)['p_up'][0]);self.assertEqual(t['up'],expected);self.assertEqual(t['upPct']+t['downPct'],100)
 def test_stale_after_midnight_wrong_day_and_invalid_range_stop(self):
  for key,value in [('quote_at','2026-11-06T19:00:00+08:00'),('day_close_date','2026-11-05'),('low',102)]:
   old=self.context['night'][0][key];self.context['night'][0][key]=value
   with self.assertRaises(ValueError):self.calc()
   self.context['night'][0][key]=old
  self.context['captured_at']='2026-11-07T06:00:00+08:00'
  with self.assertRaisesRegex(ValueError,'同一夜盤'):self.calc()
 def test_missing_or_mismatched_anchor_and_calendar_stop(self):
  with self.assertRaisesRegex(ValueError,'三個交易日'):S.calculate(self.context,self.prices,self.calendar[:2],engine=self.e)
  self.context['volatility']['close']=1
  with self.assertRaisesRegex(ValueError,'錨點'):self.calc()
 def test_higher_night_close_increases_scenario_and_no_parameter_mutation(self):
  before=json.dumps(self.e.params,sort_keys=True);a=self.calc();self.context['night'][0]['price']=100.9;b=self.calc();self.assertTrue(all(y['up']>x['up'] for x,y in zip(a['targets'],b['targets'])));self.assertEqual(before,json.dumps(self.e.params,sort_keys=True))
 def test_readback_is_only_saved_same_anchor_targets_and_hash_verified(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);context=p/'context.json';context.write_text(json.dumps(self.context));x={**self.calc(),'id':'one','contextFile':str(context),'contextSha256':C.digest(context.read_bytes())};(p/'one.json').write_bytes(C.encode(x));before={f:f.read_bytes() for f in p.iterdir()}
   kwargs={'store':p,'now':C.stamp('2026-11-06T20:21:00+08:00')}
   self.assertEqual(S.latest('2026-11-06',self.calendar[1:],**kwargs)['id'],'one');self.assertIsNone(S.latest('2026-11-05',self.calendar[1:],**kwargs));self.assertIsNone(S.latest('2026-11-06',['2026-11-09'],**kwargs));self.assertEqual(before,{f:f.read_bytes() for f in p.iterdir()});context.write_text('{}');self.assertIsNone(S.latest('2026-11-06',self.calendar[1:],**kwargs))


class ScenarioHistory(NightScenario):
 def save(self,folder,ident='one',when='2026-11-06T20:20:00+08:00',price=100.5):
  self.context['night'][0]['price']=price;context=folder/(ident+'-source.json');context.write_text(json.dumps(self.context));row={**self.calc(),'id':ident,'savedAt':when,'contextFile':str(context),'contextSha256':C.digest(context.read_bytes())};(folder/(ident+'.json')).write_bytes(C.encode(row));return row
 def test_all_vintages_preserved_with_same_anchor_targets(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);a=self.save(p);before=(p/'one.json').read_bytes();self.save(p,'two','2026-11-06T20:21:00+08:00',100.9)
   report=S.history_report(store=p,outcome_store=p/'answers',now=C.stamp('2026-11-06T20:22:00+08:00'));self.assertEqual(len(report['groups']),1);g=report['groups'][0];self.assertEqual([v['id'] for v in g['snapshots']],['one','two']);self.assertEqual(g['snapshots'][0]['targets'],a['targets']);self.assertEqual(g['snapshots'][0]['targets'][0]['targetDate'],'2026-11-09');self.assertEqual(g['outcomes'],{});self.assertEqual(before,(p/'one.json').read_bytes())
 def test_future_results_never_appear_before_deadline_and_answers_never_rewrite_odds(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);self.save(p);before=(p/'one.json').read_bytes();prices=pd.concat([self.prices,pd.DataFrame({'close':[98.]},index=['2026-11-09'])]);answers=p/'answers'
   self.assertEqual(S.mature(prices,store=p,outcome_store=answers,now=C.stamp('2026-11-09T12:00:00+08:00')),[])
   result=S.mature(prices,store=p,outcome_store=answers,now=C.stamp('2026-11-09T13:31:00+08:00'));self.assertEqual(len(result),1);self.assertEqual(result[0]['close'],98.)
   old=S.history_report(store=p,outcome_store=answers,now=C.stamp('2026-11-09T12:00:00+08:00'));self.assertEqual(old['groups'][0]['outcomes'],{})
   new=S.history_report(store=p,outcome_store=answers,now=C.stamp('2026-11-09T13:32:00+08:00'));self.assertEqual(new['groups'][0]['outcomes']['1']['close'],98.);self.assertEqual(before,(p/'one.json').read_bytes())
   prices.loc['2026-11-09','close']=999.;self.assertEqual(S.mature(prices,store=p,outcome_store=answers,now=C.stamp('2026-11-09T14:00:00+08:00')),[]);self.assertEqual(new['groups'][0]['outcomes']['1']['close'],98.)
 def test_different_threshold_or_model_is_another_root_not_a_revision(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);self.save(p);b=self.save(p,'two','2026-11-06T20:21:00+08:00');b['anchorClose']+=1;(p/'two.json').write_bytes(C.encode(b));x=S.history_report(store=p,outcome_store=p/'answers',now=C.stamp('2026-11-06T20:22:00+08:00'));self.assertEqual(len(x['groups']),2);self.assertTrue(all(len(g['snapshots'])==1 for g in x['groups']))
