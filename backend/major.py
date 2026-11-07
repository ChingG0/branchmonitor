"""Separate all-market MoneyDJ rankings and the user's watched-branch aggregate."""
from __future__ import annotations

import json
import re
import threading
from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from . import db, sources

TZ=ZoneInfo('Asia/Taipei')
_refresh_lock=threading.Lock()
FAQ='https://www.moneydj.com/e/newage/moneydj_faq1.htm'


def now():
    return datetime.now(TZ).isoformat(timespec='seconds')


def parse_market(raw, expected, market, side):
    """The ranking omits its year; corroborate its MM/DD with a full-date source probe."""
    # Flat-price rows contain a duplicated closing cell tag in the source HTML.
    html=re.sub(r'</td>\s*</td>', '</td>', sources.decode_html(raw), flags=re.I)
    soup=BeautifulSoup(html,'html.parser')
    table=soup.select_one('#oMainTable')
    label=('上市' if market=='0' else '上櫃')+'主力'+('買超' if side=='F' else '賣超')+'1日排行'
    selection=re.search(r"document\.otcchk\.M\.options\[i\]\.value\s*==\s*'([^']+)'",html)
    selected=soup.select_one(f'select[name="M"] option[value="{market}01"]')
    if not table or not selection or selection[1]!=market+'01' or not selected or selected.get_text(strip=True)!=label:
        raise sources.SourceError('主力排行市場、方向或單日範圍不符')
    date_label=table.select_one('.t11')
    match=re.fullmatch(r'日期[：:]\s*(\d{2}/\d{2})',date_label.get_text(strip=True) if date_label else '')
    if not match or match[1]!=date.fromisoformat(expected).strftime('%m/%d'):
        raise sources.SourceError('主力排行日期與完整日期探測不符，保留既有資料')
    headers=[c.get_text(strip=True) for c in table.select('#oScrollMenu > td')]
    if headers!=['名次','股票名稱','收盤價','漲跌','漲跌幅','買進張數','賣出張數','買賣超張數']:
        raise sources.SourceError('主力排行欄位或單位改變')
    rows=[]
    for tr in table.select('tr'):
        cells=tr.find_all('td',recursive=False)
        if len(cells)!=8 or not cells[0].get_text(strip=True).isdigit():
            continue
        link=cells[1].find('a')
        identity=re.fullmatch(r"javascript:Link2Stk\('([0-9A-Z]+)'\)",link.get('href','') if link else '')
        if not identity:
            raise sources.SourceError('主力排行股票代碼無法驗證')
        symbol=identity[1]
        name=link.get_text(strip=True).removeprefix(symbol).strip()
        nums=[sources.number(c.get_text().replace('%','').replace('\xa0','')) for c in cells[2:]]
        if not name or any(v is None for v in nums) or nums[0]<=0 or min(nums[3:5])<0:
            raise sources.SourceError('主力排行數值缺失')
        close,change,change_pct,buy,sell,net=nums
        if (side=='F' and net<=0) or (side=='FA' and net>=0):
            raise sources.SourceError('主力排行買賣方向不符')
        rows.append(dict(symbol=symbol,name=name,close=close,change=change,change_pct=change_pct,
                         buy=buy,sell=sell,net=net,market='上市' if market=='0' else '上櫃',rank=int(cells[0].get_text())))
    if len(rows)<30 or len({r['symbol'] for r in rows})!=len(rows) or [r['rank'] for r in rows]!=list(range(1,len(rows)+1)):
        raise sources.SourceError('主力排行不足前 30 名或名次重複／缺漏')
    expected_order=sorted(rows,key=lambda r:r['net'],reverse=side=='F')
    if [r['net'] for r in rows]!=[r['net'] for r in expected_order]:
        raise sources.SourceError('主力排行順序不符')
    return rows


def refresh_status():
    with db.connect() as c:
        row=c.execute('SELECT * FROM major_state WHERE id=1').fetchone()
    return dict(row) if row else None


def start_refresh():
    if not _refresh_lock.acquire(blocking=False):
        return refresh_status()
    try:
        with db.connect() as c:
            c.execute('INSERT OR REPLACE INTO major_state VALUES (1,?,?,NULL,?)',('running',now(),'正在讀取上市、上櫃主力排行'))
        threading.Thread(target=_refresh_worker,daemon=True).start()
    except Exception:
        _refresh_lock.release()
        raise
    return refresh_status()


def ranking_date(branch_day):
    """A delayed branch feed cannot veto newer, fully verified exchange prices."""
    today=datetime.now(TZ).date().isoformat()
    if branch_day>today:raise sources.SourceError('來源日期晚於本機日期')
    with db.connect() as c:
        row=c.execute("""SELECT MAX(m.date) FROM market_days m JOIN prices p ON p.date=m.date
            WHERE m.twse_status='ok' AND m.tpex_status='ok' AND p.symbol='TAIEX'
            AND p.close>0 AND m.date<=?""",(today,)).fetchone()
    official=row[0] if row and row[0] else None
    return max(branch_day,official or branch_day),official


