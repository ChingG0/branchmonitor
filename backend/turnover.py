"""On-demand, date-pinned five-session turnover estimates for the published main-player pool."""
from __future__ import annotations

import json
import re
import threading
from datetime import date, timedelta
from urllib.parse import urlencode, urlparse, parse_qs
from bs4 import BeautifulSoup
from . import db, sources, major

_lock = threading.Lock()


def parse_pool(raw, symbol, start, end):
    html = sources.decode_html(raw)
    soup = BeautifulSoup(html, 'html.parser')
    table = soup.select_one('#oMainTable')
    dates = re.findall(r"var\s+getYMD[12]\s*=\s*['\"]([0-9/\-]+)", html)
    if len(dates) != 2 or sorted(sources.iso(d) for d in dates) != sorted([start, end]):
        raise sources.SourceError('五日主力來源區間不符')
    if not table or f'({symbol})' not in table.get_text() or '單位：張' not in table.get_text():
        raise sources.SourceError('五日主力股票或單位不符')
    if '平均買超成本' not in table.get_text() or '平均賣超成本' not in table.get_text():
        raise sources.SourceError('五日主力來源不完整')
    headers=[c.get_text(strip=True) for tr in table.select('tr') for c in tr.find_all('td',recursive=False) if c.get('class')==['t2']]
    expected=['買超券商','買進','賣出','買超','佔成交比重','賣超券商','買進','賣出','賣超','佔成交比重']
    if not any(headers[i:i+10]==expected for i in range(len(headers))):
        raise sources.SourceError('五日主力欄位格式改變')
    rows = []; counts = [0, 0]
    for tr in table.select('tr'):
        cells = tr.find_all('td', recursive=False)
        if len(cells) != 10:
            continue
        for side, offset in enumerate([0, 5]):
            link = cells[offset].find('a')
            if not link:
                continue
            q = {k.lower(): v[0] for k,v in parse_qs(urlparse(link.get('href', '')).query).items()}
            if q.get('a') != symbol or not all(re.fullmatch(r'[0-9A-Za-z]{4,32}', q.get(k, '')) for k in ['b','bhid']):
                raise sources.SourceError('五日主力券商身份不符')
            buy, sell, net = [sources.number(c.get_text()) for c in cells[offset+1:offset+4]]
            if any(v is None or v < 0 for v in [buy,sell,net]):
                raise sources.SourceError('五日主力張數無效')
            if abs((buy-sell) - (net if side==0 else -net)) > 2:
                raise sources.SourceError('五日主力買賣方向不符')
            rows.append(dict(broker=q['bhid'],branch=q['b'],name=link.get_text(strip=True),buy=buy,sell=sell,net=net if side==0 else -net))
            counts[side] += 1
    if counts!=[15,15] or len({(r['broker'],r['branch']) for r in rows})!=len(rows):
        raise sources.SourceError('五日主力未取得完整買賣各 15 名或名單重複')
    return rows


def trading_window(end):
    date.fromisoformat(end)
    with db.connect() as c:
        rows = {r['date']: dict(r) for r in c.execute('SELECT * FROM market_days WHERE date<=? ORDER BY date DESC LIMIT 60',(end,))}
        rs_day=c.execute('SELECT payload FROM rs_days WHERE date=?',(end,)).fetchone()
    if rs_day:
        verified=json.loads(rs_day['payload']).get('trading_days',[])
        if len(verified)>=5 and verified[-1]==end:return verified[-5:]
    days = sorted(d for d,r in rows.items() if r['twse_status']=='ok')[-5:]
    if len(days)!=5 or days[-1]!=end:
        raise sources.SourceError('需先補齊此日期之前的五個交易日行情')
    current = date.fromisoformat(days[0])
    while current.isoformat()<=end:
        if current.weekday()<5:
            r=rows.get(current.isoformat())
            if not r or r['twse_status'] not in ['ok','no_data']:
                raise sources.SourceError('交易日曆尚未完整，無法確認近五日範圍')
        current+=timedelta(days=1)
    return days


def estimate(branch, history, prices, days):
    if any(r['date'] not in days for r in history):
        raise sources.SourceError('個股明細出現五個交易日以外資料')
    if any(r['date'] not in prices for r in history):
        raise sources.SourceError('個股明細缺少當日收盤價，未以其他日期補值')
    # Source rounds each daily column and the period column independently.
    for field in ['buy','sell']:
        if abs(sum(r[field] for r in history)-branch[field])>len(days)+1:
            raise sources.SourceError('每日張數與五日期間合計不符，未發布不完整排名')
    buy = sum(r['buy']*1000*prices[r['date']] for r in history)
    sell = sum(r['sell']*1000*prices[r['date']] for r in history)
    return dict(branch, buy_amount=buy, sell_amount=sell, turnover=buy+sell)


def pool_balance(pool):
    buy=sum(r['buy'] for r in pool)
    sell=sum(r['sell'] for r in pool)
    return dict(buy=buy,sell=sell,net=buy-sell,branches=len(pool))


def status(symbol, end):
    validate(symbol,end)
    with db.connect() as c:
        row=c.execute('SELECT * FROM major_turnover WHERE symbol=? AND date=?',(symbol,end)).fetchone()
    if not row:
        return dict(symbol=symbol,date=end,status='idle',message='點選載入，僅查詢這檔股票的五日主力',payload=None)
    result=dict(row)
    result['payload']=json.loads(result['payload']) if result['payload'] else None
    return result


