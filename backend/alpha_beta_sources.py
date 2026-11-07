"""Isolated official total-return inputs; never rewrites strategy price tables."""
from __future__ import annotations
import hashlib,json,math,re,sqlite3
from collections import Counter
from datetime import date,datetime,timedelta
from pathlib import Path
from urllib.parse import urlencode
from concurrent.futures import ThreadPoolExecutor
import requests
from . import db,sources,rs

def stamp():return datetime.now(rs.TZ).isoformat(timespec='seconds')
def folder():return db.DATA/'alpha_beta'
def number(v):
    try:
        x=float(str(v).replace(',','').strip());return x if math.isfinite(x) else None
    except (ValueError,TypeError):return None
def fetch(url,force=False):
    root=folder()/'raw';root.mkdir(parents=True,exist_ok=True)
    path=root/(hashlib.sha256(url.encode()).hexdigest()+'.json')
    if path.exists() and not force:return json.loads(path.read_text())
    r=requests.get(url,headers={'User-Agent':'Mozilla/5.0 (compatible; BranchMonitor/1.0)'},timeout=(10,35));r.raise_for_status()
    data=r.json()
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,ensure_ascii=False));temp.replace(path)
    (root/(path.stem+'.meta')).write_text(json.dumps(dict(url=url,fetched_at=stamp(),sha256=hashlib.sha256(r.content).hexdigest())))
    return data
def month_ranges(start,end):
    cur=date.fromisoformat(start).replace(day=1);last=date.fromisoformat(end)
    while cur<=last:
        nxt=(cur.replace(day=28)+timedelta(days=4)).replace(day=1)
        yield cur,max(cur,date.fromisoformat(start)),min(last,nxt-timedelta(days=1))
        cur=nxt
def parse_benchmark(data,month,kind):
    if kind=='TAIEX_TR':
        if data.get('stat')!='OK' or data.get('date')!=month or '發行量加權股價報酬指數' not in data.get('fields',[]):raise ValueError('加權報酬指數月份或欄位不符')
        rows=data.get('data',[]);column=1
    else:
        if data.get('stat')!='ok' or data.get('date')!=month:raise ValueError('櫃買報酬指數月份不符')
        table=data['tables'][0]
        if not any('櫃買報酬指數' in s for s in table.get('fields',[])):raise ValueError('缺櫃買報酬指數，不能使用價格指數')
        rows=table.get('data',[]);column=2
    out={}
    for r in rows:
        day=rs.roc(r[0]);v=number(r[column])
        if day.replace('-','')[:6]!=month[:6] or not v or v<=0 or day in out:raise ValueError('報酬指數日期或數值異常')
        out[day]=v
    return out
def parse_cash_events(data,market,start,end):
    if str(data.get('stat','')).lower()!='ok':raise ValueError(market+'除權息資料未就緒')
    if market=='TWSE':
        if data.get('strDate')!=start.replace('-','') or data.get('endDate')!=end.replace('-',''):raise ValueError('除權息區間不符')
        table=data;keys=('資料日期','股票代號','權/息')
    else:
        if data.get('date')!=start.replace('-','')+'~'+end.replace('-',''):raise ValueError('櫃買除權息區間不符')
        table=data['tables'][0];keys=('除權息日期','代號','權/息')
    fields=table.get('fields',[])
    if not all(k in fields for k in (*keys,'除權息前收盤價','除權息參考價')):raise ValueError('除權息欄位缺漏')
    out=[]
    if table.get('totalCount') is not None and int(table['totalCount'])!=len(table.get('data',[])):raise ValueError('除權息資料遭截斷')
    for cells in table.get('data',[]):
        r=dict(zip(fields,cells));day=rs.roc(r[keys[0]]);symbol=str(r[keys[1]]).strip()
        if not start<=day<=end:raise ValueError('除權息含區間外日期')
        kind=str(r[keys[2]]);cash=number(r.get('現金股利',r.get('權值+息值')))
        # Rights/subscriptions are not cash dividends. Exclude rather than infer
        # a total return from a rounded reference price or future share count.
        out.append(dict(date=day,symbol=symbol,market=market,cash=cash if '權' not in kind else None,
                        unsafe='權' in kind or cash is None or cash<0,kind=kind,pre_close=number(r.get('除權息前收盤價'))))
    return out
