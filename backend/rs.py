"""Manually refreshed, market-wide relative price-strength percentiles."""
from __future__ import annotations
import json
import re
import threading
from collections import Counter
from datetime import date,datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from . import db,sources

TZ=ZoneInfo('Asia/Taipei')
_lock=threading.Lock()
MODES=['20','60','ytd']

def now():return datetime.now(TZ).isoformat(timespec='seconds')

def roc(value):
    parts=re.findall(r'\d+',str(value))
    if len(parts)==1 and len(parts[0])==7:parts=[parts[0][:3],parts[0][3:5],parts[0][5:]]
    if len(parts)!=3:raise sources.SourceError('官方資料日期格式改變')
    y,m,d=map(int,parts)
    return date(y+1911 if y<1911 else y,m,d).isoformat()

def parse_index(data,month):
    if '沒有符合條件' in data.get('stat',''):return {}
    if data.get('date')!=month.replace('-',''):
        raise sources.SourceError('加權指數月份不符')
    fields=data.get('fields',[])
    if data.get('stat','').lower()!='ok' or '發行量加權股價指數' not in fields or '日期' not in fields:
        raise sources.SourceError('無法驗證官方加權指數日曆')
    rows={}
    for values in data.get('data',[]):
        row=dict(zip(fields,values));day=roc(row['日期']);close=sources.number(row['發行量加權股價指數'])
        if day[:7]!=month[:7] or close is None or close<=0 or day in rows:raise sources.SourceError('加權指數日期或價格無效')
        rows[day]=close
    if not rows:raise sources.SourceError('加權指數月份沒有交易資料')
    return rows

def index_month(month,refresh=False):
    url='https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?'+urlencode({'date':month.replace('-',''),'response':'json'})
    return parse_index(json.loads(sources.get(url,refresh=refresh)),month)

def previous_month(day):
    return date(day.year-1,12,1) if day.month==1 else date(day.year,day.month-1,1)

def quote_day(day,market,refresh=False):
    with db.connect() as c:
        saved=c.execute('SELECT payload FROM rs_quotes WHERE date=? AND market=?',(day,market)).fetchone()
        if saved and not refresh:return json.loads(saved['payload'])
        # A complete existing exchange snapshot is reusable without modifying branch data.
        field='twse_status' if market=='TWSE' else 'tpex_status'
        complete=c.execute(f'SELECT 1 FROM market_days WHERE date=? AND {field}=\'ok\'',(day,)).fetchone()
        local=[dict(r) for r in c.execute('SELECT * FROM prices WHERE date=? AND market=?',(day,market))] if complete and not refresh else []
    rows=local or sources.market_prices(day,market,refresh=refresh)
    if len(rows)<300:raise sources.SourceError(f'{day} {market} 行情不足，保留上次 RS 結果')
    with db.connect() as c:c.execute('INSERT OR REPLACE INTO rs_quotes VALUES (?,?,?,?)',(day,market,json.dumps(rows,ensure_ascii=False),now()))
    return rows

def parse_events(data,start,end,market,kind):
    # TWSE returns this exact response for a valid query with no events.
    if market=='TWSE' and data.get('stat')=='很抱歉，沒有符合條件的資料!':return []
    if data.get('stat','').lower()!='ok':raise sources.SourceError('公司事件資料未就緒，未發布可能失真的 RS')
    if market=='TPEX' and data.get('date')!=start.replace('-','')+'~'+end.replace('-',''):
        raise sources.SourceError('公司事件查詢區間不符')
    if market=='TWSE':
        params=data.get('params',{})
        if params:
            if params.get('startDate')!=start.replace('-','') or params.get('endDate')!=end.replace('-',''):raise sources.SourceError('公司事件查詢區間不符')
        else:
            dates=re.findall(r'(\d+年\d+月\d+日)',data.get('title',''))
            if [roc(d) for d in dates]!=[start,end]:raise sources.SourceError('公司事件標題日期不符')
    table=data.get('tables',[data])[0]
    fields=table.get('fields',[])
    symbol_field=next((f for f in ['股票代號','證券代號','代號'] if f in fields),None)
    date_field='資料日期' if market=='TWSE' and kind=='rights' else '除權息日期' if kind=='rights' else '恢復買賣日期'
    if not symbol_field or date_field not in fields or (kind=='rights' and '權/息' not in fields):raise sources.SourceError('公司事件欄位改變')
    result=[]
    for values in table.get('data',[]):
        row=dict(zip(fields,values));day=roc(row[date_field]);symbol=row[symbol_field].strip()
        if not start<=day<=end:raise sources.SourceError('公司事件回傳區間外日期')
        if kind=='rights' and '權' not in row['權/息']:continue
        if re.fullmatch(r'\d{4}|00[0-9A-Z]{2,5}|02\d{3}[0-9BLR]',symbol):result.append({'symbol':symbol,'date':day,'kind':kind})
    return result


