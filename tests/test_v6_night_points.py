import copy,json,math,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from backend import v6_night_points as P,v6_conditional as C

class NightPoints(unittest.TestCase):
 def setUp(self):
  self.row={'id':'one','savedAt':'2026-11-06T20:15:00+08:00','anchorDate':'2026-11-06','anchorClose':100.,'dailySigma':.01,'features':{k:0. for k in ['night_return_scaled','night_range_scaled','log_daily_sigma','night_age']},'targets':[{'horizon':i,'targetDate':d,'up':.9,'down':.1} for i,d in enumerate(['2026-11-09','2026-11-10','2026-11-11'],1)]}
  model={'features':list(self.row['features']),'mean':[0.]*4,'std':[1.]*4,'theta':[0.]*5,'calibration_residual_quantiles':{'q10':-1.,'q50':0.,'q90':1.},'summary':{'research_display_eligible':True,'median_mae_pct':1.,'central80_empirical_coverage':.78}}
  self.models={'models':{str(h):copy.deepcopy(model) for h in [1,2,3]}};self.cfg={'assets':{'models.json':'a'*64,'protocol.json':'b'*64}}
 def test_point_deltas_and_empirical_log_return_intervals(self):
  targets=P.estimate(self.row,self.models)
  for t in targets:
   h=t['horizon'];self.assertEqual(t['medianClose'],100);self.assertEqual(t['medianChangePoints'],0);self.assertAlmostEqual(t['low80Close'],100*math.exp(-math.sqrt(h)*.01));self.assertAlmostEqual(t['high80Close'],100*math.exp(math.sqrt(h)*.01));self.assertAlmostEqual(t['low80ChangePoints'],t['low80Close']-100)
 def test_direction_probability_is_not_used_to_invent_magnitude(self):
  a=P.estimate(self.row,self.models)
  for t in self.row['targets']:t['up']=.1;t['down']=.9
  self.assertEqual(a,P.estimate(self.row,self.models))
 def test_positive_feature_changes_points_and_invalid_sigma_stops(self):
  for m in self.models['models'].values():m['theta'][1]=1.
  a=P.estimate(self.row,self.models);self.row['features']['night_return_scaled']=1.;b=P.estimate(self.row,self.models);self.assertTrue(all(y['medianClose']>x['medianClose'] for x,y in zip(a,b)))
  self.row['dailySigma']=0
  with self.assertRaises(ValueError):P.estimate(self.row,self.models)
 def test_late_annotation_is_separate_read_only_and_time_filtered(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(P,'release',return_value=(self.cfg,self.models)),patch.object(C,'clock',return_value=C.stamp('2026-11-06T20:40:00+08:00')):
   store=Path(tmp);original=json.dumps(self.row,sort_keys=True);record=P.annotate(self.row,store=store);self.assertEqual(record['generatedAt'],'2026-11-06T20:40:00+08:00');self.assertEqual(record['scenarioSavedAt'],self.row['savedAt']);self.assertFalse(record['isOriginalIssueTimeEstimate']);self.assertEqual(original,json.dumps(self.row,sort_keys=True));before={f:f.read_bytes() for f in store.iterdir()};self.assertEqual(P.saved(self.row,store=store),record);self.assertIsNone(P.saved(self.row,store=store,now=C.stamp('2026-11-06T20:20:00+08:00')));self.assertEqual(before,{f:f.read_bytes() for f in store.iterdir()});self.row['anchorClose']=99;self.assertIsNone(P.saved(self.row,store=store))
 def test_no_retrospective_forecast_after_first_target_maturity(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(P,'release',return_value=(self.cfg,self.models)),patch.object(C,'clock',return_value=C.stamp('2026-11-09T14:00:00+08:00')):
   with self.assertRaisesRegex(ValueError,'已到期'):P.annotate(self.row,store=Path(tmp))
   self.assertEqual(list(Path(tmp).iterdir()),[])
