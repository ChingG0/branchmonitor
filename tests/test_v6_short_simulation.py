import json,tempfile,unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
import pandas as pd
from backend import v6_short_simulation as S,v6_engine as E,v6_weather as W,v6_comparison as C

TZ=ZoneInfo('Asia/Taipei')
def fixture():
 days=pd.bdate_range('2025-01-02',periods=10).strftime('%Y-%m-%d')
 frame=pd.DataFrame({'open':10000.,'high':10020.,'low':9980.,'close':10000.,'atr':100.,'dd':.02,
  'cross_raw':.01,'cross_valid':True,'cross_score':20.,'legacy_raw':.99,'legacy_valid':True,'legacy_score':99.},index=days)
 quotes={(d,c):{'open':10000.,'high':10020.,'low':9980.,'close':10000.} for d in days for c in ['202501','202502']}
 return frame,quotes

class ShortSimulationTests(unittest.TestCase):
 def freeze(self,f,q):return S.freeze(f,q,now=datetime(2025,1,18,10,tzinfo=TZ))
 def test_primary_uses_cross_signal_not_original_v6(self):
  f,q=fixture();r=self.freeze(f,q);self.assertEqual(r['completed'],0)
  f.loc['2025-01-03',['cross_raw','cross_score']]=[.8,85.]
  r=self.freeze(f,q);self.assertEqual(r['completed'],1)
  self.assertEqual(r['trades'][0]['signal_date'],'2025-01-03');self.assertEqual(r['trades'][0]['entry_date'],'2025-01-06')
  self.assertEqual(r['trades'][0]['signal_score'],85.);self.assertEqual(r['cooldown_days'],2)
  self.assertEqual(r['strategy_key'],'v6-2');self.assertIsNone(r['entry_allowed']);self.assertIsNone(r['signal'])
  self.assertEqual(r['simulation_kind'],'historical_replay')
 def test_eight_percent_boundary_and_missing_sources_block_entry(self):
  f,q=fixture();f.loc['2025-01-03',['cross_raw','cross_score','dd']]=[.8,85.,.08]
  self.assertEqual(self.freeze(f,q)['completed'],0)
  f.loc['2025-01-03','dd']=.0799;self.assertEqual(self.freeze(f,q)['completed'],1)
  f.loc['2025-01-03','cross_valid']=False;self.assertEqual(self.freeze(f,q)['completed'],0)
 def test_replay_retains_only_historical_data_and_no_future_order(self):
  f,q=fixture()
  with self.assertRaises(ValueError):S.freeze(f,q,now=datetime(2025,1,1,10,tzinfo=TZ))
  with self.assertRaises(ValueError):S.freeze(f,q,now=datetime(2025,1,15,12,tzinfo=TZ))
 def test_archive_is_immutable_and_get_does_not_recompute(self):
  f,q=fixture();model=self.freeze(f,q)
  with tempfile.TemporaryDirectory() as tmp:
   store=Path(tmp);a=S.publish(model,store=store);path=store/(a['id']+'.json');before=path.read_bytes()
   b=S.publish({**model,'generated_at':'2025-01-18T11:00:00+08:00'},store=store)
   with patch.object(E,'sim',side_effect=AssertionError('GET recomputed replay')),patch.object(C,'build_frame',side_effect=AssertionError('GET rebuilt sources')):
    self.assertEqual(S.report(store=store)['id'],b['id'])
    self.assertEqual(S.report(store=store,before='2025-01-18T10:30:00+08:00')['id'],a['id'])
    self.assertIsNone(S.report(store=store,before='2025-01-18T09:00:00+08:00'))
   self.assertEqual(path.read_bytes(),before)
 def test_old_weather_selection_cannot_borrow_new_primary_replay(self):
  f,q=fixture();model=self.freeze(f,q)
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);store=root/'v6_weather';store.mkdir()
   old={'id':'old','prepared_at':'2025-01-18T09:00:00+08:00','market_as_of':'2025-01-15','stage':'形成','model':{'score':33}}
   (store/'old.json').write_text(json.dumps(old))
   new=S.publish(model,store=root/'v6_short_simulation')
   with patch.object(C.v6_forecast,'index_frame',side_effect=OSError('not needed')):
    self.assertEqual(W.report(store=store)['short_simulation']['id'],new['id'])
    self.assertIsNone(W.report('old',store=store)['short_simulation'])
    self.assertIsNone(W.report('missing',store=store)['short_simulation'])
   self.assertEqual(json.loads((store/'old.json').read_text()),old)
 def test_comparison_owned_replay_keeps_its_publication_identity(self):
  f,q=fixture();model=self.freeze(f,q)
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);weather=root/'v6_weather';weather.mkdir();comparison=root/'v6_comparison';comparison.mkdir()
   owned={'id':'owned','version':C.VERSION,'prepared_at':'2025-01-18T10:00:00+08:00','market_as_of':'2025-01-15','short_simulation':model,'forecast_dates':['2025-01-16','2025-01-17','2025-01-20'],'anchor':10000}
   old={'id':'old','prepared_at':'2025-01-18T09:59:59+08:00','market_as_of':'2025-01-15','stage':'形成','comparison_id':'owned'}
   (weather/'old.json').write_text(json.dumps(old));(comparison/'owned.json').write_text(json.dumps(owned))
   with patch.object(C.v6_forecast,'index_frame',side_effect=OSError('not needed')):
    r=W.report('old',store=weather)
   self.assertEqual(r['short_simulation']['strategy_key'],'v6-2')
if __name__=='__main__':unittest.main()
