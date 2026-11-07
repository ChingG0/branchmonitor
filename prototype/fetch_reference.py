"""抓取台股產業鏈、股本與營收，存入原型資料庫。

來源都是官方公開資料，只讀不寫；原始回應快取在 prototype/raw/，可回溯。
與正式系統的 data/raw/ 分開，不共用快取也不影響分點回補。
"""
from __future__ import annotations

import gzip
import hashlib
from html import unescape as html_unescape
import json
import re
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from sectors import store, initialize, ROOT, PRICES as PRICES_DB

RAW = Path(__file__).resolve().parent / 'raw'
IC = 'https://ic.tpex.org.tw/'
PROFILE = {'TWSE': 'https://openapi.twse.com.tw/v1/opendata/t187ap03_L',
           'TPEX': 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O'}
REVENUE = {'TWSE': 'https://openapi.twse.com.tw/v1/opendata/t187ap05_L',
           'TPEX': 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O'}
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; LocalBranchMonitor/1.0)',
           'Accept': 'application/json,text/html;q=0.9,*/*;q=0.8'}
_session = requests.Session()
_last = 0.0


class SourceError(ValueError):
    pass


def get(url, refresh=False, warm=False):
    """節流取得並快取原始回應。TPEx 偶爾回傳截斷內容，交由呼叫端驗證後重試。"""
    global _last
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / (hashlib.sha256(url.encode()).hexdigest() + '.gz')
    if path.exists() and not refresh:
        return gzip.decompress(path.read_bytes())
    if warm:
        try:
            _session.get('https://www.tpex.org.tw/zh-tw/mainboard/trading/info/mi-pricing.html',
                         timeout=(12, 35), headers=HEADERS)
        except requests.RequestException:
            pass
    time.sleep(max(0, 0.4 - (time.monotonic() - _last)))
    try:
        response = _session.get(url, timeout=(12, 60), headers=HEADERS)
        response.raise_for_status()
    except requests.RequestException as error:
        _last = time.monotonic()
        raise SourceError(f'來源連線失敗：{type(error).__name__}') from error
    _last = time.monotonic()
    path.write_bytes(gzip.compress(response.content))
    (RAW / (path.stem + '.json')).write_text(json.dumps({'url': url}, ensure_ascii=False))
    return response.content


def json_source(url, expect, refresh=False):
    """TPEx 大型回應會被截斷，解析失敗就重新抓，不接受半截資料。"""
    for attempt in range(3):
        raw = get(url, refresh=refresh or attempt > 0, warm='tpex' in url)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, list) or len(data) < expect:
            continue
        return data
    raise SourceError(f'{url} 連續 3 次回傳不完整資料')


def number(value):
    text = re.sub(r'[,\s]', '', str(value or ''))
    try:
        return float(text)
    except ValueError:
        return None


# ---------- 產業鏈 ----------

def chain_codes(refresh=False):
    # 產業鏈下拉選單在各鏈頁面上，index.php 沒有完整清單。
    html = get(IC + 'introduce.php?ic=1000', refresh=refresh).decode('utf-8', 'replace')
    codes = re.findall(r"<option value='([A-Z0-9]{4})'[^>]*>([^<]+)</option>", html)
    if len(codes) < 20:
        raise SourceError('產業鏈清單格式改變')
    return codes


