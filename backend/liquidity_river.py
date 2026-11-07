"""Rolling price/turnover river using TWSE turnover less one-sided day trades."""
from __future__ import annotations
import calendar
import json
import math
import statistics
import threading
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from . import db, margin_balance, market_margin_maintenance

TZ = ZoneInfo('Asia/Taipei')
SOURCE = 'https://www.twse.com.tw/zh/trading/historical/fmtqik.html'
DAY_TRADE_SOURCE = 'https://www.twse.com.tw/zh/trading/day-trading/twtb4u-month.html'
FIELDS_MARKET = ['日期','成交股數','成交金額','成交筆數','發行量加權股價指數','漲跌點數']
FIELDS_DAY_TRADE = ['日期','當日沖銷交易總成交股數','當日沖銷交易總成交股數占市場比重%',
                    '當日沖銷交易總買進成交金額','當日沖銷交易總買進成交金額占市場比重%',
                    '當日沖銷交易總賣出成交金額','當日沖銷交易總賣出成交金額占市場比重%']
_lock = threading.Lock()


def month_shift(day, n):
    y, m = divmod(day.year * 12 + day.month - 1 + n, 12)
    return day.replace(year=y, month=m + 1, day=min(day.day, calendar.monthrange(y, m + 1)[1]))


def value(x):
    n = float(str(x).replace(',', '').strip())
    if not math.isfinite(n) or n <= 0:
        raise ValueError('官方成交資料含無效數值')
    return n


def iso(raw, month):
    y, m, d = [int(x) for x in raw.split('/')]
    result = date(y + 1911, m, d).isoformat()
    if result[:7] != month:
        raise ValueError('官方成交資料月份不符')
    return result


def parse_market(raw, month):
    data = json.loads(raw)
    if data.get('stat') != 'OK' or data.get('fields') != FIELDS_MARKET:
        raise ValueError('證交所大盤成交欄位改變')
    rows = {}
    for row in data.get('data', []):
        if len(row) != len(FIELDS_MARKET):
            raise ValueError('證交所大盤成交列不完整')
        stamp = iso(row[0], month)
        if stamp in rows:
            raise ValueError('大盤成交日期重複')
        rows[stamp] = {'close': value(row[4]), 'total_amount': value(row[2])}
    if not rows:
        raise ValueError('大盤成交資料尚未發布')
    return rows


def parse_day_trade(raw, month):
    data = json.loads(raw)
    tables = data.get('tables') or []
    if data.get('stat') != 'OK' or not tables or tables[0].get('fields') != FIELDS_DAY_TRADE:
        raise ValueError('證交所當沖欄位改變')
    rows = {}
    for row in tables[0].get('data', []):
        if len(row) != len(FIELDS_DAY_TRADE):
            raise ValueError('證交所當沖列不完整')
        stamp = iso(row[0], month)
        if stamp in rows:
            raise ValueError('當沖日期重複')
        rows[stamp] = {'buy': value(row[3]), 'sell': value(row[5])}
    if not rows:
        raise ValueError('當沖資料尚未發布')
    return rows


def weighted_quantile(values, weights, percentile):
    ordered = sorted(zip(values, weights), key=lambda pair: pair[0])
    target = sum(weights) * percentile
    seen = 0
    for value_, weight in ordered:
        seen += weight
        if seen >= target:
            return value_
    return ordered[-1][0]


