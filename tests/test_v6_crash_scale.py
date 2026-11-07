import tempfile
import unittest
from pathlib import Path

from backend import v6_crash_scale, v6_tx, v6_weather


class CrashScaleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = v6_tx._seed_input()['index']

    def test_october_drawdown_uses_correct_denominators_and_prior_weeks(self):
        result = v6_crash_scale.build(self.index, as_of='2026-10-16', generated_at='2026-10-19T07:00:00+08:00', observation_window=('2026-10-07','2026-10-15'))
        self.assertEqual(result['current_level'], '迷你股災')
        self.assertEqual(result['cycle_peak']['date'], '2026-10-06')
        self.assertEqual(result['cycle_low']['date'], '2026-10-15')
        self.assertAlmostEqual(result['close_drawdown_pct'], 5.7189, places=3)
        self.assertAlmostEqual(result['current_week']['range_points'], 2617.52, places=2)
        self.assertAlmostEqual(result['current_week']['range_pct'], 5.2972, places=3)
        self.assertAlmostEqual(result['prior_four_week_average_range_pct'], 2.8326, places=3)
        self.assertAlmostEqual(result['weekly_range_multiple'], 1.8701, places=3)
        self.assertEqual([w['week'] for w in result['prior_four_weeks']], ['2026-09-14','2026-09-21','2026-09-28','2026-10-05'])
        self.assertTrue(result['weekly_stress'])
        self.assertAlmostEqual(result['observed_window']['close_change_pct'], -5.6951, places=3)
        self.assertAlmostEqual(result['observed_window']['close_change_points'], -2836.50, places=2)
        self.assertAlmostEqual(result['examples']['2020']['pct'], 30.1207, places=3)
        self.assertAlmostEqual(result['examples']['2022']['pct'], 32.1711, places=3)
        self.assertEqual(result['examples']['2020']['level'], '大股災')
        self.assertEqual(result['examples']['2022']['level'], '大股災')
        amplitude = result['amplitude']
        self.assertAlmostEqual(amplitude['daily_average_20_pct'], 1.39077, places=4)
        self.assertAlmostEqual(amplitude['daily_recent_5_average_pct'], 1.84774, places=4)
        self.assertAlmostEqual(amplitude['daily_previous_20_average_pct'], 1.24218, places=4)
        self.assertAlmostEqual(amplitude['weekly_current_pct'], 5.30792, places=4)
        self.assertAlmostEqual(amplitude['weekly_previous_4_average_pct'], 2.90061, places=4)
        self.assertEqual((amplitude['weekly_current_color'], amplitude['weekly_confirmed_color'],
                          amplitude['weekly_confirmed_streak']), ('綠', '綠', 1))
        self.assertEqual((amplitude['weekly_previous_color'], amplitude['weekly_previous_streak']), ('紅', 4))
        self.assertEqual(amplitude['confirmations'], 2)

    def test_weekly_streak_does_not_confirm_an_unfinished_week(self):
        thursday = v6_crash_scale.build(self.index, as_of='2026-10-15',
                                         generated_at='2026-10-19T07:00:00+08:00')['amplitude']
        self.assertFalse(thursday['weekly_current_confirmed'])
        self.assertEqual(thursday['weekly_current_color'], '綠')
        self.assertEqual((thursday['weekly_confirmed_color'], thursday['weekly_confirmed_streak']), ('紅', 4))
        self.assertTrue(thursday['daily_expanding'])
        self.assertTrue(thursday['weekly_expanding'])

    def test_amplitude_uses_previous_close_and_short_history_is_unknown(self):
        sample = {
            '2026-10-12': {'open':100,'high':110,'low':90,'close':100},
            '2026-10-13': {'open':100,'high':110,'low':100,'close':105},
            '2026-10-14': {'open':105,'high':105,'low':95,'close':100},
        }
        result = v6_crash_scale.build(sample, as_of='2026-10-14',
                                       generated_at='2026-10-19T07:00:00+08:00')
        amplitude = result['amplitude']
        self.assertAlmostEqual(amplitude['daily_latest_pct'], 10/105*100, places=6)
        self.assertIsNone(amplitude['daily_average_20_pct'])
        self.assertIsNone(amplitude['weekly_previous_4_average_pct'])
        self.assertEqual(amplitude['readiness'], '資料不足')

    def test_future_rows_do_not_change_past_and_bullish_range_not_a_crash(self):
        past = v6_crash_scale.build(self.index, as_of='2026-10-15', generated_at='2026-10-19T07:00:00+08:00')
        other = dict(self.index)
        other['2026-10-16'] = {'open': 99999, 'high': 120000, 'low': 99999, 'close': 120000}
        replay = v6_crash_scale.build(other, as_of='2026-10-15', generated_at='2026-10-19T07:00:00+08:00')
        self.assertEqual(past,replay)
        rising = {'2026-10-12': {'open':100,'high':100,'low':100,'close':100},
                  '2026-10-13': {'open':100,'high':110,'low':100,'close':110}}
        week = v6_crash_scale._week_summary(sorted(rising.items()))
        self.assertGreater(week['range_pct'], 5)
        self.assertEqual(week['ordered_fall_pct'], 0)

    def test_user_defined_levels_have_exact_boundaries(self):
        for pct,label in [(4.99,'未達迷你股災'),(5,'迷你股災'),(9.99,'迷你股災'),
                          (10,'小股災'),(19.99,'小股災'),(20,'中股災'),
                          (29.99,'中股災'),(30,'大股災')]:
            self.assertEqual(v6_crash_scale._grade(pct),label)

    def test_rejects_bad_prices_and_never_reads_future_scale_for_older_forecast(self):
        with self.assertRaises(ValueError):
            v6_crash_scale.build({'2026-10-16': {'open':100,'high':90,'low':80,'close':100}},
                as_of='2026-10-16', generated_at='2026-10-19T07:00:00+08:00')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'v6_crash_scale.json').write_text('{"kind":"observed_crash_scale","as_of":"2026-10-16"}')
            self.assertIsNotNone(v6_weather.report(store=root/'v6_weather')['crash_scale'])
            (root/'v6_weather').mkdir()
            (root/'v6_weather'/'x.json').write_text('{"id":"x","prepared_at":"2026-10-10T01:00:00+08:00","market_as_of":"2026-10-09","stage":"平穩"}')
            self.assertIsNone(v6_weather.report('x',store=root/'v6_weather')['crash_scale'])


if __name__ == '__main__':
    unittest.main()