def parse_chain(html, code, label):
    panel = re.search(r'<div id="main_ic_panel".*?(?=<div id="companyList_)', html, re.S)
    if not panel:
        raise SourceError(f'{label} 找不到產業鏈圖')
    # 多數產業鏈分上／中／下游，但金融、軟體服務等是單層結構，分層缺席不算錯誤。
    tiers = [(m.start(), m.group(1)) for m in
             re.finditer(r'<div class="chain-title-panel">(上游|中游|下游)</div>', panel.group(0))]
    segments, seq = [], 0
    body = panel.group(0)
    for match in re.finditer(r'id="ic_link_([A-Z0-9]+)"[^>]*>', body):
        # 環節名稱可能包在巢狀 div 裡，掃描配對的 </div> 才知道範圍，
        # 直接切到下一個環節會切進標籤中間，把分層標題和半截標記一起帶進名稱。
        depth, cursor = 1, match.end()
        for tag in re.finditer(r'<(/?)div\b[^>]*>', body[match.end():]):
            depth += -1 if tag.group(1) else 1
            if depth == 0:
                cursor = match.end() + tag.start()
                break
        else:
            cursor = len(body)
        text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', body[match.end():cursor])).strip()
        if not text:
            continue
        previous = [name for start, name in tiers if start < match.start()]
        seq += 1
        segments.append({'code': match.group(1), 'name': html_unescape(text),
                         'tier': previous[-1] if previous else '未分層', 'seq': seq})
    if not segments:
        raise SourceError(f'{label} 沒有可辨識的環節')
    # 每個環節有本國與外國兩個 x-hidden 區塊，只取本國上市櫃公司連結後合併。
    members = {}
    blocks = list(re.finditer(r'<div id="companyList_([A-Z0-9]+)" title="([^"]*)"', html))
    for index, block in enumerate(blocks):
        end = blocks[index + 1].start() if index + 1 < len(blocks) else len(html)
        found = re.findall(r'company_basic\.php\?stk_code=(\d{4})[^>]*title="([^"]*)"',
                           html[block.end():end])
        if found:
            members.setdefault(block.group(1), {}).update(found)
    unknown = set(members) - {s['code'] for s in segments}
    if unknown:
        raise SourceError(f'{label} 公司清單對應到未知環節 {sorted(unknown)[:3]}')
    return segments, members


def sync_chains(refresh=False):
    initialize()
    with store() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS chains (code TEXT PRIMARY KEY, name TEXT NOT NULL, fetched_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS chain_segments (
          chain TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
          tier TEXT NOT NULL, seq INTEGER NOT NULL, PRIMARY KEY(chain,code));
        CREATE TABLE IF NOT EXISTS chain_members (
          chain TEXT NOT NULL, segment TEXT NOT NULL, symbol TEXT NOT NULL,
          name TEXT NOT NULL, PRIMARY KEY(chain,segment,symbol));
        CREATE INDEX IF NOT EXISTS idx_chain_members_symbol ON chain_members(symbol);
        ''')
    codes = chain_codes(refresh)
    stamp = datetime.now().isoformat(timespec='seconds')
    done, errors = 0, []
    for code, label in codes:
        try:
            html = get(IC + f'introduce.php?ic={code}', refresh=refresh).decode('utf-8', 'replace')
            segments, members = parse_chain(html, code, label)
        except (SourceError, requests.RequestException) as error:
            errors.append(f'{label}({code})：{str(error)[:90]}')
            continue
        # 單一產業鏈成功才替換自己的資料，失敗的鏈保留既有內容。
        with store() as conn:
            conn.execute('INSERT OR REPLACE INTO chains VALUES (?,?,?)', (code, label.strip(), stamp))
            conn.execute('DELETE FROM chain_segments WHERE chain=?', (code,))
            conn.execute('DELETE FROM chain_members WHERE chain=?', (code,))
            conn.executemany('INSERT INTO chain_segments VALUES (?,?,?,?,?)',
                             [(code, s['code'], s['name'], s['tier'], s['seq']) for s in segments])
            conn.executemany('INSERT INTO chain_members VALUES (?,?,?,?)',
                             [(code, segment, symbol, name)
                              for segment, found in members.items() for symbol, name in found.items()])
        done += 1
    with store() as conn:
        counts = [conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
                  for t in ['chains', 'chain_segments', 'chain_members']]
        conn.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('chains_synced_at', stamp))
    return {'chains': counts[0], 'segments': counts[1], 'members': counts[2],
            'requested': len(codes), 'ok': done, 'errors': errors}


# ---------- 股本與營收 ----------

def sync_profiles(refresh=False):
    initialize()
    with store() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS company_profile (
          symbol TEXT PRIMARY KEY, name TEXT NOT NULL, market TEXT NOT NULL,
          industry TEXT NOT NULL DEFAULT '', capital REAL, shares REAL, par REAL,
          listed_on TEXT, fetched_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS company_revenue (
          symbol TEXT PRIMARY KEY, year_month TEXT NOT NULL, monthly REAL,
          cumulative REAL, last_year_cumulative REAL, fetched_at TEXT NOT NULL);
        ''')
    stamp = datetime.now().isoformat(timespec='seconds')
    rows, revenue, errors = [], [], []
    for market, url in PROFILE.items():
        try:
            data = json_source(url, 500, refresh)
        except SourceError as error:
            errors.append(f'{market} 基本資料：{error}')
            continue
        for item in data:
            if market == 'TWSE':
                symbol, name = item.get('公司代號', ''), item.get('公司簡稱', '')
                capital, shares = number(item.get('實收資本額')), number(item.get('已發行普通股數或TDR原股發行股數'))
                par, industry, listed = number(item.get('普通股每股面額')), item.get('產業別', ''), item.get('上市日期', '')
            else:
                symbol, name = item.get('SecuritiesCompanyCode', ''), item.get('CompanyAbbreviation', '')
                capital, shares = number(item.get('Paidin.Capital.NTDollars')), number(item.get('IssueShares'))
                par = number(re.sub(r'[^\d.]', '', str(item.get('ParValueOfCommonStock', ''))))
                industry, listed = item.get('SecuritiesIndustryCode', ''), item.get('DateOfListing', '')
            if not re.fullmatch(r'\d{4}', symbol) or not name:
                continue
            if capital is not None and capital <= 0:
                capital = None
            rows.append((symbol, name.strip(), market, str(industry), capital, shares, par, str(listed), stamp))
    for market, url in REVENUE.items():
        try:
            data = json_source(url, 500, refresh)
        except SourceError as error:
            errors.append(f'{market} 月營收：{error}')
            continue
        for item in data:
            symbol, period = item.get('公司代號', ''), str(item.get('資料年月', ''))
            if not re.fullmatch(r'\d{4}', symbol) or not re.fullmatch(r'\d{5,6}', period):
                continue
            revenue.append((symbol, period, number(item.get('營業收入-當月營收')),
                            number(item.get('累計營業收入-當月累計營收')),
                            number(item.get('累計營業收入-去年累計營收')), stamp))
    if not rows:
        raise SourceError('沒有取得任何公司基本資料，保留既有內容')
    with store() as conn:
        conn.executemany('INSERT OR REPLACE INTO company_profile VALUES (?,?,?,?,?,?,?,?,?)', rows)
        if revenue:
            conn.executemany('INSERT OR REPLACE INTO company_revenue VALUES (?,?,?,?,?,?)', revenue)
        conn.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('profiles_synced_at', stamp))
        periods = [r[0] for r in conn.execute(
            'SELECT year_month,COUNT(*) FROM company_revenue GROUP BY year_month ORDER BY year_month DESC LIMIT 2')]
    return {'profiles': len(rows), 'revenue': len(revenue), 'periods': periods, 'errors': errors}


