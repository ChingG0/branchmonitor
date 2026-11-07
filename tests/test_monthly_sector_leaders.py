import copy,json,tempfile,unittest
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch
from backend import monthly_sector_leaders as S,monthly_statistics as M
TZ=ZoneInfo('Asia/Taipei');NOW=datetime(2026,3,5,18,tzinfo=TZ)
def dictionary():
 return {'error':'','snapshot':'2026-09-11','groups':[{'name':n,'parent':'自己的分類'} for n in ['AI伺服器','ABF載板','ASIC','沒有足夠行情']],
 'memberships':{'1':['AI伺服器','ASIC'],'2':['AI伺服器','ASIC'],'3':['ABF載板'],'4':['ABF載板'],'5':['沒有足夠行情'],'6':['沒有足夠行情']}}
def timeline():return [{'month':m,'date':d,'close':100,'complete':complete} for m,d,complete in [('2025-12','2025-12-31',True),('2026-01','2026-01-30',True),('2026-02','2026-02-26',True),('2026-03','2026-03-04',False)]]
def history():return {'prices':{'1':{'2025-12':100,'2026-01':120,'2026-02':108},'2':{'2025-12':100,'2026-01':110,'2026-02':99},'3':{'2025-12':100,'2026-01':105,'2026-02':110},'4':{'2025-12':100,'2026-01':115,'2026-02':120},'5':{'2025-12':100,'2026-01':999}},'metadata':{}}
def response():
 ts=[int(datetime(2026,m,1,tzinfo=TZ).timestamp()) for m in [1,2,3]]
 return {'chart':{'result':[{'meta':{'symbol':'2330.TW','dataGranularity':'1mo','currency':'TWD','exchangeTimezoneName':'Asia/Taipei'},'timestamp':ts,'indicators':{'quote':[{'close':[100,110,999]}],'adjclose':[{'adjclose':[1,2,9999]}]}}]}}
