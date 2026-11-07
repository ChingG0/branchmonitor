import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from backend import v6_daily_direction as B,v6_daily_forecast as L,v6_shared_distribution as D,v6_comparison as C
from test_v6_daily_forecast import calendar

class BinaryDailyTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.p=D.prices();cls.cfg={**B.release(),'activated_at':'2026-10-01T00:00:00+08:00'}
 def root(self,day,legacy=False):
  cal=calendar();dates=B.sessions(day,3,cal);stamp=dates[0]+'T08:35:00+08:00'
  shared=D.calculate(self.p,day,float(self.p.at[day,'close']));shared.update(version=D.VERSION,method_version=D.METHOD,market_as_of=day,anchor=float(self.p.at[day,'close']),forecast_dates=dates,issued_at=stamp,information_cutoff=dates[0]+'T08:30:00+08:00',timing_qualified=True)
  module=L if legacy else B;cfg={**module.release(),'activated_at':'2026-10-01T00:00:00+08:00'}
  with patch.object(module,'release',return_value=cfg),patch.object(module,'calendar_snapshot',return_value=cal):daily=module.freeze(shared)
  self.assertEqual(daily['status'],'reference',daily)
  return dict(id=('old-' if legacy else 'new-')+day,version=C.VERSION,market_as_of=day,prepared_at=stamp,forecast_dates=dates,anchor=shared['anchor'],shared_distribution=shared,daily_forecasts=daily)
 def test_small_moves_and_exact_tie_reclassified_without_dropping_samples(self):
  classes=B._classes(np.array([99.9,100,100.1,99.51,100.49]),100)
  self.assertEqual([c['key'] for c in classes],['up','down']);self.assertEqual([c['count'] for c in classes],[3,2]);self.assertEqual([c['probability_pct'] for c in classes],[60,40]);self.assertEqual(B.category(100,100),'up');self.assertEqual(B.category(99.999,100),'down');self.assertEqual(B.category(100.001,100),'up')
 def test_display_probabilities_sum_to100_and_scores_follow_binary_outcome(self):
  classes=B._classes([99,100,101],100);self.assertAlmostEqual(sum(c['probability_pct'] for c in classes),100)
  self.assertAlmostEqual(B.rps(classes,'down'),(1/3-1)**2);self.assertAlmostEqual(B.rps(classes,'up'),(1/3)**2)
  with self.assertRaises(ValueError):B.rps(classes,'flat')
 def test_all_horizons_use_same_full_empirical_support_and_intervals_as_legacy(self):
  day='2026-10-28';anchor=float(self.p.at['2026-10-27','close'])
  for h in [1,2,3]:
   value=B.calculate(self.p,day,h,anchor);old=L.calculate(self.p,day,h,anchor);f=self.p.loc[:day];idx=np.arange(252,len(f)-h);close=f.close.to_numpy();ends=close[-1]*close[idx+h]/close[idx]
   # Use precisely the kernel's operation ordering at the inclusive zero boundary.
   ends=close[-1]*(close[idx+h]/close[idx])
   self.assertEqual(value['classes'][0]['count'],int((ends>=anchor).sum()));self.assertEqual(sum(c['count'] for c in value['classes']),old['sample_n']);self.assertEqual(value['intervals'],old['intervals']);self.assertEqual(value['direction_boundary'],anchor);self.assertNotIn('flat_bounds',value)
 def test_future_mutation_and_prefix_leave_old_forecast_identical(self):
  day='2026-10-28';anchor=float(self.p.at['2026-10-27','close']);a=B.calculate(self.p,day,2,anchor);future=self.p.copy();future.loc[future.index>day]*=10
  self.assertEqual(a,B.calculate(future,day,2,anchor));self.assertEqual(a,B.calculate(self.p.loc[:day],day,2,anchor));self.assertLessEqual(a['last_outcome'],day)
 def test_initial_freeze_not_renormalized_legacy_and_missing_sources_stay_blank(self):
  root=self.root('2026-10-27');targets=root['daily_forecasts']['targets'];self.assertEqual([t['horizon'] for t in targets],[1,2,3]);self.assertTrue(all(len(t['classes'])==2 for t in targets));self.assertEqual(targets[2]['intervals'][1]['low_level'],root['shared_distribution']['intervals'][1]['low_level'])
  with patch.object(B,'calculate',side_effect=AssertionError('calculation on missing data')):x=B.freeze({'status':'unavailable','note':'foreign不足'})
  self.assertEqual(x['targets'],[]);self.assertEqual(x['status'],'unavailable')
 def test_publication_retired_methods_keep_original_files_and_never_add_revisions(self):
  with tempfile.TemporaryDirectory() as tmp:
   store=Path(tmp);old=self.root('2026-10-27',legacy=True);path=store/(old['id']+'.json');path.write_text(json.dumps(old));before=path.read_bytes()
   new=C.publish(self.root('2026-10-28'),store)
   self.assertEqual(new['daily_updates'],[]);self.assertEqual(path.read_bytes(),before)
   self.assertEqual(B.report(new,[old,new])['summary']['completed_targets'],0)
 def test_inactive_release_cannot_backfill_binary_forecast(self):
  root=self.root('2026-10-27');s=root['shared_distribution']
  with patch.object(B,'release',return_value={**self.cfg,'activated_at':'2099-01-01T00:00:00+08:00'}):self.assertEqual(B.freeze(s)['status'],'unavailable')

if __name__=='__main__':unittest.main()
