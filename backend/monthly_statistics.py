"""Taiwan-led month-end price returns. GET reads only; refresh is explicit."""
from __future__ import annotations
from pathlib import Path
from datetime import datetime,date,timedelta
from zoneinfo import ZoneInfo
import argparse,copy,hashlib,json,math,os,threading,uuid,sqlite3
import requests
from . import db,monthly_sector_leaders
TZ=ZoneInfo('Asia/Taipei');VERSION='monthly-price-returns-1'
SEED=db.ROOT/'config/monthly_statistics/seeds.json'
STORE=db.DATA/'monthly_statistics'
_lock=threading.Lock();_state_lock=threading.Lock();_job={'status':'idle','message':'尚未更新'}

def _number(v):
    n=float(str(v).replace(',','').strip())
    if not math.isfinite(n) or n<=0:raise ValueError('指數必須為正的有限數值')
    return n

def _month_before(m):
    d=date.fromisoformat(m+'-01')-timedelta(days=1);return d.strftime('%Y-%m')

def validate_closes(rows):
    seen=set()
    for r in rows:
        d=date.fromisoformat(r['date']);m=r['month']
        if d.strftime('%Y-%m')!=m or m in seen:raise ValueError('月份重複或與收盤日不符')
        seen.add(m);_number(r['close'])
    if not rows:raise ValueError('沒有可用月末收盤')
    clean=copy.deepcopy(rows)
    for r in clean:r['close']=_number(r['close'])
    return sorted(clean,key=lambda r:r['month'])

def monthly_from_daily(rows):
    out={};seen={}
    for r in sorted(rows,key=lambda x:x['date']):
        d=date.fromisoformat(r['date']).isoformat();v=_number(r['close'])
        if d in seen and abs(seen[d]-v)>1e-6:raise ValueError('同日指數資料衝突')
        seen[d]=v;out[d[:7]]={'month':d[:7],'date':d,'close':v,'source':r['source']}
    return list(out.values())

def parse_twse(value,month,now):
    if value.get('stat')!='OK' or value.get('date','')[:6]!=month.replace('-',''):raise ValueError('證交所月份不符或尚無資料')
    fields=value.get('fields',[])
    if '日期' not in fields or '發行量加權股價指數' not in fields:raise ValueError('缺少證交所收盤指數欄位')
    i=fields.index('發行量加權股價指數');rows=[];local=now.astimezone(TZ)
    for r in value.get('data',[]):
        bits=r[0].strip().split('/');d=date(int(bits[0])+1911,int(bits[1]),int(bits[2]))
        if d.strftime('%Y-%m')!=month:raise ValueError('證交所回傳跨月資料')
        if d>local.date() or (d==local.date() and local.strftime('%H:%M')<'13:45'):continue
        rows.append({'date':d.isoformat(),'close':_number(r[i]),'source':'twse-live'})
    if not rows:raise ValueError('尚無完整收盤資料')
    return rows

def parse_yahoo_daily(value,now):
    result=value.get('chart',{}).get('result')
    if not result:raise ValueError('Yahoo沒有可用S&P500資料')
    x=result[0];meta=x['meta']
    if meta.get('symbol')!='^GSPC' or meta.get('dataGranularity')!='1d':raise ValueError('S&P500代碼或日頻不符，拒用週／季資料')
    ts=x.get('timestamp',[]);closes=x['indicators']['quote'][0]['close']
    if len(ts)!=len(closes):raise ValueError('S&P500日期與收盤欄位長度不同')
    tz=ZoneInfo(meta['exchangeTimezoneName']);local=now.astimezone(tz);rows=[]
    for t,c in zip(ts,closes):
        day=datetime.fromtimestamp(t,tz).date()
        if c is None or day>local.date():continue
        if day==local.date():
            end=meta.get('currentTradingPeriod',{}).get('regular',{}).get('end')
            if end is None or datetime.fromtimestamp(end,tz).date()!=day or now.timestamp()<end+900:continue
        rows.append({'date':day.isoformat(),'close':_number(c),'source':'yahoo-sp500-daily'})
    if not rows:raise ValueError('沒有已完成的S&P500收盤')
    return rows