class MonthlySectorTests(unittest.TestCase):
 def test_monthly_price_close_not_adjusted_or_live_month(self):
  r=S.parse_monthly(response(),'2330.TW','2026-03');self.assertEqual(r,{'2026-01':100,'2026-02':110})
  for field,value in [('dataGranularity','1d'),('symbol','9999.TW'),('currency','USD')]:
   z=response();z['chart']['result'][0]['meta'][field]=value
   with self.assertRaises(ValueError):S.parse_monthly(z,'2330.TW','2026-03')
 def test_equal_mean_exact_calendar_month_and_custom_overlapping_groups(self):
  r=S.calculate(dictionary(),history(),timeline(),{},now=NOW);jan=r['months']['2026-01'];names=[x['name'] for x in jan['leaders']]
  self.assertEqual(set(names),{'AI伺服器','ASIC','ABF載板'});self.assertEqual(jan['eligible_groups'],3)
  self.assertAlmostEqual(next(x['return_pct'] for x in jan['leaders'] if x['name']=='AI伺服器'),15)
  self.assertEqual(jan['previous_date'],'2025-12-31');self.assertEqual(jan['date'],'2026-01-30')
  self.assertEqual(r['metadata']['classification_source'],'prototype/sectors.sqlite3')
 def test_missing_month_does_not_bridge_and_future_does_not_change_past(self):
  h=history();before=S.calculate(dictionary(),h,timeline(),{},now=NOW)['months']['2026-01'];h['prices']['1']['2026-03']=999999
  self.assertEqual(S.calculate(dictionary(),h,timeline(),{},now=NOW)['months']['2026-01'],before)
  h['prices']['1'].pop('2026-01');h['prices']['2'].pop('2026-01')
  feb=S.calculate(dictionary(),h,timeline(),{},now=NOW)['months']['2026-02'];self.assertNotIn('AI伺服器',[x['name'] for x in feb['leaders']])
 def test_current_month_requires_exact_local_dates_and_ignores_live_month_history(self):
  h=history()
  for symbol in ['1','2']:h['prices'][symbol]['2026-03']=999999
  r=S.calculate(dictionary(),h,timeline(),{},now=NOW);self.assertEqual(r['months']['2026-03']['status'],'missing')
  local={(d,s):v for s in ['1','2'] for d,v in [('2026-02-26',100),('2026-03-04',110)]}
  r=S.calculate(dictionary(),h,timeline(),local,now=NOW);mar=r['months']['2026-03'];self.assertTrue(mar['partial']);self.assertAlmostEqual(mar['leaders'][0]['return_pct'],10)
  self.assertEqual(mar['return_sources'],{'local-exact-endpoints':2})
 def test_coverage_threshold_and_at_least_two_members(self):
  cl={'error':'','groups':[{'name':'五檔群','parent':'x'}],'memberships':{str(i):['五檔群'] for i in range(5)}}
  h={'prices':{str(i):{'2025-12':100,'2026-01':110} for i in range(4)}}
  r=S.calculate(cl,h,timeline(),{},now=NOW)['months']['2026-01'];self.assertEqual(r['leaders'][0]['coverage_pct'],80)
  h['prices'].pop('3');self.assertEqual(S.calculate(cl,h,timeline(),{},now=NOW)['months']['2026-01']['status'],'missing')
 def test_no_risers_is_not_a_fabricated_positive_top_three(self):
  h=history()
  for v in h['prices'].values():v['2026-02']=v.get('2026-01',100)*.9
  r=S.calculate(dictionary(),h,timeline(),{},now=NOW)['months']['2026-02'];self.assertEqual(r['status'],'no_risers');self.assertEqual(r['leaders'],[])
 def test_get_is_read_only_and_only_taiwan_matching_dates_receive_names(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);market={'metadata':{'label':'台股','timezone':'Asia/Taipei','currency':'TWD','sources':[]},'closes':[{**x,'source':'test'} for x in timeline()]};seed={'version':M.VERSION,'markets':{'taiex':market,'sp500':{**market,'metadata':{**market['metadata'],'timezone':'America/New_York'}}}}
   (p/'cache.json').write_text(json.dumps(seed));artifact=S.calculate(dictionary(),history(),timeline(),{},now=NOW);S.atomic(p/S.FILE,artifact)
   before={x.name:x.read_bytes() for x in p.iterdir()}
   with patch.object(S,'refresh_sync',side_effect=AssertionError('GET rebuilt')),patch.object(M,'_download',side_effect=AssertionError('GET downloaded')):r=M.report(store=p,now=NOW)
   jan=next(x for x in r['markets']['taiex']['rows'] if x['month']=='2026-01');self.assertEqual(len(jan['sector_leaders']),3)
   self.assertNotIn('sector_leaders',r['markets']['sp500']['rows'][1]);self.assertEqual({x.name:x.read_bytes() for x in p.iterdir()},before)
   artifact['months']['2026-01']['date']='2026-01-29';S.atomic(p/S.FILE,artifact);r=M.report(store=p,now=NOW);jan=r['markets']['taiex']['rows'][1];self.assertEqual(jan['sector_status'],'stale');self.assertEqual(jan['sector_leaders'],[])
 def test_sector_refresh_failure_does_not_erase_index_or_prior_sector_cache(self):
  import requests
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);market={'metadata':{'label':'台股','timezone':'Asia/Taipei','currency':'TWD','sources':[]},'closes':[{**x,'source':'test'} for x in timeline()]};seed={'version':M.VERSION,'markets':{'taiex':market,'sp500':market}}
   (p/'cache.json').write_text(json.dumps(seed));old={'version':S.VERSION,'months':{},'metadata':{}};S.atomic(p/S.FILE,old);before=(p/S.FILE).read_bytes()
   with patch.object(M,'_download',side_effect=requests.ConnectionError('offline')),patch.object(S,'refresh_sync',side_effect=ValueError('sector offline')) as rebuild:
    result=M.refresh_sync(store=p,now=NOW)
   rebuild.assert_called_once();self.assertIn('sectors',result['errors']);self.assertEqual((p/S.FILE).read_bytes(),before)
   self.assertEqual(json.loads((p/'cache.json').read_text())['markets'],seed['markets'])
