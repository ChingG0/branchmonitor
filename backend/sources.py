"""Date-validated public MoneyDJ and exchange adapters. Never execute page scripts."""
from __future__ import annotations

import gzip
import html as html_module
import hashlib
import json
import re
import threading
import time
from datetime import date
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from bs4 import BeautifulSoup
from .db import DATA

BASE = 'https://concords.moneydj.com'
_request_lock = threading.Lock()
_last_request = 0.0


class SourceError(ValueError):
    pass


def number(value):
    text = html_module.unescape(re.sub(r'<[^>]+>', '', str(value))).replace(',', '').strip()
    try:
        value = float(text)
        return value if abs(value) < 1e20 else None
    except ValueError:
        return None


def _expects_json(url):
    parsed = urlparse(url)
    return (parse_qs(parsed.query).get('response', [''])[0].lower() == 'json'
            or parsed.path.startswith('/openapi/'))


def _validate_json_response(url, content):
    if not _expects_json(url):
        return
    try:
        json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as error:
        host = urlparse(url).hostname or '來源'
        label = '證交所' if host in {'www.twse.com.tw', 'wwwc.twse.com.tw'} else '來源'
        raise SourceError(f'{label}回傳維護頁或非 JSON，保留既有資料') from error


def _validated_download(url, response, headers):
    content = response.content
    try:
        _validate_json_response(url, content)
        return content, url
    except SourceError:
        if urlparse(url).hostname != 'www.twse.com.tw' or not _expects_json(url):
            raise
        # TWSE's primary CDN sometimes returns an HTTP 200 maintenance page.
        # Its official alternate hostname serves the same date-checked endpoint.
        alternate = url.replace('https://www.twse.com.tw/', 'https://wwwc.twse.com.tw/', 1)
        fallback = requests.get(alternate, timeout=(12, 35), headers=headers)
        fallback.raise_for_status()
        _validate_json_response(alternate, fallback.content)
        return fallback.content, alternate


def get(url, refresh=False):
    """Cache exact raw responses; serialize and throttle polite requests."""
    global _last_request
    folder = DATA / 'raw'
    folder.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()
    path = folder / (key + '.gz')
    if path.exists() and not refresh:
        cached = gzip.decompress(path.read_bytes())
        try:
            _validate_json_response(url, cached)
        except SourceError:
            # A previously cached HTTP 200 maintenance page is never data.
            pass
        else:
            return cached
    with _request_lock:
        time.sleep(max(0, 0.4 - (time.monotonic() - _last_request)))
        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; LocalBranchMonitor/1.0)',
            'Accept': 'application/json,text/html;q=0.9,*/*;q=0.8',
        }
        for attempt in range(3):
            try:
                response = requests.get(url, timeout=(12, 35), headers=headers)
                response.raise_for_status()
                content, fetched_url = _validated_download(url, response, headers)
                _last_request = time.monotonic()
                path.write_bytes(gzip.compress(content))
                (folder / (key + '.json')).write_text(json.dumps({'url': url, 'fetched_url': fetched_url}, ensure_ascii=False))
                return content
            except requests.RequestException as error:
                _last_request = time.monotonic()
                if attempt == 2:
                    # TPEx historical downloads occasionally fail on a fresh
                    # TLS/chunked connection while the same verified host is up.
                    # Warm one certificate-verified keep-alive session and retry
                    # the exact URL. TLS verification is intentionally unchanged.
                    host=urlparse(url).hostname
                    if host in {'www.tpex.org.tw','www.twse.com.tw'} and urlparse(url).scheme=='https':
                        try:
                            session=requests.Session()
                            headers={'User-Agent':'Mozilla/5.0 (compatible; LocalBranchMonitor/1.0)',
                                     'Accept':'application/json,text/html;q=0.9,*/*;q=0.8'}
                            warm_url=('https://www.tpex.org.tw/zh-tw/mainboard/trading/info/mi-pricing.html'
                                      if host=='www.tpex.org.tw' else 'https://www.twse.com.tw/zh/')
                            warm=session.get(warm_url,
                                             timeout=(12,35),headers=headers)
                            warm.raise_for_status()
                            response=session.get(url,timeout=(12,35),headers=headers)
                            response.raise_for_status()
                            content, fetched_url = _validated_download(url, response, headers)
                            _last_request=time.monotonic()
                            path.write_bytes(gzip.compress(content))
                            (folder/(key+'.json')).write_text(json.dumps({'url':url,'fetched_url':fetched_url},ensure_ascii=False))
                            return content
                        except requests.RequestException as fallback_error:
                            raise SourceError(f'來源連線失敗：{type(fallback_error).__name__}') from fallback_error
                    raise SourceError(f'來源連線失敗：{type(error).__name__}') from error
                time.sleep(1.5 * (attempt + 1))