def build(state, today=None, margin=None, maintenance=None):
    today = today or datetime.now(TZ).date()
    margin = margin if margin is not None else margin_balance.cached()
    maintenance = maintenance if maintenance is not None else market_margin_maintenance.cached()
    all_market, all_trade = {}, {}
    historical_market = {}
    for month in state.get('history_market', {}).values():
        historical_market.update(month)
    for row in state.get('months', {}).values():
        all_market.update(row.get('market', {}))
        all_trade.update(row.get('day_trade', {}))
    # The longer close-only history is for moving averages, not the day-trade river.
    closes = {stamp: row['close'] for stamp, row in historical_market.items() if stamp <= today.isoformat()}
    closes.update({stamp: row['close'] for stamp, row in all_market.items() if stamp <= today.isoformat()})
    close_dates = sorted(closes)
    close_index = {stamp: i for i, stamp in enumerate(close_dates)}
    prefix = [0.0]
    for stamp in close_dates:
        prefix.append(prefix[-1] + closes[stamp])
    dates = sorted(d for d in all_market if d <= today.isoformat())
    missing = [d for d in dates if d not in all_trade]
    pairs = []
    for stamp in dates:
        if stamp not in all_trade:
            continue
        market, trade = all_market[stamp], all_trade[stamp]
        total = market['total_amount']
        traded = (trade['buy'] + trade['sell']) / 2
        remaining = total - traded
        if remaining <= 0 or remaining > total:
            missing.append(stamp)
            continue
        pairs.append(dict(date=stamp, close=market['close'], total=total, traded=traded,
                          remaining=remaining, day_trade_buy=trade['buy'], day_trade_sell=trade['sell']))
    points = []
    for i, row in enumerate(pairs):
        window = pairs[max(0, i - 59):i + 1]
        if len(window) < 40:
            continue
        values = [math.log(r['close']) for r in window]
        amounts = [r['remaining'] for r in window]
        anchor = statistics.median(amounts[-20:])
        weights = [min(2.0, max(0.5, amount / anchor)) for amount in amounts]
        size = len(window)
        total_weight = sum(weights)
        xbar = sum(j * w for j, w in enumerate(weights)) / total_weight
        ybar = sum(v * w for v, w in zip(values, weights)) / total_weight
        denominator = sum(w * (j - xbar) ** 2 for j, w in enumerate(weights))
        slope = sum(w * (j - xbar) * (v - ybar) for j, (v, w) in enumerate(zip(values, weights))) / denominator if denominator else 0
        intercept = ybar - slope * xbar
        residuals = [v - (intercept + slope * j) for j, v in enumerate(values)]
        fitted = intercept + slope * (size - 1)
        levels = [math.exp(fitted + weighted_quantile(residuals, weights, p)) for p in (.10, .25, .50, .75, .90)]
        rank = sum(w for residual, w in zip(residuals, weights) if residual <= residuals[-1]) / total_weight * 100
        points.append(dict(date=row['date'], close=row['close'], low10=levels[0], low25=levels[1],
                           median=levels[2], high75=levels[3], high90=levels[4],
                           total_100m=row['total'] / 1e8, day_trade_100m=row['traded'] / 1e8,
                           after_day_trade_100m=row['remaining'] / 1e8,
                           day_trade_pct=row['traded'] / row['total'] * 100,
                           turnover_ratio=row['remaining'] / anchor,
                           percentile=rank, window_count=size))
    for row in points:
        end = close_index[row['date']] + 1
        for length in (20, 60, 239):
            row[f'ma{length}'] = (prefix[end] - prefix[end - length]) / length if end >= length else None
    if points:
        first = month_shift(date.fromisoformat(points[-1]['date']), -12).isoformat()
        points = [r for r in points if r['date'] >= first]
    provisional = [r['date'] for r in points[-2:]]
    for row in points:
        row['provisional'] = row['date'] in provisional
        balance = margin.get('days', {}).get(row['date'])
        row['financing_units'] = balance['financing_units'] if balance else None
        row['short_units'] = balance['short_units'] if balance else None
        row['margin_finalized'] = bool(balance and row['date'] < (margin.get('as_of') or ''))
        maintained = maintenance.get('days', {}).get(row['date'])
        row['maintenance_pct'] = maintained['maintenance_pct'] if maintained else None
        row['maintenance_coverage_pct'] = maintained['covered_units_pct'] if maintained else None
        row['maintenance_estimated'] = maintained['estimated'] if maintained else None
        row['maintenance_source'] = maintained.get('source', 'twse') if maintained else None
    return dict(available=bool(points), as_of=points[-1]['date'] if points else None,
                points=points, missing_dates=sorted(set(missing)),
                margin_missing_dates=[row['date'] for row in points if row['financing_units'] is None or row['short_units'] is None],
                margin_warning=margin.get('warning', ''), margin_source_url=margin_balance.SOURCE,
                maintenance_missing_dates=[row['date'] for row in points if row['maintenance_pct'] is None],
                maintenance_warning=maintenance.get('warning', ''),
                maintenance_source_url=market_margin_maintenance.SOURCE,
                maintenance_price_source_url=market_margin_maintenance.PRICE_SOURCE,
                maintenance_reference_url=market_margin_maintenance.REFERENCE_SOURCE,
                source_url=SOURCE, day_trade_source_url=DAY_TRADE_SOURCE,
                checked_at=state.get('checked_at'), warning=state.get('warning', ''),
                note='每日上市市場成交金額－（當沖買進金額＋當沖賣出金額）÷2；此為扣當沖後的成交額，不是資金淨流入。價格中線及分位帶使用截至當日的近60筆收盤資料，依扣當沖成交額加權並截斷單日權重。20／60／239 日均線是當日及之前指數收盤價的簡單平均，歷史日數不足時不顯示。最近兩個交易日當沖數字可能尚待 T+2 修正。')