def _refresh_worker():
    try:
        with db.connect() as c:
            branch=c.execute('SELECT broker,branch FROM branches WHERE active=1 ORDER BY broker,branch LIMIT 1').fetchone()
        broker,branch_id=(branch['broker'],branch['branch']) if branch else ('9300','9339')
        branch_day,_=sources.ranking(broker,branch_id,refresh=True)
        expected,official_day=ranking_date(branch_day)
        payload={'buys':[],'sells':[],'sources':[],'date_probe':{'broker':broker,'branch':branch_id,'date':expected,'branch_date':branch_day,'official_market_date':official_day}}
        for side in ['F','FA']:
            for market in ['0','1']:
                url=sources.BASE+f'/z/zg/zg_{side}_{market}_1.djhtm'
                rows=parse_market(sources.get(url,refresh=True),expected,market,side)
                payload['buys' if side=='F' else 'sells'].extend(rows)
                payload['sources'].append({'label':('上市' if market=='0' else '上櫃')+('買超' if side=='F' else '賣超'),'url':url,'rows':len(rows)})
        for key in ['buys','sells']:
            if len({r['symbol'] for r in payload[key]})!=len(payload[key]):
                raise sources.SourceError('上市、上櫃排行出現重複股票，暫不合併')
        if {r['symbol'] for r in payload['buys']} & {r['symbol'] for r in payload['sells']}:
            raise sources.SourceError('同日股票同時出現在主力買超與賣超榜，暫不合併')
        stamp=now()
        with db.connect() as c:
            c.execute('INSERT OR REPLACE INTO major_days VALUES (?,?,?)',(expected,json.dumps(payload,ensure_ascii=False),stamp))
            c.execute('UPDATE major_state SET status=?,finished_at=?,message=? WHERE id=1',('complete',stamp,f'全市場主力排行已更新至 {expected}'))
    except Exception as error:
        with db.connect() as c:
            c.execute('UPDATE major_state SET status=?,finished_at=?,message=? WHERE id=1',('failed',now(),str(error)[:250]))
    finally:
        _refresh_lock.release()


def schedule_tick(current, config):
    if not config.get('schedule_enabled') or current.weekday()>=5 or current.strftime('%H:%M')<config.get('schedule_time','18:30'):
        return
    with db.connect() as c:
        latest=c.execute('SELECT MAX(date) FROM major_days').fetchone()[0]
        state=c.execute('SELECT * FROM major_state WHERE id=1').fetchone()
    if latest==current.date().isoformat() or (state and (state['status']=='running' or state['started_at'][:13]==current.isoformat()[:13])):
        return
    start_refresh()


