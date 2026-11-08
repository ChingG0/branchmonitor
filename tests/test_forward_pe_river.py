import copy
import hashlib
import json
import statistics
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import db,forward_pe_store as S,forward_pe_valuation as V,forward_pe_river as R


def saved(month, pe=20., *, qualified=True, names=50, provider='A', policy='policy_A', suffix=0):
    year = month[:4]
    group = {'series': {'source_provider': 'licensed', 'provider': provider, 'currency': 'TWD',
                       'eps_basis': 'basic', 'share_basis': 'current_ordinary_share_adjusted', 'statistic': 'median',
                       'period_kind': 'calendar_year', 'period': year},
             'qualified': {'gate_passed': qualified, 'names': names, 'exact_top50_pe': pe}}
    stamp = month+'-28T18:00:00+08:00'
    return {'id': S.digest([month, pe, suffix])[:32], 'method_version': 'forward-pe-valuation.v1',
            'policy_sha256': policy, 'information_cutoff': stamp, 'computed_at': month+'-28T18:01:00+08:00',
            'price_date': month+'-28', 'weight_date': month+'-01',
            'snapshot_id': S.digest([month,suffix]), 'periods': [{'period_kind': 'calendar_year', 'period': year, 'groups': [group]}]}


def history():
    return [saved(R.month_shift('2026-11', i), 20 + (i+36)*.1) for i in range(-60,1)]


