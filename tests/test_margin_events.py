import unittest,copy,json,gzip,tempfile
from pathlib import Path
from datetime import date,timedelta
from backend import margin_events as e

def row(symbol='2330',q=100,p=10,debt=600000):
    model={'principal':debt,'quantity':q,'unpriced_units':0}
    return {'symbol':symbol,'name':symbol,'market':'TWSE','quantity':q,'close':p,'models':{k:dict(model) for k in e.METHODS}}
def days(n):
    out=[];d=date(2023,1,2)
    while len(out)<n:
        if d.weekday()<5:out.append(d.isoformat())
        d+=timedelta(days=1)
    return out
def width():return {'above':0,'unknown':0,'universe':100,'lower':0.,'upper':0.,'below10':True}
def series(n=50):
    return [{'date':d,'official_loan':10000-i*10,'index_close':100+i,'decline5':.1,'reference':{'threshold':.08,'n':480,'valid':True},'breadth':width(),'watch':False,'eligible':False,'cleaning':None,'divergence':None,'divergence_streak':None} for i,d in enumerate(days(n))]

class EventTests(unittest.TestCase):
    def test_price_only_change_is_not_inventory_effect(self):
        r=e.decomposition([row()],[row(p=9)],{'2330':9},'net_average')
        self.assertAlmostEqual(r['price_effect'],-1/6);self.assertAlmostEqual(r['inventory_effect'],0)
    def test_exit_is_marked_in_previous_inventory_and_identity_holds(self):
        old=[row('2330',1,50,60000),row('2317',1,100,60000)];new=[row('2317',1,110,60000)]
        r=e.decomposition(old,new,{'2330':40,'2317':110},'net_average')
        self.assertAlmostEqual(r['price_effect'],0);self.assertAlmostEqual(r['inventory_effect'],110000/60000-150000/120000)
        self.assertAlmostEqual(r['maintenance']-1.25,r['price_effect']+r['inventory_effect'])
    def test_missing_exit_price_or_principal_propagates(self):
        self.assertIsNone(e.decomposition([row()],[row('2317')],{'2317':10},'net_average')['price_effect'])
        self.assertIsNone(e.decomposition([row(debt=None)],[row()],{'2330':10},'net_average')['inventory_effect'])
    def test_inventory_increase_alone_is_not_cleaning(self):
        ref={'valid':True,'threshold':-.3};self.assertFalse(e.cleaning(.1,-.2,ref,ref))
    def test_quantile_is_fixed_linear_and_ties_need_positive(self):
        self.assertEqual(e.quantile90(list(range(11))),9);self.assertFalse(e.positive_tail(0,{'valid':True,'threshold':0}))
        self.assertTrue(e.positive_tail(.2,{'valid':True,'threshold':.2}))
    def test_reference_excludes_current_and_requires_full_two_years(self):
        d=days(800);v=[1.]*800;v[-1]=999
        r=e.reference(v,d,799);self.assertTrue(r['valid']);self.assertEqual(r['threshold'],1)
        self.assertFalse(e.reference(v,d,300)['valid'])
        v[650]=None;self.assertFalse(e.reference(v,d,799)['valid'])
    def test_two_year_leap_day_is_explicit(self):self.assertEqual(e.two_year_floor('2024-02-29'),'2022-02-28')
    def test_decline_needs_six_consecutive_observations(self):
        self.assertEqual(e.five_day_decline([100,99,98,97,96,80],5),1-80/100)
        self.assertIsNone(e.five_day_decline([100,99,None,97,96,80],5));self.assertIsNone(e.five_day_decline([100],0))
    def test_breadth_keeps_missing_and_IPO_in_denominator(self):
        current={str(i):1 for i in range(100)};history=[{str(i):2 for i in range(95)} for _ in range(19)]
        b=e.breadth(current,history);self.assertEqual(b['universe'],100);self.assertEqual(b['unknown'],5);self.assertEqual(b['upper'],.05);self.assertTrue(b['below10'])
        b=e.breadth(current,[{}]*19);self.assertIsNone(b['below10']);self.assertEqual(b['upper'],1)
    def test_breadth_threshold_is_strict(self):
        current={str(i):1 for i in range(100)};history=[{str(i):2 for i in range(90)} for _ in range(19)]
        self.assertIsNone(e.breadth(current,history)['below10'])
    def test_divergence_requires_all_prior20_and_breadth(self):
        self.assertTrue(e.divergence(90,[100]*20,1.6,[1.5]*20,.2,[.3]*20,width()))
        self.assertFalse(e.divergence(100,[100]*20,1.6,[1.5]*20,.2,[.3]*20,width()))
        self.assertIsNone(e.divergence(90,[100]*19,1.6,[1.5]*20,.2,[.3]*20,width()))
        b=width();b['below10']=None;self.assertIsNone(e.divergence(90,[100]*20,1.6,[1.5]*20,.2,[.3]*20,b))
    def test_methods_must_agree(self):
        self.assertIsNone(e.consensus([True,False]));self.assertIsNone(e.consensus([False,None]));self.assertFalse(e.consensus([False,False]))
    def test_unknown_breaks_streak_and_never_becomes_safe_zero(self):
        self.assertEqual(e.consecutive([True,True,None,True,False]),[1,2,None,1,0])
    def test_adjacent_episodes_only_and_unknown_closure(self):
        r=e.episodes([False,True,True,None,True,False,True]);self.assertEqual([(x['start'],x['end'],x['closure']) for x in r],[(1,2,'unknown'),(4,4,'ended'),(6,6,'ongoing')])
    def test_immature_and_missing_endpoints_are_distinct(self):
        self.assertEqual(e.outcome([100,101],0,5)['status'],'immature')
        self.assertEqual(e.outcome([100,None,102],0,2)['status'],'missing')
        self.assertIsNone(e.outcome([100,None,102],0,2)['return_pct'])
    def test_outcomes_are_per_event_close_paths_with_anchor(self):
        r=e.outcome([100,110,105],0,2);self.assertAlmostEqual(r['return_pct'],5);self.assertEqual(r['minimum_close_return_pct'],0)
        self.assertAlmostEqual(e.outcome([100,80,95],0,2)['minimum_close_return_pct'],-20)
    def test_event_window_is_20_before_first_to20_after_last(self):
        s=series(70);flags=[False]*70;flags[25:28]=[True]*3;r=e.event_cards(s,flags,'TPEX','official_decline5')[0]
        self.assertEqual(r['points'][0]['date'],s[5]['date']);self.assertEqual(r['points'][-1]['date'],s[47]['date']);self.assertEqual(r['pre_days'],20);self.assertEqual(r['post_days'],20)
    def test_episode_id_stays_fixed_when_it_extends(self):
        s=series(30);flags=[False]*28+[True,True]
        a=e.event_cards(s[:29],flags[:29],'TWSE','x')[0];b=e.event_cards(s,flags,'TWSE','x')[0]
        self.assertEqual(a['id'],b['id']);self.assertEqual(b['maturity'],'ongoing');self.assertEqual(b['outcomes'][0]['status'],'immature')
    def test_overlapping_event_windows_are_marked(self):
        s=series(60);flags=[False]*60;flags[25]=True;flags[30]=True;r=e.event_cards(s,flags,'TWSE','x');self.assertTrue(all(x['overlap'] for x in r))
    def test_missing_index_never_substituted_by_other_market(self):
        s=series(50)
        for r in s:r['index_close']=None
        f=[False]*50;f[25]=True;r=e.event_cards(s,f,'TPEX','x')[0]
        self.assertIsNone(r['anchor_close']);self.assertTrue(all(p['index_return_pct'] is None for p in r['points']));self.assertEqual(r['outcomes'][0]['status'],'missing')
    def test_complete_window_requires_pre_period_and_trigger_prices_too(self):
        s=series(70);s[5]['index_close']=None;flags=[False]*70;flags[25]=True
        r=e.event_cards(s,flags,'TPEX','x')[0];self.assertEqual(r['maturity'],'missing');self.assertEqual(r['missing_index_days'],1);self.assertEqual(r['outcomes'][0]['status'],'ready')
    def test_core_cleaning_can_trigger_only_with_qualified_history(self):
        d=days(800);loan=[100.]*800;loan[-1]=80.;models={k:[{'loan':v,'maintenance':1.5,'below130_share':.2,'inventory_effect':.01 if i>=795 else 0} for i,v in enumerate(loan)] for k in e.METHODS}
        r=e.analyze(d,loan,[100.]*800,[width() for _ in d],models,[True]*800)
        self.assertTrue(r[-1]['cleaning'])
        blocked=e.analyze(d,loan,[100.]*800,[width() for _ in d],models,[False]*800);self.assertIsNone(blocked[-1]['cleaning']);self.assertTrue(blocked[-1]['watch'])
        eligible=[True]*800;eligible[-6]=False;interrupted=e.analyze(d,loan,[100.]*800,[width() for _ in d],models,eligible);self.assertIsNone(interrupted[-1]['cleaning'])
    def test_prefix_replay_is_unchanged_by_future_prices_or_debt(self):
        d=days(750);loans=[100.]*750;loans[700:]=[80.]*50;price=[100.]*750;widths=[width() for _ in d]
        full=e.analyze(d,loans,price,widths);short=e.analyze(d[:720],loans[:720],price[:720],widths[:720]);self.assertEqual(full[:720],short)
    def test_primary_counts_unknown_even_with_source_observations(self):
        s=series(50);s[25]['watch']=True;r=e.frame(s,'TWSE');self.assertIsNone(r['primary']['cleaning']['confirmed_count']);self.assertEqual(len(r['observation']['episodes']),1)
    def test_missing_GET_never_creates_or_rebuilds(self):
        with tempfile.TemporaryDirectory() as tmp:self.assertEqual(e.report(store=tmp)['status'],'missing');self.assertEqual(list(Path(tmp).iterdir()),[])
    def test_serving_rejects_unapproved_primary_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=e.frame(series(),'TWSE');f['primary']['cleaning']['confirmed_count']=0
            v={'version':e.VERSION,'model_ready':False,'dates':['d'],'frames':{'d':{'markets':[f]}}};Path(tmp,'events.json.gz').write_bytes(gzip.compress(json.dumps(v).encode()))
            with self.assertRaises(ValueError):e.report(store=tmp)
    def test_serving_rejects_future_observation_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=series();s[25]['watch']=True;f=e.frame(s,'TWSE')
            v={'version':e.VERSION,'model_ready':False,'dates':['2023-02-01'],'frames':{'2023-02-01':{'markets':[f]}}};Path(tmp,'events.json.gz').write_bytes(gzip.compress(json.dumps(v).encode()))
            with self.assertRaises(ValueError):e.report(store=tmp)
    def test_serving_rejects_future_endpoint_even_when_window_points_are_past(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=series();s[40]['watch']=True;f=e.frame(s,'TWSE');f['observation']['episodes'][0]['outcomes'][1].update(status='ready',return_pct=10,minimum_close_return_pct=0)
            dates=[r['date'] for r in s];v={'version':e.VERSION,'model_ready':False,'dates':dates,'frames':{dates[-1]:{'markets':[f]}}};Path(tmp,'events.json.gz').write_bytes(gzip.compress(json.dumps(v).encode()))
            with self.assertRaises(ValueError):e.report(store=tmp)

if __name__=='__main__':unittest.main()
