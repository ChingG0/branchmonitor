import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from backend import v6_forecast as v


def frame(n=180,start='2026-01-01'):
    rng=np.random.default_rng(6)
    close=100*np.exp(np.cumsum(rng.normal(.0001,.013,n)))
    return pd.DataFrame(dict(open=close,high=close*1.015,low=close*.985,close=close),index=pd.bdate_range(start,periods=n).strftime('%Y-%m-%d'))


class ForecastTests(unittest.TestCase):
    def test_path_loss_counts_even_after_rebound_and_excludes_signal_low(self):
        f=frame();f.iloc[-6:,f.columns.get_loc('close')]=100
        f.iloc[-6:,f.columns.get_loc('low')]=99
        f.iloc[-6,f.columns.get_loc('low')]=70
        f.iloc[-4,f.columns.get_loc('low')]=95
        t=v.targets(v.features(f),v.TARGETS[2])
        self.assertEqual(t.y.iloc[-6],1)
        self.assertAlmostEqual(t.minimum_return.iloc[-6],-.05)
        f.iloc[-4,f.columns.get_loc('low')]=99
        self.assertEqual(v.targets(v.features(f),v.TARGETS[2]).y.iloc[-6],0)
        self.assertTrue(t.y.iloc[-5:].isna().all())

    def test_extension_uses_fixed_known_peak(self):
        f=frame();f.iloc[-11:]=[94,100,93,94]
        f.iloc[-10:]=[95,200,91,95]  # future higher highs cannot move the barrier
        t=v.targets(v.features(f),v.TARGETS[4]);row=t.iloc[-11]
        # Set an explicit known 20-day high to avoid random preceding highs.
        f.iloc[-31:-10]=[94,100,93,94]
        t=v.targets(v.features(f),v.TARGETS[4]);row=t.iloc[-11]
        self.assertAlmostEqual(row.barrier,90)
        self.assertEqual(row.y,0)
        f.iloc[-3,f.columns.get_loc('low')]=89
        self.assertEqual(v.targets(v.features(f),v.TARGETS[4]).y.iloc[-11],1)

    def test_features_and_change_detector_are_causal(self):
        f=frame();a=v.features(f.iloc[:140]);b=v.features(f)
        np.testing.assert_allclose(a[v.CHANGE],b.loc[a.index,v.CHANGE],equal_nan=True)
        self.assertTrue(b.cp_recent.dropna().between(0,1).all())

    def test_future_macro_publication_is_rejected(self):
        f=v.features(frame())
        for col in v.MACRO:
            if col not in f:f[col]=1.
        f['decision_at']='2026-10-20T08:30:00+08:00'
        for key in ['vix','spx','tsm','twd','oil','rates-proxy','financial','electronics']:
            f[key+'_available']='2026-10-20T08:00:00+08:00'
        self.assertTrue(v.availability(f,v.MACRO).iloc[-1])
        f.loc[f.index[-1],'spx_available']='2026-10-20T09:00:00+08:00'
        self.assertFalse(v.availability(f,v.MACRO).iloc[-1])

    def test_calibration_requires_both_classes_and_preserves_order(self):
        self.assertIsNone(v.calibrate_fit(np.full(150,.2),np.zeros(150)))
        p=np.linspace(.02,.98,300);y=(np.arange(300)%3==0).astype(float)
        cal=v.calibrate_fit(p,y);q=v.calibrated(p,cal)
        self.assertTrue(np.all(np.diff(q)>=0));self.assertTrue(np.all((q>0)&(q<1)))

    def test_walk_forward_purges_overlapping_targets(self):
        f=v.features(frame(1700,'2017-01-02'))
        r=v.evaluate(f,v.TARGETS[0],v.CORE)
        self.assertGreater(len(r['folds']),0)
        for fold in r['folds']:
            self.assertLess(fold['train_through'],f"{fold['test_year']-1}-01-01")
            self.assertLess(fold['calibration_through'],f"{fold['test_year']}-01-01")

    def test_event_count_deduplicates_and_late_alarm_is_not_early(self):
        rows=[dict(date=f'2026-01-{i+1:02}',i=i,lead=3,y=1,p=.8,alarm=.6) for i in range(8)]
        r=v.episode_metrics(rows,5)
        self.assertEqual(r['event_windows'],1);self.assertEqual(r['alerts'],2)
        rows[0]['p']=.1;rows[1]['p']=.1;rows[2]['p']=.1;rows[3]['p']=.1
        r=v.episode_metrics(rows,5);self.assertEqual(r['missed_windows'],1)

    def artifact(self,f):
        fit=dict(model=v.fit_logit(v.features(f)[v.CORE].dropna(),np.arange(len(v.features(f)[v.CORE].dropna()))%2),
                 calibration=[0,1],columns=v.CORE,baseline=.4)
        return dict(version=v.VERSION,data_through='2025-12-31',created_at='2026-01-01T00:00:00+08:00',
            targets=[dict(**v.TARGETS[0],candidates={'core':dict(current_fit=fit,metrics={'brier_skill_pct':2,'events':100,'skill_range_95':[-1,4]})})])

    def test_unvalidated_probabilities_suppressed_and_inputs_not_mutated(self):
        f=frame();day=f.index[-1];a=self.artifact(f);before=copy.deepcopy(a)
        with tempfile.TemporaryDirectory() as tmp:
            r=v.freeze(day,day+'T21:00:00+08:00',artifact=a,frame=f,data_dir=Path(tmp))
        self.assertIsNone(r['targets'][0]['probability_pct']);self.assertEqual(a,before)

    def test_forecast_cannot_be_backfilled_or_publish_after_next_open(self):
        f=frame();a=self.artifact(f);a['data_through']=f.index[-1]
        r=v.freeze(f.index[-1],f.index[-1]+'T21:00:00+08:00',artifact=a,frame=f)
        self.assertEqual(r['status'],'unavailable')
        a['data_through']='2025-12-31'
        stamp=(pd.Timestamp(f.index[-1])+pd.offsets.BDay(1)).strftime('%Y-%m-%d')+'T10:00:00+08:00'
        self.assertEqual(v.freeze(f.index[-1],stamp,artifact=a,frame=f)['status'],'unavailable')

    def test_audit_waits_for_full_horizon_and_preserves_forecast(self):
        f=frame(start='2024-01-01');d=f.index[-4]
        row={**v.TARGETS[2], 'probability_pct':30.,'barrier':None}
        snap={'forecast':dict(as_of=d,anchor_close=float(f.at[d,'close']),targets=[row])};before=copy.deepcopy(snap)
        self.assertEqual(v.audit(snap,f)['targets'][0]['status'],'pending')
        extra=frame(4,start=(pd.Timestamp(f.index[-1])+pd.offsets.BDay(1)).strftime('%Y-%m-%d'))
        f=pd.concat([f,extra]).sort_index();r=v.audit(snap,f)
        self.assertEqual(r['targets'][0]['status'],'complete');self.assertEqual(snap,before)
        f.at[d,'close']*=1.1
        self.assertEqual(v.audit(snap,f)['targets'][0]['status'],'revised')


