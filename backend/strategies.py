"""Daily ETF total-return, risk and rolling-allocation plans. No orders."""
from __future__ import annotations
import json,math,re,sqlite3,threading
from datetime import date,datetime,timedelta,time
from urllib.parse import quote
import requests
from bs4 import BeautifulSoup
from . import research,db,market_monitor

VERSION='etf-daily-v4-rolling-risk'
_lock=threading.Lock()
DEFAULTS={'etfs':['0050','006208','0056','00878','00919'],'auto_ytd_count':5,'auto_ytd_min_rs':90,'stock_value':800001,'cash_value':1000000,'allocation_count':3,'fee_bps':14.25,'sell_tax_bps':30,'slippage_bps':5,'borrow_pct':None}
METHOD='價差配對已停用；歷史紀錄只保留在本機資料庫，不再掃描候選、更新行情、建立交易或顯示績效。'
ETF_METHOD='滾動配置先以七項市場因子估計股票目標比例，再以目前股票市值與現金計算本次可布局金額；不是固定日期扣款。候選依一年位階、YTD 含息動能與 RS 排序，優先分配相對低位階且趨勢仍強的 ETF。單日目標比例最多調整 7.5 個百分點；資料覆蓋不足 70% 時不新增配置。Yahoo 還原收盤價僅用於含息報酬、回撤與位階估算，市價不冒充官方淨值。無配息 ETF 可納入；槓桿、反向、ETN 與期貨型產品排除。'

def init(c):
    for sql in [
        'CREATE TABLE IF NOT EXISTS strategy_meta(key TEXT PRIMARY KEY,payload TEXT)',
        'CREATE TABLE IF NOT EXISTS strategy_quotes(symbol TEXT PRIMARY KEY,payload TEXT)',
        'CREATE TABLE IF NOT EXISTS strategy_snapshots(id TEXT PRIMARY KEY,date TEXT,created_at TEXT,payload TEXT)',
        'CREATE TABLE IF NOT EXISTS strategy_trades(id TEXT PRIMARY KEY,report_id TEXT,basis_date TEXT,pair TEXT,created_at TEXT,payload TEXT,result TEXT,UNIQUE(basis_date,pair))',
        'CREATE TABLE IF NOT EXISTS risk_index_bars(date TEXT PRIMARY KEY,open REAL,high REAL,low REAL,close REAL,source TEXT,fetched_at TEXT)',
    ]:c.execute(sql)

def set_meta(c,key,value):c.execute('INSERT OR REPLACE INTO strategy_meta VALUES (?,?)',(key,json.dumps(value,ensure_ascii=False,allow_nan=False)))
def meta(c,key,default):
    r=c.execute('SELECT payload FROM strategy_meta WHERE key=?',(key,)).fetchone();return json.loads(r[0]) if r else default

def initialize():
    with research.store() as c:
        init(c);job=meta(c,'job',{})
        if job.get('status')=='running':set_meta(c,'job',{'status':'interrupted','message':'更新被中斷，舊快照保留；請手動重試'})

def settings():
    with research.store() as c:init(c);return {**DEFAULTS,**meta(c,'settings',{})}

def configure(values):
    allowed=set(DEFAULTS)
    if set(values)-allowed:raise ValueError('不支援的設定欄位')
    cfg={**settings(),**values}
    if not isinstance(cfg['etfs'],list) or not 1<=len(cfg['etfs'])<=12 or any(not isinstance(s,str) or not re.fullmatch(r'00[A-Z0-9]{2,5}',s) for s in cfg['etfs']):raise ValueError('請輸入1–12檔 ETF 代碼')
    cfg['etfs']=list(dict.fromkeys(cfg['etfs']))
    for key,limit in [('auto_ytd_count',7),('auto_ytd_min_rs',100),('stock_value',10000000000),('cash_value',10000000000),('allocation_count',12),('fee_bps',100),('sell_tax_bps',100),('slippage_bps',100),('borrow_pct',100)]:
        v=cfg[key]
        if key=='borrow_pct' and v is None:continue
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=limit:raise ValueError('金額或成本設定超出範圍')
    if not isinstance(cfg['auto_ytd_count'],int) or not isinstance(cfg['allocation_count'],int) or cfg['allocation_count']<1:raise ValueError('候選與配置檔數需為整數')
    with research.store() as c:init(c);set_meta(c,'settings',cfg)
    return cfg

def finite(x,positive=True):return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) and (x>0 if positive else x>=0)
def number(x):return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)

def parse_chart(data,symbol,target):
    try:
        if data['chart'].get('error'):raise ValueError('來源未提供行情')
        d=data['chart']['result'][0];m=d['meta'];q=d['indicators']['quote'][0];adj=d['indicators']['adjclose'][0]['adjclose'];stamps=d['timestamp']
        if m['symbol']!=symbol or m.get('currency')!='TWD' or m.get('exchangeTimezoneName')!='Asia/Taipei':raise ValueError('行情代碼、幣別或時區不符')
        if len(stamps)!=len(adj) or len(stamps)!=len(q['close']):raise ValueError('行情長度不符')
        rows=[];gaps=[]
        for i,t in enumerate(stamps):
            day=datetime.fromtimestamp(t,research.TZ).date().isoformat()
            if day>target:continue
            op=q.get('open',[])[i];hi=q.get('high',[])[i];lo=q.get('low',[])[i];cl=q['close'][i];a=adj[i];vol=q.get('volume',[])[i]
            if not all(finite(v) for v in [op,hi,lo,cl,a]) or not finite(vol,False) or not lo<=min(op,cl)<=max(op,cl)<=hi:gaps.append(day);continue
            factor=a/cl
            rows.append({'date':day,'open':op,'high':hi,'low':lo,'close':cl,'volume':vol,'adj':a,'adj_open':op*factor,'adj_high':hi*factor,'adj_low':lo*factor})
        if len(rows)<2 or len({r['date'] for r in rows})!=len(rows):raise ValueError('行情筆數不足或日期重複')
        events=d.get('events',{})
        dividends=[]
        for e in events.get('dividends',{}).values():
            day=datetime.fromtimestamp(e['date'],research.TZ).date().isoformat()
            if day<=target:
                if not finite(e['amount'],False):raise ValueError('配息金額無效')
                dividends.append({'date':day,'amount':e['amount']})
        splits=[]
        for e in events.get('splits',{}).values():
            day=datetime.fromtimestamp(e['date'],research.TZ).date().isoformat()
            if day<=target:splits.append(day)
        return {'symbol':symbol,'rows':sorted(rows,key=lambda x:x['date']),'dividends':sorted(dividends,key=lambda x:x['date']),'splits':splits,'gaps':gaps,'fetched_at':research.now(),'source':'https://finance.yahoo.com/quote/'+quote(symbol)+'/history/','basis':'供應商還原價格估算；含息再投入，未逐筆官方對帳'}
    except (KeyError,IndexError,TypeError,OverflowError):raise ValueError('行情欄位缺少或格式異常') from None

