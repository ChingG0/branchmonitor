"""Official TWSE/TPEx stock yields and trailing ETF cash distributions."""
from __future__ import annotations

import json
import math
import re
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from . import db

TZ = ZoneInfo('Asia/Taipei')
TWSE_STOCK = 'https://wwwc.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d'
TPEX_STOCK = 'https://www.tpex.org.tw/web/stock/aftertrading/peratio_analysis/pera_result.php'
TWSE_EX_HISTORY = 'https://wwwc.twse.com.tw/rwd/zh/exRight/TWT49U'
TWSE_EX_DETAIL = 'https://wwwc.twse.com.tw/rwd/zh/exRight/TWT49UDetail'
TPEX_EX_HISTORY = 'https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ'
TWSE_ETF = 'https://wwwc.twse.com.tw/zh/ETFortune/dividendList'
TPEX_ETF = 'https://info.tpex.org.tw/api/etfExDiv'
TWSE_EX_NOTICE = 'https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL'
TPEX_EX_NOTICE = 'https://www.tpex.org.tw/www/zh-tw/bulletin/prePost'
HOLIDAY_SCHEDULE = 'https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule'
WANTGOO_EX_HISTORY = 'https://www.wantgoo.com/stock/dividend-policy/all-ex-dividend-data'
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; BranchMonitor/1.0)'}
WANTGOO_HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
                   'Accept': 'application/json, text/javascript, */*; q=0.01',
                   'X-Requested-With': 'XMLHttpRequest',
                   'Referer': 'https://www.wantgoo.com/stock/dividend-yield'}
_lock = threading.Lock()
_state = {'status': 'idle', 'message': ''}


def _tables():
    with db.connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS dividend_yield_days (date TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched_at TEXT NOT NULL)')
        conn.execute('CREATE TABLE IF NOT EXISTS dividend_ex_details (market TEXT NOT NULL, symbol TEXT NOT NULL, date TEXT NOT NULL, cash REAL NOT NULL, shares REAL NOT NULL, fetched_at TEXT NOT NULL, PRIMARY KEY(market,symbol,date))')
        conn.execute('CREATE TABLE IF NOT EXISTS dividend_wantgoo_cache (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL, fetched_at TEXT NOT NULL)')


def _number(value):
    try:
        result = float(str(value).replace(',', '').strip())
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _roc_day(value):
    value = str(value).strip()
    match = re.fullmatch(r'(\d{2,3})年(\d{1,2})月(\d{1,2})日', value)
    if not match:
        raise ValueError(f'ETF 除息日期格式已變更：{value[:20]}')
    return date(int(match[1]) + 1911, int(match[2]), int(match[3]))


def _roc_slash_day(value):
    match = re.fullmatch(r'(\d{2,3})/(\d{1,2})/(\d{1,2})', str(value).strip())
    if not match:
        raise ValueError('櫃買中心除息預告日期格式已變更')
    return date(int(match[1]) + 1911, int(match[2]), int(match[3]))


def _roc_compact_day(value):
    match = re.fullmatch(r'(\d{2,3})(\d{2})(\d{2})', str(value).strip())
    if not match:
        raise ValueError('證交所除息預告日期格式已變更')
    return date(int(match[1]) + 1911, int(match[2]), int(match[3]))


def parse_twse_notices(data):
    if not isinstance(data, list):
        raise ValueError('證交所除息預告格式已變更')
    events = []
    for item in data:
        if not isinstance(item, dict) or not {'Date', 'Code', 'Exdividend'} <= item.keys():
            raise ValueError('證交所除息預告欄位已變更')
        if '息' not in str(item['Exdividend']):
            continue
        symbol = str(item['Code']).strip()
        if re.fullmatch(r'\d{4,6}[A-Z]?', symbol):
            events.append((symbol, _roc_compact_day(item['Date'])))
    return events


def parse_tpex_notices(data):
    tables = data.get('tables') if isinstance(data, dict) else None
    if not isinstance(data, dict) or data.get('stat') != 'ok' or not isinstance(tables, list) or len(tables) != 1:
        raise ValueError('櫃買中心除息預告格式已變更')
    expected = ['除權息日期', '代號', '名稱', '除權息']
    table = tables[0]
    if table.get('fields', [])[:4] != expected:
        raise ValueError('櫃買中心除息預告欄位已變更')
    events = []
    for cells in table.get('data', []):
        if len(cells) < 4:
            raise ValueError('櫃買中心除息預告資料格式已變更')
        if '息' not in str(cells[3]):
            continue
        symbol = str(cells[1]).strip()
        if re.fullmatch(r'\d{4,6}[A-Z]?', symbol):
            events.append((symbol, _roc_slash_day(cells[0])))
    return events


def parse_holidays(data, year):
    if not isinstance(data, list) or not data:
        raise ValueError('證交所開休市日曆格式已變更')
    holidays = set()
    seen_years = set()
    for item in data:
        if not isinstance(item, dict) or not {'Date', 'Name', 'Description'} <= item.keys():
            raise ValueError('證交所開休市日曆欄位已變更')
        day = _roc_compact_day(item['Date'])
        seen_years.add(day.year)
        description = str(item['Description'])
        name = str(item['Name'])
        if '交易日' not in name and ('放假' in description or '補假' in description or '市場無交易' in name):
            holidays.add(day)
    if year not in seen_years:
        raise ValueError('官方開休市日曆尚未涵蓋排行日期')
    return holidays


def previous_trading_day(ex_day, holidays, calendar_year):
    candidate = ex_day - timedelta(days=1)
    for _ in range(15):
        if candidate.year != calendar_year:
            return None
        if candidate.weekday() < 5 and candidate not in holidays:
            return candidate
        candidate -= timedelta(days=1)
    raise ValueError('除息日前找不到交易日')


def _get(url, **kwargs):
    for attempt in range(4):
        try:
            response = requests.get(url, headers=HEADERS, timeout=(12, 35), **kwargs)
            if response.status_code not in {428, 429, 502, 503} or attempt == 3:
                response.raise_for_status()
                return response
        except requests.RequestException:
            if attempt == 3:
                raise
        time.sleep(1.0 * (attempt + 1))


