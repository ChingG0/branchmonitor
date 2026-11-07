import html,json,math,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from backend import business_cycle as b,db
NOW='2026-11-08T15:00:00+08:00'
def chart_payload(values):
 return '<input id="ContentPlaceHolder1_hidChartData" value="'+html.escape(json.dumps(values),quote=True)+'">'
class BusinessCycleTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.p=patch.object(db,'DATA',Path(self.tmp.name));self.p.start()
 def tearDown(self):self.p.stop();self.tmp.cleanup()
 def test_ndc_lights_boundaries_and_pmi_identity(self):
  self.assertEqual([b.lamp(v)[0] for v in [9,16,17,22,23,31,32,37,38,45]],['藍燈','藍燈','黃藍燈','黃藍燈','綠燈','綠燈','黃紅燈','黃紅燈','紅燈','紅燈'])
  with self.assertRaises(ValueError):b.lamp(46)
  p={'big':'55','right':{'55':{'lang':'製造業PMI(季調值)','d':[{'m':'202609','n':63.4},{'m':'202610','n':62.5}]}}}
  result=b.ndc_series('tw_pmi',p,NOW);self.assertEqual(b.summarize('tw_pmi',result)['reading'],'擴張放慢')
  self.assertAlmostEqual(b.summarize('tw_pmi',result)['change'],-.9)
  p['right']['55']['lang']='非製造業NMI'
  with self.assertRaises(ValueError):b.ndc_series('tw_pmi',p,NOW)
 def test_cpi_ignores_annual_forecast_and_keeps_monthly_yoy(self):
  raw=chart_payload([{'Title':'消費者物價指數年增率','data':[{'Date':'2026/10/1','Value':'2.33','Title_2':'115年10月'}]},{'Title':'消費者物價指數年增率','data':[{'Date':'2026/1/1','Value':'2.07','Title_2':'115年預測'}]}])
  rows=b.stat_series('tw_cpi',raw,NOW)['points'];self.assertEqual(rows,[{'month':'2026-10','value':2.33}])
 def test_yoy_pairs_calendar_month_not_twelve_rows_back(self):
  rows=[{'month':'2025-09','value':100},{'month':'2025-11','value':200},{'month':'2026-09','value':150}]
  result=b.yearly(rows);self.assertEqual([(r['month'],r['value']) for r in result],[('2026-09',50)])
  with self.assertRaises(ValueError):b.yearly([{'month':'2026-10','value':100}])
 def test_orders_amount_has_usd_units_and_not_exports(self):
  raw=chart_payload([{'Title':'外銷訂單','data':[{'Date':'2025/9/1','Value':'70,343','Title_2':'114年9月'},{'Date':'2026/9/1','Value':'114,210','Title_2':'115年9月'}]}])
  r=b.stat_series('tw_orders',raw,NOW)['points'][-1]
  self.assertEqual(r['amount_million_usd'],114210);self.assertAlmostEqual(r['value'],62.3616,places=4)
 def test_bls_annual_average_is_not_a_month_and_series_checked(self):
  payload={'status':'REQUEST_SUCCEEDED','Results':{'series':[{'seriesID':'CUUR0000SA0','data':[{'year':'2025','period':'M09','value':'100'},{'year':'2026','period':'M09','value':'103.7'},{'year':'2026','period':'M13','value':'900'},{'year':'2025','period':'M10','value':'-','footnotes':[{'code':'X'}]}]}]}}
  r=b.bls_series(payload,NOW)['points'];self.assertEqual(len(r),1);self.assertEqual(r[0]['value'],3.7)
  payload['Results']['series'][0]['seriesID']='CUSR0000SA0'
  with self.assertRaises(ValueError):b.bls_series(payload,NOW)
 def test_reject_nonfinite_conflicts_and_future_months(self):
  for v in [None,'NaN','Infinity']:
   with self.assertRaises(ValueError):b.normalize([{'month':'2026-10','value':v}],NOW)
  with self.assertRaises(ValueError):b.normalize([{'month':'2026-10','value':1},{'month':'2026-10','value':2}],NOW)
  self.assertEqual(len(b.normalize([{'month':'2026-10','value':1},{'month':'2026-12','value':2}],NOW)),1)
 def test_partial_refresh_preserves_old_values_and_report_never_fetches(self):
  b.atomic(b.folder()/'latest.json',{'series':{'tw_pmi':{'points':[{'month':'2026-10','value':62.5}],'checked_at':'2026-11-02'}},'checked_at':'2026-11-02','warnings':[]})
  r=b.refresh_sync(collector=lambda now:({}, {'tw_pmi':'來源拒絕'}),now=NOW);self.assertEqual(r['status'],'partial')
  with patch.object(b,'fetch',side_effect=AssertionError('GET cannot fetch')):
   report=b.report();item=next(s for s in report['series'] if s['key']=='tw_pmi');self.assertEqual(item['points'][-1]['value'],62.5);self.assertEqual(item['checked_at'],'2026-11-02');self.assertEqual(item['error'],'來源拒絕')
 def test_manual_final_has_provenance_and_does_not_inherit_flash(self):
  path=b.folder()/'manual'/'us_pmi.json'
  b.atomic(path,{'points':[{'month':'2026-10','value':54.9,'stage':'final'}],'source_url':'https://www.spglobal.com/report','verified_at':NOW,'released_at':'2026-11-02'})
  b.refresh_sync(collector=lambda now:({},{}),now=NOW)
  s=b.read()['series']['us_pmi'];self.assertEqual(s['points'][0]['value'],54.9);self.assertEqual(s['checked_at'],NOW)
  b.atomic(path,{'points':[{'month':'2026-10','value':56}],'source_url':'https://bad.example','verified_at':NOW})
  b.refresh_sync(collector=lambda now:({},{}),now=NOW);self.assertEqual(b.read()['series']['us_pmi']['points'][0]['value'],54.9)
if __name__=='__main__':unittest.main()
