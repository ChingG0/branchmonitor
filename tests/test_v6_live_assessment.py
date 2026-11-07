import copy,json,sqlite3,tempfile,unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
import numpy as np,pandas as pd
from backend import v6_live_assessment as L,v6_shared_distribution as D,v6_weather as W
TZ=ZoneInfo('Asia/Taipei');NOW=datetime(2026,11,6,0,50,tzinfo=TZ)
def night():
 return {'RtCode':'0','RtData':{'QuoteList':[dict(SymbolID='TXFK6-M',CDate='20261105',CTime='004700',CLastPrice='39972',CRefPrice='41882',CHighPrice='42298',CLowPrice='39365',CDiff='-1910')]}}
def yahoo():
 at=datetime(2026,11,6,0,48,tzinfo=TZ).timestamp();begin=datetime(2026,11,5,22,30,tzinfo=TZ).timestamp();end=datetime(2026,11,6,5,tzinfo=TZ).timestamp()
 return {'chart':{'result':[{'meta':{'symbol':'TSM','previousClose':395.75,'regularMarketTime':at,'regularMarketPrice':363.46,'exchangeTimezoneName':'America/New_York','currentTradingPeriod':{'regular':{'start':begin,'end':end}}},'timestamp':[at],'indicators':{'quote':[{'close':[363.46]}]}}]}}
