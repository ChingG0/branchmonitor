import unittest,json,gzip,tempfile,copy
from pathlib import Path
from backend import margin_checker as c
class CheckerTests(unittest.TestCase):
 def row(self,q=100,principal=600000):
  model={'quantity':q,'principal':principal,'unknown_fraction':.25,'unknown_units':25,'unpriced_units':0,'cap_unknown_units':0}
  return {'date':'2026-01-02','symbol':'2330','name':'台積電','quantity':q,'close':12,'price_proxy':11,'reset_reasons':[],'models':{k:copy.deepcopy(model) for v in c.MODES.values() for k in v}}
 def test_equivalent_price_uses_shares_and_six_tenths(self):
  r=self.row();before=copy.deepcopy(r);v=c.derive(r);self.assertEqual(v['models']['net']['average']['cost'],10);self.assertEqual(v['models']['net']['average']['unknown_pct'],25);self.assertEqual(r,before)
 def test_unpriced_principal_never_draws_partial_cost(self):
  r=self.row(principal=None);v=c.derive(r);self.assertIsNone(v['models']['net']['fifo']['cost'])
  r=self.row();r['models']['net_fifo']['unpriced_units']=1;self.assertIsNone(c.derive(r)['models']['net']['fifo']['cost'])
 def test_no_financing_is_not_zero_cost_or_safe_unknown_ratio(self):
  v=c.derive(self.row(0,0));self.assertIsNone(v['models']['net']['average']['cost']);self.assertIsNone(v['models']['net']['average']['unknown_pct'])
 def test_missing_days_not_filled_or_compressed(self):
  a=self.row();b=copy.deepcopy(a);b['date']='2026-01-06';s=c.assemble(['2026-01-02','2026-01-05','2026-01-06'],{'TWSE':['2330']},{'TWSE':[a,b]})[0];self.assertEqual(s['days'],2);self.assertEqual(s['expected_days'],3);self.assertIsNone(s['points'][1]);self.assertEqual(s['points'][2]['date'],'2026-01-06')
 def test_model_quantity_mismatch_rejected(self):
  r=self.row();r['models']['net_fifo']['quantity']=99
  with self.assertRaises(ValueError):c.derive(r)
 def test_outside_dates_and_duplicate_calendar_rejected(self):
  with self.assertRaises(ValueError):c.assemble(['2026-01-01'],{'TWSE':['2330']},{'TWSE':[self.row()]})
  with self.assertRaises(ValueError):c.assemble(['2026-01-02','2026-01-02'],{}, {})
 def test_reset_labels_preserved_without_loan_or_maintenance_publication(self):
  r=self.row();r['reset_reasons']=['previous_balance_difference'];v=c.derive(r);self.assertIn('前餘',v['reset_labels'][0]);self.assertNotIn('principal',v);self.assertNotIn('maintenance',v)
 def test_no_auto_build_on_missing_artifact(self):
  with tempfile.TemporaryDirectory() as tmp:
   self.assertEqual(c.report(store=tmp)['status'],'missing');self.assertFalse(c.report(store=tmp)['model_ready']);self.assertEqual(list(Path(tmp).iterdir()),[])
 def test_only_fixed_selection_readonly(self):
  with tempfile.TemporaryDirectory() as tmp:
   stocks=c.assemble(['2026-01-02'],{'TWSE':['2330']},{'TWSE':[self.row()]});v={'version':c.VERSION,'model_ready':False,'status':'diagnostic_only','stocks':stocks};p=Path(tmp)/'checker.json.gz';p.write_bytes(gzip.compress(json.dumps(v).encode()));raw=p.read_bytes();r=c.report('2330',store=tmp);self.assertEqual(r['detail']['symbol'],'2330');self.assertFalse(r['model_ready']);self.assertEqual(p.read_bytes(),raw)
   with self.assertRaises(ValueError):c.report('9999',store=tmp)
 def test_artifact_cannot_claim_model_ready(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'checker.json.gz';p.write_bytes(gzip.compress(json.dumps({'version':c.VERSION,'model_ready':True,'stocks':[]}).encode()))
   with self.assertRaises(ValueError):c.report(store=tmp)
if __name__=='__main__':unittest.main()
