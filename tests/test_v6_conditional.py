"""Synthetic operational tests; historical fixtures do not count as live records."""
import csv,hashlib,json,tempfile,unittest
from datetime import datetime,timedelta
from pathlib import Path
from backend import v6_conditional as V

R=V.db.ROOT;B=R/'research/v6_conditional_forecast_20261106'
def date(s):return datetime.fromisoformat(s).replace(tzinfo=V.TZ)
def rows(p):
 with p.open() as f:return list(csv.DictReader(f))
def weekdays(n=400):
 out=[];d=date('2026-01-02')
 while len(out)<n:
  if d.weekday()<5:out.append(d.date().isoformat())
  d+=timedelta(days=1)
 return out

class Operational(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.e=V.Engine(self.root/'ledger')
 def tearDown(self):self.temp.cleanup()
 def enroll(self,when='2026-11-02T14:00:00'):self.e.enroll(date(when))
 def bundle(self,decision='2026-11-03'):
  p1=B/'package1';mapping={'cash':'local-snapshot/taiex-official.csv','tx':'results/tx-single-contract-ohlcv.csv',
    'tsm':'results/tsm-bars.csv','sox':'results/sox-bars.csv','spx':'results/spx-bars.csv','vix':'results/vix-official.csv',
    'fx':'results/fx-cbc.csv','stock2330':'results/stock2330-research-combined.csv'}
  sources={}
  for k,path in mapping.items():
   values=[r for r in rows(p1/path) if r['date']>='2026-05-01']
   file=self.root/(k+'.csv')
   with file.open('w') as f:w=csv.DictWriter(f,fieldnames=list(values[0]));w.writeheader();w.writerows(values)
   sources[k]={'file':file.name,'raw_file':file.name,'sha256':V.digest(file.read_bytes()),'raw_sha256':V.digest(file.read_bytes()),
      'available_at':decision+'T08:00:00+08:00','source_url':'https://example.test/synthetic-fixture'}
  cal=self.root/'calendar.json';cal.write_text(json.dumps({'sessions':[r['date'] for r in rows(p1/mapping['cash'])]}))
  sources['calendar']={'file':cal.name,'raw_file':cal.name,'sha256':V.digest(cal.read_bytes()),'raw_sha256':V.digest(cal.read_bytes()),
      'available_at':decision+'T08:00:00+08:00','source_url':'https://example.test/synthetic-calendar'}
  file=self.root/'bundle.json';file.write_text(json.dumps({'decision_date':decision,'sources':sources}));return file
 def seed(self,n=60):
  days=weekdays();self.e.enroll(date('2026-01-01T14:00:00'))
  with self.e.transaction():
   self.e.append('capture',{'calendar':days},date(days[0]+'T08:34:00'))
   for i,d in enumerate(days[:n]):self.synthetic(d,i%2,True)
   self.e.append('source_status',{'withheld':False,'note':'synthetic only'},date(days[n-1]+'T14:00:00'))
  return days
 def synthetic(self,d,y,good):
  models={str(h):{m:{'up':(.5 if m=='M0' else (.9 if y==good else .1)), 'down':(.5 if m=='M0' else (.1 if y==good else .9))} for m in ('M0','M1','M2')} for h in (1,2,3)}
  self.e.append('forecast',{'decision_date':d,'feature':{'anchor_date':d,'anchor_close':100},'targets':[d,d,d],'models':models,'qualified':True},date(d+'T08:35:00'))
  self.e.append('outcome',{'root':d,'horizon':1,'target_date':d,'up':y},date(d+'T13:31:00'))

 def test_read_does_not_create_or_write(self):
  self.assertEqual(self.e.report()['status'],'not_started');self.assertFalse(self.e.store.exists())
  self.enroll();before={p.name:p.read_bytes() for p in self.e.store.iterdir()};self.e.report()
  self.assertEqual(before,{p.name:p.read_bytes() for p in self.e.store.iterdir()})
 def test_enrollment_cannot_reset_or_activate_empty(self):
  self.enroll()
  with self.assertRaises(ValueError):self.e.enroll()
  with self.assertRaises(ValueError):self.e.activate(1)
 def test_chain_detects_tamper(self):
  self.enroll();p=next(self.e.store.glob('event-*.json'));p.write_bytes(p.read_bytes()+b' ')
  with self.assertRaises(ValueError):self.e.report()
 def test_asset_tamper_withholds(self):
  import shutil
  config=self.root/'config';shutil.copytree(V.CONFIG,config);(config/'models.py').write_text('tampered')
  with self.assertRaises(ValueError):V.Engine(self.root/'ledger',config)
 def test_missing_source_records_failure_without_probability(self):
  self.enroll();value=self.e.update(None,date('2026-11-03T08:35:00'))
  self.assertTrue(value['withheld']);self.assertIsNone(value['lastForecast'])
 def test_late_publication_is_not_backfilled(self):
  self.enroll();value=self.e.update(self.bundle(),date('2026-11-03T14:00:00'))
  self.assertTrue(value['withheld']);self.assertIsNone(value['lastForecast'])
 def test_nine_sources_and_content_hashes_required(self):
  self.enroll();p=self.bundle();x=json.loads(p.read_text());del x['sources']['vix'];p.write_text(json.dumps(x))
  self.assertTrue(self.e.update(p,date('2026-11-03T08:35:00'))['withheld'])
  p=self.bundle();(self.root/'tx.csv').write_text('bad')
  self.assertTrue(self.e.update(p,date('2026-11-03T08:36:00'))['withheld'])
 def test_source_after_cutoff_is_rejected(self):
  self.enroll();p=self.bundle();x=json.loads(p.read_text());x['sources']['tx']['available_at']='2026-11-03T08:31:00+08:00';p.write_text(json.dumps(x))
  value=self.e.update(p,date('2026-11-03T08:35:00'));self.assertTrue(value['withheld']);self.assertIsNone(value['lastForecast'])
 def test_frozen_feature_and_probability_reproduction(self):
  self.enroll();p=self.bundle();_,data,evidence=self.e.capture(p,date('2026-11-03T08:35:00'));f,ledger,targets=self.e.feature('2026-11-03',data,evidence)
  original=next(r for r in rows(B/'package3/results/features-unscaled.csv') if r['decision_date']=='2026-11-03')
  for key in self.e.models.FEATURES:self.assertAlmostEqual(f[key],float(original[key]),places=14)
  probabilities=self.e.predictions(f)
  for row in rows(B/'package4/results/predictions-initial-blind.csv'):
   if row['decision_date']=='2026-11-03':self.assertEqual(probabilities[row['horizon']][row['model']]['up'],float(row['p_up']))
  self.assertEqual(targets,['2026-11-03','2026-11-04','2026-11-05'])
 def test_duplicate_and_preclose_outcomes(self):
  self.enroll();p=self.bundle();one=self.e.update(p,date('2026-11-03T08:35:00'));two=self.e.update(p,date('2026-11-03T08:36:00'))
  self.assertFalse(two['withheld']);self.assertEqual(sum(e['kind']=='forecast' for e in self.e.events),1)
  self.assertEqual([h['matured'] for h in two['horizons']],[0,0,0]);self.assertFalse(two['active'])
  self.assertEqual(one['lastForecast']['models'],two['lastForecast']['models'])
 def test_outcome_after_close_does_not_rewrite_forecast(self):
  self.enroll();p=self.bundle();first=self.e.update(p,date('2026-11-03T08:35:00'));snapshot=V.encode(first['lastForecast'])
  value=self.e.update(p,date('2026-11-03T14:00:00'));self.assertEqual([h['matured'] for h in value['horizons']],[1,0,0])
  self.assertEqual(snapshot,V.encode(value['lastForecast']));self.assertTrue(value['withheld'])
 def test_missing_cash_day_does_not_shift_volatility_window(self):
  self.enroll();p=self.bundle();_,data,evidence=self.e.capture(p,date('2026-11-03T08:35:00'));data['cash']=[r for r in data['cash'] if r['date']!='2026-11-02']
  with self.assertRaises(ValueError):self.e.feature('2026-11-03',data,evidence)
 def test_calendar_change_preserves_original_deadline(self):
  self.enroll();p=self.bundle();self.e.update(p,date('2026-11-03T08:35:00'))
  cal=self.root/'calendar.json';v=json.loads(cal.read_text());v['sessions'].remove('2026-11-04');v['sessions'].append('2026-11-06');cal.write_text(json.dumps(v))
  b=json.loads(p.read_text());b['sources']['calendar']['sha256']=V.digest(cal.read_bytes());b['sources']['calendar']['raw_sha256']=V.digest(cal.read_bytes());p.write_text(json.dumps(b))
  result=self.e.update(p,date('2026-11-03T14:00:00'))
  self.assertEqual(result['lastForecast']['targets'],['2026-11-03','2026-11-04','2026-11-05'])
  self.assertEqual(result['lastForecast']['currentTargets'],['2026-11-03','2026-11-05','2026-11-06'])
  self.assertEqual(sum(e['kind']=='calendar_change' for e in self.e.events),2)
 def test_no_backdated_record_before_enrollment(self):
  self.enroll('2026-11-03T14:00:00')
  with self.assertRaises(ValueError):self.e.update(None,date('2026-11-03T08:35:00'))
  self.assertIsNone(self.e.report()['lastForecast'])
 def test_fifty_nine_matured_not_enough(self):
  self.seed(59);self.assertFalse(self.e.eligibility(1)['eligible'])
 def test_sixty_without_each_class_twenty_not_enough(self):
  days=self.seed(60)
  # Direct synthetic metric summary test: strong scores do not excuse class gate.
  original=self.e.pairs;self.e.pairs=lambda h,after=None:[{**r,'y':1} for r in original(h,after)]
  self.assertFalse(self.e.eligibility(1)['gates']['eachClass20'])
 def test_sixty_good_m1_only_manual_activation(self):
  days=self.seed();self.assertTrue(self.e.eligibility(1)['eligible']);self.assertFalse(self.e.report()['active'])
  result=self.e.activate(1,date(days[59]+'T14:01:00'));self.assertTrue(result['horizons'][0]['active']);self.assertFalse(result['horizons'][1]['active'])
 def test_rollback_and_sixty_new_restore(self):
  days=self.seed();self.e.activate(1,date(days[59]+'T14:01:00'))
  with self.e.transaction():
   for i,d in enumerate(days[60:120]):
    self.synthetic(d,i%2,False);self.e.monitor(date(d+'T13:32:00'))
    if not self.e.active(1):break
  self.assertFalse(self.e.active(1));self.assertFalse(self.e.eligibility(1)['eligible'])
  start=days.index(d)+1
  with self.e.transaction():
   for i,day in enumerate(days[start:start+59]):self.synthetic(day,i%2,True)
  self.assertFalse(self.e.eligibility(1)['eligible'])
  with self.e.transaction():self.synthetic(days[start+59],1,True)
  self.assertTrue(self.e.eligibility(1)['eligible']);self.assertFalse(self.e.active(1))
 def test_no_http_or_clock_override_cli(self):
  source=Path(V.__file__).read_text();self.assertNotIn("add_argument('--now'",source);self.assertNotIn("add_argument('--date'",source)
  server=(R/'backend/server.py').read_text();self.assertNotIn('v6_conditional',server)

if __name__=='__main__':unittest.main(verbosity=2)