# ---------- 三大法人買賣超 ----------

T86 = 'https://www.twse.com.tw/rwd/zh/fund/T86?selectType=ALLBUT0999&response=json&date={}'
TPEX_INSTI = 'https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW&response=json&date={}'
TWSE_FIELDS = ['外陸資買賣超股數(不含外資自營商)', '投信買賣超股數',
               '自營商買賣超股數', '三大法人買賣超股數']


def parse_twse_institutional(raw, day):
    data = json.loads(raw)
    if str(data.get('stat', '')).lower() != 'ok' or data.get('date') != day.replace('-', ''):
        raise SourceError(f'{day} 上市法人日期或狀態不符')
    fields = data.get('fields', [])
    if any(f not in fields for f in TWSE_FIELDS) or '證券代號' not in fields:
        raise SourceError('上市法人欄位改變')
    index = {f: fields.index(f) for f in TWSE_FIELDS}
    rows = []
    for values in data.get('data', []):
        symbol = str(values[fields.index('證券代號')]).strip()
        if not re.fullmatch(r'[1-9]\d{3}', symbol):
            continue
        picked = [number(values[index[f]]) for f in TWSE_FIELDS]
        if any(v is None for v in picked):
            raise SourceError(f'{day} 上市法人 {symbol} 股數缺漏')
        rows.append((day, symbol, 'TWSE', *picked))
    if len(rows) < 500:
        raise SourceError(f'{day} 上市法人只有 {len(rows)} 檔，資料不完整')
    return rows