def cached():
    try:
        state = json.loads((db.DATA / 'market_liquidity_cache.json').read_text())
        if not isinstance(state.get('months'), dict):
            raise ValueError('價量快取格式異常')
        return state
    except (OSError, ValueError):
        return {'months': {}}


def report():
    return build(cached())


def refresh(fetch):
    with _lock:
        state = cached()
        now = datetime.now(TZ)
        today = now.date()
        months = [month_shift(today.replace(day=1), offset).strftime('%Y-%m') for offset in range(-15, 1)]
        errors = []
        history = state.setdefault('history_market', {})
        for offset in range(-24, -15):
            month = month_shift(today.replace(day=1), offset).strftime('%Y-%m')
            if month in history or month in state['months']:
                continue
            try:
                history[month] = parse_market(fetch('https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?response=json&date=' + month.replace('-', '') + '01'), month)
            except Exception:
                errors.append(month + ' 均線歷史收盤待補')
        for month in months:
            previous = state['months'].get(month)
            # Prior month is revisited until T+2 corrections have had time to publish.
            if previous and (month < today.strftime('%Y-%m') and
                             (month != month_shift(today, -1).strftime('%Y-%m') or today.day > 10)):
                continue
            try:
                market = parse_market(fetch('https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?response=json&date=' + month.replace('-', '') + '01'), month)
                trade = parse_day_trade(fetch('https://www.twse.com.tw/exchangeReport/TWTB4U2?response=json&date=' + month.replace('-', '') + '01'), month)
                state['months'][month] = {'market': market, 'day_trade': trade}
                if market.keys() != trade.keys():
                    errors.append(month + ' 當沖與大盤日期待對齊')
            except Exception:
                errors.append(month + ' 大盤或當沖資料待補')
        state['warning'] = '；'.join(errors)
        state['checked_at'] = now.isoformat(timespec='seconds')
        result = build(state, today)
        if not result['available']:
            raise ValueError(state['warning'] or '價量資料尚未齊全')
        db.DATA.mkdir(parents=True, exist_ok=True)
        path = db.DATA / 'market_liquidity_cache.json'
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(state, ensure_ascii=False, allow_nan=False))
        tmp.replace(path)
        try:
            dates = [row['date'] for row in result['points']]
            margin = margin_balance.refresh(fetch, dates)
            maintenance = market_margin_maintenance.refresh(fetch, dates)
            return build(state, today, margin, maintenance)
        except Exception as exc:
            result['margin_warning'] = f'融資資料暫不可用：{type(exc).__name__}'
            return result