def decode_html(raw):
    # MoneyDJ declares Big5; scripts and Chinese labels must be decoded together.
    return raw.decode('cp950', errors='replace') if isinstance(raw, bytes) else raw


def iso(value):
    parts = re.split(r'[/-]', value)
    return date(*map(int, parts)).isoformat()


def validate_identity(html, broker, branch):
    if broker is None or branch is None:
        return
    identity = re.search(r"GenFundCorpCombo\(\s*'([^']+)'\s*,\s*'([^']+)'",html)
    def canonical_branch(value):
        # MoneyDJ encodes some branch IDs as UTF-16 hex. Hex digit case is
        # immaterial, unlike the decoded character or ordinary branch codes.
        return value.lower() if len(value)>4 and len(value)%4==0 and re.fullmatch(r'[0-9a-fA-F]+',value) else value
    if not identity or identity.group(1)!=broker or canonical_branch(identity.group(2))!=canonical_branch(branch):
        raise SourceError('來源券商或分點代碼不符')


def parse_ranking(raw, expected=None, unit='E', broker=None, branch=None):
    html = decode_html(raw)
    validate_identity(html,broker,branch)
    soup = BeautifulSoup(html, 'html.parser')
    table = soup.select_one('#oMainTable')
    if not table or ('買進張數' if unit == 'E' else '買進金額') not in table.get_text():
        raise SourceError('分點頁面格式或單位改變，未寫入資料')
    dates = re.findall(r'var\s+getYMD[12]\s*=\s*[\'"]([0-9/\-]+)', html)
    if len(dates) < 2:
        # Also accept the page function with an optional line-break/semicolon style.
        dates = re.findall(r'function\s+getYMD[12][\s\S]{0,100}?return\s*[\'"]([0-9/\-]+)', html)
    if len(dates) < 2:
        raise SourceError('無法核對來源日期')
    start, end = iso(dates[0]), iso(dates[1])
    if start != end or (expected and end != expected):
        raise SourceError(f'來源日期 {start}～{end} 與請求單日不符')
    rows = []
    for tr in table.select('tr'):
        cells = tr.find_all('td', recursive=False)
        if len(cells) != 4:
            continue
        cell = str(cells[0])
        match = re.search(r"GenLink2stk\(['\"]AS([^'\"]+)['\"],\s*['\"]([^'\"]+)['\"]", cell, re.I)
        if match:
            symbol, name = match.groups()
        else:
            match = re.search(r"Link2Stk\(['\"]([^'\"]+)['\"]", cell, re.I)
            if not match:
                continue
            symbol = match.group(1)
            name = cells[0].get_text(strip=True).removeprefix(symbol)
        values = [number(x.get_text()) for x in cells[1:]]
        if any(v is None for v in values) or values[0] < 0 or values[1] < 0:
            raise SourceError('分點張數含無效數值')
        rows.append(dict(date=end, symbol=symbol, name=name, buy=values[0], sell=values[1], net=values[2]))
    if not rows and '無此券商分點交易資料' not in soup.get_text():
        raise SourceError('分點頁沒有可辨識的交易列')
    actual = re.search(r'資料日期\s*[：:]\s*(\d{8})',soup.get_text())
    if rows and (not actual or actual.group(1) != end.replace('-','')):
        raise SourceError('榜單實際資料日期與查詢日期不符')
    if len({r['symbol'] for r in rows}) != len(rows):
        raise SourceError('分點榜單出現重複股票')
    return end, rows


