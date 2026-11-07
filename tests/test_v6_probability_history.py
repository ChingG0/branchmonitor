"""Isolated projection tests; never issue a production forecast."""
import json,shutil,tempfile,unittest
from pathlib import Path
from datetime import datetime
from backend import v6_conditional as C,v6_probability_history as H

def at(s):return datetime.fromisoformat(s).replace(tzinfo=C.TZ)
class History(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=self.root/'ledger';self.weather=self.root/'weather';self.e=C.Engine(self.store)
 def tearDown(self):self.tmp.cleanup()
 def read(self):return H.build(store=self.store,weather_store=self.weather,now=at('2026-11-06T16:00:00'))
 def append(self,kind,p,when):
  with self.e.transaction():self.e.append(kind,p,at(when))
 def enroll(self):self.e.enroll(at('2026-11-02T14:00:00'))
 def forecast(self,day,p,targets):
  self.append('forecast',{'decision_date':day,'information_cutoff':day+'T08:30:00+08:00','feature':{'anchor_date':'2026-11-02','anchor_close':100},'targets':targets,'models':{str(h):{'M1':{'up':p,'down':1-p}} for h in (1,2,3)},'qualified':True,'role':'prospective_parallel'},day+'T08:35:00')
 def seed(self):
  self.enroll();self.append('capture',{'calendar':['2026-11-03','2026-11-04','2026-11-05','2026-11-06','2026-11-09']},'2026-11-03T08:34:00');self.forecast('2026-11-03',.6,['2026-11-03','2026-11-04','2026-11-05']);self.append('source_status',{'withheld':False,'note':'合成資料僅供測試'},'2026-11-03T08:36:00')
 def bytes(self):return {str(p.relative_to(self.store)):p.read_bytes() for p in self.store.rglob('*') if p.is_file()}
 def test_empty_read_never_creates_ledger(self):
  self.assertEqual(self.read()['status'],'not_started');self.assertFalse(self.store.exists())
 def test_enroll_only_does_not_invent_probabilities(self):
  self.enroll();before=self.bytes();x=self.read();self.assertEqual(x['liveMaturedByHorizon'],[0,0,0]);self.assertEqual([s['rows'] for s in x['series']],[[],[],[]]);self.assertEqual(before,self.bytes())
 def test_saved_raw_probabilities_preserved_and_display_complementary(self):
  self.seed();x=self.read();self.assertEqual(x['status'],'ready')
  for s in x['series']:
   r=s['rows'][0];self.assertEqual(r['up'],.6);self.assertEqual(r['down'],.4);self.assertEqual(r['upPct']+r['downPct'],100);self.assertEqual(r['savedAt'],'2026-11-03T08:35:00+08:00');self.assertIsNone(r['deltaUpPp'])
 def test_missing_session_is_gap_and_delta_uses_previous_valid(self):
  self.seed();self.append('source_status',{'withheld':True,'note':'盤後不倒填'},'2026-11-04T14:00:00');self.forecast('2026-11-05',.8,['2026-11-05','2026-11-06','2026-11-09']);x=self.read()['series'][0]['rows'];self.assertEqual([r['status'] for r in x],['saved','missing','saved']);self.assertIsNone(x[1]['up']);self.assertAlmostEqual(x[2]['deltaUpPp'],20);self.assertEqual(x[2]['previousValidDate'],'2026-11-03');self.assertIn('不是相同到期日',x[2]['note'])
 def test_later_failed_attempt_does_not_rewrite_morning_probability(self):
  self.seed();self.append('source_status',{'withheld':True,'note':'超過盤前時段'},'2026-11-03T14:00:00');x=self.read();self.assertTrue(x['withheld']);self.assertEqual(x['series'][0]['rows'][0]['up'],.6);self.assertTrue(x['series'][0]['rows'][0]['marketAttempt']['withheld'])
 def test_outcome_kept_separate_and_never_turns_forecast_into_certainty(self):
  self.seed();self.append('outcome',{'root':'2026-11-03','horizon':1,'target_date':'2026-11-03','close':98,'up':0,'available_at':'2026-11-03T13:30:00+08:00'},'2026-11-03T14:00:00');x=self.read();r=x['series'][0]['rows'][0];self.assertEqual(r['up'],.6);self.assertFalse(r['outcome']['up']);self.assertEqual(x['liveMaturedByHorizon'],[1,0,0])
 def test_calendar_change_preserves_original_and_current_target(self):
  self.seed();self.append('calendar_change',{'root':'2026-11-03','horizon':2,'to':'2026-11-05'},'2026-11-03T14:00:00');r=self.read()['series'][1]['rows'][0];self.assertEqual(r['targetDate'],'2026-11-05');self.assertEqual(r['originalTargetDate'],'2026-11-04')
 def test_duplicate_forecast_fails_closed(self):
  self.seed();p=dict(self.e.events[-2]['payload']);self.append('forecast',p,'2026-11-03T08:37:00');x=self.read();self.assertEqual(x['status'],'unavailable');self.assertEqual(x['series'],[])
 def test_wrong_timestamp_or_role_cannot_enter_actual_history(self):
  self.enroll();p={'decision_date':'2026-11-03','information_cutoff':'2026-11-03T08:30:00+08:00','qualified':True,'role':'prospective_parallel'};self.append('forecast',p,'2026-11-03T14:00:00');self.assertEqual(self.read()['status'],'unavailable')
 def test_chain_tamper_fails_closed(self):
  self.seed();p=next(self.store.glob('event-*.json'));p.write_bytes(p.read_bytes()+b' ');self.assertEqual(self.read()['status'],'unavailable')
 def test_market_time_is_separate_from_probability_time(self):
  self.seed();self.weather.mkdir();row={'id':'research-only','prepared_at':'2026-11-06T15:30:00+08:00','market_as_of':'2026-11-06','live_assessment':{'captured_at':'2026-11-06T15:29:00+08:00','status':'ready'}};(self.weather/'research-only.json').write_text(json.dumps(row));x=self.read();self.assertEqual(x['lastMarketUpdate']['savedAt'],row['prepared_at']);self.assertEqual(x['lastProbabilitySavedAt'],'2026-11-03T08:35:00+08:00')
 def test_market_research_before_enrollment_is_not_misreported_as_absent(self):
  self.seed();self.weather.mkdir();row={'id':'pre-observation','prepared_at':'2026-11-02T10:00:00+08:00','market_as_of':'2026-10-30'};(self.weather/'pre-observation.json').write_text(json.dumps(row));x=self.read();self.assertEqual(x['lastMarketUpdate']['savedAt'],row['prepared_at']);self.assertTrue(x['lastMarketUpdate']['beforeObservationStarted']);self.assertEqual(x['lastProbabilitySavedAt'],'2026-11-03T08:35:00+08:00')
 def test_export_only_writes_derivative_and_report_is_read_only(self):
  self.seed();before=self.bytes();H.export(store=self.store,weather_store=self.weather,roots=[self.root/'public']);self.assertTrue((self.root/'public/v6-research/daily-probability-history.json').exists());self.assertEqual(before,self.bytes());self.read();self.assertEqual(before,self.bytes())
 def test_foreign_result_or_future_availability_fails_closed(self):
  self.seed();self.append('outcome',{'root':'2026-11-03','horizon':1,'target_date':'2026-11-03','close':99,'up':0,'available_at':'2026-11-03T15:00:00+08:00'},'2026-11-03T14:00:00');self.assertEqual(self.read()['status'],'unavailable')
 def market(self,day):
  self.weather.mkdir(exist_ok=True);row={'id':'latest','prepared_at':'2026-11-06T15:30:00+08:00','market_as_of':day};(self.weather/'latest.json').write_text(json.dumps(row))
 def test_future_deadlines_skip_weekend_and_do_not_reuse_stale_odds(self):
  self.seed();self.append('capture',{'calendar':['2026-11-02','2026-11-03','2026-11-04','2026-11-05','2026-11-06','2026-11-09','2026-11-10','2026-11-11']},'2026-11-06T15:00:00');self.market('2026-11-06');before=self.bytes();x=self.read();u=x['upcoming']
  self.assertEqual(u['anchorDate'],'2026-11-06');self.assertEqual([t['targetDate'] for t in u['targets']],['2026-11-09','2026-11-10','2026-11-11']);self.assertTrue(all(t['status']=='pending' and t['upPct'] is None for t in u['targets']));self.assertEqual(before,self.bytes());self.assertEqual([s['savedCount'] for s in x['series']],[1,1,1])
 def test_future_deadlines_follow_captured_holiday_calendar_not_weekdays(self):
  self.seed();self.append('capture',{'calendar':['2026-11-06','2026-11-09','2026-11-11','2026-11-12']},'2026-11-06T15:00:00');self.market('2026-11-06');self.assertEqual([t['targetDate'] for t in self.read()['upcoming']['targets']],['2026-11-09','2026-11-11','2026-11-12'])
 def test_insufficient_calendar_is_explicit_and_no_market_anchor_is_not_guessed(self):
  self.enroll();self.assertIsNone(self.read()['upcoming']);self.append('capture',{'calendar':['2026-11-06','2026-11-09']},'2026-11-06T15:00:00');self.market('2026-11-06');t=self.read()['upcoming']['targets'];self.assertEqual([v['status'] for v in t],['pending','calendar_missing','calendar_missing']);self.assertIsNone(t[1]['targetDate'])
 def test_only_exact_saved_anchor_and_target_odds_can_appear_upcoming(self):
  self.seed();self.append('capture',{'calendar':['2026-11-02','2026-11-03','2026-11-04','2026-11-05']},'2026-11-06T15:00:00');self.market('2026-11-02');x=self.read();self.assertTrue(all(t['status']=='saved' and t['upPct']==60 for t in x['upcoming']['targets']));self.assertEqual(x['upcoming']['targets'][0]['probabilitySavedAt'],'2026-11-03T08:35:00+08:00')
 def test_frozen_runtime_and_http_are_not_modified_for_chart(self):
  C.Engine(self.store);server=(C.db.ROOT/'backend/server.py').read_text();self.assertNotIn('v6_probability_history',server)
  code=Path(H.__file__).read_text();self.assertNotIn("add_argument('--now'",code);self.assertNotIn('engine.update(',code);self.assertNotIn('engine.report(',code)

if __name__=='__main__':unittest.main(verbosity=2)
