import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from backend import v6_allocation as a, strategies, db
from tests.test_v6_evening import frame

def quiet(**values):
 data=dict(close=100.,ma20=99.,ma60=98.,atr20=2.,annualVolPct=10.,drawdownPct=0.,stretchAtr=.5,ma5Change=1.,break20=False)
 data.update(values);return pd.Series(data,name='2026-11-06')
def previous(pct=80,day='2026-11-05'):return {'equityPct':pct,'anchorDate':day}
def targets(down=.2,q10=99):return [{'anchorClose':100.,'targetDate':d,'down':down,'q10Close':q10} for d in ['2026-11-09','2026-11-10','2026-11-11']]
class Allocation(unittest.TestCase):
 def test_causal_features_do_not_change_when_future_prices_change(self):
  p=frame();changed=p.copy();changed.iloc[350:]*=100
  pd.testing.assert_frame_equal(a.features(p).iloc[:350],a.features(changed).iloc[:350])
 def test_high_position_alone_is_not_a_reason_to_add_and_overheat_caps_it(self):
  r=a.decision(quiet(stretchAtr=3.5),previous=previous())
  self.assertEqual(r['equityPct'],35);self.assertEqual(r['binding'][0]['key'],'overheat')
 def test_emergency_reduction_is_not_limited_to_7_5pp(self):
  r=a.decision(quiet(drawdownPct=-21),previous=previous())
  self.assertEqual(r['equityPct'],10);self.assertEqual(r['changePp'],-70)
 def test_repeated_same_day_revisions_never_add_more_risk(self):
  first=a.decision(quiet(),previous=previous(20));self.assertEqual(first['equityPct'],25)
  revised=a.decision(quiet(),previous=first);self.assertEqual(revised['equityPct'],25)
  cut=a.decision(quiet(drawdownPct=-21),previous=revised);self.assertEqual(cut['equityPct'],10)
  rebound=a.decision(quiet(),previous=cut);self.assertEqual(rebound['equityPct'],10)
 def test_positive_probability_does_not_override_risk_caps(self):
  r=a.decision(quiet(stretchAtr=3.5),previous=previous(),targets=targets(.01,100.1))
  self.assertEqual(r['equityPct'],35);self.assertTrue(r['probabilityUsed'])
 def test_down_probability_and_tail_can_only_tighten(self):
  self.assertEqual(a.decision(quiet(),previous=previous(),targets=targets(.9))['equityPct'],20)
  self.assertAlmostEqual(a.decision(quiet(),previous=previous(),targets=targets(.2,80))['equityPct'],10)
 def test_forecast_with_a_different_anchor_is_rejected(self):
  t=targets();t[0]['anchorClose']=120
  with self.assertRaises(ValueError):a.decision(quiet(),targets=t)
 def test_zero_range_does_not_block_forecast_allocation(self):
  p=frame();p.loc[:,:]=100.
  self.assertEqual(a.calculate(p,targets())['equityPct'],40)
 def test_first_decision_does_not_inherit_retired_account_settings(self):
  self.assertEqual(a.decision(quiet())['equityPct'],40)
 def test_next_open_execution_cannot_capture_a_gap_already_happened(self):
  p=pd.DataFrame({'open':[100,80,82],'high':[100,80,82],'low':[100,80,82],'close':[100,80,82]},index=['2026-11-04','2026-11-05','2026-11-06'])
  r=a._simulate(p,{'2026-11-04':1.,'2026-11-05':1.},p.index[0],p.index[-1],a.policy())
  self.assertEqual(r['curve'][0]['equity'],1);self.assertLess(r['curve'][1]['equity'],1)
  self.assertGreater(r['returnPct'],0);self.assertLess(r['returnPct'],2.5)
 def test_retired_etf_entrypoint_never_fetches_or_starts_worker(self):
  with patch.object(strategies.research,'context',side_effect=AssertionError('must not fetch')),patch.object(strategies.threading,'Thread',side_effect=AssertionError('must not start')):
   self.assertEqual(strategies.refresh()['status'],'retired')
 def test_study_GET_reads_saved_file_only(self):
  with tempfile.TemporaryDirectory() as t,patch.object(db,'DATA',Path(t)),patch.object(a,'study',side_effect=AssertionError('must not calculate')):
   self.assertEqual(a.read_study()['status'],'not_started');p=Path(t)/'v6_allocation/study.json';p.parent.mkdir();p.write_text('{"status":"saved"}');self.assertEqual(a.read_study()['status'],'saved')

if __name__=='__main__':unittest.main()