class LiveAssessmentTests(unittest.TestCase):
 def test_night_midnight_same_contract_day_close_and_reference_remain_distinct(self):
  r=L.parse_night(night(),'2026-11-05',NOW,{'202611':41915},41574.72)[0]
  self.assertEqual(r['contract'],'202611');self.assertEqual(r['quote_at'],'2026-11-06T00:47:00+08:00');self.assertEqual(r['change_day_points'],-1943);self.assertEqual(r['change_reference_points'],-1910);self.assertAlmostEqual(r['day_basis_points'],340.28);self.assertAlmostEqual(r['vs_closed_cash_points'],-1602.72);self.assertTrue(r['included']);self.assertIn('非同步',r['note'])
 def test_wrong_contract_future_time_wrong_date_and_reference_mismatch_excluded(self):
  for field,value in [('SymbolID','TXFL6-M'),('CTime','015000'),('CDate','20261005'),('CDiff','-999')]:
   with self.subTest(field=field):
    d=night();d['RtData']['QuoteList'][0][field]=value;self.assertEqual(L.parse_night(d,'2026-11-05',NOW,{'202611':41915},41574.72),[])
 def test_stale_night_not_used_and_last_completed_night_allowed(self):
  d=night();d['RtData']['QuoteList'][0]['CTime']='002000';r=L.parse_night(d,'2026-11-05',NOW,{'202611':41915},41574.72)[0];self.assertEqual(r['state'],'stale');self.assertFalse(r['included'])
  d['RtData']['QuoteList'][0]['CTime']='045900';r=L.parse_night(d,'2026-11-05',datetime(2026,11,6,8,tzinfo=TZ),{'202611':41915},41574.72)[0];self.assertEqual(r['state'],'closed');self.assertTrue(r['included'])
 def test_year_wrap_decodes_january_contract(self):
  self.assertEqual(L._contract('TXFA7-M','2026-12-31'),'202701');self.assertIsNone(L._contract('TXF-P','2026-12-31'))
 def test_yahoo_uses_previous_close_not_multiday_chart_previous_close(self):
  d=yahoo();d['chart']['result'][0]['meta']['chartPreviousClose']=431.81;r=L.parse_yahoo(d,'tsm','2026-11-05',NOW)
  self.assertTrue(r['included']);self.assertEqual(r['source_date'],'2026-11-05');self.assertAlmostEqual(r['change_pct'],(363.46/395.75-1)*100)
 def test_future_overseas_quote_not_consumed(self):
  d=yahoo();q=d['chart']['result'][0];q['timestamp']=[datetime(2026,11,6,1,tzinfo=TZ).timestamp()];q['meta']['regularMarketTime']=q['timestamp'][0]
  with self.assertRaises(ValueError):L.parse_yahoo(d,'tsm','2026-11-05',NOW)
 def test_overseas_already_reflected_and_stale_do_not_become_new_signals(self):
  for at in [datetime(2026,11,5,5,tzinfo=TZ),datetime(2026,11,5,23,tzinfo=TZ)]:
   d=yahoo();q=d['chart']['result'][0];q['timestamp']=[at.timestamp()];q['meta']['regularMarketTime']=at.timestamp();r=L.parse_yahoo(d,'tsm','2026-11-05',NOW);self.assertFalse(r['included'])
 def test_bad_symbol_and_missing_previous_close_rejected(self):
  for field,value in [('symbol','TSLA'),('previousClose',None)]:
   d=yahoo();d['chart']['result'][0]['meta'][field]=value
   with self.assertRaises(ValueError):L.parse_yahoo(d,'tsm','2026-11-05',NOW)
 def test_volatility_prefix_invariance_and_amplitude_formula(self):
  p=D.prices();asof='2026-11-04';a=L.volatility(p,asof);future=p.copy();future.loc[future.index>asof]*=10
  self.assertEqual(a,L.volatility(future,asof));self.assertEqual(a,L.volatility(p.loc[:asof],asof));f=p.loc[:asof];ranges=(f.high-f.low)/f.close.shift()*100
  self.assertAlmostEqual(a['range5_pct'],float(ranges.tail(5).mean()));self.assertGreater(a['atr14_points'],0)
 def test_assessment_uses_new_negative_quotes_without_fabricating_probability(self):
  n=L.parse_night(night(),'2026-11-05',NOW,{'202611':41915},41574.72);r=L.parse_yahoo(yahoo(),'tsm','2026-11-05',NOW);s={**r,'key':'spx','label':'標普500','change_pct':-2};v=L.volatility(D.prices(),'2026-11-05');a=L.assess(n,[r,s],v)
  self.assertEqual(a['state'],'夜盤與海外同步偏弱');self.assertFalse(a['updates_probabilities']);self.assertEqual(a['probability_status'],'not_calibrated');self.assertIn('ATR',a['drivers'][1])
 def test_missing_data_not_low_risk_or_direction_probability(self):
  a=L.assess([],[],None);self.assertIn('等待',a['state']);self.assertFalse(a['updates_probabilities']);self.assertNotIn('probability',a)
 def test_custom_store_publish_and_get_never_fetch_live_data(self):
  draft={'market_as_of':'2026-11-05','stage':'高風險','summary':'測試保存與只讀流程不抓即時行情','drivers':[],'watch_next':[],'weakening':[],'news':[],'indicators':[{'category':'global','label':'VIX','value':20,'unit':'','as_of':'2026-11-04','source':'Cboe','url':'https://www.cboe.com/','reading':'只讀測試'}]}
  with tempfile.TemporaryDirectory() as tmp,patch.object(L,'collect',side_effect=AssertionError('unexpected network')):
   store=Path(tmp);saved=W.publish(draft,store=store,now=NOW,model={},local_indicators=[]);w=W.report(store=store);self.assertEqual(w['snapshot']['id'],saved['id']);self.assertIsNone(w['snapshot']['live_assessment'])
 def test_real_publish_collects_before_save_and_passes_same_context_to_comparison(self):
  draft={'market_as_of':'2026-11-05','stage':'高風險','summary':'時間戳與共用資料測試','indicators':[{'category':'global','label':'VIX','value':20,'unit':'','as_of':'2026-11-04','source':'Cboe','url':'https://www.cboe.com/','reading':'測試'}]}
  captured=datetime.now(TZ).isoformat(timespec='seconds');context={'version':L.VERSION,'captured_at':captured,'assessment':{'updates_probabilities':False}}
  with tempfile.TemporaryDirectory() as tmp:
   target=Path(tmp)/'weather';source=Path(tmp)/'sources.json';source.write_text('{}')
   with patch.object(W,'STORE',target),patch.object(L,'collect',return_value=context) as collect,patch.object(W.v6_forecast,'freeze',return_value={}),patch.object(W.v6_comparison,'SOURCES',source),patch.object(W.v6_comparison,'publish_current',return_value={'id':'same-context'}) as comparison,patch('backend.v6_evening_forecast.manual_update',return_value={'status':'test_only','qualified':False}) as observation,patch('backend.v6_conditional.manual_update',side_effect=AssertionError('archived observer updated')) as archived:
    saved=W.publish(draft,model={},local_indicators=[]);row=json.loads((target/(saved['id']+'.json')).read_text())
    collect.assert_called_once_with('2026-11-05');comparison.assert_called_once_with(live_assessment=context);observation.assert_called_once_with(live_assessment=context,weather_id=saved['id'],minute_bars=None,feature_history=None);archived.assert_not_called()
    self.assertEqual(row['live_assessment'],context);self.assertGreaterEqual(row['prepared_at'],captured);self.assertEqual(row['comparison_id'],'same-context')
 def test_access_denial_stops_once(self):
  from unittest.mock import Mock
  r=Mock();r.status_code=403
  with patch.object(L.requests,'get',return_value=r) as get:
   with self.assertRaisesRegex(ValueError,'403'):L._fetch('https://example.com')
   self.assertEqual(get.call_count,1)

if __name__=='__main__':unittest.main()