def ranking_url(broker, branch, day=None, unit='E'):
    params = {'a': broker, 'b': branch, 'c': unit}
    params.update({'e': day, 'f': day} if day else {'d': '1'})
    return BASE + '/z/zg/zgb/zgb0.djhtm?' + urlencode(params)


def ranking(broker, branch, day=None, refresh=False, unit='E'):
    url = ranking_url(broker, branch, day, unit)
    actual, rows = parse_ranking(get(url, refresh=refresh), day, unit, broker, branch)
    for row in rows:
        row['source'] = url
    return actual, rows


def parse_history(raw, symbol, name, start, end, broker=None, branch=None):
    html = decode_html(raw)
    validate_identity(html,broker,branch)
    soup = BeautifulSoup(html, 'html.parser')
    table = soup.select_one('#oMainTable')
    dates = re.findall(r'var\s+getYMD[12]\s*=\s*[\'"]([0-9/\-]+)', html)
    if len(dates) != 2 or sorted(iso(d) for d in dates) != sorted([start, end]):
        raise SourceError('個股明細查詢區間不符')
    if not table and '查無(' in soup.get_text() and symbol in soup.get_text() and '主力券商' in soup.get_text():
        return []
    if not table or '買進(張)' not in table.get_text() or f'({symbol})' not in soup.get_text():
        raise SourceError('個股分點明細格式或股票代碼不符')
    rows = []
    for tr in table.select('tr'):
        cells = tr.find_all('td', recursive=False)
        if len(cells) != 5 or not re.fullmatch(r'\d{4}/\d{2}/\d{2}', cells[0].get_text(strip=True)):
            continue
        day = iso(cells[0].get_text(strip=True))
        if not start <= day <= end:
            raise SourceError('個股明細回傳區間外日期')
        values = [number(x.get_text()) for x in cells[1:]]
        if any(v is None for v in values) or values[0] < 0 or values[1] < 0:
            raise SourceError('個股明細含無效張數')
        rows.append(dict(date=day, symbol=symbol, name=name, buy=values[0], sell=values[1], net=values[3]))
    if not rows:
        raise SourceError('個股明細沒有交易列，未推定為零交易')
    if len({r['date'] for r in rows}) != len(rows):
        raise SourceError('個股明細含重複日期')
    return rows


def history(broker, branch, symbol, name, start, end, refresh=True):
    url = BASE + '/z/zc/zco/zco0/zco0.djhtm?' + urlencode({
        'A': symbol, 'BHID': broker, 'b': branch, 'C': '1', 'D': start, 'E': end, 'ver': 'V3'})
    rows = parse_history(get(url, refresh=refresh), symbol, name, start, end, broker, branch)
    for row in rows:
        row['source'] = url
    return rows


def parse_market(data, day, market):
    expected = day.replace('-', '')
    if str(data.get('stat', '')).lower() != 'ok':
        if '沒有符合條件' in str(data.get('stat', '')):
            return []
        raise SourceError(f'{market} 回應異常：{str(data.get("stat", ""))[:70]}')
    if data.get('date') != expected:
        raise SourceError(f'{market} 行情日期不符')
    prices = []
    found = False
    for table in data.get('tables', []):
        fields = table.get('fields', [])
        if ('證券代號' in fields and '收盤價' in fields) or ('代號' in fields and '收盤' in fields):
            found = True
            for values in table.get('data', []):
                row = dict(zip(fields, values))
                symbol = row.get('證券代號', row.get('代號', ''))
                # Listed equities and funds. Do not treat warrants as ordinary equities.
                if not (re.fullmatch(r'\d{4}', symbol) or re.fullmatch(r'00[0-9A-Z]{2,5}', symbol) or re.fullmatch(r'02\d{3}[0-9BLR]', symbol)):
                    continue
                close = number(row.get('收盤價', row.get('收盤')))
                if not close or close <= 0:
                    continue
                prices.append(dict(date=day, symbol=symbol, name=row.get('證券名稱',row.get('名稱','')),
                    close=close, open=number(row.get('開盤價',row.get('開盤'))),
                    high=number(row.get('最高價',row.get('最高'))), low=number(row.get('最低價',row.get('最低'))),
                    volume=number(row.get('成交股數')), market=market))
        if market == 'TWSE' and '指數' in fields and '收盤指數' in fields:
            for values in table.get('data', []):
                row = dict(zip(fields, values))
                if row.get('指數') == '發行量加權股價指數':
                    close = number(row['收盤指數'])
                    if close:
                        prices.append(dict(date=day,symbol='TAIEX',name='加權指數',close=close,
                                           open=None,high=None,low=None,volume=None,market=market))
    if not found:
        raise SourceError(f'{market} 沒有可辨識的行情表')
    return prices


