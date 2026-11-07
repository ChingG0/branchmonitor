import unittest

import numpy as np

from backend.price_levels import first_resistance


class PriceLevelTests(unittest.TestCase):
    def test_nearby_observed_highs_form_first_zone(self):
        highs = np.array([[900.], [814.], [819.], [816.]])
        self.assertEqual(first_resistance(highs, 811, 20, 3, 0),
                         {'low': 814, 'high': 819, 'lookback': 20})

    def test_no_overhead_high_returns_no_price_target(self):
        highs = np.array([[99.], [101.], [102.5], [110.]])
        self.assertIsNone(first_resistance(highs, 102.5, 3, 2, 0))

    def test_older_history_used_only_without_recent_overhead_high(self):
        highs = np.array([[110.]] + [[99.]] * 30 + [[100.]])
        self.assertEqual(first_resistance(highs, 100, 3, 31, 0),
                         {'low': 110, 'high': 110, 'lookback': 60})
