import copy,json,tempfile,unittest
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch
import requests
from backend import monthly_statistics as M
TZ=ZoneInfo('Asia/Taipei');NOW=datetime(2026,2,6,22,tzinfo=TZ)
def market(label='台股',timezone='Asia/Taipei',closes=None):
 return {'metadata':{'label':label,'timezone':timezone,'currency':'TWD','sources':[]},'closes':closes or [{'month':'2025-12','date':'2025-12-31','close':100,'source':'test','complete':True},{'month':'2026-01','date':'2026-01-30','close':110,'source':'test','complete':True},{'month':'2026-02','date':'2026-02-05','close':99,'source':'test','complete':False}]}
def seed():return {'version':M.VERSION,'markets':{'taiex':market(),'sp500':market('S&P500','America/New_York')}}
def twvalue(month,close=110):
 y,m=map(int,month.split('-'));return {'stat':'OK','date':month.replace('-','')+'01','fields':['日期','發行量加權股價指數'],'data':[[f'{y-1911}/{m:02d}/05' if m==2 else f'{y-1911}/{m:02d}/30',str(close)]]}
class MonthlyTests(unittest.TestCase):
 def test_month_end_compounded_annual_and_current_month_excluded(self):
  v=M.calculate(market(),NOW);jan,feb=v['rows'][1:];self.assertAlmostEqual(jan['return_pct'],10);self.assertAlmostEqual(feb['return_pct'],-10)
  self.assertTrue(feb['partial']);self.assertAlmostEqual(v['years'][0]['return_pct'],-1);self.assertEqual(v['seasonality'][1]['n'],0);self.assertEqual(v['seasonality'][0]['n'],1)
 def test_calendar_rollover_does_not_finalize_stale_partial_month(self):
  v=M.calculate(market(),datetime(2026,3,6,22,tzinfo=TZ));self.assertTrue(v['rows'][-1]['partial']);self.assertFalse(v['rows'][-1]['in_progress']);self.assertEqual(v['seasonality'][1]['n'],0)
 def test_first_month_not_invented_and_missing_month_not_bridged(self):
  v=M.calculate(market(),NOW);self.assertIsNone(v['rows'][0]['return_pct']);x=market();x['closes'].pop(1);v=M.calculate(x,NOW)
  self.assertIsNone(v['rows'][-1]['return_pct']);self.assertIsNone(v['years'][0]['return_pct']);self.assertEqual(v['missing_months'],['2026-01'])
 def test_month_end_not_month_average_or_first_close(self):
  rows=[{'date':'2026-01-02','close':50,'source':'a'},{'date':'2026-01-30','close':100,'source':'a'},{'date':'2026-02-05','close':110,'source':'a'}]
  out=M.monthly_from_daily(rows);self.assertEqual(out[0]['close'],100);self.assertEqual(out[0]['date'],'2026-01-30')
  with self.assertRaisesRegex(ValueError,'衝突'):M.monthly_from_daily(rows+[{'date':'2026-01-30','close':99,'source':'a'}])
 def test_taiwan_owns_comparison_dates_and_missing_annual(self):
  s=seed();s['markets']['sp500']['closes'].insert(0,{'month':'2025-11','date':'2025-11-28','close':90,'source':'test'})
  r=M.build(s,NOW);self.assertEqual([v['month'] for v in r['markets']['sp500']['rows']],['2026-01','2026-02']);self.assertEqual(len(r['markets']['sp500']['years']),2);self.assertIsNone(r['markets']['sp500']['years'][-1]['return_pct'])
 def test_flat_and_win_rate_denominator_include_zero(self):
  rows=[{'month':'2020-01','return_pct':10,'partial':False},{'month':'2021-01','return_pct':0,'partial':False},{'month':'2022-01','return_pct':-5,'partial':False},{'month':'2023-01','return_pct':99,'partial':True}]
  m=M.seasonality(rows)[0];self.assertEqual((m['up'],m['down'],m['flat'],m['n']),(1,1,1,3));self.assertAlmostEqual(m['up_rate'],100/3);self.assertEqual(m['median'],0)
 def test_no_future_or_unclosed_tw_prices_and_month_identity(self):
  d=twvalue('2026-02',120);d['data'] += [['115/02/07','999']]
  r=M.parse_twse(d,'2026-02',NOW);self.assertEqual([x['date'] for x in r],['2026-02-05'])
  d['data']=[['115/02/06','120']];self.assertEqual(M.parse_twse(d,'2026-02',NOW)[0]['close'],120)
  with self.assertRaises(ValueError):M.parse_twse(d,'2026-02',datetime(2026,2,6,13,30,tzinfo=TZ))
  with self.assertRaisesRegex(ValueError,'月份'):M.parse_twse(d,'2026-01',NOW)
 def test_yahoo_must_be_daily_unadjusted_completed_local_session(self):
  ny=ZoneInfo('America/New_York');times=[int(datetime(2026,2,d,9,30,tzinfo=ny).timestamp()) for d in [4,5,6]];end=int(datetime(2026,2,6,16,tzinfo=ny).timestamp())
  x={'meta':{'symbol':'^GSPC','dataGranularity':'1d','exchangeTimezoneName':'America/New_York','currentTradingPeriod':{'regular':{'end':end}}},'timestamp':times,'indicators':{'quote':[{'close':[100,110,999]}],'adjclose':[{'adjclose':[1,2,3]}]}};d={'chart':{'result':[x]}}
  out=M.parse_yahoo_daily(d,NOW);self.assertEqual([r['close'] for r in out],[100,110]);self.assertEqual(out[-1]['date'],'2026-02-05')
  x['meta']['dataGranularity']='3mo'
  with self.assertRaisesRegex(ValueError,'日頻'):M.parse_yahoo_daily(d,NOW)
 def test_get_report_is_read_only(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);f=p/'cache.json';f.write_text(json.dumps(seed()));before=f.read_bytes()
   with patch.object(M,'_download',side_effect=AssertionError('GET fetched')):r=M.report(store=p,now=NOW)
   self.assertNotIn('error',r);self.assertEqual(f.read_bytes(),before);self.assertEqual(list(p.iterdir()),[f])
 def test_refresh_failure_preserves_closes_and_records_source_errors(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);s=seed();(p/'cache.json').write_text(json.dumps(s))
   with patch.object(M,'_download',side_effect=requests.ConnectionError('offline')):r=M.refresh_sync(store=p,now=NOW)
   self.assertEqual(r['status'],'partial');out=json.loads((p/'cache.json').read_text());self.assertEqual(out['markets'],s['markets']);self.assertEqual(set(out['source_errors']),{'taiex','sp500'})
 def test_refresh_sources_independent_and_no_date_regression(self):
  def download(url,params,raw):
   if 'yahoo' in url:raise requests.ConnectionError('offline')
   m=params['date'][:4]+'-'+params['date'][4:6];return twvalue(m,111 if m.endswith('01') else 115),{'url':url}
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);s=seed();(p/'cache.json').write_text(json.dumps(s))
   with patch.object(M,'_download',side_effect=download):r=M.refresh_sync(store=p,now=NOW)
   out=json.loads((p/'cache.json').read_text());self.assertEqual(set(r['errors']),{'sp500'});self.assertEqual(out['markets']['taiex']['closes'][-1]['close'],115);self.assertEqual(out['markets']['sp500'],s['markets']['sp500'])
 def test_invalid_current_month_reply_is_not_silently_successful(self):
  def download(url,params,raw):
   if 'yahoo' in url:raise requests.ConnectionError('offline')
   if params['date'].startswith('202602'):return {'stat':'unexpected'},{}
   return twvalue('2026-01'),{}
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);initial=seed();(p/'cache.json').write_text(json.dumps(initial))
   with patch.object(M,'_download',side_effect=download):out=M.refresh_sync(store=p,now=NOW)
   self.assertIn('taiex',out['errors']);self.assertEqual(json.loads((p/'cache.json').read_text())['markets']['taiex'],initial['markets']['taiex'])
 def test_duplicate_or_bad_monthly_values_rejected(self):
  rows=market()['closes']
  for bad in [rows+[rows[0]], [{**rows[0],'close':float('nan')}], [{**rows[0],'date':'2026-01-01'}]]:
   with self.assertRaises(ValueError):M.validate_closes(bad)
 def test_published_history_contiguous_and_aligned(self):
  r=M.report(now=datetime(2026,11,4,22,tzinfo=TZ));self.assertNotIn('error',r)
  self.assertEqual(r['markets']['taiex']['first_return_month'],'1967-02');self.assertEqual(r['markets']['taiex']['missing_months'],[])
  tw={x['month'] for x in r['markets']['taiex']['rows'] if x['return_pct'] is not None};sp={x['month'] for x in r['markets']['sp500']['rows']};self.assertTrue(sp.issubset(tw));self.assertTrue(all(x['year']>=1967 for x in r['markets']['sp500']['years']))
if __name__=='__main__':unittest.main(verbosity=2)
