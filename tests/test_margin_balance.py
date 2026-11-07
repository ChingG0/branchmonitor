import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import db, margin_balance


def official(financing=(100, 110), short=(20, 21), dates=None):
    dates = dates or ['2026/10/07', '2026/10/08']
    return json.dumps({
        'range': '20261007-20261008',
        'taiex': {'categories': dates, 'series': [{'data': [49000, 49100]}]},
        'purchase': {'categories': dates, 'series': [{'data': list(financing)}, {'data': [1, 2]}]},
        'shortSale': {'categories': dates, 'series': [{'data': list(short)}, {'data': [3, 4]}]},
    })


class MarginBalanceTest(unittest.TestCase):
    def test_range_uses_balance_series_not_daily_change_and_validates_dates(self):
        rows = margin_balance.parse_range(official(), '2026-10-07', '2026-10-08')
        self.assertEqual(rows['2026-10-07'], {'financing_units': 100, 'short_units': 20})
        self.assertEqual(rows['2026-10-08'], {'financing_units': 110, 'short_units': 21})
        with self.assertRaises(ValueError):
            margin_balance.parse_range(official(), '2026-10-08', '2026-10-08')
        with self.assertRaises(ValueError):
            margin_balance.parse_range(official(short=(-1, 21)), '2026-10-07', '2026-10-08')

    def test_one_range_request_caches_days_and_failed_update_preserves_last_good(self):
        calls = []
        def fetch(url):
            calls.append(url)
            return official()
        with tempfile.TemporaryDirectory() as folder, patch.object(db, 'DATA', Path(folder)):
            result = margin_balance.refresh(fetch, ['2026-10-07', '2026-10-08'])
            self.assertEqual(len(calls), 1)
            self.assertEqual(result['as_of'], '2026-10-08')
            self.assertEqual(len(result['days']), 2)
            failed = margin_balance.refresh(lambda _: b'<html>blocked</html>', ['2026-10-07', '2026-10-08'])
            self.assertEqual(failed['days'], result['days'])
            self.assertIn('暫不可用', failed['warning'])


if __name__ == '__main__':
    unittest.main()
