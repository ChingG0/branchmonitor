import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import db, forward_pe_store as S, forward_pe_valuation as V

CUTOFF = '2026-11-08T21:00:00+08:00'


def fixture():
    members = [{'rank': i + 1, 'symbol': str(1000 + i), 'name': 'fixture', 'weight_pct': 2.0} for i in range(50)]
    quotes = {m['symbol']: {'symbol': m['symbol'], 'market_date': '2026-11-06', 'close': 100.,
                          'shares': 1000., 'shares_unit': 'shares', 'shares_basis': 'official_issued_shares',
                          'currency': 'TWD', 'price_basis': 'raw_close', 'known_at': CUTOFF} for m in members}
    estimates = [{'id': m['symbol'], 'symbol': m['symbol'], 'source_provider': 'licensed_export',
                  'provider': 'consensus_A', 'period_kind': 'calendar_year', 'period': '2026', 'statistic': 'median',
                  'value': 10., 'currency': 'TWD', 'eps_basis': 'basic', 'share_basis': 'current_ordinary_share_adjusted',
                  'forecast_as_of': '2026-11-05', 'published_at': '2026-11-05T18:00:00+08:00',
                  'known_at': CUTOFF, 'estimate_type': 'analyst_consensus',
                  'basis_evidence': {'reference': 'current ordinary shares and basic EPS verified', 'share_basis_date': '2026-11-06'}} for m in members]
    return members, quotes, estimates


