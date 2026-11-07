"""Tracked-branch consensus, persistent turnover leaders and same-date RS overlap."""
from __future__ import annotations
import json,re,threading
from collections import Counter,defaultdict
from datetime import date,datetime,timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from . import db,sources,rs,sector_flow

START='2026-09-01'
TZ=ZoneInfo('Asia/Taipei')
_lock=threading.Lock()
_last_slot=None

def now():return datetime.now(TZ).isoformat(timespec='seconds')
def stock(symbol):return bool(re.fullmatch(r'[1-9]\d{3}',symbol))
def market_url(day,market):
    if market=='TWSE':return 'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?'+urlencode({'date':day.replace('-',''),'type':'ALLBUT0999','response':'json'})
    return 'https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?'+urlencode({'response':'json','date':day.replace('-','/')})

def parse_turnover(raw,day,market):
    data=json.loads(raw)
    if data.get('stat','').lower()!='ok' or data.get('date')!=day.replace('-',''):raise ValueError(f'{market} 成交金額日期不符或尚未發布')
    code,name,amount,close=('證券代號','證券名稱','成交金額','收盤價') if market=='TWSE' else ('代號','名稱','成交金額(元)','收盤')
    result=[];seen=set()
    for table in data.get('tables',[]):
        fields=table.get('fields',[])
        if not all(f in fields for f in [code,name,amount,close]):continue
        for values in table.get('data',[]):
            r=dict(zip(fields,values));symbol=str(r.get(code,''))
            if not stock(symbol):continue
            value=sources.number(r.get(amount));price=sources.number(r.get(close))
            if value is None or value<0:raise ValueError(f'{market} 成交金額缺漏')
            if not value:continue
            if symbol in seen:raise ValueError(f'{market} 個股重複')
            seen.add(symbol)
            result.append(dict(symbol=symbol,name=r[name],market=market,amount=value,close=price))
    if len(result)<30:raise ValueError(f'{market} 普通股成交金額不足，暫不保存排名')
    return result


def parse_tpex_openapi_turnover(data,day):
    """Parse the official current TPEx OpenAPI with strict date validation."""
    if not isinstance(data,list) or not data:raise ValueError('TPEX OpenAPI 沒有成交資料')
    expected=str(int(day[:4])-1911)+day[5:7]+day[8:10]
    if {str(row.get('Date','')).strip() for row in data}!={expected}:
        raise ValueError('TPEX OpenAPI 成交金額日期不符')
    result=[];seen=set()
    for row in data:
        symbol=str(row.get('SecuritiesCompanyCode','')).strip()
        if not stock(symbol):continue
        amount=sources.number(row.get('TransactionAmount'));close=sources.number(row.get('Close'))
        if amount is None or amount<0:raise ValueError('TPEX OpenAPI 成交金額缺漏')
        if not amount:continue
        if symbol in seen:raise ValueError('TPEX OpenAPI 個股重複')
        seen.add(symbol);result.append(dict(symbol=symbol,name=str(row.get('CompanyName','')).strip(),
                                            market='TPEX',amount=amount,close=close))
    if len(result)<30:raise ValueError('TPEX OpenAPI 普通股成交金額不足，暫不保存排名')
    return result


def market_turnover(day,market):
    url=market_url(day,market)
    try:
        raw=sources.get(url)
        try:return parse_turnover(raw,day,market)
        except (json.JSONDecodeError,KeyError,ValueError):
            return parse_turnover(sources.get(url,refresh=True),day,market)
    except sources.SourceError:
        if market!='TPEX':raise
        fallback='https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes'
        try:raw=sources.get(fallback,refresh=True)
        except sources.SourceError:raw=sources.get(fallback,refresh=False)
        return parse_tpex_openapi_turnover(json.loads(raw),day)

def record_day(day):
    # Reuse full exchange responses already cached by the branch backfill.
    rows=[]
    for market in ['TWSE','TPEX']:
        rows+=market_turnover(day,market)
    if len({r['symbol'] for r in rows})!=len(rows):raise ValueError('上市櫃個股代碼重複，暫不保存')
    rows=sorted(rows,key=lambda r:(-r['amount'],r['symbol']))[:30]
    for i,r in enumerate(rows,1):r['rank']=i
    payload=dict(rows=rows,sources=[market_url(day,m) for m in ['TWSE','TPEX']],definition='上市＋上櫃四碼普通股，依全日實際成交金額排名，不含 ETF／ETN／權證')
    with db.connect() as c:c.execute('INSERT OR REPLACE INTO focus_turnover_days VALUES (?,?,?)',(day,json.dumps(payload,ensure_ascii=False),now()))