def seasonality(rows):
    result=[]
    for month in range(1,13):
        vals=sorted(r['return_pct'] for r in rows if int(r['month'][5:])==month and r['return_pct'] is not None and not r['partial'])
        n=len(vals);middle=(vals[n//2] if n%2 else (vals[n//2-1]+vals[n//2])/2) if n else None
        up=sum(v>1e-10 for v in vals);down=sum(v<-1e-10 for v in vals)
        result.append({'month':month,'n':n,'up':up,'down':down,'flat':n-up-down,'up_rate':up/n*100 if n else None,'mean':sum(vals)/n if n else None,'median':middle})
    return result

def calculate(market,now):
    meta=market['metadata'];closes=validate_closes(market['closes']);today=now.astimezone(ZoneInfo(meta['timezone']));current=today.strftime('%Y-%m');by={r['month']:r for r in closes};rows=[]
    for r in closes:
        if r['month']>current or r['date']>today.date().isoformat():continue
        previous=by.get(_month_before(r['month']))
        rows.append({**r,'previous_date':previous['date'] if previous else None,'previous_close':previous['close'] if previous else None,
          'return_pct':(r['close']/previous['close']-1)*100 if previous else None,'partial':r.get('complete') is not True,'in_progress':r['month']==current})
    if not rows:raise ValueError('沒有截至當日可用月份')
    years=[];indexed={r['month']:r for r in rows};lastmonth=rows[-1]['month']
    for year in sorted({int(r['month'][:4]) for r in rows},reverse=True):
        count=int(lastmonth[5:]) if year==int(lastmonth[:4]) else 12
        months=[indexed.get(f'{year}-{i:02d}') for i in range(1,13)]
        valid=all(r and r['return_pct'] is not None and (not r['partial'] or (year==today.year and r['month']==lastmonth)) for r in months[:count]);baseline=by.get(f'{year-1}-12')
        annual=(months[count-1]['close']/baseline['close']-1)*100 if valid and baseline else None
        years.append({'year':year,'months':months,'return_pct':annual,'through':months[count-1]['date'] if months[count-1] else None,'partial':year==today.year or count<12 or any(r and r['partial'] for r in months),'months_included':count})
    missing=[];d=date.fromisoformat(rows[0]['month']+'-01');end=date.fromisoformat(lastmonth+'-01')
    while d<=end:
        if d.strftime('%Y-%m') not in indexed:missing.append(d.strftime('%Y-%m'))
        d=(d.replace(day=28)+timedelta(days=4)).replace(day=1)
    usable=[r for r in rows if r['return_pct'] is not None]
    return {'metadata':meta,'rows':rows,'years':years,'seasonality':seasonality(rows),'first_close_month':rows[0]['month'],
      'first_return_month':usable[0]['month'] if usable else None,'latest_month':lastmonth,'as_of':rows[-1]['date'],'latest_close':rows[-1]['close'],'missing_months':missing}

def build(saved,now):
    markets={k:calculate(v,now) for k,v in saved['markets'].items()};tw=markets['taiex'];eligible={r['month'] for r in tw['rows'] if r['return_pct'] is not None}
    # Taiwan owns the timeline; US history is never shown outside its months.
    sp=markets['sp500'];sp['rows']=[r for r in sp['rows'] if r['month'] in eligible]
    sp['seasonality']=seasonality(sp['rows']);sp['years']=[y for y in sp['years'] if y['year'] in {x['year'] for x in tw['years']}]
    for y in sp['years']:
        y['months']=[r if r and r['month'] in eligible else None for r in y['months']]
        twyear=next(t for t in tw['years'] if t['year']==y['year'])
        if twyear['return_pct'] is None:y['return_pct']=None
    sp['first_return_month']=sp['rows'][0]['month'] if sp['rows'] else None
    return {'version':VERSION,'saved_at':saved.get('saved_at'),'source_errors':saved.get('source_errors',{}),'markets':markets,
      'note':'台股月份為主；月報酬＝本月末收盤÷上月末收盤−1。皆為當地幣別價格指數，不含股息與匯率。統計排除本月迄今；上漲比例不是未來上漲機率。',
      'history_note':'台股1967–1998取歷史轉載，1999起取證交所官方收盤。1967年1月僅作基準，2月起有月報酬；未用1966年基期100冒充年底收盤。S&P500只對照相同台股月份。'}

def _read(store=None):
    path=(store or STORE)/'cache.json'
    value=json.loads((path if path.exists() else SEED).read_text())
    if value.get('version')!=VERSION:raise ValueError('月份統計資料版本不符')
    return value

def report(*,store=None,now=None):
    with _state_lock:job=dict(_job)
    try:
        result=build(_read(store),now or datetime.now(TZ))
        monthly_sector_leaders.attach(result,monthly_sector_leaders.cached(store=store or STORE))
        return {**result,'job':job}
    except (OSError,ValueError,KeyError,TypeError) as e:return {'version':VERSION,'markets':{},'job':job,'error':'月份統計暫不可用：'+str(e)}

def _download(url,params,raw):
    response=requests.get(url,params=params,headers={'User-Agent':'Mozilla/5.0'},timeout=(10,35));response.raise_for_status();value=response.json()
    name=uuid.uuid4().hex+'.json';p=raw/name;p.write_bytes(response.content)
    return value,{'url':response.url,'file':'raw/'+name,'sha256':hashlib.sha256(response.content).hexdigest(),'retrieved_at':datetime.now(TZ).isoformat()}

def refresh_sync(*,store=None,now=None):
    target=store or STORE;target.mkdir(parents=True,exist_ok=True);raw=target/'raw';raw.mkdir(exist_ok=True)
    now=now or datetime.now(TZ);saved=_read(target);manifest=[];errors={};month=now.strftime('%Y-%m');previous=_month_before(month)
    for key in ['taiex','sp500']:
        try:
            fresh=[]
            if key=='taiex':
                for m in [previous,month]:
                    value,proof=_download('https://www.twse.com.tw/exchangeReport/FMTQIK',{'date':m.replace('-','')+'01','response':'json'},raw)
                    if value.get('stat')!='OK' and m==month:
                        already=any(r['month']==m for r in saved['markets'][key]['closes'])
                        if not already and any(t in str(value.get('stat','')) for t in ['沒有符合條件','查無資料','沒有資料']):continue
                        raise ValueError('證交所本月回應異常，保留已保存收盤')
                    fresh.extend(parse_twse(value,m,now));manifest.append(proof)
            else:
                value,proof=_download('https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC',{'range':'3mo','interval':'1d'},raw)
                fresh=parse_yahoo_daily(value,now);manifest.append(proof)
            # Keep the long-history seed immutable; refresh the previous/current
            # month only, with the newest completed daily close in each.
            months=monthly_from_daily(fresh);closes={r['month']:r for r in saved['markets'][key]['closes']}
            for r in months:
                r['complete']=r['month']<now.astimezone(ZoneInfo(saved['markets'][key]['metadata']['timezone'])).strftime('%Y-%m')
                if r['month']>=previous:
                    old=closes.get(r['month'])
                    if old and old['date']>r['date']:raise ValueError('新來源收盤日期退回，保留已保存資料')
                    closes[r['month']]=r
            saved['markets'][key]['closes']=validate_closes(list(closes.values()))
            if key=='taiex':
                sources=saved['markets'][key]['metadata']['sources']
                sources[:]=[x for x in sources if x['id']!='twse-live']
                sources.append({'id':'twse-live','label':'證交所最近月份每日收盤','url':'https://www.twse.com.tw/zh/trading/historical/fmtqik.html','retrieved_at':datetime.now(TZ).isoformat(),'note':'前月與本月官方日收盤彙整；本月未完資料不計入完整月份統計。'})
        except (requests.RequestException,OSError,ValueError,KeyError,TypeError,IndexError) as e:errors[key]=str(e)[:200]
    # Recompute the own-sector annotations only on explicit month refresh.
    # This reads already backfilled monthly histories and local endpoints;
    # GET never fetches, rebuilds, or changes the user's sector dictionary.
    if (target/monthly_sector_leaders.HISTORY).exists() or (target/monthly_sector_leaders.FILE).exists():
        try:monthly_sector_leaders.refresh_sync(saved['markets']['taiex']['closes'],store=target,now=now)
        except (OSError,ValueError,KeyError,TypeError,sqlite3.Error) as e:errors['sectors']='自訂族群月排行待補：'+str(e)[:160]
    saved.update(saved_at=datetime.now(TZ).isoformat(),source_errors=errors,latest_manifest=manifest)
    build(saved,now)
    tmp=target/('.'+uuid.uuid4().hex+'.tmp')
    with tmp.open('x') as f:json.dump(saved,f,ensure_ascii=False,allow_nan=False);f.flush();os.fsync(f.fileno())
    tmp.replace(target/'cache.json');return {'status':'partial' if errors else 'complete','message':'部分來源更新失敗，保留既有資料' if errors else '月份資料已更新','errors':errors}

def refresh():
    if not _lock.acquire(blocking=False):return {'status':'running','message':'月份資料更新中'}
    with _state_lock:_job.update(status='running',message='正在更新最近月份收盤')
    def run():
        try:result=refresh_sync()
        except Exception as e:result={'status':'failed','message':'更新失敗，保留既有資料：'+str(e)[:160]}
        with _state_lock:_job.update(result)
        _lock.release()
    threading.Thread(target=run,daemon=True).start();return {'status':'running','message':'正在更新最近月份收盤'}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--refresh',action='store_true');args=parser.parse_args()
    if args.refresh:print(json.dumps(refresh_sync(),ensure_ascii=False))
    else:print(json.dumps(report(),ensure_ascii=False))
