"""Listed-market margin maintenance estimate from TWSE daily credit and close data."""
from __future__ import annotations

import json
import threading
import math
from datetime import datetime
from zoneinfo import ZoneInfo

from . import db, market_margin_sources

TZ = ZoneInfo('Asia/Taipei')
FILE = 'market_margin_maintenance_cache.json'
_lock=threading.Lock()
SOURCE = 'https://www.twse.com.tw/zh/trading/margin/mi-margn.html'
PRICE_SOURCE = 'https://www.twse.com.tw/zh/trading/exchange/mi-index.html'
REFERENCE_SOURCE = 'https://www.istock.tw/post/twmarginrequirement_more'
MARGIN_API = 'https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={day}&selectType=ALL&response=json'
PRICE_API = 'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={day}&type=ALLBUT0999&response=json'
SUMMARY_FIELDS = ['項目', '買進', '賣出', '現金(券)償還', '前日餘額', '今日餘額']
MARGIN_FIELDS = ['代號', '名稱', '買進', '賣出', '現金償還', '前日餘額', '今日餘額', '次一營業日限額',
                 '買進', '賣出', '現券償還', '前日餘額', '今日餘額', '次一營業日限額', '資券互抵', '註記']


def _integer(value):
    if isinstance(value, bool):
        raise ValueError('信用交易資料不是有效整數')
    raw = str(value).replace(',', '').strip()
    if not raw.isdigit():
        raise ValueError('信用交易資料不是有效整數')
    return int(raw)


def parse_margin(raw, day):
    payload = json.loads(raw)
    tables = payload.get('tables') or []
    if payload.get('stat') != 'OK' or payload.get('date') != day.replace('-', '') or len(tables) < 2:
        raise ValueError('證交所每日信用交易資料尚未發布或日期不符')
    summary, detail = tables[:2]
    if summary.get('fields') != SUMMARY_FIELDS or detail.get('fields') != MARGIN_FIELDS:
        raise ValueError('證交所每日信用交易欄位改變')
    rows = summary.get('data') or []
    if len(rows) != 3 or [r[0] for r in rows] != ['融資(交易單位)', '融券(交易單位)', '融資金額(仟元)']:
        raise ValueError('證交所融資金額彙總格式改變')
    total_units, short_units, loan_thousand = [_integer(row[5]) for row in rows]
    if not total_units or not loan_thousand:
        raise ValueError('融資餘額或融資金額為零')
    balances = {}
    for row in detail.get('data') or []:
        if len(row) != len(MARGIN_FIELDS):
            raise ValueError('證交所個股融資餘額列不完整')
        symbol = str(row[0]).strip()
        if not symbol or symbol in balances:
            raise ValueError('證交所個股融資代號重複或空白')
        balances[symbol] = _integer(row[6])
    if sum(balances.values()) != total_units:
        raise ValueError('證交所個股融資餘額與總額不符')
    return {'balances': balances, 'total_units': total_units,
            'short_units': short_units, 'loan_thousand': loan_thousand}


def parse_prices(raw, day):
    payload = json.loads(raw)
    if payload.get('stat') != 'OK' or payload.get('date') != day.replace('-', ''):
        raise ValueError('證交所個股收盤資料尚未發布或日期不符')
    tables = [table for table in payload.get('tables', [])
              if '每日收盤行情' in (table.get('title') or '')]
    if len(tables) != 1:
        raise ValueError('證交所每日收盤行情表格式改變')
    table = tables[0]
    fields = table.get('fields') or []
    if len(fields) < 9 or fields[0] != '證券代號' or fields[8] != '收盤價':
        raise ValueError('證交所個股收盤欄位改變')
    prices = {}
    for row in table.get('data') or []:
        if len(row) != len(fields):
            raise ValueError('證交所個股收盤列不完整')
        symbol = str(row[0]).strip()
        if not symbol or symbol in prices:
            raise ValueError('證交所個股收盤代號重複或空白')
        raw_close = str(row[8]).replace(',', '').strip()
        if raw_close in ('', '--'):
            continue
        close = float(raw_close)
        if not math.isfinite(close) or close <= 0:
            raise ValueError('證交所個股收盤價無效')
        prices[symbol] = close
    if not prices:
        raise ValueError('證交所每日個股收盤價為空')
    return prices