def job_status():
    with db.connect() as c:r=c.execute('SELECT * FROM focus_state WHERE id=1').fetchone()
    return dict(r) if r else None

def progress(message):
    with db.connect() as c:c.execute('UPDATE focus_state SET message=? WHERE id=1',(message,))

def refresh():
    if not _lock.acquire(blocking=False):return job_status()
    try:
        with db.connect() as c:c.execute("INSERT OR REPLACE INTO focus_state VALUES (1,'running',?,NULL,?)",(now(),'正在確認 2026/9/1 起交易日；沿用已保存日期'))
        threading.Thread(target=_worker,daemon=True).start()
    except Exception:_lock.release();raise
    return job_status()

def _worker():
    try:
        current=datetime.now(TZ)
        cutoff=current.date() if current.strftime('%H:%M')>='14:30' else current.date()-timedelta(days=1)
        cursor=date.fromisoformat(START);days=[]
        while cursor<=cutoff:
            days.extend(d for d in rs.index_month(cursor.isoformat(),refresh=cursor.strftime('%Y-%m')==current.strftime('%Y-%m')) if START<=d<=cutoff.isoformat())
            cursor=date(cursor.year+1,1,1) if cursor.month==12 else date(cursor.year,cursor.month+1,1)
        days=sorted(set(days))
        with db.connect() as c:saved={r[0] for r in c.execute('SELECT date FROM focus_turnover_days')}
        pending=[d for d in days if d not in saved];failed=[]
        for i,d in enumerate(pending):
            progress(f'成交重心回補 {i+1}/{len(pending)} · {d}；沿用 {len(set(days)&saved)} 個交易日')
            try:record_day(d)
            except Exception as e:failed.append(d+'：'+str(e)[:90])
        message=f'成交重心已保存 {len(days)-len(failed)} 個交易日 · 從 {START} 起；新增 {len(pending)-len(failed)} 日'
        if failed:message+='；待重試 '+ '、'.join(failed[:4])
        with db.connect() as c:c.execute('UPDATE focus_state SET status=?,finished_at=?,message=? WHERE id=1',('partial' if failed else 'complete',now(),message))
    except Exception as e:
        with db.connect() as c:c.execute("UPDATE focus_state SET status='failed',finished_at=?,message=? WHERE id=1",(now(),'成交重心回補未完成，已保存資料保留：'+str(e)[:160]))
    finally:_lock.release()

def schedule_tick(current,config):
    global _last_slot
    if not config.get('schedule_enabled') or current.weekday()>4 or current.strftime('%H:%M')<max('14:30',config.get('schedule_time','18:30')):return
    slot=current.strftime('%Y-%m-%d %H')
    if slot==_last_slot:return
    with db.connect() as c:complete=c.execute('SELECT 1 FROM focus_turnover_days WHERE date=?',(current.date().isoformat(),)).fetchone()
    if complete:return
    _last_slot=slot;refresh()

def consensus(as_of=None):
    ignored=db.ignored_symbols()
    with db.connect() as c:
        c.execute('BEGIN')
        branches=[dict(r) for r in c.execute('SELECT * FROM branches WHERE active=1')]
        days=[r[0] for r in c.execute("SELECT DISTINCT s.date FROM snapshots s JOIN branches b USING(broker,branch) WHERE b.active=1 AND s.status='observed' ORDER BY s.date DESC")]
        if as_of and as_of not in days:raise ValueError('此日期尚無追蹤分點資料')
        as_of=as_of or (days[0] if days else None)
        observed={tuple(r) for r in c.execute("SELECT s.broker,s.branch FROM snapshots s JOIN branches b USING(broker,branch) WHERE b.active=1 AND s.status='observed' AND s.date=?",(as_of,))}
        flows=c.execute("""SELECT f.*,b.name AS branch_name,p.close FROM flows f JOIN branches b USING(broker,branch)
            JOIN snapshots s ON s.broker=f.broker AND s.branch=f.branch AND s.date=f.date
            LEFT JOIN prices p ON p.symbol=f.symbol AND p.date=f.date
            WHERE b.active=1 AND s.status='observed' AND f.date=?""",(as_of,)).fetchall()
    grouped={}
    for f in flows:
        if f['symbol'] in ignored or not stock(f['symbol']):continue
        r=grouped.setdefault(f['symbol'],dict(symbol=f['symbol'],name=f['name'],close=f['close'],buy_lots=0.,sell_lots=0.,net=0.,buyers=[],sellers=[]))
        r['net']+=f['net']
        if f['net']:
            value=f['net']*1000*f['close'] if f['close'] is not None else None
            side='buyers' if f['net']>0 else 'sellers';r[side].append(dict(name=f['branch_name'],broker=f['broker'],branch=f['branch'],net=f['net'],net_value=value))
            r['buy_lots' if f['net']>0 else 'sell_lots']+=abs(f['net'])
    rows=list(grouped.values())
    for r in rows:
        r['buy_count']=len(r['buyers']);r['sell_count']=len(r['sellers'])
        r['buy_value']=r['buy_lots']*1000*r['close'] if r['close'] is not None else None
        r['sell_value']=r['sell_lots']*1000*r['close'] if r['close'] is not None else None
        r['net_value']=r['net']*1000*r['close'] if r['close'] is not None else None
        for side in ['buyers','sellers']:r[side].sort(key=lambda b:(-abs(b['net']),b['name']))
    return dict(as_of=as_of,available_dates=days,rows=rows,observed=len(observed),total=len(branches),missing=[b['name'] for b in branches if (b['broker'],b['branch']) not in observed])