def parse_tpex_institutional(raw, day):
    data = json.loads(raw)
    if str(data.get('stat', '')).lower() != 'ok' or data.get('date') != day.replace('-', ''):
        raise SourceError(f'{day} 上櫃法人日期或狀態不符')
    table = (data.get('tables') or [{}])[0]
    fields = table.get('fields', [])
    # 上櫃欄位沒有分組標題，是 7 組（買進／賣出／買賣超）加總計的位置式排列。
    # 靠組間恆等式驗證對位：外資合計＝外資不含自營＋外資自營、自營合計＝自行＋避險、
    # 三大法人＝外資合計＋投信＋自營合計。對不上就不寫入。
    if len(fields) != 24 or fields[0] != '代號':
        raise SourceError(f'上櫃法人欄位數改變（{len(fields)}）')
    rows = []
    for values in table.get('data', []):
        symbol = str(values[0]).strip()
        if not re.fullmatch(r'[1-9]\d{3}', symbol):
            continue
        nums = [number(v) for v in values[2:24]]
        if any(v is None for v in nums):
            raise SourceError(f'{day} 上櫃法人 {symbol} 股數缺漏')
        net = [nums[i * 3 + 2] for i in range(7)]
        foreign, dealer_self, hedge, total = net[0], net[4], net[5], nums[21]
        if abs(net[2] - (net[0] + net[1])) > 1 or abs(net[6] - (dealer_self + hedge)) > 1:
            raise SourceError(f'{day} 上櫃法人 {symbol} 分組合計不符，欄位可能改變')
        if abs(total - (net[2] + net[3] + net[6])) > 1:
            raise SourceError(f'{day} 上櫃法人 {symbol} 三大法人合計不符')
        rows.append((day, symbol, 'TPEX', foreign, net[3], net[6], total))
    if len(rows) < 300:
        raise SourceError(f'{day} 上櫃法人只有 {len(rows)} 檔，資料不完整')
    return rows


def sync_institutional(days=20, refresh=False):
    """抓最近 N 個本機已驗證交易日的三大法人買賣超股數。"""
    initialize()
    with store() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS institutional (
          date TEXT NOT NULL, symbol TEXT NOT NULL, market TEXT NOT NULL,
          foreign_net REAL NOT NULL, trust_net REAL NOT NULL,
          dealer_net REAL NOT NULL, total_net REAL NOT NULL, PRIMARY KEY(date,symbol));
        CREATE TABLE IF NOT EXISTS institutional_days (
          date TEXT PRIMARY KEY, twse INTEGER NOT NULL, tpex INTEGER NOT NULL,
          fetched_at TEXT NOT NULL);
        ''')
        done = {r['date'] for r in conn.execute('SELECT date FROM institutional_days')}
    from sectors import quotes, calendar
    with quotes() as conn:
        sessions = calendar(conn, '9999')[-days:]
    stamp = datetime.now().isoformat(timespec='seconds')
    added, errors = [], []
    for day in sessions:
        if day in done and not refresh:
            continue
        try:
            listed = parse_twse_institutional(get(T86.format(day.replace('-', '')), refresh=True), day)
            otc = parse_tpex_institutional(
                get(TPEX_INSTI.format(day.replace('-', '/')), refresh=True, warm=True), day)
        except (SourceError, json.JSONDecodeError, requests.RequestException) as error:
            errors.append(f'{day}：{str(error)[:110]}')
            continue
        # 兩市都成功才寫入，避免只有半個市場的資金流被當成全市場。
        with store() as conn:
            conn.executemany('INSERT OR REPLACE INTO institutional VALUES (?,?,?,?,?,?,?)', listed + otc)
            conn.execute('INSERT OR REPLACE INTO institutional_days VALUES (?,?,?,?)',
                         (day, len(listed), len(otc), stamp))
        added.append(day)
    with store() as conn:
        total = conn.execute('SELECT COUNT(*) FROM institutional').fetchone()[0]
        covered = [r['date'] for r in conn.execute('SELECT date FROM institutional_days ORDER BY date')]
    return {'added': added, 'errors': errors, 'rows': total, 'dates': covered}


# ---------- 國際大廠報價 ----------

YAHOO = 'https://query1.finance.yahoo.com/v8/finance/chart/{}?interval=1d&range=5d'
# 供應鏈字典用 MoneyDJ 代碼，Yahoo 用自己的後綴；只對應得出來的才查，其餘略過不猜。
SUFFIX = {'US': '', 'KS': '.KS', 'JP': '.T'}


# MoneyDJ 的德股代碼（S@GR 等）沒有規則可推，逐檔對應。
MANUAL = {'S@GR': 'BAS.DE', 'X@GR': 'IFX.DE'}


def yahoo_symbol(code):
    if code in MANUAL:
        return MANUAL[code]
    if '.' in code:
        base, market = code.rsplit('.', 1)
        if market.upper() in SUFFIX:
            return base.upper() + SUFFIX[market.upper()]
        return None
    if re.fullmatch(r'\d{4}', code):      # 港股
        return code.lstrip('0').zfill(4) + '.HK'
    if re.fullmatch(r'\d{6}', code):      # 陸股：深交所 000/001/002/300，上交所 6 開頭
        return code + ('.SZ' if code[0] in '03' else '.SS' if code[0] == '6' else '')
    return None


def parse_quote(raw, symbol):
    payload = json.loads(raw)
    result = (payload.get('chart', {}).get('result') or [None])[0]
    if not result:
        raise SourceError(f'{symbol} 無報價結果')
    meta = result.get('meta', {})
    if str(meta.get('symbol', '')).upper() != symbol.upper():
        raise SourceError(f'{symbol} 回傳商品不符（{meta.get("symbol")}）')
    price = meta.get('regularMarketPrice')
    previous = meta.get('chartPreviousClose') or meta.get('previousClose')
    stamp = meta.get('regularMarketTime')
    if not price or price <= 0 or not previous or previous <= 0 or not stamp:
        raise SourceError(f'{symbol} 報價缺漏')
    when = datetime.fromtimestamp(stamp, tz=None)
    if when.timestamp() > datetime.now().timestamp() + 300:
        raise SourceError(f'{symbol} 報價時間晚於現在')
    return {'price': price, 'previous': previous, 'change': price - previous,
            'change_pct': (price / previous - 1) * 100,
            'currency': meta.get('currency', ''), 'exchange': meta.get('fullExchangeName', ''),
            'as_of': when.isoformat(timespec='seconds')}


def sync_partner_quotes(refresh=True):
    initialize()
    with store() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS partner_quotes (
          partner TEXT PRIMARY KEY, code TEXT NOT NULL, yahoo TEXT NOT NULL,
          price REAL, previous REAL, change REAL, change_pct REAL,
          currency TEXT, exchange TEXT, as_of TEXT, fetched_at TEXT NOT NULL)''')
        partners = [(r['partner'], r['partner_code']) for r in conn.execute(
            'SELECT DISTINCT partner,partner_code FROM chain_links ORDER BY partner')]
    stamp = datetime.now().isoformat(timespec='seconds')
    saved, skipped, errors = 0, [], []
    for partner, code in partners:
        symbol = yahoo_symbol(code)
        if not symbol:
            skipped.append(f'{partner}({code})')
            continue
        try:
            quote = parse_quote(get(YAHOO.format(symbol), refresh=refresh), symbol)
        except (SourceError, json.JSONDecodeError, requests.RequestException) as error:
            errors.append(f'{partner}({symbol})：{str(error)[:80]}')
            continue
        with store() as conn:
            conn.execute('INSERT OR REPLACE INTO partner_quotes VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                         (partner, code, symbol, quote['price'], quote['previous'], quote['change'],
                          quote['change_pct'], quote['currency'], quote['exchange'],
                          quote['as_of'], stamp))
        saved += 1
    return {'saved': saved, 'skipped': skipped, 'errors': errors, 'total': len(partners)}


