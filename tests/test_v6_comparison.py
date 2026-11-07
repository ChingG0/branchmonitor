import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np,pandas as pd
from backend import v6_comparison as C

def fixture(n=180):
    rng=np.random.default_rng(51);close=100*np.exp(np.cumsum(rng.normal(0,.015,n)))
    return pd.DataFrame({'close':close,'legacy_score':np.linspace(20,80,n),'legacy_valid':True},index=pd.bdate_range('2025-01-01',periods=n).strftime('%Y-%m-%d'))

class ComparisonTests(unittest.TestCase):
    def test_pressure_mapping_preserves_threshold_without_claiming_probability(self):
        cut=.23426757518123026;p=np.linspace(.001,.999,10000);s=C.pressure_score(p,cut)
        self.assertAlmostEqual(float(C.pressure_score(cut,cut)),65)
        np.testing.assert_array_equal(s>=65,p>=cut)
        self.assertTrue(np.all(np.diff(s)>0));self.assertTrue(np.all((s>0)&(s<100)))

    def test_exclusive_three_classes_boundary_and_probability_sum(self):
        self.assertEqual([C.scenario_class(v) for v in [-.006,-.005,0,.005,.006]],['down','flat','flat','flat','up'])
        f=fixture();r=C.scenarios(f,'legacy_score','legacy_valid')
        self.assertEqual(r['sample_n'],80);self.assertAlmostEqual(sum(a['probability_pct'] for a in r['classes']),100.)
        self.assertEqual(sum(a['count'] for a in r['classes']),80)
        self.assertLessEqual(r['last_outcome'],f.index[-1])

    def test_unfinished_targets_and_future_prices_cannot_change_forecast(self):
        f=fixture();at=f.index[125];a=C.scenarios(f,'legacy_score','legacy_valid',at=at)
        changed=f.copy();changed.loc[changed.index>at,'close']=999999
        self.assertEqual(a,C.scenarios(changed,'legacy_score','legacy_valid',at=at))
        self.assertEqual(a,C.scenarios(f.loc[:at],'legacy_score','legacy_valid'))
        last_allowed=f.index[122];self.assertTrue(all(d<=last_allowed for d in a['analog_dates']))

    def test_zero_class_does_not_invent_a_path_and_missing_never_becomes_zero_risk(self):
        f=fixture();f['close']=100*np.exp(np.arange(len(f))*.01)
        r=C.scenarios(f,'legacy_score','legacy_valid')
        for a in r['classes']:
            if a['key']!='up':self.assertEqual(a['path'],[]);self.assertEqual(a['count'],0)
        f.iloc[-1,f.columns.get_loc('legacy_valid')]=False
        r=C.scenarios(f,'legacy_score','legacy_valid');self.assertEqual(r['classes'],[])
        self.assertEqual(r['status'],'missing')
        self.assertEqual(C.scenarios(f.iloc[:20],'legacy_score','legacy_valid')['status'],'missing')

    def test_audit_uses_third_close_not_intermediate_low(self):
        f=fixture();s={'anchor':100,'forecast_dates':list(f.index[-3:])};f.loc[f.index[-2],'close']=80;f.loc[f.index[-1],'close']=101
        self.assertEqual(C.audit(s,f)['actual_class'],'up')
        self.assertEqual(C.audit(s,f.iloc[:-1])['status'],'pending')

    def test_publication_history_is_immutable_and_reads_never_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            base={'version':C.VERSION,'prepared_at':'2026-10-31T10:00:00+08:00','market_as_of':'2026-10-30','forecast_dates':['2026-11-02','2026-11-03','2026-11-04'],'anchor':100}
            a=C.publish(base,Path(tmp));path=Path(tmp)/(a['id']+'.json');before=path.read_bytes()
            b=C.publish({**base,'prepared_at':'2026-10-31T11:00:00+08:00'},Path(tmp))
            with patch.object(C,'build_frame',side_effect=AssertionError('read attempted computation')),patch.object(C.v6_forecast,'index_frame',return_value=fixture()):
                self.assertEqual(C.report(store=Path(tmp))['snapshot']['id'],b['id'])
                self.assertEqual(C.report(store=Path(tmp),before='2026-10-31T10:30:00+08:00')['snapshot']['id'],a['id'])
                self.assertIsNone(C.report('../missing',store=Path(tmp))['snapshot'])
                self.assertIsNone(C.report(store=Path(tmp),before='2026-10-30T12:00:00+08:00')['snapshot'])
            self.assertEqual(before,path.read_bytes())

if __name__=='__main__':unittest.main()
