import copy,unittest
from backend import margin_observations as o,margin_observation_engine as e,margin_principal_repair as strict

def row(symbol='1234',q=1,close=10,a=6000,b=6500,category='listed_stock'):
 return dict(symbol=symbol,name='樣本'+symbol,market='TWSE',category=category,quantity=q,close=close,loan_a=a,loan_b=b,unpriced_a=0.,unpriced_b=0.,unknown_a=1.,unknown_b=1.,filled_a=False,filled_b=False)
class ObservationTests(unittest.TestCase):
 def test_official_ratio_does_not_require_individual_cost(self):
  r=row(close=35,a=None,b=None);x=o.official_market([r],20000,18000,'TWSE');self.assertEqual(x['official_maintenance'],1.75);self.assertEqual(x['change20_amount'],2000);self.assertIsNone(x['stock_risk']['maintenance'])
 def test_official_ratio_includes_ETF_and_marks_missing_price_as_lower_bound(self):
  rows=[row(close=10),row('0050',close=20,category='etf_cap'),row('3333',close=None)]
  x=o.official_market(rows,20000,None,'TWSE');self.assertEqual(x['official_maintenance'],1.5);self.assertTrue(x['maintenance_lower_bound']);self.assertEqual(x['missing_close_names'],1)
 def test_all_prices_missing_does_not_publish_zero(self):
  self.assertIsNone(o.official_market([row(close=None)],1000,None,'TWSE')['official_maintenance'])
 def test_initial_unknown_cost_is_estimate_not_global_gate(self):
  x=o.aggregate([row()]);self.assertEqual(x['classified_names'],1);self.assertIsNotNone(x['maintenance']);self.assertEqual(x['initial_assumption_names'],1)
 def test_one_missing_cost_does_not_hide_other_names(self):
  x=o.aggregate([row(),row('5678',a=None,b=None)]);self.assertEqual(x['classified_names'],1);self.assertEqual(x['pending_names'],1);self.assertIsNotNone(x['bins'][4]['amount'])
 def test_missing_price_pending_distinct_from_missing_cost(self):
  x=o.aggregate([row(),row('2',close=None),row('3',a=None,b=None)]);self.assertEqual(x['missing_cost_names'],1);self.assertEqual(x['missing_price_names'],1)
 def test_two_method_range_uses_same_pool_and_keeps_cross_band(self):
  x=o.aggregate([row(close=10,a=7000,b=9000)]);self.assertEqual(x['below130_count'],[0,1]);self.assertEqual(x['below130_amount'],[0.,9000.]);self.assertEqual(x['cross_band_names'],1)
 def test_each_methods_bins_conserve_debt_and_not_interval_endpoints(self):
  x=o.aggregate([row('1',a=7000,b=9000),row('2',a=10000,b=9000)])
  self.assertEqual(x['loan'],[17000,18000]);self.assertEqual(x['classified_names'],2)
  self.assertEqual(x['range_kind'],'two_method_estimate_not_confidence_interval')
 def test_ETF_risk_counts_not_published(self):
  x=o.aggregate([row('0050',a=10000,b=11000,category='etf_cap')],etf=True);self.assertIsNone(x['below130_count']);self.assertTrue(all(b['count'] is None for b in x['bins']));self.assertIsNotNone(x['below130_amount'])
 def test_zero_positions_do_not_count_as_risk(self):
  x=o.aggregate([row(q=0,a=0,b=0)]);self.assertEqual(x['active_names'],0);self.assertEqual(x['below130_count'],[0,0]);self.assertIsNone(x['maintenance'])
 def test_no_priced_names_does_not_mean_zero_danger(self):
  x=o.aggregate([row(a=None,b=None)]);self.assertIsNone(x['below130_count']);self.assertTrue(all(b['amount'] is None for b in x['bins']))
 def test_changes_use_comparable_names_not_different_universes(self):
  now=[row('1',a=9000,b=10000),row('2',a=100000,b=100000)];old=[row('1',a=6000,b=8000)]
  d=o.comparison(now,old);self.assertEqual(d['amount'],[2000,3000]);self.assertEqual(d['compared_names'],1);self.assertEqual(d['requested_names'],2)
 def test_zero_baseline_is_not_divided_by(self):
  d=o.comparison([row()],[row(q=0,a=0,b=0)]);self.assertEqual(d['amount'],[6000,6500]);self.assertIsNone(d['pct'])
 def test_missing_baseline_remains_unknown(self):self.assertIsNone(o.comparison([row()],None)['amount'])
 def test_debt_rank_fixed_by_midpoint_not_one_method(self):
  rows=[row('1',a=1000,b=11000),row('2',a=7000,b=7000)];v=o.select(rows,None,None,'TWSE');self.assertEqual(v['debt30'][0]['symbol'],'2');self.assertIsNone(v['increase30']);self.assertIsNone(v['rs30'])
 def test_increase_rank_only_positive_increases(self):
  now=[row('1',a=5000,b=6000),row('2',a=9000,b=10000)];old=[row('1',a=6000,b=6500),row('2',a=6000,b=6500)];v=o.select(now,old,None,'TWSE');self.assertEqual([r['symbol'] for r in v['increase30']],['2'])
 def test_RS_YTD_exact_market_and_positive_excess(self):
  rows=[row('1'),row('2'),row('3')];rs={'groups':{'20':[{'symbol':'2','market':'TWSE'},{'symbol':'1','market':'TPEX'},{'symbol':'1','market':'TWSE'}],'ytd':[{'symbol':'1','market':'TWSE','excess_pct':0},{'symbol':'2','market':'TWSE','excess_pct':3},{'symbol':'3','market':'TWSE','excess_pct':-1}]}};v=o.select(rows,None,rs,'TWSE');self.assertEqual([r['symbol'] for r in v['rs30']],['2','1']);self.assertEqual([r['symbol'] for r in v['ytd']],['2'])
 def test_exact_boundaries_use_buffer_not_rounded_maintenance(self):
  self.assertEqual(e.bucket(1.3),1);self.assertEqual(e.bucket(1.3/.93),1);self.assertEqual(e.bucket(1.3/.87),2);self.assertEqual(e.bucket(1.3/.78),3);self.assertEqual(e.bucket(1.7),4)
 def test_invalid_category_and_market_rejected(self):
  with self.assertRaises(ValueError):o.report(category='invalid')
  with self.assertRaises(ValueError):o.report(market='invalid')

