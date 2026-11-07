"""Read-only holding-time summaries of completed, costed strategy trades."""
from __future__ import annotations

import math
from datetime import date


def _number(value):
    return (float(value) if not isinstance(value, bool)
            and isinstance(value, (int, float)) and math.isfinite(value) else None)


def _day(value):
    try:
        return value if isinstance(value, str) and date.fromisoformat(value).isoformat() == value else None
    except ValueError:
        return None


def summarize(trades, calendar=(), *, as_of=None):
    """Equal-weight completed trades; keep zero-day trades and omit open marks.

    Durations follow the existing trade tables: elapsed trading sessions,
    excluding entry day. Calendar fallback is needed by saved V3 accounts.
    Never mutate a model, value a position, or reconcile an account here.
    """
    index = {day: i for i, day in enumerate(sorted(set(calendar)))}
    groups = {'win': [], 'loss': []}
    completed = missing = flat = 0
    cutoff = _day(as_of)
    for trade in trades or []:
        if not isinstance(trade, dict):
            continue
        exit_day = _day(trade.get('exit_date'))
        if not exit_day or cutoff and exit_day > cutoff:
            continue
        completed += 1
        entry_day = _day(trade.get('entry_date'))
        result = next((value for key in ('pnl', 'net_return_pct', 'return_pct', 'pnl_points')
                       if (value := _number(trade.get(key))) is not None), None)
        duration = _number(trade.get('holding_sessions'))
        if duration is None or duration < 0:
            duration = (index[exit_day] - index[entry_day]
                        if entry_day in index and exit_day in index else None)
        if not entry_day or exit_day < entry_day or result is None or duration is None or duration < 0:
            missing += 1
            continue
        if result == 0:
            flat += 1
        else:
            groups['win' if result > 0 else 'loss'].append(duration)
    return {
        'as_of': cutoff,
        'win': {'samples': len(groups['win']),
                'avg_days': round(sum(groups['win']) / len(groups['win']), 4) if groups['win'] else None},
        'loss': {'samples': len(groups['loss']),
                 'avg_days': round(sum(groups['loss']) / len(groups['loss']), 4) if groups['loss'] else None},
        'completed_count': completed, 'flat_count': flat, 'unavailable_count': missing,
        'method': '已完成交易等權平均，依成本後損益分組；交易日不含進場日，同日買賣為0日；未平倉與損益兩平不混入獲利／虧損平均。',
    }