def fetch_chart(symbol,market,target):
    ticker=symbol+('.TWO' if market=='TPEX' else '.TW')
    end=datetime.combine(date.fromisoformat(target)+timedelta(days=1),time(),research.TZ)
    start=end-timedelta(days=760)
    try:
        r=requests.get('https://query1.finance.yahoo.com/v8/finance/chart/'+quote(ticker),params={'interval':'1d','period1':int(start.timestamp()),'period2':int(end.timestamp()),'events':'div,splits'},headers={'User-Agent':'branch-monitor/1.0'},timeout=(8,25),allow_redirects=False)
        if r.status_code!=200:raise ValueError(f'行情來源 HTTP {r.status_code}，保留快取')
        return parse_chart(r.json(),ticker,target)
    except requests.RequestException:raise ValueError('行情來源連線失敗，保留快取') from None

def repair_official_gaps(data,symbol,target):
    """Fill invalid vendor sessions only when an exact official exchange row is saved locally."""
    gaps=sorted({d for d in data.get('gaps',[]) if d<=target})
    if not gaps:return data
    placeholders=','.join('?' for _ in gaps)
    try:
        with research.main_db() as c:
            official=[dict(r) for r in c.execute(
                f'SELECT date,open,high,low,close,volume FROM prices WHERE symbol=? AND date IN ({placeholders})',
                (symbol,*gaps))]
    except sqlite3.Error:return data
    rows=list(data.get('rows',[]));repaired=set();event_days={e['date'] for e in data.get('dividends',[])}|set(data.get('splits',[]))
    for item in official:
        stamp=item['date'];values=[item.get(k) for k in ('open','high','low','close')];volume=item.get('volume')
        if not all(finite(v) for v in values) or not finite(volume,False) or not item['low']<=min(item['open'],item['close'])<=max(item['open'],item['close'])<=item['high']:continue
        before=max((r for r in rows if r['date']<stamp),key=lambda r:r['date'],default=None)
        after=min((r for r in rows if r['date']>stamp),key=lambda r:r['date'],default=None)
        anchor=after if stamp in event_days else before or after
        if not anchor or not finite(anchor.get('close')) or not finite(anchor.get('adj')):continue
        factor=anchor['adj']/anchor['close']
        rows.append({'date':stamp,'open':item['open'],'high':item['high'],'low':item['low'],'close':item['close'],'volume':volume,
                     'adj':item['close']*factor,'adj_open':item['open']*factor,'adj_high':item['high']*factor,'adj_low':item['low']*factor})
        repaired.add(stamp)
    return {**data,'rows':sorted(rows,key=lambda r:r['date']),'gaps':[d for d in data.get('gaps',[]) if d not in repaired],
            'basis':data.get('basis','')+('；供應商異常日以本機已保存的證交所同日開高低收補齊' if repaired else '')}

def parse_nav_page(text,symbol,target):
    match=re.search(r'let\s+chartData\s*=\s*(\{.*?\});',text,re.S)
    if not match:raise ValueError('官方淨值頁未提供圖表資料')
    try:data=json.loads(match.group(1))
    except (TypeError,ValueError):raise ValueError('官方淨值格式異常') from None
    target_day=date.fromisoformat(target);rows=[]
    premium={str(r.get('date')):r.get('count') for r in data.get('atmps',[])}
    for row in data.get('netPrice',[]):
        stamp=str(row.get('date',''))
        try:
            month,day=map(int,stamp.split('/'));candidate=date(target_day.year,month,day)
            if candidate>target_day:candidate=date(target_day.year-1,month,day)
            if finite(row.get('count')):rows.append((candidate,row['count'],premium.get(stamp)))
        except (TypeError,ValueError):continue
    rows=[r for r in rows if r[0]<=target_day]
    if not rows:raise ValueError('官方淨值頁沒有研究日前資料')
    day,nav,prem=max(rows,key=lambda r:r[0])
    return {'nav':nav,'nav_as_of':day.isoformat(),'premium_pct':prem if number(prem) else None,'nav_source':'https://www.twse.com.tw/zh/ETFortune/etfInfo/'+symbol,'nav_note':'證交所 e添富盤後淨值與折溢價'}

def fetch_nav(symbol,target):
    try:
        r=requests.get('https://www.twse.com.tw/zh/ETFortune/etfInfo/'+quote(symbol),headers={'User-Agent':'branch-monitor/1.0'},timeout=(8,20),allow_redirects=False)
        if r.status_code!=200:raise ValueError(f'官方淨值 HTTP {r.status_code}')
        return parse_nav_page(r.text,symbol,target)
    except requests.RequestException:raise ValueError('官方淨值來源連線失敗') from None

def _index_day(value):
    parts=[int(x) for x in re.findall(r'\d+',str(value))[:3]]
    if len(parts)!=3:raise ValueError('加權指數日期格式異常')
    year,month,day=parts
    if year<1911:year+=1911
    return date(year,month,day).isoformat()

def _index_number(value):
    try:return float(str(value).replace(',','').strip())
    except (TypeError,ValueError):raise ValueError('加權指數價格格式異常') from None

