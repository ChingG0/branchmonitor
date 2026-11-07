import csv
import json
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo
import pandas as pd

from backend import v6_tx, v6_features


class V6TxTests(unittest.TestCase):
    def setUp(self):
        # Frozen-study expectations must not depend on newer local market rows.
        self.inputs = patch.object(v6_tx, '_complete_input', return_value=(v6_tx._seed_input(), []))
        self.sources = patch.object(v6_tx, 'LIVE', Path('/nonexistent/v6-sources'))
        self.inputs.start(); self.sources.start()
        self.addCleanup(self.inputs.stop); self.addCleanup(self.sources.stop)

    def test_frozen_study_replays_same_trade_results(self):
        with patch.object(v6_tx,'LIVE',Path('/nonexistent/v6-sources')),patch.object(v6_tx.v6_trade_policy,'COOLDOWN_DAYS',{'legacy':5,'cross':5}):
            result=v6_tx.report()
        self.assertEqual(result['as_of'],'2026-10-16')
        self.assertEqual((result['wins'],result['completed']),(5,8))
        self.assertAlmostEqual(result['pnl_points'],5114.39,places=2)
        self.assertEqual(result['validation_status'],'未通過穩定70%驗證')
        seventy=result['observation70']
        self.assertEqual((seventy['wins'],seventy['completed']),(3,4))
        self.assertAlmostEqual(seventy['pnl_points'],3495.54,places=2)
        outlook=result['outlook']
        self.assertEqual((outlook['baseline']['declines'],outlook['baseline']['count']),(311,726))
        self.assertEqual((outlook['at_least65']['declines'],outlook['at_least65']['count']),(22,41))
        self.assertEqual((outlook['at_least70']['declines'],outlook['at_least70']['count']),(9,17))
        self.assertEqual([h['key'] for h in outlook['horizons']],['d1','d2','d3','d5','next_week'])
        self.assertEqual((outlook['horizons'][3]['at_least65']['declines'],outlook['horizons'][3]['at_least65']['count']),(13,41))
        self.assertEqual((outlook['horizons'][4]['at_least65']['declines'],outlook['horizons'][4]['at_least65']['count']),(8,23))
        if result['readiness']!='ready':
            self.assertIsNone(outlook['value'])

    def test_frozen_scores_match_research_while_sources_are_unchanged(self):
        raw=v6_tx._seed_input()
        with patch.object(v6_features,'RAW',v6_tx.SEED/'raw'):
            frame=v6_features.build(raw)
        score,available=v6_tx._score(frame)
        with (v6_tx.SEED/'sentiment-scores.csv').open() as handle:
            rows=list(csv.DictReader(handle))
        for day in ('2025-11-17','2026-03-05','2026-10-16'):
            expected=next(r for r in rows if r['signal_date']==day)
            self.assertTrue(available.loc[day])
            self.assertAlmostEqual(score.loc[day],float(expected['score']),places=9)

    def test_missing_source_blocks_signal(self):
        with patch.object(v6_tx,'LIVE',Path('/nonexistent/v6-sources')):
            with patch.object(v6_tx,'_source_age',return_value=['spx']):
                result=v6_tx.report()
        self.assertEqual(result['readiness'],'missing')
        self.assertIsNone(result['signal'])
        self.assertIsNone(result['entry_allowed'])
        self.assertIsNone(result['outlook']['value'])

    def test_outlook_waits_for_decision_and_clears_stale_score(self):
        tz=ZoneInfo('Asia/Taipei')
        with patch.object(v6_tx,'_now',return_value=datetime(2026,10,19,8,29,tzinfo=tz)):
            waiting=v6_tx.report()
        self.assertEqual(waiting['readiness'],'waiting')
        self.assertIsNone(waiting['outlook']['value'])
        with patch.object(v6_tx,'_now',return_value=datetime(2026,10,19,8,31,tzinfo=tz)):
            ready=v6_tx.report()
        self.assertEqual(ready['readiness'],'ready')
        self.assertEqual(ready['outlook']['sample']['count'],685)
        self.assertEqual(ready['outlook']['value'],42.2)
        with patch.object(v6_tx,'_now',return_value=datetime(2026,10,20,8,31,tzinfo=tz)):
            stale=v6_tx.report()
        self.assertEqual(stale['readiness'],'missing')
        self.assertIn('index_close',stale['missing_sources'])
        self.assertIsNone(stale['outlook']['value'])

    def test_weekly_k_counts_one_completed_week_once(self):
        days=pd.date_range('2023-01-02','2023-01-20',freq='B')
        frame=pd.DataFrame({'open':[100.0]*len(days),'close':[99.0]*len(days),
                            'decision_at':[(d+pd.offsets.BDay(1)).strftime('%Y-%m-%dT08:30:00+08:00') for d in days]},
                           index=days.strftime('%Y-%m-%d'))
        scores=pd.Series(.72,index=frame.index)
        available=pd.Series(True,index=frame.index)
        with patch.object(v6_tx,'_source_age',return_value=[]):
            result=v6_tx._downside_outlook(frame,scores,available,'ready')
        weekly=result['horizons'][-1]
        self.assertEqual(weekly['baseline']['count'],1)
        self.assertEqual(weekly['at_least70']['declines'],1)

    def test_new_index_bar_without_new_position_data_does_not_emit_signal(self):
        raw=v6_tx._seed_input()
        new_day='2026-10-19'
        raw['index'][new_day]=dict(raw['index'][v6_tx.SEED_END])
        for row in list(raw['futures']):
            if row['date']==v6_tx.SEED_END:
                raw['futures'].append({**row,'date':new_day})
        with patch.object(v6_tx,'_complete_input',return_value=(raw,[])):
            with patch.object(v6_tx,'LIVE',Path('/nonexistent/v6-sources')):
                result=v6_tx.report()
        self.assertEqual(result['as_of'],new_day)
        self.assertEqual(result['readiness'],'missing')
        self.assertIn('foreign',result['missing_sources'])
        self.assertIsNone(result['signal'])

    def test_error_page_rejected_and_seed_untouched(self):
        original=(v6_tx.SEED/'raw'/'positions-TXF-2026.csv').read_bytes()
        with self.assertRaisesRegex(ValueError,'錯誤頁'):
            v6_tx._append_csv(original,b'<!doctype html><html>Error</html>','2026-10-16')
        self.assertEqual((v6_tx.SEED/'raw'/'positions-TXF-2026.csv').read_bytes(),original)

    def test_us_daily_rows_wait_until_close_and_later_corrections_replace_provisional_values(self):
        ny=ZoneInfo('America/New_York')
        self.assertEqual(v6_tx._completed_us_day(datetime(2026,10,19,9,30,tzinfo=ny)),'2026-10-18')
        self.assertEqual(v6_tx._completed_us_day(datetime(2026,10,19,20,30,tzinfo=ny)),'2026-10-19')
        times=[int(datetime(2026,10,d,9,30,tzinfo=ny).timestamp()) for d in (16,19)]
        def chart(dates, values):
            return json.dumps({'chart':{'result':[{'meta':{'exchangeTimezoneName':'America/New_York'},
                'timestamp':dates,'indicators':{'quote':[{'close':values}]}}],'error':None}}).encode()
        old=chart(times[:1],[100.0]);incoming=chart(times,[100.0,110.0])
        waiting,count=v6_tx._merge_yahoo(old,incoming,'2026-10-18')
        self.assertEqual(count,0)
        self.assertEqual(json.loads(waiting)['chart']['result'][0]['timestamp'],times[:1])
        completed,count=v6_tx._merge_yahoo(waiting,incoming,'2026-10-19')
        self.assertEqual(count,1)
        corrected,count=v6_tx._merge_yahoo(completed,chart(times,[100.0,109.0]),'2026-10-19')
        self.assertEqual(count,0)
        self.assertEqual(json.loads(corrected)['chart']['result'][0]['indicators']['quote'][0]['close'],[100.0,109.0])

    def test_failed_refresh_leaves_previous_snapshot(self):
        with TemporaryDirectory() as tmp:
            live=Path(tmp)/'v6_sources';live.mkdir();(live/'marker').write_text('unchanged')
            with patch.object(v6_tx,'LIVE',live),patch.object(v6_tx.v9_tx,'refresh',return_value={'status':'complete'}),patch.object(v6_tx,'_complete_input',return_value=(v6_tx._seed_input(),[])),patch.object(v6_tx,'_request',side_effect=OSError('offline')):
                outcome=v6_tx.refresh()
            self.assertEqual(outcome['status'],'partial')
            self.assertEqual((live/'marker').read_text(),'unchanged')
            self.assertEqual(list(Path(tmp).glob('*.stage-*')),[])

    def test_taiwan_etf_uses_own_close_and_removes_typhoon_holiday_bars(self):
        zone=ZoneInfo('Asia/Taipei')
        times=[int(datetime(2026,10,d,9,tzinfo=zone).timestamp()) for d in (16,22,23,27)]
        def chart(ts,values):
            return json.dumps({'chart':{'result':[{'meta':{'exchangeTimezoneName':'Asia/Taipei'},'timestamp':ts,
                'indicators':{'quote':[{'close':values}]}}],'error':None}}).encode()
        old=chart(times[:3],[50,51,51]);fresh=chart(times,[50,51,51,52])
        merged,count=v6_tx._merge_yahoo(old,fresh,'2026-10-27',{'2026-10-22','2026-10-27'})
        result=json.loads(merged)['chart']['result'][0]
        self.assertEqual(result['timestamp'],[times[0],times[1],times[3]])
        self.assertEqual(result['indicators']['quote'][0]['close'],[50,51,52])
        self.assertEqual(count,1)

    def test_failed_base_quote_refresh_preserves_snapshot(self):
        with TemporaryDirectory() as tmp:
            live=Path(tmp)/'v6_sources';live.mkdir();(live/'marker').write_text('unchanged')
            with patch.object(v6_tx,'LIVE',live),patch.object(v6_tx.v9_tx,'refresh',side_effect=OSError('database unavailable')):
                outcome=v6_tx.refresh()
            self.assertEqual(outcome['status'],'partial')
            self.assertIn('database unavailable',outcome['message'])
            self.assertEqual((live/'marker').read_text(),'unchanged')

if __name__=='__main__':unittest.main()
