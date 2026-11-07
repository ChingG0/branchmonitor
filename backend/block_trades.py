"""Daily TWSE block-trade snapshots and local history."""
from __future__ import annotations
import json,math,re,threading
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from . import db

TZ=ZoneInfo('Asia/Taipei');SOURCE='https://www.twse.com.tw/zh/trading/block/bfiauu.html';_lock=threading.Lock()
_job={'status':'idle','message':'讀取已保存鉅額交易','started_at':None,'finished_at':None}

def now():return datetime.now(TZ).isoformat(timespec='seconds')
def _num(value):
    try:number=float(str(value).replace(',','').strip())
    except (TypeError,ValueError):raise ValueError('鉅額交易數值缺漏') from None
    if not math.isfinite(number):raise ValueError('鉅額交易數值無效')
    return number
def init(c):c.execute('CREATE TABLE IF NOT EXISTS block_trade_days(date TEXT PRIMARY KEY,payload TEXT NOT NULL,fetched_at TEXT NOT NULL)')

def parse_payload(raw,target):
    data=json.loads(raw)
    fields=['證券代號','證券名稱','交易別','成交價','成交股數','成交金額']
    if data.get('stat','').lower()!='ok' or data.get('fields')!=fields:raise ValueError('證交所鉅額交易格式改變或當日尚無資料')
    stamp=str(data.get('date',''))
    if stamp!=target.replace('-',''):raise ValueError('鉅額交易資料日期不符')
    grouped={}
    for values in data.get('data',[]):
        if len(values)!=6:continue
        symbol,name,kind=map(str,values[:3])
        try:price,shares,amount=map(_num,values[3:])
        except ValueError:continue
        if not re.fullmatch(r'[0-9A-Z]{2,10}',symbol) or min(price,shares,amount)<=0:continue
        if abs(price*shares-amount)>max(1,amount*.001):continue
        row=grouped.setdefault(symbol,{'symbol':symbol,'name':name.strip(),'amount':0,'shares':0,'count':0,'min_price':price,'max_price':price,'types':set()})
        row['amount']+=amount;row['shares']+=shares;row['count']+=1;row['min_price']=min(row['min_price'],price);row['max_price']=max(row['max_price'],price);row['types'].add(kind.strip())
    if not grouped:raise ValueError('當日沒有可驗證的單一證券鉅額交易')
    rows=[]
    for row in grouped.values():
        row['vwap']=row['amount']/row['shares'];row['types']=sorted(row['types']);rows.append(row)
    rows.sort(key=lambda r:(-r['amount'],r['symbol']))
    return {'date':target,'rows':rows,'total_amount':sum(r['amount'] for r in rows),'total_shares':sum(r['shares'] for r in rows),'trade_count':sum(r['count'] for r in rows),'source':SOURCE,'note':'證交所單一證券鉅額交易；交易別不代表主動買方或賣方，頁面不臆測方向。'}

def fetch_day(target):
    url='https://www.twse.com.tw/rwd/zh/block/BFIAUU?response=json&selectType=S&date='+target.replace('-','')
    try:
        response=requests.get(url,headers={'User-Agent':'branch-monitor/1.0'},timeout=(8,25))
        if response.status_code!=200:raise ValueError(f'證交所鉅額交易 HTTP {response.status_code}')
        return parse_payload(response.text,target)
    except requests.RequestException:raise ValueError('證交所鉅額交易連線失敗') from None

def latest_market_day():
    with db.connect() as c:
        row=c.execute("SELECT MAX(date) FROM prices WHERE market='TWSE'").fetchone()
    return row[0] if row and row[0] else datetime.now(TZ).date().isoformat()

def report(target=None):
    with db.connect() as c:
        init(c);dates=[r[0] for r in c.execute('SELECT date FROM block_trade_days ORDER BY date DESC LIMIT 260')]
        chosen=target or (dates[0] if dates else None)
        if target and target not in dates:raise ValueError('此日期尚未保存鉅額交易')
        row=c.execute('SELECT payload,fetched_at FROM block_trade_days WHERE date=?',(chosen,)).fetchone() if chosen else None
        history=[]
        for old in c.execute('SELECT date,payload FROM block_trade_days WHERE date<? ORDER BY date DESC LIMIT 20',(chosen or '9999',)):
            history.append((old['date'],{r['symbol']:r for r in json.loads(old['payload'])['rows']}))
    payload=json.loads(row['payload']) if row else {'date':None,'rows':[],'total_amount':0,'total_shares':0,'trade_count':0,'source':SOURCE,'note':'尚未更新'}
    for item in payload['rows']:
        values=[daily[item['symbol']]['amount'] for _,daily in history if item['symbol'] in daily]
        average=sum(values)/len(values) if values else None
        item['average_amount']=average;item['amount_ratio']=item['amount']/average if average else None;item['history_days']=len(values)
    payload['rows'].sort(key=lambda r:(-(r.get('amount_ratio') or 0),-r['amount']))
    return {'snapshot':payload,'available_dates':dates,'fetched_at':row['fetched_at'] if row else None,'job':dict(_job)}

def refresh(target=None,wait=False):
    if not _lock.acquire(False):return dict(_job)
    target=target or latest_market_day()
    if not re.fullmatch(r'20\d{2}-\d{2}-\d{2}',target):_lock.release();raise ValueError('鉅額交易日期格式錯誤')
    _job.update(status='running',message=f'更新 {target} 鉅額交易',started_at=now(),finished_at=None)
    def worker():
        try:
            payload=fetch_day(target);stamp=now()
            with db.connect() as c:init(c);c.execute('INSERT OR REPLACE INTO block_trade_days VALUES (?,?,?)',(target,json.dumps(payload,ensure_ascii=False,allow_nan=False),stamp))
            _job.update(status='complete',message=f"已保存 {target} · {len(payload['rows'])} 檔 · {payload['trade_count']} 筆鉅額交易",finished_at=stamp)
        except Exception as error:_job.update(status='failed',message=str(error)[:180] if isinstance(error,ValueError) else '鉅額交易更新失敗，舊資料保留',finished_at=now())
        finally:_lock.release()
    if wait:worker()
    else:
        try:threading.Thread(target=worker,daemon=True).start()
        except Exception:_lock.release();raise
    return dict(_job)