def calculate(margin, prices, previous_prices, day):
    value_thousand = 0.0
    covered_units = 0
    missing = []
    for symbol, units in margin['balances'].items():
        if not units:
            continue
        close = prices.get(symbol)
        if close is None:
            previous = previous_prices.get(symbol)
            if previous and previous['date'] <= day:
                close = previous['close']
        if close is None:
            missing.append(symbol)
            continue
        # Trading unit = 1,000 shares; both value and official loan are in NT$ thousands.
        value_thousand += units * close
        covered_units += units
    coverage = covered_units / margin['total_units'] * 100
    if coverage < 99:
        raise ValueError(f'融資部位收盤價涵蓋率不足：{coverage:.2f}%')
    return {'maintenance_pct': value_thousand / margin['loan_thousand'] * 100,
            'market_value_thousand': round(value_thousand),
            'loan_thousand': margin['loan_thousand'],
            'covered_units_pct': coverage, 'missing_symbols': missing,
            'estimated': bool(missing), 'source': 'twse'}


def cached():
    try:
        state = json.loads((db.DATA / FILE).read_text())
        if not isinstance(state.get('days'), dict) or not isinstance(state.get('last_prices'), dict):
            raise ValueError('融資維持率快取格式異常')
        return state
    except (OSError, ValueError):
        return {'days': {}, 'last_prices': {}, 'as_of': None, 'checked_at': None, 'warning': ''}


def _save(state):
    db.DATA.mkdir(parents=True, exist_ok=True)
    path = db.DATA / FILE
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(state, ensure_ascii=False, allow_nan=False))
    temp.replace(path)


def refresh(fetch, dates, max_days=3):
    with _lock:return _refresh(fetch, dates, max_days)

def _refresh(fetch, dates, max_days=3):
    state = cached()
    dates = sorted(set(dates))
    if not dates:
        return state
    # Latest first; a failed current source never fans out into old requests.
    recent=dates[-2:]
    missing=[day for day in reversed(dates) if day not in state['days'] and day not in recent]
    targets=list(reversed(recent))+missing[:max(0,max_days-len(recent))]
    local=market_margin_sources.read_saved(targets,prices=True)
    errors=[]
    previous_prices=dict(state['last_prices'])
    for day in targets:
        if errors:break
        try:
            if day in local:
                item=local[day];margin=item['margin'];prices=item['prices']
            else:
                if fetch is None:raise ValueError('本機尚無同日官方融資與收盤資料')
                key=day.replace('-','')
                margin=parse_margin(fetch(MARGIN_API.format(day=key)),day)
                prices=parse_prices(fetch(PRICE_API.format(day=key)),day)
            prior={s:v for s,v in previous_prices.items() if v['date']<=day}
            for old_day in sorted(d for d in local if d<=day):
                for symbol,close in local[old_day]['prices'].items():prior[symbol]={'date':old_day,'close':close}
            value=calculate(margin,prices,prior,day)
            if day in local:value.update(source_snapshot_id=item['credit_snapshot_id'],price_snapshot_id=item['price_snapshot_id'],source_retrieved_at=item['credit_retrieved_at'])
            state['days'][day]=value
            for symbol,close in prices.items():
                previous=state['last_prices'].get(symbol)
                if not previous or previous['date']<=day:state['last_prices'][symbol]={'date':day,'close':close}
        except Exception as exc:errors.append(f'{day} {str(exc)[:120]}；請手動更新')
    state['as_of'] = max(state['days'], default=None)
    state['checked_at'] = datetime.now(TZ).isoformat(timespec='seconds')
    state['warning'] = '；'.join(errors)
    _save(state)
    return state