class ForecastScorecardTests(unittest.TestCase):
    def test_revisions_do_not_replace_first_forecast(self):
        f=frame(start='2024-01-01');d=f.index[-8]
        def snap(time,p):
            return dict(prepared_at=time,forecast=dict(version=v.VERSION,as_of=d,family='core',anchor_close=float(f.at[d,'close']),targets=[{**v.TARGETS[0],'probability_pct':p,'barrier':None}]))
        r=v.scorecard([snap('2024-09-01T18:00:00+08:00',0),snap('2024-09-01T19:00:00+08:00',100)],f)
        self.assertEqual(r['saved'],2);self.assertEqual(r['rows'][0]['completed'],1)
        self.assertEqual(r['rows'][0]['mean_probability_pct'],0)

class ForecastCalendarTests(unittest.TestCase):
    def test_missing_session_does_not_shift_the_evaluation_date(self):
        f=frame(start='2024-01-01');d=f.index[-8]
        snap={'forecast':dict(as_of=d,anchor_close=float(f.at[d,'close']),targets=[{**v.TARGETS[2],'probability_pct':20.,'barrier':None}])}
        missing=f.drop(f.index[-6])
        row=v.audit(snap,missing)['targets'][0]
        self.assertEqual(row['status'],'missing')

    def test_official_holiday_is_not_an_extra_trading_day(self):
        import sqlite3,json
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            with sqlite3.connect(p/'research.sqlite3') as c:
                c.execute('CREATE TABLE bt_calendars(year INTEGER,payload TEXT)')
                c.execute('INSERT INTO bt_calendars VALUES (?,?)',(2026,json.dumps({'closed':['2026-10-09'],'opened':[]})))
            self.assertEqual(v.session_dates('2026-10-08',1,p),['2026-10-12'])

