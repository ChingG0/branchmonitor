import json
import math
import tempfile
import unittest
from datetime import date,timedelta
from pathlib import Path
from unittest.mock import patch
from backend import liquidity_river as l, db, pe_river

FIELDS_MARKET=l.FIELDS_MARKET
FIELDS_DAY=l.FIELDS_DAY_TRADE


def payload(rows, fields, daytrade=False):
 return json.dumps({'stat':'OK','fields':fields,'tables':[{'fields':fields,'data':rows}] if daytrade else [],'data':rows})


class LiquidityTests(unittest.TestCase):
 def test_official_join_deducts_one_side_average_and_rejects_bad_schema(self):
  market=payload([['115/10/08','1000','1,000','10','49,000','0']],FIELDS_MARKET)
  trade=payload([['115/10/08','100','10','300','30','320','32']],FIELDS_DAY,True)
  m=l.parse_market(market,'2026-10');t=l.parse_day_trade(trade,'2026-10')
  self.assertEqual(m['2026-10-08']['total_amount']-(t['2026-10-08']['buy']+t['2026-10-08']['sell'])/2,690)
  with self.assertRaises(ValueError):l.parse_day_trade(trade,'2026-09')
  with self.assertRaises(ValueError):l.parse_market(payload([['115/10/08','1000','1,000','10','49,000','0']],['wrong']),'2026-10')

 def test_insufficient_and_missing_days_not_replaced_with_gross(self):
  start=date(2026,7,1)
  market,day_trade={},{}
  for i in range(65):
   stamp=(start+timedelta(days=i)).isoformat()
   market[stamp]={'close':100+i,'total_amount':1000}
   if i != 64:day_trade[stamp]={'buy':300,'sell':300}
  state={'months':{'2026-07':{'market':market,'day_trade':day_trade}}}
  result=l.build(state,date(2026,10,11))
  self.assertEqual(result['as_of'],(start+timedelta(days=63)).isoformat())
  self.assertIn((start+timedelta(days=64)).isoformat(),result['missing_dates'])
  self.assertEqual(result['points'][-1]['after_day_trade_100m'],700/1e8)
  self.assertEqual(result['points'][-1]['window_count'],60)

 def test_trend_center_and_no_future_data(self):
  start=date(2026,5,1);market={};day_trade={}
  for i in range(100):
   stamp=(start+timedelta(days=i)).isoformat()
   market[stamp]={'close':math.exp(6+.005*i),'total_amount':1000}
   day_trade[stamp]={'buy':300,'sell':300}
  state={'months':{'2026-05':{'market':market,'day_trade':day_trade}}}
  result=l.build(state,date(2026,10,11));latest=result['points'][-1]
  self.assertAlmostEqual(latest['median'],latest['close'],places=7)
  self.assertEqual(latest['after_day_trade_100m'],700/1e8)
  early=l.build(state,start+timedelta(days=75))['points'][-1]
  self.assertEqual(early['date'],(start+timedelta(days=75)).isoformat())
  self.assertAlmostEqual(early['median'],early['close'],places=7)

 def test_moving_averages_use_all_prior_market_closes_without_future_prices(self):
  start=date(2025,1,1)
  history={};market={};day_trade={}
  for i in range(260):
   stamp=(start+timedelta(days=i)).isoformat()
   row={'close':100+i,'total_amount':1000}
   if i<180:history[stamp]=row
   else:
    market[stamp]=row
    day_trade[stamp]={'buy':300,'sell':300}
  state={'history_market':{'older':history},'months':{'recent':{'market':market,'day_trade':day_trade}}}
  result=l.build(state,start+timedelta(days=259))
  latest=result['points'][-1]
  self.assertAlmostEqual(latest['ma20'],sum(range(340,360))/20)
  self.assertAlmostEqual(latest['ma60'],sum(range(300,360))/60)
  self.assertAlmostEqual(latest['ma239'],sum(range(121,360))/239)
  self.assertIsNone(result['points'][0]['ma239'])
  previous=l.build(state,start+timedelta(days=258))['points'][-1]
  self.assertEqual(previous['date'],(start+timedelta(days=258)).isoformat())
  self.assertAlmostEqual(previous['ma239'],sum(range(120,359))/239)

 def test_read_is_offline_and_cache_survives_failed_month(self):
  with tempfile.TemporaryDirectory() as folder,patch.object(db,'DATA',Path(folder)):
   self.assertFalse(l.report()['available'])
   day=date.today();key=day.strftime('%Y-%m')
   data={'months':{key:{'market':{'2026-10-01':{'close':1,'total_amount':1000}},'day_trade':{'2026-10-01':{'buy':1,'sell':1}}}}}
   (Path(folder)/'market_liquidity_cache.json').write_text(json.dumps(data))
   with patch.object(l,'parse_market',side_effect=ValueError('broken')),patch.object(l,'month_shift',return_value=day):
    with self.assertRaises(ValueError):l.refresh(lambda u:b'{}')
   self.assertEqual(l.cached()['months'],data['months'])

 def test_pe_api_includes_separate_liquidity_report(self):
  with tempfile.TemporaryDirectory() as folder,patch.object(db,'DATA',Path(folder)):
   result=pe_river.report()
   self.assertIn('liquidity',result)
   self.assertFalse(result['liquidity']['available'])