def parse_tpex_openapi_market(data, day):
    """Parse the official current TPEx OpenAPI snapshot with strict date checks."""
    if not isinstance(data, list) or not data:
        raise SourceError('TPEX OpenAPI 沒有行情資料')
    expected = str(int(day[:4]) - 1911) + day[5:7] + day[8:10]
    returned = {str(row.get('Date', '')).strip() for row in data}
    if returned != {expected}:
        raise SourceError('TPEX OpenAPI 行情日期不符')
    prices = []
    for row in data:
        symbol = str(row.get('SecuritiesCompanyCode', '')).strip()
        if not (re.fullmatch(r'\d{4}', symbol) or re.fullmatch(r'00[0-9A-Z]{2,5}', symbol) or re.fullmatch(r'02\d{3}[0-9BLR]', symbol)):
            continue
        close = number(row.get('Close'))
        if not close or close <= 0:
            continue
        prices.append(dict(
            date=day, symbol=symbol, name=str(row.get('CompanyName', '')).strip(),
            close=close, open=number(row.get('Open')), high=number(row.get('High')),
            low=number(row.get('Low')), volume=number(row.get('TradingShares')), market='TPEX'))
    if len(prices) < 300:
        raise SourceError('TPEX OpenAPI 可辨識行情不足')
    return prices


def market_prices(day, market, refresh=False):
    if market == 'TWSE':
        url = 'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?' + urlencode({
            'date': day.replace('-',''), 'type':'ALLBUT0999', 'response':'json'})
    else:
        url = 'https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?' + urlencode({
            'response':'json','date':day.replace('-','/')})
    try:
        data = json.loads(get(url, refresh=refresh))
        return parse_market(data, day, market)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise SourceError(f'{market} 回傳內容不是 JSON') from error
    except SourceError as primary_error:
        if market != 'TPEX':
            raise
        # The TPEx page API can return a transient Cloudflare 520 while the
        # exchange's official current-day OpenAPI remains available.  The
        # fallback is always fetched fresh, and its ROC date must match the
        # requested day exactly so a current snapshot can never fill history.
        fallback = 'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes'
        try:
            try:
                raw = get(fallback, refresh=True)
            except SourceError:
                # A same-day response already verified by its embedded ROC date
                # remains safe to reuse during a transient reconnect failure.
                raw = get(fallback, refresh=False)
            data = json.loads(raw)
            return parse_tpex_openapi_market(data, day)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise SourceError('TPEX 官方備援回傳內容不是 JSON') from error
        except SourceError as fallback_error:
            raise SourceError(f'TPEX 主來源失敗；官方 OpenAPI 備援未能提供 {day} 行情：{fallback_error}') from primary_error


def branch_catalog():
    html = decode_html(get(BASE + '/z/js/zbrokerjs.djjs'))
    match = re.search(r"g_BrokerList\s*=\s*'([^']+)'", html)
    if not match:
        raise SourceError('無法讀取分點清單')
    result = []
    for group in match.group(1).split(';'):
        parts = group.split('!')
        broker = parts[0].split(',')[0]
        for branch in parts[1:]:
            code, name = branch.split(',', 1)
            result.append({'broker':broker,'branch':code,'name':name})
    return result
