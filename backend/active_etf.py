"""Daily holdings snapshots for the four tracked Taiwan active ETFs."""
from __future__ import annotations

import json
import calendar
import re
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from . import db, sources

TZ = ZoneInfo('Asia/Taipei')
_lock = threading.Lock()

FUNDS = {
    '00981A': {
        'name': '主動統一台股增長', 'issuer': '統一投信', 'kind': 'ezmoney',
        'fund_code': '49YTW', 'source': 'https://www.ezmoney.com.tw/ETF/Transaction/PCF',
    },
    '00400A': {
        'name': '主動國泰動能高息', 'issuer': '國泰投信', 'kind': 'cathay',
        'fund_code': 'EA', 'source': 'https://www.cathaysite.com.tw/ETF/purchase?code=EA',
    },
    '00980A': {
        'name': '主動野村臺灣優選', 'issuer': '野村投信', 'kind': 'nomura',
        'fund_code': '00980A', 'source': 'https://www.nomurafunds.com.tw/ETFWEB/pcf',
    },
    '00985A': {
        'name': '主動野村台灣50', 'issuer': '野村投信', 'kind': 'nomura',
        'fund_code': '00985A', 'source': 'https://www.nomurafunds.com.tw/ETFWEB/pcf',
    },
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; LocalBranchMonitor/1.0)',
    'Accept': 'application/json,text/html;q=0.9,*/*;q=0.8',
}


def now():
    return datetime.now(TZ).isoformat(timespec='seconds')


def _number(value):
    try:
        return float(str(value).replace(',', '').replace('%', '').strip())
    except (TypeError, ValueError):
        return None


