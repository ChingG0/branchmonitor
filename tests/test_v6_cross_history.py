import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np,pandas as pd
from backend import v6_cross_history as H,v6_comparison as C
from backend.v6_scenario_views import scenario_views,market_view

def frame():
    rows=[]
    for cash,decision in [('2026-09-30','2026-10-01'),('2026-10-01','2026-10-02'),('2026-10-30','2026-11-02')]:
        r={'decision_at':decision+'T08:30:00+08:00','close':100.,'high':103.,'low':97.,'ma5':102.,'cross_valid':True,'cross_score':72.,'spy_r5':-.01,'tlt_r5':.02,'gold_r5':-.02,'dollar_r5':.01,'tsmc_gap5':-.03}
        for k in ('foreign','retail','twd','spx','tsm','sox','spy','tlt','gold','dollar'):r[k+'_available']=pd.Timestamp(cash+'T20:00:00+08:00')
        rows.append(r)
    return pd.DataFrame(rows,index=['2026-09-30','2026-10-01','2026-10-30'])

class CrossHistoryTests(unittest.TestCase):
    def test_reconstruction_uses_decision_day_and_excludes_future_decisions(self):
        f=frame();r=H.reconstruct(f,start='2026-10-01',end='2026-11-02',generated_at='2026-10-31T12:00:00+08:00')
        self.assertEqual([p['date'] for p in r['points']],['2026-10-01','2026-10-02'])
        self.assertEqual(r['points'][0]['market_as_of'],'2026-09-30')
        self.assertEqual(r['points'][0]['score'],72.)
        self.assertEqual(r['strategy_key'],'v6-2');self.assertIn('非當時預報',r['note'])
        self.assertTrue(all(pd.Timestamp(i['available_at'])<=pd.Timestamp(p['decision_at']) for p in r['points'] for i in p['indicators'] if i.get('available_at')))

    def test_future_or_missing_publication_suppresses_score_and_indicator(self):
        f=frame();f.loc['2026-09-30','spy_available']=pd.Timestamp('2026-10-01T09:00:00+08:00')
        r=H.reconstruct(f,start='2026-10-01',end='2026-10-02',generated_at='2026-10-31T12:00:00+08:00');p=r['points'][0]
        self.assertIsNone(p['score']);self.assertIn('spy',p['missing_sources'])
        self.assertNotIn('美股 SPY 近5日',[i['label'] for i in p['indicators']])
        f.loc['2026-09-30','spy_available']=pd.NaT
        self.assertIsNone(H.reconstruct(f,start='2026-10-01',end='2026-10-02',generated_at='2026-10-31T12:00:00+08:00')['points'][0]['score'])

    def test_cooldown_counts_five_sessions_excludes_weekend(self):
        dates=['2026-11-02','2026-11-03','2026-11-04','2026-11-05','2026-11-06','2026-11-09']
        with patch.object(C.v6_forecast,'session_dates',return_value=dates):r=C.cooldown_details('2026-10-30','2026-10-30',5)
        self.assertEqual(r['blocked_dates'],dates[:5]);self.assertEqual(r['earliest_review'],'2026-11-09');self.assertIn('冷卻不是風險降低',r['note'])
        with patch.object(C.v6_forecast,'session_dates',side_effect=AssertionError('inactive calendar query')):
            self.assertIsNone(C.cooldown_details('2026-10-01','2026-10-30',0)['earliest_review'])

    def test_reading_uses_known_levels_without_recalculating_probabilities(self):
        row=frame().iloc[0];v=scenario_views(row);self.assertEqual(set(v),{'up','flat','down'})
        self.assertIn('103點',v['up']['support']);self.assertIn('97點',v['down']['support']);self.assertIn('不是預測盤中',v['flat']['reading'])
        m=market_view(row);self.assertIn('反彈修復',m['summary']);self.assertIn('不能直接視為全面變現壓力',''.join(m['evidence']))
        self.assertNotIn('nan',str(scenario_views({'close':100,'high':np.nan,'low':np.nan,'ma5':None})))

    def test_old_record_cannot_receive_later_generated_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);store=p/'v6_comparison'
            H.save(frame(),generated_at='2026-10-31T12:00:00+08:00',path=p/'v6_comparison_history.json')
            snap={'version':C.VERSION,'prepared_at':'2026-10-31T11:00:00+08:00','market_as_of':'2026-10-30','forecast_dates':['2026-11-02','2026-11-03','2026-11-04'],'anchor':100}
            old=C.publish(snap,store)
            with patch.object(C.v6_forecast,'index_frame',return_value=frame()):
                self.assertIsNone(C.report(old['id'],store=store)['retrospective'])
                self.assertIsNone(C.report(store=store,before='2026-10-31T11:30:00+08:00')['retrospective'])
                self.assertEqual(C.report(store=store)['retrospective']['strategy_key'],'v6-2')

    def test_saved_history_survives_later_reconstruction(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);store=p/'v6_comparison'
            original=H.reconstruct(frame(),start='2026-10-01',end='2026-10-30',generated_at='2026-10-31T11:00:00+08:00')
            old=C.publish({'version':C.VERSION,'prepared_at':original['generated_at'],'market_as_of':'2026-10-30','forecast_dates':['2026-11-02','2026-11-03','2026-11-04'],'anchor':100,'retrospective':original},store)
            H.save(frame(),generated_at='2026-10-31T12:00:00+08:00',path=p/'v6_comparison_history.json')
            with patch.object(C.v6_forecast,'index_frame',return_value=frame()):
                self.assertEqual(C.report(old['id'],store=store)['retrospective'],original)

if __name__=='__main__':unittest.main()