def validate(symbol, end):
    if not isinstance(symbol,str) or not isinstance(end,str) or not re.fullmatch(r'[0-9A-Z]{4,7}',symbol) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',end):
        raise ValueError('股票代碼或日期格式錯誤')
    date.fromisoformat(end)


def start(symbol, end):
    validate(symbol,end)
    # All reads are reusable; a second request never refreshes a complete result.
    with _lock:
        current=status(symbol,end)
        if current['status'] in ['complete','queued','running']:
            return current
        try:
            report=major.report(end)
            allowed={r['symbol'] for scope in ['market','watched'] for side in ['buy_top','sell_top'] for r in report[scope][side]}
        except ValueError:
            allowed=set()
        with db.connect() as c:
            local=c.execute('SELECT 1 FROM flows f JOIN branches b USING(broker,branch) WHERE b.active=1 AND f.symbol=? AND f.date<=? LIMIT 1',(symbol,end)).fetchone()
            rs_row=c.execute('SELECT payload FROM rs_days WHERE date=?',(end,)).fetchone()
        if local:allowed.add(symbol)
        if rs_row:
            payload=json.loads(rs_row['payload'])
            allowed.update(row['symbol'] for group in payload['groups'].values() for row in group if row['rs']>60)
        if symbol not in allowed:
            raise ValueError('請從關注個股或此日期的主力／RS 排行載入主力')
        trading_window(end)
        with db.connect() as c:
            c.execute('INSERT OR REPLACE INTO major_turnover VALUES (?,?,?,?,?,?,?)',(symbol,end,'queued','已排入個股查詢佇列',None,major.now(),None))
        try:
            threading.Thread(target=_drain,daemon=True).start()
        except Exception:
            with db.connect() as c:
                c.execute("UPDATE major_turnover SET status='failed',message='無法啟動查詢，請重試' WHERE symbol=? AND date=?",(symbol,end))
            raise
    return status(symbol,end)


_worker_lock=threading.Lock()


def _drain():
    # Blocking acquisition avoids stranding a newly queued request during worker shutdown.
    with _worker_lock:
        while True:
            with db.connect() as c:
                row=c.execute("SELECT symbol,date FROM major_turnover WHERE status='queued' ORDER BY started_at,symbol LIMIT 1").fetchone()
            if not row:
                return
            _fetch(row['symbol'],row['date'])


def _message(symbol,end,message):
    with db.connect() as c:
        c.execute("UPDATE major_turnover SET status='running',message=? WHERE symbol=? AND date=?",(message,symbol,end))


def _fetch(symbol,end):
    try:
        _message(symbol,end,'核對五個交易日與主力名單')
        days=trading_window(end)
        url=sources.BASE+'/z/zc/zco/zco.djhtm?'+urlencode({'a':symbol,'e':days[0],'f':end})
        try:
            pool=parse_pool(sources.get(url),symbol,days[0],end)
        except sources.SourceError:
            pool=parse_pool(sources.get(url,refresh=True),symbol,days[0],end)
        with db.connect() as c:
            prices={r['date']:r['close'] for r in c.execute('SELECT date,close FROM prices WHERE symbol=? AND date>=? AND date<=?',(symbol,days[0],end))}
        histories=[]
        for i,branch in enumerate(pool):
            _message(symbol,end,f'計算五日交易額 {i+1} / {len(pool)} · {branch["name"]}')
            try:
                history=sources.history(branch['broker'],branch['branch'],symbol,symbol,days[0],end,refresh=False)
            except sources.SourceError:
                history=sources.history(branch['broker'],branch['branch'],symbol,symbol,days[0],end,refresh=True)
            histories.append((branch,history))
        missing={r['date'] for _,history in histories for r in history if r['date'] not in prices}
        for day in sorted(missing):
            for market in ['TWSE','TPEX']:
                match=next((r for r in sources.market_prices(day,market) if r['symbol']==symbol),None)
                if match:
                    prices[day]=match['close'];break
        rows=[estimate(branch,history,prices,days) for branch,history in histories]
        rows.sort(key=lambda r:(-r['turnover'],r['broker'],r['branch']))
        payload=dict(days=days,start=days[0],end=end,candidate_count=len(pool),top=rows[:3],source=url,pool_balance=pool_balance(pool),
                     method='sum_daily_close',estimated=True,
                     scope='近五日買超、賣超各前 15 名券商的聯集；不是全部券商交易額排名。',
                     formula='Σ（每日買進張數＋每日賣出張數）× 1,000 × 當日收盤價；估算金額，非實際成交金額。')
        with db.connect() as c:
            c.execute("UPDATE major_turnover SET status='complete',message='五日主力交易額已計算並快取',payload=?,finished_at=? WHERE symbol=? AND date=?",(json.dumps(payload,ensure_ascii=False),major.now(),symbol,end))
    except Exception as error:
        message=str(error) if isinstance(error,(ValueError,sources.SourceError)) else f'查詢失敗：{type(error).__name__}'
        with db.connect() as c:
            c.execute("UPDATE major_turnover SET status='failed',message=?,finished_at=? WHERE symbol=? AND date=?",(message,major.now(),symbol,end))
