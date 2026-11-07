"""Observed TAIEX drawdown ladder, frozen separately from V6 forecasts.

This describes *realized* price damage, not a probability of further decline.
It is generated only during a chat-led V6 review and read without recalculation.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from . import db, v6_tx, v6_weather

LEVELS = ((30, '大股災'), (20, '中股災'), (10, '小股災'), (5, '迷你股災'))
SOURCE_URL = 'https://www.twse.com.tw/zh/indices/taiex/mi-5min-hist.html'


def _grade(pct):
    return next((label for threshold, label in LEVELS if pct >= threshold), '未達迷你股災')


def _week_key(day):
    value = date.fromisoformat(day)
    return (value - timedelta(days=value.weekday())).isoformat()


def _week_summary(rows):
    """The ordered fall cannot mistake a low before a later high for a selloff."""
    top = 0.0
    top_day = None
    drop = (0.0, None, None)
    high = max(row['high'] for _, row in rows)
    low = min(row['low'] for _, row in rows)
    for day, row in rows:
        # Daily OHLC does not reveal whether today's high preceded today's low.
        # A new high qualifies as an anchor only from the following day onward.
        if top:
            candidate = (top - row['low']) / top * 100
            if candidate > drop[0]:
                drop = (candidate, top_day, day)
        if row['high'] > top:
            top, top_day = row['high'], day
    return {'week': _week_key(rows[0][0]), 'first_day': rows[0][0], 'last_day': rows[-1][0],
            'open': rows[0][1]['open'], 'close': rows[-1][1]['close'],
            'high': high, 'low': low, 'range_points': high - low,
            'range_pct': (high - low) / high * 100,
            'ordered_fall_pct': drop[0], 'fall_peak_day': drop[1], 'fall_low_day': drop[2]}


def _ordered_peak_drop(rows):
    peak = 0.0
    peak_day = None
    worst = (0.0, None, None, None, None)
    for day, row in rows:
        if peak:
            drop = (peak - row['low']) / peak * 100
            if drop > worst[0]:
                worst = (drop, peak_day, day, peak, row['low'])
        if row['high'] > peak:
            peak, peak_day = row['high'], day
    return {'pct': worst[0], 'peak_day': worst[1], 'low_day': worst[2],
            'peak': worst[3], 'low': worst[4], 'level': _grade(worst[0])}


def _amplitude_summary(rows, weeks, week_keys, current_key, as_of):
    """Use prior closes as denominators; exclude the current week from its baseline."""
    daily = []
    for (_, prior), (day, bar) in zip(rows, rows[1:]):
        daily.append((day, (bar['high'] - bar['low']) / prior['close'] * 100))
    daily20 = sum(value for _, value in daily[-20:]) / 20 if len(daily) >= 20 else None
    daily5 = sum(value for _, value in daily[-5:]) / 5 if len(daily) >= 5 else None
    previous20 = sum(value for _, value in daily[-25:-5]) / 20 if len(daily) >= 25 else None

    week_amplitudes = {}
    for i, key in enumerate(week_keys):
        if not i:
            continue
        group = weeks[key]
        previous_close = weeks[week_keys[i-1]][-1][1]['close']
        week_amplitudes[key] = (max(bar['high'] for _, bar in group)
                                - min(bar['low'] for _, bar in group)) / previous_close * 100
    current_index = week_keys.index(current_key)
    earlier = week_keys[max(0, current_index-4):current_index]
    previous4 = (sum(week_amplitudes[key] for key in earlier) / 4
                 if len(earlier) == 4 and all(key in week_amplitudes for key in earlier) else None)
    current_amp = week_amplitudes.get(current_key)

    def color(key):
        group = weeks[key]
        first_open, last_close = group[0][1]['open'], group[-1][1]['close']
        return '紅' if last_close > first_open else '綠' if last_close < first_open else '平'

    current_color = color(current_key)
    current_confirmed = date.fromisoformat(as_of).weekday() == 4
    confirmed_keys = week_keys if current_confirmed else week_keys[:-1]
    confirmed_color = color(confirmed_keys[-1]) if confirmed_keys else '平'
    count = 0
    if confirmed_color != '平':
        for key in reversed(confirmed_keys):
            if color(key) != confirmed_color:
                break
            count += 1
    previous_keys = confirmed_keys[:-count] if count else confirmed_keys[:-1]
    previous_color = color(previous_keys[-1]) if previous_keys else '平'
    previous_count = 0
    if previous_color != '平':
        for key in reversed(previous_keys):
            if color(key) != previous_color:
                break
            previous_count += 1
    daily_expanding = daily5 > previous20 if daily5 is not None and previous20 is not None else None
    weekly_expanding = (current_amp > previous4 and current_color == '綠'
                        if current_amp is not None and previous4 is not None else None)
    green_streak = count >= 2 and confirmed_color == '綠'
    checks = (daily_expanding, weekly_expanding, green_streak)
    confirmations = sum(value is True for value in checks)
    return {
        'daily_average_20_pct': daily20, 'daily_recent_5_average_pct': daily5,
        'daily_previous_20_average_pct': previous20, 'daily_latest_pct': daily[-1][1] if daily else None,
        'weekly_current_pct': current_amp, 'weekly_previous_4_average_pct': previous4,
        'weekly_current_color': current_color, 'weekly_current_confirmed': current_confirmed,
        'weekly_confirmed_color': confirmed_color, 'weekly_confirmed_streak': count,
        'weekly_previous_color': previous_color, 'weekly_previous_streak': previous_count,
        'daily_expanding': daily_expanding, 'weekly_expanding': weekly_expanding,
        'green_streak': green_streak, 'confirmations': confirmations,
        'readiness': '完整' if daily_expanding is not None and weekly_expanding is not None else '資料不足',
    }


def build(index, *, as_of, generated_at, observation_window=None):
    v6_weather._date(as_of)
    rows = sorted((day, bar) for day, bar in index.items() if day <= as_of)
    if not rows or rows[-1][0] != as_of:
        raise ValueError('所選日期缺少完整加權指數行情')
    for day, bar in rows:
        prices = [bar.get(key) for key in ('open', 'high', 'low', 'close')]
        if any(not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0 for value in prices):
            raise ValueError('加權指數行情含無效價格：' + day)
        if bar['high'] < bar['low'] or bar['high'] < max(bar['open'], bar['close']) or bar['low'] > min(bar['open'], bar['close']):
            raise ValueError('加權指數高低價格異常：' + day)
    weeks = defaultdict(list)
    for day, bar in rows:
        weeks[_week_key(day)].append((day, bar))
    week_keys = sorted(weeks)
    current_key = _week_key(as_of)
    current_week = _week_summary(weeks[current_key])
    amplitude = _amplitude_summary(rows, weeks, week_keys, current_key, as_of)
    earlier = week_keys[max(0, week_keys.index(current_key)-4):week_keys.index(current_key)]
    prior_weeks = [_week_summary(weeks[key]) for key in earlier]
    prior_avg = sum(row['range_pct'] for row in prior_weeks) / 4 if len(prior_weeks) == 4 else None
    prior_fall_avg = sum(row['ordered_fall_pct'] for row in prior_weeks) / 4 if len(prior_weeks) == 4 else None
    week_ratio = current_week['range_pct'] / prior_avg if prior_avg else None
    fall_ratio = current_week['ordered_fall_pct'] / prior_fall_avg if prior_fall_avg else None
    # A wide upside week does not qualify; use high-before-low and a weak close.
    weekly_stress = bool(prior_fall_avg is not None and current_week['ordered_fall_pct'] >= 5
                         and current_week['ordered_fall_pct'] > prior_fall_avg
                         and current_week['close'] < current_week['open'])
    latest = rows[-1][1]
    top_day, top_price = max(((day, bar['high']) for day, bar in rows), key=lambda item: item[1])
    since_peak = [(day, bar) for day, bar in rows if day >= top_day]
    low_day, low_price = min(((day, bar['low']) for day, bar in since_peak), key=lambda item: item[1])
    close_dd = (top_price - latest['close']) / top_price * 100
    worst_dd = (top_price - low_price) / top_price * 100
    current_level = _grade(close_dd)
    if current_level == '未達迷你股災' and weekly_stress:
        current_level = '迷你股災'
    examples = {}
    for year in (2020, 2022):
        annual = [(day, bar) for day, bar in rows if day.startswith(str(year))]
        if annual:
            examples[str(year)] = _ordered_peak_drop(annual)
    observed_window = None
    if observation_window:
        start, end = observation_window
        if start not in index or end not in index or not start < end <= as_of:
            raise ValueError('觀察區間需為已完成且已保存的兩個交易日')
        start_close = index[start]['close']
        end_close = index[end]['close']
        observed_window = {'start': start, 'end': end, 'start_close': start_close,
                           'end_close': end_close, 'close_change_points': end_close-start_close,
                           'close_change_pct': (end_close/start_close-1)*100}
    return {'kind': 'observed_crash_scale', 'as_of': as_of, 'generated_at': generated_at,
            'source': '本機保存的加權指數日線（開高低收）', 'source_url': SOURCE_URL,
            'current_level': current_level, 'weekly_stress': weekly_stress,
            'amplitude': amplitude,
            'current_week': current_week, 'prior_four_weeks': prior_weeks,
            'prior_four_week_average_range_pct': prior_avg, 'weekly_range_multiple': week_ratio,
            'prior_four_week_average_fall_pct': prior_fall_avg, 'weekly_fall_multiple': fall_ratio,
            'cycle_peak': {'date': top_day, 'high': top_price},
            'cycle_low': {'date': low_day, 'low': low_price},
            'latest_close': latest['close'], 'close_drawdown_pct': close_dd,
            'cycle_worst_drawdown_pct': worst_dd,
            'cycle_worst_level': _grade(worst_dd), 'examples': examples,
            'observed_window': observed_window,
            'levels': [{'threshold_pct': threshold, 'label': label} for threshold, label in reversed(LEVELS)],
            'note': '自保存資料起的週期高點至目前收盤回撤作為當前級距；本輪盤中最大回撤另列。迷你級亦可由相對前四個完整交易週擴大的當週下跌觸發。這是使用者自訂的跌勢觀察級距，描述已發生的價格變動；不預測下一交易日或事件是否擴大。'}


def main():
    parser = argparse.ArgumentParser(description='Save V6 observed drawdown grades during manual review')
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--window-start')
    parser.add_argument('--window-end')
    parser.add_argument('--output', type=Path, default=db.DATA / 'v6_crash_scale.json')
    args = parser.parse_args()
    if bool(args.window_start) != bool(args.window_end):
        parser.error('--window-start 與 --window-end 必須一起提供')
    raw, _ = v6_tx._complete_input()
    result = build(raw['index'], as_of=args.as_of,
                   generated_at=datetime.now(v6_weather.TZ).isoformat(timespec='seconds'),
                   observation_window=(args.window_start, args.window_end) if args.window_start else None)
    temp = args.output.with_suffix('.tmp')
    temp.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    temp.replace(args.output)
    print(json.dumps({'as_of': result['as_of'], 'level': result['current_level'],
                      'weekly_range_pct': round(result['current_week']['range_pct'], 2)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