def _post(url, **kwargs):
    for attempt in range(4):
        try:
            response = requests.post(url, timeout=(12, 35), **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException:
            if attempt == 3:
                raise
        time.sleep(1.0 * (attempt + 1))


def parse_twse_stocks(data, target):
    fields = ['證券代號', '證券名稱', '收盤價', '殖利率(%)', '股利年度', '本益比', '股價淨值比', '財報年/季']
    if data.get('stat') != 'OK' or data.get('date') != target.strftime('%Y%m%d') or data.get('fields') != fields:
        raise ValueError('證交所個股殖利率日期或欄位不符')
    rows = []
    for cells in data.get('data', []):
        if len(cells) != len(fields):
            raise ValueError('證交所個股殖利率資料格式已變更')
        symbol, name, close, yield_pct, dividend_year = cells[:5]
        close, yield_pct, pe = _number(close), _number(yield_pct), _number(cells[5])
        if not re.fullmatch(r'\d{4}', str(symbol)) or close is None or close <= 0:
            continue
        if yield_pct is not None and not 0 <= yield_pct <= 200:
            raise ValueError('證交所殖利率超出合理範圍')
        rows.append(dict(symbol=symbol, name=str(name).strip(), market='上市', close=close,
                         yield_pct=yield_pct, pe_ratio=pe if pe and pe > 0 else None,
                         dividend_year=str(dividend_year), source='TWSE'))
    if len(rows) < 700:
        raise ValueError('證交所個股殖利率筆數不足')
    return rows


def parse_tpex_stocks(data, target, quotes):
    tables = data.get('tables') or []
    if len(tables) != 1 or tables[0].get('date') != f'{target.year-1911}/{target.month:02d}/{target.day:02d}':
        raise ValueError('櫃買中心個股殖利率日期不符')
    expected = ['股票代號', '公司名稱', '本益比', '每股股利', '股利年度', '殖利率(%)', '股價淨值比', '財報年/季']
    if tables[0].get('fields') != expected:
        raise ValueError('櫃買中心個股殖利率欄位已變更')
    rows = []
    for cells in tables[0].get('data', []):
        if len(cells) != len(expected):
            raise ValueError('櫃買中心個股殖利率資料格式已變更')
        symbol, name, pe_value, _, dividend_year, yield_pct = cells[:6]
        if not re.fullmatch(r'\d{4}', str(symbol)) or symbol not in quotes:
            continue
        yield_pct = _number(yield_pct)
        if yield_pct is not None and not 0 <= yield_pct <= 200:
            raise ValueError('櫃買中心殖利率超出合理範圍')
        pe = _number(pe_value)
        rows.append(dict(symbol=symbol, name=str(name).strip(), market='上櫃', close=quotes[symbol]['close'],
                         yield_pct=yield_pct, pe_ratio=pe if pe and pe > 0 else None,
                         dividend_year=str(dividend_year), source='TPEx'))
    if len(rows) < 600:
        raise ValueError('櫃買中心個股殖利率筆數不足')
    return rows


def _twse_ex_detail(symbol, day):
    for attempt in range(2):
        try:
            response = requests.get(TWSE_EX_DETAIL,
                                    params={'STK_NO':symbol, 'T1':day.strftime('%Y%m%d'), 'response':'json'},
                                    headers=HEADERS, timeout=(8, 12))
            response.raise_for_status()
            data = response.json()
            break
        except (requests.RequestException, ValueError):
            if attempt == 1:
                raise
            time.sleep(0.4)
    if data.get('stat') != 'ok' or not data.get('data') or len(data['data'][0]) < 5:
        raise ValueError(f'{symbol} {day} 證交所除權息明細不完整')
    cells = data['data'][0]
    cash = _number(str(cells[2]).split(' ')[0])
    shares = _number(str(cells[4]).split(' ')[0])
    if cash is None or shares is None:
        raise ValueError(f'{symbol} {day} 證交所股利金額無法判讀')
    return cash, shares


def parse_twse_ex_history(data, target):
    expected = ['資料日期', '股票代號', '股票名稱', '除權息前收盤價', '除權息參考價',
                '權值+息值', '權/息']
    if data.get('stat') != 'OK' or data.get('fields', [])[:7] != expected:
        raise ValueError('證交所歷史除權息欄位已變更')
    events, details, incomplete = [], [], defaultdict(set)
    for cells in data.get('data', []):
        if len(cells) < 12 or not re.fullmatch(r'\d{4}', str(cells[1])):
            continue
        match = re.fullmatch(r'(\d{2,3})年(\d{1,2})月(\d{1,2})日', str(cells[0]))
        if not match:
            raise ValueError('證交所歷史除權息日期格式已變更')
        day = date(int(match[1])+1911, int(match[2]), int(match[3]))
        if day > target:
            continue
        symbol, kind = str(cells[1]), str(cells[6])
        if kind == '息':
            cash = _number(cells[5])
            if cash is None:
                raise ValueError(f'{symbol} {day} 現金股利金額缺漏')
            events.append((symbol, day, cash, 0.0, _number(cells[3])))
        elif '權' in kind:
            details.append((symbol, day, _number(cells[3])))
    if len(events) + len(details) < 100:
        raise ValueError('證交所歷史除權息筆數不足')
    if details:
        _tables()
        with db.connect() as conn:
            cache = {(row['symbol'], row['date']):(row['cash'], row['shares']) for row in
                     conn.execute("SELECT symbol,date,cash,shares FROM dividend_ex_details WHERE market='TWSE'")}
        missing = []
        for symbol, day, pre_close in details:
            cached = cache.get((symbol, day.isoformat()))
            if cached is None:
                missing.append((symbol, day, pre_close))
            else:
                events.append((symbol, day, *cached, pre_close))
        fetched = []
        missing.sort(key=lambda item:item[1], reverse=True)
        for symbol, day, _ in missing[60:]:
            incomplete[symbol].add(day)
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = {pool.submit(_twse_ex_detail, symbol, day):(symbol, day, pre_close)
                    for symbol, day, pre_close in missing[:60]}
            for job in as_completed(jobs):
                symbol, day, pre_close = jobs[job]
                try:
                    cash, shares = job.result()
                except (requests.RequestException, ValueError):
                    incomplete[symbol].add(day)
                    continue
                events.append((symbol, day, cash, shares, pre_close))
                fetched.append((symbol, day.isoformat(), cash, shares, datetime.now(TZ).isoformat(timespec='seconds')))
        if fetched:
            with db.connect() as conn:
                conn.executemany("INSERT OR REPLACE INTO dividend_ex_details VALUES ('TWSE',?,?,?,?,?)", fetched)
    return events, incomplete


def _twse_ex_history_chunks(start, end):
    """Bound official requests to one month; the two-year query can time out."""
    ranges=[];cursor=start
    while cursor<=end:
        following=date(cursor.year+1,1,1) if cursor.month==12 else date(cursor.year,cursor.month+1,1)
        stop=min(end,following-timedelta(days=1));ranges.append((cursor,stop));cursor=stop+timedelta(days=1)
    def fetch_chunk(bounds):
        begin,stop=bounds
        data=_get(TWSE_EX_HISTORY,params={'startDate':begin.strftime('%Y%m%d'),'endDate':stop.strftime('%Y%m%d'),'response':'json'}).json()
        if data.get('stat')!='OK' or data.get('strDate')!=begin.strftime('%Y%m%d') or data.get('endDate')!=stop.strftime('%Y%m%d'):
            raise ValueError('證交所除權息分段日期不符')
        for row in data.get('data',[]):
            match=re.fullmatch(r'(\d{2,3})年(\d{1,2})月(\d{1,2})日',str(row[0]))
            if not match or not begin<=date(int(match[1])+1911,int(match[2]),int(match[3]))<=stop:
                raise ValueError('證交所除權息含區間外資料')
        return data
    with ThreadPoolExecutor(max_workers=3) as pool:chunks=list(pool.map(fetch_chunk,ranges))
    result=dict(chunks[0]);result.update(strDate=start.strftime('%Y%m%d'),endDate=end.strftime('%Y%m%d'),data=[])
    seen={}
    for chunk in chunks:
        if chunk.get('fields')!=result.get('fields'):raise ValueError('證交所除權息分段欄位不一致')
        for row in chunk.get('data',[]):
            key=(row[0],str(row[1]))
            if key in seen and seen[key]!=row:raise ValueError('證交所除權息分段重複資料不一致')
            seen[key]=row
    result['data']=list(seen.values())
    return result


def parse_tpex_ex_history(data, target):
    tables = data.get('tables') if isinstance(data, dict) else None
    if not isinstance(tables, list) or len(tables) != 1:
        raise ValueError('櫃買中心歷史除權息格式已變更')
    fields = tables[0].get('fields', [])
    if fields[:3] != ['除權息日期', '代號', '名稱'] or '現金股利' not in fields or '每仟股無償配股' not in fields:
        raise ValueError('櫃買中心歷史除權息欄位已變更')
    cash_idx, shares_idx = fields.index('現金股利'), fields.index('每仟股無償配股')
    events = []
    for cells in tables[0].get('data', []):
        if len(cells) <= max(cash_idx, shares_idx) or not re.fullmatch(r'\d{4}', str(cells[1])):
            continue
        day = _roc_slash_day(cells[0])
        if day > target:
            continue
        cash, shares = _number(cells[cash_idx]), _number(cells[shares_idx])
        if cash is None or shares is None:
            raise ValueError(f'{cells[1]} {day} 櫃買中心股利資料缺漏')
        events.append((str(cells[1]), day, cash, shares, _number(cells[3])))
    if len(events) < 100:
        raise ValueError('櫃買中心歷史除權息筆數不足')
    return events


CORPORATE_SOURCES = {
    'twse_par': 'https://wwwc.twse.com.tw/rwd/zh/change/TWTB8U',
    'tpex_par': 'https://www.tpex.org.tw/www/zh-tw/bulletin/pvChgRslt',
    'twse_capital': 'https://wwwc.twse.com.tw/rwd/zh/reducation/TWTAUU',
    'tpex_capital': 'https://www.tpex.org.tw/www/zh-tw/bulletin/revivt',
}


def parse_share_changes(data, start, target, market, kind):
    from .rs import parse_events
    # Reuse date/range/column validation, but retain the actual exchange ratio.
    valid = parse_events(data, start.isoformat(), target.isoformat(), market, kind)
    if not valid:
        return []
    table = data.get('tables', [data])[0]
    fields = table['fields']
    symbol_key = next(f for f in ('股票代號', '證券代號', '代號') if f in fields)
    values = {(str(row[fields.index(symbol_key)]).strip(), str(row[0])):dict(zip(fields, row))
              for row in table['data']}
    from .rs import roc
    by_day = {(symbol, roc(day)):row for (symbol, day), row in values.items()}
    result = []
    for event in valid:
        row = by_day[(event['symbol'], event['date'])]
        factor = None
        if kind == 'par':
            if market == 'TPEX':
                detail = BeautifulSoup(str(row.get('詳細資料', '')), 'html.parser')
                for tr in detail.select('tr'):
                    if '變更股票面額換股率' in tr.get_text():
                        cell = tr.select_one('td')
                        factor = _number(cell.get_text(strip=True)) if cell else None
            else:
                before, after = _number(row.get('停止買賣前收盤價格')), _number(row.get('恢復買賣參考價'))
                if before and after and after > 0:
                    factor = before/after
            if factor is None or factor <= 0:
                raise ValueError(f"{event['symbol']} 面額換股率無法核對")
        # Capital reductions can include returned cash and simultaneous issues;
        # do not infer their share ratio from reference prices.
        result.append(dict(symbol=event['symbol'], date=event['date'], kind=kind, factor=factor))
    return result


def _share_changes(start, target):
    results = []
    for key, url in CORPORATE_SOURCES.items():
        market, kind = key.split('_')
        fmt = '%Y%m%d' if market=='twse' else '%Y/%m/%d'
        data = _get(url, params={'startDate':start.strftime(fmt),
                                'endDate':target.strftime(fmt), 'response':'json'}).json()
        results.extend(parse_share_changes(data, start, target, market.upper(), kind))
    return results


ETF_SPLIT_SOURCES = {
    'twse': 'https://wwwc.twse.com.tw/rwd/zh/split/TWTCAU',
    'tpex': 'https://www.tpex.org.tw/www/zh-tw/bulletin/etfSplitRslt',
    'tpex_reverse': 'https://www.tpex.org.tw/www/zh-tw/bulletin/etfRvsRslt',
}


def parse_etf_share_changes(data, start, target, market):
    from .etf_rs import parse_splits
    from .rs import roc
    valid = parse_splits(data, start.isoformat(), target.isoformat(), market)
    if not valid:
        return []
    tables = [data] if market=='TWSE' else data['tables']
    ratios = {}
    for table in tables:
        for cells in table.get('data', []):
            row = dict(zip(table['fields'], cells))
            symbol = row['ETF代號' if market=='TWSE' else '證券代號'].strip()
            before = _number(row.get('停止買賣前收盤價格') if market=='TWSE' else row.get('最後交易日之收盤價格'))
            after = _number(row.get('恢復買賣參考價') if market=='TWSE' else row.get('恢復買賣開始參考價'))
            if not before or not after or before<=0 or after<=0:
                raise ValueError(f'{symbol} ETF 分割比率不完整')
            ratios[(symbol, roc(row['恢復買賣日期']))] = before/after
    return [{**event, 'factor':ratios[(event['symbol'],event['date'])]} for event in valid]


def _etf_share_changes(start, target):
    result = []
    for key, url in ETF_SPLIT_SOURCES.items():
        market = 'TWSE' if key=='twse' else 'TPEX'
        fmt = '%Y%m%d' if market=='TWSE' else '%Y/%m/%d'
        data = _get(url, params={'startDate':start.strftime(fmt), 'endDate':target.strftime(fmt), 'response':'json'}).json()
        result.extend(parse_etf_share_changes(data, start, target, market))
    return result


def _adjust_etf_cash(symbol, ex_day, amount, target, changes):
    return amount / math.prod(e['factor'] for e in changes
        if e['symbol']==symbol and ex_day.isoformat()<e['date']<=target.isoformat())


def parse_dividend_calendar(data):
    """Use fiscal periods/payment dates only; never consume the provider's yield."""
    if not isinstance(data, list) or len(data) < 5000:
        raise ValueError('股利年度對照紀錄不足')
    result = defaultdict(list)
    seen = {}
    for item in data:
        symbol = str(item.get('stockNo', ''))
        if not re.fullmatch(r'\d{4}', symbol):
            continue
        cash = _number(item.get('cashDividend'))
        if cash is None or cash < 0:
            raise ValueError('股利年度對照金額不完整')
        seasons = item.get('yearSeasons') or []
        years = {int(q)//10 for q in seasons}
        quarters = sorted({int(q)%10 for q in seasons})
        if len(years) != 1 or not set(quarters) <= {1, 2, 3, 4}:
            match = re.fullmatch(r'(\d{2,3})年度', str(item.get('period', '')))
            if not match:
                continue
            years, quarters = {int(match[1])+1911}, [1, 2, 3, 4]
        year = years.pop()
        ex_day = date.fromisoformat(str(item['date']).replace('/', '-')).isoformat()
        payment = item.get('paymentDate')
        payment = date.fromisoformat(str(payment)[:10]).isoformat() if payment else None
        event = dict(year=year, quarters=quarters, ex_date=ex_day,
                     payment_date=payment, cash=cash)
        key = symbol, ex_day, year
        if key in seen:
            if seen[key] != event:
                raise ValueError(f'{symbol} {ex_day} 股利年度對照互相矛盾')
            continue
        seen[key] = event
        result[symbol].append(event)
    if len(result) < 1300:
        raise ValueError('股利年度對照覆蓋不足')
    return dict(result)


def _stock_cash_estimates(rows, events, target, incomplete=frozenset(), calendar=None, share_changes=()):
    """Reconcile fiscal-year payouts to official ex-events on current share basis.

    A complete, paid fiscal year takes precedence over trailing cash. Incomplete
    years fall back to an older complete year, then a clearly labelled last paid
    distribution. Unknown payments or inconsistent cash amounts stay unranked.
    """
    by_symbol = defaultdict(dict)
    for symbol, day, cash, shares, *rest in events:
        if day <= target:
            value = (cash, shares)
            if day in by_symbol[symbol] and by_symbol[symbol][day] != value:
                raise ValueError(f'{symbol} {day} 官方除息資料互相矛盾')
            by_symbol[symbol][day] = value
    for row in rows:
        official_yield = row.get('yield_pct')
        row.update(yield_pct=None, cash_per_share=None, last_ex_date=None,
                   cash_dividend_year=None, yield_basis='年度現金股利待核對',
                   yield_method='unavailable', yield_components=[])
        # Obsolete single-event yield was displayed beside the annual yield.
        for key in ('last_ex_yield_pct', 'last_cash_ex_date', 'last_cash_ex_amount', 'last_cash_ex_pre_close'):
            row.pop(key, None)
        ordered = by_symbol.get(row['symbol'], {})
        gaps = incomplete.get(row['symbol'], ()) if hasattr(incomplete, 'get') else (
            (target,) if row['symbol'] in incomplete else ())
        preferred = _number(row.get('dividend_year'))
        preferred = int(preferred) + (1911 if preferred < 1911 else 0) if preferred else target.year-1
        corporate = [e for e in share_changes if e['symbol']==row['symbol'] and e['date']<=target.isoformat()]
        grouped = defaultdict(list)
        for event in (calendar or {}).get(row['symbol'], []):
            if event['year'] <= preferred:
                grouped[event['year']].append(event)
        verified = {}
        for year, group in grouped.items():
            for event in group:
                day = date.fromisoformat(event['ex_date'])
                actual = ordered.get(day)
                if (event['cash'] <= 0 or not actual or day > target
                        or not event['payment_date'] or event['payment_date'] > target.isoformat()
                        or event['payment_date'] < event['ex_date']):
                    continue
                cash, _ = actual
                if abs(cash-event['cash']) > max(0.0001, cash*0.001):
                    continue
                # Later stock dividends dilute the historical per-share cash.
                # Same-day cash was also paid on the pre-rights share count.
                if any(gap >= day for gap in gaps):
                    continue
                changes = [e for e in corporate if e['date'] >= event['ex_date']]
                if any(e['factor'] is None for e in changes):
                    continue
                factor = math.prod(1+shares/1000 for rights_day, (_, shares) in ordered.items()
                                   if rights_day >= day) * math.prod(e['factor'] for e in changes)
                verified[event['ex_date']] = dict(ex_date=event['ex_date'],
                    payment_date=event['payment_date'], cash=cash,
                    adjusted_cash=cash/factor, share_factor=factor, year=year)
        chosen, used_year, method = [], None, None
        if official_yield == 0:
            used_year, method = preferred, 'zero'
        for year in sorted(grouped, reverse=True):
            if method is not None:
                break
            group = grouped[year]
            quarters = set().union(*(set(e['quarters']) for e in group))
            positive = [e for e in group if e['cash'] > 0]
            paid = positive and all(e['ex_date'] in verified for e in positive)
            official_agrees = (paid and year == preferred and official_yield is not None and
                abs(sum(verified[e['ex_date']]['adjusted_cash'] for e in positive)/row['close']*100-official_yield) <= 0.0051)
            if (quarters == {1, 2, 3, 4} or official_agrees) and paid:
                chosen = [verified[e['ex_date']] for e in positive]
                used_year, method = year, 'annual' if year == preferred else 'previous_year'
                break
            if (year == preferred and quarters == {1, 2, 3, 4} and not positive
                    and all(e['ex_date'] <= target.isoformat() for e in group)):
                used_year, method = year, 'zero'
                break
        if method is None and verified:
            latest = max(verified.values(), key=lambda e:e['ex_date'])
            chosen, used_year, method = [latest], latest['year'], 'last_paid'
        if method is None:
            continue
        cash_total = sum(e['adjusted_cash'] for e in chosen)
        row.update(yield_pct=round(cash_total/row['close']*100, 2),
                   cash_per_share=round(cash_total, 6), cash_dividend_year=used_year,
                   last_ex_date=max((e['ex_date'] for e in chosen), default=None),
                   yield_method=method, yield_components=chosen,
                   yield_basis=(f'{used_year} 年度未配發現金' if method=='zero' else
                                f'{used_year} 年度已發放現金' if method=='annual' else
                                f'{used_year} 年度已發放現金（前年度）' if method=='previous_year' else
                                '前次已發放現金估算（非全年）'))


def _fill_metrics(rows, events, target):
    """Observed close-price fills within 60 trading days; unfinished events are censored."""
    by_symbol = defaultdict(list)
    rights = defaultdict(list)
    for symbol, day, cash, shares, *rest in events:
        if cash > 0 and rest and rest[0] and rest[0] > 0:
            by_symbol[symbol].append((day, rest[0]))
        if shares > 0:
            rights[symbol].append(day)
    with db.connect() as conn:
        first = conn.execute('SELECT MIN(date) FROM prices').fetchone()[0]
        first_day = date.fromisoformat(first) if first else target
        for row in rows:
            successes, measured, days = 0, 0, []
            for ex_day, pre_close in by_symbol.get(row['symbol'], []):
                if ex_day < first_day or ex_day > target:
                    continue
                prices = conn.execute(
                    'SELECT date,close FROM prices WHERE symbol=? AND date>=? AND date<=? ORDER BY date LIMIT 60',
                    (row['symbol'], ex_day.isoformat(), target.isoformat())).fetchall()
                if not prices or prices[0]['date'] != ex_day.isoformat():
                    continue
                next_right = min((day for day in rights.get(row['symbol'], []) if day > ex_day), default=None)
                filled = next((i+1 for i, price in enumerate(prices)
                               if (next_right is None or date.fromisoformat(price['date']) < next_right)
                               and price['close'] >= pre_close), None)
                if filled is not None:
                    successes += 1
                    measured += 1
                    days.append(filled)
                elif len(prices) == 60 and (next_right is None or next_right > date.fromisoformat(prices[-1]['date'])):
                    measured += 1
            row['fill_probability_pct'] = round(successes/measured*100, 1) if measured else None
            row['fill_days_avg'] = round(sum(days)/len(days), 1) if days else None
            row['fill_successes'] = successes
            row['fill_sample'] = measured
    return first_day.isoformat()


def parse_wantgoo_fill_data(data, source_day):
    """Reproduce the fill-rate calculation used by WantGoo's dividend ranking."""
    if not isinstance(data, list) or len(data) < 5000:
        raise ValueError('玩股網除息紀錄筆數不足')
    grouped = defaultdict(list)
    for item in data:
        if not isinstance(item, dict) or not {'stockNo', 'date', 'cashDividend', 'recoveryDate'} <= item.keys():
            raise ValueError('玩股網除息紀錄格式已變更')
        symbol = str(item['stockNo']).strip()
        cash = _number(item['cashDividend'])
        if not re.fullmatch(r'\d{4,6}[A-Z]?', symbol) or cash is None or cash <= 0:
            continue
        try:
            ex_day = date.fromisoformat(str(item['date']).replace('/', '-'))
            recovered = (date.fromisoformat(str(item['recoveryDate'])[:10])
                         if item['recoveryDate'] else None)
        except ValueError as error:
            raise ValueError('玩股網除息或填息日期格式已變更') from error
        if ex_day > source_day:
            continue
        days = (recovered - ex_day).days + 1 if recovered and recovered <= source_day else None
        grouped[symbol].append((ex_day, days if days is not None and 1 <= days <= 360 else None))
    if len(grouped) < 1500:
        raise ValueError('玩股網個股除息覆蓋不足')
    result = {}
    for symbol, observations in grouped.items():
        filled_days = [days for _, days in observations if days is not None]
        result[symbol] = dict(fill_sample=len(observations), fill_successes=len(filled_days),
                              fill_days_total=sum(filled_days),
                              wantgoo_last_ex_date=max(day for day, _ in observations).isoformat())
    return result


def _wantgoo_fill_stats():
    """Refresh the public source once; retain the previous verified baseline on failure."""
    _tables()
    source_day = datetime.now(TZ).date()
    try:
        response = requests.get(WANTGOO_EX_HISTORY, headers=WANTGOO_HEADERS, timeout=(12, 35))
        response.raise_for_status()
        data = response.json()
        stats = parse_wantgoo_fill_data(data, source_day)
        calendar = parse_dividend_calendar(data)
        fetched_at = datetime.now(TZ).isoformat(timespec='seconds')
        saved = dict(stats=stats, calendar=calendar, source_day=source_day.isoformat())
        with db.connect() as conn:
            conn.execute('INSERT OR REPLACE INTO dividend_wantgoo_cache VALUES (1,?,?)',
                         (json.dumps(saved, ensure_ascii=False, allow_nan=False), fetched_at))
        return stats, source_day.isoformat(), fetched_at, 'live', calendar
    except (requests.RequestException, ValueError):
        with db.connect() as conn:
            cached = conn.execute('SELECT payload,fetched_at FROM dividend_wantgoo_cache WHERE id=1').fetchone()
        if cached:
            saved = json.loads(cached['payload'])
            return saved['stats'], saved['source_day'], cached['fetched_at'], 'cached', saved.get('calendar', {})
        return {}, None, None, 'unavailable', {}


def _merge_wantgoo_fills(rows, official_events, target, wantgoo_stats):
    """Use WantGoo's baseline, then add only official ex-dates it has not listed."""
    by_symbol = defaultdict(dict)
    for symbol, day, cash, _, *rest in official_events:
        if cash > 0 and day <= target and rest and rest[0] and rest[0] > 0:
            by_symbol[symbol][day] = rest[0]
    with db.connect() as conn:
        for row in rows:
            baseline = wantgoo_stats.get(row['symbol'])
            if not baseline:
                row['fill_source'] = '本機'
                row['fill_new_events'] = 0
                continue
            successes = baseline['fill_successes']
            sample = baseline['fill_sample']
            days_total = baseline['fill_days_total']
            newest = date.fromisoformat(baseline['wantgoo_last_ex_date'])
            added = 0
            for ex_day, pre_close in sorted(by_symbol.get(row['symbol'], {}).items()):
                if ex_day <= newest:
                    continue
                prices = conn.execute(
                    'SELECT date,high,close FROM prices WHERE symbol=? AND date>=? AND date<=? ORDER BY date',
                    (row['symbol'], ex_day.isoformat(), target.isoformat())).fetchall()
                if not prices or prices[0]['date'] != ex_day.isoformat():
                    continue
                sample += 1
                added += 1
                first_fill = next((price for price in prices
                                   if (price['high'] or price['close']) >= pre_close
                                   and (date.fromisoformat(price['date'])-ex_day).days < 360), None)
                if first_fill:
                    successes += 1
                    days_total += (date.fromisoformat(first_fill['date'])-ex_day).days + 1
            row['fill_probability_pct'] = round(successes/sample*100, 2) if sample else None
            row['fill_days_avg'] = round(days_total/successes, 2) if successes else None
            row['fill_successes'] = successes
            row['fill_sample'] = sample
            row['fill_source'] = '玩股網＋本機新除息' if added else '玩股網'
            row['fill_new_events'] = added


def _etf_fill_metrics(rows, distributions, target, wantgoo_stats, share_changes=()):
    """Use dated ETF recovery records, adding only newer official ex-dividends.

    A new event needs a local pre-ex close and ex-date quote. A later split
    censors the event until the source publishes an adjusted recovery date.
    """
    by_symbol = defaultdict(dict)
    for symbol, ex_day, cash in distributions:
        if cash > 0 and ex_day <= target:
            previous = by_symbol[symbol].get(ex_day)
            if previous is not None and abs(previous-cash) > 1e-7:
                raise ValueError(f'{symbol} {ex_day} ETF 除息金額互相矛盾')
            by_symbol[symbol][ex_day] = cash
    with db.connect() as conn:
        for row in rows:
            symbol = row['symbol']
            baseline = wantgoo_stats.get(symbol)
            successes = baseline['fill_successes'] if baseline else 0
            sample = baseline['fill_sample'] if baseline else 0
            days_total = baseline['fill_days_total'] if baseline else 0
            newest = date.fromisoformat(baseline['wantgoo_last_ex_date']) if baseline else date.min
            added = 0
            for ex_day in sorted(day for day in by_symbol.get(symbol, ()) if day > newest):
                prior = conn.execute(
                    'SELECT close FROM prices WHERE symbol=? AND date<? ORDER BY date DESC LIMIT 1',
                    (symbol, ex_day.isoformat())).fetchone()
                if not prior or not prior['close'] or prior['close'] <= 0:
                    continue
                prices = conn.execute(
                    'SELECT date,high,close FROM prices WHERE symbol=? AND date>=? AND date<=? ORDER BY date',
                    (symbol, ex_day.isoformat(), target.isoformat())).fetchall()
                if not prices or prices[0]['date'] != ex_day.isoformat():
                    continue
                split_day = min((change['date'] for change in share_changes
                                 if change['symbol'] == symbol and ex_day.isoformat() < change['date'] <= target.isoformat()),
                                default=None)
                if split_day:
                    continue
                sample += 1
                added += 1
                first_fill = next((price for price in prices
                                   if (date.fromisoformat(price['date'])-ex_day).days < 360
                                   and (price['high'] or price['close']) >= prior['close']), None)
                if first_fill:
                    successes += 1
                    days_total += (date.fromisoformat(first_fill['date'])-ex_day).days + 1
            row['fill_probability_pct'] = round(successes/sample*100, 2) if sample else None
            row['fill_days_avg'] = round(days_total/successes, 2) if successes else None
            row['fill_successes'] = successes
            row['fill_sample'] = sample
            row['fill_source'] = ('玩股網＋本機新除息' if baseline and added else
                                  '玩股網' if baseline else '本機' if added else '無歷史樣本')
            row['fill_new_events'] = added


def parse_twse_etf(html):
    table = BeautifulSoup(html, 'html.parser').select_one('table#myTable')
    if table is None:
        raise ValueError('證交所 ETF 配息表格格式已變更')
    rows = []
    for tr in table.select('tbody tr'):
        cells = [cell.get_text(' ', strip=True) for cell in tr.select('td')]
        if len(cells) < 6:
            continue
        symbol = cells[0].strip()
        if not re.fullmatch(r'\d{4,6}[A-Z]?', symbol):
            continue
        ex_day = _roc_day(cells[2])
        amount = _number(cells[5])
        if amount is not None and amount > 0:
            rows.append((symbol, ex_day, amount))
    if len(rows) < 200:
        raise ValueError('證交所 ETF 配息筆數不足')
    return rows


def parse_tpex_etf(data):
    if not isinstance(data, list) or len(data) < 300:
        raise ValueError('櫃買中心 ETF 配息清單格式已變更')
    rows = []
    for item in data:
        if not isinstance(item, dict) or not {'stockNo', 'divDate', 'amount'} <= item.keys():
            raise ValueError('櫃買中心 ETF 配息欄位已變更')
        symbol = str(item['stockNo']).strip()
        if not re.fullmatch(r'\d{4,6}[A-Z]?', symbol):
            continue
        ex_day = _roc_day(item['divDate'])
        amount = _number(item['amount'])
        if amount is not None and amount > 0:
            rows.append((symbol, ex_day, amount))
    if len(rows) < 200:
        raise ValueError('櫃買中心 ETF 已除息資料不足')
    return rows


def _quotes(target):
    with db.connect() as conn:
        rows = conn.execute('SELECT symbol,name,close,market FROM prices WHERE date=? AND market IN (\'TWSE\',\'TPEX\')', (target.isoformat(),)).fetchall()
    return {row['symbol']: dict(row) for row in rows}


def latest_market_day():
    with db.connect() as conn:
        row = conn.execute("SELECT MAX(date) FROM prices WHERE symbol='TAIEX'").fetchone()
    return row[0] if row else None


def _build(target, quotes, stock_rows, distributions, notices, holidays,
           stock_cash_events, incomplete_cash_symbols=frozenset(),
           fallback_etfs=None, fallback_etfs_as_of=None,
           wantgoo_stats=None, wantgoo_source_day=None,
           wantgoo_fetched_at=None, wantgoo_status='unavailable', dividend_calendar=None, share_changes=(), etf_share_changes=()):
    start = target - timedelta(days=365)
    payouts = defaultdict(dict)
    for symbol, ex_day, amount in distributions:
        if start < ex_day <= target:
            old = payouts[symbol].get(ex_day)
            if old is not None and abs(old-amount) > 1e-7:
                raise ValueError(f'{symbol} {ex_day} ETF 配息來源互相矛盾')
            payouts[symbol][ex_day] = amount
    upcoming = defaultdict(list)
    for symbol, ex_day in notices:
        if ex_day < target:
            continue
        last_buy = previous_trading_day(ex_day, holidays, target.year)
        if last_buy is not None and last_buy >= target:
            upcoming[symbol].append((last_buy, ex_day))
    next_dates = {symbol: min(events) for symbol, events in upcoming.items()}
    etfs = list(fallback_etfs) if fallback_etfs is not None else []
    if fallback_etfs is None:
        for symbol, quote in quotes.items():
            if not symbol.startswith('00') or quote['market'] not in {'TWSE', 'TPEX'}:
                continue
            events = payouts.get(symbol, {})
            cash = sum(_adjust_etf_cash(symbol, day, amount, target, etf_share_changes)
                       for day, amount in events.items())
            yield_pct = round(cash / quote['close'] * 100, 2)
            last_buy, next_ex = next_dates.get(symbol, (None, None))
            etfs.append(dict(symbol=symbol, name=quote['name'], market='上市' if quote['market']=='TWSE' else '上櫃',
                             close=quote['close'], yield_pct=yield_pct, cash_per_unit=round(cash, 4),
                             distribution_count=len(events), last_ex_date=max(events).isoformat() if events else None,
                             last_buy_date=last_buy.isoformat() if last_buy else None,
                             next_ex_date=next_ex.isoformat() if next_ex else None))
    stocks = [row for row in stock_rows if row['symbol'] in quotes and quotes[row['symbol']]['market']==('TWSE' if row['market']=='上市' else 'TPEX')]
    for row in stocks:
        last_buy, next_ex = next_dates.get(row['symbol'], (None, None))
        row['last_buy_date'] = last_buy.isoformat() if last_buy else None
        row['next_ex_date'] = next_ex.isoformat() if next_ex else None
    _stock_cash_estimates(stocks, stock_cash_events, target, incomplete_cash_symbols, dividend_calendar, share_changes)
    fill_window_start = _fill_metrics(stocks, stock_cash_events, target)
    if wantgoo_stats:
        _merge_wantgoo_fills(stocks, stock_cash_events, target, wantgoo_stats)
    else:
        for row in stocks:
            row['fill_source'] = '本機'
            row['fill_new_events'] = 0
    _etf_fill_metrics(etfs, distributions if fallback_etfs is None else (), target,
                      wantgoo_stats or {}, etf_share_changes)
    if len(stocks) < 1300 or len(etfs) < 150:
        raise ValueError('本機同日行情或官方殖利率覆蓋不足，保留舊排行')
    stocks.sort(key=lambda r: (r['yield_pct'] is None, -(r['yield_pct'] or 0), r['symbol']))
    etfs.sort(key=lambda r: (-r['yield_pct'], r['symbol']))
    return dict(as_of=target.isoformat(), yield_version=2, fill_window_start=fill_window_start,
                yield_audit={key:sum(row['yield_method']==key for row in stocks)
                             for key in ('annual','zero','previous_year','last_paid','unavailable')},
                wantgoo_source_day=wantgoo_source_day, wantgoo_fetched_at=wantgoo_fetched_at,
                wantgoo_status=wantgoo_status,
                etfs_as_of=fallback_etfs_as_of or target.isoformat(),
                etfs_reused=fallback_etfs is not None, stocks=stocks, etfs=etfs,
                window_start=start.isoformat(), fetched_at=datetime.now(TZ).isoformat(timespec='seconds'),
                sources={'twse_stock':TWSE_STOCK, 'tpex_stock':TPEX_STOCK,
                         'twse_ex_history':TWSE_EX_HISTORY, 'tpex_ex_history':TPEX_EX_HISTORY,
                         'twse_etf':TWSE_ETF, 'tpex_etf':TPEX_ETF,
                         'twse_notice':TWSE_EX_NOTICE, 'tpex_notice':TPEX_EX_NOTICE,
                         'holidays':HOLIDAY_SCHEDULE, 'wantgoo_fill':WANTGOO_EX_HISTORY,
                         'dividend_calendar':WANTGOO_EX_HISTORY, **CORPORATE_SOURCES})


def _download(target):
    quotes = _quotes(target)
    if len(quotes) < 1500:
        raise ValueError('最新收盤價尚未補齊上市櫃行情')
    twse = _get(TWSE_STOCK, params={'date':target.strftime('%Y%m%d'),'selectType':'ALL','response':'json'}).json()
    roc = f'{target.year-1911}/{target.month:02d}/{target.day:02d}'
    tpex = _get(TPEX_STOCK, params={'l':'zh-tw','o':'json','d':roc,'s':'0'}).json()
    stock_rows = parse_twse_stocks(twse,target) + parse_tpex_stocks(tpex,target,quotes)
    history_start = target - timedelta(days=730)
    share_changes = _share_changes(history_start, target)
    twse_history = _twse_ex_history_chunks(history_start,target)
    tpex_history = _get(TPEX_EX_HISTORY, params={
        'startDate':f'{history_start.year-1911}/{history_start.month:02d}/{history_start.day:02d}',
        'endDate':f'{target.year-1911}/{target.month:02d}/{target.day:02d}'}).json()
    twse_cash_events, incomplete_cash_symbols = parse_twse_ex_history(twse_history,target)
    stock_cash_events = twse_cash_events + parse_tpex_ex_history(tpex_history,target)
    years = {'startDate':str((target-timedelta(days=365)).year),'endDate':str(target.year)}
    old = report()
    fallback_etfs = None
    fallback_etfs_as_of = None
    distributions = []
    etf_share_changes = []
    if fallback_etfs is None:
        try:
            twse_etf = _get(TWSE_ETF, params=years).text
            tpex_etf = _post(TPEX_ETF, data={'stkNo':'','startDate':f'{target.year-1}0101','endDate':f'{target.year}1231','lang':'zh-tw'},
                                     headers={**HEADERS,'Origin':'https://info.tpex.org.tw','Referer':'https://info.tpex.org.tw/ETF/zh/dividend-list.html'})
            distributions = parse_twse_etf(twse_etf) + parse_tpex_etf(tpex_etf.json())
            etf_share_changes = _etf_share_changes(target-timedelta(days=365), target)
        except (requests.RequestException, ValueError):
            if not old.get('etfs') or not old.get('as_of'):
                raise
            fallback_etfs, fallback_etfs_as_of = old['etfs'], old.get('etfs_as_of') or old['as_of']
    twse_notices = parse_twse_notices(_get(TWSE_EX_NOTICE).json())
    tpex_response = requests.post(TPEX_EX_NOTICE, data={},
                                  headers={**HEADERS, 'Referer': 'https://www.tpex.org.tw/zh-tw/announce/market/ex/announce.html'},
                                  timeout=(12, 35))
    tpex_response.raise_for_status()
    tpex_notices = parse_tpex_notices(tpex_response.json())
    holidays = parse_holidays(_get(HOLIDAY_SCHEDULE).json(), target.year)
    wantgoo_stats, wantgoo_source_day, wantgoo_fetched_at, wantgoo_status, dividend_calendar = _wantgoo_fill_stats()
    if not dividend_calendar:
        raise ValueError('股利年度與發放日對照暫不可用，保留舊排行')
    return _build(target, quotes, stock_rows, distributions, twse_notices + tpex_notices,
                  holidays, stock_cash_events, incomplete_cash_symbols,
                  fallback_etfs, fallback_etfs_as_of,
                  wantgoo_stats, wantgoo_source_day, wantgoo_fetched_at, wantgoo_status, dividend_calendar, share_changes, etf_share_changes)


def report():
    _tables()
    with db.connect() as conn:
        row = conn.execute('SELECT payload FROM dividend_yield_days ORDER BY date DESC LIMIT 1').fetchone()
    saved = json.loads(row[0]) if row else {'as_of':None,'stocks':[],'etfs':[],'fetched_at':None,'sources':{}}
    return {**saved,'latest_market_day':latest_market_day(),'job':dict(_state)}


def refresh():
    if not _lock.acquire(blocking=False):
        return report()
    target_text = latest_market_day()
    if not target_text:
        _lock.release()
        raise ValueError('本機尚無大盤收盤行情，請先更新市場資料')
    _state.update(status='running',message=f'正在更新 {target_text} 上市櫃與 ETF 殖利率')

    def worker():
        try:
            payload = _download(date.fromisoformat(target_text))
            _tables()
            with db.connect() as conn:
                conn.execute('INSERT OR REPLACE INTO dividend_yield_days VALUES (?,?,?)',
                             (target_text,json.dumps(payload,ensure_ascii=False,allow_nan=False),payload['fetched_at']))
            stale_etfs = payload['etfs_as_of'] != target_text
            incomplete = payload['yield_audit']['unavailable']
            _state.update(status='partial' if stale_etfs or incomplete else 'complete',
                          message=(f"股票已更新至 {target_text}；ETF 官方來源暫不可用，沿用 {payload['etfs_as_of']} 已保存資料"
                                   if stale_etfs else f"已更新 {target_text} · 股票 {len(payload['stocks'])} 檔／ETF {len(payload['etfs'])} 檔"
                                   + (f'；{incomplete} 檔年度或股數資料待核對，殖利率留空' if incomplete else '')))
        except Exception as error:
            _state.update(status='failed',message=f'官方資料取得失敗或格式變更：{type(error).__name__} {str(error)[:120]}；已保存的舊排行保留')
        finally:
            _lock.release()
    threading.Thread(target=worker,daemon=True).start()
    return report()