class HistoryTests(unittest.TestCase):
    def view(self, records, as_of='2026-11-28T22:00:00+08:00'):
        return R.analyze(records, as_of=as_of)

    def test_36_and_60_closed_months_exclude_current_from_reference(self):
        records=history()
        result=self.view(records)
        self.assertEqual(len(result['series']),1)
        win=result['series'][0]['windows']['3']
        expected=[r['periods'][0]['groups'][0]['qualified']['exact_top50_pe'] for r in records[-37:-1]]
        self.assertEqual(win['status'],'ready')
        self.assertEqual(win['reference']['required_months'],36)
        self.assertAlmostEqual(win['reference']['mean'],statistics.mean(expected))
        self.assertAlmostEqual(win['reference']['sd'],statistics.stdev(expected))
        self.assertEqual(len(win['points']),37)
        self.assertEqual(result['series'][0]['windows']['5']['status'],'ready')
        altered=copy.deepcopy(records)
        altered[-1]['periods'][0]['groups'][0]['qualified']['exact_top50_pe']=500
        next_window=self.view(altered)['series'][0]['windows']['3']
        self.assertEqual(win['reference'],next_window['reference'])
        self.assertNotEqual(win['position']['z'],next_window['position']['z'])

    def test_future_data_do_not_change_past_points_or_as_of(self):
        records=history()
        before=self.view(records)
        records.append(saved('2026-12',999))
        self.assertEqual(before,self.view(records))
        later=self.view(records,as_of='2026-12-28T22:00:00+08:00')
        first=before['series'][0]['windows']['3']['points']
        last=later['series'][0]['windows']['3']['points']
        by_month={p['month']:p for p in last}
        for p in first:
            if p['month'] in by_month:self.assertEqual(p,by_month[p['month']])

    def test_calculations_not_yet_saved_are_not_visible(self):
        r=saved('2026-10');r['computed_at']='2026-12-01T22:00:00+08:00'
        self.assertEqual(self.view([r])['status'],'not_saved')
        r['computed_at']='2026-10-28T17:59:00+08:00'
        self.assertEqual(self.view([r])['status'],'not_saved')

    def test_missing_month_blocks_position_and_remains_null_not_zero(self):
        records=[r for r in history() if r['information_cutoff'][:7]!='2026-01']
        w=self.view(records)['series'][0]['windows']['3']
        self.assertEqual(w['status'],'history_gap')
        self.assertEqual(w['reference']['available_months'],35)
        self.assertEqual(w['reference']['missing_months'],['2026-01'])
        self.assertIsNone(w['position']['z'])
        self.assertIsNone(next(p for p in w['points'] if p['month']=='2026-01')['pe'])

    def test_repeated_builds_and_latest_unqualified_month_do_not_inflate_history(self):
        records=history()
        duplicate=copy.deepcopy(records[-1]);duplicate.update(id='f'*32,computed_at='2026-11-28T18:02:00+08:00')
        duplicate['periods'][0]['groups'][0]['qualified'].update(gate_passed=False,names=8,exact_top50_pe=None)
        records.append(duplicate)
        w=self.view(records)['series'][0]['windows']['3']
        self.assertEqual(w['status'],'current_source_gap')
        self.assertIsNone(w['current']['pe'])
        self.assertEqual(w['current']['report_id'],'f'*32)
        self.assertEqual(w['reference']['available_months'],36)
        self.assertEqual(w['observed_months'],36)
        self.assertIsNone(w['position']['z'])

    def test_qualifying_subset_is_not_a_full50_month(self):
        records=history();records[-1]['periods'][0]['groups'][0]['qualified']['names']=48
        w=self.view(records)['series'][0]['windows']['3']
        self.assertIsNone(w['current']['pe'])
        self.assertIn('50檔',w['current']['reasons'][0])

    def test_provider_and_method_policy_changes_have_independent_histories(self):
        records=history();records.append(saved('2026-11',25,provider='B',policy='policy_B',suffix=1))
        records[-1]['information_cutoff']='2026-11-28T19:00:00+08:00'
        records[-1]['computed_at']='2026-11-28T19:01:00+08:00'
        series=self.view(records)['series']
        self.assertEqual(len(series),2)
        for s in series:
            w=s['windows']['3']
            if s['key']['provider']=='B':
                self.assertEqual(w['reference']['available_months'],0)
                self.assertEqual(w['status'],'history_gap')
            else:
                self.assertEqual(w['status'],'current_source_gap')

    def test_constant_history_has_no_z_score_or_expensive_label(self):
        records=[saved(R.month_shift('2026-11',i),20) for i in range(-36,1)]
        w=self.view(records)['series'][0]['windows']['3']
        self.assertEqual(w['status'],'flat_history')
        self.assertEqual(w['reference']['sd'],0)
        self.assertIsNone(w['position']['z'])
        self.assertIsNone(w['position']['label'])

    def test_annual_roll_keeps_actual_period_and_does_not_become_NTM(self):
        points=self.view(history())['series'][0]['windows']['3']['points']
        self.assertEqual(next(p for p in points if p['month']=='2025-12')['forecast_period'],'2025')
        self.assertEqual(next(p for p in points if p['month']=='2026-01')['forecast_period'],'2026')

    def test_expired_or_non12_month_NTM_is_not_in_history(self):
        r=saved('2026-11');p=r['periods'][0];p.update(period_kind='NTM',period='provider_NTM')
        g=p['groups'][0];g['series'].update(period_kind='NTM',period='provider_NTM',period_start='2026-11-28',period_end='2027-11-27')
        w=self.view([r])['series'][0]['windows']['3']
        self.assertEqual(w['current']['pe'],20)
        g['series']['period_end']='2028-11-28'
        self.assertIsNone(self.view([r])['series'][0]['windows']['3']['current']['pe'])

    def test_saved_api_reads_verified_artifacts_without_mutating_or_calculating(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(db,'DATA',Path(tmp)):
            folder=S.home()/'calculations';folder.mkdir(parents=True)
            r=saved('2026-10');payload={'report':r,'sha256':S.digest(r)}
            target=folder/(r['id']+'.json');target.write_text(S.canonical(payload))
            corrupt=folder/('a'*32+'.json');corrupt.write_text('broken')
            baseline={str(p):p.read_bytes() for p in folder.iterdir()}
            with patch.object(V,'compute',side_effect=AssertionError('must not compute valuation')):
                out=R.report(as_of='2026-11-01T00:00:00+08:00')
            self.assertEqual(out['valuation']['id'],r['id'])
            self.assertEqual(len(out['warnings']),1)
            self.assertEqual(baseline,{str(p):p.read_bytes() for p in folder.iterdir()})
        with tempfile.TemporaryDirectory() as tmp,patch.object(db,'DATA',Path(tmp)):
            self.assertEqual(R.report()['status'],'not_saved')
            self.assertFalse(S.home().exists())


if __name__=='__main__':unittest.main()
