import copy,gzip,hashlib,json,tempfile,unittest
from pathlib import Path
from backend import margin_dashboard as d,margin_statistics as m,margin_checker as checker

class DashboardTests(unittest.TestCase):
 def make(self,root):
  m.initialize(root)
  with m.connect(root,True) as c:
   for i in range(1,24):
    day=f'2026-01-{i:02d}';c.execute('INSERT INTO calendar VALUES(?,?)',(day,'cal'))
    for market in ['TWSE','TPEX']:
     if market=='TWSE' and i==22:continue
     sid=market+day;c.execute('INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?)',(sid,market,'credit',day,'digest','https://example.test',day+'T18:00:00',None,0,'observed','raw'))
     c.execute('INSERT INTO latest VALUES(?,?,?,?)',(market,'credit',day,sid))
     c.execute('INSERT INTO totals VALUES(?,?,?,?,?,?,?,?,?)',(sid,0,0,0,0,i*1000,0,0,0))
 def test_missing_store_never_creates_or_downloads(self):
  with tempfile.TemporaryDirectory() as t:
   r=d.report(store=t);self.assertEqual(r['status'],'missing');self.assertFalse(r['model_ready']);self.assertEqual(list(Path(t).iterdir()),[])
 def test_exact_calendar_baseline_not_previous_available_observation(self):
  with tempfile.TemporaryDirectory() as t:
   self.make(t);r=d.report(store=t);listed=r['markets'][0];otc=r['markets'][1]
   self.assertEqual(listed['official_loan'],23000);self.assertIsNone(listed['change1']['amount']);self.assertEqual(otc['change1']['amount'],1000);self.assertEqual(listed['change20']['amount'],20000)
 def test_missing_today_is_not_forward_filled_or_zero(self):
  with tempfile.TemporaryDirectory() as t:
   self.make(t);r=d.report('2026-01-22',store=t)['markets'][0]
   self.assertIsNone(r['official_loan']);self.assertIsNone(r['change1']['amount']);self.assertEqual(r['last_observation']['date'],'2026-01-21');self.assertEqual(r['missing_days'],1)
 def test_old_cutoff_excludes_all_future_amounts(self):
  with tempfile.TemporaryDirectory() as t:
   self.make(t);r=d.report('2026-01-05',store=t)
   for market in r['markets']:
    self.assertEqual(len(market['series']),5);self.assertTrue(all(p['date']<='2026-01-05' for p in market['series']));self.assertIsNone(market['change20']['amount']);self.assertEqual(market['last_observation']['date'],'2026-01-05')
 def test_future_source_changes_do_not_change_old_reading(self):
  with tempfile.TemporaryDirectory() as t:
   self.make(t);before=d.report('2026-01-05',store=t)['markets']
   with m.connect(t,True) as c:c.execute("UPDATE totals SET loan_amount=999999 WHERE snapshot_id LIKE '%2026-01-23'")
   self.assertEqual(before,d.report('2026-01-05',store=t)['markets'])
 def test_invalid_dates_rejected(self):
  with tempfile.TemporaryDirectory() as t:
   self.make(t)
   for day in ['bad','2026-02-30','2025-12-31','2026-01-24','2026-01-05 OR 1=1']:
    with self.assertRaises(ValueError):d.report(day,store=t)
 def test_source_db_stays_byte_identical(self):
  with tempfile.TemporaryDirectory() as t:
   self.make(t);p=Path(t)/'sources.sqlite3';before=p.read_bytes();d.report(store=t);d.report('2026-01-02',store=t);self.assertEqual(p.read_bytes(),before)
 def test_no_unaccepted_risk_fields_are_published(self):
  with tempfile.TemporaryDirectory() as t:
   self.make(t);r=d.report(store=t);self.assertFalse(r['model_ready']);self.assertTrue(all(x is None for x in r['risk_metrics'].values()))
 def test_latest_validation_not_claimed_as_historical_prediction(self):
  with tempfile.TemporaryDirectory() as t:
   self.make(t);(Path(t)/'principal-validation-repair.json').write_text(json.dumps({'version':'margin-principal-repair-3','status':'not_accepted','model_ready':False,'as_of':'2026-01-23'}))
   r=d.report('2026-01-05',store=t);self.assertEqual(r['validation']['as_of'],'2026-01-23');self.assertEqual(r['validation']['scope'],'latest_method_validation_not_historical_decision')
 def test_negative_official_amount_is_not_accepted(self):
  with tempfile.TemporaryDirectory() as t:
   self.make(t)
   with m.connect(t,True) as c:c.execute('UPDATE totals SET loan_amount=-1')
   with self.assertRaises(ValueError):d.report(store=t)
 def test_zero_amount_is_distinct_from_missing_and_zero_base_has_no_pct(self):
  self.assertEqual(d.change(0,100),{'amount':-100,'pct':-1.});self.assertEqual(d.change(100,0),{'amount':100,'pct':None});self.assertEqual(d.change(None,100),{'amount':None,'pct':None})

class CheckerCutoffTests(unittest.TestCase):
 def data(self):
  points=[{'date':f'2026-01-0{i}','close':i*10,'balance':i,'reset_reasons':['future_reset'] if i==3 else []} for i in range(1,4)]
  return {'calendar':[p['date'] for p in points],'as_of':points[-1]['date'],'stocks':[{'symbol':'2330','points':points,'days':3,'expected_days':3,'reset_days':1,'latest':points[-1]}]}
 def test_cutoff_removes_future_prices_events_and_mini_inputs(self):
  raw=self.data();before=copy.deepcopy(raw);v=checker.clip(raw,'2026-01-02');self.assertEqual(raw,before);self.assertEqual(v['calendar'],['2026-01-01','2026-01-02']);s=v['stocks'][0];self.assertEqual(s['latest']['close'],20);self.assertEqual(s['reset_days'],0);self.assertEqual(s['expected_days'],2);self.assertEqual(v['as_of'],'2026-01-02');self.assertEqual(v['source_as_of'],'2026-01-03')
 def test_missing_selected_day_does_not_reuse_old_last_reading(self):
  raw=self.data();raw['stocks'][0]['points'][1]=None;v=checker.clip(raw,'2026-01-02');self.assertIsNone(v['stocks'][0]['latest']);self.assertEqual(v['stocks'][0]['last'],'2026-01-01')
 def test_invalid_cutoff_rejected(self):
  with self.assertRaises(ValueError):checker.clip(self.data(),'2026-01-04')

if __name__=='__main__':unittest.main()
