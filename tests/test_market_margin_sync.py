import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from backend import db,margin_statistics as src,market_margin_sources as shared,margin_balance as balance,market_margin_maintenance as maintenance,liquidity_river as river,pe_river,market_monitor as market

DAY='2026-11-06'
class MarketMarginSync(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.old=db.DATA;db.DATA=Path(self.tmp.name);db.initialize();src.initialize()
  with db.connect() as c:c.execute('INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?)',(DAY,'TAIEX','加權',100,100,101,99,1,'TWSE'))
 def tearDown(self):db.DATA=self.old;self.tmp.cleanup()
 def seed(self,day=DAY,market='TWSE',quote=True):
  with src.connect(write=True) as c:
   c.execute('INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?)',('credit',market,'credit',day,'hash','official',DAY+'T18:00:00+08:00',None,2,'validated','unused'))
   c.execute('INSERT INTO latest VALUES(?,?,?,?)',(market,'credit',day,'credit'))
   c.execute('INSERT INTO totals VALUES(?,?,?,?,?,?,?,?,?)',('credit',8000000,0,0,0,8000000,150,150,3))
   for symbol,q in [('2330',100),('0050',50)]:
    c.execute('INSERT INTO credit VALUES('+','.join('?'*16)+')',('credit',symbol,symbol,0,0,0,q,q,0,0,0,0,0,0,'',0))
   if quote:
    c.execute('INSERT INTO latest VALUES(?,?,?,?)',(market,'quotes',day,'prices'))
    for symbol,close in [('2330',120),('0050',80)]:c.execute('INSERT INTO quotes VALUES(?,?,?,?,?,?,?,?,?,?)',('prices',symbol,symbol,close,close,close,close,1,close,None))
 def test_shared_sources_fill_both_caches_without_network_or_date_guess(self):
  self.seed()
  with patch.object(market,'fetch',side_effect=AssertionError('should not request')):
   result=river.refresh_financing(market.fetch,[DAY])
  self.assertEqual(result['margin']['days'][DAY]['financing_units'],150)
  self.assertEqual(result['margin']['days'][DAY]['short_units'],3)
  self.assertEqual(result['maintenance']['days'][DAY]['maintenance_pct'],200)
  self.assertEqual(result['maintenance']['days'][DAY]['covered_units_pct'],100)
  self.assertEqual(result['maintenance']['days'][DAY]['source_snapshot_id'],'credit')
 def test_PE_failure_cannot_skip_credit_update(self):
  self.seed()
  with patch.object(pe_river,'cached',return_value={'months':{},'closes':{},'price_months':{},'complete_price_months':[]}),patch.object(river,'report',return_value={'points':[]}),patch.object(market,'fetch',side_effect=ValueError('PE unavailable')):
   with self.assertRaises(ValueError):pe_river.refresh(market.fetch)
  self.assertEqual(maintenance.cached()['as_of'],DAY);self.assertEqual(balance.cached()['as_of'],DAY)
 def test_no_cross_market_or_cross_day_substitution(self):
  self.seed(day='2026-11-05',market='TPEX')
  self.assertEqual(shared.read_saved([DAY]),{})
  result=river.refresh_financing(None,[DAY])
  self.assertIsNone(result['maintenance']['as_of']);self.assertNotIn(DAY,result['margin']['days']);self.assertIn('請手動更新',result['maintenance']['warning'])
 def test_missing_quote_source_keeps_balance_but_does_not_invent_ratio(self):
  self.seed(quote=False);result=river.refresh_financing(None,[DAY])
  self.assertEqual(result['margin']['as_of'],DAY);self.assertIsNone(result['maintenance']['as_of'])
 def test_one_failed_cache_does_not_block_the_other(self):
  self.seed()
  with patch.object(balance,'refresh',side_effect=OSError('write failed')):result=river.refresh_financing(None,[DAY])
  self.assertEqual(result['maintenance']['days'][DAY]['maintenance_pct'],200)
  self.assertIn('write failed',result['margin']['warning'])
 def test_nested_margin_gap_is_partial_instead_of_success(self):
  value={'available':True,'warning':'','liquidity':{'available':True,'as_of':DAY,'points':[{'date':DAY,'financing_units':150,'short_units':3,'maintenance_pct':None}],'maintenance_warning':'來源未齊'}}
  with patch.object(market,'load_card',return_value=value):self.assertFalse(market.update_one('pe_river'))
  with db.connect() as c:r=c.execute("SELECT error FROM market_observations WHERE key='pe_river'").fetchone()
  self.assertIn('維持率未齊',r['error']);self.assertIn('請手動更新',r['error'])
 def test_failed_explicit_update_is_partial_and_not_automatically_retried(self):
  class Immediate:
   def __init__(self,target,**kwargs):self.target=target
   def start(self):self.target()
  from concurrent.futures import Future
  class Pool:
   def __init__(self,**kwargs):pass
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def submit(self,fn,*args):
    f=Future();f.set_result(fn(*args));return f
  old=dict(market._job)
  try:
   with patch.object(market,'update_one',return_value=False) as update,patch.object(market.threading,'Thread',Immediate),patch.object(market,'ThreadPoolExecutor',Pool):
    result=market.refresh('pe_river');self.assertEqual(update.call_count,1)
   self.assertEqual(result['status'],'partial');self.assertFalse(result['running']);self.assertIn('請手動更新',result['message'])
  finally:market._job.clear();market._job.update(old)

if __name__=='__main__':unittest.main()
