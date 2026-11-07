import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np,pandas as pd
from backend import v6_daily_forecast as F,v6_shared_distribution as D,v6_comparison as C,v6_remaining as R
ROOT=Path(__file__).resolve().parents[1]

def calendar(closed=None,saved='2026-09-30T18:00:00+08:00'):
 years={'2026':{'closed':closed if closed is not None else ['2026-10-09','2026-10-23','2026-10-26'],'opened':[],'saved_at':saved,'source':'https://www.twse.com.tw/zh/trading/holiday.html'}}
 return {'years':years,'sha256':__import__('hashlib').sha256(json.dumps(years,sort_keys=True).encode()).hexdigest(),'observed_at':'2026-11-04T20:00:00+08:00','information_cutoff':'2026-11-04T20:00:00+08:00'}

class DailyForecastTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.p=D.prices();cls.cfg={**F.release(),'activated_at':'2026-10-01T00:00:00+08:00'}
  cls.replay=json.loads((ROOT/'research/v6_daily_horizons_20261104/predictions-sealed.json').read_text())
 def root(self,day,cal=None,qualified=True):
  cal=cal or calendar();dates=F.sessions(day,3,cal);issued=dates[0]+'T08:35:00+08:00';shared=D.calculate(self.p,day,float(self.p.at[day,'close']))
  shared.update(version=D.VERSION,method_version=D.METHOD,market_as_of=day,anchor=float(self.p.at[day,'close']),forecast_dates=dates,issued_at=issued,information_cutoff=dates[0]+'T08:30:00+08:00',timing_qualified=qualified,official_price_sha256='test-official',source_manifest_sha256='test-source')
  with patch.object(F,'release',return_value=self.cfg),patch.object(F,'calendar_snapshot',return_value=cal):d=F.freeze(shared)
  self.assertEqual(d['status'],'reference',d)
  return {'id':'root-'+day,'version':C.VERSION,'market_as_of':day,'prepared_at':issued,'forecast_dates':dates,'anchor':shared['anchor'],'shared_distribution':shared,'daily_forecasts':d}
 def test_all_month_predictions_and_revisions_reproduce_sealed_research(self):
  for row in self.replay:
   if row['version']!='expanding':continue
   value=F.calculate(self.p,row['as_of'],row['remaining'],row['anchor'])
   for c in value['classes']:self.assertAlmostEqual(c['probability'],row['probabilities'][c['key']],places=13)
   for band in value['intervals']:
    np.testing.assert_allclose([band['low_level'],band['high_level']],row['intervals'][str(band['central_pct'])],atol=2e-8,rtol=0)
   self.assertLessEqual(value['last_outcome'],row['as_of'])
 def test_future_mutation_prefix_and_fixed_original_anchor(self):
  anchor=float(self.p.at['2026-10-27','close']);day='2026-10-28';a=F.calculate(self.p,day,2,anchor)
  future=self.p.copy();future.loc[future.index>day]*=8
  self.assertEqual(a,F.calculate(future,day,2,anchor));self.assertEqual(a,F.calculate(self.p.loc[:day],day,2,anchor))
  self.assertEqual(a['flat_bounds'],[anchor*.995,anchor*1.005]);self.assertEqual(a['remaining_sessions'],2)
  for h in [0,4,True]:
   with self.assertRaises(ValueError):F.calculate(self.p,day,h,anchor)
 def test_missing_source_does_not_calculate_or_create_zero_probabilities(self):
  with patch.object(F,'calculate',side_effect=AssertionError('missing source calculated')):
   out=F.freeze({'status':'unavailable','note':'foreign不足'})
  self.assertEqual(out['status'],'unavailable');self.assertEqual(out['targets'],[]);self.assertIn('foreign',out['note'])
 def test_initial3_reproduces_shared_T1_and_saves_calendar_vintage(self):
  root=self.root('2026-10-27');d=root['daily_forecasts'];self.assertEqual([t['horizon'] for t in d['targets']],[1,2,3]);self.assertEqual([t['target_date'] for t in d['targets']],['2026-10-28','2026-10-29','2026-10-30'])
  self.assertEqual(d['targets'][2]['classes'],root['shared_distribution']['classes']);self.assertEqual(d['calendar']['years']['2026']['saved_at'],'2026-09-30T18:00:00+08:00')
 def test_calendar_cutoff_refuses_future_vintage_and_unknown_year(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp);con=sqlite3.connect(path/'research.sqlite3');con.execute('CREATE TABLE bt_calendars(year INTEGER PRIMARY KEY,payload TEXT)');con.execute('INSERT INTO bt_calendars VALUES (?,?)',(2026,json.dumps(calendar(saved='2026-11-05T08:00:00+08:00')['years']['2026'])));con.commit();con.close()
   with self.assertRaisesRegex(ValueError,'截點以前'):F.calendar_snapshot('2026-11-04T21:00:00+08:00',data_dir=path)
  with self.assertRaisesRegex(ValueError,'年份'):F.sessions('2026-12-31',1,calendar())
 def test_manual_publication_no_longer_updates_unconditional_forecasts(self):
  with tempfile.TemporaryDirectory() as tmp:
   store=Path(tmp);root=C.publish(self.root('2026-10-27'),store);path=store/(root['id']+'.json');before=path.read_bytes()
   one=C.publish(self.root('2026-10-28'),store)
   self.assertEqual(one['daily_updates'],[]);self.assertEqual(path.read_bytes(),before)
   self.assertEqual(F.report(one,[root,one])['summary']['completed_targets'],0)
 def test_missing_current_source_suppresses_revisions_but_allows_price_outcome(self):
  root=self.root('2026-10-27');current=self.root('2026-10-28');current['id']='current';current['daily_forecasts']={'status':'unavailable','note':'來源不足'}
  with tempfile.TemporaryDirectory() as tmp,patch.object(F,'release',return_value=self.cfg),patch.object(F,'calendar_snapshot',return_value=calendar()):
   store=Path(tmp);(store/(root['id']+'.json')).write_text(json.dumps(root));us=F.prepare(current,store)
  self.assertEqual(us[0]['status'],'complete');self.assertTrue(us[0]['official_price_sha256']);self.assertEqual([u['status'] for u in us[1:]],['unavailable','unavailable']);self.assertTrue(all(u['classes']==[] for u in us[1:]))
 def test_older_shared_only_snapshots_never_get_daily_backfill(self):
  root=self.root('2026-10-27');root.pop('daily_forecasts');current=self.root('2026-10-28');current['id']='current'
  with tempfile.TemporaryDirectory() as tmp:
   store=Path(tmp);(store/(root['id']+'.json')).write_text(json.dumps(root));self.assertEqual(F.prepare(current,store),[])
  self.assertEqual(F.report(root,[root])['groups'],[])
 def test_notice_based_holiday_shift_retains_original_date_and_source(self):
  oldcal=calendar(closed=['2026-10-09']);root=self.root('2026-10-22',oldcal);self.assertEqual(root['daily_forecasts']['targets'][0]['target_date'],'2026-10-23')
  current=self.root('2026-10-27');current['id']='current';newcal=calendar(saved='2026-10-27T18:00:00+08:00')
  with tempfile.TemporaryDirectory() as tmp,patch.object(F,'release',return_value=self.cfg):
   store=Path(tmp);(store/(root['id']+'.json')).write_text(json.dumps(root))
   with patch.object(F,'calendar_snapshot',return_value=newcal):us=F.prepare(current,store)
   self.assertEqual(us[0]['status'],'complete');self.assertEqual(us[0]['original_target_date'],'2026-10-23');self.assertEqual(us[0]['target_date'],'2026-10-27');self.assertEqual(us[0]['schedule_changes'][0]['calendar_saved_at'],'2026-10-27T18:00:00+08:00')
   with patch.object(F,'calendar_snapshot',return_value=oldcal):bad=F.prepare(current,store)
   self.assertTrue(all(u['status']=='unavailable' for u in bad));self.assertIn('日曆與已保存行情不一致',bad[0]['note'])
 def test_canonical_origin_prefers_first_qualified_and_does_not_inflate_scores(self):
  a=self.root('2026-10-27',qualified=False);a['id']='early';b=copy.deepcopy(a);b['id']='qualified';b['prepared_at']='2026-10-28T08:36:00+08:00';b['daily_forecasts']['timing_qualified']=True
  c=copy.deepcopy(b);c['id']='repeat';c['prepared_at']='2026-10-28T08:37:00+08:00'
  end={'id':'end','prepared_at':'2026-11-02T08:35:00+08:00','daily_updates':[]}
  for root in [a,b,c]:
   for h in [1,2,3]:end['daily_updates'].append({'root_id':root['id'],'source_snapshot_id':'end','method_version':F.METHOD,'horizon':h,'status':'complete','issued_at':end['prepared_at'],'target_date':root['daily_forecasts']['targets'][h-1]['target_date'],'original_rps':.2,'revision_scores':[]})
  result=F.report(end,[a,b,c,end]);self.assertEqual(len(result['groups']),1);self.assertEqual(result['groups'][0]['root_id'],'qualified');self.assertEqual(result['summary']['completed_targets'],3);self.assertEqual(result['summary']['prospective_origins'],1)

if __name__=='__main__':unittest.main(verbosity=2)