def parse_index_ohlc(raw,target):
    """Parse the official TWSE monthly index response (JSON or HTML)."""
    fields=[];data=[]
    if isinstance(raw,dict):
        if str(raw.get('stat','OK')).upper() not in ('OK',''):raise ValueError('證交所未提供加權指數月資料')
        fields=raw.get('fields') or []
        data=raw.get('data') or []
    elif isinstance(raw,str):
        text=raw.strip()
        if text.startswith('{'):
            try:return parse_index_ohlc(json.loads(text),target)
            except json.JSONDecodeError:raise ValueError('證交所加權指數格式異常') from None
        soup=BeautifulSoup(text,'html.parser')
        table=next((table for table in soup.select('table') if '開盤指數' in table.get_text()),None)
        if not table:raise ValueError('證交所頁面未找到加權指數日 K')
        fields=[cell.get_text(' ',strip=True) for cell in table.select('thead th')]
        if not fields:
            first=table.select_one('tr');fields=[cell.get_text(' ',strip=True) for cell in first.select('th,td')] if first else []
        for tr in table.select('tbody tr') or table.select('tr')[1:]:
            cells=[cell.get_text(' ',strip=True) for cell in tr.select('td')]
            if cells:data.append(cells)
    else:raise ValueError('證交所加權指數格式異常')
    wanted={'date':'日期','open':'開盤指數','high':'最高指數','low':'最低指數','close':'收盤指數'}
    normalized=[re.sub(r'\s+','',str(x)) for x in fields]
    try:index={key:normalized.index(label) for key,label in wanted.items()}
    except ValueError:raise ValueError('證交所加權指數欄位缺少') from None
    rows=[]
    for cells in data:
        try:
            day=_index_day(cells[index['date']])
            values={key:_index_number(cells[position]) for key,position in index.items() if key!='date'}
            if day>target or not all(finite(v) for v in values.values()):continue
            if not values['low']<=min(values['open'],values['close'])<=max(values['open'],values['close'])<=values['high']:continue
            rows.append({'date':day,**values})
        except (IndexError,ValueError):continue
    rows=sorted({row['date']:row for row in rows}.values(),key=lambda row:row['date'])
    if not rows:raise ValueError('證交所加權指數月資料沒有有效日 K')
    return rows

def _month_starts(target,count):
    cursor=date.fromisoformat(target).replace(day=1);months=[]
    for _ in range(count):
        months.append(cursor)
        cursor=(cursor-timedelta(days=1)).replace(day=1)
    return list(reversed(months))

def refresh_index_bars(target):
    """Cache official TAIEX OHLC. Initial run fills four months; daily runs refresh one month."""
    with research.store() as c:
        init(c);existing=c.execute('SELECT count(*) FROM risk_index_bars').fetchone()[0]
    months=_month_starts(target,4 if existing<40 else 1);saved=0
    for month in months:
        try:
            response=requests.get('https://www.twse.com.tw/indicesReport/MI_5MINS_HIST',params={'date':month.strftime('%Y%m%d'),'response':'json'},headers={'User-Agent':'branch-monitor/1.0'},timeout=(8,25),allow_redirects=False)
            if response.status_code!=200:raise ValueError(f'證交所加權指數 HTTP {response.status_code}')
            try:raw=response.json()
            except requests.exceptions.JSONDecodeError:raw=response.text
            rows=parse_index_ohlc(raw,target)
            with research.store() as c:
                init(c)
                for row in rows:
                    c.execute('INSERT OR REPLACE INTO risk_index_bars VALUES (?,?,?,?,?,?,?)',(row['date'],row['open'],row['high'],row['low'],row['close'],'https://www.twse.com.tw/indicesReport/MI_5MINS_HIST',research.now()))
            saved+=len(rows)
        except requests.RequestException:raise ValueError('證交所加權指數連線失敗，沿用已保存日 K') from None
    return saved

def catalog_snapshot(target):
    with research.main_db() as c:
        r=c.execute('SELECT date,payload FROM etf_rs_days WHERE date<=? ORDER BY date DESC LIMIT 1',(target,)).fetchone()
        rows=json.loads(r['payload']).get('groups',{}).get('ytd',[]) if r else []
    return (r['date'] if r else None),{row['symbol']:row for row in rows}

def catalog(target):return catalog_snapshot(target)[1]

def normal_etf(row):
    kind=row.get('product_type','');return 'ETF' in kind and not any(x in kind+row.get('name','') for x in ['槓桿','反向','正2','反1','ETN'])

def auto_ytd_candidates(universe,cfg):
    """Pick strong plain-equity ETFs without using dividend history as a filter."""
    blocked=['期元大','期街口','期富邦','期國泰','期中信','原油','布蘭特','黃金','白銀','美元','日圓']
    eligible=[]
    for row in universe.values():
        value=row.get('ytd_pct',row.get('return_pct'));strength=row.get('rs')
        if not normal_etf(row) or any(word in row.get('name','') for word in blocked):continue
        if not finite(value,False) or not finite(strength,False) or value<=0 or strength<cfg['auto_ytd_min_rs']:continue
        eligible.append(row)
    eligible.sort(key=lambda row:(-row['rs'],-row.get('ytd_pct',row.get('return_pct',0)),row['symbol']))
    slots=max(0,min(cfg['auto_ytd_count'],12-len(cfg['etfs'])))
    return [row for row in eligible if row['symbol'] not in cfg['etfs']][:slots]

def clamp(value,low=0,high=100):return max(low,min(high,value))

def refresh_market_risk_inputs(max_age_minutes=60):
    """Refresh live risk inputs when their last successful fetch is no longer recent."""
    cutoff=datetime.now(research.TZ)-timedelta(minutes=max_age_minutes)
    refreshed=[]
    with research.main_db() as c:
        saved={r['key']:dict(r) for r in c.execute("SELECT key,payload,saved_at FROM market_observations WHERE key IN ('fear','twd','volume')")}
    for key in ('fear','twd','volume'):
        stamp=saved.get(key,{}).get('saved_at')
        try:fresh=bool(stamp) and datetime.fromisoformat(stamp)>=cutoff
        except (TypeError,ValueError):fresh=False
        if key=='volume' and fresh:
            try:fresh=number(json.loads(saved[key]['payload']).get('ratio')) is not None
            except (KeyError,TypeError,ValueError,json.JSONDecodeError):fresh=False
        if not fresh:
            market_monitor.update_one(key);refreshed.append(key)
    return refreshed

def _observation(target,use_latest=False):
    """Return only saved observations whose own data date is not after target."""
    out={}
    with research.main_db() as c:
        for r in c.execute('SELECT key,payload FROM market_observations WHERE payload IS NOT NULL'):
            try:
                payload=json.loads(r['payload']);as_of=str(payload.get('as_of',''))[:10]
                if r['key']=='volume' and as_of>target:
                    rows=[x for x in payload.get('rows',[]) if x.get('date','')<=target]
                    if rows:
                        latest=rows[-1];base=rows[-21:-1];average=sum(x['amount_100m'] for x in base)/len(base) if base else None
                        payload={**payload,'as_of':latest['date'],'value':latest['amount_100m'],'amount':latest['amount'],'average_20':average,'ratio':latest['amount_100m']/average if average else None};as_of=latest['date']
                if as_of and (as_of<=target or use_latest and r['key'] in ('fear','twd')):out[r['key']]=payload
            except (TypeError,ValueError):pass
    return out