def parse_tpex_openapi_rights(data, day):
    """Read current TPEx ex-right events; cash-only dividends do not distort RS."""
    if not isinstance(data,list) or not data:
        raise sources.SourceError('TPEX 除權 OpenAPI 沒有資料')
    dated=[];result=[]
    for row in data:
        event_day=roc(row.get('Date',''));dated.append(event_day)
        if event_day!=day or '權' not in str(row.get('ExRightsDiviend','')):continue
        symbol=str(row.get('SecuritiesCompanyCode','')).strip()
        if re.fullmatch(r'\d{4}|00[0-9A-Z]{2,5}|02\d{3}[0-9BLR]',symbol):
            result.append({'symbol':symbol,'date':day,'kind':'rights'})
    # The current feed may include near-future schedules. Requiring it to span
    # the requested day prevents a stale response from certifying a newer day.
    if min(dated)>day or max(dated)<day:
        raise sources.SourceError('TPEX 除權 OpenAPI 尚未涵蓋目標日')
    return result


def tpex_event_fallback(start,end,kind):
    """Extend the last validated event range by one verified trading day."""
    with db.connect() as c:
        current=c.execute('SELECT payload FROM rs_quotes WHERE date=? AND market=\'TPEX\'',(end,)).fetchone()
        prior=c.execute('SELECT date,payload FROM rs_quotes WHERE date<? AND market=\'TPEX\' ORDER BY date DESC LIMIT 1',(end,)).fetchone()
    if not current or not prior or len(json.loads(current['payload']))<300 or len(json.loads(prior['payload']))<300:
        raise sources.SourceError('TPEX 事件備援缺少相鄰交易日行情')
    prior_day=prior['date']
    path={'rights':'bulletin/exDailyQ','capital':'bulletin/revivt','par':'bulletin/pvChgRslt'}[kind]
    previous_url='https://www.tpex.org.tw/www/zh-tw/'+path+'?'+urlencode({
        'startDate':start.replace('-','/'),'endDate':prior_day.replace('-','/'),'response':'json'})
    previous=parse_events(json.loads(sources.get(previous_url)),start,prior_day,'TPEX',kind)
    if kind=='rights':
        latest_url='https://www.tpex.org.tw/openapi/v1/tpex_exright_daily'
        try:raw=sources.get(latest_url,refresh=True)
        except sources.SourceError:raw=sources.get(latest_url,refresh=False)
        latest=parse_tpex_openapi_rights(json.loads(raw),end)
    else:
        # Reduction/par-value changes resume after a trading suspension. The
        # conservative delta excludes every security that has a valid close on
        # the target day but not on the immediately preceding trading day.
        before={row['symbol'] for row in json.loads(prior['payload']) if row.get('close')}
        after={row['symbol'] for row in json.loads(current['payload']) if row.get('close')}
        latest=[{'symbol':symbol,'date':end,'kind':kind} for symbol in sorted(after-before)]
    return previous+latest

