"""On-demand Taiwan warrant lookup, comparison, calculator inputs and weekly broker flow."""
from __future__ import annotations

import json
import math
import re
import threading
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from . import db, sources

TZ = ZoneInfo('Asia/Taipei')
CMONEY = 'https://www.cmoney.tw/warrants/ashx/view/analysis.ashx'
CMONEY_QUICK = 'https://www.cmoney.tw/warrants/ashx/view/quicksearch.ashx'
MONEYDJ = 'https://concords.moneydj.com/z/zc/zco/zco_{code}_2.djhtm'
MONEYDJ_DAILY = 'https://concords.moneydj.com/z/zc/zco/zco_{code}.djhtm'
RISK_FREE = 0.017
_lock = threading.Lock()
_state_lock = threading.Lock()
_job = {'status': 'idle', 'message': '輸入權證或股票代號開始分析', 'started_at': None, 'finished_at': None}


def now():
    return datetime.now(TZ).isoformat(timespec='seconds')


def init(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS warrant_lookup_days (
      query TEXT NOT NULL, date TEXT NOT NULL, kind TEXT NOT NULL,
      payload TEXT NOT NULL, fetched_at TEXT NOT NULL,
      PRIMARY KEY(query,date)
    );
    CREATE TABLE IF NOT EXISTS warrant_queries (
      query TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
      active INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS warrant_analysis_state (
      id INTEGER PRIMARY KEY CHECK(id=1), status TEXT NOT NULL, started_at TEXT NOT NULL,
      finished_at TEXT, message TEXT NOT NULL
    );
    ''')


def _float(value):
    if value is None:
        return None
    text = str(value).replace(',', '').replace('%', '').strip()
    if text in {'', '-', '--', 'null', 'None'}:
        return None
    try:
        result = float(text)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _normal_cdf(value):
    return 0.5 * (1 + math.erf(value / math.sqrt(2)))


def black_scholes(stock, strike, days, iv, ratio=1.0, kind='call', rate=RISK_FREE):
    """European reference value and Greeks; theta is per calendar day."""
    values = [stock, strike, days, iv, ratio]
    if any(value is None or value <= 0 for value in values):
        return {'theoretical': None, 'delta': None, 'theta': None, 'gamma': None, 'vega': None}
    t = max(days, 1 / 365) / 365
    sigma = iv / 100
    root = math.sqrt(t)
    d1 = (math.log(stock / strike) + (rate + sigma * sigma / 2) * t) / (sigma * root)
    d2 = d1 - sigma * root
    density = math.exp(-d1 * d1 / 2) / math.sqrt(2 * math.pi)
    discount = math.exp(-rate * t)
    if kind == 'put':
        price = (strike * discount * _normal_cdf(-d2) - stock * _normal_cdf(-d1)) * ratio
        delta = (_normal_cdf(d1) - 1) * ratio
        theta_year = (-stock * density * sigma / (2 * root) + rate * strike * discount * _normal_cdf(-d2)) * ratio
    else:
        price = (stock * _normal_cdf(d1) - strike * discount * _normal_cdf(d2)) * ratio
        delta = _normal_cdf(d1) * ratio
        theta_year = (-stock * density * sigma / (2 * root) - rate * strike * discount * _normal_cdf(d2)) * ratio
    gamma = density / (stock * sigma * root) * ratio
    vega = stock * density * root / 100 * ratio
    return {key: round(value, 6) for key, value in {
        'theoretical': price, 'delta': delta, 'theta': theta_year / 365,
        'gamma': gamma, 'vega': vega,
    }.items()}


def _json(raw):
    try:
        value = json.loads(raw.decode('utf-8-sig') if isinstance(raw, bytes) else raw)
        data = value.get('Data') if isinstance(value, dict) else None
        if not isinstance(data, dict): raise ValueError
        return data
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as error:
        raise sources.SourceError('權證資料格式改變，未寫入本機') from error


def _stock_json(raw):
    """A valid null Data value means the stock has no issued warrants."""
    try:
        value = json.loads(raw.decode('utf-8-sig') if isinstance(raw, bytes) else raw)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
        raise sources.SourceError('權證資料格式改變，未寫入本機') from error
    if not isinstance(value, dict) or 'Data' not in value:
        raise sources.SourceError('權證資料格式改變，未寫入本機')
    data = value['Data']
    if data is None:
        return None
    if not isinstance(data, dict):
        raise sources.SourceError('權證資料格式改變，未寫入本機')
    return data


def _market_day(detail=None):
    ticks = (detail or {}).get('HistoryTicks') or []
    values = [str(row.get('Date', ''))[:10] for row in ticks if re.match(r'^20\d{2}-\d{2}-\d{2}', str(row.get('Date', '')))]
    return max(values) if values else datetime.now(TZ).date().isoformat()


def _spread(bid, ask):
    if bid is None or ask is None or bid <= 0 or ask <= 0: return None
    mid = (bid + ask) / 2
    return round((ask - bid) / mid * 100, 2) if mid else None


def _score(days, moneyness, iv, bid, ask, effective, volume=None):
    reasons, warnings, score = [], [], 0
    if days >= 120: score += 20; reasons.append('剩餘天數充足')
    elif days >= 90: score += 16
    elif days >= 60: score += 10
    else: warnings.append('到期不足 60 日，時間價值衰減較快')
    distance = abs(moneyness) if moneyness is not None else 999
    if distance <= 10: score += 20; reasons.append('價內外距離適中')
    elif distance <= 15: score += 14
    elif distance <= 20: score += 8
    else: score += 2; warnings.append('價內外距離偏大')
    spread = _spread(bid, ask)
    if spread is None: warnings.append('缺少完整買賣報價，成交可行性較低')
    elif spread <= 2.5: score += 20; reasons.append('買賣價差小')
    elif spread <= 5: score += 12
    elif spread <= 10: score += 5
    else: warnings.append('買賣價差過大')
    if effective is not None:
        if 3 <= effective <= 8: score += 15; reasons.append('實質槓桿落在較平衡區間')
        elif 8 < effective <= 12: score += 10
        elif effective > 12: score += 4; warnings.append('槓桿偏高，價格波動風險大')
        else: score += 5
    if iv is None: warnings.append('缺少可用隱含波動率')
    elif iv <= 40: score += 15; reasons.append('隱波相對低')
    elif iv <= 60: score += 10
    elif iv <= 90: score += 5
    else: score += 2; warnings.append('隱波偏高，權證溢價與波動風險較高')
    if volume is not None and volume >= 100: score += 10
    elif volume is not None and volume > 0: score += 5
    elif bid and ask: score += 5
    else: warnings.append('成交量或造市報價不足')
    score = min(100, score)
    # A warrant without a two-sided quote cannot be treated as a good buy even
    # when its theoretical terms look attractive. Missing IV also prevents a
    # reliable time-value comparison.
    if spread is None: score = min(score, 49)
    if iv is None: score = min(score, 39)
    label = '優選' if score >= 80 else '良好' if score >= 65 else '觀察' if score >= 50 else '不建議'
    return {'value': score, 'label': label, 'reasons': reasons, 'warnings': warnings, 'spread': spread}


def _market_warnings(info, score, bid_iv, ask_iv, previous_ask_iv, iv_ticks):
    warnings = []
    spread = score.get('spread')
    if spread is None:
        warnings.append({'code': 'quote_missing', 'severity': 'high', 'title': '掛單不完整',
                         'message': '目前缺少完整委買或委賣報價，實際成交與退出風險較高。'})
    elif spread >= 10:
        warnings.append({'code': 'wide_spread', 'severity': 'high', 'title': '買賣價差過大',
                         'message': f'目前價差比 {spread:.2f}%，一買一賣的摩擦成本偏高。'})
    elif spread >= 5:
        warnings.append({'code': 'wide_spread', 'severity': 'medium', 'title': '買賣價差偏大',
                         'message': f'目前價差比 {spread:.2f}%，成交前應再次核對造市報價。'})
    if bid_iv and ask_iv and ask_iv - bid_iv >= 8:
        warnings.append({'code': 'iv_gap', 'severity': 'high', 'title': '委買／委賣隱波落差大',
                         'message': f'委賣隱波比委買隱波高 {ask_iv-bid_iv:.2f} 個百分點。'})
    if previous_ask_iv and ask_iv and ask_iv - previous_ask_iv >= 8:
        warnings.append({'code': 'ask_iv_up', 'severity': 'high', 'title': '委賣隱波快速調升',
                         'message': f'委賣隱波較前值增加 {ask_iv-previous_ask_iv:.2f} 個百分點，買方可能付出較高溢價。'})
    previous_tick = sorted(iv_ticks or [], key=lambda row: str(row.get('Date', '')))[-2:-1]
    previous_bid_iv = _float(previous_tick[0].get('BIV')) if previous_tick else None
    if previous_bid_iv and bid_iv and previous_bid_iv - bid_iv >= 8:
        warnings.append({'code': 'bid_iv_down', 'severity': 'high', 'title': '委買隱波快速調降',
                         'message': f'委買隱波較前一筆降低 {previous_bid_iv-bid_iv:.2f} 個百分點，賣回價格可能受到壓抑。'})
    inventory_raw = _float(info.get('Invertory'))
    inventory = inventory_raw if bool(info.get('HasShow')) or (inventory_raw is not None and inventory_raw > 0) else None
    outstanding = _float(info.get('OutSideQty'))
    if inventory is not None and inventory < 1000:
        warnings.append({'code': 'low_inventory', 'severity': 'medium', 'title': '自營庫存低於 1,000 張',
                         'message': f'來源顯示自營庫存約 {inventory:,.0f} 張，後續造市積極度可能下降。'})
    if outstanding is not None and outstanding < 100:
        warnings.append({'code': 'low_outstanding', 'severity': 'medium', 'title': '流通張數低於 100 張',
                         'message': f'流通在外約 {outstanding:,.0f} 張，屬冷門權證，需留意掛單品質。'})
    return warnings, inventory, outstanding


def parse_warrant_detail(raw, query):
    data = _json(raw)
    info = data.get('WarrantInfo')
    if not isinstance(info, dict) or str(info.get('CommKey', '')).upper() != query:
        raise sources.SourceError('查無此權證代號，或來源回傳代號不符')
    stock_info = info.get('StockInfo') or {}
    stock, strike = _float(stock_info.get('SalePr')), _float(info.get('StrikePr'))
    ratio, days = _float(info.get('UserRate')), int(_float(info.get('LastDays')) or 0)
    kind = 'put' if int(_float(info.get('CallorPut')) or 1) != 1 else 'call'
    bid, ask, last = _float(info.get('BuyPr1')), _float(info.get('SellPr1')), _float(info.get('SalePr'))
    iv = _float(info.get('BIV')) or _float(info.get('IV')) or _float(info.get('SIV'))
    greeks = black_scholes(stock, strike, days, iv, ratio, kind)
    mark = bid if bid and bid > 0 else last
    effective = abs(greeks['delta'] * stock / mark) if greeks['delta'] is not None and mark else None
    moneyness = _float(info.get('PrOutIn'))
    score = _score(days, moneyness, iv, bid, ask, effective, int(_float(info.get('SaleQty')) or 0))
    iv_ticks = data.get('IVTicks') or []
    latest_iv_tick = max(iv_ticks, key=lambda row: str(row.get('Date', '')), default={})
    bid_iv, ask_iv, previous_ask_iv = _float(info.get('BIV')), _float(info.get('SIV')), _float(info.get('PrvSIV'))
    market_warnings, inventory, outstanding = _market_warnings(
        info, score, bid_iv, ask_iv, previous_ask_iv, iv_ticks)
    return {
        'code': query, 'name': str(info.get('CommName') or query), 'kind': kind,
        'underlying': {'code': str(info.get('StockCommkey') or stock_info.get('CommKey') or ''),
                       'name': str(info.get('StockName') or stock_info.get('CommName') or ''),
                       'price': stock, 'change_percent': _float(stock_info.get('CFPersent'))},
        'last': last, 'volume': int(_float(info.get('SaleQty')) or 0), 'bid': bid, 'ask': ask,
        'bid_qty': int(_float(info.get('BuyQty1')) or 0), 'ask_qty': int(_float(info.get('SellQty1')) or 0),
        'strike': strike, 'ratio': ratio, 'expiry': str(info.get('LimitDe', ''))[:10] or None,
        'last_trade_date': str(info.get('LastTransDe', ''))[:10] or None, 'remaining_days': days,
        'moneyness': moneyness, 'iv': iv, 'raw_bid_iv': bid_iv,
        'raw_ask_iv': ask_iv, 'previous_ask_iv': previous_ask_iv, 'style': str(info.get('AorE') or ''),
        'inventory': inventory, 'outstanding': outstanding, 'market_warnings': market_warnings,
        'historical_volatility': {'date': str(latest_iv_tick.get('Date', ''))[:10] or None,
                                  'hv20': _float(latest_iv_tick.get('HV20')),
                                  'hv60': _float(latest_iv_tick.get('HV60')),
                                  'average_iv': _float(latest_iv_tick.get('AverageIV'))},
        'effective_leverage': round(effective, 2) if effective is not None else None,
        'greeks': greeks, 'score': score, 'entry_price': ask if ask and ask > 0 else last,
    }, _market_day(data)


def _quick_rows(raw):
    if raw is None: return {}
    try:
        value = json.loads(raw.decode('utf-8-sig') if isinstance(raw, bytes) else raw)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
        raise sources.SourceError('權證快速篩選資料格式改變') from error
    if isinstance(value, dict) and value.get('ErrorMessage') in {'無符合條件的資料', '查無資料'}:
        return {}
    data = value.get('Data') if isinstance(value, dict) else None
    if not isinstance(data, dict):
        raise sources.SourceError('權證快速篩選資料格式改變')
    values = {}
    for section in ('BasicInfo', 'OtherInfo', 'PrQtyInfo'):
        for row in data.get(section) or []:
            code = str(row.get('CommKey') or '').upper()
            if code: values.setdefault(code, {}).update(row)
    return values


def parse_stock_warrants(raw, query, quick_raw=None):
    data = _stock_json(raw)
    if data is None:
        return {'code': query, 'name': query, 'price': None, 'change_percent': None,
                'rows': [], 'total': 0, 'eligible': 0, 'no_warrants': True,
                'message': '沒有相關權證'}
    stock, grouped = data.get('Stock'), data.get('Warrants')
    if not isinstance(stock, dict) or str(stock.get('CommKey', '')).upper() != query:
        raise sources.SourceError('股票代號與來源回傳不符，未寫入本機')
    if grouped is None:
        grouped = {'Call': [], 'Put': []}
    if not isinstance(grouped, dict):
        raise sources.SourceError('權證資料格式改變，未寫入本機')
    stock_price, today, rows = _float(stock.get('SalePr')), datetime.now(TZ).date(), []
    quick = _quick_rows(quick_raw)
    for key, kind in [('Call', 'call'), ('Put', 'put')]:
        for source in grouped.get(key) or []:
            days = int(_float(source.get('RemainDays')) or 0)
            if days < 60: continue
            bid, ask, last = _float(source.get('BuyPr')), _float(source.get('SellPr')), _float(source.get('SalePr'))
            iv, ratio, strike = _float(source.get('SIV')), _float(source.get('ExecutiveRatio')), _float(source.get('StrikePrice'))
            moneyness = _float(source.get('PrOutIn'))
            greeks = black_scholes(stock_price, strike, days, iv, ratio, kind)
            mark = ask if ask and ask > 0 else bid if bid and bid > 0 else last
            effective = abs(greeks['delta'] * stock_price / mark) if greeks['delta'] is not None and mark else None
            code = str(source.get('CommKey') or '').upper(); quote_row = quick.get(code, {})
            volume_value = _float(quote_row.get('SaleQty'))
            yesterday_iv = _float(source.get('YesterdaySIV'))
            iv_change = round(iv - yesterday_iv, 2) if iv is not None and yesterday_iv is not None else None
            score = _score(days, moneyness, iv, bid, ask, effective,
                           int(volume_value) if volume_value is not None else None)
            spread_leverage = round(score['spread'] / effective, 3) if score['spread'] is not None and effective else None
            exact_expiry = str(quote_row.get('ExpDate') or '')[:10] or None
            row_warnings = []
            if score['spread'] is None: row_warnings.append('掛單不完整')
            elif score['spread'] >= 5: row_warnings.append('價差偏大')
            if iv_change is not None and abs(iv_change) >= 8: row_warnings.append('隱波快速調整')
            rows.append({'code': code, 'name': str(source.get('CommName') or ''),
                'kind': kind, 'bid': bid, 'ask': ask, 'last': last, 'remaining_days': days,
                'expiry': exact_expiry or (today + timedelta(days=days)).isoformat(), 'expiry_exact': bool(exact_expiry),
                'ratio': ratio, 'strike': strike,
                'moneyness': moneyness, 'iv': iv, 'delta': greeks['delta'],
                'effective_leverage': round(effective, 2) if effective is not None else None,
                'spread': score['spread'], 'spread_leverage': spread_leverage,
                'iv_change': iv_change, 'volume': int(volume_value) if volume_value is not None else None,
                'latest_flow': None, 'market_warnings': row_warnings, 'score': score})
    rows.sort(key=lambda row: (-row['score']['value'], row['spread'] if row['spread'] is not None else 999, -row['remaining_days']))
    total = sum(len(grouped.get(key) or []) for key in ('Call', 'Put'))
    return {'code': query, 'name': str(stock.get('CommName') or query), 'price': stock_price,
        'change_percent': _float(stock.get('CFPersent')), 'rows': rows,
        'total': total, 'eligible': len(rows), 'no_warrants': total == 0,
        'message': '沒有相關權證' if total == 0 else None}


def parse_broker_flow(raw, code, period='近 5 個交易日'):
    soup = BeautifulSoup(sources.decode_html(raw), 'html.parser'); text = soup.get_text(' ', strip=True)
    match = re.search(r'最後更新日[：:]\s*(20\d{2})/(\d{2})/(\d{2})', text)
    if not match or f'({code})' not in text: raise sources.SourceError('近五日券商買賣超格式或權證代號改變')
    buys, sells = [], []; total_buy = total_sell = avg_buy = avg_sell = None
    table = soup.select_one('#oMainTable')
    if not table: raise sources.SourceError('近五日券商買賣超缺少表格')
    for tr in table.select('tr'):
        cells = [cell.get_text(' ', strip=True) for cell in tr.select('th,td')]
        if len(cells) == 10 and cells[0] != '買超券商':
            for offset, target, side in [(0, buys, 'buy'), (5, sells, 'sell')]:
                if not cells[offset]: continue
                bought, sold, net = (_float(cells[offset + index]) for index in (1, 2, 3))
                if None not in (bought, sold, net):
                    target.append({'broker': cells[offset], 'buy': bought, 'sell': sold, 'net': net,
                                   'share': _float(cells[offset + 4]), 'side': side})
        elif len(cells) == 4 and cells[0] == '合計買超張數': total_buy, total_sell = _float(cells[1]), _float(cells[3])
        elif len(cells) == 4 and cells[0] == '平均買超成本': avg_buy, avg_sell = _float(cells[1]), _float(cells[3])
    for row in buys: row['estimated_amount'] = round(row['net'] * (avg_buy or 0) * 1000)
    for row in sells: row['estimated_amount'] = round(row['net'] * (avg_sell or 0) * 1000)
    return {'date': '-'.join(match.groups()), 'period': period, 'buys': buys, 'sells': sells,
            'total_buy': total_buy, 'total_sell': total_sell, 'average_buy_cost': avg_buy, 'average_sell_cost': avg_sell}


def parse_weekly_flow(raw, code):
    return parse_broker_flow(raw, code, '近 5 個交易日')


def _fetch(query, refresh=True):
    query = str(query or '').strip().upper()
    if not re.fullmatch(r'[0-9A-Z]{4,10}', query): raise ValueError('請輸入 4 碼股票代號或 5～10 碼權證代號')
    fetched_at = now()
    if len(query) == 4 and query.isdigit():
        url = f'{CMONEY}?action=GetWarrantByStockId&id={quote(query)}'
        quick_url = f'{CMONEY_QUICK}?action=Search&stockId={quote(query)}&warrantsType=-1&issuer=-1'
        quick_raw = None; quick_error = None
        try: quick_raw = sources.get(quick_url, refresh=refresh)
        except Exception as error: quick_error = str(error)[:160]
        stock = parse_stock_warrants(sources.get(url, refresh=refresh), query, quick_raw)
        payload = {'kind': 'stock', 'query': query, 'date': fetched_at[:10], 'stock': stock,
                   'quick_error': quick_error, 'sources': {'terms': url, 'quick': quick_url}}
        name = stock['name']
    else:
        url = f'{CMONEY}?action=GetWarrantByWarrantId&warrantId={quote(query)}'
        detail, market_day = parse_warrant_detail(sources.get(url, refresh=refresh), query)
        flow_url = MONEYDJ.format(code=query); weekly_error = None
        try: weekly = parse_weekly_flow(sources.get(flow_url, refresh=refresh), query)
        except Exception as error:
            weekly, weekly_error = {'date': None, 'period': '近 5 個交易日', 'buys': [], 'sells': []}, str(error)[:160]
        payload = {'kind': 'warrant', 'query': query, 'date': market_day, 'detail': detail,
            'weekly': weekly, 'weekly_error': weekly_error, 'sources': {'terms': url, 'weekly': flow_url},
            'assumptions': {'risk_free_rate': RISK_FREE, 'model': 'Black–Scholes 歐式近似'}}
        name = detail['name']
    with db.connect() as conn:
        init(conn)
        conn.execute('INSERT OR REPLACE INTO warrant_lookup_days VALUES (?,?,?,?,?)',
                     (query, payload['date'], payload['kind'], json.dumps(payload, ensure_ascii=False), fetched_at))
        conn.execute('INSERT OR REPLACE INTO warrant_queries VALUES (?,?,?,?,?)', (query, payload['kind'], name, 1, fetched_at))
    return payload


def _set_job(**values):
    with _state_lock: _job.update(values)


def job_status():
    with _state_lock: return dict(_job)


def lookup(query, force=True):
    payload = _fetch(query, refresh=force)
    _set_job(status='complete', message=f"已更新 {payload['query']} {payload['date']} 資料", finished_at=now())
    return report(payload['query'])


def enrich_stock(query, limit=20):
    """Fetch one-day broker flow only for liquid rows returned by the batch quote source."""
    query = str(query or '').strip().upper()
    if not re.fullmatch(r'\d{4}', query): raise ValueError('最新分點只能套用在股票代號查詢')
    limit = max(1, min(20, int(limit or 20)))
    with db.connect() as conn:
        init(conn)
        row = conn.execute('SELECT date,payload FROM warrant_lookup_days WHERE query=? ORDER BY date DESC LIMIT 1', (query,)).fetchone()
    if not row: raise ValueError('請先查詢股票代號')
    payload = json.loads(row['payload'])
    if payload.get('kind') != 'stock': raise ValueError('查詢結果不是股票權證清單')
    candidates = [item for item in payload['stock']['rows'] if item.get('volume') is not None]
    candidates.sort(key=lambda item: (-item['score']['value'], -(item.get('volume') or 0)))
    failures = []
    for item in candidates[:limit]:
        if item.get('latest_flow'): continue
        try:
            flow = parse_broker_flow(sources.get(MONEYDJ_DAILY.format(code=item['code']), refresh=True),
                                     item['code'], '最新 1 個交易日')
            best_buy = max(flow['buys'], key=lambda value: value['net'], default=None)
            best_sell = max(flow['sells'], key=lambda value: value['net'], default=None)
            dominant = None
            if best_buy or best_sell:
                if best_buy and (not best_sell or best_buy['net'] >= best_sell['net']):
                    dominant = {'side': 'buy', 'broker': best_buy['broker'], 'lots': best_buy['net'], 'date': flow['date']}
                else:
                    dominant = {'side': 'sell', 'broker': best_sell['broker'], 'lots': best_sell['net'], 'date': flow['date']}
            item['latest_flow'] = dominant or {'side': 'flat', 'broker': '', 'lots': 0, 'date': flow['date']}
        except Exception as error:
            failures.append(f"{item['code']}：{str(error)[:80]}")
    fetched_at = now()
    with db.connect() as conn:
        init(conn)
        conn.execute('UPDATE warrant_lookup_days SET payload=?,fetched_at=? WHERE query=? AND date=?',
                     (json.dumps(payload, ensure_ascii=False), fetched_at, query, row['date']))
    response = report(query)
    response['enrichment'] = {'planned': min(limit, len(candidates)),
                              'completed': min(limit, len(candidates)) - len(failures),
                              'failed': len(failures), 'errors': failures[:5]}
    return response


def latest_complete_date():
    with db.connect() as conn:
        init(conn); row = conn.execute('SELECT MAX(date) FROM warrant_lookup_days').fetchone()
    return row[0] if row and row[0] else None


def _worker(query=None):
    started = now(); failures = []
    try:
        with db.connect() as conn:
            init(conn); queries = [query] if query else [row[0] for row in conn.execute('SELECT query FROM warrant_queries WHERE active=1 ORDER BY updated_at DESC')]
        if not queries: status, message = 'complete', '尚無追蹤查詢；輸入權證或股票代號即可建立每日追蹤'
        else:
            for item in queries:
                try: _fetch(item, refresh=True)
                except Exception as error: failures.append(f'{item}：{str(error)[:100]}')
            status = 'partial' if failures and len(failures) < len(queries) else 'failed' if failures else 'complete'
            message = f'已更新 {len(queries)-len(failures)}/{len(queries)} 個權證查詢' + (f'；{len(failures)} 個待重試' if failures else '')
        finished = now()
        with db.connect() as conn:
            init(conn); conn.execute('INSERT OR REPLACE INTO warrant_analysis_state VALUES (1,?,?,?,?)', (status, started, finished, message))
        _set_job(status=status, message=message, finished_at=finished)
    except Exception as error:
        _set_job(status='failed', message=str(error)[:180] or '權證資料更新失敗', finished_at=now())
    finally: _lock.release()


def refresh(query=None):
    if not _lock.acquire(blocking=False): return job_status()
    started = now(); _set_job(status='running', message='正在更新已追蹤的權證查詢', started_at=started, finished_at=None)
    threading.Thread(target=_worker, args=(query,), daemon=True).start()
    return job_status()


def report(query=None, day=None):
    query = str(query or '').strip().upper()
    with db.connect() as conn:
        init(conn); tracked = [dict(row) for row in conn.execute('SELECT query,kind,name,updated_at FROM warrant_queries WHERE active=1 ORDER BY updated_at DESC LIMIT 12')]
        if not query and tracked: query = tracked[0]['query']
        dates = [row[0] for row in conn.execute('SELECT date FROM warrant_lookup_days WHERE query=? ORDER BY date DESC', (query,))] if query else []
        selected = day if day in dates else (dates[0] if dates else None)
        row = conn.execute('SELECT payload,fetched_at FROM warrant_lookup_days WHERE query=? AND date=?', (query, selected)).fetchone() if selected else None
        state = conn.execute('SELECT status,started_at,finished_at,message FROM warrant_analysis_state WHERE id=1').fetchone()
    job = job_status()
    if job['status'] == 'idle' and state: job = dict(state)
    return {'result': json.loads(row['payload']) if row else None, 'query': query, 'tracked': tracked,
        'available_dates': dates, 'fetched_at': row['fetched_at'] if row else None, 'job': job,
        'note': '評分用於比較流動性、時間價值與價格條件，不預測標的漲跌；試算採 Black–Scholes 近似，實際造市報價可能不同。'}