def risk_dashboard(target,cfg,previous=None,use_latest=False):
    weights={'散戶情緒':12,'外資買賣力道':18,'大盤位階':18,'大盤量能':10,'恐懼貪婪':12,'台幣走勢':10,'美國利率':12}
    factors=[];obs=_observation(target,use_latest)
    def add(name,score,as_of,detail):
        factors.append({'name':name,'weight':weights[name],'score':None if score is None else round(clamp(score),1),'as_of':as_of,'detail':detail,'available':score is not None})
    retail=obs.get('retail')
    if retail and number(retail.get('value')):add('散戶情緒',50-retail['value']*2,retail.get('as_of'),f"小台散戶多空比 {retail['value']:+.2f}%；反向計分")
    else:add('散戶情緒',None,None,'等待小台散戶多空比')
    foreign=[];dates=[]
    for key in ('twse','tpex'):
        item=obs.get(key)
        if item:
            row=next((x for x in item.get('rows',[]) if x.get('name')=='外資'),None)
            if row and number(row.get('net')):foreign.append(row['net']);dates.append(item.get('as_of'))
    if foreign:
        total=sum(foreign);add('外資買賣力道',50+50*math.tanh(total/50000000000),max(dates),f"上市櫃外資合計 {total/100000000:.1f} 億元")
    else:add('外資買賣力道',None,None,'等待上市櫃外資資料')
    with research.main_db() as c:
        prices=[dict(r) for r in c.execute("SELECT date,close,volume FROM prices WHERE symbol='TAIEX' AND date<=? AND close>0 ORDER BY date DESC LIMIT 252",(target,))]
    prices.reverse();market_score=None;market_detail='至少需要20個大盤收盤日';volatility=None;drawdown=None
    if len(prices)>=20:
        closes=[r['close'] for r in prices];last=closes[-1];lo=min(closes);hi=max(closes);position=(last-lo)/(hi-lo)*100 if hi>lo else 50
        checks=[]
        for n in (20,60,120):
            if len(closes)>=n:checks.append(last>=sum(closes[-n:])/n)
        trend=sum(checks)/len(checks)*100 if checks else 50
        market_score=.45*position+.55*trend;market_detail=f"近{len(closes)}日位階 {position:.1f}%，站上 {sum(checks)}/{len(checks)} 條均線"
        returns=[closes[i]/closes[i-1]-1 for i in range(1,len(closes))]
        if len(returns)>=20:
            mean=sum(returns[-20:])/20;volatility=math.sqrt(sum((x-mean)**2 for x in returns[-20:])/19)*math.sqrt(252)*100
        drawdown=(last/max(closes)-1)*100
    add('大盤位階',market_score,prices[-1]['date'] if prices else None,market_detail)
    volume=obs.get('volume')
    if volume and number(volume.get('ratio')):
        add('大盤量能',50+50*math.tanh((volume['ratio']-1)*2),volume.get('as_of'),f"上市成交金額 {volume['value']:.0f} 億元，為近期均值 {volume['ratio']:.2f} 倍")
    else:add('大盤量能',None,None,'等待證交所每日成交金額')
    fear=obs.get('fear')
    if fear and finite(fear.get('value'),False):
        value=fear['value'];score=100-value if market_score is not None and market_score>=50 else value
        add('恐懼貪婪',score,str(fear.get('as_of',''))[:10],f"指標 {value:.1f} · {'逆向低接' if market_score is not None and market_score>=50 else '順勢保守'}計分")
    else:add('恐懼貪婪',None,None,'等待恐懼貪婪資料')
    twd=obs.get('twd')
    if twd and number(twd.get('change_pct')):
        add('台幣走勢',50-twd['change_pct']*8,str(twd.get('as_of',''))[:10],f"美元兌台幣日變動 {twd['change_pct']:+.2f}%（下降代表台幣轉強）")
    else:add('台幣走勢',None,None,'等待美元兌台幣資料')
    rates=obs.get('rates')
    if rates and finite(rates.get('low'),False) and finite(rates.get('high'),False):
        midpoint=(rates['low']+rates['high'])/2;add('美國利率',80-midpoint*8,rates.get('as_of'),f"目標區間 {rates['low']:.2f}–{rates['high']:.2f}%；只反映利率水準")
    else:add('美國利率',None,None,'等待聯邦基金利率資料')
    available=sum(x['weight'] for x in factors if x['available']);coverage=available/sum(weights.values())*100
    score=sum(x['score']*x['weight'] for x in factors if x['available'])/available if available else 50
    base=20+.6*score;vol_mult=clamp(18/(volatility or 18),.6,1.1);dd_mult=.65 if drawdown is not None and drawdown<=-20 else .85 if drawdown is not None and drawdown<=-10 else 1
    raw=clamp(base*vol_mult*dd_mult,20,80)
    actionable=coverage>=70
    if not actionable:target_pct=previous if finite(previous,False) else 50
    elif finite(previous,False):target_pct=clamp(raw,previous-7.5,previous+7.5)
    else:target_pct=raw
    total=cfg['stock_value']+cfg['cash_value'];target_stock=total*target_pct/100
    deployable=max(0,min(cfg['cash_value'],target_stock-cfg['stock_value'])) if actionable else 0
    trim_amount=max(0,cfg['stock_value']-target_stock) if actionable else 0
    reserve=cfg['cash_value']-deployable
    status='可分批布局' if deployable>0 else '建議分批調節高檔部位' if trim_amount>0 else '維持現況／等待低檔' if actionable else '資料不足，暫停新增配置'
    return {'score':round(score,1),'coverage':round(coverage,1),'actionable':actionable,'target_equity_pct':round(target_pct,1),'raw_target_pct':round(raw,1),'previous_target_pct':previous,'current_equity_pct':round(cfg['stock_value']/total*100,1) if total else 0,'stock_value':cfg['stock_value'],'cash_value':cfg['cash_value'],'total_assets':total,'target_stock_value':round(target_stock),'deployable':round(deployable),'trim_amount':round(trim_amount),'reserve_cash':round(reserve),'post_trim_cash':round(cfg['cash_value']+trim_amount),'volatility':None if volatility is None else round(volatility,1),'drawdown':None if drawdown is None else round(drawdown,1),'factors':factors,'status':status}

