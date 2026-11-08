"""Package 4: point-in-time monthly forward-PE history from saved calculations.

Only reads package-3 artifacts. No quotes/estimates are fetched or recomputed.
Missing months, unqualified baskets, and model versions are never spliced.
"""
from __future__ import annotations

from datetime import date
import statistics

from . import forward_pe_store as S, forward_pe_valuation as V

VERSION = 'forward-pe-river.v1'
COLORS = ['#4e9179', '#6c9db6', '#d3bc6b', '#cf9860', '#b76562']
LABELS = ['相對便宜', '偏低', '中間區間', '偏高', '相對昂貴']


def month_shift(month, offset):
    y, m = divmod(int(month[:4]) * 12 + int(month[5:7]) - 1 + offset, 12)
    return f'{y:04d}-{m + 1:02d}'


def group_key(report, group, role):
    # Annual roles intentionally roll fiscal labels; NTM keeps a constant
    # 12-month horizon, not identical start/end dates across observation months.
    fields = ('source_provider', 'provider', 'currency', 'eps_basis', 'share_basis', 'statistic', 'period_kind')
    return {'method_version': report['method_version'], 'policy_sha256': report['policy_sha256'],
            'role': role, **{k: group['series'].get(k) for k in fields}}


def groups(report):
    year = str(S.instant(report['information_cutoff']).year)
    for period in report['periods']:
        role = 'NTM' if period['period_kind'] == 'NTM' else (
            'current_year' if period['period'] == year else
            'next_year' if period['period'] == str(int(year) + 1) else None)
        if role:
            for g in period['groups']:
                yield group_key(report, g, role), g


def observation(report, group, role):
    a = group['qualified'] if group else {}
    pe = a.get('exact_top50_pe')
    reasons = []
    if not group:
        reasons.append('該保存版本缺少同口徑來源')
    if not a.get('gate_passed') or a.get('names') != 50 or pe is None or pe <= 0:
        pe = None
        reasons.append('河流圖需50檔均通過資格，不沿用缺漏子集')
    if role == 'NTM' and group:
        try:
            start, end = [date.fromisoformat(group['series'][k]) for k in ('period_start', 'period_end')]
            cutoff = S.instant(report['information_cutoff']).date()
            if not 330 <= (end - start).days <= 400 or abs((start - cutoff).days) > 31:
                raise ValueError('NTM horizon')
        except (KeyError, TypeError, ValueError):
            pe = None
            reasons.append('供應商NTM起訖非相近的未來12個月')
    return {'month': report['information_cutoff'][:7], 'information_cutoff': report['information_cutoff'],
            'computed_at': report['computed_at'], 'price_date': report['price_date'],
            'weight_date': report['weight_date'], 'report_id': report['id'], 'snapshot_id': report['snapshot_id'],
            'pe': pe, 'forecast_period': group['series'].get('period') if group else None,
            'qualified_names': a.get('names', 0), 'reasons': reasons}


def reference(month, months, observed):
    expected = [month_shift(month, i) for i in range(-months, 0)]
    usable = [observed[m]['pe'] for m in expected if m in observed and observed[m]['pe'] is not None]
    missing = [m for m in expected if m not in observed or observed[m]['pe'] is None]
    result = {'required_months': months, 'available_months': len(usable), 'missing_months': missing,
              'mean': None, 'sd': None, 'cuts': None, 'values': usable}
    if not missing:
        result['mean'] = statistics.mean(usable)
        result['sd'] = statistics.stdev(usable)
        if result['sd'] > 0:
            result['cuts'] = [result['mean'] + s * result['sd'] for s in (-1, -.5, .5, 1)]
    return result