def rs_overlap():
    with db.connect() as c:r=c.execute('SELECT * FROM rs_days ORDER BY date DESC LIMIT 1').fetchone()
    if not r:return dict(as_of=None,rows=[])
    payload=json.loads(r['payload']);long={row['symbol']:row for row in payload['groups']['ytd'] if row['rs']>60}
    ignored=db.ignored_symbols();rows=[]
    for row in payload['groups']['20']:
        if row['rs']<=60 or row['symbol'] not in long or row['symbol'] in ignored:continue
        rows.append(dict(symbol=row['symbol'],name=row['name'],rs20=row['rs'],rs_ytd=long[row['symbol']]['rs'],return20=row['return_pct'],ytd_pct=row['ytd_pct'],excess_pct=row['excess_pct']))
    rows.sort(key=lambda r:(-min(r['rs20'],r['rs_ytd']),-r['rs20'],r['symbol']))
    return dict(as_of=r['date'],fetched_at=r['fetched_at'],rows=rows,anchors=payload['anchors'],index_ytd=payload['index_ytd'])

def tag(symbol,sector):
    if not isinstance(symbol,str) or not stock(symbol):raise ValueError('請提供四碼個股代碼')
    if not isinstance(sector,str) or len(sector.strip())>30 or any(ord(ch)<32 for ch in sector):raise ValueError('族群名稱限 30 字，不可含換行')
    with db.connect() as c:
        # Permit only symbols already present in local stock data or stored leaders.
        exists=c.execute('SELECT 1 FROM prices WHERE symbol=? LIMIT 1',(symbol,)).fetchone()
        if not exists:
            exists=any(any(r['symbol']==symbol for r in json.loads(day[0])['rows']) for day in c.execute('SELECT payload FROM focus_turnover_days'))
        if not exists:raise ValueError('此個股尚無本機資料')
        c.execute('INSERT OR REPLACE INTO focus_tags VALUES (?,?,?)',(symbol,sector.strip(),now()))
    return {'symbol':symbol,'sector':sector.strip()}

def report(as_of=None,turnover_date=None):
    with db.connect() as c:
        saved=c.execute('SELECT * FROM focus_turnover_days WHERE date>=? ORDER BY date DESC',(START,)).fetchall()
    classification=sector_flow.classification()
    memberships=classification['memberships']
    tags={symbol:'、'.join(names) for symbol,names in memberships.items()}
    dates=[r['date'] for r in saved]
    if turnover_date and turnover_date not in dates:raise ValueError('此日期尚未保存成交重心')
    target=turnover_date or (dates[0] if dates else None)
    selected=next((r for r in saved if r['date']==target),None)
    payload=json.loads(selected['payload']) if selected else {'rows':[],'sources':[]}
    # Counts use only days through the selected date; viewing history cannot see future appearances.
    appearances=Counter(r['symbol'] for day in saved if day['date']<=target for r in json.loads(day['payload'])['rows']) if target else Counter()
    return dict(consensus=consensus(as_of),rs=rs_overlap(),tags=tags,sector_memberships=memberships,sector_classification={k:v for k,v in classification.items() if k!='memberships'},turnover=dict(as_of=target,available_dates=dates,rows=[dict(r,appearances=appearances[r['symbol']]) for r in payload['rows']],sources=payload['sources'],fetched_at=selected['fetched_at'] if selected else None,start=START),job=job_status())