def events(start,end):
    results=[]
    paths={'TWSE':{'rights':'exRight/TWT49U','capital':'reducation/TWTAUU','par':'change/TWTB8U'},
           'TPEX':{'rights':'bulletin/exDailyQ','capital':'bulletin/revivt','par':'bulletin/pvChgRslt'}}
    for market,types in paths.items():
        for kind,path in types.items():
            base='https://www.twse.com.tw/rwd/zh/' if market=='TWSE' else 'https://www.tpex.org.tw/www/zh-tw/'
            a=start.replace('-','') if market=='TWSE' else start.replace('-','/')
            b=end.replace('-','') if market=='TWSE' else end.replace('-','/')
            url=base+path+'?'+urlencode({'startDate':a,'endDate':b,'response':'json'})
            # Fixed historical end date; retry a cached source only if validation fails.
            try:parsed=parse_events(json.loads(sources.get(url)),start,end,market,kind)
            # SourceError subclasses ValueError, so it must be handled before
            # JSON/shape errors or the retry would bypass the official fallback.
            except sources.SourceError:
                if market!='TPEX':raise
                parsed=tpex_event_fallback(start,end,kind)
            except (json.JSONDecodeError,UnicodeDecodeError,IndexError):
                parsed=parse_events(json.loads(sources.get(url,refresh=True)),start,end,market,kind)
            results.extend(parsed)
    return results

def rank(rows):
    """0–100 inclusive midrank: tied returns receive identical percentiles."""
    ordered=sorted(rows,key=lambda r:r['return_pct'])
    n=len(ordered);i=0
    while i<n:
        j=i+1
        while j<n and abs(ordered[j]['return_pct']-ordered[i]['return_pct'])<1e-9:j+=1
        percentile=100*((i+j-1)/2)/(n-1) if n>1 else 50.
        for row in ordered[i:j]:row['rs']=round(percentile,2)
        i=j
    return sorted(ordered,key=lambda r:(-r['rs'],-r['ytd_pct'],r['symbol']))

def calculate(snapshots,anchors,as_of,index_ytd,corporate_events,universe='stock'):
    current=snapshots[as_of];groups={};excluded={}
    for mode in MODES:
        rows=[];reasons=Counter();start=min(anchors[mode],anchors['ytd'])
        affected={r['symbol'] for r in corporate_events if start<r['date']<=as_of}
        for symbol,row in current.items():
            if universe=='etf':
                if not (re.fullmatch(r'00[0-9A-Z]{2,5}',symbol) or re.fullmatch(r'02\d{3}[0-9BLR]',symbol)):continue
            elif not re.fullmatch(r'\d{4}',symbol) or symbol.startswith('00'):continue
            if symbol in affected:reasons['公司事件待還原']+=1;continue
            base=snapshots[anchors[mode]].get(symbol);year=snapshots[anchors['ytd']].get(symbol)
            if not base or not year:reasons['缺少期初價格或新上市']+=1;continue
            if base['market']!=row['market'] or year['market']!=row['market']:reasons['轉板期間']+=1;continue
            ret=(row['close']/base['close']-1)*100;ytd=(row['close']/year['close']-1)*100
            item=dict(symbol=symbol,name=row['name'],market=row['market'],close=row['close'],return_pct=ret,ytd_pct=ytd,excess_pct=ytd-index_ytd)
            if universe=='etf':item['product_type']=('ETN' if symbol.startswith('02') else 'ETF')+(' · 槓桿' if symbol.endswith('L') else ' · 反向' if symbol.endswith('R') else '')
            rows.append(item)
        groups[mode]=rank(rows);excluded[mode]=dict(reasons)
    return {'groups':groups,'excluded':excluded,'anchors':anchors,'as_of':as_of,'index_ytd':index_ytd}

