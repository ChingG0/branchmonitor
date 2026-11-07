import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from datetime import date,timedelta,datetime
from backend import db,research,strategies as s

class StrategiesTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.p=patch.object(db,'DATA',Path(self.tmp.name));self.p.start();s.initialize()
 def tearDown(self):self.p.stop();self.tmp.cleanup()
 def quote(self,day,value,adj=None,op=None):return {'date':day,'open':op or value,'close':value,'adj':adj or value,'adj_open':(op or value)*(adj or value)/value}
 def sample(self):
  days=[(date(2025,12,31)+timedelta(days=i)).isoformat() for i in range(50)]
  return {'rows':[self.quote(d,100) for d in days],'dividends':[],'source':'https://example.org','gaps':[]}
 def test_settings_reject_invalid_and_blank_borrow_preserved(self):
  for data in [{'stock_value':True},{'allocation_count':0},{'borrow_pct':float('nan')},{'etfs':['../../bad']},{'fee_bps':-1}]:
   with self.assertRaises(ValueError):s.configure(data)
  self.assertIsNone(s.configure({'borrow_pct':None})['borrow_pct'])
 def test_leverage_inverse_etn_excluded(self):
  for typ in ['ETF · 槓桿','ETF · 反向','ETN']:self.assertFalse(s.normal_etf({'product_type':typ}))
  self.assertTrue(s.normal_etf({'product_type':'ETF','name':'無配息基金'}))
 def test_auto_ytd_candidates_ignore_distribution_and_exclude_derivatives(self):
  rows=[
   {'symbol':'00901','name':'無配息科技','product_type':'ETF','rs':96,'ytd_pct':82},
   {'symbol':'00902','name':'有配息科技','product_type':'ETF','rs':94,'ytd_pct':80},
   {'symbol':'00903','name':'低強度','product_type':'ETF','rs':89,'ytd_pct':70},
   {'symbol':'00663L','name':'台灣正2','product_type':'ETF · 槓桿','rs':99,'ytd_pct':120},
   {'symbol':'00642U','name':'期元大石油','product_type':'ETF','rs':98,'ytd_pct':100},
  ]
  cfg={**s.DEFAULTS,'etfs':['0050'],'auto_ytd_count':5,'auto_ytd_min_rs':90}
  result=s.auto_ytd_candidates({r['symbol']:r for r in rows},cfg)
  self.assertEqual([r['symbol'] for r in result],['00901','00902'])
 def test_auto_ytd_candidates_respect_total_twelve_and_no_duplicates(self):
  manual=[f'00{i:03d}' for i in range(10)]
  rows=[{'symbol':symbol,'name':'一般ETF','product_type':'ETF','rs':99-i,'ytd_pct':80-i} for i,symbol in enumerate(['00901','00902','00903'])]
  rows.append({'symbol':manual[0],'name':'自選重複','product_type':'ETF','rs':100,'ytd_pct':100})
  cfg={**s.DEFAULTS,'etfs':manual,'auto_ytd_count':5,'auto_ytd_min_rs':90}
  self.assertEqual([r['symbol'] for r in s.auto_ytd_candidates({r['symbol']:r for r in rows},cfg)],['00901','00902'])
 def test_auto_settings_validate_integer_count(self):
  with self.assertRaises(ValueError):s.configure({'auto_ytd_count':1.5})
  self.assertEqual(s.configure({'auto_ytd_count':0,'auto_ytd_min_rs':95})['auto_ytd_count'],0)
 def test_total_return_uses_adjustment_without_double_dividend(self):
  d=self.sample()
  for r in d['rows'][1:]:r['close']=90
  d['dividends']=[{'date':'2026-01-01','amount':10}]
  m=s.etf_metrics(d,'2026-02-18',{**s.DEFAULTS,'fee_bps':0,'slippage_bps':0})
  self.assertEqual(m['ytd_total'],0);self.assertAlmostEqual(m['price_return'],-10);self.assertEqual(m['position_pct'],0)
 def test_no_distribution_etf_has_position_and_market_price(self):
  m=s.etf_metrics(self.sample(),'2026-02-18',{**s.DEFAULTS,'fee_bps':0,'slippage_bps':0})
  self.assertEqual(m['dividends'],[]);self.assertEqual(m['market_price'],100);self.assertEqual(m['position_label'],'中位');self.assertIsNone(m['nav'])
 def test_rolling_plan_prefers_low_position_and_respects_amount(self):
  rows=[{'symbol':'A','position_pct':10,'rs_ytd':90,'ytd_total':20,'market_price':100},{'symbol':'B','position_pct':30,'rs_ytd':95,'ytd_total':40,'market_price':50},{'symbol':'C','position_pct':80,'rs_ytd':99,'ytd_total':80,'market_price':10}]
  risk={'deployable':10000,'actionable':True};plan=s.rolling_plan(rows,risk,2)
  self.assertEqual(plan['symbols'],['A','B']);self.assertEqual(sum(r['recommended_amount'] for r in rows),10000);self.assertFalse(rows[2]['recommended']);self.assertEqual(rows[0]['estimated_lots'],0);self.assertGreater(rows[0]['estimated_odd_lots'],0)
 def test_daily_analysis_freezes_selected_etf_evidence(self):
  risk={'target_equity_pct':62.5,'coverage':87.5,'deployable':12000,'trim_amount':0}
  plan={'count':1};rows=[{'symbol':'0050','name':'元大台灣50','recommended':True,'recommended_amount':12000,'estimated_units':60,'estimated_price':200,'market_price':202,'position_pct':32,'rs_ytd':91,'ytd_total':18,'swing_action':'靠近支撐'},
                          {'symbol':'0056','name':'高股息','recommended':False}]
  result=s.daily_analysis('2026-10-01',risk,plan,rows,'2026-10-01T18:00:00+08:00')
  self.assertTrue(result['frozen']);self.assertEqual(result['mode'],'rolling-etf-v1');self.assertEqual(result['action'],'add')
  self.assertEqual([r['symbol'] for r in result['selected']],['0050']);self.assertEqual(result['selected'][0]['units'],60)
  self.assertIn('12,000',result['summary'])
 def test_refresh_reuses_latest_same_day_snapshot_without_market_fetch(self):
  target='2026-10-01';payload={'id':'saved','date':target,'created_at':target+'T18:00:00+08:00','risk':{'target_equity_pct':60},'warnings':[],'settings':s.DEFAULTS,'pairs':{'rows':[]},'etfs':[]}
  with research.store() as c:c.execute('INSERT INTO strategy_snapshots VALUES (?,?,?,?)',('saved',target,payload['created_at'],json.dumps(payload)))
  context={'date':target,'candidates':[],'warnings':[]}
  with patch.object(research,'context',return_value=context),patch.object(s,'catalog_snapshot',return_value=(target,{})),patch.object(s,'refresh_market_risk_inputs') as live:
   first=s.refresh(target,wait=True);second=s.refresh(target,wait=True)
  self.assertTrue(first['cached']);self.assertTrue(second['cached']);self.assertEqual(first['snapshot_id'],'saved');live.assert_not_called()
  with research.store() as c:self.assertEqual(c.execute('SELECT count(*) FROM strategy_snapshots').fetchone()[0],1)
 def test_rotation_plan_marks_profitable_high_position_and_next_etf(self):
  rows=[{'symbol':'HIGH','name':'高檔','position_pct':90,'rs_ytd':95,'ytd_total':40,'market_price':100,'resistance_1':102,'support_1':90,'distance_resistance_pct':2},{'symbol':'LOW','name':'低檔','position_pct':20,'rs_ytd':90,'ytd_total':15,'market_price':50,'resistance_1':60,'support_1':48,'distance_resistance_pct':20}]
  plan=s.rolling_plan(rows,{'deployable':0,'trim_amount':10000,'post_trim_cash':30000,'actionable':True},1)
  self.assertEqual(plan['adjustments'],['HIGH']);self.assertEqual(plan['rotations'][0]['to_symbol'],'LOW');self.assertEqual(rows[0]['estimated_trim_units'],100);self.assertEqual(plan['post_trim_cash'],30000)
 def test_future_prices_never_included(self):
  d=self.sample();a=s.etf_metrics(d,'2026-02-10',s.DEFAULTS);d['rows'][-1]['adj']=100000
  self.assertEqual(a,s.etf_metrics(d,'2026-02-10',s.DEFAULTS))
 def test_missing_ytd_anchor_does_not_invent_ytd(self):
  d=self.sample();d['rows']=d['rows'][1:];self.assertIsNone(s.etf_metrics(d,'2026-02-18',s.DEFAULTS)['ytd_total'])
 def test_missing_session_blocks_dca(self):
  d=self.sample();b=copy.deepcopy(d);d['rows'].pop(20)
  with self.assertRaises(ValueError):s.etf_metrics(d,'2026-02-18',s.DEFAULTS,b)
 def test_explicit_gap_blocks_dca(self):
  d=self.sample();d['gaps']=['2026-01-08']
  with self.assertRaises(ValueError):s.etf_metrics(d,'2026-02-18',s.DEFAULTS)
 def test_exact_official_row_repairs_vendor_gap_without_interpolation(self):
  db.initialize();d=self.sample();missing=d['rows'][20];d['rows'].remove(missing);d['gaps']=[missing['date']]
  with db.connect() as c:c.execute('INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?)',(missing['date'],'0050','元大台灣50',101,100,102,99,123456,'TWSE'))
  repaired=s.repair_official_gaps(d,'0050','2026-02-18')
  row=next(r for r in repaired['rows'] if r['date']==missing['date'])
  self.assertEqual((row['open'],row['high'],row['low'],row['close'],row['volume']),(100,102,99,101,123456))
  self.assertEqual(repaired['gaps'],[]);self.assertIn('證交所同日',repaired['basis'])
 def test_vendor_gap_without_official_row_remains_blocked(self):
  db.initialize();d=self.sample();d['gaps']=['2026-01-08']
  self.assertEqual(s.repair_official_gaps(d,'0050','2026-02-18')['gaps'],['2026-01-08'])
 def test_parse_chart_reject_wrong_currency(self):
  d={'chart':{'result':[{'meta':{'symbol':'0050.TW','currency':'USD','exchangeTimezoneName':'Asia/Taipei'},'indicators':{'quote':[{'close':[]}],'adjclose':[{'adjclose':[]}]},'timestamp':[]}]}}
  with self.assertRaises(ValueError):s.parse_chart(d,'0050.TW','2026-09-18')
 def test_parse_official_nav_page(self):
  html='<script>let chartData = {"netPrice":[{"count":111.7,"date":"09/22"}],"atmps":[{"count":0.13,"date":"09/22"}]};</script>'
  result=s.parse_nav_page(html,'0050','2026-09-23')
  self.assertEqual(result['nav'],111.7);self.assertEqual(result['nav_as_of'],'2026-09-22');self.assertEqual(result['premium_pct'],.13)
 def test_parse_official_index_candles(self):
  raw={'stat':'OK','fields':['日期','開盤指數','最高指數','最低指數','收盤指數'],'data':[['115/09/22','47,100.5','47,550','46,980','47,400'],['115/09/24','48,000','48,200','47,900','48,100']]}
  rows=s.parse_index_ohlc(raw,'2026-09-23')
  self.assertEqual(rows,[{'date':'2026-09-22','open':47100.5,'high':47550.0,'low':46980.0,'close':47400.0}])
 def test_live_risk_inputs_refresh_stale_and_allow_latest_values(self):
  db.initialize()
  fear={'value':55,'as_of':'2026-09-23T06:00:00+08:00'};twd={'value':31.8,'change_pct':-.2,'as_of':'2026-09-23T14:00:00+08:00'}
  with db.connect() as c:
   c.execute('INSERT OR REPLACE INTO market_observations VALUES (?,?,?,?,?)',('fear',json.dumps(fear),'2000-01-01T00:00:00+08:00','2000-01-01T00:00:00+08:00',''))
   c.execute('INSERT OR REPLACE INTO market_observations VALUES (?,?,?,?,?)',('twd',json.dumps(twd),'2000-01-01T00:00:00+08:00','2000-01-01T00:00:00+08:00',''))
  with patch.object(s.market_monitor,'update_one',return_value=True) as update:
   self.assertEqual(s.refresh_market_risk_inputs(),['fear','twd','volume']);self.assertEqual(update.call_count,3)
  self.assertNotIn('fear',s._observation('2026-09-22'))
  live=s._observation('2026-09-22',use_latest=True);self.assertEqual(live['fear']['value'],55);self.assertEqual(live['twd']['change_pct'],-.2)
 def test_allocation_evidence_excludes_future(self):
  with research.store() as c:c.execute('INSERT INTO strategy_snapshots VALUES (?,?,?,?)',('x','2026-09-19','2026-09-20','{}'))
  self.assertIsNone(s.allocation_evidence('2026-09-18')['date'])
 def test_refresh_fetches_etfs_only_and_never_legacy_pair_symbols(self):
  target='2026-10-01';universe={symbol:{'symbol':symbol,'name':symbol,'product_type':'ETF','market':'TWSE','rs':95,'ytd_pct':20} for symbol in s.DEFAULTS['etfs']}
  context={'date':target,'candidates':[{'symbol':'2330'},{'symbol':'2317'}],'warnings':[]}
  risk={'score':50,'coverage':100,'actionable':True,'target_equity_pct':60,'current_equity_pct':50,'stock_value':100000,'cash_value':100000,'total_assets':200000,'target_stock_value':120000,'deployable':20000,'trim_amount':0,'reserve_cash':80000,'factors':[],'status':'測試'}
  plan={'count':0,'symbols':[],'deployable':20000,'estimated_used':0,'leftover':20000,'method':'測試'}
  metric={'as_of':target,'start':'2025-12-31','sample':2,'ytd_total':1,'price_return':1,'excess_0050':0,'drawdown':0,'volatility':1,'position_pct':50,'position_label':'中位','market_price':100,'support_1':95,'support_2':90,'resistance_1':105,'resistance_2':110,'distance_support_pct':5,'distance_resistance_pct':5,'swing_action':'觀察','nav':None,'nav_as_of':None,'premium_pct':None,'nav_note':'待補','dividends':[],'curve':[],'source':'test','stale':False,'limitations':'測試'}
  with patch.object(research,'context',return_value=context) as research_context,patch.object(s,'catalog_snapshot',return_value=(target,universe)),patch.object(s,'refresh_market_risk_inputs',return_value=[]),patch.object(s,'refresh_index_bars'),patch.object(s,'fetch_chart',return_value=self.sample()),patch.object(s,'etf_metrics',return_value=metric),patch.object(s,'fetch_nav',side_effect=ValueError('待補')),patch.object(s,'risk_dashboard',return_value=risk),patch.object(s,'rolling_plan',return_value=plan):
   result=s.refresh(target,wait=True,force=True)
  self.assertIn(result['status'],('complete','partial'));research_context.assert_called_once_with(target,include_stocks=False)
  with research.store() as c:
   self.assertEqual([r[0] for r in c.execute('SELECT symbol FROM strategy_quotes ORDER BY symbol')],sorted(s.DEFAULTS['etfs']))
   payload=json.loads(c.execute('SELECT payload FROM strategy_snapshots WHERE date=?',(target,)).fetchone()[0])
   self.assertEqual(payload['pairs']['rows'],[]);self.assertNotIn('2330',[r[0] for r in c.execute('SELECT symbol FROM strategy_quotes')])
   self.assertEqual(c.execute('SELECT count(*) FROM strategy_trades').fetchone()[0],0)
 def test_risk_history_deduplicates_day_and_delays_decision_until_next_session(self):
  days=[(date(2026,8,20)+timedelta(days=i)).isoformat() for i in range(25)]
  def payload(target,current=40):return json.dumps({'risk':{'target_equity_pct':target,'current_equity_pct':current}})
  with research.store() as c:
   for i,day in enumerate(days):c.execute('INSERT INTO risk_index_bars VALUES (?,?,?,?,?,?,?)',(day,100+i-.5,101+i,99+i,100+i,'test','2026-09-24'))
   c.execute('INSERT INTO strategy_snapshots VALUES (?,?,?,?)',('a',days[19],days[19]+'T18:00:00+08:00',payload(70)))
   c.execute('INSERT INTO strategy_snapshots VALUES (?,?,?,?)',('b',days[22],days[22]+'T18:00:00+08:00',payload(50)))
   c.execute('INSERT INTO strategy_snapshots VALUES (?,?,?,?)',('c',days[22],days[22]+'T19:00:00+08:00',payload(55)))
  result=s.risk_history();self.assertEqual(len(result['decisions']),2);self.assertEqual(result['decisions'][-1]['target'],55)
  self.assertEqual(result['decisions'][0]['effective_date'],days[20]);self.assertEqual(result['decisions'][1]['effective_date'],days[23])
  self.assertEqual(sum(row['target_equity_pct'] is not None for row in result['bars']),2)
  self.assertGreater(result['summary']['latest_trend_20_pct'],0);self.assertEqual(result['summary']['rising_market_cuts'],1)
  self.assertIsNone(result['summary']['trend_allocation_correlation']);self.assertIn('分批增持',result['summary']['guidance'])