class CalculationTests(unittest.TestCase):
    def run_calc(self, members, quotes, estimates):
        return V.calculate(members, quotes, estimates, quote_date='2026-11-06', cutoff=CUTOFF)

    def test_aggregate_uses_earnings_yield_not_arithmetic_pe(self):
        m, q, e = fixture()
        e = e[:2]
        e[0]['value'], e[1]['value'] = 10., 5.
        group = self.run_calc(m, q, e)['groups'][0]
        preview = group['mechanical_preview']
        self.assertAlmostEqual(preview['matched_subset_pe'], 200000 / 15000)
        self.assertNotEqual(preview['matched_subset_pe'], (10 + 20) / 2)
        self.assertEqual(preview['matched_market_cap_twd'], 200000)
        self.assertEqual(preview['original_basket_weight_coverage_pct'], 4)
        self.assertIsNone(group['qualified']['adopted_subset_pe'])

    def test_loss_and_zero_eps_remain_in_aggregate(self):
        m, q, e = fixture()
        e = e[:3]
        e[0]['value'], e[1]['value'], e[2]['value'] = 10., -2., 0.
        g = self.run_calc(m, q, e)['groups'][0]
        a = g['qualified']
        self.assertEqual(a['net_forecast_earnings_twd'], 8000)
        self.assertEqual(a['matched_market_cap_twd'], 300000)
        self.assertEqual(a['matched_subset_pe'], 37.5)
        self.assertEqual((a['negative_eps_names'], a['zero_eps_names']), (1, 1))
        self.assertIsNone(g['rows'][1]['individual_pe'])
        self.assertEqual(g['rows'][1]['earnings_yield_pct'], -2.)
        e[0]['value'] = 1.
        a = self.run_calc(m, q, e)['groups'][0]['qualified']
        self.assertIsNone(a['matched_subset_pe'])
        self.assertEqual(a['pe_undefined_reason'], 'nonpositive_net_earnings')

    def test_full_coverage_and_qualifying_partial_basket_have_distinct_labels(self):
        m, q, e = fixture()
        full = self.run_calc(m, q, e)['groups'][0]['qualified']
        self.assertTrue(full['gate_passed'])
        self.assertEqual(full['exact_top50_pe'], 10)
        partial = self.run_calc(m, q, e[:48])['groups'][0]['qualified']
        self.assertTrue(partial['gate_passed'])
        self.assertEqual(partial['adopted_subset_pe'], 10)
        self.assertIsNone(partial['exact_top50_pe'])
        self.assertFalse(partial['is_full_market'])
        self.assertIn('子集', partial['label'])

    def test_both_weight_denominators_and_largest_name_are_required(self):
        m, q, e = fixture()
        q[m[-1]['symbol']]['close'] = 100000
        a = self.run_calc(m, q, e[:49])['groups'][0]['qualified']
        self.assertTrue(a['criteria']['original_weight_coverage'])
        self.assertFalse(a['criteria']['revalued_cap_coverage'])
        self.assertFalse(a['gate_passed'])
        a = self.run_calc(m, q, e[1:])['groups'][0]['qualified']
        self.assertFalse(a['criteria']['top_rank_present'])

    def test_unknown_basic_diluted_normalized_and_providers_never_mix(self):
        m, q, e = fixture()
        e[0]['eps_basis'] = 'diluted'
        e[1]['eps_basis'] = 'normalized'
        e[2]['eps_basis'] = 'not_disclosed'
        e[3]['provider'] = 'consensus_B'
        e[4]['source_provider'] = 'other_export'
        e[5]['statistic'] = 'mean'
        e[6]['period'] = '2027'
        e[7]['currency'] = 'USD'
        groups = self.run_calc(m, q, e)['groups']
        self.assertEqual(len(groups), 9)
        for g in groups:
            self.assertEqual(len({r['estimate']['provider'] for r in g['rows'] if r['estimate']}), 1)
            if g['series']['eps_basis'] != 'basic':
                self.assertEqual(g['qualified']['names'], 0)
        self.assertEqual(next(g for g in groups if g['series']['currency'] == 'USD')['mechanical_preview']['names'], 0)

    def test_unverified_current_inputs_are_preview_only_not_hidden_or_zero(self):
        m, q, e = fixture()
        for r in e:
            r.update(eps_basis='not_disclosed', share_basis='not_disclosed', forecast_as_of=None, basis_evidence=None,
                     calculation_ready=True)  # Stored flags are not trusted.
        g = self.run_calc(m, q, e[:8])['groups'][0]
        self.assertEqual(g['mechanical_preview']['names'], 8)
        self.assertEqual(g['mechanical_preview']['matched_subset_pe'], 10)
        self.assertEqual(g['qualified']['names'], 0)
        self.assertIsNone(g['qualified']['matched_subset_pe'])
        self.assertIsNone(g['rows'][8]['estimate'])

    def test_freshness_uses_provider_date_not_recent_retrieval(self):
        m, q, e = fixture()
        e[0]['forecast_as_of'] = '2026-08-01'
        e[0]['retrieved_at'] = CUTOFF
        g = self.run_calc(m, q, e)['groups'][0]
        self.assertIn('forecast_outside_freshness_window', g['rows'][0]['issues'])
        self.assertEqual(g['qualified']['names'], 49)
        self.assertFalse(g['qualified']['gate_passed'])

    def test_price_share_date_basis_and_missing_values_are_validated(self):
        m, q, e = fixture()
        q['1000']['shares_unit'] = 'thousand_shares'
        q['1001']['shares_date'] = '2026-11-05'
        q['1002']['market_date'] = '2026-11-05'
        q['1003']['known_at'] = '2026-11-09T00:00:00+08:00'
        q['1004']['close'] = None
        g = self.run_calc(m, q, e)['groups'][0]
        self.assertEqual(g['qualified']['names'], 45)
        self.assertIsNone(g['qualified']['revalued_basket_cap_coverage_pct'])
        self.assertFalse(g['qualified']['gate_passed'])
        self.assertEqual(g['mechanical_preview']['matched_market_cap_twd'], 4500000)

    def test_both_publication_and_actual_knowledge_cutoffs_apply(self):
        m, q, e = fixture()
        e[0]['known_at'] = '2026-11-09T00:00:00+08:00'
        e[1]['published_at'] = '2026-11-09T00:00:00+08:00'
        result = self.run_calc(m, q, e)
        self.assertEqual(len(result['rejected_after_cutoff']), 2)
        self.assertEqual(result['groups'][0]['qualified']['names'], 48)

    def test_revision_uses_saved_comparable_vintages_not_parenthetical_news_value(self):
        m, q, e = fixture()
        older = {**e[0], 'id': 'older', 'value': 8., 'published_at': '2026-10-01T00:00:00+08:00'}
        e[0]['reported_prior_value'] = 999
        e.append(older)
        row = self.run_calc(m, q, e)['groups'][0]['rows'][0]
        self.assertEqual(row['estimate']['value'], 10)
        self.assertEqual(row['revision']['change_pct'], 25)
        self.assertEqual(row['revision']['previous_id'], 'older')
        self.assertTrue(row['revision']['basis_comparability_verified'])
        older['value'] = -8
        row = self.run_calc(m, q, e)['groups'][0]['rows'][0]
        self.assertIsNone(row['revision']['change_pct'])

    def test_adjustment_date_and_expired_ntm_cannot_be_qualified(self):
        m, q, e = fixture()
        e[0]['basis_evidence']['share_basis_date'] = '2026-11-05'
        g = self.run_calc(m, q, e)['groups'][0]
        self.assertIn('per_share_adjustment_date_not_aligned', g['rows'][0]['issues'])
        self.assertFalse(g['qualified']['gate_passed'])
        a = copy.deepcopy(e[1])
        a.update(period_kind='NTM', period='explicit_window', period_start='2025-11-01', period_end='2026-11-01')
        g = self.run_calc(m, q, [a])['groups'][0]
        self.assertEqual(g['qualified']['names'], 0)
        a.update(period_start='2026-11-06', period_end='2027-11-05')
        b = copy.deepcopy(a)
        b.update(symbol='1002', period_start='2026-11-07', period_end='2027-11-06')
        self.assertEqual(len(self.run_calc(m, q, [a, b])['groups']), 2)

    def test_conflicting_latest_values_do_not_crash_other_stocks_or_choose_a_hash(self):
        m, q, e = fixture()
        e.append({**e[0], 'id': 'conflict', 'value': 20})
        g = self.run_calc(m, q, e)['groups'][0]
        self.assertEqual(g['rows'][0]['issues'], ['conflicting_latest_estimates'])
        self.assertEqual(g['qualified']['names'], 49)
        self.assertFalse(g['qualified']['gate_passed'])


class SavedCalculationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(db, 'DATA', Path(self.tmp.name))
        self.patch.start()
        S.initialize()
        self.members, q, e = fixture()
        sid = S.source('test', 'licensed_export', 'https://source.example/official', b'fixture evidence')
        uid = S.universe(sid, '2026-10-30', self.members)
        for row in q.values():
            row.pop('known_at')
        qids = S.quote_batch(sid, list(q.values()))
        self.eids = S.estimate_batch(sid, e)
        self.snapshot = S.snapshot(uid, qids, self.eids)

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_exact_snapshot_is_not_replaced_by_newer_eps_or_backdated(self):
        before = V.compute(snapshot_id=self.snapshot)
        sid = S.source('eps', 'licensed_export', 'https://source.example/new', b'new evidence')
        e = fixture()[2][0]
        e['value'] = 99
        S.estimate_batch(sid, [e])
        after = V.compute(snapshot_id=self.snapshot)
        self.assertEqual(before['periods'], after['periods'])
        with self.assertRaises(ValueError):
            V.compute(snapshot_id=self.snapshot, cutoff='2026-11-06T18:00:00+08:00')
        self.assertEqual(len(after['periods']), 3)
        self.assertEqual(after['periods'][2]['period_kind'], 'NTM')
        self.assertEqual(after['periods'][2]['groups'], [])

    def test_read_does_not_compute_fetch_or_write_and_reports_are_append_only(self):
        out = V.save(snapshot_id=self.snapshot)
        original = Path(out['report_path']).read_bytes()
        db_path = S.home() / 'observations.sqlite3'
        baseline = db_path.read_bytes()
        paths = sorted(str(p) for p in S.home().rglob('*'))
        with patch.object(V, 'compute', side_effect=AssertionError('GET cannot calculate')):
            report = V.read()
            self.assertEqual(report['id'], out['id'])
            self.assertEqual(V.read(out['id']), report)
        self.assertEqual(db_path.read_bytes(), baseline)
        self.assertEqual(sorted(str(p) for p in S.home().rglob('*')), paths)
        out2 = V.save(snapshot_id=self.snapshot)
        self.assertNotEqual(out['id'], out2['id'])
        self.assertEqual(Path(out['report_path']).read_bytes(), original)
        self.assertEqual(V.read(out['id'])['id'], out['id'])
        self.assertIn('2026', Path(out['csv_path']).read_text())

    def test_input_raw_and_output_hashes_are_checked(self):
        out = V.save(snapshot_id=self.snapshot)
        path = Path(out['report_path'])
        value = json.loads(path.read_text())
        value['report']['status'] = 'tampered'
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            V.read()
        next((S.home() / 'raw').glob('*.gz')).write_bytes(b'corrupt')
        with self.assertRaises((ValueError, OSError)):
            V.compute(snapshot_id=self.snapshot)

    def test_missing_saved_report_creates_nothing_and_id_cannot_traverse_paths(self):
        with patch.object(db, 'DATA', Path(self.tmp.name) / 'empty'):
            self.assertEqual(V.read()['status'], 'not_saved')
            self.assertFalse(S.home().exists())
        with self.assertRaises(ValueError):
            V.read('../../other')


if __name__ == '__main__':
    unittest.main()