def backfill_local_days(start,end=None,corporate_events=None,min_universe=300):
    """Build point-in-time daily RS snapshots from verified local prices only."""
    start=date.fromisoformat(start).isoformat();end=date.fromisoformat(end).isoformat() if end else None
    with db.connect() as c:
        calendar=[r[0] for r in c.execute("SELECT date FROM market_days WHERE twse_status='ok' AND tpex_status='ok' ORDER BY date")]
        saved={r[0] for r in c.execute('SELECT date FROM rs_days')}
        candidates=[day for day in calendar if day>=start and (end is None or day<=end) and day not in saved]
        year_base=f'{date.fromisoformat(start).year-1}-12-31'
        base_rows=[]
        for market in ('TWSE','TPEX'):
            row=c.execute('SELECT payload FROM rs_quotes WHERE date=? AND market=?',(year_base,market)).fetchone()
            if not row:raise sources.SourceError(f'缺少 {year_base} {market} 年初基準行情，無法建立歷史 RS')
            base_rows.extend(json.loads(row['payload']))
    index={day:i for i,day in enumerate(calendar)}
    missing=[day for day in candidates if index[day]>=60]
    if not missing:return {'created':[],'start':start,'end':end,'skipped':len(candidates)}
    if corporate_events is None:corporate_events=events(year_base,max(missing))
    base={row['symbol']:row for row in base_rows};base_index=base.get('TAIEX')
    if not base_index or not base_index.get('close'):raise sources.SourceError('年初基準缺少加權指數')
    prepared=[]
    with db.connect() as c:
        def snapshot(day):
            return {r['symbol']:dict(r) for r in c.execute('SELECT symbol,name,market,close FROM prices WHERE date=?',(day,))}
        for day in missing:
            i=index[day];anchors={'20':calendar[i-20],'60':calendar[i-60],'ytd':year_base}
            current=snapshot(day);snapshots={day:current,anchors['20']:snapshot(anchors['20']),anchors['60']:snapshot(anchors['60']),year_base:base}
            current_index=current.get('TAIEX')
            if not current_index or not current_index.get('close'):raise sources.SourceError(f'{day} 缺少加權指數，無法建立歷史 RS')
            index_ytd=(current_index['close']/base_index['close']-1)*100
            payload=calculate(snapshots,anchors,day,index_ytd,corporate_events)
            if any(len(rows)<min_universe for rows in payload['groups'].values()):
                raise sources.SourceError(f'{day} 可評分樣本不足，未建立歷史 RS')
            payload.update(index_close=current_index['close'],index_base_close=base_index['close'],fetched_at=now(),
                           trading_days=calendar[max(0,i-60):i+1],source='verified-local-history')
            prepared.append((day,json.dumps(payload,ensure_ascii=False),now()))
        c.executemany('INSERT OR IGNORE INTO rs_days VALUES (?,?,?)',prepared)
    return {'created':[row[0] for row in prepared],'start':start,'end':end or max(missing),'skipped':len(candidates)-len(missing)}

def job_status():
    with db.connect() as c:r=c.execute('SELECT * FROM rs_state WHERE id=1').fetchone()
    return dict(r) if r else None

def progress(message):
    with db.connect() as c:c.execute('UPDATE rs_state SET message=? WHERE id=1',(message,))

def refresh():
    if not _lock.acquire(blocking=False):return job_status()
    try:
        with db.connect() as c:c.execute('INSERT OR REPLACE INTO rs_state VALUES (1,?,?,NULL,?)',('running',now(),'讀取官方交易日曆'))
        threading.Thread(target=_worker,daemon=True).start()
    except Exception:
        with db.connect() as c:c.execute("UPDATE rs_state SET status='failed',message='無法啟動 RS 計算' WHERE id=1")
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
        corporate=events(min(anchors.values()),as_of)
        ytd=(latest_index['close']/base_index['close']-1)*100
        payload=calculate(snapshots,anchors,as_of,ytd,corporate)
        if any(len(rows)<300 for rows in payload['groups'].values()):raise sources.SourceError('可評分樣本不足，保留既有結果')
        payload.update(index_close=latest_index['close'],index_base_close=base_index['close'],fetched_at=now(),trading_days=valid)
        with db.connect() as c:
            c.execute('INSERT OR REPLACE INTO rs_days VALUES (?,?,?)',(as_of,json.dumps(payload,ensure_ascii=False),now()))
            c.execute("UPDATE rs_state SET status='complete',finished_at=?,message=? WHERE id=1",(now(),f'RS 已計算至 {as_of} · 20 日／60 日／YTD 可切換'))
    except Exception as error:
        message=str(error) if isinstance(error,ValueError) else f'RS 更新失敗：{type(error).__name__}'
        with db.connect() as c:c.execute("UPDATE rs_state SET status='failed',finished_at=?,message=? WHERE id=1",(now(),message))
    finally:_lock.release()

def report(mode='20'):
    if mode not in MODES:raise ValueError('RS 期間需為 20、60 或 ytd')
    with db.connect() as c:
        r=c.execute('SELECT * FROM rs_days ORDER BY date DESC LIMIT 1').fetchone()
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