class ForecastPipelineTests(unittest.TestCase):
    def test_page_read_never_publishes_or_retrains(self):
        from backend import v6_weather
        from datetime import datetime
        from zoneinfo import ZoneInfo
        import hashlib
        f=frame(start='2024-01-01');day=f.index[-8]
        forecast=dict(version=v.VERSION,as_of=day,family='core',anchor_close=float(f.at[day,'close']),targets=[{**v.TARGETS[0],'probability_pct':40.,'barrier':None}])
        draft=dict(market_as_of=day,stage='形成',summary='驗證預報保存。',drivers=[],watch_next=[],weakening=[],news=[],indicators=[dict(category='valuation',label='測試',value=1,unit='%',as_of=day,source='交易所',url='https://www.twse.com.tw/',reading='測試保存')])
        with tempfile.TemporaryDirectory() as tmp:
            store=Path(tmp)
            with patch.object(v,'freeze',return_value=forecast):
                saved=v6_weather.publish(draft,store=store,model={},local_indicators=[],now=datetime(2024,9,1,18,tzinfo=ZoneInfo('Asia/Taipei')))
            path=store/(saved['id']+'.json');before=hashlib.sha256(path.read_bytes()).hexdigest()
            with patch.object(v,'freeze',side_effect=AssertionError('read must not publish')),patch.object(v,'research',side_effect=AssertionError('read must not train')),patch.object(v,'index_frame',return_value=f):
                result=v6_weather.report(store=store)
            self.assertEqual(result['forecast_scorecard']['saved'],1)
            self.assertEqual(result['forecast_audit']['targets'][0]['status'],'complete')
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)


class ForecastPublishTests(unittest.TestCase):
    def test_valid_forecast_keeps_anchor_model_and_probability(self):
        f=frame(start='2024-01-01');day=f.index[-1]
        fit=dict(model={'median':[0.]*len(v.CORE),'sd':[1.]*len(v.CORE),'beta':[0.]*(len(v.CORE)+1)},
                 calibration=[0,1],columns=v.CORE,baseline=.4)
        artifact=dict(version=v.VERSION,created_at='2024-01-01T00:00:00+08:00',data_through='2023-12-31',targets=[dict(**v.TARGETS[0],candidates={'core':dict(current_fit=fit,metrics={'brier_skill_pct':3,'events':100,'skill_range_95':[1,5]})})])
        with tempfile.TemporaryDirectory() as tmp:
            out=v.freeze(day,day+'T20:00:00+08:00',artifact=artifact,frame=f,data_dir=Path(tmp))
        self.assertEqual(out['targets'][0]['probability_pct'],50.)
        self.assertEqual(out['anchor_close'],float(f.close.iloc[-1]))
        self.assertEqual(len(out['model_hash']),64)
        self.assertEqual(out['family'],'core')


if __name__=='__main__':unittest.main()