class ProvisionalCostTests(unittest.TestCase):
 def test_continues_existing_unit_cost_not_new_quote(self):
  for method in ['average','fifo']:
   b=e.Book(method);b.prepare(6000);b.add(10,6000);b.prepare(30000);b.add(2,None);self.assertEqual(b.principal(),72000);self.assertTrue(b.summary()['cost_filled'])
 def test_provisional_fill_never_changes_strict_engine(self):
  b=strict.Book('average');b.add(10,None,unknown=True);self.assertIsNone(b.principal())
 def test_no_cost_and_no_price_stays_missing(self):
  b=e.Book('fifo');b.prepare(None);b.add(5,None);self.assertIsNone(b.principal());self.assertFalse(b.summary()['cost_filled'])
 def test_first_available_reference_is_explicit_unknown_initialization(self):
  for method in ['average','fifo']:
   b=e.Book(method);b.add(5,None,unknown=True);b.prepare(7000);self.assertEqual(b.principal(),35000);self.assertEqual(b.summary()['unknown_fraction'],1.);self.assertTrue(b.summary()['cost_filled']);b.remove(5);self.assertEqual(b.principal(),0)
 def test_future_prices_do_not_rewrite_observation_prefix(self):
  def r(q,prev,buy,p):return {'symbol':'1','name':'樣本','previous':prev,'balance':q,'buy':buy,'sell':0,'repay':0,'notes':'','close':p,'volume':1000,'turnover':p*1000,'low':p,'high':p,'shares':1000000}
  seq=[(0,'a',[r(10,0,10,10)],1),(1,'b',[r(12,12,0,30)],1),(2,'c',[r(12,12,0,40)],1)]
  short=list(e.reconstruct(seq[:2],methods=('net_average','net_fifo')));full=list(e.reconstruct(seq,methods=('net_average','net_fifo')));self.assertEqual(short,full[:2]);self.assertEqual(full[1]['securities'][0]['models']['net_average']['principal'],72000)

if __name__=='__main__':unittest.main()
