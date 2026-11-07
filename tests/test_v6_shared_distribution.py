import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np,pandas as pd
from backend import v6_shared_distribution as D,v6_comparison as C
ROOT=Path(__file__).resolve().parents[1]

class SharedDistributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prices=D.prices();cls.replay=pd.read_csv(ROOT/'research/v6_distribution_20261104/results/terminal-test-forecasts.csv',float_precision='round_trip')
        cls.events=pd.read_csv(ROOT/'research/v6_distribution_20261104/results/event-test-forecasts.csv',float_precision='round_trip')

    def test_all318_origins_reproduce_frozen_T1_E1(self):
        t=self.replay[(self.replay.vintage=='primary')&(self.replay.model=='T1')]
        e=self.events[(self.events.vintage=='primary')&(self.events.model=='E1')].set_index('signal_date')
        for r in t.itertuples():
            a=D.calculate(self.prices,r.signal_date,float(self.prices.at[r.signal_date,'close']))
            cs={c['key']:c for c in a['classes']}
            for k in ['up','flat','down']:self.assertAlmostEqual(cs[k]['probability'],getattr(r,'p_'+k),places=14)
            self.assertAlmostEqual(a['event_reference']['probability'],e.at[r.signal_date,'p_event'],places=14)
            self.assertAlmostEqual(sum(c['probability_pct'] for c in a['classes']),100,places=10)
            self.assertLessEqual(a['last_outcome'],r.signal_date)
            q=a['intervals'][-1]
            self.assertAlmostEqual(q['low_return_pct']/100,r.q025,places=14)
            self.assertAlmostEqual(q['high_return_pct']/100,r.q975,places=14)

    def test_future_prices_and_prefix_cannot_change_distribution(self):
        day='2026-10-22';anchor=float(self.prices.at[day,'close']);a=D.calculate(self.prices,day,anchor)
        changed=self.prices.copy();changed.loc[changed.index>day]*=2
        self.assertEqual(a,D.calculate(changed,day,anchor));self.assertEqual(a,D.calculate(self.prices.loc[:day],day,anchor))
        with self.assertRaisesRegex(ValueError,'未對齊'):D.calculate(self.prices,day,anchor+1)

    def test_US_holiday_calendar_not_missing_quotes(self):
        days=D._us_sessions(pd.Timestamp('2026-11-27T08:30:00+08:00'))
        self.assertEqual(days[-1],'2026-11-25')
        with self.assertRaisesRegex(ValueError,'年份'):D._us_sessions(pd.Timestamp('2028-03-03T08:30:00+08:00'))

    def test_manual_freeze_gates_missing_sources_and_preserves_actual_time(self):
        f=pd.read_csv(ROOT/'research/v6_audit_20261104/results/current-feature-snapshot.csv',index_col=0).loc[:'2026-11-03']
        days=['2026-11-04','2026-11-05','2026-11-06'];release={**D.release(),'activated_at':'2026-11-03T00:00:00+08:00'}
        with patch.object(D,'release',return_value=release),patch.object(D,'source_gate',return_value=([],['foreign'])),patch.object(D,'calculate',side_effect=AssertionError('missing source produced probability')):
            a=D.freeze(f,days,'2026-11-03T21:00:00+08:00',data_dir=Path('/nonexistent/v6-test-cache'),sources=Path('/private/tmp'))
            self.assertEqual(a['status'],'unavailable');self.assertEqual(a['classes'],[]);self.assertIn('foreign',a['note'])
        with patch.object(D,'release',return_value=release),patch.object(D,'source_gate',return_value=([],[])):
            a=D.freeze(f,days,'2026-11-03T21:00:00+08:00',data_dir=Path('/nonexistent/v6-test-cache'),sources=Path('/private/tmp'))
            self.assertEqual(a['status'],'reference');self.assertEqual(a['issued_at'],'2026-11-03T21:00:00+08:00');self.assertFalse(a['timing_qualified'])
            self.assertEqual(a['information_cutoff'],a['issued_at']);self.assertEqual(len(a['classes']),3)
            b=D.freeze(f,days,'2026-11-04T08:35:00+08:00',data_dir=Path('/nonexistent/v6-test-cache'),sources=Path('/private/tmp'))
            self.assertTrue(b['timing_qualified']);self.assertEqual(b['information_cutoff'],'2026-11-04T08:30:00+08:00')
            self.assertEqual(b['official_price_sha256'],release['base_sha256']);self.assertEqual(b['protocol_sha256'],release['protocol_sha256'])
            self.assertEqual(D.freeze(f,days,'2026-11-04T08:45:00+08:00',data_dir=Path('/nonexistent/v6-test-cache'),sources=Path('/private/tmp'))['status'],'unavailable')

    def test_reads_never_refresh_calculate_or_migrate_old_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Path(tmp);old={'id':'old','version':C.VERSION,'prepared_at':'2026-11-03T19:00:00+08:00','market_as_of':'2026-11-03','forecast_dates':['2026-11-04','2026-11-05','2026-11-06'],'anchor':100}
            path=store/'old.json';path.write_text(json.dumps(old));before=path.read_bytes()
            with patch.object(D,'refresh_prices',side_effect=AssertionError('read fetched')),patch.object(D,'calculate',side_effect=AssertionError('read calculated')),patch.object(C.v6_forecast,'index_frame',return_value=pd.DataFrame({'close':[100]},index=['2026-11-03'])):
                result=C.report(store=store);self.assertEqual(result['snapshot'],old);self.assertTrue(result['unconditional_forecast_retired']);self.assertNotIn('distribution_method',result)
            self.assertEqual(before,path.read_bytes());self.assertEqual(len(list(store.iterdir())),1)

    def test_actual_source_gate_uses_Taiwan_sessions_not_uniform4days(self):
        f=pd.read_csv(ROOT/'research/v6_audit_20261104/results/current-feature-snapshot.csv',index_col=0).loc[:'2026-10-22']
        sources=ROOT/'research/v6_audit_20261104/inputs/live_sources'
        checks,missing=D.source_gate(f,pd.Timestamp('2026-10-27T08:30:00+08:00'),self.prices,sources)
        self.assertEqual(missing,[])
        self.assertTrue(next(c for c in checks if c['key']=='foreign')['ready'])
        f.loc[f.index[-1],'foreign_available']='2026-10-27T09:00:00+08:00'
        _,missing=D.source_gate(f,pd.Timestamp('2026-10-27T08:30:00+08:00'),self.prices,sources)
        self.assertIn('foreign',missing)

    def test_price_refresh_does_not_modify_frozen_base_or_swallow_revisions(self):
        base=(D.CONFIG/'official-base.csv').read_bytes();old=self.prices.loc[:'2026-11-03'].tail(2).copy()
        payload={'stat':'OK','data':[['115/11/02',1,2,1,2],['115/11/03',1,2,1,2],['115/11/04',1,2,1,2]]}
        class Response:
            url='https://www.twse.com.tw/indicesReport/MI_5MINS_HIST';content=json.dumps(payload).encode()
            def raise_for_status(self):pass
            def json(self):return payload
        with tempfile.TemporaryDirectory() as tmp,patch('requests.get',return_value=Response()),patch.object(D,'prices',return_value=old):
            with self.assertRaisesRegex(ValueError,'修訂'):D.refresh_prices('2026-11-04',Path(tmp))
            self.assertFalse((Path(tmp)/'v6_distribution_prices').exists())
        self.assertEqual(base,(D.CONFIG/'official-base.csv').read_bytes())

    def test_successful_official_append_is_atomic_and_replayable(self):
        old=self.prices.loc[:'2026-10-28'].tail(2).copy()
        rows=[]
        for day,r in old.iterrows():
            y,m,d=map(int,day.split('-'));rows.append([f'{y-1911}/{m:02d}/{d:02d}',*r.tolist()])
        rows.append(['115/10/29',46000,47000,45000,46500])
        payload={'stat':'OK','data':rows}
        class Response:
            url='https://www.twse.com.tw/indicesReport/MI_5MINS_HIST';content=json.dumps(payload).encode()
            def raise_for_status(self):pass
            def json(self):return payload
        with tempfile.TemporaryDirectory() as tmp,patch('requests.get',return_value=Response()),patch.object(D,'prices',return_value=old):
            result=D.refresh_prices('2026-10-29',Path(tmp));self.assertEqual(result['added'],1)
            cache=Path(tmp)/'v6_distribution_prices';before={p.name:p.read_bytes() for p in cache.iterdir()}
            saved=pd.read_csv(cache/'prices.csv',index_col='date',float_precision='round_trip')
            self.assertEqual(saved.at['2026-10-29','close'],46500)
            with patch.object(D,'prices',return_value=saved):
                with self.assertRaisesRegex(ValueError,'尚未到'):D.refresh_prices('2026-10-30',Path(tmp))
            self.assertEqual(before,{p.name:p.read_bytes() for p in cache.iterdir()})
            self.assertFalse(cache.with_name(cache.name+'.previous').exists())
            self.assertEqual(list(Path(tmp).glob('v6-official-stage-*')),[])

if __name__=='__main__':unittest.main()
