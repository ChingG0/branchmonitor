"""Package 3: reproducible valuation of a dated top-50 membership basket.

No fetch, strategy publication, historical backfill, or calculation on GET.
Unknown per-share bases remain explicitly unqualified mechanical previews.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import uuid
from datetime import date
from pathlib import Path

from . import forward_pe_store as S

POLICY_PATH = S.db.ROOT / 'config/forward_pe/calculation.json'
GROUP_FIELDS = ('source_provider', 'provider', 'currency', 'eps_basis',
                'share_basis', 'period_kind', 'period', 'statistic',
                'period_start', 'period_end')


def policy():
    return json.loads(POLICY_PATH.read_text())


def _number(value, *, positive=False):
    try:
        return S.finite(value, positive=positive)
    except (TypeError, ValueError, OverflowError):
        return None


def _visible(value, cutoff):
    try:
        return bool(value) and S.instant(value) <= cutoff
    except (ValueError, TypeError):
        return False


def _signature(estimate):
    return tuple(estimate.get(k) for k in GROUP_FIELDS)


def _quote_row(member, quote, quote_date, cutoff):
    reasons = []
    q = quote or {}
    close = _number(q.get('close'), positive=True)
    shares = _number(q.get('shares'), positive=True)
    if close is None:
        reasons.append('missing_or_invalid_price')
    if shares is None:
        reasons.append('missing_or_invalid_shares')
    if q.get('market_date') != quote_date:
        reasons.append('price_date_not_aligned')
    if q.get('shares_date', q.get('market_date')) != quote_date:
        reasons.append('shares_date_not_aligned')
    if q.get('currency') != 'TWD' or q.get('price_basis') != 'raw_close':
        reasons.append('price_currency_or_basis_mismatch')
    if q.get('shares_unit') != 'shares' or q.get('shares_basis') != 'official_issued_shares':
        reasons.append('issued_share_basis_unverified')
    if not _visible(q.get('known_at'), cutoff):
        reasons.append('quote_not_known_at_cutoff')
    if not quote_date or quote_date > cutoff.date().isoformat():
        reasons.append('future_or_missing_price_date')
    return {**member, 'quote': q or None, 'quote_issues': reasons,
            'market_cap_twd': close * shares if not reasons else None}


def _estimate_issues(e, cutoff, rules, quote_date):
    reasons = []
    try:
        S.validate_estimate(e)
    except (KeyError, ValueError, TypeError):
        reasons.append('invalid_estimate')
    if not _visible(e.get('known_at'), cutoff) or not _visible(e.get('published_at'), cutoff):
        reasons.append('estimate_not_known_at_cutoff')
    if e.get('currency') != 'TWD':
        reasons.append('eps_currency_mismatch')
    # Diluted EPS requires its own diluted denominator. Multiplying it by
    # ordinary issued shares cannot silently stand in for aggregate profit.
    if e.get('eps_basis') != rules['qualified_eps_basis']:
        reasons.append('basic_eps_basis_not_verified')
    if e.get('share_basis') != rules['qualified_share_basis'] or not e.get('basis_evidence'):
        reasons.append('current_per_share_adjustment_not_verified')
    evidence = e.get('basis_evidence')
    adjustment_date = e.get('share_basis_date') or (evidence.get('share_basis_date') if isinstance(evidence, dict) else None)
    if adjustment_date != quote_date:
        reasons.append('per_share_adjustment_date_not_aligned')
    if e.get('period_kind') == 'NTM':
        try:
            start, end = date.fromisoformat(e['period_start']), date.fromisoformat(e['period_end'])
            if end <= cutoff.date() or end <= start:
                reasons.append('NTM_period_expired_or_invalid')
        except (KeyError, TypeError, ValueError):
            reasons.append('NTM_period_expired_or_invalid')
    try:
        age = (cutoff.date() - date.fromisoformat(e['forecast_as_of'])).days
        if age < 0 or age > rules['gate']['max_forecast_age_days']:
            reasons.append('forecast_outside_freshness_window')
    except (KeyError, TypeError, ValueError):
        reasons.append('provider_forecast_date_missing')
    return reasons


def _aggregate(rows, chosen, *, qualified, rules):
    usable = [r for r in rows if r['symbol'] in chosen and
              r['market_cap_twd'] is not None and
              (not qualified or chosen[r['symbol']]['qualified'])]
    caps_complete = all(r['market_cap_twd'] is not None for r in rows)
    total_cap = sum(r['market_cap_twd'] for r in rows) if caps_complete else None
    cap = sum(r['market_cap_twd'] for r in usable)
    profit = sum(chosen[r['symbol']]['eps_times_issued_shares_twd'] for r in usable)
    original_weight = sum(r['weight_pct'] for r in usable)
    all_weight = sum(r['weight_pct'] for r in rows)
    coverage = original_weight / all_weight * 100
    cap_coverage = cap / total_cap * 100 if total_cap else None
    criteria = {
        'min_names': len(usable) >= rules['gate']['min_names'],
        'original_weight_coverage': coverage >= rules['gate']['min_original_basket_weight_coverage_pct'],
        'revalued_cap_coverage': cap_coverage is not None and cap_coverage >= rules['gate']['min_revalued_basket_cap_coverage_pct'],
        'top_rank_present': not rules['gate']['require_top_rank'] or any(r['rank'] == 1 for r in usable),
        'complete_price_denominator': caps_complete,
        'positive_net_forecast_earnings': bool(usable) and profit > 0,
    }
    passed = qualified and all(criteria.values())
    pe = cap / profit if usable and profit > 0 else None
    exact = qualified and len(usable) == rules['basket_size'] and caps_complete
    return {
        'names': len(usable), 'symbols': [r['symbol'] for r in usable],
        'missing_symbols': [r['symbol'] for r in rows if r not in usable],
        'matched_market_cap_twd': cap if usable else None,
        'net_forecast_earnings_twd': profit if usable else None,
        'earnings_yield_pct': profit / cap * 100 if usable and cap else None,
        'matched_subset_pe': pe,
        'pe_undefined_reason': None if pe is not None else ('no_eligible_names' if not usable else 'nonpositive_net_earnings'),
        'negative_eps_names': sum(chosen[r['symbol']]['estimate']['value'] < 0 for r in usable),
        'zero_eps_names': sum(chosen[r['symbol']]['estimate']['value'] == 0 for r in usable),
        'original_index_weight_pct': original_weight,
        'original_basket_weight_coverage_pct': coverage,
        'revalued_basket_cap_coverage_pct': cap_coverage,
        'full_basket_market_cap_twd': total_cap,
        'gate_passed': bool(passed), 'criteria': criteria,
        'adopted_subset_pe': pe if passed else None,
        'exact_top50_pe': pe if exact and passed else None,
        'label': ('前50完整固定成分籃子' if exact and passed else
                  '通過涵蓋門檻的已覆蓋子集，並非完整前50' if passed else
                  '未通過資格／涵蓋門檻，不作正式估值'),
        'is_full_market': False,
    }


def calculate(members, quotes, estimates, *, quote_date, cutoff, rules=None):
    """Pure calculation. EPS series are separated before any aggregation."""
    rules = rules or policy()
    cutoff = S.instant(cutoff)
    if len(members) != rules['basket_size'] or len({r['symbol'] for r in members}) != len(members):
        raise ValueError('須為不重複的前50成分籃子')
    if sorted(r['rank'] for r in members) != list(range(1, rules['basket_size'] + 1)):
        raise ValueError('前50排名不連續')
    for m in members:
        S.finite(m['weight_pct'], positive=True)
    rows = [_quote_row(m, quotes.get(m['symbol']), quote_date, cutoff) for m in members]
    symbols = {r['symbol'] for r in rows}
    grouped = {}
    rejected = []
    for e in estimates:
        if e['symbol'] not in symbols:
            continue
        if not _visible(e.get('known_at'), cutoff) or not _visible(e.get('published_at'), cutoff):
            rejected.append({'id': e.get('id'), 'symbol': e['symbol'], 'reason': 'not_known_at_cutoff'})
            continue
        grouped.setdefault(_signature(e), {}).setdefault(e['symbol'], []).append(e)
    results = []
    for sig, series in sorted(grouped.items(), key=lambda item: S.canonical(item[0])):
        chosen = {}
        details = []
        for row in rows:
            versions = series.get(row['symbol'], [])
            if not versions:
                details.append({**row, 'estimate': None, 'issues': ['missing_forecast'], 'qualified': False})
                continue
            latest = max((S.instant(e['published_at']), S.instant(e['known_at'])) for e in versions)
            tied = [e for e in versions if (S.instant(e['published_at']), S.instant(e['known_at'])) == latest]
            if len({S.canonical(e['value']) for e in tied}) != 1:
                details.append({**row, 'estimate': None, 'conflicting_estimate_ids': [e.get('id') for e in tied],
                                'issues': ['conflicting_latest_estimates'], 'qualified': False})
                continue
            e = sorted(tied, key=lambda v: v.get('id', ''))[-1]
            issues = row['quote_issues'] + _estimate_issues(e, cutoff, rules, quote_date)
            eps = _number(e.get('value'))
            mechanical = row['market_cap_twd'] is not None and eps is not None and e.get('currency') == 'TWD'
            previous = [v for v in versions if S.instant(v['published_at']) < S.instant(e['published_at'])]
            revision = None
            if previous:
                prior_time = max(S.instant(v['published_at']) for v in previous)
                prior = [v for v in previous if S.instant(v['published_at']) == prior_time]
                # Conflicting prior versions cannot produce an invented revision.
                if len({S.canonical(v['value']) for v in prior}) == 1:
                    p = max(prior, key=lambda v: S.instant(v['known_at']))
                    old = p['value']
                    revision = {'previous_id': p.get('id'), 'previous_value': old,
                                'change': eps - old,
                                'change_pct': (eps / old - 1) * 100 if old > 0 else None,
                                'basis_comparability_verified': (e['eps_basis'] == rules['qualified_eps_basis'] and
                                    e['share_basis'] == rules['qualified_share_basis'] and
                                    bool(e.get('basis_evidence')) and bool(p.get('basis_evidence')) and
                                    (e.get('share_basis_date') or (e['basis_evidence'].get('share_basis_date') if isinstance(e.get('basis_evidence'), dict) else None)) ==
                                    (p.get('share_basis_date') or (p['basis_evidence'].get('share_basis_date') if isinstance(p.get('basis_evidence'), dict) else None)) == quote_date),
                                'note': '同標記的已保存前版；未知每股口徑只作未核對變化試算；前值非正時不算變化率'} if eps is not None else None
            entry = {**row, 'estimate': e, 'issues': issues, 'qualified': not issues,
                     'mechanical_preview_only': bool(issues),
                     'individual_pe': row['quote']['close'] / eps if mechanical and eps > 0 else None,
                     'individual_pe_undefined_reason': None if mechanical and eps > 0 else 'nonpositive_eps_or_missing_input',
                     'eps_times_issued_shares_twd': eps * row['quote']['shares'] if mechanical else None,
                     'earnings_yield_pct': eps / row['quote']['close'] * 100 if mechanical else None,
                     'revision': revision}
            details.append(entry)
            if mechanical:
                chosen[row['symbol']] = entry
        preview = _aggregate(rows, chosen, qualified=False, rules=rules)
        qualified = _aggregate(rows, chosen, qualified=True, rules=rules)
        results.append({'series': dict(zip(GROUP_FIELDS, sig)), 'rows': details,
                        'mechanical_preview': preview, 'qualified': qualified})
    return {'groups': results, 'quote_rows': rows, 'rejected_after_cutoff': rejected,
            'quote_names': sum(r['market_cap_twd'] is not None for r in rows),
            'basket_names': len(rows)}


def _load(snapshot_id, cutoff):
    snap = S.read_snapshot(snapshot_id)
    if not snap:
        raise ValueError('找不到資料快照')
    stamp = S.instant(cutoff or snap['created_at'])
    if stamp != S.instant(snap['created_at']):
        raise ValueError('包3只重算明確快照截點；不可將今日採集快照倒填過去或混入之後的資料')
    refs = snap['payload']
    with S.connect() as c:
        u = dict(c.execute('SELECT * FROM universes WHERE id=?', (refs['universe_id'],)).fetchone())
        if not _visible(u['known_at'], stamp) or u['effective_date'] > stamp.date().isoformat():
            raise ValueError('成分股版本在截點尚不可用')
        members = sorted(json.loads(u['payload']), key=lambda r: r['rank'])[:50]
        quotes = {}
        estimates = []
        sources = {u['source_id']}
        for table, ids in [('quotes', refs['quote_ids']), ('estimates', refs['estimate_ids'])]:
            for record_id in ids:
                record = c.execute(f'SELECT * FROM {table} WHERE id=?', (record_id,)).fetchone()
                if record is None:
                    raise ValueError('快照參照缺漏')
                src = S.get_source(c, record['source_id'])
                sources.add(src['id'])
                v = {**json.loads(record['payload']), 'id': record['id'], 'source_id': src['id'],
                     'known_at': record['known_at'], 'source_provider': src['provider'], 'source_url': src['url']}
                if table == 'quotes':
                    if v['symbol'] in quotes:
                        raise ValueError('快照含同股多筆行情，須先明確選擇版本')
                    quotes[v['symbol']] = v
                else:
                    estimates.append(v)
        manifest = [dict(c.execute('SELECT * FROM sources WHERE id=?', (sid,)).fetchone()) for sid in sorted(sources)]
    for src in manifest:
        path = S.home() / 'raw' / (src['sha256'] + '.gz')
        if hashlib.sha256(gzip.decompress(path.read_bytes())).hexdigest() != src['sha256']:
            raise ValueError('來源原檔SHA不符')
    quote_date = max((q['market_date'] for q in quotes.values()), default=None)
    if quote_date and quote_date < u['effective_date']:
        raise ValueError('股價早於成分股生效日')
    return snap, u, members, quotes, estimates, manifest, quote_date


def compute(*, snapshot_id=None, cutoff=None):
    """Explicit pure build from exact immutable snapshot IDs, no global latest EPS."""
    rules = policy()
    snap, u, members, quotes, estimates, sources, quote_date = _load(snapshot_id, cutoff)
    year = str(S.instant(snap['created_at']).year)
    selected = [e for e in estimates if e['statistic'] == rules['statistic'] and
                (e['period_kind'] == 'calendar_year' and e['period'] in (year, str(int(year) + 1)) or
                 e['period_kind'] == 'NTM')]
    result = calculate(members, quotes, selected, quote_date=quote_date, cutoff=snap['created_at'], rules=rules)
    # FY and NTM are never manufactured from calendar-year estimates.
    periods = []
    for kind, period in [('calendar_year', year), ('calendar_year', str(int(year) + 1)), ('NTM', None)]:
        groups = [g for g in result['groups'] if g['series']['period_kind'] == kind and
                  (period is None or g['series']['period'] == period)]
        periods.append({'period_kind': kind, 'period': period, 'groups': groups,
                        'status': 'qualified' if any(g['qualified']['gate_passed'] for g in groups) else 'source_gap',
                        'document_names': len({r['symbol'] for g in groups for r in g['rows'] if r['estimate']}),
                        'largest_qualified_series_names': max((g['qualified']['names'] for g in groups), default=0),
                        'missing_forecast_symbols': [m['symbol'] for m in members if not any(r['symbol'] == m['symbol'] and r['estimate'] for g in groups for r in g['rows'])]})
    return {
        'method_version': rules['version'], 'computed_at': S.now(),
        'information_cutoff': snap['created_at'], 'snapshot_id': snap['id'],
        'snapshot_sha256': S.digest(snap), 'policy': rules, 'policy_sha256': S.digest(rules),
        'engine_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'source_manifest': sources, 'input_quote_ids': snap['payload']['quote_ids'],
        'input_estimate_ids': snap['payload']['estimate_ids'],
        'weight_date': u['effective_date'], 'price_date': quote_date,
        'basket_original_index_weight_pct': sum(m['weight_pct'] for m in members),
        'basket_label': '月底排名前50固定成分，以同日收盤及發行股數重估；非官方即時權重或全大盤',
        'basket_names': len(members), 'quote_names': result['quote_names'],
        'periods': periods, 'status': 'qualified' if any(p['status'] == 'qualified' for p in periods) else 'source_gap',
        'notes': [
            '未知EPS不是0；每個子集只用相同股票的市值及預估獲利。虧損和0EPS均保留。',
            '原始月底權重與按行情日重估的籃子市值涵蓋率分開；完整50檔也不是全大盤。',
            '機械試算不代表口徑已核對：來源／基本每股／調整證據／預估日期任一不足均不採用。',
            'basic EPS搭配已核對的每股調整；diluted或normalized不得默認當作普通股股數對應獲利。',
            '不同共識來源、期間、均值／中位數、每股口徑及NTM起訖不混合加總。',
            '只有涵蓋門檻通過才有採用估值；不足50檔時仍只稱已覆蓋子集。',
            '此資料截點為實際入庫快照，不是行情日盤後當時預報；新聞可能晚於行情日。',
            '本包沒有5年位階／河流區間，未用今日EPS回填歷史，未發布策略。',
            '目前為Cloud本機工作暫存；保存不代表已完成Drive讀回驗證。',
        ],
    }


def _artifact_path(report_id):
    if not re.fullmatch(r'[0-9a-f]{32}', report_id):
        raise ValueError('報告ID格式錯誤')
    return S.home() / 'calculations' / (report_id + '.json')


def save(*, snapshot_id=None):
    report = compute(snapshot_id=snapshot_id)
    report['id'] = uuid.uuid4().hex
    envelope = {'sha256': S.digest(report), 'report': report}
    path = _artifact_path(report['id'])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        f.write(S.canonical(envelope))
    csv_path = path.with_suffix('.csv')
    columns = ['method_version', 'information_cutoff', 'price_date', 'weight_date', 'period_kind', 'period',
               'source_provider', 'provider', 'eps_basis', 'share_basis', 'rank', 'symbol', 'name',
               'close', 'shares', 'market_cap_twd', 'eps', 'individual_pe', 'eps_times_issued_shares_twd',
               'qualified', 'issues', 'published_at', 'forecast_as_of', 'known_at', 'source_url', 'estimate_id']
    with csv_path.open('x', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for period in report['periods']:
            for group in period['groups']:
                for row in group['rows']:
                    e, q = row.get('estimate') or {}, row.get('quote') or {}
                    flat = {**group['series'], **row, **{k: report[k] for k in ('method_version', 'information_cutoff', 'price_date', 'weight_date')},
                            'close': q.get('close'), 'shares': q.get('shares'), 'eps': e.get('value'),
                            'published_at': e.get('published_at'), 'forecast_as_of': e.get('forecast_as_of'),
                            'known_at': e.get('known_at'), 'source_url': e.get('source_url'), 'estimate_id': e.get('id'),
                            'issues': '|'.join(row['issues'])}
                    writer.writerow({k: flat.get(k) for k in columns})
    S.audit({'kind': 'forward_pe_valuation_saved', 'method_version': report['method_version'],
             'report_id': report['id'], 'snapshot_id': report['snapshot_id'], 'sha256': envelope['sha256'],
             'csv_sha256': hashlib.sha256(csv_path.read_bytes()).hexdigest()})
    pointer = path.parent / ('latest-' + report['id'] + '.tmp')
    pointer.write_text(S.canonical({'id': report['id'], 'sha256': envelope['sha256']}))
    pointer.replace(path.parent / 'latest.json')
    return {'id': report['id'], 'status': report['status'], 'report_path': str(path),
            'csv_path': str(csv_path), 'sha256': envelope['sha256']}


def read(report_id=None):
    """Read and verify an already saved calculation; never creates directories."""
    expected_sha = None
    if report_id is None:
        pointer = S.home() / 'calculations/latest.json'
        if not pointer.exists():
            return {'status': 'not_saved', 'message': '尚未手動保存包3計算'}
        latest = json.loads(pointer.read_text())
        report_id, expected_sha = latest['id'], latest['sha256']
    path = _artifact_path(report_id)
    if not path.exists():
        return {'status': 'not_saved', 'message': '沒有這份保存報告'}
    envelope = json.loads(path.read_text())
    actual = S.digest(envelope['report'])
    if actual != envelope['sha256'] or expected_sha and actual != expected_sha:
        raise ValueError('保存計算SHA不符')
    return envelope['report']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('build', 'read'))
    parser.add_argument('--snapshot-id')
    parser.add_argument('--id')
    args = parser.parse_args()
    print(json.dumps(save(snapshot_id=args.snapshot_id) if args.command == 'build' else read(args.id), ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
