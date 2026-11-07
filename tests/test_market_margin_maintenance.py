import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import db, market_margin_maintenance as m


DAY = '2026-10-12'


def margin():
    return json.dumps({'stat': 'OK', 'date': '20261012', 'tables': [
        {'fields': m.SUMMARY_FIELDS, 'data': [
            ['融資(交易單位)', '0', '0', '0', '0', '150'],
            ['融券(交易單位)', '0', '0', '0', '0', '3'],
            ['融資金額(仟元)', '0', '0', '0', '0', '8,000']]},
        {'fields': m.MARGIN_FIELDS, 'data': [
            ['2330', '台積電', '0', '0', '0', '0', '100', '0', '0', '0', '0', '0', '0', '0', '0', ' '],
            ['0050', '元大台灣50', '0', '0', '0', '0', '50', '0', '0', '0', '0', '0', '0', '0', '0', ' ']]}
    ]})


def prices(fund_close='--'):
    return json.dumps({'stat': 'OK', 'date': '20261012', 'tables': [
        {'title': '115年10月12日 每日收盤行情',
         'fields': ['證券代號', '名稱', '成交股數', '成交筆數', '成交金額', '開盤價', '最高價', '最低價', '收盤價'],
         'data': [['2330', '台積電', '0', '0', '0', '0', '0', '0', '120'],
                  ['0050', '元大台灣50', '0', '0', '0', '0', '0', '0', fund_close]]}
    ]})


class MarketMarginMaintenanceTest(unittest.TestCase):
    def test_uses_stock_value_over_loan_and_only_past_close_for_suspended_security(self):
        data = m.parse_margin(margin(), DAY)
        quotes = m.parse_prices(prices(), DAY)
        result = m.calculate(data, quotes, {'0050': {'date': '2026-10-08', 'close': 80}}, DAY)
        self.assertAlmostEqual(result['maintenance_pct'], 200.0)
        self.assertEqual(result['covered_units_pct'], 100)
        self.assertFalse(result['estimated'])
        with self.assertRaises(ValueError):
            m.calculate(data, quotes, {'0050': {'date': '2026-10-13', 'close': 80}}, DAY)

    def test_rejects_wrong_date_and_stock_sum_and_preserves_cache_on_refresh_failure(self):
        with self.assertRaises(ValueError):
            m.parse_margin(margin(), '2026-10-08')
        bad = json.loads(margin())
        bad['tables'][1]['data'][0][6] = '99'
        with self.assertRaises(ValueError):
            m.parse_margin(json.dumps(bad), DAY)
        with tempfile.TemporaryDirectory() as folder, patch.object(db, 'DATA', Path(folder)):
            result = m.refresh(lambda url: margin() if 'MI_MARGN' in url else prices('80'), [DAY])
            self.assertEqual(result['days'][DAY]['maintenance_pct'], 200.0)
            result = m.refresh(lambda url: b'<html>blocked</html>', [DAY])
            self.assertEqual(result['days'][DAY]['maintenance_pct'], 200.0)
            self.assertIn(DAY, result['warning'])

    def test_source_failure_does_not_trigger_historical_backfill_requests(self):
        calls = []
        def blocked(url):
            calls.append(url)
            raise ConnectionError('offline')
        with tempfile.TemporaryDirectory() as folder, patch.object(db, 'DATA', Path(folder)):
            result = m.refresh(blocked, ['2026-10-07', '2026-10-08', DAY])
            self.assertEqual(len(calls), 1)
            self.assertFalse(result['days'])


if __name__ == '__main__':
    unittest.main()
