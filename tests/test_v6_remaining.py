import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np,pandas as pd
from backend import v6_remaining as R,v6_shared_distribution as D,v6_comparison as C
ROOT=Path(__file__).resolve().parents[1]

def root_at(p,day):
    i=list(p.index).index(day);days=list(p.index[i+1:i+4]);s=D.calculate(p,day,float(p.at[day,'close']))
    s.update(version=D.VERSION,method_version=D.METHOD,market_as_of=day,anchor=float(p.at[day,'close']),forecast_dates=days,timing_qualified=True)
    return {'id':'origin','version':C.VERSION,'market_as_of':day,'prepared_at':days[0]+'T08:35:00+08:00','anchor':s['anchor'],'forecast_dates':days,'shared_distribution':s}

class RemainingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p=D.prices();cls.root=root_at(cls.p,'2026-10-27')

    def test_remaining2_and1_exactly_use_matured_horizon_and_fixed_anchor(self):
        for day,m in [('2026-10-28',2),('2026-10-29',1)]:
            r=R.calculate(self.p,self.root,day);f=self.p.loc[:day];c=f.close.to_numpy();idx=np.arange(252,len(f)-m);ends=c[-1]*c[idx+m]/c[idx]
            a=self.root['anchor'];by={x['key']:x for x in r['classes']}
            self.assertEqual(r['remaining_sessions'],m);self.assertEqual(r['target_date'],'2026-10-30');self.assertEqual(r['anchor'],a)
            self.assertEqual(by['up']['count'],int((ends>a*1.005).sum()));self.assertEqual(by['down']['count'],int((ends<a*.995).sum()))
            self.assertEqual(sum(x['count'] for x in r['classes']),len(idx));self.assertAlmostEqual(sum(x['probability_pct'] for x in r['classes']),100)
            self.assertEqual(r['sample_last'],f.index[-m-1]);self.assertEqual(r['median_level'],float(np.quantile(ends,.5,method='inverted_cdf')))

    def test_future_prices_and_prefix_do_not_change_revision(self):
        day='2026-10-28';r=R.calculate(self.p,self.root,day);future=self.p.copy();future.loc[future.index>day]*=4
        self.assertEqual(r,R.calculate(future,self.root,day));self.assertEqual(r,R.calculate(self.p.loc[:day],self.root,day))

    def test_realized_loss_is_carried_forward_not_reset(self):
        p=self.p.copy();day='2026-10-28';a=self.root['anchor'];p.at[day,'close']=a*.97;p.at[day,'high']=max(float(p.at[day,'high']),a*.97);p.at[day,'low']=min(float(p.at[day,'low']),a*.97)
        r=R.calculate(p,self.root,day);c={x['key']:x['probability'] for x in r['classes']}
        self.assertAlmostEqual(r['realized_return_pct'],-3);self.assertGreater(c['down'],.9);self.assertEqual(r['flat_bounds'],[a*.995,a*1.005])

    def test_already_observed_low_is_not_a_future100percent_forecast(self):
        p=self.p.copy();p.at['2026-10-28','low']=self.root['anchor']*.979
        r=R.calculate(p,self.root,'2026-10-28');self.assertTrue(r['event']['occurred']);self.assertEqual(r['event']['status'],'observed');self.assertIsNone(r['event']['probability'])
        self.assertEqual(r['event']['label'],'回落2%已發生')

    def test_wrong_calendar_or_anchor_refuses_revision(self):
        bad=copy.deepcopy(self.root);bad['shared_distribution']['forecast_dates']=['2026-10-29','2026-10-30','2026-11-02']
        with self.assertRaisesRegex(ValueError,'交易日不一致'):R.calculate(self.p,bad,'2026-10-28')
        bad=copy.deepcopy(self.root);bad['shared_distribution']['anchor']+=1
        with self.assertRaisesRegex(ValueError,'錨點'):R.calculate(self.p,bad,'2026-10-28')

    def test_expired_outcome_uses_original_deadline_and_low(self):
        r=R.calculate(self.p,self.root,'2026-10-30');self.assertEqual(r['status'],'complete');self.assertEqual(r['classes'],[])
        self.assertAlmostEqual(r['actual_return_pct'],(self.p.at['2026-10-30','close']/self.root['anchor']-1)*100)
        self.assertEqual(r['event']['occurred'],bool(self.p.loc['2026-10-28':'2026-10-30','low'].min()<=self.root['anchor']*.98))

    def test_production_publication_retires_unconditional_remaining_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Path(tmp);root=C.publish(self.root,store);path=store/(root['id']+'.json');before=path.read_bytes()
            current=C.publish(root_at(self.p,'2026-10-28'),store)
            self.assertEqual(current['remaining_updates'],[]);self.assertEqual(path.read_bytes(),before)

    def test_old_nearest80_and_pre_activation_are_never_backfilled(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);old={**self.root,'shared_distribution':None};(p/'origin.json').write_text(json.dumps(old))
            cfg={**R.release(),'activated_at':'2026-10-01T00:00:00+08:00'}
            with patch.object(R,'release',return_value=cfg):self.assertEqual(R.prepare(root_at(self.p,'2026-10-28'),p),[])
            with patch.object(R,'release',return_value={**cfg,'activated_at':'2026-11-04T17:00:00+08:00'}):self.assertEqual(R.prepare(root_at(self.p,'2026-10-28'),p),[])

    def test_remaining_event_uses_future_lows_and_excludes_original_day_low(self):
        p=self.p.copy();p.at['2026-10-27','low']=self.root['anchor']*.8
        r=R.calculate(p,self.root,'2026-10-28');self.assertEqual(r['event']['status'],'reference')
        f=p.loc[:'2026-10-28'];c=f.close.to_numpy();lo=f.low.to_numpy();idx=np.arange(252,len(f)-2)
        hit=np.array([c[-1]*lo[j+1:j+3].min()/c[j]<=self.root['anchor']*.98 for j in idx])
        self.assertEqual(r['event']['count'],int(hit.sum()));self.assertEqual(r['event']['probability'],float((hit.sum()+.5)/(len(idx)+1)))

    def test_incomplete_or_future_close_refuses_new_probability(self):
        cfg={**R.release(),'activated_at':'2026-10-01T00:00:00+08:00'}
        with tempfile.TemporaryDirectory() as tmp,patch.object(R,'release',return_value=cfg):
            store=Path(tmp);(store/'origin.json').write_text(json.dumps(self.root))
            for day in ['2026-10-28','2026-10-29']:
                current=root_at(self.p,day);current['id']='future-test';current['prepared_at']='2026-10-28T09:00:00+08:00'
                updates=R.prepare(current,store);self.assertEqual(len(updates),1);self.assertEqual(updates[0]['status'],'unavailable');self.assertEqual(updates[0]['classes'],[]);self.assertIn('尚未完成',updates[0]['note'])

    def test_repeated_same_origin_does_not_inflate_prospective_count(self):
        a=copy.deepcopy(self.root);a['id']='first';b=copy.deepcopy(self.root);b['id']='repeat';b['prepared_at']='2026-10-28T08:40:00+08:00'
        end={'id':'end','version':C.VERSION,'prepared_at':'2026-11-02T08:40:00+08:00','remaining_updates':[]}
        for root in [a,b]:
            end['remaining_updates'].append({'root_id':root['id'],'source_snapshot_id':'end','method_version':R.METHOD,'status':'complete','issued_at':end['prepared_at'],'target_date':'2026-10-30','original_rps':.25,'revision_scores':[]})
        r=R.report(end,[a,b,end]);self.assertEqual(r['summary']['completed'],1);self.assertEqual(r['summary']['completed_records'],2);self.assertEqual(r['summary']['prospective_qualified'],1)

    def test_ordered_error_has_correct_extremes(self):
        correct=[{'key':'down','probability':1},{'key':'flat','probability':0},{'key':'up','probability':0}]
        self.assertEqual(R.rps(correct,'down'),0);self.assertEqual(R.rps(correct,'flat'),.5);self.assertEqual(R.rps(correct,'up'),1)

if __name__=='__main__':unittest.main()
