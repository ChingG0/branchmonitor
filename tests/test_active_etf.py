import json
import tempfile
import unittest
from unittest.mock import patch, Mock
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

from backend import active_etf, db, sources


class ActiveEtfTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = db.DATA
        db.DATA = Path(self.temp.name)
        db.initialize()

    def tearDown(self):
        db.DATA = self.original
        self.temp.cleanup()

    def issuer_payload(self, holdings_day='2026-11-05', pcf_day='2026-11-06'):
        def epoch(day):
            value = int(datetime.fromisoformat(day).replace(tzinfo=ZoneInfo('Asia/Taipei')).timestamp()*1000)
            return f'/Date({value})/'
        details = [{'DetailCode': f'{1000+i}', 'DetailName': f'股票{i}',
                    'Share': 1000, 'NavRate': 1, 'TranDate': epoch(holdings_day)} for i in range(10)]
        return {'pcf': [{'TranDate': epoch(holdings_day), 'PostDate': epoch(pcf_day)}],
                'asset': [{'AssetCode': 'ST', 'Details': details}]}

    def test_ezmoney_uses_holdings_date_not_next_pcf_date(self):
        actual, holdings, metadata = active_etf._parse_ezmoney_snapshot(self.issuer_payload())
        self.assertEqual(actual, '2026-11-05')
        self.assertEqual(metadata['source_pcf_date'], '2026-11-06')
        self.assertEqual(metadata['holdings_as_of'], actual)
        self.assertEqual(len(holdings), 10)

    def test_ezmoney_rejects_conflicting_detail_date(self):
        data = self.issuer_payload()
        data['asset'][0]['Details'][0]['TranDate'] = '2026-11-04'
        with self.assertRaises(sources.SourceError):
            active_etf._parse_ezmoney_snapshot(data)

    def test_ezmoney_rejects_missing_and_mixed_metadata_dates(self):
        for field in ['TranDate', 'PostDate']:
            with self.subTest(field=field):
                data = self.issuer_payload()
                data['pcf'].append({**data['pcf'][0], field: None})
                with self.assertRaises(sources.SourceError):
                    active_etf._parse_ezmoney_snapshot(data)

    def test_ezmoney_skips_weekend_before_fetching_friday_holdings(self):
        response = Mock()
        response.json.return_value = self.issuer_payload('2026-10-30', '2026-11-02')
        with patch.object(active_etf, '_request', return_value=response) as request:
            snapshot = active_etf._fetch_ezmoney(active_etf.FUNDS['00981A'], '2026-10-30', Mock())
        self.assertEqual(snapshot[0], '2026-10-30')
        self.assertEqual(request.call_args.kwargs['json']['date'], '115/11/02')

    def test_ezmoney_handles_exchange_holiday_gap(self):
        empty, available = Mock(), Mock()
        empty.json.return_value = {'pcf': []}
        available.json.return_value = self.issuer_payload('2026-10-08', '2026-10-12')
        with patch.object(active_etf, '_request', side_effect=[Mock(), empty, available]) as request:
            snapshot = active_etf._fetch_ezmoney(active_etf.FUNDS['00981A'], '2026-10-08', Mock())
        self.assertEqual(snapshot[0], '2026-10-08')
        self.assertEqual(request.call_args.kwargs['json']['date'], '115/10/12')

    def test_ezmoney_never_relabels_newer_holdings_as_requested_day(self):
        response = Mock()
        response.json.return_value = self.issuer_payload('2026-11-05', '2026-11-06')
        with patch.object(active_etf, '_request', return_value=response):
            with self.assertRaises(sources.SourceError):
                active_etf._fetch_ezmoney(active_etf.FUNDS['00981A'], '2026-11-04', Mock())

    def test_ezmoney_never_relabels_stale_holdings_as_requested_day(self):
        response = Mock()
        response.json.return_value = self.issuer_payload('2026-11-04', '2026-11-05')
        with patch.object(active_etf, '_request', return_value=response):
            with self.assertRaises(sources.SourceError):
                active_etf._fetch_ezmoney(active_etf.FUNDS['00981A'], '2026-11-05', Mock())

    def test_worker_preserves_source_date_metadata_in_payload_and_report(self):
        snapshot = active_etf._parse_ezmoney_snapshot(self.issuer_payload())
        active_etf._lock.acquire()
        with patch.object(active_etf, '_trading_days', return_value=['2026-11-05']), \
             patch.object(active_etf, 'FUNDS', {'00981A': active_etf.FUNDS['00981A']}), \
             patch.dict(active_etf.FETCHERS, {'ezmoney': Mock(return_value=snapshot)}):
            active_etf._worker('2026-11-05')
        with db.connect() as conn:
            saved = json.loads(conn.execute('SELECT payload FROM active_etf_days').fetchone()[0])
        self.assertEqual(saved['source_pcf_date'], '2026-11-06')
        self.assertEqual(saved['date'], '2026-11-05')
        fund = next(x for x in active_etf.report()['funds'] if x['fund'] == '00981A')
        self.assertEqual(fund['source_pcf_date'], '2026-11-06')
        self.assertEqual(fund['holdings_as_of'], '2026-11-05')

    def test_validate_rejects_duplicate_symbol(self):
        rows = [{'symbol': f'{1000+i}', 'name': f'股票{i}', 'shares': 100, 'weight': 1} for i in range(10)]
        rows.append(dict(rows[0]))
        with self.assertRaises(sources.SourceError):
            active_etf._validate('2026-10-02', rows)

    def test_report_compares_raw_shares_and_builds_common_moves(self):
        old = [{'symbol': f'{1000+i}', 'name': f'股票{i}', 'shares': 100, 'weight': 1} for i in range(10)]
        new = [dict(row) for row in old]
        new[0].update(shares=250, weight=2.5)
        with db.connect() as conn:
            for fund in active_etf.FUNDS:
                for day, holdings in [('2026-10-01', old), ('2026-10-02', new)]:
                    payload={'fund':fund,'date':day,'holdings':holdings}
                    conn.execute('INSERT INTO active_etf_days VALUES (?,?,?,?,?)',
                                 (fund, day, json.dumps(payload), 'official', day))
        result = active_etf.report('2026-10-02')
        self.assertEqual(result['funds'][0]['changes'][0]['change_shares'], 150)
        self.assertEqual(result['common'][0]['fund_count'], 4)
        self.assertEqual(result['common'][0]['direction'], 'buy')

    def test_price_performance_uses_prior_period_close(self):
        with db.connect() as conn:
            for day, close in [('2025-12-31', 10), ('2026-03-31', 12),
                               ('2026-09-30', 15), ('2026-10-02', 18)]:
                conn.execute('INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?)',
                             (day, '00981A', 'ETF', close, close, close, close, 1, 'TWSE'))
        result = active_etf._price_performance('00981A', '2026-10-02')
        self.assertEqual(result['month']['return_pct'], 20.0)
        self.assertEqual(result['half_year']['return_pct'], 50.0)
        self.assertEqual(result['ytd']['return_pct'], 80.0)
        self.assertFalse(result['ytd']['limited'])


if __name__ == '__main__':
    unittest.main()
