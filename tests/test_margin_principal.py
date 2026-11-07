import unittest,copy
from backend.margin_principal import Book,flows,price_proxy,bucket,distribution_distance,sensitivity_tier,drift,reconstruct
class PrincipalTests(unittest.TestCase):
 def test_average_and_fifo_hand_calculation(self):
  a,f=Book('average'),Book('fifo')
  for b in [a,f]:b.add(100,6000,True);b.add(10,12000);b.remove(20);self.assertEqual(b.q,90)
  self.assertAlmostEqual(a.principal(),720000*90/110);self.assertAlmostEqual(f.principal(),600000);self.assertAlmostEqual(a.unknown_q,100*90/110);self.assertEqual(f.unknown_q,80)
 def test_zero_net_can_replace_all_old_inventory(self):
  self.assertEqual(flows(100,100,100,0,100,'net'),(0,0));self.assertEqual(flows(100,100,100,0,100,'replacement'),(100,100))
 def test_same_day_exits_can_exceed_previous_without_negative_lots(self):self.assertEqual(flows(10,100,95,0,15,'replacement'),(10,15))
 def test_flow_identity_and_no_double_offset(self):
  self.assertEqual(flows(100,30,10,5,115),(0,15))
  with self.assertRaises(ValueError):flows(100,30,10,5,110)
 def test_over_repayment_and_negative_add_rejected(self):
  b=Book('fifo');b.add(10,6000)
  with self.assertRaises(ValueError):b.remove(11)
  with self.assertRaises(ValueError):b.add(-1,100)
 def test_unpriced_is_not_zero_and_can_be_repaid(self):
  for method in ['average','fifo']:
   b=Book(method);b.add(10,None,True);self.assertIsNone(b.principal());b.remove(10);self.assertEqual(b.principal(),0)
 def test_seed_sensitivity_linear_without_rescaling(self):
  b=Book('average');b.add(100,6000,True);b.add(10,12000);self.assertEqual(b.principal(.5),420000);self.assertEqual(b.principal(2),1320000)
 def test_cap_uncertainty_proportional_or_fifo(self):
  a,f=Book('average'),Book('fifo')
  for b in [a,f]:b.add(100,6000,cap_unknown=True);b.add(100,6000);b.remove(100)
  self.assertEqual(a.cap_q,50);self.assertEqual(f.cap_q,0)
 def test_price_proxy_uses_same_day_actual_amount_volume(self):
  r={'volume':1000,'turnover':20000,'low':19,'high':21,'close':19};self.assertEqual(price_proxy(r),(20,'market_vwap'));r['turnover']=None;self.assertEqual(price_proxy(r),(None,'unpriced'));self.assertEqual(price_proxy(r,True),(19,'seed_close'))
 def test_bucket_boundaries(self):
  self.assertEqual(bucket(1.299),0);self.assertEqual(bucket(1.3),1)
  for distance,index in [(.07,1),(.13,2),(.22,3),(.221,4)]:self.assertEqual(bucket(1.3/(1-distance)),index)
 def test_sensitivity_strictest_gate(self):
  self.assertAlmostEqual(distribution_distance([.4,.6],[.5,.5]),.1);self.assertEqual(sensitivity_tier(.05,.01),'point');self.assertEqual(sensitivity_tier(.051,.01),'interval');self.assertEqual(sensitivity_tier(.04,.031),'suppress');self.assertEqual(sensitivity_tier(.101,.0),'suppress')
 def test_drift_requires_sixty_consecutive_ratios(self):
  self.assertIsNone(drift([0]*59));self.assertIsNone(drift([None]+[0]*59));self.assertAlmostEqual(drift([0]*40+[.02]*20),.02)
 def row(self,previous=100,balance=100,buy=0,sell=0,price=10,notes=''):
  return dict(symbol='1234',name='sample',previous=previous,balance=balance,buy=buy,sell=sell,repay=0,offset=999,close=price,low=price,high=price,volume=1000,turnover=price*1000,shares=1000000,notes=notes)
 def test_gap_resets_unknown_instead_of_inventing_trades(self):
  data=[(0,'a',[self.row()],600000),(1,'b',None,None),(2,'c',[self.row()],600000)];v=list(reconstruct(data));self.assertTrue(v[1]['source_missing']);self.assertIn('disclosure_or_market_gap',v[2]['securities'][0]['reset_reasons']);self.assertEqual(v[2]['methods']['net_fifo']['principal'],600000)
 def test_previous_revision_resets_entire_remaining_inventory(self):
  v=list(reconstruct([(0,'a',[self.row(0,100,100)],600000),(1,'b',[self.row(90,90)],540000)]));r=v[1]['securities'][0];self.assertIn('previous_balance_difference',r['reset_reasons']);self.assertEqual(r['models']['net_average']['unknown_fraction'],1)
 def test_unsafe_action_reset_and_cash_dividend_no_retirement(self):
  data=[(0,'a',[self.row(0,100,100)],600000),(1,'b',[self.row(price=9)],600000)];normal=list(reconstruct(data));unsafe=list(reconstruct(data,[('b','1234')]));self.assertEqual(normal[1]['methods']['net_fifo']['principal'],600000);self.assertEqual(normal[1]['securities'][0]['reset_reasons'],[]);self.assertIn('unsafe_corporate_event',unsafe[1]['securities'][0]['reset_reasons'])
 def test_same_day_note_not_used_as_current_cap(self):
  v=list(reconstruct([(0,'a',[self.row(0,0,notes='')],1),(1,'b',[self.row(0,100,100,notes='61D')],600000),(2,'c',[self.row(100,110,10)],660000)]));self.assertEqual(v[1]['securities'][0]['models']['net_fifo']['cap_unknown_units'],0);self.assertEqual(v[2]['securities'][0]['models']['net_fifo']['cap_unknown_units'],10)
 def test_missing_price_not_omitted_from_principal(self):
  r=self.row(0,100,100);r.update(close=None,volume=None,turnover=None);v=list(reconstruct([(0,'a',[r],600000)]))[0];self.assertIsNone(v['methods']['net_average']['principal']);self.assertIsNone(v['distribution_D'])
 def test_all_cold_active_names_remain_in_denominator(self):
  v=list(reconstruct([(0,'a',[self.row()],600000)]))[0];self.assertEqual(v['methods']['net_average']['active_names'],1);self.assertEqual(v['methods']['net_average']['warmup_names'],1)
 def test_official_total_never_rescales_model(self):
  a=list(reconstruct([(0,'a',[self.row(0,100,100)],600000)]))[0]
  b=list(reconstruct([(0,'a',[self.row(0,100,100)],6000000)]))[0]
  self.assertEqual(a['methods']['net_average']['principal'],b['methods']['net_average']['principal']);self.assertNotEqual(a['methods']['net_average']['relative_error'],b['methods']['net_average']['relative_error'])
 def test_future_perturbation_cannot_change_prefix(self):
  rows=[(0,'a',[self.row(0,100,100)],600000),(1,'b',[self.row()],600000),(2,'c',[self.row(price=11)],600000)];altered=copy.deepcopy(rows);altered[2][2][0]['close']=999;self.assertEqual(list(reconstruct(rows))[:2],list(reconstruct(altered))[:2])
if __name__=='__main__':unittest.main()