def rolling_plan(etfs,risk,count):
    eligible=[e for e in etfs if not e.get('error') and finite(e.get('position_pct'),False)]
    for e in eligible:
        rs=e.get('rs_ytd') if finite(e.get('rs_ytd'),False) else 50
        ytd=e.get('ytd_total') if number(e.get('ytd_total')) else 0
        e['opportunity_score']=round(.65*(100-e['position_pct'])+.2*rs+.15*clamp(50+ytd),1)
        e.update({'recommended':False,'allocation_weight':0,'recommended_amount':0,'estimated_price':0,'estimated_units':0,'estimated_lots':0,'estimated_odd_lots':0,'adjustment_action':'續抱觀察','suggested_trim_amount':0,'estimated_trim_units':0,'rotation_preview_amount':0,'rotation_units':0})
    chosen=sorted(eligible,key=lambda e:(-e['opportunity_score'],e['position_pct'],e['symbol']))[:max(1,min(count,len(eligible)))]
    total_score=sum(max(1,e['opportunity_score']) for e in chosen)
    amount=risk['deployable'] if risk['actionable'] else 0
    for e in chosen:
        weight=max(1,e['opportunity_score'])/total_score if total_score else 1/len(chosen)
        allocated=amount*weight;near_pressure=str(e.get('swing_action','')).startswith('接近壓力');price=(e.get('support_1') if near_pressure else e.get('market_price')) or 0;units=math.floor(allocated/price) if price else 0
        action='等待回檔至支撐 1' if near_pressure else '支撐附近分批承接' if str(e.get('swing_action','')).startswith('接近支撐') else '分批承接'
        e.update({'recommended':True,'allocation_weight':round(weight*100,1),'recommended_amount':round(allocated),'estimated_price':price,'estimated_units':units,'estimated_lots':units//1000,'estimated_odd_lots':units%1000,'adjustment_action':action if amount else '下一檔接棒候選'})
    used=sum(e.get('estimated_units',0)*(e.get('estimated_price') or 0) for e in chosen)
    trim_amount=risk.get('trim_amount',0) if risk.get('actionable') else 0
    swing_sources=sorted([e for e in eligible if number(e.get('ytd_total')) and e['ytd_total']>0 and e['position_pct']>=65 and e.get('distance_resistance_pct',999)<=3],key=lambda e:(e.get('distance_resistance_pct',999),-e['position_pct'],-e['ytd_total'],e['symbol']))[:max(1,min(count,len(eligible)))]
    trim_candidates=swing_sources if trim_amount>0 else []
    trim_total=sum(max(1,e['position_pct']-55) for e in trim_candidates)
    for e in trim_candidates:
        allocated=trim_amount*max(1,e['position_pct']-55)/trim_total if trim_total else 0;price=e.get('market_price') or 0;units=math.floor(allocated/price) if price else 0
        e.update({'adjustment_action':'接近壓力，分批調節','suggested_trim_amount':round(allocated),'estimated_trim_units':units})
    rotation_targets=[e for e in chosen if e not in swing_sources] or chosen
    rotation_total=sum(max(1,e['opportunity_score']) for e in rotation_targets)
    rotation_budget=trim_amount or amount
    for e in rotation_targets:
        preview=rotation_budget*max(1,e['opportunity_score'])/rotation_total if rotation_total else 0;price=(e.get('support_1') or e.get('market_price') or 0);units=math.floor(preview/price) if price else 0
        e.update({'rotation_preview_amount':round(preview),'rotation_units':units})
    rotations=[]
    for i,source in enumerate(swing_sources):
        if not rotation_targets:break
        target=rotation_targets[i%len(rotation_targets)]
        preview=source['suggested_trim_amount'] if trim_amount else rotation_budget/max(1,len(swing_sources));from_units=source['estimated_trim_units'] if trim_amount else math.floor(preview/(source.get('market_price') or 1));to_units=math.floor(preview/(target.get('support_1') or target.get('market_price') or 1))
        rotations.append({'from_symbol':source['symbol'],'from_name':source['name'],'to_symbol':target['symbol'],'to_name':target['name'],'preview_amount':round(preview),'from_units':from_units,'to_units':to_units,'trigger':f"{source['symbol']} 接近壓力 {source.get('resistance_1',0):.2f}，且 {target['symbol']} 回到支撐 {target.get('support_1',0):.2f} 附近"})
    return {'count':len(chosen),'symbols':[e['symbol'] for e in chosen],'deployable':amount,'estimated_used':round(used),'leftover':round(max(0,amount-used)),'trim_amount':round(trim_amount),'post_trim_cash':risk.get('post_trim_cash',risk.get('cash_value',0)),'adjustments':[e['symbol'] for e in swing_sources],'rotations':rotations,'method':'依低位階 65%、YTD RS 20%、YTD 含息動能 15% 排序；接近波段壓力且已有正報酬者列為調節候選，接棒標的等待回到支撐附近。估計單位以預計承接價計算；調節額應先滿足風控保留現金，全額輪動只作預覽。'}

def disabled_pair_snapshot():
    return {'rows':[],'tested':0,'missing':0,'method':METHOD,'limitations':'價差配對已停用，後續研究改用每日鉅額交易追蹤。'}

def etf_metrics(data,target,cfg,benchmark=None,plan_count=None):
    import numpy as np
    rows=[r for r in data['rows'] if r['date']<=target]
    if len(rows)<21:raise ValueError('至少需要21個有效交易日')
    if any(target[:4]+'-01-01'<=d<=rows[-1]['date'] for d in data.get('gaps',[])):raise ValueError('期間有缺漏行情，暫不計算含息績效')
    # Use a common YTD window, retaining pre-year anchor only for YTD calculation.
    prior=[r for r in rows if r['date']<target[:4]+'-01-01'];current=[r for r in rows if r['date']>=target[:4]+'-01-01']
    if not current:raise ValueError('缺少今年行情')
    period=([prior[-1]] if prior else [])+current
    values=np.array([r['adj'] for r in period]);drawdown=float(min(values/np.maximum.accumulate(values)-1)*100)
    returns=np.diff(values)/values[:-1]
    expected={r['date'] for r in (benchmark or {}).get('rows',[]) if current[0]['date']<=r['date']<=current[-1]['date']}
    if expected-set(r['date'] for r in current):raise ValueError('期間交易日不連續，暫停位階與含息計算')
    curve=[{'date':r['date'],'value':r['adj']/period[0]['adj']*100} for r in current]
    ytd=(values[-1]/values[0]-1)*100 if prior and prior[-1]['date']>=str(int(target[:4])-1)+'-12-20' else None
    br={r['date']:r['adj'] for r in (benchmark or {}).get('rows',[])}
    excess=ytd-((br[period[-1]['date']]/br[period[0]['date']]-1)*100) if ytd is not None and period[0]['date'] in br and period[-1]['date'] in br else None
    window=rows[-min(252,len(rows)):];market_price=rows[-1]['close'];low=min(r['close'] for r in window);high=max(r['close'] for r in window);position=(market_price-low)/(high-low)*100 if high>low else 50
    swing=rows[-min(60,len(rows)):];short=swing[-min(20,len(swing)):]
    support_1=min(r.get('low') or r['close'] for r in short);support_2=min(r.get('low') or r['close'] for r in swing)
    resistance_1=max(r.get('high') or r['close'] for r in short);resistance_2=max(r.get('high') or r['close'] for r in swing)
    distance_support=(market_price/support_1-1)*100 if support_1 else None;distance_resistance=(resistance_1/market_price-1)*100 if market_price else None
    position_label='低檔' if position<=20 else '偏低' if position<=40 else '中位' if position<=60 else '偏高' if position<=80 else '高檔'
    swing_action='接近壓力，可分批調節' if ytd is not None and ytd>0 and position>=65 and distance_resistance is not None and distance_resistance<=3 else '接近支撐，可分批承接' if position<=40 and distance_support is not None and distance_support<=6 else '區間內續抱觀察'
    return {'as_of':rows[-1]['date'],'start':period[0]['date'],'sample':len(current),'ytd_total':ytd,'price_return':(period[-1]['close']/period[0]['close']-1)*100 if prior else None,'excess_0050':excess,'drawdown':drawdown,'volatility':float(np.std(returns,ddof=1)*np.sqrt(252)*100) if len(returns)>1 else None,'position_pct':position,'position_label':position_label,'market_price':market_price,'support_1':support_1,'support_2':support_2,'resistance_1':resistance_1,'resistance_2':resistance_2,'distance_support_pct':distance_support,'distance_resistance_pct':distance_resistance,'swing_action':swing_action,'nav':None,'nav_as_of':None,'premium_pct':None,'nav_note':'官方當日淨值尚未取得；市價不代替淨值','dividends':[e for e in data['dividends'] if e['date']>=target[:4]+'-01-01'],'curve':curve,'source':data['source'],'stale':rows[-1]['date']!=target,'limitations':'含息績效與位階使用供應商還原價格；波段支撐壓力為近20／60個有效交易日高低區間，不保證價格一定反轉。當日官方淨值缺值時保留空白，不以市價代替。管理費已反映於基金價格，未再次扣除；持股重疊與完整費用率待補。'}

def allocation_evidence(target):
    """Read saved V6 exposure only; never resurrect the retired ETF generator."""
    from . import v6_evening_forecast as evening
    rows=[e['payload'] for e in evening.read() if e['kind']=='forecast' and e['payload']['anchorDate']<=target and e['payload'].get('allocation')]
    if not rows:return {'date':None,'risk':None,'warnings':['V6配置尚未隨新機率保存；舊ETF規劃已停止更新。']}
    f=rows[-1];a=f['allocation']
    return {'date':f['anchorDate'],'snapshot_id':f['id'],'created_at':f['savedAt'],'risk':{'target_equity_pct':a['equityPct'],'status':a['action']},'warnings':[a['note']]}

def allocation_appendix(evidence):
    s=evidence.get('strategies',{});risk=s.get('risk') or {}
    return '\n\n## V6股票曝險上限\n'+(f"建議股票上限 {risk['target_equity_pct']}%，{risk['status']}。詳見V6，非實際持倉。\n" if risk else '尚無隨新機率保存的V6配置；舊ETF規劃已停止更新。\n')


def risk_history():
    """Align saved allocation decisions with actual TAIEX candles and rolling trend."""
    with research.store() as c:
        init(c);saved=c.execute('SELECT id,date,created_at,payload FROM strategy_snapshots ORDER BY date,created_at,id').fetchall()
        bars=[dict(r) for r in c.execute('SELECT date,open,high,low,close FROM risk_index_bars ORDER BY date')]
    by_date={}
    for row in saved:
        try:payload=json.loads(row['payload']);risk=payload.get('risk') or {};target=risk.get('target_equity_pct')
        except (TypeError,ValueError,json.JSONDecodeError):continue
        if finite(target,False):by_date[row['date']]={'id':row['id'],'date':row['date'],'created_at':row['created_at'],'target':float(target),'risk':risk}
    if not by_date:return {'bars':[],'decisions':[],'summary':{'message':'尚無已保存的風控配置快照'}}
    decisions=[by_date[d] for d in sorted(by_date)]
    if not bars:return {'bars':[],'decisions':decisions,'summary':{'message':'缺少加權指數日 K，請按手動更新策略補入證交所資料'}}
    price_dates=[r['date'] for r in bars];price_by_date={r['date']:r['close'] for r in bars}
    latest_risk=decisions[-1]['risk'];previous=None
    for item in decisions:
        created=datetime.fromisoformat(item['created_at']);created=(created if created.tzinfo else created.replace(tzinfo=research.TZ)).astimezone(research.TZ)
        eligible=lambda d:d>=created.date().isoformat() if created.time()<time(8,45) else d>created.date().isoformat()
        item['effective_date']=next((d for d in price_dates if eligible(d)),None)
        item['index_close']=price_by_date.get(item['date'])
        item['change_pp']=None if previous is None else round(item['target']-previous,1)
        item['action']='建立基準' if previous is None else '目標上調' if item['change_pp']>.05 else '目標下修' if item['change_pp']<-.05 else '維持'
        previous=item['target']
        position=price_dates.index(item['date']) if item['date'] in price_by_date else None
        if position is not None and position>=19:
            window=bars[position-19:position+1];item['ma20']=round(sum(r['close'] for r in window)/20,2)
            item['trend_20_pct']=round((bars[position]['close']/bars[position-19]['close']-1)*100,3)
            item['above_ma20']=bars[position]['close']>=item['ma20']
        else:item['ma20']=None;item['trend_20_pct']=None;item['above_ma20']=None
        item.pop('risk',None)
    chart=[]
    for index,row in enumerate(bars):
        ma20=sum(r['close'] for r in bars[index-19:index+1])/20 if index>=19 else None
        decision=by_date.get(row['date'])
        chart.append({**row,'ma20':None if ma20 is None else round(ma20,2),'target_equity_pct':None if not decision else decision['target'],'change_pp':None if not decision else next((d['change_pp'] for d in decisions if d['date']==row['date']),None)})
    pairs=[d for d in decisions if d.get('trend_20_pct') is not None]
    correlation=None
    if len(pairs)>=5:
        xs=[d['trend_20_pct'] for d in pairs];ys=[d['target'] for d in pairs];xm=sum(xs)/len(xs);ym=sum(ys)/len(ys)
        numerator=sum((x-xm)*(y-ym) for x,y in zip(xs,ys));denominator=math.sqrt(sum((x-xm)**2 for x in xs)*sum((y-ym)**2 for y in ys))
        correlation=round(numerator/denominator,3) if denominator else None
    rising_cuts=[d for d in decisions if d.get('change_pp') is not None and d['change_pp']<-.05 and d.get('trend_20_pct') is not None and d['trend_20_pct']>0 and d.get('above_ma20')]
    latest=decisions[-1];risk=latest_risk;current=risk.get('current_equity_pct');target=latest['target'];delta=latest.get('change_pp')
    if finite(current,False) and current<target:
        change='已建立基準' if delta is None else f"較前次{abs(delta):.1f}個百分點{'下降' if delta<0 else '上升'}"
        guidance=f'目標配置{change}，但目前股票比例 {current:.1f}% 仍低於目標 {target:.1f}%，屬於分批增持至目標，不是直接減碼。'
    elif finite(current,False) and current>target:guidance=f'目前股票比例 {current:.1f}% 高於目標 {target:.1f}%，風控方向為分批降低曝險。'
    else:guidance=f'目前股票比例接近目標 {target:.1f}%，先維持並等待下一次風控更新。'
    if len(pairs)<5:message=f'樣本不足：目前 {len(decisions)} 個配置研究日，其中 {len(pairs)} 日已有20日趨勢；至少累積5日才能顯示初步相關性。'
    elif correlation is None:message='配置比例或大盤趨勢變化不足，暫時無法計算相關性。'
    elif correlation>=.3:message='配置比例與大盤20日趨勢目前呈正向關係；仍需持續滾動驗證。'
    elif correlation<=-.3:message='配置比例與大盤20日趨勢目前呈反向關係，需留意上漲趨勢中持續降持股的機會成本。'
    else:message='配置比例與大盤20日趨勢目前關係偏弱，繼續累積每日風控快照。'
    latest_trend=next((d.get('trend_20_pct') for d in reversed(decisions) if d.get('trend_20_pct') is not None),None)
    shown=chart[-40:]
    return {'bars':shown,'decisions':decisions,'summary':{'start':shown[0]['date'] if shown else None,'end':shown[-1]['date'] if shown else None,'sessions':len(shown),'decision_dates':len(decisions),'paired_points':len(pairs),'trend_allocation_correlation':correlation,'latest_trend_20_pct':latest_trend,'rising_market_cuts':len(rising_cuts),'message':message,'guidance':guidance,'method':'上圖使用證交所發行量加權股價指數每日開高低收與20日均線；下圖只在實際保存風控快照的交易日畫出建議股票比例。相關性以各快照日的20日指數漲跌幅與建議比例計算，至少5個配對樣本才顯示初值。'}}

def report(ident=None):
    with research.store() as c:
        init(c);history=[dict(r) for r in c.execute('SELECT id,date,created_at FROM strategy_snapshots ORDER BY date DESC,created_at DESC,id DESC LIMIT 100')]
        r=c.execute('SELECT payload FROM strategy_snapshots WHERE id=?',(ident or (history[0]['id'] if history else ''),)).fetchone()
        return {'snapshot':ensure_daily_snapshot(json.loads(r[0])) if r else None,'history':history,'trades':[],'summary':{'closed':0,'win_rate':None,'net_pnl':0},'risk_history':risk_history(),'job':meta(c,'job',{'status':'idle','message':'尚未更新；請手動建立快照'}),'settings':{**DEFAULTS,**meta(c,'settings',{})},'method':METHOD,'etf_method':ETF_METHOD}

def daily_analysis(target,risk,plan,etfs,created_at):
    selected=[]
    for row in etfs:
        if not row.get('recommended') and not row.get('suggested_trim_amount'):continue
        selected.append({'symbol':row['symbol'],'name':row.get('name',row['symbol']),
                         'action':'trim' if row.get('suggested_trim_amount') else 'add',
                         'amount':row.get('suggested_trim_amount') or row.get('recommended_amount') or 0,
                         'price':row.get('market_price'),'estimated_price':row.get('estimated_price'),
                         'units':row.get('estimated_trim_units') if row.get('suggested_trim_amount') else row.get('estimated_units'),
                         'position_pct':row.get('position_pct'),'rs_ytd':row.get('rs_ytd'),'ytd_total':row.get('ytd_total'),
                         'reason':row.get('adjustment_action') or row.get('swing_action')})
    action='add' if risk.get('deployable',0)>0 else 'trim' if risk.get('trim_amount',0)>0 else 'hold'
    summary=(f"股票目標 {risk.get('target_equity_pct',0):.1f}%；本次可新增 {risk.get('deployable',0):,.0f} 元，"
             f"建議調節 {risk.get('trim_amount',0):,.0f} 元；配置 {plan.get('count',0)} 檔 ETF。")
    return {'mode':'rolling-etf-v1','date':target,'created_at':created_at,'frozen':True,'action':action,
            'target_equity_pct':risk.get('target_equity_pct'),'coverage':risk.get('coverage'),
            'selected':selected,'summary':summary,
            'method':'每日使用當時已保存的市場風控、ETF 位階、YTD 含息動能與 RS 生成；同一交易日預設沿用最新一份完整規劃，只有手動重新分析才建立新版本。'}

def ensure_daily_snapshot(snapshot):
    snapshot.setdefault('version',VERSION);snapshot.setdefault('mode','rolling-etf-v1')
    if not snapshot.get('analysis'):
        snapshot['analysis']=daily_analysis(snapshot.get('date',''),snapshot.get('risk') or {},snapshot.get('plan') or {},snapshot.get('etfs') or [],snapshot.get('created_at',''))
    return snapshot

def refresh(target=None,wait=False,force=False):
    return {'status':'retired','message':'ETF滾動存股規劃已停止更新；股票配置改隨V6對話更新。'}

def _archived_refresh(target=None,wait=False,force=False):
    if not isinstance(force,bool):raise ValueError('重新分析設定需為布林值')
    if not _lock.acquire(False):return {'status':'running','message':'策略更新中'}
    try:
        evidence=research.context(target,include_stocks=False);target=evidence['date'];cfg=settings();catalog_date,universe=catalog_snapshot(target)
        with research.store() as c:
            init(c);set_meta(c,'job',{'status':'running','message':'讀取 ETF 行情、位階與風控資料','started_at':research.now()})
            saved=c.execute('SELECT id,created_at,payload FROM strategy_snapshots WHERE date=? ORDER BY created_at DESC,id DESC LIMIT 1',(target,)).fetchone()
            if saved and not force:
                payload=ensure_daily_snapshot(json.loads(saved['payload']))
                if 'analysis' not in json.loads(saved['payload']):c.execute('UPDATE strategy_snapshots SET payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False,allow_nan=False),saved['id']))
                job={'status':'complete','message':f'{target} ETF 每日規劃已保存，本次沿用既有版本','cached':True,'snapshot_id':saved['id'],'finished_at':research.now()}
                set_meta(c,'job',job);_lock.release();return job
            old=c.execute('SELECT payload FROM strategy_snapshots WHERE date<? ORDER BY date DESC,created_at DESC,id DESC LIMIT 1',(target,)).fetchone()
            previous=(json.loads(old[0]).get('risk') or {}).get('target_equity_pct') if old else None
    except Exception:_lock.release();raise
    def worker():
        try:
            names={};errors=[]
            with research.store() as c:set_meta(c,'job',{'status':'running','message':'確認恐懼貪婪與台幣最新資料'})
            refresh_market_risk_inputs()
            try:
                with research.store() as c:set_meta(c,'job',{'status':'running','message':'更新加權指數日 K 與風控對照'})
                refresh_index_bars(target)
            except ValueError as e:errors.append('加權指數日 K：'+str(e))
            automatic=auto_ytd_candidates(universe,cfg)
            auto_symbols={row['symbol'] for row in automatic}
            plan_symbols=list(dict.fromkeys([*cfg['etfs'],*(row['symbol'] for row in automatic)]))
            selected={symbol:universe.get(symbol) for symbol in plan_symbols}
            for s,r in selected.items():
                if r and normal_etf(r):names[s]=r
            if '0050' in universe:names['0050']=universe['0050']
            with research.store() as c:
                cached={r['symbol']:json.loads(r['payload']) for r in c.execute('SELECT * FROM strategy_quotes')}
            quotes={}
            for index,(symbol,item) in enumerate(names.items()):
                with research.store() as c:set_meta(c,'job',{'status':'running','message':f'更新行情 {index+1}/{len(names)} · {symbol}'})
                try:
                    market=item.get('market')
                    if not market:
                        with research.main_db() as c:r=c.execute('SELECT market FROM prices WHERE symbol=? AND date<=? ORDER BY date DESC LIMIT 1',(symbol,target)).fetchone();market=r[0] if r else 'TWSE'
                    data=repair_official_gaps(fetch_chart(symbol,market,target),symbol,target);quotes[symbol]=data
                    if not cached.get(symbol) or data['rows'][-1]['date']>=cached[symbol]['rows'][-1]['date']:
                        with research.store() as c:c.execute('INSERT OR REPLACE INTO strategy_quotes VALUES (?,?)',(symbol,json.dumps(data)))
                except ValueError as e:
                    errors.append(symbol+'：'+str(e))
                    if symbol in cached:quotes[symbol]=cached[symbol]
            pairs=disabled_pair_snapshot()
            risk=risk_dashboard(target,cfg,previous,use_latest=True);etfs=[]
            for symbol,item in selected.items():
                row={'symbol':symbol,'name':(item or {}).get('name',symbol),'selection':'YTD 強勢' if symbol in auto_symbols else '自選','rs_ytd':(item or {}).get('rs'),'rank_ytd':(item or {}).get('ytd_pct',(item or {}).get('return_pct')),'rank_date':catalog_date}
                try:
                    if not item or not normal_etf(item):raise ValueError('未在本機原型 ETF 名單；槓桿／反向／ETN 不納入存股')
                    if symbol not in quotes:raise ValueError('缺少含息行情；請手動重試')
                    row.update(etf_metrics(quotes[symbol],target,cfg,quotes.get('0050'),len(plan_symbols)))
                    try:row.update(fetch_nav(symbol,target))
                    except ValueError as e:row['nav_note']=str(e)+'；保留空白，不以市價代替'
                    row['distribution']='今年有配息事件' if row['dividends'] else '今年未列配息事件'
                except ValueError as e:row['error']=str(e)
                etfs.append(row)
            plan=rolling_plan(etfs,risk,cfg['allocation_count'])
            import uuid
            created_at=research.now();analysis=daily_analysis(target,risk,plan,etfs,created_at)
            snapshot={'id':uuid.uuid4().hex,'date':target,'created_at':created_at,'version':VERSION,'mode':'rolling-etf-v1','settings':cfg,'catalog_date':catalog_date,'plan_symbols':plan_symbols,'automatic_symbols':sorted(auto_symbols),'pairs':pairs,'etfs':etfs,'risk':risk,'plan':plan,'analysis':analysis,'warnings':errors+evidence['warnings'],'universe':list(universe.values())}
            with research.store() as c:c.execute('INSERT INTO strategy_snapshots VALUES (?,?,?,?)',(snapshot['id'],target,snapshot['created_at'],json.dumps(snapshot,ensure_ascii=False,allow_nan=False)))
            with research.store() as c:set_meta(c,'job',{'status':'partial' if errors else 'complete','message':f"已保存 {target} 快照 · 股票目標 {risk['target_equity_pct']}% · 可布局 {risk['deployable']:,.0f} 元 · {len(etfs)} 檔 ETF",'errors':errors,'finished_at':research.now()})
        except Exception as e:
            with research.store() as c:set_meta(c,'job',{'status':'failed','message':str(e)[:180] if isinstance(e,ValueError) else '策略計算未完成，舊快照保留','finished_at':research.now()})
        finally:_lock.release()
    if wait:worker()
    else:
        try:threading.Thread(target=worker,daemon=True).start()
        except Exception:_lock.release();raise
    return report()['job']
