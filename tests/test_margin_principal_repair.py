import unittest,copy,json,gzip,tempfile
from pathlib import Path
from unittest.mock import patch
from backend import margin_principal_repair as e,margin_statistics as m,margin_statistics_sources as s

class RepairTests(unittest.TestCase):
 def row(self,previous=0,balance=0,buy=0,notes=''):
  return {'symbol':'1234','name':'sample','previous':previous,'balance':balance,'buy':buy,'sell':previous+buy-balance,'repay':0,'offset':0,'notes':notes,'close':10,'low':10,'high':10,'volume':10000,'turnover':100000,'shares':1000000}
 def test_note_pair_not_eleven_reductions(self):
  r=e.margin_rate('TPEX','11     C');self.assertEqual(r['ratio'],.5);self.assertTrue(r['verified'])
 def test_six_reductions_prohibits_new_borrowing(self):
  r=e.margin_rate('TPEX','61      D');self.assertEqual(r['ratio'],0);self.assertTrue(r['prohibited'])
 def test_short_margin_second_digit_does_not_reduce_financing(self):
  self.assertEqual(e.margin_rate('TPEX','12  C')['ratio'],.5)
 def test_missing_and_ambiguous_notes_never_infer_verified_cap(self):
  for note in [None,'1 C',' 1 C','7X','123C','特殊']:
   self.assertFalse(e.margin_rate('TPEX',note)['verified'])
 def test_TWSE_blank_not_evidence_of_no_reduction(self):self.assertFalse(e.margin_rate('TWSE','')['verified'])
 def test_short_stop_not_long_stop(self):
  self.assertFalse(e.margin_rate('TPEX','X')['prohibited']);self.assertTrue(e.margin_rate('TPEX','O')['prohibited'])
 def test_cap_applies_next_trading_day_only(self):
  rows=[(0,'a',[self.row(notes='')],1),(1,'b',[self.row(0,100,100,notes='11 C')],600000),(2,'c',[self.row(100,110,10)],650000)]
  v=list(e.reconstruct(rows,market='TPEX'));self.assertEqual(v[1]['securities'][0]['models']['net_fifo']['principal'],600000);self.assertEqual(v[2]['securities'][0]['models']['net_fifo']['principal'],650000)
 def test_same_snapshot_comparison_does_not_fit_official_totals(self):
  rows=[(0,'a',[self.row(notes='11 C')],1),(1,'b',[self.row(0,100,100)],500000)]
  fixed=list(e.reconstruct(rows,market='TPEX',cap_mode='fixed60'))[-1];aware=list(e.reconstruct(rows,market='TPEX'))[-1]
  self.assertEqual(fixed['methods']['net_fifo']['principal'],600000);self.assertEqual(aware['methods']['net_fifo']['principal'],500000)
  altered=copy.deepcopy(rows);altered[-1]=(*altered[-1][:3],999999)
  self.assertEqual(list(e.reconstruct(altered,market='TPEX'))[-1]['methods']['net_fifo']['principal'],500000)
 def test_forbidden_reported_buy_not_treated_as_zero_debt(self):
  rows=[(0,'a',[self.row(notes='61 D')],1),(1,'b',[self.row(0,10,10)],1)];r=list(e.reconstruct(rows,market='TPEX'))[-1];self.assertIsNone(r['methods']['net_fifo']['principal'])
 def test_future_note_changes_cannot_rewrite_prefix(self):
  rows=[(0,'a',[self.row(notes='')],1),(1,'b',[self.row(0,10,10,notes='11 C')],60000),(2,'c',[self.row(10,20,10)],110000)];changed=copy.deepcopy(rows);changed[-1][2][0]['notes']='61 D';self.assertEqual(list(e.reconstruct(rows))[:2],list(e.reconstruct(changed))[:2])
 def test_six_digit_DR_retained_and_warrant_excluded(self):
  fields=['證券代號','證券名稱','收盤價','開盤價','最高價','最低價','成交股數','成交金額']
  data={'stat':'OK','date':'20261104','tables':[{'title':'每日收盤行情','fields':fields,'data':[['912000','晨訊科-DR','2.43','2.47','2.47','2.43','212100','517875'],['123456','購權證','1','1','1','1','1000','1000']]}]}
  r=m.parse_quotes(data,'TWSE','2026-11-04');self.assertEqual([x[0] for x in r],['912000']);self.assertEqual(r[0][2],2.43)
 def test_canonical_credit_route(self):self.assertIn('/rwd/zh/marginTrading/MI_MARGN',s.urls('2026-10-30')[0][2])
 def test_non_json_response_pauses_host_with_clear_error(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(m,'folder',return_value=Path(tmp)),patch.object(s.db,'DATA',Path(tmp)),patch.object(s.requests,'get') as get:
   s._BLOCKED.clear();get.return_value.status_code=200;get.return_value.content=b'<html>maintenance</html>';get.return_value.headers={'Content-Type':'text/html'}
   try:
    with self.assertRaises(s.SourcePaused):s.fetch('https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?response=json',lambda v:None)
    with self.assertRaises(s.SourcePaused):s.fetch('https://www.twse.com.tw/other',lambda v:None)
    self.assertEqual(get.call_count,1)
    records=list((Path(tmp)/'source-errors').glob('*.json'));self.assertEqual(len(records),1)
    evidence=json.loads(records[0].read_text());self.assertEqual(evidence['status'],200);self.assertEqual(evidence['content_type'],'text/html');self.assertEqual(gzip.decompress((Path(tmp)/'source-errors'/evidence['raw_file']).read_bytes()),b'<html>maintenance</html>')
   finally:s._BLOCKED.clear()


class ContinuityTests(unittest.TestCase):
 row=RepairTests.row
 def test_price_jump_is_not_a_loan_cash_flow(self):
  first=self.row(0,100,100);later=self.row(100,100);later.update(close=30,low=30,high=30,turnover=300000)
  for key in ['net_average','net_fifo','replacement_average','replacement_fifo']:
   r=list(e.reconstruct([(0,'a',[first],1),(1,'b',[later],1)]))[-1]
   self.assertEqual(r['securities'][0]['models'][key]['principal'],600000)
   self.assertEqual(r['securities'][0]['models'][key]['unknown_units'],100)
 def test_unexplained_extra_units_have_no_invented_cost(self):
  r=list(e.reconstruct([(0,'a',[self.row(0,100,100)],1),(1,'b',[self.row(110,110)],1)]))[-1]
  for x in r['securities'][0]['models'].values():
   self.assertIsNone(x['principal']);self.assertAlmostEqual(x['unknown_principal_proxy'],600000);self.assertEqual(x['unpriced_units'],10)
 def test_revised_lower_balance_does_not_reprice_remaining_debt(self):
  first=self.row(0,100,100);later=self.row(90,90);later.update(close=12,low=12,high=12,turnover=120000)
  r=list(e.reconstruct([(0,'a',[first],1),(1,'b',[later],1)]))[-1]
  for x in r['securities'][0]['models'].values():self.assertAlmostEqual(x['principal'],540000);self.assertEqual(x['unknown_units'],90)
 def test_gap_preserves_proxy_but_not_identification(self):
  r=list(e.reconstruct([(0,'a',[self.row(0,100,100)],1),(1,'b',None,None),(2,'c',[self.row(100,100)],1)]))[-1]
  self.assertEqual(r['securities'][0]['models']['net_average']['principal'],600000);self.assertEqual(r['methods']['net_average']['warmup_names'],1)
 def test_unreported_inventory_cannot_be_full_market_total(self):
  r=list(e.reconstruct([(0,'a',[self.row(0,100,100)],1),(1,'b',[],1)]))[-1]
  self.assertIsNone(r['methods']['net_average']['principal']);self.assertIsNone(r['distribution_D'])
 def test_missing_mark_cannot_produce_valid_distribution_distance(self):
  row=self.row(0,100,100);row['close']=None
  r=list(e.reconstruct([(0,'a',[row],1)]))[-1]
  self.assertIsNone(r['distribution_D']);self.assertEqual(r['raw_sensitivity_tier'],'unverified')
 def test_forbidden_residual_unpriced_is_not_a_new_borrowing_conflict(self):
  a=self.row(10,10,notes='61 D');a.update(close=None,turnover=None,volume=None)
  r=list(e.reconstruct([(0,'a',[a],1),(1,'b',[self.row(10,10)],1)]))[-1]
  self.assertGreater(r['methods']['net_average']['unpriced_units'],0)
  self.assertEqual(r['securities'][0]['models']['net_average']['prohibited_added_units'],0)
 def test_quarantine_fifo_and_average_removal_conserves_unknown_units(self):
  for method in ['average','fifo']:
   b=e.Book(method);b.add(10,100);b.add(5,None);b.quarantine();b.remove(8);x=b.summary()
   self.assertAlmostEqual(x['quantity'],7);self.assertAlmostEqual(x['unknown_units'],7);self.assertGreaterEqual(x['unpriced_units'],0)
 def test_unknown_share_uses_same_priced_components_in_both_sides(self):
  r=list(e.reconstruct([(0,'a',[self.row(0,100,100)],1),(1,'b',[self.row(110,110)],1)]))[-1]
  x=r['methods']['net_average'];self.assertEqual(x['unknown_principal_proxy_share'],1.);self.assertIsNone(x['principal'])
 def test_same_public_total_can_hide_different_below130_debt(self):
  # Same shares, marks and official total; a cash-only principal repayment
  # can belong to either name. An aggregate fit cannot identify the split.
  marks=[1000,2000];a=[600,900];b=[900,600]
  def danger(debts):return sum(d for m,d in zip(marks,debts) if e.bucket(m/d)==0)
  self.assertEqual(sum(a),sum(b));self.assertEqual(danger(a),0);self.assertEqual(danger(b),900)

class GateTests(unittest.TestCase):
 def sample(self):
  methods={k:{'relative_error':0.,'warmup_names':0,'unpriced_units':0.,'cap_unknown_names':0,'unreported_inventory_names':0,'scenarios':{'1.0':{'shares':[0,0,0,0,1,0]}}} for k in ['net_average','net_fifo']}
  return {'methods':methods,'raw_sensitivity_tier':'point'}, {k:[0.]*59 for k in methods}
 def test_fixed_gates_can_pass_when_evidence_really_passes(self):
  from backend.margin_principal_validation import grade
  day,h=self.sample();self.assertTrue(grade(day,h,source_checks={'corporate_action_continuity_verified':True})['accepted'])
 def test_missing_sources_and_incomplete_history_do_not_pass(self):
  from backend.margin_principal_validation import grade
  for checks in [{},{'corporate_action_continuity_verified':False},{'corporate_action_continuity_verified':None}]:
   day,h=self.sample();self.assertFalse(grade(day,h,source_checks=checks)['accepted'])
  day,h=self.sample();h['net_fifo'][20]=None;self.assertFalse(grade(day,h,source_checks={'verified':True})['accepted'])
 def test_unsafe_numeric_gates_fail_individually(self):
  from backend.margin_principal_validation import grade
  for field,value in [('relative_error',.031),('warmup_names',1),('cap_unknown_names',1),('unreported_inventory_names',1),('unpriced_units',1)]:
   day,h=self.sample();day['methods']['net_fifo'][field]=value;self.assertFalse(grade(day,h,source_checks={'verified':True})['accepted'],field)
 def test_latest_repair_read_without_overwriting_original_diagnostic(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);old={'version':'margin-principal-diagnostic-1','model_ready':False,'status':'not_accepted'}
   (root/'principal-validation.json').write_text(json.dumps(old));new=dict(old,version=e.VERSION)
   (root/'principal-validation-repair.json').write_text(json.dumps(new));self.assertEqual(m.principal_validation(root)['version'],e.VERSION)
   self.assertEqual(json.loads((root/'principal-validation.json').read_text()),old)
   (root/'principal-validation-repair.json').write_text('invalid JSON');self.assertIsNone(m.principal_validation(root))
 def test_reader_supports_repair_but_cannot_enable_risk_model(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'principal-validation.json';p.write_text(json.dumps({'version':e.VERSION,'model_ready':False,'status':'not_accepted'}));self.assertIsNotNone(m.principal_validation(tmp))
   p.write_text(json.dumps({'version':e.VERSION,'model_ready':True,'status':'accepted'}));self.assertIsNone(m.principal_validation(tmp))

if __name__=='__main__':unittest.main()