def parse_amounts(data,day,market):
    if str(data.get('stat','')).lower()!='ok' or data.get('date')!=day.replace('-',''):raise ValueError('成交值行情日期不符')
    result={}
    for t in data.get('tables',[]):
        fs=t.get('fields',[]);sf=next((x for x in ['證券代號','代號'] if x in fs),None)
        af=next((x for x in fs if '成交金額' in x),None)
        if sf and af:
            for cells in t.get('data',[]):
                row=dict(zip(fs,cells));v=number(row.get(af));s=str(row[sf]).strip()
                if v is not None and v>=0:result[s]=v
    if len(result)<300:raise ValueError('官方成交金額覆蓋不足，不能以收盤價乘成交量冒充')
    return result
def classify(symbol,meta):
    if re.fullmatch(r'[1-9]\d{3}',symbol):return 'stocks',None
    if not re.fullmatch(r'00[0-9A-Z]{2,5}',symbol):return 'other','非ETF或普通股'
    if not meta:return ('bonds' if symbol.endswith(('B','D')) else 'unmatched'),'ETF官方分類待核對'
    kind=meta.get('基金類型','');index=meta.get('標的指數/追蹤指數名稱','');name=meta.get('基金中文名稱','')
    text=index+' '+name
    if symbol.endswith(('K','C')):return 'unmatched','外幣交易單位待換算'
    if '債' in kind or symbol.endswith(('B','D')) or '美債' in text or '公債' in text:return 'bonds',None
    if '期貨' in kind or symbol.endswith('U'):return 'commodities',None
    domestic=('國內成分' in kind and meta.get('是否包含國外成分股')=='否' and not re.search('美國|全球|日本|中國|香港|韓國|印度',text)) or ('槓桿/反向' in kind and re.search('臺灣|台灣|藍籌30',index))
    if domestic:return ('leveraged' if symbol.endswith(('L','R')) else 'domestic'),None
    return ('overseas_leveraged' if symbol.endswith(('L','R')) else 'overseas'),None

# Only explicitly matched US benchmarks are scored. Other global/bond/commodity
# funds stay visible with a missing-benchmark status, never compared to TAIEX.
def foreign_benchmark(meta):
    index=meta.get('標的指數/追蹤指數名稱','').upper().replace(' ','')
    if re.fullmatch(r'(?:標普500|S&P500)(?:指數|2倍槓桿指數|反向指數)?',index):return 'SPY'
    if 'NASDAQ-100' in index or 'NASDAQ100' in index or '納斯達克100' in index:return 'QQQ'
    if '道瓊' in index and ('工業' in index or '單日' in index):return 'DIA'
    if '美國費城半導體指數'==index:return 'SOXX'
    return None
def parse_proxy(data,ticker):
    if data.get('chart',{}).get('error'):raise ValueError('海外基準來源錯誤')
    r=data['chart']['result'][0]
    if r['meta']['symbol']!=ticker:raise ValueError('海外基準代碼不符')
    from zoneinfo import ZoneInfo
    zone=ZoneInfo(r['meta']['exchangeTimezoneName']);adj=r['indicators'].get('adjclose',[{}])[0].get('adjclose')
    if ticker=='TWD=X':adj=r['indicators']['quote'][0]['close']
    if not adj:raise ValueError('海外基準缺含息還原價')
    rows={}
    for ts,v in zip(r['timestamp'],adj):
        day=datetime.fromtimestamp(ts,zone).date().isoformat()
        if v and v>0:rows[day]=float(v)
    return rows
def period_dates(text):
    matches=re.findall(r'(?<!\d)(\d{3,4})[/年.\-](\d{1,2})[/月.\-](\d{1,2})(?:日)?(?!\d)|(?<!\d)(\d{7,8})(?!\d)',str(text))
    out=[]
    for y,m,d,compact in matches:
        if compact:
            if len(compact)==7:y,m,d=compact[:3],compact[3:5],compact[5:]
            else:y,m,d=compact[:4],compact[4:6],compact[6:]
        try:out.append(rs.roc(f'{y}/{m}/{d}'))
        except (ValueError,sources.SourceError):return []
    return out

def parse_dispositions(raw,market,url):
    if str(raw.get('stat','')).lower()!='ok':raise ValueError('處置區間來源未就緒')
    rows=[]
    tables=raw.get('tables',[raw])
    if not tables:raise ValueError('缺處置公告表')
    for table in tables:
        fs=table.get('fields',[])
        period_key=next((k for k in ['處置起迄時間','處置起訖時間'] if k in fs),None)
        if not period_key or not all(k in fs for k in ['證券代號','公布日期']):raise ValueError('處置欄位改變')
        data=table.get('data',[]);total=table.get('total',table.get('totalCount'))
        if total is not None and int(total)!=len(data):raise ValueError('處置公告資料遭截斷')
        for cells in data:
            row=dict(zip(fs,cells))
            if not str(row.get('證券代號','')).strip() and any('本日無處置資料' in str(v) for v in cells):continue
            period=str(row[period_key]);dd=period_dates(period)
            if len(dd)!=2 or dd[1]<dd[0]:raise ValueError('處置起訖日無法核對')
            rows.append(dict(symbol=str(row['證券代號']).strip(),market=market,published=rs.roc(row['公布日期']),period=period,
                             start=dd[0],end=dd[1],source_url=url))
    return rows

