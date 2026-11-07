import copy,hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np,pandas as pd
from backend import v6_comparison as C,v6_forecast_presentation as P,v6_night_scenario as S

class PresentationTests(unittest.TestCase):
 def test_grid_is_saved_source_sensitivity_not_new_forecast(self):
  row=json.loads((S.STORE/'20261106T201544-188c40ce.json').read_text());before=copy.deepcopy(row)
  files=list(S.STORE.glob('*.json'));digests={p:p.read_bytes() for p in files}
  result=P.conditions(row)
  self.assertEqual(row,before);self.assertFalse(result['isForecast']);self.assertFalse(result['validatedForThisTime'])
  self.assertEqual([r['nightCloseAssumed'] for r in result['rows']],[row['night']['low'],row['night']['price'],row['night']['high']])
  matched=next(r for r in result['rows'] if r['nightCloseAssumed']==row['night']['price'])
  self.assertAlmostEqual(matched['up'],row['targets'][0]['up'],places=14)
  self.assertEqual(result['intervalStatus'],'withheld_unvalidated_intranight');self.assertNotIn('low80Close',result)
  self.assertEqual(digests,{p:p.read_bytes() for p in files})
 def test_tampered_model_source_or_impossible_level_fails_closed(self):
  row=json.loads((S.STORE/'20261106T201544-188c40ce.json').read_text())
  bad=copy.deepcopy(row);bad['releaseSha256']='0'*64
  with self.assertRaises(ValueError):P.conditions(bad)
  bad=copy.deepcopy(row);bad['contextSha256']='0'*64
  with self.assertRaises(ValueError):P.conditions(bad)
  bad=copy.deepcopy(row);bad['night']['price']=bad['night']['high']+1
  with self.assertRaises(ValueError):P.conditions(bad)
 def test_audit_retains_original_errors_and_past_only_candidate(self):
  d=P.audit();self.assertEqual(d['all']['n'],485);self.assertFalse(d['calibrationAdopted'])
  self.assertEqual(d['directions']['M1_vs_same_contract_futures_regular_open_to_close']['matches'],239)
  self.assertEqual(d['exact50N'],0);self.assertEqual(len(d['rounded50']),2);self.assertTrue(all(not r['y'] for r in d['rounded50']))
  self.assertTrue(all(P.C.stamp(r['latestTrainingOutcome'])<P.C.stamp(r['cutoff']) for r in d['rollingRows']))
 def test_publication_never_prepares_retired_baseline_even_when_old_fields_present(self):
  base={'version':C.VERSION,'prepared_at':'2026-11-06T21:30:00+08:00','market_as_of':'2026-11-06','forecast_dates':['2026-11-09','2026-11-10','2026-11-11'],'anchor':100,'daily_forecasts':{'version':'v6-daily-direction-2'},'shared_distribution':{'status':'reference'}}
  with tempfile.TemporaryDirectory() as t,patch.object(C.v6_daily_direction,'prepare',side_effect=AssertionError('retired')),patch.object(C.v6_daily_forecast,'prepare',side_effect=AssertionError('retired')),patch.object(C.v6_remaining,'prepare',side_effect=AssertionError('retired')):
   root=Path(t);saved=C.publish(base,root);raw=(root/(saved['id']+'.json')).read_bytes()
   self.assertEqual(saved['daily_updates'],[]);self.assertEqual(saved['remaining_updates'],[])
   with patch.object(C.v6_daily_direction,'report',side_effect=AssertionError('retired')),patch.object(C.v6_daily_forecast,'report',side_effect=AssertionError('retired')),patch.object(C.v6_remaining,'report',side_effect=AssertionError('retired')),patch.object(C.v6_forecast,'index_frame',return_value=pd.DataFrame()):
    result=C.report(store=root)
   self.assertNotIn('daily',result);self.assertNotIn('remaining',result);self.assertTrue(result['unconditional_forecast_retired']);self.assertEqual(raw,(root/(saved['id']+'.json')).read_bytes())
 def test_new_strategy_snapshot_does_not_build_unconditional_odds(self):
  dates=['2026-11-05','2026-11-06'];f=pd.DataFrame({'close':[100,99],'dd':.01,'legacy_score':40.,'legacy_raw':.4,'legacy_valid':True,'cross_score':50.,'cross_raw':.1,'cross_valid':True,'triple_weak':False,'dollar_r5':0.,'foreign_value':.3},index=dates)
  simulation={'trades':[],'completed':0,'wins':0,'win_rate':None,'points':0,'max_drawdown_pct':0,'curve':[],'open_position':None}
  with patch.object(C.v6_forecast,'session_dates',return_value=['2026-11-09','2026-11-10','2026-11-11']),patch.object(C,'latest_sources',return_value=([],[])),patch.object(C.E,'sim',return_value=simulation),patch.object(C.v6_short_simulation,'freeze',return_value={}),patch.object(C,'scenario_views',return_value={}),patch.object(C,'market_view',return_value={}),patch.object(C,'cooldown_details',return_value={}),patch.object(C.shared_distribution,'freeze',side_effect=AssertionError('retired')),patch.object(C.v6_daily_direction,'freeze',side_effect=AssertionError('retired')):
   snapshot=C.freeze(f,{},now=pd.Timestamp('2026-11-06T21:00:00+08:00'),validation={})
  self.assertNotIn('shared_distribution',snapshot);self.assertNotIn('daily_forecasts',snapshot);self.assertEqual(len(snapshot['strategies']),2)

if __name__=='__main__':unittest.main()