def _request(session, method, url, **kwargs):
    headers = {**HEADERS, **kwargs.pop('headers', {})}
    last_error = None
    for attempt in range(3):
        try:
            response = session.request(method, url, headers=headers, timeout=(12, 35), **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            last_error = error
            if attempt < 2:
                time.sleep(1.2 * (attempt + 1))
    raise sources.SourceError(f'來源連線失敗：{type(last_error).__name__}') from last_error


def _dotnet_date(value):
    text = str(value or '')
    iso_match = re.match(r'(\d{4})-(\d{2})-(\d{2})', text)
    if iso_match:
        return '-'.join(iso_match.groups())
    match = re.search(r'/Date\((\d+)', text)
    if not match:
        return None
    # The legacy ASP.NET payload represents the issuer's Taipei midnight as an
    # epoch value. Interpret it in the issuer timezone before comparing dates.
    return datetime.fromtimestamp(int(match.group(1)) / 1000, TZ).date().isoformat()


def _validate(day, rows):
    clean = []
    seen = set()
    for row in rows:
        symbol = str(row.get('symbol') or '').strip().upper()
        name = re.sub(r'\s+', ' ', str(row.get('name') or '').strip())
        shares = _number(row.get('shares'))
        weight = _number(row.get('weight'))
        if not re.fullmatch(r'\d{4}[A-Z]?', symbol) or not name or shares is None or weight is None:
            continue
        if symbol in seen or shares < 0 or not 0 <= weight <= 100:
            raise sources.SourceError(f'{day} 官方持股欄位異常，未寫入')
        seen.add(symbol)
        clean.append({'symbol': symbol, 'name': name, 'shares': int(round(shares)), 'weight': round(weight, 4)})
    if len(clean) < 10:
        raise sources.SourceError(f'{day} 官方持股筆數不足，未寫入')
    if sum(row['weight'] for row in clean) > 115:
        raise sources.SourceError(f'{day} 官方持股權重異常，未寫入')
    return sorted(clean, key=lambda row: (-row['weight'], row['symbol']))


def _parse_ezmoney_snapshot(data):
    """PCF PostDate is the next subscription day, not the holdings date."""
    pcf = data.get('pcf') or []
    if not pcf:
        return None
    holdings_dates = {_dotnet_date(row.get('TranDate')) for row in pcf}
    pcf_dates = {_dotnet_date(row.get('PostDate')) for row in pcf}
    if len(holdings_dates) != 1 or None in holdings_dates or len(pcf_dates) != 1 or None in pcf_dates:
        raise sources.SourceError('統一投信持股日／清單適用日不一致，未寫入')
    actual, pcf_date = next(iter(holdings_dates)), next(iter(pcf_dates))
    if pcf_date <= actual:
        raise sources.SourceError('統一投信清單適用日未晚於持股日，未寫入')
    stock = next((asset for asset in data.get('asset') or [] if asset.get('AssetCode') == 'ST'), None)
    details = (stock or {}).get('Details') or []
    if not details or any(_dotnet_date(row.get('TranDate')) != actual for row in details):
        raise sources.SourceError('統一投信股票明細持股日不一致，未寫入')
    rows = [{'symbol': row.get('DetailCode'), 'name': row.get('DetailName'),
             'shares': row.get('Share'), 'weight': row.get('NavRate')} for row in details]
    return actual, _validate(actual, rows), {
        'date_basis': 'holdings_tran_date_v2', 'holdings_as_of': actual,
        'source_pcf_date': pcf_date,
    }


def _fetch_ezmoney(info, day, session):
    page = info['source']
    _request(session, 'GET', page)
    target = date.fromisoformat(day)
    latest = None
    # A Friday's holdings can be in Monday's PCF; exchange holidays can extend
    # this gap. Never subtract one calendar day or relabel an older snapshot.
    for offset in range(1, 15):
        candidate = target + timedelta(days=offset)
        if candidate.weekday() >= 5:
            continue
        roc = f'{candidate.year-1911}/{candidate.month:02}/{candidate.day:02}'
        response = _request(session, 'POST', 'https://www.ezmoney.com.tw/ETF/Transaction/GetPCF',
                            headers={'Content-Type': 'application/json; charset=utf-8',
                                     'X-Requested-With': 'XMLHttpRequest',
                                     'Origin': 'https://www.ezmoney.com.tw', 'Referer': page},
                            json={'fundCode': info['fund_code'], 'date': roc, 'specificDate': True})
        snapshot = _parse_ezmoney_snapshot(response.json())
        if snapshot is None:
            continue
        latest = snapshot[0]
        if latest == day:
            return snapshot
        if latest > day:
            break
    raise sources.SourceError(f'統一投信實際持股日 {latest or "尚未公告"} 與 {day} 不符，保留原資料')


def _fetch_cathay(info, day, session):
    base = 'https://cwapi.cathaysite.com.tw/api/'
    params = {'FundCode': info['fund_code'], 'SearchDate': day.replace('-', '/'), 'IsTest': 'false'}
    meta = _request(session, 'GET', base + 'BuySale/GetBuySale', params=params).json()
    result = meta.get('result') or {}
    actual = str(result.get('date') or '').replace('/', '-')
    if meta.get('returnCode') != '2000' or actual != day or result.get('stockCode') != '00400A':
        raise sources.SourceError(f'國泰投信來源日期 {actual or "未知"} 與 {day} 不符')
    details = _request(session, 'GET', base + 'ETF/GetETFDetailStockList', params=params).json()
    if details.get('returnCode') != '2000':
        raise sources.SourceError(f'{day} 國泰投信持股明細尚未公告')
    rows = [{'symbol': row.get('stockCode'), 'name': row.get('stockName'),
             'shares': row.get('volumn'), 'weight': row.get('weights')}
            for row in details.get('result') or []]
    return actual, _validate(day, rows)


def _fetch_nomura(info, day, session):
    url = 'https://www.nomurafunds.com.tw/API/ETFAPI/api/Fund/GetFundAssets'
    data = _request(session, 'POST', url, headers={'Content-Type': 'application/json'},
                    json={'FundID': info['fund_code'], 'SearchDate': day}).json()
    if data.get('StatusCode') != 0:
        raise sources.SourceError(f'{day} 野村投信持股明細尚未公告')
    entries = data.get('Entries') or {}
    if entries.get('FundID') != info['fund_code']:
        raise sources.SourceError('野村投信基金代碼不符')
    tables = ((entries.get('Data') or {}).get('Table') or [])
    table = next((row for row in tables if row.get('TableTitle') == '股票'), None)
    actual = str((table or {}).get('NavDate') or '').replace('/', '-')
    if actual != day:
        raise sources.SourceError(f'野村投信來源日期 {actual or "未知"} 與 {day} 不符')
    rows = [{'symbol': row[0], 'name': row[1], 'shares': row[2], 'weight': row[3]}
            for row in (table or {}).get('Rows') or [] if len(row) >= 4]
    return actual, _validate(day, rows)


FETCHERS = {'ezmoney': _fetch_ezmoney, 'cathay': _fetch_cathay, 'nomura': _fetch_nomura}


def _trading_days(target=None, count=2):
    with db.connect() as conn:
        if target:
            values = [row[0] for row in conn.execute(
                "SELECT date FROM market_days WHERE date<=? AND (twse_status='ok' OR tpex_status='ok') ORDER BY date DESC LIMIT ?",
                (target, count))]
        else:
            values = [row[0] for row in conn.execute(
                "SELECT date FROM market_days WHERE twse_status='ok' OR tpex_status='ok' ORDER BY date DESC LIMIT ?", (count,))]
    if values:
        return sorted(set(values))
    cursor = date.fromisoformat(target) if target else datetime.now(TZ).date()
    fallback = []
    while len(fallback) < count:
        if cursor.weekday() < 5:
            fallback.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return sorted(fallback)


def job_status():
    with db.connect() as conn:
        row = conn.execute('SELECT * FROM active_etf_state WHERE id=1').fetchone()
    return dict(row) if row else {'status': 'idle', 'message': '尚未建立主動 ETF 快照'}


def latest_complete_date():
    with db.connect() as conn:
        row = conn.execute('SELECT date,COUNT(*) AS count FROM active_etf_days GROUP BY date HAVING COUNT(*)=? ORDER BY date DESC LIMIT 1',
                           (len(FUNDS),)).fetchone()
    return row['date'] if row else None


def refresh(target=None):
    if target:
        try:
            date.fromisoformat(target)
        except ValueError as error:
            raise ValueError('日期格式需為 YYYY-MM-DD') from error
    if not _lock.acquire(blocking=False):
        return job_status()
    started = now()
    with db.connect() as conn:
        conn.execute('INSERT OR REPLACE INTO active_etf_state VALUES (1,?,?,NULL,?)',
                     ('running', started, '正在讀取四檔官方申購買回清單'))
    try:
        threading.Thread(target=_worker, args=(target,), daemon=True).start()
    except Exception:
        _lock.release()
        raise
    return job_status()


def _worker(target=None):
    errors = []
    saved = 0
    try:
        days = _trading_days(target, 2)
        for day in days:
            for fund, info in FUNDS.items():
                with db.connect() as conn:
                    conn.execute('UPDATE active_etf_state SET message=? WHERE id=1',
                                 (f'讀取 {fund} · {day}',))
                try:
                    session = requests.Session()
                    snapshot = FETCHERS[info['kind']](info, day, session)
                    actual, rows = snapshot[:2]
                    metadata = snapshot[2] if len(snapshot) > 2 else {}
                    if actual != day:
                        raise sources.SourceError(f'實際持股日 {actual} 與要求日期 {day} 不符，未寫入')
                    payload = {'fund': fund, 'name': info['name'], 'issuer': info['issuer'],
                               'date': actual, 'holdings': rows, **metadata}
                    with db.connect() as conn:
                        conn.execute('INSERT OR REPLACE INTO active_etf_days VALUES (?,?,?,?,?)',
                                     (fund, actual, json.dumps(payload, ensure_ascii=False), info['source'], now()))
                    saved += 1
                except Exception as error:
                    message = str(error) if isinstance(error, ValueError) else type(error).__name__
                    errors.append(f'{fund} {day}：{message}')
        latest = max(days) if days else None
        with db.connect() as conn:
            have = conn.execute('SELECT COUNT(*) FROM active_etf_days WHERE date=?', (latest,)).fetchone()[0]
            state = 'complete' if have == len(FUNDS) and not errors else 'partial' if saved or have else 'failed'
            message = f'主動 ETF 已保存至 {latest} · {have}/{len(FUNDS)} 檔'
            if errors:
                message += f'；{len(errors)} 筆來源待補'
            conn.execute('UPDATE active_etf_state SET status=?,finished_at=?,message=? WHERE id=1',
                         (state, now(), message))
    except Exception as error:
        with db.connect() as conn:
            conn.execute('UPDATE active_etf_state SET status=?,finished_at=?,message=? WHERE id=1',
                         ('failed', now(), f'主動 ETF 更新失敗：{type(error).__name__}'))
    finally:
        _lock.release()


def _change_rows(current, previous):
    before = {row['symbol']: row for row in previous}
    after = {row['symbol']: row for row in current}
    rows = []
    for symbol in sorted(set(before) | set(after)):
        old, new = before.get(symbol), after.get(symbol)
        old_shares = old['shares'] if old else 0
        new_shares = new['shares'] if new else 0
        if new_shares == old_shares:
            continue
        kind = 'new' if old is None else 'removed' if new is None else 'increase' if new_shares > old_shares else 'decrease'
        rows.append({'symbol': symbol, 'name': (new or old)['name'], 'kind': kind,
                     'previous_shares': old_shares, 'shares': new_shares, 'change_shares': new_shares - old_shares,
                     'previous_weight': old['weight'] if old else 0, 'weight': new['weight'] if new else 0,
                     'change_weight': round((new['weight'] if new else 0) - (old['weight'] if old else 0), 4)})
    return sorted(rows, key=lambda row: (-abs(row['change_shares']), row['symbol']))


def _months_before(value, months):
    year = value.year
    month = value.month - months
    while month <= 0:
        year -= 1
        month += 12
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def _price_performance(symbol, as_of):
    """Return MTD, six-month and YTD price changes using local exchange closes."""
    if not as_of:
        return None
    with db.connect() as conn:
        prices = {row['date']: float(row['close']) for row in conn.execute(
            'SELECT date,close FROM prices WHERE symbol=? AND date<=? ORDER BY date', (symbol, as_of))}
        # ETF RS stores a few verified anchor snapshots outside the ordinary
        # branch-history range, including the prior year-end close.
        for row in conn.execute('SELECT date,payload FROM rs_quotes WHERE date<=? ORDER BY date', (as_of,)):
            try:
                quote = next((item for item in json.loads(row['payload']) if item.get('symbol') == symbol), None)
                close = _number((quote or {}).get('close'))
                if close and close > 0:
                    prices.setdefault(row['date'], close)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
    if not prices:
        return None
    ordered = sorted((day, close) for day, close in prices.items() if day <= as_of and close > 0)
    if not ordered:
        return None
    price_date, close = ordered[-1]
    current_day = date.fromisoformat(price_date)
    anchors = {
        'month': current_day.replace(day=1),
        'half_year': _months_before(current_day, 6),
        'ytd': current_day.replace(month=1, day=1),
    }
    output = {'close': close, 'price_date': price_date}
    for key, anchor in anchors.items():
        prior = [(day, value) for day, value in ordered if day < anchor.isoformat()]
        limited = False
        if prior:
            base_date, base_close = prior[-1]
        else:
            base_date, base_close = ordered[0]
            limited = True
        output[key] = {
            'return_pct': round((close / base_close - 1) * 100, 2) if base_close else None,
            'base_date': base_date, 'base_close': base_close, 'limited': limited,
        }
    return output


def report(selected=None):
    with db.connect() as conn:
        dates = [row[0] for row in conn.execute('SELECT DISTINCT date FROM active_etf_days ORDER BY date DESC')]
        current_date = selected if selected in dates else (dates[0] if dates else None)
        records = list(conn.execute('SELECT * FROM active_etf_days WHERE date=? ORDER BY fund', (current_date or '',)))
    funds = []
    all_changes = []
    current_by_fund = {row['fund']: row for row in records}
    for fund, info in FUNDS.items():
        row = current_by_fund.get(fund)
        payload = json.loads(row['payload']) if row else {'holdings': []}
        with db.connect() as conn:
            prior = conn.execute('SELECT * FROM active_etf_days WHERE fund=? AND date<? ORDER BY date DESC LIMIT 1',
                                 (fund, current_date or '')).fetchone()
        previous = json.loads(prior['payload']) if prior else {'holdings': []}
        changes = _change_rows(payload['holdings'], previous['holdings']) if row and prior else []
        counts = {key: sum(change['kind'] == key for change in changes)
                  for key in ('new', 'increase', 'decrease', 'removed')}
        item = {'fund': fund, **{key: info[key] for key in ('name', 'issuer', 'source')},
                'date': row['date'] if row else None, 'previous_date': prior['date'] if prior else None,
                'holdings_as_of': payload.get('holdings_as_of', row['date'] if row else None),
                'source_pcf_date': payload.get('source_pcf_date'),
                'holdings': payload['holdings'], 'changes': changes, 'counts': counts,
                'total_holdings': len(payload['holdings']),
                'performance': _price_performance(fund, row['date'] if row else current_date)}
        funds.append(item)
        all_changes.extend([{**change, 'fund': fund, 'fund_name': info['name']} for change in changes])
    grouped = defaultdict(list)
    for row in all_changes:
        grouped[row['symbol']].append(row)
    common = []
    for symbol, rows in grouped.items():
        buys = [row for row in rows if row['change_shares'] > 0]
        sells = [row for row in rows if row['change_shares'] < 0]
        for direction, selected_rows in (('buy', buys), ('sell', sells)):
            if len(selected_rows) >= 2:
                common.append({'symbol': symbol, 'name': selected_rows[0]['name'], 'direction': direction,
                               'fund_count': len(selected_rows), 'funds': [row['fund'] for row in selected_rows],
                               'change_shares': sum(row['change_shares'] for row in selected_rows)})
    common.sort(key=lambda row: (-row['fund_count'], -abs(row['change_shares']), row['symbol']))
    return {'available': bool(current_date), 'date': current_date, 'available_dates': dates, 'funds': funds,
            'common': common, 'job': job_status(),
            'note': '依實際持股日比較各投信官方總持股股數與權重；PCF 清單適用日另列，不等同持股日。未換算每百萬受益權單位，申購或買回也可能造成股數變化。'}