class YtdSectorTests(unittest.TestCase):
 def test_ytd_uses_prior_december_not_month_return_or_sum(self):
  h=history()
  for symbol in ['1','2']:h['prices'][symbol]={'2025-12':100,'2026-01':200,'2026-02':180}
  for symbol in ['3','4']:h['prices'][symbol]={'2025-12':100,'2026-01':100,'2026-02':110}
  months=S.calculate(dictionary(),h,timeline(),{},now=NOW)['months'];feb=months['2026-02'];ytd=feb['ytd']
  self.assertEqual([v['name'] for v in feb['leaders']],['ABF載板'])
  self.assertEqual({v['name'] for v in ytd['leaders'][:2]},{'AI伺服器','ASIC'})
  self.assertAlmostEqual(ytd['leaders'][0]['return_pct'],80)
  self.assertEqual(ytd['previous_date'],'2025-12-31');self.assertEqual(ytd['baseline_month'],'2025-12')
  self.assertEqual(months['2026-01']['leaders'],months['2026-01']['ytd']['leaders'])
  h['prices']['1']['2026-03']=99999
  self.assertEqual(S.calculate(dictionary(),h,timeline(),{},now=NOW)['months']['2026-02']['ytd'],ytd)
 def test_ytd_requires_year_baseline_even_when_month_pair_available(self):
  h=history()
  for v in h['prices'].values():v.pop('2025-12',None)
  feb=S.calculate(dictionary(),h,timeline(),{},now=NOW)['months']['2026-02']
  self.assertEqual(feb['status'],'ready');self.assertEqual(feb['ytd']['status'],'missing')
  self.assertEqual(feb['ytd']['leaders'],[])
 def test_ytd_partial_month_uses_local_year_end_not_live_or_mixed_prices(self):
  h=history()
  for symbol in ['1','2']:h['prices'][symbol]['2025-12']=9999;h['prices'][symbol]['2026-03']=999999
  local={(d,s):v for s in ['1','2'] for d,v in [('2025-12-31',100),('2026-02-26',120),('2026-03-04',135)]}
  mar=S.calculate(dictionary(),h,timeline(),local,now=NOW)['months']['2026-03']
  self.assertAlmostEqual(mar['leaders'][0]['return_pct'],12.5)
  self.assertAlmostEqual(mar['ytd']['leaders'][0]['return_pct'],35)
  self.assertEqual(mar['ytd']['previous_date'],'2025-12-31');self.assertTrue(mar['ytd']['partial'])
  local.pop(('2025-12-31','2'))
  self.assertEqual(S.calculate(dictionary(),h,timeline(),local,now=NOW)['months']['2026-03']['ytd']['status'],'missing')
 def test_ytd_resets_each_year_and_excludes_missing_year_baseline(self):
  h={'prices':{s:{'2024-12':10,'2025-12':100,'2026-01':110} for s in ['1','2']}}
  ts=[{'month':'2024-12','date':'2024-12-31','complete':True},*timeline()]
  jan=S.calculate(dictionary(),h,ts,{},now=NOW)['months']['2026-01']['ytd']
  self.assertAlmostEqual(jan['leaders'][0]['return_pct'],10)
  h['prices']['1'].pop('2025-12');self.assertEqual(S.calculate(dictionary(),h,ts,{},now=NOW)['months']['2026-01']['ytd']['status'],'missing')
 def test_ytd_attach_requires_matching_year_end_dates(self):
  artifact=S.calculate(dictionary(),history(),timeline(),{},now=NOW)
  rows=[{'month':r['month'],'date':r['date'],'previous_date':timeline()[i-1]['date'] if i else None} for i,r in enumerate(timeline())]
  report={'markets':{'taiex':{'rows':rows},'sp500':{'rows':copy.deepcopy(rows)}}};S.attach(report,artifact)
  self.assertEqual(rows[2]['sector_ytd']['previous_date'],'2025-12-31')
  self.assertNotIn('sector_ytd',report['markets']['sp500']['rows'][2])
  artifact['months']['2026-02']['ytd']['previous_date']='2025-12-30';S.attach(report,artifact)
  self.assertEqual(rows[2]['sector_ytd']['status'],'stale');self.assertEqual(rows[2]['sector_ytd']['leaders'],[])
if __name__=='__main__':unittest.main()