def window(observed, month, years):
    ref = reference(month, years * 12, observed)
    current = observed.get(month)
    current_pe = current.get('pe') if current else None
    points = []
    for m in [month_shift(month, i) for i in range(-years * 12, 1)]:
        p = observed.get(m, {'month': m, 'pe': None, 'reasons': ['當月沒有合格保存資料']})
        past = reference(m, years * 12, observed)
        points.append({**p, 'cuts': past['cuts']})
    z = percentile = label = color = None
    if current_pe is not None and ref['cuts'] is not None:
        z = (current_pe - ref['mean']) / ref['sd']
        percentile = 100 * (sum(v < current_pe for v in ref['values']) + .5 * sum(v == current_pe for v in ref['values'])) / len(ref['values'])
        category = sum(current_pe > c for c in ref['cuts'])
        label, color = LABELS[category], COLORS[category]
    status = ('current_source_gap' if current_pe is None else 'history_gap' if ref['missing_months'] else
              'flat_history' if ref['cuts'] is None else 'ready')
    return {'years': years, 'status': status, 'points': points, 'current': current,
            'reference': {k: v for k, v in ref.items() if k != 'values'},
            'position': {'z': z, 'percentile': percentile, 'label': label, 'color': color},
            'observed_months': sum(p['pe'] is not None for p in points)}


def analyze(reports, *, as_of):
    """Pure analysis; reconstructed calculations must already exist by as_of."""
    cutoff = S.instant(as_of)
    visible = [r for r in reports if S.instant(r['information_cutoff']) <= S.instant(r['computed_at']) <= cutoff]
    visible.sort(key=lambda r: (S.instant(r['information_cutoff']), S.instant(r['computed_at']), r['id']))
    if not visible:
        return {'status': 'not_saved', 'version': VERSION, 'as_of': as_of, 'valuation': None, 'series': []}
    latest = visible[-1]
    month = latest['information_cutoff'][:7]
    profiles = {}
    for r in visible:
        for key, g in groups(r):
            profiles[S.digest(key)[:20]] = key
    series = []
    for ident, key in profiles.items():
        observed = {}
        for r in visible:
            g = next((g for k, g in groups(r) if k == key), None)
            observed[r['information_cutoff'][:7]] = observation(r, g, key['role'])
        series.append({'id': ident, 'key': key, 'windows': {str(y): window(observed, month, y) for y in (3, 5)}})
    return {'version': VERSION, 'status': 'ok', 'as_of': as_of, 'valuation': latest,
            'series': series, 'saved_calculations': len(visible), 'history_month': month,
            'notes': [
                '每月取資訊截點最晚的已保存版本；這是保存月觀測，不冒稱交易所月末估值。',
                '同月有多個版本只計一個月；新版本缺資料時斷線，不回用較早合格版本。',
                '位階使用當月之前連續36／60個完整保存月；當月不進自己的平均與標準差。',
                '河流每一點都只使用其之前月份的資料，缺月斷線，不以今天EPS重建過去。',
                '年度系列依當年／次年切換年度，年初換年斷線；估值會受剩餘預估期間影響，並非NTM。',
                '五色是歷史平均±0.5／±1個樣本標準差，不是報酬機率、信賴區間或買賣訊號。',
                '不同來源、每股口徑、估值方法或規則版本不混合；只用完整50檔通過資格的估值。',
                '前50成分隨官方月底名單變動，不是固定公司歷史，也不是全大盤。',
                '尚未保存或當時未通過資格的月份，不用事後取得的舊新聞補成已知歷史。',
            ]}


def report(*, as_of=None):
    cutoff = S.instant(as_of or S.now()).isoformat()
    reports, warnings = [], []
    folder = S.home() / 'calculations'
    if folder.exists():
        for path in sorted(folder.glob('*.json')):
            if path.stem == 'latest':
                continue
            try:
                reports.append(V.read(path.stem))
            except (ValueError, KeyError, OSError) as error:
                warnings.append({'id': path.stem, 'message': '保存檔損壞或驗證失敗，未納入：' + str(error)})
    result = analyze(reports, as_of=cutoff)
    result['warnings'] = warnings
    return result
