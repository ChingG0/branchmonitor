import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from backend import v6_weather_history as history, v6_weather


class V6HistoryTests(unittest.TestCase):
    def frame(self):
        day = '2026-09-30'
        row = {'decision_at': '2026-10-01T08:30:00+08:00', 'close': 30000}
        for name in history.FEATURES:
            row[name + '_value'] = 20.0
            row[name + '_available'] = pd.Timestamp('2026-10-01T08:00:00+08:00') if name in history.OVERSEAS else pd.Timestamp('2026-09-30T20:00:00+08:00')
        row['foreign_net_short'] = 40000
        return pd.DataFrame([row], index=[day])

    def reconstruct(self, frame, **kwargs):
        return history.reconstruct(frame, pd.Series([.68], index=frame.index),
            pd.Series([True], index=frame.index), start='2026-10-01', end='2026-10-16',
            generated_at='2026-10-19T09:00:00+08:00', **kwargs)

    def test_cutoff_uses_previous_close_and_excludes_future_context(self):
        frame = self.frame()
        river = [{'date': '2026-09-30', 'close': 30000, 'ma20': 29000, 'maintenance_pct': 180},
                 {'date': '2026-10-16', 'close': 99999, 'ma20': 88888, 'maintenance_pct': 999}]
        dix = [{'date': '2026-09-30', 'dix': '.45', 'gex': '123456789'},
               {'date': '2026-10-01', 'dix': '.9', 'gex': '999999999'}]
        with patch.object(v6_weather.liquidity_river, 'report', side_effect=AssertionError('latest fallback')):
            result = self.reconstruct(frame, river_points=river, dix_rows=dix)
        point = result['points'][0]
        self.assertEqual(point['date'], '2026-10-01')
        self.assertEqual(point['score'], 68)
        self.assertEqual(point['market_as_of'], '2026-09-30')
        self.assertEqual(point['news'], [])
        values = {r['label']: r['value'] for r in point['indicators']}
        self.assertEqual(values['融資維持率估值'], 180)
        self.assertEqual(values['美國 DIX 暗池指標'], 45)
        self.assertTrue(all(r['as_of'] < '2026-10-01' for r in point['indicators']))
        with patch.object(v6_weather.liquidity_river, 'report', side_effect=AssertionError('latest fallback')):
            no_river = self.reconstruct(frame, river_points=river[1:], dix_rows=[])
        self.assertNotIn('融資維持率估值', [r['label'] for r in no_river['points'][0]['indicators']])

    def test_future_feature_disables_score_and_is_not_displayed(self):
        frame = self.frame()
        frame.loc['2026-09-30', 'vix_available'] = pd.Timestamp('2026-10-02T08:00:00+08:00')
        point = self.reconstruct(frame, river_points=[], dix_rows=[])['points'][0]
        self.assertIsNone(point['score'])
        self.assertIn('vix', point['missing_sources'])
        self.assertNotIn('VIX', [r['label'] for r in point['indicators']])

    def test_history_is_separate_from_manual_forecast_and_read_only(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            history_file = folder / 'v6_weather_retrospective.json'
            data = self.reconstruct(self.frame(), river_points=[], dix_rows=[])
            history_file.write_text(json.dumps(data))
            original = history_file.read_bytes()
            report = v6_weather.report(store=folder / 'v6_weather')
            self.assertIsNone(report['snapshot'])
            self.assertEqual(report['history'], [])
            self.assertEqual(len(report['historical']['points']), 1)
            self.assertEqual(history_file.read_bytes(), original)
            history_file.write_text('invalid')
            self.assertIsNone(v6_weather.report(store=folder / 'v6_weather')['historical'])


if __name__ == '__main__':
    unittest.main()
