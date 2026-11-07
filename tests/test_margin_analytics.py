import copy,gzip,json,tempfile,unittest
from pathlib import Path
from backend import margin_analytics as a

def row(symbol='2330',q=100,close=10,loan=600000,fifo=None):
    model={'quantity':q,'principal':loan,'unpriced_units':0}
    return {'symbol':symbol,'name':symbol,'market':'TWSE','quantity':q,'close':close,'models':{'net_average':copy.deepcopy(model),'net_fifo':dict(model,principal=loan if fifo is None else fifo)}}
def grade():
    return {'accepted':True,'source_missing':False,'raw_sensitivity_tier':'point','publication_blockers':[], 'quantitative_gates':{k:{v:True for v in ('total','warmup','drift','price_identified','cap_rule_verified','disclosure_continuity')} for k in a.METHODS}}

class AnalyticsTests(unittest.TestCase):
    def test_nine_prefix_ordinary_stocks_are_not_depositary_receipts(self):
        for s,n in [('9904','寶成'),('9802','鈺齊-KY'),('9958','世紀鋼')]:self.assertEqual(a.classify(s,n,'TWSE'),'listed_stock')
        self.assertEqual(a.classify('9949','琉園','TPEX'),'otc_stock')
        self.assertEqual(a.classify('9103','美德醫療-DR','TWSE'),'other')
    def test_aggregate_is_ratio_of_sums_not_mean_of_ratios(self):
        r=a.metrics([row(),row('2317',q=100,close=20,loan=400000)])
        self.assertEqual(r['loan'],1000000);self.assertEqual(r['maintenance'],3);self.assertAlmostEqual(r['buffer'],1-1.3/3)
    def test_low130_money_share_and_count(self):
        r=a.metrics([row(close=5),row('2317',loan=400000)])
        self.assertEqual(r['below130_amount'],600000);self.assertEqual(r['below130_count'],1);self.assertEqual(r['below130_share'],.6)
    def test_negative_buffer_means_already_below130(self):
        self.assertLess(a.metrics([row(close=5)])['buffer'],0)
    def test_unpriced_principal_is_not_omitted_or_zero(self):
        self.assertIsNone(a.metrics([row(),row('2317',loan=None)])['loan'])
        self.assertIsNone(a.distribution([row(),row('2317',loan=None)]))
    def test_missing_close_preserves_loan_but_blocks_price_metrics(self):
        v=a.metrics([row(close=None)]);self.assertEqual(v['loan'],600000);self.assertIsNone(v['maintenance'])
    def test_cap_includes_unfinanced_names_and_missing_blocks_denominator(self):
        u=[{'close':10,'shares':1000000},{'close':20,'shares':2000000}]
        obs=a.source_observations([row()],u,True);self.assertEqual(obs['market_cap'],50000000)
        self.assertEqual(a.metrics([row()],market_cap=obs['market_cap'])['loan_cap'],.012)
        u[1]['shares']=None;self.assertIsNone(a.source_observations([row()],u,True)['market_cap'])
    def test_missing_market_is_not_zero_financing(self):
        o=a.source_observations([],[],False);self.assertIsNone(o['finance_units']);self.assertIsNone(o['financed_names']);self.assertIsNone(o['financed_market_value'])
    def test_zero_financing_is_distinct_from_missing(self):
        o=a.source_observations([row(q=0,loan=0)],[],True);self.assertEqual(o['finance_units'],0);self.assertIsNone(a.metrics([row(q=0,loan=0)])['maintenance'])
    def test_twenty_day_baseline_never_substituted(self):
        self.assertIsNone(a.metrics([row()])['change20']);self.assertEqual(a.metrics([row()],base20=[row(loan=300000)])['change20'],1)
    def test_distribution_amounts_and_shares_conserve(self):
        v=a.distribution([row(close=5),row('2317',close=20)]);self.assertEqual(sum(x['amount'] for x in v),1200000);self.assertAlmostEqual(sum(x['share'] for x in v),1);self.assertEqual(sum(x['count'] for x in v),2)
    def test_buffer_boundaries_match_protocol(self):
        for i,m in enumerate([1.2,1.3/(1-.07),1.3/(1-.13),1.3/(1-.22),2.]):
            v=a.distribution([row(q=1,close=m*100,loan=100000)])
            self.assertEqual(v[i]['count'],1)
    def test_ETF_has_no_count_and_inverse_is_separate(self):
        self.assertTrue(all(x['count'] is None for x in a.distribution([row()],etf=True)))
        self.assertEqual(a.classify('00631L','元大台灣50正2','TWSE'),'etf_leverage');self.assertEqual(a.classify('00632R','元大台灣50反1','TWSE'),'etf_inverse')
    def test_interval_is_method_range_not_confidence_interval(self):
        rows=[row(q=1,close=1,loan=2000,fifo=4000),row('2317',q=1,close=200,loan=98000,fifo=96000)]
        v=a.compared_distribution(rows);self.assertEqual(v['tier'],'interval');self.assertIsNone(v['bins'][0]['amount']);self.assertEqual(v['bins'][0]['amount_range'],[2000,4000])
    def test_large_method_difference_suppresses(self):
        rows=[row(q=1,close=1,loan=2000,fifo=40000),row('2317',q=1,close=200,loan=98000,fifo=60000)]
        self.assertEqual(a.compared_distribution(rows)['tier'],'suppress');self.assertIsNone(a.compared_distribution(rows)['bins'])
    def test_numeric_pass_cannot_override_acceptance_or_source_gates(self):
        g=grade();g['accepted']=False;v=a.public_basket('listed_stock',[row()],[{'symbol':'2330','market':'TWSE','close':10,'shares':1000000}],{'TWSE':g})
        self.assertEqual(v['status'],'blocked');self.assertTrue(all(x is None for x in v['metrics'].values()));self.assertIsNone(v['distribution']['bins'])
    def test_one_bad_method_blocks(self):
        g=grade();g['quantitative_gates']['net_fifo']['warmup']=False;self.assertTrue(a.gate_reasons(g))
    def test_classification_and_dividend_verification_gates_ETF(self):
        v=a.public_basket('etf_cap',[row('0050')],[{'symbol':'0050','market':'TWSE','close':10,'shares':1e6}],{'TWSE':grade()});self.assertEqual(v['status'],'blocked');self.assertIsNone(v['metrics']['index_buffer'])
    def test_debt_and_increase_rank_are_distinct_and_no_negative_increase(self):
        now=[row(),row('2317',loan=800000)];old=[row(loan=100000),row('2317',loan=900000)]
        v=a.select_subsets(now,old,None,'2026-01-01','TWSE');self.assertEqual(v['debt30'][0]['symbol'],'2317');self.assertEqual([r['symbol'] for r in v['increase30']],['2330'])
    def test_zero_observed_baseline_can_enter_increase_rank(self):
        v=a.select_subsets([row()],[row(q=0,loan=0)],None,'2026-01-01','TWSE');self.assertEqual(v['increase30'][0]['rank_value'],600000)
    def test_missing_baseline_never_inferred_as_zero(self):
        self.assertEqual(a.select_subsets([row()],[],None,'d','TWSE')['increase30'],[])
    def test_RS_uses_exact_date_market_and_financing_intersection(self):
        snap={'as_of':'2026-01-01','groups':{'20':[{'symbol':'2317','market':'TWSE','rs':99},{'symbol':'2330','market':'TWSE','rs':90},{'symbol':'3374','market':'TPEX','rs':100}],'ytd':[{'symbol':'2330','market':'TWSE','excess_pct':5}]}}
        v=a.select_subsets([row()],None,snap,'2026-01-01','TWSE');self.assertEqual([x['symbol'] for x in v['rs30']],['2330']);self.assertEqual(len(v['ytd']),1)
        self.assertIsNone(a.select_subsets([row()],None,snap,'2025-12-31','TWSE')['rs30'])
    def test_RS_missing_group_not_zero_members(self):
        v=a.select_subsets([row()],None,{'as_of':'d','groups':{}},'d','TWSE');self.assertIsNone(v['rs30'])
    def test_fixed_current_cohort_five_day_maintenance_change(self):
        now=[row(),row('2317',close=100)];old=[row(close=8),row('2317',close=500)]
        r=a.cohort_metrics(now,[{'symbol':'2330'}],now,old);self.assertAlmostEqual(r['change5_pp'],(10/6-8/6)*100)
        self.assertIsNone(a.cohort_metrics(now,[{'symbol':'2330'}],now,[])['change5_pp'])
    def test_public_debt_members_hidden_but_RS_observations_can_show(self):
        g=grade();g['accepted']=False;snap={'as_of':'d','groups':{'20':[{'symbol':'2330','market':'TWSE','rs':100}],'ytd':[]}}
        out=a.public_subsets('TWSE','d',[row()],None,None,g,None,None,snap)
        self.assertIsNone(out[0]['members']);self.assertEqual(out[2]['count'],1);self.assertTrue(all(x is None for x in out[2]['metrics'].values()))
    def test_missing_credit_market_does_not_become_empty_RS_intersection(self):
        snap={'as_of':'d','groups':{'20':[{'symbol':'2330','market':'TWSE','rs':100}],'ytd':[]}}
        out=a.public_subsets('TWSE','d',[],None,None,{'source_missing':True},None,None,snap)
        self.assertIsNone(out[2]['members']);self.assertIsNone(out[2]['count']);self.assertIsNone(out[3]['count'])
    def test_GET_does_not_create_artifacts_or_accept_model_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(a.report(store=tmp)['status'],'missing');self.assertEqual(list(Path(tmp).iterdir()),[])
            p=Path(tmp)/'analytics.json.gz';p.write_bytes(gzip.compress(json.dumps({'version':a.VERSION,'model_ready':True}).encode()))
            with self.assertRaises(ValueError):a.report(store=tmp)
    def test_serving_boundary_rejects_unapproved_numeric_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            v={'version':a.VERSION,'model_ready':False,'dates':['d'],'frames':{'d':{'baskets':[{'status':'blocked','metrics':{'loan':1},'distribution':{'bins':None}}],'subsets':{}}}}
            p=Path(tmp)/'analytics.json.gz';p.write_bytes(gzip.compress(json.dumps(v).encode()))
            with self.assertRaises(ValueError):a.report(store=tmp)

if __name__=='__main__':unittest.main()