def collect_dispositions():
    observed=stamp();end=date.fromisoformat(observed[:10]);start=end-timedelta(days=180)
    rows=[];coverage={};warnings=[]
    for market in ['TWSE','TPEX']:
        if market=='TWSE':url='https://www.twse.com.tw/rwd/zh/announcement/punish?'+urlencode(dict(startDate=start.strftime('%Y%m%d'),endDate=end.strftime('%Y%m%d'),response='json',querytype=1))
        else:url='https://www.tpex.org.tw/www/zh-tw/bulletin/disposal?'+urlencode(dict(startDate=start.strftime('%Y/%m/%d'),endDate=end.strftime('%Y/%m/%d'),response='json'))
        try:
            records=parse_dispositions(fetch(url,True),market,url);rows.extend(records);coverage[market]=True
        except (requests.RequestException,ValueError,TypeError,KeyError):
            coverage[market]=False;warnings.append(market+'處置公告無法核對；預設暫不列入排行')
    return dict(dispositions=rows,disposition_coverage=coverage,disposition_observed_at=observed,
                disposition_query_start=start.isoformat(),disposition_query_end=end.isoformat()),warnings

def collect(progress=lambda s:None):
    with db.connect() as c:
        end=c.execute("SELECT MAX(date) FROM market_days WHERE twse_status='ok' AND tpex_status='ok'").fetchone()[0]
        if not end:raise ValueError('請先更新市場行情')
        dates=[r[0] for r in c.execute("SELECT date FROM market_days WHERE date<=? AND twse_status='ok' AND tpex_status='ok' ORDER BY date",(end,))][-260:]
        start=dates[0]
        prices=[dict(r) for r in c.execute('SELECT date,symbol,name,market,close,open,high,low,volume FROM prices WHERE date>=? AND date<=? ORDER BY date,symbol',(start,end))]
    baseline={k:{} for k in ['TAIEX_TR','TPEX_TR']};events=[];amounts={};jobs=[]
    for m,a,b in month_ranges(start,end):
        month=m.strftime('%Y%m%d')
        for kind in baseline:
            url=('https://www.twse.com.tw/indicesReport/MFI94U?'+urlencode(dict(date=month,response='json')) if kind=='TAIEX_TR' else
                 'https://www.tpex.org.tw/www/zh-tw/indexInfo/ROE?'+urlencode(dict(date=m.strftime('%Y/%m/%d'),response='json')))
            jobs.append(('benchmark',kind,month,url))
        for market in ['TWSE','TPEX']:
            url=('https://wwwc.twse.com.tw/rwd/zh/exRight/TWT49U?'+urlencode(dict(startDate=a.strftime('%Y%m%d'),endDate=b.strftime('%Y%m%d'),response='json')) if market=='TWSE' else
                 'https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ?'+urlencode(dict(startDate=a.strftime('%Y/%m/%d'),endDate=b.strftime('%Y/%m/%d'),response='json')))
            jobs.append(('events',market,(a.isoformat(),b.isoformat()),url))
    progress('取得官方含息指數與除權息月資料')
    def run(job):
        typ,key,period,url=job
        # Static historical month caches are reused; the active month is checked.
        active=(period[:6]==end.replace('-','')[:6] if isinstance(period,str) else period[1]==end)
        return job,fetch(url,force=active)
    with ThreadPoolExecutor(max_workers=3) as pool:
        for (typ,key,period,url),raw in pool.map(run,jobs):
            if typ=='benchmark':
                try:baseline[key].update(parse_benchmark(raw,period,key))
                except ValueError:
                    (folder()/'raw'/(hashlib.sha256(url.encode()).hexdigest()+'.json')).unlink(missing_ok=True)
                    raise
            else:
                try:events.extend(parse_cash_events(raw,key,*period))
                except ValueError:
                    (folder()/'raw'/(hashlib.sha256(url.encode()).hexdigest()+'.json')).unlink(missing_ok=True)
                    raise
    # Incomplete common market sessions cannot be filled forward.
    dates=[d for d in dates if d in baseline['TAIEX_TR']]
    if end not in dates or len(dates)<181:raise ValueError('含息基準不足180筆日報酬或缺最新收盤')
    counts=Counter((r['date'],r['market']) for r in prices)
    missing=[(d,m) for d in dates for m in ['TWSE','TPEX'] if counts[d,m]<300]
    progress(f'補獨立歷史行情 {len(missing)} 組；不改寫策略行情')
    seen={(r['date'],r['symbol']) for r in prices}
    for d,m in missing:
        for r in sources.market_prices(d,m):
            if (d,r['symbol']) not in seen:prices.append(r);seen.add((d,r['symbol']))
    progress('核對20日實際成交金額、公司事件與ETF分類')
    # Reuse exact date-validated exchange archives already downloaded by the app.
    for d in dates[-20:]:
        for m in ['TWSE','TPEX']:
            url=('https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?'+urlencode(dict(date=d.replace('-',''),type='ALLBUT0999',response='json')) if m=='TWSE' else
                 'https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?'+urlencode(dict(response='json',date=d.replace('-','/'))))
            raw=json.loads(sources.get(url));amounts[d,m]=parse_amounts(raw,d,m)
    # Exact ex-cash figures are used; capital/face changes and ETF splits remain
    # unscored until individually reconciled instead of creating fake alpha.
    unsafe=[]
    for market,paths in [('TWSE',['reducation/TWTAUU','change/TWTB8U','split/TWTCAU']),('TPEX',['bulletin/revivt','bulletin/pvChgRslt','bulletin/etfSplitRslt'])]:
        for path in paths:
            a=start.replace('-','') if market=='TWSE' else start.replace('-','/')
            b=end.replace('-','') if market=='TWSE' else end.replace('-','/')
            base='https://www.twse.com.tw/rwd/zh/' if market=='TWSE' else 'https://www.tpex.org.tw/www/zh-tw/'
            raw=fetch(base+path+'?'+urlencode(dict(startDate=a,endDate=b,response='json')),True)
            if '沒有符合條件' in raw.get('stat',''):continue
            if str(raw.get('stat','')).lower()!='ok':raise ValueError('公司事件來源未驗證：'+path)
            for table in raw.get('tables',[raw]):
                fs=table.get('fields',[]);sf=next((x for x in ['股票代號','證券代號','ETF代號','代號'] if x in fs),None)
                df=next((x for x in ['恢復買賣日期','恢復交易日期'] if x in fs),None)
                if table.get('data') and (not sf or not df):raise ValueError('公司事件欄位改變：'+path)
                for cells in table.get('data',[]):
                    row=dict(zip(fs,cells));d=rs.roc(row[df])
                    if not start<=d<=end:raise ValueError('公司事件日期超出範圍')
                    unsafe.append(dict(symbol=str(row[sf]).strip(),date=d,kind=path))
    meta=fetch('https://openapi.twse.com.tw/v1/opendata/t187ap47_L',True)
    if not isinstance(meta,list) or len(meta)<100:raise ValueError('ETF官方分類資料不足')
    metadata={str(r['基金代號']).strip():r for r in meta}
    disposition_data,quality=collect_dispositions()
    proxies={}
    from zoneinfo import ZoneInfo
    us_now=datetime.now(ZoneInfo('America/New_York'))
    cutoff=(us_now.date() if us_now.hour>=20 else us_now.date()-timedelta(days=1)).isoformat()
    for ticker in ['SPY','QQQ','DIA','SOXX','TWD=X']:
        succeeded=False
        for host in ['query1','query2']:
            url='https://'+host+'.finance.yahoo.com/v8/finance/chart/'+ticker+'?'+urlencode(dict(range='2y',interval='1d',events='div,splits'))
            try:
                raw=fetch(url,True);parsed=parse_proxy(raw,ticker)
                proxies[ticker]={d:v for d,v in parsed.items() if d<=min(end,cutoff)}
                if not proxies[ticker]:raise ValueError('海外基準無完成日資料')
                succeeded=True;break
            except (requests.RequestException,ValueError,TypeError,KeyError):pass
        if not succeeded:quality.append(ticker+'含息／匯率基準待核對')
    return dict(as_of=end,dates=dates,prices=prices,benchmarks=baseline,events=events,unsafe=unsafe,
                amounts=[dict(date=d,market=m,values=v) for (d,m),v in amounts.items()],metadata=metadata,
                quality=quality,proxies=proxies,fetched_at=stamp(),**disposition_data)
