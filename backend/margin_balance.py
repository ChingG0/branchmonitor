"""TWSE listed-market daily financing and short balances from its range chart."""
from __future__ import annotations

import json
import threading
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import db, market_margin_sources


TZ = ZoneInfo('Asia/Taipei')
SOURCE = 'https://www.twse.com.tw/IIH2/zh/market/margin.html'
API = 'https://www.twse.com.tw/rwd/zh/IIH/market/margin?start={start}&end={end}'
FILE = 'market_margin_cache.json'
_lock=threading.Lock()


def parse_range(raw, first, last):
    data = json.loads(raw)
    if not isinstance(data, dict) or not all(name in data for name in ('taiex', 'purchase', 'shortSale')):
        raise ValueError('證交所融資融券區間資料格式改變')
    categories = data['taiex'].get('categories')
    if not isinstance(categories, list) or not categories:
        raise ValueError('證交所融資融券區間沒有交易日')
    for name in ('purchase', 'shortSale'):
        section = data[name]
        if section.get('categories') != categories or not section.get('series'):
            raise ValueError('融資融券日期或餘額序列不一致')
        if len(section['series'][0].get('data', [])) != len(categories):
            raise ValueError('融資融券餘額筆數不完整')
    days = {}
    for index, raw_day in enumerate(categories):
        stamp = date.fromisoformat(str(raw_day).replace('/', '-')).isoformat()
        if stamp < first or stamp > last or stamp in days:
            raise ValueError('融資融券餘額日期超出查詢範圍或重複')
        financing = data['purchase']['series'][0]['data'][index]
        short = data['shortSale']['series'][0]['data'][index]
        if isinstance(financing, bool) or isinstance(short, bool) or not all(
                isinstance(value, int) and value >= 0 for value in (financing, short)):
            raise ValueError('融資融券餘額不是有效交易單位數')
        days[stamp] = {'financing_units': financing, 'short_units': short}
    if list(days) != sorted(days):
        raise ValueError('融資融券餘額日期未依序排列')
    return days


def cached():
    try:
        state = json.loads((db.DATA / FILE).read_text())
        if not isinstance(state.get('days'), dict):
            raise ValueError('融資融券快取格式異常')
        return state
    except (OSError, ValueError):
        return {'days': {}, 'as_of': None, 'checked_at': None, 'warning': ''}


def refresh(fetch, dates):
    with _lock:return _refresh(fetch, dates)

def _refresh(fetch, dates):
    state = cached()
    dates = sorted(set(dates))
    if not dates:
        return state
    # Share normalized official financing sources instead of maintaining an
    # unrelated stale cache when the range endpoint is delayed or unavailable.
    local=market_margin_sources.read_saved(dates)
    for day,item in local.items():
        m=item['margin']
        state['days'][day]={'financing_units':m['total_units'],'short_units':m['short_units'],'loan_thousand':m['loan_thousand'],'source_snapshot_id':item['credit_snapshot_id'],'source_retrieved_at':item['credit_retrieved_at']}
    first,last=dates[0],dates[-1]
    if last not in local:
        try:
            if fetch is None:raise ValueError('本機尚無同日官方融資資料')
            days=parse_range(fetch(API.format(start=first.replace('-',''),end=last.replace('-',''))),first,last)
            if last not in days:raise ValueError(f'區間來源尚未包含{last}')
            state['days'].update(days);state['warning']=''
        except Exception as exc:
            state['warning']=f'融資融券餘額來源暫不可用：{str(exc)[:100]}；請手動更新'
    else:state['warning']=''
    state['as_of']=max(state['days'],default=None)
    state['checked_at']=datetime.now(TZ).isoformat(timespec='seconds')
    db.DATA.mkdir(parents=True, exist_ok=True)
    path = db.DATA / FILE
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(state, ensure_ascii=False, allow_nan=False))
    temp.replace(path)
    return state