def report(as_of=None):
    ignored=db.ignored_symbols()
    with db.connect() as c:
        c.execute('BEGIN')
        branches=[dict(r) for r in c.execute('SELECT * FROM branches WHERE active=1 ORDER BY name')]
        order=c.execute("SELECT value FROM settings WHERE key='watchlist_order'").fetchone()
        order=json.loads(order[0]) if order else []
        branches.sort(key=lambda b:order.index(b['broker']+':'+b['branch']) if b['broker']+':'+b['branch'] in order else len(order))
        dates={r[0] for r in c.execute("SELECT DISTINCT s.date FROM snapshots s JOIN branches b USING(broker,branch) WHERE b.active=1 AND s.status='observed'")}
        market_dates=[r[0] for r in c.execute('SELECT date FROM major_days ORDER BY date DESC')]
        dates=sorted(dates|set(market_dates),reverse=True)
        if as_of and as_of not in dates:
            raise ValueError('此日期尚無主力排行或追蹤分點資料')
        as_of=as_of or (dates[0] if dates else None)
        snapshots={(r['broker'],r['branch']):dict(r) for r in c.execute('SELECT * FROM snapshots WHERE date=?',(as_of,))}
        coverage={(r['broker'],r['branch'],r['symbol']) for r in c.execute('SELECT broker,branch,symbol FROM history_intervals WHERE start<=? AND end>=?',(as_of,as_of))}
        candidates=defaultdict(set)
        for r in c.execute('SELECT broker,branch,symbol FROM candidates'):
            if r['symbol'] in ignored:continue
            candidates[(r['broker'],r['branch'])].add(r['symbol'])
        flows=[dict(r) for r in c.execute("""SELECT f.*,p.close,p.market FROM branches b
            JOIN snapshots s ON s.broker=b.broker AND s.branch=b.branch AND s.date=? AND s.status='observed'
            JOIN flows f ON f.broker=b.broker AND f.branch=b.branch AND f.date=s.date
            LEFT JOIN prices p ON p.symbol=f.symbol AND p.date=f.date WHERE b.active=1""",(as_of,))]
        market_row=c.execute('SELECT * FROM major_days WHERE date=?',(as_of,)).fetchone()
        state=c.execute('SELECT * FROM major_state WHERE id=1').fetchone()
        cached_brokers={r['symbol']:json.loads(r['payload']) for r in c.execute("SELECT symbol,payload FROM major_turnover WHERE date=? AND status='complete'",(as_of,))}
    by_branch=defaultdict(list)
    by_stock={}
    names={(b['broker'],b['branch']):b['name'] for b in branches}
    for r in flows:
        if r['symbol'] in ignored:continue
        key=(r['broker'],r['branch'])
        by_branch[key].append(r)
        stock=by_stock.setdefault(r['symbol'],dict(symbol=r['symbol'],name=r['name'],buy=0.,sell=0.,net=0.,close=r['close'],market=r['market'],contributors=[]))
        for field in ['buy','sell','net']:stock[field]+=r[field]
        stock['contributors'].append(dict(broker=r['broker'],branch=r['branch'],name=names[key],buy=r['buy'],sell=r['sell'],net=r['net'],history_verified=(*key,r['symbol']) in coverage))
    for stock in by_stock.values():
        stock['net_value']=stock['net']*1000*stock['close'] if stock['close'] is not None else None
        stock['buy_branches']=sum(r['net']>0 for r in stock['contributors'])
        stock['sell_branches']=sum(r['net']<0 for r in stock['contributors'])
        stock['contributors'].sort(key=lambda r:abs(r['net']),reverse=True)
    branch_rows=[]
    for b in branches:
        key=(b['broker'],b['branch']);snapshot=snapshots.get(key);observed=bool(snapshot and snapshot['status']=='observed')
        rows=by_branch[key]
        verified=sum((*key,symbol) in coverage for symbol in candidates[key])
        branch_rows.append(dict(b,observed=observed,status='ready' if observed and verified==len(candidates[key]) else 'partial' if observed else 'missing',
            buy=sum(r['buy'] for r in rows) if observed else None,sell=sum(r['sell'] for r in rows) if observed else None,
            net=sum(r['net'] for r in rows) if observed else None,stocks=len(rows),verified=verified,candidates=len(candidates[key])))
    rows=list(by_stock.values())
    observed=sum(b['observed'] for b in branch_rows)
    totals={key:sum(r[key] for r in rows) if observed else None for key in ['buy','sell','net']}
    totals.update(stocks=len(rows),buy_stocks=sum(r['net']>0 for r in rows),sell_stocks=sum(r['net']<0 for r in rows),
                  net_value=sum(r['net_value'] for r in rows) if observed and all(r['net_value'] is not None for r in rows) else None,
                  ready_branches=sum(b['status']=='ready' for b in branch_rows),observed_branches=observed,total_branches=len(branches),
                  unpriced_stocks=sum(r['close'] is None for r in rows))
    watched={'totals':totals,'branches':branch_rows,'buy_top':sorted((r for r in rows if r['net']>0),key=lambda r:(-r['net'],r['symbol']))[:30],
             'sell_top':sorted((r for r in rows if r['net']<0),key=lambda r:(r['net'],r['symbol']))[:30],
             'buy_value_top':sorted((r for r in rows if r['net']>0 and r['net_value'] is not None),key=lambda r:(-r['net_value'],r['symbol']))[:30],
             'sell_value_top':sorted((r for r in rows if r['net']<0 and r['net_value'] is not None),key=lambda r:(r['net_value'],r['symbol']))[:30]}
    market={'available':bool(market_row),'latest_date':market_dates[0] if market_dates else None,'buy_top':[],'sell_top':[],
            'buy_value_top':[],'sell_value_top':[],
            'sources':[],'fetched_at':None,'definition_url':FAQ,'job':dict(state) if state else None}
    if market_row:
        payload=json.loads(market_row['payload'])
        for row in payload['buys']+payload['sells']:
            row['net_value']=row['net']*1000*row['close'] if row.get('close') is not None else None
        market.update(buy_top=sorted(payload['buys'],key=lambda r:(-r['net'],r['symbol']))[:30],
                      sell_top=sorted(payload['sells'],key=lambda r:(r['net'],r['symbol']))[:30],
                      buy_value_top=sorted((r for r in payload['buys'] if r['net_value'] is not None),key=lambda r:(-r['net_value'],r['symbol']))[:30],
                      sell_value_top=sorted((r for r in payload['sells'] if r['net_value'] is not None),key=lambda r:(r['net_value'],r['symbol']))[:30],
                      sources=payload['sources'],fetched_at=market_row['fetched_at'])
    for scope in [watched,market]:
        for side in ['buy_top','sell_top','buy_value_top','sell_value_top']:
            for row in scope[side]:
                cached=cached_brokers.get(row['symbol'],{})
                row['top_broker_names']=[b['name'] for b in cached.get('top',[])]
                balance=cached.get('pool_balance')
                row['major_balance']=dict(balance,start=cached['start'],end=cached['end']) if balance else None
    return {'as_of':as_of,'available_dates':dates,'watched':watched,'market':market}
