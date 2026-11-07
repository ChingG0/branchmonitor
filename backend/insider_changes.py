"""Monthly changes reported for shareholders holding over 10% on MOPS."""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from . import db


TZ = ZoneInfo('Asia/Taipei')
SOURCE = 'https://mopsov.twse.com.tw/mops/web/t93sb06_1'
QUERY = 'https://mopsov.twse.com.tw/mops/web/ajax_t93sb06_1'
MARKETS = {'sii': '上市', 'otc': '上櫃', 'rotc': '興櫃', 'pub': '公開發行'}
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; BranchMonitor/1.0)', 'Referer': SOURCE}
_lock = threading.Lock()
_state = {'status': 'idle', 'done': 0, 'total': 0, 'message': ''}


def _tables():
    with db.connect() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS insider_change_months (
          year INTEGER NOT NULL, month INTEGER NOT NULL, market TEXT NOT NULL,
          payload TEXT NOT NULL, fetched_at TEXT NOT NULL,
          PRIMARY KEY(year, month, market)
        );
        ''')


def _int(value):
    cleaned = re.sub(r'[\s,]', '', value).replace('−', '-')
    if not re.fullmatch(r'-?\d+', cleaned):
        raise ValueError(f'股數不是整數：{value[:30]}')
    return int(cleaned)


def parse_month(html, year, month, market):
    """Reject format changes; never silently mark a broken response complete."""
    soup = BeautifulSoup(html, 'html.parser')
    table = soup.select_one('table.hasBorder')
    if table is None:
        raise ValueError('官方表格未出現')
    trs = table.select('tr')
    if not trs:
        raise ValueError('官方表格為空')
    headings = [cell.get_text(' ', strip=True) for cell in trs[0].find_all(['td', 'th'])]
    expected = ['公司代號', '公司名稱', '大股東名稱', '上月實際持股(股)', '本月實際持股(股)', '異動數']
    if headings != expected:
        raise ValueError('官方表格欄位已改變')
    rows = []
    seen = set()
    for tr in trs[1:]:
        cells = [cell.get_text(' ', strip=True) for cell in tr.find_all('td')]
        if len(cells) != 6:
            continue
        symbol, company, holder = (re.sub(r'\s+', ' ', c).strip() for c in cells[:3])
        if not symbol or not company or not holder:
            continue
        previous, current, change = map(_int, cells[3:])
        if previous < 0 or current < 0 or current - previous != change:
            raise ValueError(f'{symbol} {holder} 持股與異動數不一致')
        if change == 0:
            continue
        key = (symbol, holder)
        if key in seen:
            raise ValueError(f'{symbol} {holder} 重複列示')
        seen.add(key)
        rows.append({'year': year, 'month': month, 'market': market,
                     'market_name': MARKETS[market], 'symbol': symbol,
                     'company': company, 'holder': holder, 'previous_shares': previous,
                     'current_shares': current, 'change_shares': change})
    if len(trs) < 2:
        raise ValueError('官方表格沒有資料列')
    return rows


def _fetch(year, month, market):
    for attempt in range(2):
        try:
            with requests.Session() as session:
                session.get(SOURCE, headers=HEADERS, timeout=(10, 20)).raise_for_status()
                response = session.post(
                    QUERY, data={'step': '1', 'firstin': '1', 'off': '1', 'TYPEK': market,
                                 'year': str(year - 1911), 'month': f'{month:02d}'},
                    headers=HEADERS, timeout=(10, 30),
                )
                response.raise_for_status()
                rows = parse_month(response.text, year, month, market)
                return year, month, market, rows
        except requests.RequestException:
            if attempt:
                raise
            time.sleep(1)


def _months(year):
    today = datetime.now(TZ)
    if year == today.year:
        # The monthly post-event statement describes the previous month.
        last = today.month - 1
    else:
        last = 12
    return range(1, last + 1)


def _worker(year, force):
    errors = []
    try:
        _tables()
        months = list(_months(year))
        latest = months[-1] if months else 0
        with db.connect() as conn:
            existing = {(row['month'], row['market']) for row in conn.execute(
                'SELECT month,market FROM insider_change_months WHERE year=?', (year,))}
        targets = [(year, month, market) for month in months for market in MARKETS
                   if force or month == latest or (month, market) not in existing]
        _state.update(done=0, total=len(targets), message=f'查詢 {year} 年公開資訊觀測站月報')
        if targets:
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = {pool.submit(_fetch, *args): args for args in targets}
                for future in as_completed(futures):
                    year_, month, market = futures[future]
                    try:
                        _, _, _, rows = future.result()
                        with db.connect() as conn:
                            conn.execute('''INSERT INTO insider_change_months(year,month,market,payload,fetched_at)
                                VALUES(?,?,?,?,?) ON CONFLICT(year,month,market) DO UPDATE SET
                                payload=excluded.payload,fetched_at=excluded.fetched_at''',
                                (year_, month, market, json.dumps(rows, ensure_ascii=False),
                                 datetime.now(TZ).isoformat(timespec='seconds')))
                    except (requests.RequestException, ValueError) as error:
                        errors.append(f'{month:02d}月{MARKETS[market]}：{type(error).__name__}')
                    except Exception as error:
                        errors.append(f'{month:02d}月{MARKETS[market]}：{type(error).__name__}')
                    _state['done'] += 1
        _state['status'] = 'partial' if errors else 'complete'
        _state['message'] = '；'.join(errors[:8]) if errors else ('今年月報已更新' if months else '今年尚未有已結束的月份')
    except Exception as error:
        _state.update(status='failed', message=f'更新失敗：{type(error).__name__}')
    finally:
        _lock.release()


def refresh(force=False):
    _tables()
    if not _lock.acquire(blocking=False):
        return {'status': 'running', **_state}
    year = datetime.now(TZ).year
    _state.update(status='running', done=0, total=0, message=f'準備讀取 {year} 年資料')
    try:
        threading.Thread(target=_worker, args=(year, bool(force)), daemon=True).start()
    except Exception:
        _lock.release()
        raise
    return {'status': 'running', **_state}


def _next_month(year, month):
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _monthly_price_windows(conn, year):
    """Use the first open and final close only for verified, completed months."""
    current = datetime.now(TZ).strftime('%Y-%m')
    calendar = {}
    for row in conn.execute('''SELECT date,twse_status,tpex_status FROM market_days
        WHERE date>=? AND date<? ORDER BY date''', (f'{year}-01-01', current + '-01')):
        calendar.setdefault(row['date'][:7], []).append(row)
    first_days, final_days = {}, {}
    for month, days in calendar.items():
        completed = [item for item in days if item['twse_status'] == item['tpex_status'] == 'ok']
        if not completed:
            continue
        last = completed[-1]
        if any(item['twse_status'] != item['tpex_status'] or item['twse_status'] != 'no_data'
               for item in days if item['date'] > last['date']):
            continue
        first_days[month] = completed[0]['date']
        final_days[month] = last['date']
    if not final_days:
        return {}, {}
    dates = sorted(set(first_days.values()) | set(final_days.values()))
    placeholders = ','.join('?' for _ in dates)
    prices = {}
    for row in conn.execute(
            f'SELECT date,symbol,open,close FROM prices WHERE date IN ({placeholders})', dates):
        month = row['date'][:7]
        entry = prices.setdefault((month, row['symbol']), {})
        if row['date'] == first_days.get(month) and row['open'] and row['open'] > 0:
            entry['start_open'], entry['start_date'] = row['open'], row['date']
        if row['date'] == final_days.get(month) and row['close'] and row['close'] > 0:
            entry['end_close'], entry['end_date'] = row['close'], row['date']
    return prices, final_days


def _effect(row, prices, final_days):
    if row['market'] not in {'sii', 'otc'}:
        return {'status': 'unavailable'}
    next_year, next_month = _next_month(row['year'], row['month'])
    target_year, target_month = _next_month(next_year, next_month)
    target = f'{target_year}-{target_month:02d}'
    if target not in final_days:
        return {'status': 'pending', 'month': target}
    window = prices.get((target, row['symbol']), {})
    if 'start_open' not in window or 'end_close' not in window:
        return {'status': 'missing', 'month': target}
    pct = (window['end_close'] / window['start_open'] - 1) * 100
    return {'status': 'ready', 'return_pct': round(pct, 2),
            'direction': 'up' if pct > 0 else 'down' if pct < 0 else 'flat',
            'start_date': window['start_date'], 'end_date': window['end_date'],
            'start_open': window['start_open'], 'end_close': window['end_close']}


def _with_price_history(rows, prices, final_days):
    """One company/month/direction is one trial, even with multiple holders."""
    events = {}
    for row in rows:
        key = (row['market'], row['symbol'], row['month'], 1 if row['change_shares'] > 0 else -1)
        events.setdefault(key, _effect(row, prices, final_days))
    groups = {}
    for (market, symbol, month, side), outcome in events.items():
        if outcome['status'] != 'ready':
            continue
        groups.setdefault((market, symbol, side), []).append((month, outcome))
    for trials in groups.values():
        trials.sort(key=lambda item: item[0])
    for row in rows:
        side = 1 if row['change_shares'] > 0 else -1
        key = (row['market'], row['symbol'], row['month'], side)
        row['next_month_price'] = events[key]
        previous = [outcome for month, outcome in groups.get((row['market'], row['symbol'], side), [])
                    if month < row['month']]
        wins = sum(outcome['direction'] == ('up' if side > 0 else 'down') for outcome in previous)
        count = len(previous)
        row['history'] = {'wins': wins, 'total': count,
                          'rate_pct': round(100 * wins / count, 1) if count else None}
    return rows


def report():
    _tables()
    year = datetime.now(TZ).year
    with db.connect() as conn:
        snapshots = conn.execute('''SELECT month,market,payload,fetched_at FROM insider_change_months
            WHERE year=? ORDER BY month DESC,market''', (year,)).fetchall()
        prices, final_days = _monthly_price_windows(conn, year)
    rows = [row for snapshot in snapshots for row in json.loads(snapshot['payload'])]
    _with_price_history(rows, prices, final_days)
    rows.sort(key=lambda row: (-abs(row['change_shares']), -row['month'], row['symbol'], row['holder']))
    months = list(_months(year))
    coverage = [{'month': month, 'market': market, 'market_name': label,
                 'available': any(s['month'] == month and s['market'] == market for s in snapshots)}
                for month in months for market, label in MARKETS.items()]
    evaluated = sum(row['next_month_price']['status'] == 'ready' for row in rows)
    return {'year': year, 'rows': rows, 'count': len(rows), 'evaluated': evaluated,
            'price_through_month': max(final_days, default=None), 'coverage': coverage,
            'fetched_at': max((s['fetched_at'] for s in snapshots), default=None),
            'source': SOURCE, 'job': dict(_state)}
