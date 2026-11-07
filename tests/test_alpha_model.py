import json
import unittest

from backend import alpha_model, research


class AlphaModelTests(unittest.TestCase):
    def candidate(self,index=0):
        return {'symbol':f'{2000+index}','name':'測試','close':100+index,'reasons':['券商同步買超','RS20 / YTD 雙強'],
                'sectors':['半導體'],'sector_hits':['半導體'],'rs20':70,'rs60':65,'rs_ytd':75,'ytd_pct':20,
                'buy_count':3,'sell_count':1,'net_lots':500,'buyers':[{'name':'甲','net':300}],
                'sellers':[{'name':'乙','net':-50}],'supply_chain':{'upstream':[],'peer':[],'downstream':[]},
                'technical':{'dmi_pass':True,'label':'多方延續','status':'bullish_continuation','plus_di':30,
                             'minus_di':15,'di_spread':15,'adx':25,'bullish_days':5,'sessions_since_cross':4,
                             'sar':{'bullish':True,'label':'SAR多方確認','value':95,'distance_pct':5}}}

    def test_training_has_minimum_and_time_order_validation(self):
        names=('x',)
        warming=alpha_model._fit([{'features':{'x':i},'target':i} for i in range(19)],names,20)
        self.assertEqual(warming['status'],'warming')
        trained=alpha_model._fit([{'features':{'x':i},'target':2*i+1} for i in range(24)],names,20)
        self.assertEqual(trained['status'],'trained')
        self.assertGreater(trained['validation']['samples'],0)
        self.assertAlmostEqual(alpha_model._predict(trained,{'x':12}),25,delta=2)

    def test_prepare_does_not_train_on_future_outcome(self):
        feature=alpha_model.candidate_features(self.candidate())
        def row(day):
            return {'id':day,'eligible':True,'recommendation_rule':{'features':feature},
                    'result':{'status':'closed','exit_date':day,'net_return_pct':2,'alpha_pct':1}}
        evidence={'date':'2026-09-20','candidates':[self.candidate()]}
        model=alpha_model.prepare(evidence,[row('2026-09-19'),row('2026-09-21')],[])
        self.assertEqual(model['stock']['samples'],1)
        self.assertEqual(model['cutoff'],'2026-09-20')

    def test_futures_calibration_preserves_raw_values(self):
        names=list(alpha_model.FUTURES_FEATURES)
        trained={'status':'trained','feature_names':names,'mean':[0]*len(names),'scale':[1]*len(names),
                 'coefficients':[.1]+[0]*len(names),'samples':20}
        evidence={'execution':{'basis':{'open':10000,'high':10100,'low':9900,'close':10000},'opening_markets':{'rows':[]}},
                  'alpha_model':{'futures':{'open':trained,'close':trained}}}
        raw={'support2':9700,'support1':9800,'open':10000,'close':10050,'resistance1':10200,'resistance2':10300}
        result=alpha_model.calibrate_futures(raw,evidence)
        self.assertEqual(result['raw_open'],10000);self.assertEqual(result['raw_close'],10050)
        self.assertEqual(result['open'],10010);self.assertEqual(result['close'],10060)
        self.assertTrue(result['calibration']['applied'])

    def test_market_v4_removes_stock_and_etf_lists(self):
        candidates=[self.candidate(i) for i in range(30)]
        evidence={'date':'2026-09-24','captured_at':'x','news_cutoff':'x','dates':{},'warnings':[],'coverage':{},
                  'generation_policy':{'version':'research-v2'},'technical_policy':{},'candidates':candidates,
                  'sector_inflow':[],'sector_outflow':[],'broker_sells':[],'strategies':{'risk':{},'plan':{},
                  'etfs':[{'symbol':'0050','curve':[{'date':'x','value':1}]}]},'execution':{},
                  'alpha_model':alpha_model.prepare({'date':'2026-09-24','candidates':candidates},[],[]),
                  'box_strategy':{'levels':[]},'method':'test'}
        compact=research.compact_evidence(evidence)
        self.assertNotIn('candidates',compact)
        self.assertNotIn('technical_policy',compact)
        self.assertNotIn('broker_sells',compact)
        self.assertNotIn('box_strategy',compact)
        self.assertNotIn('stock',compact['alpha_model'])
        self.assertNotIn('etfs',compact['strategies'])
        self.assertNotIn('plan',compact['strategies'])
        self.assertLess(len(json.dumps(compact)),len(json.dumps(evidence))*.6)

    def test_prepare_futures_never_builds_stock_model(self):
        result=alpha_model.prepare_futures({'date':'2026-09-24'},[])
        self.assertNotIn('stock',result);self.assertEqual(set(result['futures']),{'open','close'})


if __name__=='__main__':unittest.main()