# ---------- 自有行情（正式系統尚未同步的交易日） ----------

TWSE_QUOTE = 'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?type=ALLBUT0999&response=json&date={}'
TPEX_QUOTE = 'https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?response=json&date={}'


def _market_parser():
    """借用正式系統的行情解析器，確保與正式資料同一套驗證，但不寫入它的快取。"""
    import sys
    sys.path.insert(0, str(ROOT))
    from backend import sources as backend_sources
    return backend_sources


def sync_prices(days=10, refresh=False):
    """補正式系統缺少的交易日收盤價，存進原型資料庫。

    只在正式系統沒有該日 market_days 時才抓；正式系統補上之後，合併檢視一律以它為準，
    原型這份自動退居備援，不會覆蓋或修改正式資料。
    """
    backend_sources = _market_parser()
    initialize()
    with store() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS own_prices (
          date TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT NOT NULL,
          close REAL NOT NULL CHECK(close > 0), open REAL, high REAL, low REAL,
          volume REAL, market TEXT NOT NULL, PRIMARY KEY(date,symbol));
        CREATE INDEX IF NOT EXISTS idx_own_prices_symbol ON own_prices(symbol,date);
        CREATE TABLE IF NOT EXISTS own_market_days (
          date TEXT PRIMARY KEY, twse_status TEXT NOT NULL, tpex_status TEXT NOT NULL,
          message TEXT NOT NULL DEFAULT '', fetched_at TEXT NOT NULL);
        ''')
        mine = {r['date'] for r in conn.execute("SELECT date FROM own_market_days WHERE twse_status='ok'")}
    with sqlite3.connect(f'file:{PRICES_DB}?mode=ro', uri=True) as official:
        official.row_factory = sqlite3.Row
        known = {r['date'] for r in official.execute('SELECT date FROM market_days')}
        newest = official.execute("SELECT MAX(date) FROM market_days WHERE twse_status='ok'").fetchone()[0]

    today = date.today()
    start = date.fromisoformat(newest) + timedelta(days=1) if newest else today - timedelta(days=days)
    wanted = [(start + timedelta(days=i)).isoformat() for i in range((today - start).days + 1)
              if (start + timedelta(days=i)).weekday() < 5][-days:]
    stamp = datetime.now().isoformat(timespec='seconds')
    added, skipped, errors = [], [], []
    for day in wanted:
        if day in known:                      # 正式系統已有，交給它負責
            skipped.append(day)
            continue
        if day in mine and not refresh:
            continue
        rows, status = [], {}
        for market, url in (('TWSE', TWSE_QUOTE.format(day.replace('-', ''))),
                            ('TPEX', TPEX_QUOTE.format(day.replace('-', '/')))):
            try:
                raw = get(url, refresh=True, warm=market == 'TPEX')
                parsed = backend_sources.parse_market(json.loads(raw), day, market)
                status[market] = 'ok' if parsed else 'no_data'
                rows.extend(parsed)
            except Exception as error:
                status[market] = 'error'
                errors.append(f'{day} {market}：{str(error)[:90]}')
        # 上市沒有資料就不是可用的交易日（休市或尚未發布），不寫入半套資料。
        if status.get('TWSE') != 'ok':
            continue
        with store() as conn:
            conn.executemany('INSERT OR REPLACE INTO own_prices VALUES (?,?,?,?,?,?,?,?,?)',
                             [(r['date'], r['symbol'], r['name'], r['close'], r['open'],
                               r['high'], r['low'], r['volume'], r['market']) for r in rows])
            conn.execute('INSERT OR REPLACE INTO own_market_days VALUES (?,?,?,?,?)',
                         (day, status.get('TWSE', 'error'), status.get('TPEX', 'error'), '', stamp))
        added.append(day)
    with store() as conn:
        covered = [r['date'] for r in conn.execute(
            "SELECT date FROM own_market_days WHERE twse_status='ok' ORDER BY date")]
    return {'added': added, 'skipped': skipped, 'errors': errors, 'own_dates': covered}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='抓取產業鏈、股本與營收')
    parser.add_argument('target', choices=['chains', 'profiles', 'institutional', 'quotes', 'prices', 'all'],
                        default='all', nargs='?')
    parser.add_argument('--days', type=int, default=20, help='法人買賣超回補的交易日數')
    parser.add_argument('--refresh', action='store_true', help='忽略快取重新抓取')
    args = parser.parse_args()
    if args.target in ('chains', 'all'):
        result = sync_chains(args.refresh)
        print(f"產業鏈 {result['ok']}/{result['requested']} 成功 · 環節 {result['segments']} · 成員 {result['members']}")
        for line in result['errors']:
            print('  ⚠', line)
    if args.target in ('profiles', 'all'):
        result = sync_profiles(args.refresh)
        print(f"公司基本資料 {result['profiles']} 家 · 營收 {result['revenue']} 筆 · 期別 {result['periods']}")
        for line in result['errors']:
            print('  ⚠', line)
    if args.target in ('prices', 'all'):
        result = sync_prices()
        print(f"自有行情 新增 {result['added'] or '無'} · 正式系統已有 {len(result['skipped'])} 日")
        for line in result['errors']:
            print('  ⚠', line)
    if args.target in ('quotes', 'all'):
        result = sync_partner_quotes()
        print(f"國際大廠報價 {result['saved']}/{result['total']} 家")
        if result['skipped']:
            print(f"  代碼無法對應 Yahoo：{'、'.join(result['skipped'])}")
        for line in result['errors']:
            print('  ⚠', line)
    if args.target in ('institutional', 'all'):
        result = sync_institutional(args.days, args.refresh)
        print(f"三大法人 新增 {len(result['added'])} 日 · 累計 {result['rows']} 筆 · "
              f"涵蓋 {result['dates'][0] if result['dates'] else '—'}～{result['dates'][-1] if result['dates'] else '—'}")
        for line in result['errors']:
            print('  ⚠', line)
