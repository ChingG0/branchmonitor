"""Independent ETF/ETN RS cache; reads exchange snapshots without branch backfill."""
import json
import threading
from datetime import date,datetime,timedelta
from urllib.parse import urlencode
from . import db,sources,rs
from .rs import TZ,MODES,now,roc,previous_month,calculate
_lock=threading.Lock()

def events(start,end):
    # TPEx truncates large multi-month corporate-event responses. Validate each
    # smaller response with the same date/field checks as the stock RS pipeline.
    cursor=date.fromisoformat(start);last=date.fromisoformat(end);result=[]
    while cursor<=last:
        stop=min(last,cursor+timedelta(days=89))
        result.extend(rs.events(cursor.isoformat(),stop.isoformat()))
        cursor=stop+timedelta(days=1)
    return result

def index_month(month,refresh=False):
    try:return rs.index_month(month,refresh=refresh)
    except sources.SourceError:
        if not refresh:raise
        # A previously validated official month can give an earlier as-of date.
        # The report always displays that date rather than inventing fresh data.
        return rs.index_month(month,refresh=False)

def quote_day(day,market,refresh=False):
    # Older rs_quotes/prices omit ETNs. Reparse the archived exchange response
    # rather than mislabel those legacy snapshots as complete ETF/ETN coverage.
    try:rows=sources.market_prices(day,market,refresh=refresh)
    except sources.SourceError:
        if not refresh:raise
        rows=sources.market_prices(day,market,refresh=False)
    if len(rows)<300:raise sources.SourceError(f'{day} {market} 行情不足，保留 ETF RS 結果')
    # Keep one date-verified shared snapshot so split fallbacks can compare the
    # target day with the immediately preceding trading day without refetching.
    with db.connect() as c:
        c.execute('INSERT OR REPLACE INTO rs_quotes VALUES (?,?,?,?)',
                  (day,market,json.dumps(rows,ensure_ascii=False),now()))
    return rows

def parse_splits(data,start,end,market):
    if data.get('stat','').lower()!='ok':raise sources.SourceError('ETF 分割資料未就緒')
    if market=='TWSE':
        if data.get('startDate')!=start.replace('-','') or data.get('endDate')!=end.replace('-',''):
            raise sources.SourceError('ETF 分割區間不符')
        tables=[data];symbol_field='ETF代號'
    else:
        if data.get('date')!=start.replace('-','')+'~'+end.replace('-',''):
            raise sources.SourceError('ETF 分割區間不符')
        tables=data.get('tables',[]);symbol_field='證券代號'
    if not tables:raise sources.SourceError('ETF 分割資料缺少表格')
    output=[]
    for table in tables:
        fields=table.get('fields',[])
        if '恢復買賣日期' not in fields or symbol_field not in fields:raise sources.SourceError('ETF 分割欄位變動')
        for values in table.get('data',[]):
            row=dict(zip(fields,values));day=roc(row['恢復買賣日期'])
            if not start<=day<=end:raise sources.SourceError('ETF 分割日期超出區間')
            output.append(dict(symbol=row[symbol_field].strip(),date=day,kind='split'))
    return output


def tpex_split_fallback(start,end,path):
    """Extend the last validated split range by one verified trading day."""
    with db.connect() as c:
        current=c.execute('SELECT payload FROM rs_quotes WHERE date=? AND market=\'TPEX\'',(end,)).fetchone()
        prior=c.execute('SELECT date,payload FROM rs_quotes WHERE date<? AND market=\'TPEX\' ORDER BY date DESC LIMIT 1',(end,)).fetchone()
    if not current or not prior or len(json.loads(current['payload']))<300 or len(json.loads(prior['payload']))<300:
        raise sources.SourceError('TPEX ETF 分割備援缺少相鄰交易日行情')
    prior_day=prior['date']
    previous_url='https://www.tpex.org.tw/www/zh-tw/'+path+'?'+urlencode(dict(
        startDate=start.replace('-','/'),endDate=prior_day.replace('-','/'),response='json'))
    previous=parse_splits(json.loads(sources.get(previous_url)),start,prior_day,'TPEX')
    before={row['symbol'] for row in json.loads(prior['payload']) if row.get('close')}
    after={row['symbol'] for row in json.loads(current['payload']) if row.get('close')}
    # Both split feeds publish a resume-trading date. Conservatively excluding
    # every newly resumed symbol also covers an event that the page API cannot
    # serve during a transient outage.
    latest=[{'symbol':symbol,'date':end,'kind':'split'} for symbol in sorted(after-before)]
    return previous+latest

def split_events(start,end):
    result=[]
    for market,path in [('TWSE','split/TWTCAU'),('TPEX','bulletin/etfSplitRslt'),('TPEX','bulletin/etfRvsRslt')]:
        base='https://www.twse.com.tw/rwd/zh/' if market=='TWSE' else 'https://www.tpex.org.tw/www/zh-tw/'
        a=start.replace('-','') if market=='TWSE' else start.replace('-','/')
        b=end.replace('-','') if market=='TWSE' else end.replace('-','/')
        url=base+path+'?'+urlencode(dict(startDate=a,endDate=b,response='json'))
        try:parsed=parse_splits(json.loads(sources.get(url,refresh=True)),start,end,market)
        except sources.SourceError:
            if market!='TPEX':raise
            parsed=tpex_split_fallback(start,end,path)
        result.extend(parsed)
    return result

def job_status():
    with db.connect() as c:r=c.execute('SELECT * FROM etf_rs_state WHERE id=1').fetchone()
    return dict(r) if r else None

def progress(message):
    with db.connect() as c:c.execute('UPDATE etf_rs_state SET message=? WHERE id=1',(message,))

def refresh():
    if not _lock.acquire(blocking=False):return job_status()
    try:
        with db.connect() as c:c.execute('INSERT OR REPLACE INTO etf_rs_state VALUES (1,?,?,NULL,?)',('running',now(),'讀取官方交易日曆'))
        threading.Thread(target=_worker,daemon=True).start()
    except Exception:
        with db.connect() as c:c.execute("UPDATE etf_rs_state SET status='failed',message='無法啟動 RS 計算' WHERE id=1")
        _lock.release();raise
    return job_status()

def _worker():
    try:
        today=datetime.now(TZ).date();month=today.replace(day=1);calendar_rows={}
        for i in range(6):
            calendar_rows.update(index_month(month.isoformat(),refresh=i<2))
            valid=sorted(d for d in calendar_rows if d<=today.isoformat())
            if len(valid)>=61:break
            month=previous_month(month)
        if len(valid)<61:raise sources.SourceError('不足 61 個已驗證交易日，無法計算 60 日漲幅')
        as_of=valid[-1];base_year=date.fromisoformat(as_of).year-1
        december=index_month(f'{base_year}-12-01')
        if not december:raise sources.SourceError('缺少前一年年底指數')
        year_base=max(december);anchors={'20':valid[-21],'60':valid[-61],'ytd':year_base}
        snapshots={}
        for day in sorted(set([as_of,*anchors.values()])):
            snapshots[day]={}
            for market in ['TWSE','TPEX']:
                progress(f'RS 行情 · {day} · {"上市" if market=="TWSE" else "上櫃"}')
                for row in quote_day(day,market,refresh=day==as_of):
                    if row['symbol'] in snapshots[day]:raise sources.SourceError('上市櫃出現相同股票代碼')
                    snapshots[day][row['symbol']]=row
        latest_index=snapshots[as_of].get('TAIEX');base_index=snapshots[year_base].get('TAIEX')
        if not latest_index or not base_index or abs(latest_index['close']-calendar_rows[as_of])>.02 or abs(base_index['close']-december[year_base])>.02:
            raise sources.SourceError('加權指數兩個官方來源日期／數值不符')
        progress('核對除權、減資、面額變更，計算市場 RS 百分位')
        corporate=events(min(anchors.values()),as_of)+split_events(min(anchors.values()),as_of)
        ytd=(latest_index['close']/base_index['close']-1)*100
        payload=calculate(snapshots,anchors,as_of,ytd,corporate,universe='etf')
        if any(len(rows)<30 for rows in payload['groups'].values()):raise sources.SourceError('可評分樣本不足，保留既有結果')
        payload.update(index_close=latest_index['close'],index_base_close=base_index['close'],fetched_at=now(),trading_days=valid)
        with db.connect() as c:
            c.execute('INSERT OR REPLACE INTO etf_rs_days VALUES (?,?,?)',(as_of,json.dumps(payload,ensure_ascii=False),now()))
            c.execute("UPDATE etf_rs_state SET status='complete',finished_at=?,message=? WHERE id=1",(now(),f'ETF／ETN RS 已計算至 {as_of} · 20 日／60 日／YTD 可切換'))
    except Exception as error:
        message=str(error) if isinstance(error,ValueError) else f'RS 更新失敗：{type(error).__name__}'
        with db.connect() as c:c.execute("UPDATE etf_rs_state SET status='failed',finished_at=?,message=? WHERE id=1",(now(),message))
    finally:_lock.release()

def report(mode='20'):
    if mode not in MODES:raise ValueError('RS 期間需為 20、60 或 ytd')
    with db.connect() as c:
        r=c.execute('SELECT * FROM etf_rs_days ORDER BY date DESC LIMIT 1').fetchone()
        balances={row['symbol']:json.loads(row['payload']) for row in c.execute("SELECT symbol,payload FROM major_turnover WHERE date=? AND status='complete'",(r['date'] if r else '',))}
    if not r:return dict(available=False,mode=mode,job=job_status(),left=[],right=[],equal=[])
    payload=json.loads(r['payload']);rows=payload['groups'][mode];shown=[]
    for row in rows:
        if row['rs']<=60:continue
        cached=balances.get(row['symbol'],{});balance=cached.get('pool_balance')
        row['major_balance']=dict(balance,start=cached['start'],end=cached['end']) if balance else None
        shown.append(row)
    return dict(available=True,mode=mode,job=job_status(),as_of=payload['as_of'],anchor=payload['anchors'][mode],year_base=payload['anchors']['ytd'],
                index_ytd=payload['index_ytd'],index_close=payload['index_close'],index_base_close=payload['index_base_close'],fetched_at=payload['fetched_at'],
                universe=len(rows),hidden=len(rows)-len(shown),excluded=payload['excluded'][mode],
                left=[r for r in shown if r['excess_pct']>1e-9],right=[r for r in shown if r['excess_pct'] < -1e-9],equal=[r for r in shown if abs(r['excess_pct'])<=1e-9])
